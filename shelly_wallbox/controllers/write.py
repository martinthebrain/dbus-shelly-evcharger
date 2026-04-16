# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus write-handling helpers for the Shelly wallbox service.

Victron GUI writes arrive here through writable DBus paths such as /Mode,
/Enable, /StartStop, and /AutoStart. The controller translates those user
actions into wallbox-specific state changes and Shelly relay commands.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from shelly_wallbox.auto.policy import AutoPolicy, validate_auto_policy
from shelly_wallbox.backend.models import effective_supported_phase_selections
from shelly_wallbox.core.contracts import write_failure_is_reversible
from shelly_wallbox.controllers.write_snapshot import (
    SNAPSHOT_ATTRS,
    SNAPSHOT_DBUS_PATHS,
    SNAPSHOT_DEQUE_ATTRS,
    SNAPSHOT_MAPPING_ATTRS,
    SNAPSHOT_VALUE_ATTRS,
    capture_write_state,
    restore_write_state,
)


class DbusWriteController:
    """Encapsulate writable DBus path handling for the Shelly wallbox service."""

    SNAPSHOT_DBUS_PATHS = SNAPSHOT_DBUS_PATHS
    SNAPSHOT_ATTRS = SNAPSHOT_ATTRS
    SNAPSHOT_DEQUE_ATTRS = SNAPSHOT_DEQUE_ATTRS
    SNAPSHOT_VALUE_ATTRS = SNAPSHOT_VALUE_ATTRS
    SNAPSHOT_MAPPING_ATTRS = SNAPSHOT_MAPPING_ATTRS
    CURRENT_SETTING_PATHS = ("/SetCurrent", "/MaxCurrent", "/MinCurrent")
    AUTO_RUNTIME_SETTING_PATHS = {
        "/Auto/StartSurplusWatts",
        "/Auto/StopSurplusWatts",
        "/Auto/MinSoc",
        "/Auto/ResumeSoc",
        "/Auto/StartDelaySeconds",
        "/Auto/StopDelaySeconds",
        "/Auto/ScheduledEnabledDays",
        "/Auto/ScheduledFallbackDelaySeconds",
        "/Auto/ScheduledLatestEndTime",
        "/Auto/ScheduledNightCurrent",
        "/Auto/DbusBackoffBaseSeconds",
        "/Auto/DbusBackoffMaxSeconds",
        "/Auto/GridRecoveryStartSeconds",
        "/Auto/StopSurplusDelaySeconds",
        "/Auto/StopSurplusVolatilityLowWatts",
        "/Auto/StopSurplusVolatilityHighWatts",
        "/Auto/ReferenceChargePowerWatts",
        "/Auto/LearnChargePowerEnabled",
        "/Auto/LearnChargePowerMinWatts",
        "/Auto/LearnChargePowerAlpha",
        "/Auto/LearnChargePowerStartDelaySeconds",
        "/Auto/LearnChargePowerWindowSeconds",
        "/Auto/LearnChargePowerMaxAgeSeconds",
        "/Auto/PhaseSwitching",
        "/Auto/PhasePreferLowestWhenIdle",
        "/Auto/PhaseUpshiftDelaySeconds",
        "/Auto/PhaseDownshiftDelaySeconds",
        "/Auto/PhaseUpshiftHeadroomWatts",
        "/Auto/PhaseDownshiftMarginWatts",
        "/Auto/PhaseMismatchRetrySeconds",
        "/Auto/PhaseMismatchLockoutCount",
        "/Auto/PhaseMismatchLockoutSeconds",
    }

    def __init__(self, port: Any) -> None:
        self.port = port
        self._external_side_effect_started = False

    @classmethod
    def _snapshot_write_state(cls, svc: Any) -> dict[str, Any]:
        """Capture mutable write-path state so failed writes can be rolled back."""
        return capture_write_state(
            svc,
            attrs=cls.SNAPSHOT_ATTRS,
            deque_attrs=cls.SNAPSHOT_DEQUE_ATTRS,
            value_attrs=cls.SNAPSHOT_VALUE_ATTRS,
            mapping_attrs=cls.SNAPSHOT_MAPPING_ATTRS,
            dbus_paths=cls.SNAPSHOT_DBUS_PATHS,
        )

    @staticmethod
    def _restore_write_state(svc: Any, snapshot: dict[str, Any]) -> None:
        """Restore one previously captured write-path snapshot."""
        restore_write_state(svc, snapshot)

    def _queue_relay_command(self, svc: Any, relay_on: bool, current_time: float) -> None:
        """Queue one relay command and record that write handling became irreversible."""
        svc._queue_relay_command(relay_on, current_time)
        self._external_side_effect_started = True

    def _mark_external_side_effect_started(self) -> None:
        """Record that one external control path accepted a non-reversible action."""
        self._external_side_effect_started = True

    @staticmethod
    def _publish_local_pm_status_best_effort(svc: Any, relay_on: bool, current_time: float) -> None:
        """Try to publish one optimistic local relay placeholder without aborting the write."""
        try:
            svc.publish_local_pm_status(relay_on, current_time)
        except Exception as error:  # pylint: disable=broad-except
            logging.warning(
                "Local relay placeholder publish failed after queuing relay=%s: %s",
                int(bool(relay_on)),
                error,
                exc_info=error,
            )

    @staticmethod
    def _log_normalized_mode(requested_mode: int, applied_mode: int) -> None:
        """Log when an unsupported mode was normalized."""
        if applied_mode == requested_mode:
            return
        logging.info(
            "Unsupported mode %s requested on /Mode, normalizing to %s",
            requested_mode,
            applied_mode,
        )

    @staticmethod
    def _reset_auto_decision_state(svc: Any) -> None:
        """Clear pending auto timers and averaging when the mode changes."""
        svc.auto_start_condition_since = None
        svc.auto_stop_condition_since = None
        svc._clear_auto_samples()

    @staticmethod
    def _activate_auto_without_cutover(svc: Any) -> None:
        """Enter Auto mode without forcing a relay cutover."""
        svc.auto_mode_cutover_pending = False
        svc.ignore_min_offtime_once = False

    def _queue_auto_cutover(self, svc: Any, current_time: float) -> None:
        """Perform the clean manual-to-auto cutover while charging."""
        self._queue_relay_command(svc, False, current_time)
        svc.virtual_enable = 1
        svc.virtual_startstop = 0
        svc.auto_mode_cutover_pending = True
        svc.ignore_min_offtime_once = False
        # Auto mode should take control from a known relay-off state. This
        # avoids inheriting a manual ON state and makes the next Auto start
        # decision explicit and visible in the logs.
        self._publish_local_pm_status_best_effort(svc, False, current_time)

    def _handle_mode_transition_to_auto(self, previous_mode: int, current_time: float) -> None:
        """Apply side effects when switching into an Auto-controlled mode."""
        port = self.port
        if port.mode_uses_auto_logic(previous_mode):
            return
        if port.relay_may_be_on_for_cutover():
            self._queue_auto_cutover(port, current_time)
            port.manual_override_until = 0.0
            return
        self._activate_auto_without_cutover(port)
        port.manual_override_until = 0.0

    @staticmethod
    def _snapshot_for_mode(svc: Any, current_time: float, auto_mode_active: bool) -> None:
        """Publish the mode-specific worker snapshot state."""
        snapshot = svc._get_worker_snapshot()
        svc._update_worker_snapshot(
            captured_at=current_time,
            auto_mode_active=auto_mode_active,
            pv_power=None if not auto_mode_active else snapshot.get("pv_power"),
            battery_soc=None if not auto_mode_active else snapshot.get("battery_soc"),
            grid_power=None if not auto_mode_active else snapshot.get("grid_power"),
        )

    @staticmethod
    def _auto_startstop_value(svc: Any) -> int:
        """Return the GUI StartStop value while Auto owns relay decisions."""
        return int(svc.virtual_enable or svc.virtual_startstop)

    @classmethod
    def _startstop_value_for_mode(cls, svc: Any, auto_mode_active: bool) -> int:
        """Return the mode-dependent StartStop display value."""
        return cls._auto_startstop_value(svc) if auto_mode_active else int(svc.virtual_startstop)

    @classmethod
    def _publish_startstop_enable(
        cls,
        svc: Any,
        current_time: float,
        auto_mode_active: bool | None = None,
    ) -> None:
        """Force-publish StartStop and Enable after a control-path write."""
        resolved_auto_mode_active = svc._mode_uses_auto_logic(svc.virtual_mode) if auto_mode_active is None else auto_mode_active
        svc._publish_dbus_path(
            "/StartStop",
            cls._startstop_value_for_mode(svc, resolved_auto_mode_active),
            current_time,
            force=True,
        )
        svc._publish_dbus_path("/Enable", int(svc.virtual_enable), current_time, force=True)

    @staticmethod
    def _publish_mode_paths(svc: Any, current_time: float, auto_mode_active: bool) -> None:
        """Publish /Mode, /StartStop, and /Enable after a mode change."""
        svc._publish_dbus_path("/Mode", svc.virtual_mode, current_time, force=True)
        DbusWriteController._publish_startstop_enable(svc, current_time, auto_mode_active)

    @staticmethod
    def _supported_phase_selection_text(svc: Any, current_time: float) -> str:
        """Return the DBus-facing CSV form of supported phase selections."""
        effective_supported = effective_supported_phase_selections(
            getattr(svc, "supported_phase_selections", ("P1",)),
            lockout_selection=getattr(svc, "_phase_switch_lockout_selection", None),
            lockout_until=getattr(svc, "_phase_switch_lockout_until", None),
            now=current_time,
        )
        return ",".join(effective_supported)

    @staticmethod
    def _queue_phase_switch_state(
        svc: Any,
        requested_selection: str,
        current_time: float,
        *,
        resume_relay: bool,
    ) -> None:
        """Record one staged phase-switch request before relay-off confirmation."""
        svc.requested_phase_selection = requested_selection
        svc._phase_switch_pending_selection = requested_selection
        svc._phase_switch_state = "waiting-relay-off"
        svc._phase_switch_requested_at = current_time
        svc._phase_switch_stable_until = None
        svc._phase_switch_resume_relay = bool(resume_relay)

    @classmethod
    def _publish_phase_selection_paths(cls, svc: Any, current_time: float) -> None:
        """Force-publish phase selection state after a phase-control write."""
        svc._publish_dbus_path("/PhaseSelection", svc.requested_phase_selection, current_time, force=True)
        svc._publish_dbus_path("/PhaseSelectionActive", svc.active_phase_selection, current_time, force=True)
        svc._publish_dbus_path(
            "/SupportedPhaseSelections",
            cls._supported_phase_selection_text(svc, current_time),
            current_time,
            force=True,
        )

    @staticmethod
    def _clear_phase_lockout_state(svc: Any) -> None:
        """Clear phase mismatch and lockout runtime state after an operator reset."""
        svc._phase_switch_mismatch_active = False
        svc._phase_switch_mismatch_counts = {}
        svc._phase_switch_last_mismatch_selection = None
        svc._phase_switch_last_mismatch_at = None
        svc._phase_switch_lockout_selection = None
        svc._phase_switch_lockout_reason = ""
        svc._phase_switch_lockout_at = None
        svc._phase_switch_lockout_until = None

    @classmethod
    def _publish_phase_lockout_paths(cls, svc: Any, current_time: float) -> None:
        """Force-publish phase lockout and degradation diagnostics after operator actions."""
        configured_supported = ",".join(tuple(getattr(svc, "supported_phase_selections", ("P1",))))
        effective_supported = cls._supported_phase_selection_text(svc, current_time)
        svc._publish_dbus_path("/Auto/PhaseLockoutActive", 0, current_time, force=True)
        svc._publish_dbus_path("/Auto/PhaseLockoutTarget", "", current_time, force=True)
        svc._publish_dbus_path("/Auto/PhaseLockoutReason", "", current_time, force=True)
        svc._publish_dbus_path("/Auto/PhaseSupportedConfigured", configured_supported, current_time, force=True)
        svc._publish_dbus_path("/Auto/PhaseSupportedEffective", effective_supported, current_time, force=True)
        svc._publish_dbus_path(
            "/Auto/PhaseDegradedActive",
            int(configured_supported != effective_supported),
            current_time,
            force=True,
        )
        svc._publish_dbus_path("/Auto/PhaseLockoutAge", -1, current_time, force=True)
        svc._publish_dbus_path("/Auto/PhaseLockoutReset", 0, current_time, force=True)

    @staticmethod
    def _clear_contactor_lockout_state(svc: Any) -> None:
        """Clear latched contactor-fault runtime state after an operator reset."""
        svc._contactor_fault_counts = {}
        svc._contactor_fault_active_reason = None
        svc._contactor_fault_active_since = None
        svc._contactor_lockout_reason = ""
        svc._contactor_lockout_source = ""
        svc._contactor_lockout_at = None
        svc._contactor_suspected_open_since = None
        svc._contactor_suspected_welded_since = None

    @staticmethod
    def _publish_contactor_lockout_paths(svc: Any, current_time: float) -> None:
        """Force-publish contactor lockout diagnostics after operator actions."""
        svc._publish_dbus_path("/Auto/ContactorFaultCount", 0, current_time, force=True)
        svc._publish_dbus_path("/Auto/ContactorLockoutActive", 0, current_time, force=True)
        svc._publish_dbus_path("/Auto/ContactorLockoutReason", "", current_time, force=True)
        svc._publish_dbus_path("/Auto/ContactorLockoutSource", "", current_time, force=True)
        svc._publish_dbus_path("/Auto/ContactorLockoutAge", -1, current_time, force=True)
        svc._publish_dbus_path("/Auto/ContactorLockoutReset", 0, current_time, force=True)

    def _apply_auto_disable(self, svc: Any, current_time: float) -> None:
        """Queue a relay-off transition after an Auto deny request."""
        self._queue_relay_command(svc, False, current_time)
        svc.virtual_enable = 0
        svc.virtual_startstop = 0
        self._publish_local_pm_status_best_effort(svc, False, current_time)

    def _apply_manual_startstop_request(self, svc: Any, wanted_on: bool, current_time: float) -> None:
        """Apply direct relay control in Manual mode."""
        self._apply_manual_enable_like_request(svc, wanted_on, current_time)

    def _apply_manual_enable_like_request(self, svc: Any, wanted_on: bool, current_time: float) -> None:
        """Apply one Manual direct-control request via charger or relay control."""
        if svc.charger_enable_available():
            svc.charger_set_enabled(wanted_on)
            self._mark_external_side_effect_started()
            svc.virtual_startstop = 1 if wanted_on else 0
            svc.virtual_enable = svc.virtual_startstop
            svc.manual_override_until = current_time + svc.auto_manual_override_seconds
            return
        self._queue_relay_command(svc, wanted_on, current_time)
        svc.virtual_startstop = 1 if wanted_on else 0
        svc.virtual_enable = svc.virtual_startstop
        svc.manual_override_until = current_time + svc.auto_manual_override_seconds
        self._publish_local_pm_status_best_effort(svc, wanted_on, current_time)

    def _apply_manual_enable_request(self, svc: Any, wanted_on: bool, current_time: float) -> None:
        """Apply direct enable/disable control in Manual mode."""
        self._apply_manual_enable_like_request(svc, wanted_on, current_time)

    def _handle_mode_write(self, requested_mode: int) -> None:
        port = self.port
        previous_mode = int(port.virtual_mode)
        current_time = port.time_now()
        normalized_mode = port.normalize_mode(requested_mode)
        auto_mode_active = port.mode_uses_auto_logic(normalized_mode)
        self._log_normalized_mode(requested_mode, normalized_mode)
        if auto_mode_active:
            self._handle_mode_transition_to_auto(previous_mode, current_time)
        port.virtual_mode = normalized_mode
        self._reset_auto_decision_state(port)
        self._snapshot_for_mode(port, current_time, auto_mode_active)
        self._publish_mode_paths(port, current_time, auto_mode_active)
        logging.info(
            "DBus write /Mode requested=%s previous=%s applied=%s %s",
            requested_mode,
            previous_mode,
            port.virtual_mode,
            port.state_summary(),
        )

    def _handle_autostart_write(self, value: Any) -> None:
        port = self.port
        port.virtual_autostart = int(value)
        port.publish_dbus_path("/AutoStart", port.virtual_autostart, port.time_now(), force=True)
        logging.info(
            "DBus write /AutoStart=%s %s",
            port.virtual_autostart,
            port.state_summary(),
        )

    def _handle_startstop_write(self, wanted_on: bool) -> None:
        port = self.port
        current_time = port.time_now()
        if port.mode_uses_auto_logic(port.virtual_mode):
            # In Auto, StartStop acts like an allow/deny request for the auto
            # controller. It must not bypass SOC/night/surplus checks by
            # forcing the relay on directly.
            if not wanted_on:
                self._apply_auto_disable(port, current_time)
            else:
                port.virtual_enable = 1
            self._publish_startstop_enable(port, current_time, auto_mode_active=True)
        else:
            # In Manual, StartStop remains direct relay control.
            self._apply_manual_startstop_request(port, wanted_on, current_time)
            self._publish_startstop_enable(port, current_time, auto_mode_active=False)
        logging.info(
            "DBus write /StartStop=%s auto_mode=%s %s",
            int(wanted_on),
            int(port.mode_uses_auto_logic(port.virtual_mode)),
            port.state_summary(),
        )

    def _handle_enable_write(self, wanted_on: bool) -> None:
        port = self.port
        current_time = port.time_now()
        if port.mode_uses_auto_logic(port.virtual_mode):
            if not wanted_on:
                self._apply_auto_disable(port, current_time)
            else:
                port.virtual_enable = 1
        else:
            self._apply_manual_enable_request(port, wanted_on, current_time)
        self._publish_startstop_enable(
            port,
            current_time,
            auto_mode_active=port.mode_uses_auto_logic(port.virtual_mode),
        )
        logging.info(
            "DBus write /Enable=%s auto_mode=%s %s",
            int(wanted_on),
            int(port.mode_uses_auto_logic(port.virtual_mode)),
            port.state_summary(),
        )

    def _handle_current_setting_write(self, path: str, value: Any) -> None:
        port = self.port
        current_time = port.time_now()
        if path == "/SetCurrent":
            requested_current = float(value)
            if port.charger_current_available():
                port.charger_set_current(requested_current)
                self._mark_external_side_effect_started()
            port.virtual_set_current = requested_current
            target_value = port.virtual_set_current
        elif path == "/MaxCurrent":
            port.max_current = float(value)
            target_value = port.max_current
        else:
            port.min_current = float(value)
            target_value = port.min_current
        port.publish_dbus_path(path, target_value, current_time, force=True)

    @staticmethod
    def _sync_auto_policy_runtime(port: Any) -> None:
        """Rebuild and validate the structured Auto policy after runtime tuning writes."""
        validate_auto_policy(AutoPolicy.from_service(port._service), port._service)
        port.validate_runtime_config()

    def _handle_auto_runtime_setting_write(self, path: str, value: Any) -> None:
        """Apply one writable Auto tuning value that may persist in runtime overrides."""
        port = self.port
        current_time = port.time_now()
        target_value: Any
        if path == "/Auto/StartSurplusWatts":
            port.auto_start_surplus_watts = float(value)
            self._sync_auto_policy_runtime(port)
            target_value = float(port.auto_start_surplus_watts)
        elif path == "/Auto/StopSurplusWatts":
            port.auto_stop_surplus_watts = float(value)
            self._sync_auto_policy_runtime(port)
            target_value = float(port.auto_stop_surplus_watts)
        elif path == "/Auto/MinSoc":
            port.auto_min_soc = float(value)
            self._sync_auto_policy_runtime(port)
            target_value = float(port.auto_min_soc)
        elif path == "/Auto/ResumeSoc":
            port.auto_resume_soc = float(value)
            self._sync_auto_policy_runtime(port)
            target_value = float(port.auto_resume_soc)
        elif path == "/Auto/StartDelaySeconds":
            port.auto_start_delay_seconds = float(value)
            port.validate_runtime_config()
            target_value = float(port.auto_start_delay_seconds)
        elif path == "/Auto/StopDelaySeconds":
            port.auto_stop_delay_seconds = float(value)
            port.validate_runtime_config()
            target_value = float(port.auto_stop_delay_seconds)
        elif path == "/Auto/ScheduledEnabledDays":
            port.auto_scheduled_enabled_days = value
            port.validate_runtime_config()
            target_value = str(port.auto_scheduled_enabled_days)
        elif path == "/Auto/ScheduledFallbackDelaySeconds":
            port.auto_scheduled_night_start_delay_seconds = float(value)
            port.validate_runtime_config()
            target_value = float(port.auto_scheduled_night_start_delay_seconds)
        elif path == "/Auto/ScheduledLatestEndTime":
            port.auto_scheduled_latest_end_time = value
            port.validate_runtime_config()
            target_value = str(port.auto_scheduled_latest_end_time)
        elif path == "/Auto/ScheduledNightCurrent":
            port.auto_scheduled_night_current_amps = float(value)
            port.validate_runtime_config()
            target_value = float(port.auto_scheduled_night_current_amps)
        elif path == "/Auto/DbusBackoffBaseSeconds":
            port.auto_dbus_backoff_base_seconds = float(value)
            port.validate_runtime_config()
            target_value = float(port.auto_dbus_backoff_base_seconds)
        elif path == "/Auto/DbusBackoffMaxSeconds":
            port.auto_dbus_backoff_max_seconds = float(value)
            port.validate_runtime_config()
            target_value = float(port.auto_dbus_backoff_max_seconds)
        elif path == "/Auto/GridRecoveryStartSeconds":
            port.auto_grid_recovery_start_seconds = float(value)
            self._sync_auto_policy_runtime(port)
            target_value = float(port.auto_grid_recovery_start_seconds)
        elif path == "/Auto/StopSurplusDelaySeconds":
            port.auto_stop_surplus_delay_seconds = float(value)
            self._sync_auto_policy_runtime(port)
            target_value = float(port.auto_stop_surplus_delay_seconds)
        elif path == "/Auto/StopSurplusVolatilityLowWatts":
            port.auto_stop_surplus_volatility_low_watts = float(value)
            self._sync_auto_policy_runtime(port)
            target_value = float(port.auto_stop_surplus_volatility_low_watts)
        elif path == "/Auto/StopSurplusVolatilityHighWatts":
            port.auto_stop_surplus_volatility_high_watts = float(value)
            self._sync_auto_policy_runtime(port)
            target_value = float(port.auto_stop_surplus_volatility_high_watts)
        elif path == "/Auto/ReferenceChargePowerWatts":
            port.auto_reference_charge_power_watts = float(value)
            self._sync_auto_policy_runtime(port)
            target_value = float(port.auto_reference_charge_power_watts)
        elif path == "/Auto/LearnChargePowerEnabled":
            port.auto_learn_charge_power_enabled = int(value)
            self._sync_auto_policy_runtime(port)
            target_value = int(port.auto_learn_charge_power_enabled)
        elif path == "/Auto/LearnChargePowerMinWatts":
            port.auto_learn_charge_power_min_watts = float(value)
            self._sync_auto_policy_runtime(port)
            target_value = float(port.auto_learn_charge_power_min_watts)
        elif path == "/Auto/LearnChargePowerAlpha":
            port.auto_learn_charge_power_alpha = float(value)
            self._sync_auto_policy_runtime(port)
            target_value = float(port.auto_learn_charge_power_alpha)
        elif path == "/Auto/LearnChargePowerStartDelaySeconds":
            port.auto_learn_charge_power_start_delay_seconds = float(value)
            self._sync_auto_policy_runtime(port)
            target_value = float(port.auto_learn_charge_power_start_delay_seconds)
        elif path == "/Auto/LearnChargePowerWindowSeconds":
            port.auto_learn_charge_power_window_seconds = float(value)
            self._sync_auto_policy_runtime(port)
            target_value = float(port.auto_learn_charge_power_window_seconds)
        elif path == "/Auto/LearnChargePowerMaxAgeSeconds":
            port.auto_learn_charge_power_max_age_seconds = float(value)
            self._sync_auto_policy_runtime(port)
            target_value = float(port.auto_learn_charge_power_max_age_seconds)
        else:
            if path == "/Auto/PhaseSwitching":
                port.auto_phase_switching_enabled = int(value)
                self._sync_auto_policy_runtime(port)
                target_value = int(port.auto_phase_switching_enabled)
            elif path == "/Auto/PhasePreferLowestWhenIdle":
                port.auto_phase_prefer_lowest_when_idle = int(value)
                self._sync_auto_policy_runtime(port)
                target_value = int(port.auto_phase_prefer_lowest_when_idle)
            elif path == "/Auto/PhaseUpshiftDelaySeconds":
                port.auto_phase_upshift_delay_seconds = float(value)
                self._sync_auto_policy_runtime(port)
                target_value = float(port.auto_phase_upshift_delay_seconds)
            elif path == "/Auto/PhaseDownshiftDelaySeconds":
                port.auto_phase_downshift_delay_seconds = float(value)
                self._sync_auto_policy_runtime(port)
                target_value = float(port.auto_phase_downshift_delay_seconds)
            elif path == "/Auto/PhaseUpshiftHeadroomWatts":
                port.auto_phase_upshift_headroom_watts = float(value)
                self._sync_auto_policy_runtime(port)
                target_value = float(port.auto_phase_upshift_headroom_watts)
            elif path == "/Auto/PhaseDownshiftMarginWatts":
                port.auto_phase_downshift_margin_watts = float(value)
                self._sync_auto_policy_runtime(port)
                target_value = float(port.auto_phase_downshift_margin_watts)
            elif path == "/Auto/PhaseMismatchRetrySeconds":
                port.auto_phase_mismatch_retry_seconds = float(value)
                self._sync_auto_policy_runtime(port)
                target_value = float(port.auto_phase_mismatch_retry_seconds)
            elif path == "/Auto/PhaseMismatchLockoutCount":
                port.auto_phase_mismatch_lockout_count = int(value)
                self._sync_auto_policy_runtime(port)
                target_value = int(port.auto_phase_mismatch_lockout_count)
            else:
                port.auto_phase_mismatch_lockout_seconds = float(value)
                self._sync_auto_policy_runtime(port)
                target_value = float(port.auto_phase_mismatch_lockout_seconds)
        port.publish_dbus_path(path, target_value, current_time, force=True)

    def _handle_phase_selection_write(self, value: Any) -> None:
        """Apply one phase selection when the current backend can do so safely."""
        port = self.port
        current_time = port.time_now()
        requested_selection = port.normalize_phase_selection(value)
        if requested_selection not in port.supported_phase_selections:
            raise ValueError(
                f"Unsupported phase selection '{value}' "
                f"(supported: {','.join(port.supported_phase_selections)})"
            )
        if port.phase_selection_requires_pause() and port.relay_may_be_on_for_cutover():
            self._queue_phase_switch_state(
                port._service,
                requested_selection,
                current_time,
                resume_relay=True,
            )
            self._queue_relay_command(port, False, current_time)
            self._publish_local_pm_status_best_effort(port, False, current_time)
            self._publish_phase_selection_paths(port, current_time)
            logging.info(
                "DBus write /PhaseSelection requested=%s staged=%s %s",
                value,
                requested_selection,
                port.state_summary(),
            )
            return
        applied_selection = port.apply_phase_selection(requested_selection)
        port.requested_phase_selection = applied_selection
        port.active_phase_selection = applied_selection
        self._publish_phase_selection_paths(port, current_time)
        logging.info(
            "DBus write /PhaseSelection requested=%s applied=%s %s",
            value,
            applied_selection,
            port.state_summary(),
        )

    def _handle_phase_lockout_reset_write(self, value: Any) -> None:
        """Clear phase lockout and mismatch tracking on explicit operator request."""
        port = self.port
        current_time = port.time_now()
        if not bool(int(value)):
            port.publish_dbus_path("/Auto/PhaseLockoutReset", 0, current_time, force=True)
            return
        self._clear_phase_lockout_state(port._service)
        self._publish_phase_selection_paths(port, current_time)
        self._publish_phase_lockout_paths(port, current_time)
        logging.info("DBus write /Auto/PhaseLockoutReset=1 cleared phase lockout state %s", port.state_summary())

    def _handle_contactor_lockout_reset_write(self, value: Any) -> None:
        """Clear latched contactor-fault state on explicit operator request."""
        port = self.port
        current_time = port.time_now()
        if not bool(int(value)):
            port.publish_dbus_path("/Auto/ContactorLockoutReset", 0, current_time, force=True)
            return
        self._clear_contactor_lockout_state(port._service)
        self._publish_contactor_lockout_paths(port, current_time)
        logging.info(
            "DBus write /Auto/ContactorLockoutReset=1 cleared contactor lockout state %s",
            port.state_summary(),
        )

    def _handle_mode_value_write(self, value: Any) -> None:
        """Normalize and dispatch one /Mode write value."""
        self._handle_mode_write(int(value))

    def _handle_startstop_value_write(self, value: Any) -> None:
        """Normalize and dispatch one /StartStop write value."""
        self._handle_startstop_write(bool(int(value)))

    def _handle_enable_value_write(self, value: Any) -> None:
        """Normalize and dispatch one /Enable write value."""
        self._handle_enable_write(bool(int(value)))

    def _direct_write_handlers(self) -> dict[str, Callable[[Any], None]]:
        """Return dedicated write handlers for scalar DBus paths."""
        return {
            "/Mode": self._handle_mode_value_write,
            "/AutoStart": self._handle_autostart_write,
            "/StartStop": self._handle_startstop_value_write,
            "/Enable": self._handle_enable_value_write,
            "/PhaseSelection": self._handle_phase_selection_write,
            "/Auto/PhaseLockoutReset": self._handle_phase_lockout_reset_write,
            "/Auto/ContactorLockoutReset": self._handle_contactor_lockout_reset_write,
        }

    def _execute_write(self, path: str, value: Any) -> None:
        """Dispatch one writable DBus path to its dedicated handler."""
        handler = self._direct_write_handlers().get(path)
        if handler is not None:
            handler(value)
            return
        if path in self.CURRENT_SETTING_PATHS:
            self._handle_current_setting_write(path, value)
            return
        if path in self.AUTO_RUNTIME_SETTING_PATHS:
            self._handle_auto_runtime_setting_write(path, value)

    def handle_write(self, path: str, value: Any) -> bool:
        """Handle writable DBus path updates from Venus OS."""
        port = self.port
        snapshot = self._snapshot_write_state(port._service)
        self._external_side_effect_started = False
        try:
            self._execute_write(path, value)
            port.save_runtime_state()
            port.save_runtime_overrides()
            return True
        except Exception as error:  # pylint: disable=broad-except
            if write_failure_is_reversible(self._external_side_effect_started):
                self._restore_write_state(port._service, snapshot)
                logging.warning("Write to %s=%s failed: %s", path, value, error, exc_info=error)
                return False
            logging.warning(
                "Write to %s=%s failed after external side effects started; keeping in-flight state: %s",
                path,
                value,
                error,
                exc_info=error,
            )
            # Once a relay or native charger command was accepted, the DBus
            # write is no longer safely reversible. Report success so callers
            # do not treat an in-flight external transition as if it had been
            # rejected.
            return True
        finally:
            self._external_side_effect_started = False
