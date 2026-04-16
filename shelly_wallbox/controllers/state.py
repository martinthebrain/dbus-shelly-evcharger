# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration and RAM-only runtime-state helpers for the Shelly wallbox service."""

import configparser
from dataclasses import dataclass
import json
import logging
import os
import time
from typing import Any, Callable, cast

from shelly_wallbox.auto.policy import validate_auto_policy
from shelly_wallbox.backend.models import (
    effective_supported_phase_selections,
    PhaseSelection,
    normalize_phase_selection,
    normalize_phase_selection_tuple,
    switch_feedback_mismatch,
)
from shelly_wallbox.core.common import (
    _charger_retry_remaining_seconds,
    _fresh_charger_retry_reason,
    _fresh_charger_retry_source,
    _fresh_charger_transport_reason,
    _fresh_charger_transport_source,
    evse_fault_reason,
)
from shelly_wallbox.core.contracts import (
    finite_float_or_none,
    non_negative_float_or_none,
    non_negative_int,
    normalize_learning_phase,
    normalize_learning_state,
)
from shelly_wallbox.core.shared import compact_json, write_text_atomically


@dataclass(frozen=True)
class RuntimeOverrideSpec:
    """One DBus-writable runtime setting that can persist in an override file."""

    dbus_path: str
    config_key: str
    attr_name: str
    value_kind: str


RUNTIME_OVERRIDE_SPECS: tuple[RuntimeOverrideSpec, ...] = (
    RuntimeOverrideSpec("/Mode", "Mode", "virtual_mode", "int"),
    RuntimeOverrideSpec("/AutoStart", "AutoStart", "virtual_autostart", "bool"),
    RuntimeOverrideSpec("/SetCurrent", "SetCurrent", "virtual_set_current", "float"),
    RuntimeOverrideSpec("/MinCurrent", "MinCurrent", "min_current", "float"),
    RuntimeOverrideSpec("/MaxCurrent", "MaxCurrent", "max_current", "float"),
    RuntimeOverrideSpec("/PhaseSelection", "PhaseSelection", "requested_phase_selection", "phase"),
    RuntimeOverrideSpec("/Auto/StartSurplusWatts", "AutoStartSurplusWatts", "auto_start_surplus_watts", "float"),
    RuntimeOverrideSpec("/Auto/StopSurplusWatts", "AutoStopSurplusWatts", "auto_stop_surplus_watts", "float"),
    RuntimeOverrideSpec("/Auto/MinSoc", "AutoMinSoc", "auto_min_soc", "float"),
    RuntimeOverrideSpec("/Auto/ResumeSoc", "AutoResumeSoc", "auto_resume_soc", "float"),
    RuntimeOverrideSpec("/Auto/StartDelaySeconds", "AutoStartDelaySeconds", "auto_start_delay_seconds", "float"),
    RuntimeOverrideSpec("/Auto/StopDelaySeconds", "AutoStopDelaySeconds", "auto_stop_delay_seconds", "float"),
    RuntimeOverrideSpec(
        "/Auto/DbusBackoffBaseSeconds",
        "AutoDbusBackoffBaseSeconds",
        "auto_dbus_backoff_base_seconds",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/DbusBackoffMaxSeconds",
        "AutoDbusBackoffMaxSeconds",
        "auto_dbus_backoff_max_seconds",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/GridRecoveryStartSeconds",
        "AutoGridRecoveryStartSeconds",
        "auto_grid_recovery_start_seconds",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/StopSurplusDelaySeconds",
        "AutoStopSurplusDelaySeconds",
        "auto_stop_surplus_delay_seconds",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/StopSurplusVolatilityLowWatts",
        "AutoStopSurplusVolatilityLowWatts",
        "auto_stop_surplus_volatility_low_watts",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/StopSurplusVolatilityHighWatts",
        "AutoStopSurplusVolatilityHighWatts",
        "auto_stop_surplus_volatility_high_watts",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/ReferenceChargePowerWatts",
        "AutoReferenceChargePowerWatts",
        "auto_reference_charge_power_watts",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/LearnChargePowerEnabled",
        "AutoLearnChargePower",
        "auto_learn_charge_power_enabled",
        "bool",
    ),
    RuntimeOverrideSpec(
        "/Auto/LearnChargePowerMinWatts",
        "AutoLearnChargePowerMinWatts",
        "auto_learn_charge_power_min_watts",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/LearnChargePowerAlpha",
        "AutoLearnChargePowerAlpha",
        "auto_learn_charge_power_alpha",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/LearnChargePowerStartDelaySeconds",
        "AutoLearnChargePowerStartDelaySeconds",
        "auto_learn_charge_power_start_delay_seconds",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/LearnChargePowerWindowSeconds",
        "AutoLearnChargePowerWindowSeconds",
        "auto_learn_charge_power_window_seconds",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/LearnChargePowerMaxAgeSeconds",
        "AutoLearnChargePowerMaxAgeSeconds",
        "auto_learn_charge_power_max_age_seconds",
        "float",
    ),
    RuntimeOverrideSpec("/Auto/PhaseSwitching", "AutoPhaseSwitching", "auto_phase_switching_enabled", "bool"),
    RuntimeOverrideSpec(
        "/Auto/PhasePreferLowestWhenIdle",
        "AutoPhasePreferLowestWhenIdle",
        "auto_phase_prefer_lowest_when_idle",
        "bool",
    ),
    RuntimeOverrideSpec(
        "/Auto/PhaseUpshiftDelaySeconds",
        "AutoPhaseUpshiftDelaySeconds",
        "auto_phase_upshift_delay_seconds",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/PhaseDownshiftDelaySeconds",
        "AutoPhaseDownshiftDelaySeconds",
        "auto_phase_downshift_delay_seconds",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/PhaseUpshiftHeadroomWatts",
        "AutoPhaseUpshiftHeadroomWatts",
        "auto_phase_upshift_headroom_watts",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/PhaseDownshiftMarginWatts",
        "AutoPhaseDownshiftMarginWatts",
        "auto_phase_downshift_margin_watts",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/PhaseMismatchRetrySeconds",
        "AutoPhaseMismatchRetrySeconds",
        "auto_phase_mismatch_retry_seconds",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/PhaseMismatchLockoutCount",
        "AutoPhaseMismatchLockoutCount",
        "auto_phase_mismatch_lockout_count",
        "int",
    ),
    RuntimeOverrideSpec(
        "/Auto/PhaseMismatchLockoutSeconds",
        "AutoPhaseMismatchLockoutSeconds",
        "auto_phase_mismatch_lockout_seconds",
        "float",
    ),
)
RUNTIME_OVERRIDE_BY_PATH: dict[str, RuntimeOverrideSpec] = {
    spec.dbus_path: spec for spec in RUNTIME_OVERRIDE_SPECS
}
RUNTIME_OVERRIDE_BY_CONFIG_KEY: dict[str, RuntimeOverrideSpec] = {
    spec.config_key: spec for spec in RUNTIME_OVERRIDE_SPECS
}
RUNTIME_OVERRIDE_SECTION = "RuntimeOverrides"


class _CasePreservingConfigParser(configparser.ConfigParser):
    """Config parser that keeps option names exactly as written."""

    def optionxform(self, optionstr: str) -> str:
        return optionstr


class ServiceStateController:
    """Encapsulate config loading, config validation, and volatile runtime state."""

    NON_NEGATIVE_INTERVAL_ATTRS = (
        "auto_pv_scan_interval_seconds",
        "auto_battery_scan_interval_seconds",
        "auto_dbus_backoff_base_seconds",
        "auto_dbus_backoff_max_seconds",
        "auto_grid_missing_stop_seconds",
        "auto_average_window_seconds",
        "auto_min_runtime_seconds",
        "auto_min_offtime_seconds",
        "auto_start_delay_seconds",
        "auto_stop_delay_seconds",
        "auto_input_cache_seconds",
        "auto_input_helper_restart_seconds",
        "auto_input_helper_stale_seconds",
        "auto_shelly_soft_fail_seconds",
        "auto_watchdog_stale_seconds",
        "auto_watchdog_recovery_seconds",
        "auto_startup_warmup_seconds",
        "auto_manual_override_seconds",
        "auto_phase_upshift_delay_seconds",
        "auto_phase_downshift_delay_seconds",
        "auto_phase_mismatch_retry_seconds",
        "auto_phase_mismatch_lockout_seconds",
        "startup_device_info_retry_seconds",
        "auto_audit_log_max_age_hours",
        "auto_audit_log_repeat_seconds",
    )

    def __init__(self, service: Any, normalize_mode_func: Callable[[object], int]) -> None:
        self.service = service
        self._normalize_mode = normalize_mode_func

    @staticmethod
    def config_path() -> str:
        """Return the path to the local Shelly wallbox config file."""
        return os.path.join(
            os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..")),
            "deploy",
            "venus",
            "config.shelly_wallbox.ini",
        )

    @classmethod
    def runtime_overrides_path(cls, defaults: configparser.SectionProxy) -> str:
        """Return the persistent runtime-override path for the current service instance."""
        device_instance = defaults.get("DeviceInstance", "60").strip() or "60"
        fallback = f"/data/etc/dbus-shelly-wallbox-overrides-{device_instance}.ini"
        return defaults.get("RuntimeOverridesPath", fallback).strip()

    @staticmethod
    def _summary_flag(value: object) -> str:
        """Return one runtime flag as 0/1 text."""
        return str(int(bool(value)))

    @staticmethod
    def _summary_text(value: object, default: str) -> str:
        """Return one compact summary text with a fallback."""
        text = str(value).strip() if value is not None else ""
        return text or default

    @staticmethod
    def _summary_float(value: object, default: str = "na") -> str:
        """Return one compact floating-point summary text with a fallback."""
        normalized = finite_float_or_none(value)
        return default if normalized is None else f"{normalized:.1f}"

    @classmethod
    def _summary_observed_phase(cls, svc: Any) -> str:
        """Return the latest observed phase selection for debug summaries."""
        confirmed_pm_status = getattr(svc, "_last_confirmed_pm_status", None)
        if isinstance(confirmed_pm_status, dict):
            observed = cls._summary_text(confirmed_pm_status.get("_phase_selection"), "")
            if observed:
                return observed
        return cls._summary_text(getattr(svc, "_last_charger_state_phase_selection", None), "na")

    @staticmethod
    def _summary_phase_mismatch_active(svc: Any) -> str:
        """Return the active phase-mismatch state as one compact flag."""
        active = bool(getattr(svc, "_phase_switch_mismatch_active", False))
        if active:
            return "1"
        return "1" if str(getattr(svc, "_last_health_reason", "")) == "phase-switch-mismatch" else "0"

    @classmethod
    def _summary_phase_lockout_active(cls, svc: Any) -> str:
        """Return whether a phase-switch lockout is currently active."""
        current_time = time.time()
        lockout_until = finite_float_or_none(getattr(svc, "_phase_switch_lockout_until", None))
        lockout_selection = getattr(svc, "_phase_switch_lockout_selection", None)
        if lockout_until is None or lockout_selection is None:
            return "0"
        return "1" if lockout_until > current_time else "0"

    @classmethod
    def _summary_phase_lockout_target(cls, svc: Any) -> str:
        """Return the active phase-lockout target for debug summaries."""
        if cls._summary_phase_lockout_active(svc) != "1":
            return "na"
        return cls._summary_text(getattr(svc, "_phase_switch_lockout_selection", None), "na")

    @classmethod
    def _summary_phase_supported_effective(cls, svc: Any) -> str:
        """Return the effective supported phase layouts for debug summaries."""
        effective_supported = effective_supported_phase_selections(
            getattr(svc, "supported_phase_selections", ("P1",)),
            lockout_selection=getattr(svc, "_phase_switch_lockout_selection", None),
            lockout_until=getattr(svc, "_phase_switch_lockout_until", None),
            now=time.time(),
        )
        return ",".join(effective_supported)

    @classmethod
    def _summary_phase_degraded_active(cls, svc: Any) -> str:
        """Return whether runtime phase support is currently degraded."""
        configured = normalize_phase_selection_tuple(getattr(svc, "supported_phase_selections", ("P1",)), ("P1",))
        effective = effective_supported_phase_selections(
            configured,
            lockout_selection=getattr(svc, "_phase_switch_lockout_selection", None),
            lockout_until=getattr(svc, "_phase_switch_lockout_until", None),
            now=time.time(),
        )
        return "1" if configured != effective else "0"

    @staticmethod
    def _summary_switch_feedback_closed(svc: Any) -> str:
        """Return explicit switch feedback as one compact debug value."""
        feedback_closed = getattr(svc, "_last_switch_feedback_closed", None)
        return "na" if feedback_closed is None else str(int(bool(feedback_closed)))

    @staticmethod
    def _summary_switch_interlock_ok(svc: Any) -> str:
        """Return explicit switch interlock state as one compact debug value."""
        interlock_ok = getattr(svc, "_last_switch_interlock_ok", None)
        return "na" if interlock_ok is None else str(int(bool(interlock_ok)))

    @classmethod
    def _summary_switch_feedback_mismatch(cls, svc: Any) -> str:
        """Return whether explicit switch feedback currently disagrees with relay state."""
        feedback_closed = getattr(svc, "_last_switch_feedback_closed", None)
        if feedback_closed is None:
            return "1" if str(getattr(svc, "_last_health_reason", "")) == "contactor-feedback-mismatch" else "0"
        confirmed_pm_status = getattr(svc, "_last_confirmed_pm_status", None)
        relay_on = False if not isinstance(confirmed_pm_status, dict) else bool(confirmed_pm_status.get("output", False))
        return str(int(switch_feedback_mismatch(relay_on, feedback_closed)))

    @staticmethod
    def _summary_contactor_fault_count(svc: Any) -> str:
        """Return the current latched or active contactor-fault counter for debug summaries."""
        counts = getattr(svc, "_contactor_fault_counts", None)
        if not isinstance(counts, dict):
            return "0"
        reason = str(getattr(svc, "_contactor_lockout_reason", "") or "")
        if not reason:
            reason = str(getattr(svc, "_contactor_fault_active_reason", "") or "")
        if not reason:
            return "0"
        return str(int(counts.get(reason, 0)))

    @staticmethod
    def _summary_contactor_suspected_open(svc: Any) -> str:
        """Return whether runtime currently suspects an open contactor without explicit feedback."""
        return "1" if str(getattr(svc, "_last_health_reason", "")) == "contactor-suspected-open" else "0"

    @staticmethod
    def _summary_contactor_suspected_welded(svc: Any) -> str:
        """Return whether runtime currently suspects a welded contactor without explicit feedback."""
        return "1" if str(getattr(svc, "_last_health_reason", "")) == "contactor-suspected-welded" else "0"

    @staticmethod
    def _summary_contactor_lockout_active(svc: Any) -> str:
        """Return whether a contactor-fault lockout is currently latched."""
        return "1" if str(getattr(svc, "_contactor_lockout_reason", "") or "") else "0"

    @classmethod
    def _summary_contactor_lockout_reason(cls, svc: Any) -> str:
        """Return the active contactor-fault lockout reason for debug summaries."""
        if cls._summary_contactor_lockout_active(svc) != "1":
            return "na"
        return cls._summary_text(getattr(svc, "_contactor_lockout_reason", None), "na")

    @staticmethod
    def _summary_fault_active(svc: Any) -> str:
        """Return whether a hard EVSE fault is currently active."""
        return "1" if evse_fault_reason(getattr(svc, "_last_health_reason", "")) is not None else "0"

    @staticmethod
    def _summary_fault_reason(svc: Any) -> str:
        """Return the active hard EVSE-fault reason for debug summaries."""
        reason = evse_fault_reason(getattr(svc, "_last_health_reason", ""))
        return "na" if reason is None else reason

    @staticmethod
    def _summary_charger_transport_reason(svc: Any) -> str:
        """Return the active charger-transport reason for debug summaries."""
        return ServiceStateController._summary_text(_fresh_charger_transport_reason(svc, time.time()), "na")

    @staticmethod
    def _summary_charger_transport_source(svc: Any) -> str:
        """Return the active charger-transport source for debug summaries."""
        return ServiceStateController._summary_text(_fresh_charger_transport_source(svc, time.time()), "na")

    @staticmethod
    def _summary_charger_retry_reason(svc: Any) -> str:
        """Return the active charger-retry reason for debug summaries."""
        return ServiceStateController._summary_text(_fresh_charger_retry_reason(svc, time.time()), "na")

    @staticmethod
    def _summary_charger_retry_source(svc: Any) -> str:
        """Return the active charger-retry source for debug summaries."""
        return ServiceStateController._summary_text(_fresh_charger_retry_source(svc, time.time()), "na")

    @staticmethod
    def _summary_recovery_active(svc: Any) -> str:
        """Return whether the broad Auto state currently represents recovery."""
        return "1" if str(getattr(svc, "_last_auto_state", "idle")) == "recovery" else "0"

    def state_summary(self) -> str:
        """Return a compact runtime state summary for debug logging."""
        svc = self.service
        parts = (
            f"mode={getattr(svc, 'virtual_mode', 'na')}",
            f"enable={getattr(svc, 'virtual_enable', 'na')}",
            f"startstop={getattr(svc, 'virtual_startstop', 'na')}",
            f"autostart={getattr(svc, 'virtual_autostart', 'na')}",
            f"cutover={self._summary_flag(getattr(svc, '_auto_mode_cutover_pending', False))}",
            f"ignore_offtime={self._summary_flag(getattr(svc, '_ignore_min_offtime_once', False))}",
            f"phase={getattr(svc, 'active_phase_selection', 'na')}",
            f"phase_req={getattr(svc, 'requested_phase_selection', 'na')}",
            f"phase_obs={self._summary_observed_phase(svc)}",
            f"phase_switch={self._summary_text(getattr(svc, '_phase_switch_state', 'na'), 'idle')}",
            f"phase_mismatch={self._summary_phase_mismatch_active(svc)}",
            f"phase_lockout={self._summary_phase_lockout_active(svc)}",
            f"phase_lockout_target={self._summary_phase_lockout_target(svc)}",
            f"phase_effective={self._summary_phase_supported_effective(svc)}",
            f"phase_degraded={self._summary_phase_degraded_active(svc)}",
            f"switch_feedback={self._summary_switch_feedback_closed(svc)}",
            f"switch_interlock={self._summary_switch_interlock_ok(svc)}",
            f"switch_feedback_mismatch={self._summary_switch_feedback_mismatch(svc)}",
            f"contactor_fault_count={self._summary_contactor_fault_count(svc)}",
            f"contactor_suspected_open={self._summary_contactor_suspected_open(svc)}",
            f"contactor_suspected_welded={self._summary_contactor_suspected_welded(svc)}",
            f"contactor_lockout={self._summary_contactor_lockout_active(svc)}",
            f"contactor_lockout_reason={self._summary_contactor_lockout_reason(svc)}",
            f"backend={getattr(svc, 'backend_mode', 'combined')}",
            f"meter_backend={getattr(svc, 'meter_backend_type', 'shelly_combined')}",
            f"switch_backend={getattr(svc, 'switch_backend_type', 'shelly_combined')}",
            f"charger_backend={self._summary_text(getattr(svc, 'charger_backend_type', None), 'na')}",
            f"charger_target={self._summary_float(getattr(svc, '_charger_target_current_amps', None))}",
            f"charger_status={self._summary_text(getattr(svc, '_last_charger_state_status', ''), 'na')}",
            f"charger_fault={self._summary_text(getattr(svc, '_last_charger_state_fault', ''), 'na')}",
            f"charger_transport={self._summary_charger_transport_reason(svc)}",
            f"charger_transport_source={self._summary_charger_transport_source(svc)}",
            f"charger_retry={self._summary_charger_retry_reason(svc)}",
            f"charger_retry_source={self._summary_charger_retry_source(svc)}",
            f"charger_retry_remaining={_charger_retry_remaining_seconds(svc, time.time())}",
            f"status_source={self._summary_text(getattr(svc, '_last_status_source', ''), 'unknown')}",
            f"fault={self._summary_fault_active(svc)}",
            f"fault_reason={self._summary_fault_reason(svc)}",
            f"auto_state={getattr(svc, '_last_auto_state', 'na')}",
            f"recovery={self._summary_recovery_active(svc)}",
            f"health={getattr(svc, '_last_health_reason', 'na')}",
        )
        return " ".join(parts)

    @staticmethod
    def coerce_runtime_int(value: object, default: int = 0) -> int:
        """Convert persisted runtime values to int safely."""
        if isinstance(value, bool):
            return int(default)
        if not isinstance(value, (str, int, float)):
            return int(default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def coerce_runtime_float(value: object, default: float = 0.0) -> float:
        """Convert persisted runtime values to float safely."""
        normalized = finite_float_or_none(value)
        return float(default) if normalized is None else normalized

    def current_runtime_state(self) -> dict[str, object]:
        """Return the volatile runtime state that should survive service restarts."""
        svc = self.service
        return {
            "mode": int(svc.virtual_mode),
            "autostart": int(svc.virtual_autostart),
            "enable": int(svc.virtual_enable),
            "startstop": int(svc.virtual_startstop),
            "manual_override_until": float(svc.manual_override_until),
            "auto_mode_cutover_pending": 1 if svc._auto_mode_cutover_pending else 0,
            "learned_charge_power_watts": getattr(svc, "learned_charge_power_watts", None),
            "learned_charge_power_updated_at": getattr(svc, "learned_charge_power_updated_at", None),
            "learned_charge_power_state": getattr(svc, "learned_charge_power_state", "unknown"),
            "learned_charge_power_learning_since": getattr(svc, "learned_charge_power_learning_since", None),
            "learned_charge_power_sample_count": int(getattr(svc, "learned_charge_power_sample_count", 0)),
            "learned_charge_power_phase": getattr(svc, "learned_charge_power_phase", None),
            "learned_charge_power_voltage": getattr(svc, "learned_charge_power_voltage", None),
            "learned_charge_power_signature_mismatch_sessions": int(
                getattr(svc, "learned_charge_power_signature_mismatch_sessions", 0)
            ),
            "learned_charge_power_signature_checked_session_started_at": getattr(
                svc,
                "learned_charge_power_signature_checked_session_started_at",
                None,
            ),
            "active_phase_selection": self._normalize_runtime_phase_selection(
                getattr(svc, "active_phase_selection", "P1")
            ),
            "requested_phase_selection": self._normalize_runtime_phase_selection(
                getattr(svc, "requested_phase_selection", "P1")
            ),
            "supported_phase_selections": list(
                self._normalize_runtime_supported_phase_selections(
                    getattr(svc, "supported_phase_selections", ("P1",))
                )
            ),
            "phase_switch_pending_selection": (
                None
                if getattr(svc, "_phase_switch_pending_selection", None) is None
                else self._normalize_runtime_phase_selection(
                    getattr(svc, "_phase_switch_pending_selection"),
                    getattr(svc, "requested_phase_selection", "P1"),
                )
            ),
            "phase_switch_state": self._normalize_phase_switch_state(
                getattr(svc, "_phase_switch_state", None)
            ),
            "phase_switch_requested_at": self._coerce_optional_runtime_past_time(
                getattr(svc, "_phase_switch_requested_at", None)
            ),
            "phase_switch_stable_until": self._coerce_optional_runtime_float(
                getattr(svc, "_phase_switch_stable_until", None)
            ),
            "phase_switch_resume_relay": 1 if bool(getattr(svc, "_phase_switch_resume_relay", False)) else 0,
            "phase_switch_mismatch_counts": dict(getattr(svc, "_phase_switch_mismatch_counts", {}) or {}),
            "phase_switch_last_mismatch_selection": (
                None
                if getattr(svc, "_phase_switch_last_mismatch_selection", None) is None
                else self._normalize_runtime_phase_selection(getattr(svc, "_phase_switch_last_mismatch_selection"))
            ),
            "phase_switch_last_mismatch_at": self._coerce_optional_runtime_past_time(
                getattr(svc, "_phase_switch_last_mismatch_at", None)
            ),
            "phase_switch_lockout_selection": (
                None
                if getattr(svc, "_phase_switch_lockout_selection", None) is None
                else self._normalize_runtime_phase_selection(getattr(svc, "_phase_switch_lockout_selection"))
            ),
            "phase_switch_lockout_reason": str(getattr(svc, "_phase_switch_lockout_reason", "") or ""),
            "phase_switch_lockout_at": self._coerce_optional_runtime_past_time(
                getattr(svc, "_phase_switch_lockout_at", None)
            ),
            "phase_switch_lockout_until": self._coerce_optional_runtime_float(
                getattr(svc, "_phase_switch_lockout_until", None)
            ),
            "contactor_fault_counts": dict(getattr(svc, "_contactor_fault_counts", {}) or {}),
            "contactor_fault_active_reason": str(getattr(svc, "_contactor_fault_active_reason", "") or "") or None,
            "contactor_fault_active_since": self._coerce_optional_runtime_past_time(
                getattr(svc, "_contactor_fault_active_since", None)
            ),
            "contactor_lockout_reason": str(getattr(svc, "_contactor_lockout_reason", "") or ""),
            "contactor_lockout_source": str(getattr(svc, "_contactor_lockout_source", "") or ""),
            "contactor_lockout_at": self._coerce_optional_runtime_past_time(
                getattr(svc, "_contactor_lockout_at", None)
            ),
            "relay_last_changed_at": svc.relay_last_changed_at,
            "relay_last_off_at": svc.relay_last_off_at,
        }

    @classmethod
    def _read_runtime_override_values(cls, path: str) -> dict[str, str]:
        """Return normalized runtime-override config values from one INI file."""
        if not str(path).strip():
            return {}
        parser = _CasePreservingConfigParser()
        try:
            read_files = parser.read(path)
        except Exception as error:  # pylint: disable=broad-except
            logging.warning("Unable to read runtime overrides from %s: %s", path, error)
            return {}
        if not read_files or not parser.has_section(RUNTIME_OVERRIDE_SECTION):
            return {}
        values: dict[str, str] = {}
        section = parser[RUNTIME_OVERRIDE_SECTION]
        for config_key, raw_value in section.items():
            spec = RUNTIME_OVERRIDE_BY_CONFIG_KEY.get(str(config_key).strip())
            if spec is None:
                continue
            values[spec.config_key] = str(raw_value).strip()
        return values

    @classmethod
    def _apply_runtime_overrides_to_config(
        cls,
        svc: Any,
        config: configparser.ConfigParser,
    ) -> configparser.ConfigParser:
        """Overlay persistent runtime overrides onto one loaded config parser."""
        defaults = config["DEFAULT"]
        path = cls.runtime_overrides_path(defaults)
        values = cls._read_runtime_override_values(path)
        for config_key, value in values.items():
            defaults[config_key] = str(value)
        svc.runtime_overrides_path = path
        svc._runtime_overrides_active = bool(values)
        svc._runtime_overrides_values = dict(values)
        svc._runtime_overrides_serialized = compact_json(values)
        return config

    @staticmethod
    def _override_value_as_text(spec: RuntimeOverrideSpec, value: object) -> str:
        """Return one runtime override as stable text for INI persistence."""
        if spec.value_kind == "bool":
            return str(int(bool(value)))
        if spec.value_kind == "int":
            return str(ServiceStateController.coerce_runtime_int(value))
        if spec.value_kind == "phase":
            return str(ServiceStateController._normalize_runtime_phase_selection(value))
        return str(ServiceStateController.coerce_runtime_float(value))

    def current_runtime_overrides(self) -> dict[str, str]:
        """Return the persistent runtime override payload as config-keyed text values."""
        svc = self.service
        values: dict[str, str] = {}
        for spec in RUNTIME_OVERRIDE_SPECS:
            values[spec.config_key] = self._override_value_as_text(spec, getattr(svc, spec.attr_name))
        return values

    def _serialized_runtime_overrides(self) -> str:
        """Return one stable serialized snapshot of the current runtime overrides."""
        return compact_json(self.current_runtime_overrides())

    def save_runtime_overrides(self) -> None:
        """Persist runtime-overridable DBus settings to a small INI file."""
        svc = self.service
        path = str(getattr(svc, "runtime_overrides_path", "")).strip()
        if not path:
            return
        payload = self.current_runtime_overrides()
        serialized = compact_json(payload)
        if serialized == getattr(svc, "_runtime_overrides_serialized", None):
            return
        parser = _CasePreservingConfigParser()
        parser[RUNTIME_OVERRIDE_SECTION] = payload
        try:
            from io import StringIO

            handle = StringIO()
            parser.write(handle)
            write_text_atomically(path, handle.getvalue())
            svc._runtime_overrides_serialized = serialized
            svc._runtime_overrides_active = True
            svc._runtime_overrides_values = dict(payload)
        except Exception as error:  # pylint: disable=broad-except
            logging.warning("Unable to write runtime overrides to %s: %s", path, error)

    def _serialized_runtime_state(self) -> str:
        """Return the normalized JSON string used for RAM-only state persistence."""
        return compact_json(self.current_runtime_state())

    @staticmethod
    def _coerce_optional_runtime_float(value: object) -> float | None:
        """Convert optional persisted timestamps to float-or-None."""
        if value is None:
            return None
        return ServiceStateController.coerce_runtime_float(value)

    @staticmethod
    def _coerce_optional_runtime_past_time(value: object, now: float | None = None) -> float | None:
        """Convert one persisted historical timestamp, rejecting implausible future values."""
        normalized = ServiceStateController._coerce_optional_runtime_float(value)
        if normalized is None:
            return None
        current = time.time() if now is None else float(now)
        if normalized > (current + 1.0):
            return None
        return normalized

    @staticmethod
    def _normalize_learned_charge_power_state(value: object) -> str:
        """Return one supported learned-power state string."""
        return normalize_learning_state(value)

    @staticmethod
    def _normalize_learned_charge_power_phase(value: object) -> str | None:
        """Return one supported learned-power phase signature."""
        return normalize_learning_phase(value)

    @staticmethod
    def _normalize_runtime_phase_selection(
        value: object,
        default: PhaseSelection = "P1",
    ) -> PhaseSelection:
        """Return one normalized service phase-selection state value."""
        return normalize_phase_selection(value, default)

    @staticmethod
    def _normalize_runtime_supported_phase_selections(
        value: object,
        default: tuple[PhaseSelection, ...] = ("P1",),
    ) -> tuple[PhaseSelection, ...]:
        """Return normalized supported service phase selections."""
        normalized: tuple[PhaseSelection, ...] = normalize_phase_selection_tuple(value, default)
        return normalized

    @staticmethod
    def _normalize_phase_switch_state(value: object) -> str | None:
        """Return one supported phase-switch orchestration state."""
        state = str(value).strip().lower() if value is not None else ""
        if state in {"waiting-relay-off", "stabilizing"}:
            return state
        return None

    @staticmethod
    def _read_runtime_state_payload(path: str) -> dict[str, object] | None:
        """Return one persisted runtime-state payload or None when unavailable."""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                loaded_state = json.load(handle)
        except FileNotFoundError:
            return None
        except Exception as error:  # pylint: disable=broad-except
            logging.warning("Unable to read runtime state from %s: %s", path, error)
            return None
        return cast(dict[str, object], loaded_state)

    @staticmethod
    def _runtime_load_time(svc: Any) -> float:
        """Return the current timestamp used while restoring persisted runtime state."""
        time_now = getattr(svc, "_time_now", None)
        raw_current_time: object = time_now() if callable(time_now) else time.time()
        return ServiceStateController.coerce_runtime_float(raw_current_time, time.time())

    def _restore_basic_runtime_state(self, svc: Any, state: dict[str, object]) -> None:
        """Restore mode, enable, and manual-override runtime values."""
        svc.virtual_mode = self._normalize_mode(state.get("mode", svc.virtual_mode))
        svc.virtual_autostart = self.coerce_runtime_int(state.get("autostart"), svc.virtual_autostart)
        svc.virtual_enable = self.coerce_runtime_int(state.get("enable"), svc.virtual_enable)
        svc.virtual_startstop = self.coerce_runtime_int(state.get("startstop"), svc.virtual_startstop)
        svc.manual_override_until = self.coerce_runtime_float(
            state.get("manual_override_until"),
            svc.manual_override_until,
        )
        svc._auto_mode_cutover_pending = bool(
            self.coerce_runtime_int(state.get("auto_mode_cutover_pending"), svc._auto_mode_cutover_pending)
        )
        # This flag is a one-shot runtime bypass, not durable session state.
        # Clear it on every service start, even if an older runtime-state file
        # still contains the legacy field.
        svc._ignore_min_offtime_once = False

    def _restore_learned_charge_power_state(
        self,
        svc: Any,
        state: dict[str, object],
        current_time: float,
    ) -> None:
        """Restore the persisted learned charging-power runtime state."""
        learned_charge_power_watts = state.get(
            "learned_charge_power_watts",
            getattr(svc, "learned_charge_power_watts", None),
        )
        learned_charge_power_updated_at = state.get(
            "learned_charge_power_updated_at",
            getattr(svc, "learned_charge_power_updated_at", None),
        )
        svc.learned_charge_power_watts = non_negative_float_or_none(learned_charge_power_watts)
        svc.learned_charge_power_updated_at = self._coerce_optional_runtime_past_time(
            learned_charge_power_updated_at,
            current_time,
        )
        svc.learned_charge_power_state = self._normalize_learned_charge_power_state(
            state.get("learned_charge_power_state", getattr(svc, "learned_charge_power_state", "unknown"))
        )
        svc.learned_charge_power_learning_since = self._coerce_optional_runtime_past_time(
            state.get(
                "learned_charge_power_learning_since",
                getattr(svc, "learned_charge_power_learning_since", None),
            ),
            current_time,
        )
        svc.learned_charge_power_sample_count = non_negative_int(
            state.get(
                "learned_charge_power_sample_count",
                getattr(svc, "learned_charge_power_sample_count", 0),
            ),
            0,
        )
        svc.learned_charge_power_phase = self._normalize_learned_charge_power_phase(
            state.get("learned_charge_power_phase", getattr(svc, "learned_charge_power_phase", None))
        )
        svc.learned_charge_power_voltage = non_negative_float_or_none(
            state.get("learned_charge_power_voltage", getattr(svc, "learned_charge_power_voltage", None))
        )
        svc.learned_charge_power_signature_mismatch_sessions = non_negative_int(
            state.get(
                "learned_charge_power_signature_mismatch_sessions",
                getattr(svc, "learned_charge_power_signature_mismatch_sessions", 0),
            ),
            0,
        )
        svc.learned_charge_power_signature_checked_session_started_at = self._coerce_optional_runtime_past_time(
            state.get(
                "learned_charge_power_signature_checked_session_started_at",
                getattr(svc, "learned_charge_power_signature_checked_session_started_at", None),
            ),
            current_time,
        )

    def _restore_phase_switch_runtime_state(
        self,
        svc: Any,
        state: dict[str, object],
        current_time: float,
    ) -> None:
        """Restore phase-selection and staged phase-switch runtime state."""
        supported_phase_selections = self._normalize_runtime_supported_phase_selections(
            state.get(
                "supported_phase_selections",
                getattr(svc, "supported_phase_selections", ("P1",)),
            )
        )
        svc.supported_phase_selections = supported_phase_selections
        default_phase_selection: PhaseSelection = supported_phase_selections[0]
        svc.requested_phase_selection = self._normalize_runtime_phase_selection(
            state.get(
                "requested_phase_selection",
                getattr(svc, "requested_phase_selection", default_phase_selection),
            ),
            default_phase_selection,
        )
        svc.active_phase_selection = self._normalize_runtime_phase_selection(
            state.get(
                "active_phase_selection",
                getattr(svc, "active_phase_selection", svc.requested_phase_selection),
            ),
            svc.requested_phase_selection,
        )
        pending_phase_selection = state.get(
            "phase_switch_pending_selection",
            getattr(svc, "_phase_switch_pending_selection", None),
        )
        svc._phase_switch_pending_selection = (
            None
            if pending_phase_selection is None
            else self._normalize_runtime_phase_selection(pending_phase_selection, svc.requested_phase_selection)
        )
        svc._phase_switch_state = self._normalize_phase_switch_state(
            state.get("phase_switch_state", getattr(svc, "_phase_switch_state", None))
        )
        svc._phase_switch_requested_at = self._coerce_optional_runtime_past_time(
            state.get("phase_switch_requested_at", getattr(svc, "_phase_switch_requested_at", None)),
            current_time,
        )
        svc._phase_switch_stable_until = self._coerce_optional_runtime_float(
            state.get("phase_switch_stable_until", getattr(svc, "_phase_switch_stable_until", None))
        )
        svc._phase_switch_resume_relay = bool(
            self.coerce_runtime_int(
                state.get("phase_switch_resume_relay", getattr(svc, "_phase_switch_resume_relay", False)),
                1 if bool(getattr(svc, "_phase_switch_resume_relay", False)) else 0,
            )
        )
        raw_mismatch_counts = state.get(
            "phase_switch_mismatch_counts",
            getattr(svc, "_phase_switch_mismatch_counts", {}),
        )
        svc._phase_switch_mismatch_counts = {}
        if isinstance(raw_mismatch_counts, dict):
            for raw_selection, raw_count in raw_mismatch_counts.items():
                normalized_selection = self._normalize_runtime_phase_selection(raw_selection, svc.requested_phase_selection)
                svc._phase_switch_mismatch_counts[normalized_selection] = non_negative_int(raw_count, 0)
        last_mismatch_selection = state.get(
            "phase_switch_last_mismatch_selection",
            getattr(svc, "_phase_switch_last_mismatch_selection", None),
        )
        svc._phase_switch_last_mismatch_selection = (
            None
            if last_mismatch_selection is None
            else self._normalize_runtime_phase_selection(last_mismatch_selection, svc.requested_phase_selection)
        )
        svc._phase_switch_last_mismatch_at = self._coerce_optional_runtime_past_time(
            state.get("phase_switch_last_mismatch_at", getattr(svc, "_phase_switch_last_mismatch_at", None)),
            current_time,
        )
        lockout_selection = state.get(
            "phase_switch_lockout_selection",
            getattr(svc, "_phase_switch_lockout_selection", None),
        )
        svc._phase_switch_lockout_selection = (
            None
            if lockout_selection is None
            else self._normalize_runtime_phase_selection(lockout_selection, svc.requested_phase_selection)
        )
        svc._phase_switch_lockout_reason = str(
            state.get("phase_switch_lockout_reason", getattr(svc, "_phase_switch_lockout_reason", "")) or ""
        )
        svc._phase_switch_lockout_at = self._coerce_optional_runtime_past_time(
            state.get("phase_switch_lockout_at", getattr(svc, "_phase_switch_lockout_at", None)),
            current_time,
        )
        svc._phase_switch_lockout_until = self._coerce_optional_runtime_float(
            state.get("phase_switch_lockout_until", getattr(svc, "_phase_switch_lockout_until", None))
        )
        if svc._phase_switch_state is None or svc._phase_switch_pending_selection is None:
            svc._phase_switch_pending_selection = None
            svc._phase_switch_state = None
            svc._phase_switch_requested_at = None
            svc._phase_switch_stable_until = None
            svc._phase_switch_resume_relay = False

    def _restore_relay_runtime_state(
        self,
        svc: Any,
        state: dict[str, object],
        current_time: float,
    ) -> None:
        """Restore relay timing markers used by the runtime logic."""
        relay_last_changed_at = state.get("relay_last_changed_at", svc.relay_last_changed_at)
        relay_last_off_at = state.get("relay_last_off_at", svc.relay_last_off_at)
        svc.relay_last_changed_at = self._coerce_optional_runtime_past_time(relay_last_changed_at, current_time)
        svc.relay_last_off_at = self._coerce_optional_runtime_past_time(relay_last_off_at, current_time)

    def _restore_contactor_runtime_state(
        self,
        svc: Any,
        state: dict[str, object],
        current_time: float,
    ) -> None:
        """Restore contactor-fault counters, active suspicion, and latched lockout state."""
        raw_fault_counts = state.get("contactor_fault_counts", getattr(svc, "_contactor_fault_counts", {}))
        svc._contactor_fault_counts = {}
        if isinstance(raw_fault_counts, dict):
            for raw_reason, raw_count in raw_fault_counts.items():
                reason = str(raw_reason).strip()
                if reason not in {"contactor-suspected-open", "contactor-suspected-welded"}:
                    continue
                svc._contactor_fault_counts[reason] = non_negative_int(raw_count, 0)
        active_reason = str(
            state.get("contactor_fault_active_reason", getattr(svc, "_contactor_fault_active_reason", "")) or ""
        ).strip()
        if active_reason not in {"contactor-suspected-open", "contactor-suspected-welded"}:
            active_reason = ""
        svc._contactor_fault_active_reason = active_reason or None
        svc._contactor_fault_active_since = self._coerce_optional_runtime_past_time(
            state.get("contactor_fault_active_since", getattr(svc, "_contactor_fault_active_since", None)),
            current_time,
        )
        svc._contactor_lockout_reason = str(
            state.get("contactor_lockout_reason", getattr(svc, "_contactor_lockout_reason", "")) or ""
        )
        if svc._contactor_lockout_reason not in {"contactor-suspected-open", "contactor-suspected-welded"}:
            svc._contactor_lockout_reason = ""
        svc._contactor_lockout_source = str(
            state.get("contactor_lockout_source", getattr(svc, "_contactor_lockout_source", "")) or ""
        )
        svc._contactor_lockout_at = self._coerce_optional_runtime_past_time(
            state.get("contactor_lockout_at", getattr(svc, "_contactor_lockout_at", None)),
            current_time,
        )

    def load_runtime_state(self) -> None:
        """Restore volatile runtime state from a RAM-backed file if present."""
        svc = self.service
        path = getattr(svc, "runtime_state_path", "").strip()
        if not path:
            return
        state = self._read_runtime_state_payload(path)
        if state is None:
            return
        current_time = self._runtime_load_time(svc)
        self._restore_basic_runtime_state(svc, state)
        self._restore_learned_charge_power_state(svc, state, current_time)
        self._restore_phase_switch_runtime_state(svc, state, current_time)
        self._restore_contactor_runtime_state(svc, state, current_time)
        self._restore_relay_runtime_state(svc, state, current_time)
        svc._runtime_state_serialized = self._serialized_runtime_state()
        logging.info("Restored runtime state from %s: %s", path, self.state_summary())

    def save_runtime_state(self) -> None:
        """Persist volatile runtime state to a RAM-backed file without touching flash."""
        svc = self.service
        path = getattr(svc, "runtime_state_path", "").strip()
        if not path:
            return

        payload = self._serialized_runtime_state()
        if payload == getattr(svc, "_runtime_state_serialized", None):
            return

        try:
            write_text_atomically(path, payload)
            svc._runtime_state_serialized = payload
            logging.debug("Saved runtime state to %s: %s", path, self.state_summary())
        except Exception as error:  # pylint: disable=broad-except
            logging.warning("Unable to write runtime state to %s: %s", path, error)

    @staticmethod
    def _clamp_min_int(svc: Any, attr_name: str, minimum: int, label: str, unit: str) -> None:
        """Clamp integer-like config values to a minimum."""
        value = getattr(svc, attr_name)
        if value >= minimum:
            return
        logging.warning("%s %s too small, clamping to %s%s", label, value, minimum, unit)
        setattr(svc, attr_name, minimum)

    @staticmethod
    def _clamp_non_negative_float(svc: Any, attr_name: str) -> None:
        """Clamp negative floating-point runtime settings to zero."""
        if not hasattr(svc, attr_name):
            return
        value = getattr(svc, attr_name)
        if value >= 0:
            return
        logging.warning("%s %s invalid, clamping to 0", attr_name, value)
        setattr(svc, attr_name, 0.0)

    @staticmethod
    def _clamp_positive_timeout(svc: Any, attr_name: str, minimum: float, label: str) -> None:
        """Clamp timeout-style config values to a positive default."""
        value = getattr(svc, attr_name)
        if value > 0:
            return
        logging.warning("%s %s invalid, clamping to %s", label, value, minimum)
        setattr(svc, attr_name, minimum)

    @staticmethod
    def _clamp_percentage(svc: Any, attr_name: str, label: str) -> None:
        """Clamp percentage settings to the inclusive 0..100 range."""
        value = getattr(svc, attr_name)
        if 0 <= value <= 100:
            return
        logging.warning("%s %s outside 0..100, clamping", label, value)
        setattr(svc, attr_name, min(100.0, max(0.0, value)))

    def _clamp_interval_settings(self) -> None:
        """Clamp all non-negative timing and retry intervals."""
        svc = self.service
        for attr_name in self.NON_NEGATIVE_INTERVAL_ATTRS:
            self._clamp_non_negative_float(svc, attr_name)

    def _clamp_soc_thresholds(self) -> None:
        """Clamp battery SOC thresholds and keep resume >= minimum."""
        svc = self.service
        self._clamp_percentage(svc, "auto_min_soc", "AutoMinSoc")
        self._clamp_percentage(svc, "auto_resume_soc", "AutoResumeSoc")
        if hasattr(svc, "auto_high_soc_threshold"):
            self._clamp_percentage(svc, "auto_high_soc_threshold", "AutoHighSocThreshold")
        if hasattr(svc, "auto_high_soc_release_threshold"):
            self._clamp_percentage(svc, "auto_high_soc_release_threshold", "AutoHighSocReleaseThreshold")
            if svc.auto_high_soc_release_threshold > svc.auto_high_soc_threshold:
                logging.warning(
                    "AutoHighSocReleaseThreshold %s above AutoHighSocThreshold %s, clamping",
                    svc.auto_high_soc_release_threshold,
                    svc.auto_high_soc_threshold,
                )
                svc.auto_high_soc_release_threshold = svc.auto_high_soc_threshold
        if svc.auto_resume_soc >= svc.auto_min_soc:
            return
        logging.warning(
            "AutoResumeSoc %s below AutoMinSoc %s, clamping to AutoMinSoc",
            svc.auto_resume_soc,
            svc.auto_min_soc,
        )
        svc.auto_resume_soc = svc.auto_min_soc

    @staticmethod
    def _clamp_surplus_pair(
        svc: Any,
        start_attr: str,
        stop_attr: str,
        start_label: str,
        stop_label: str,
    ) -> None:
        """Keep one stop surplus threshold at or below its start threshold."""
        start_value = getattr(svc, start_attr)
        stop_value = getattr(svc, stop_attr)
        if stop_value <= start_value:
            return
        logging.warning(
            "%s %s above %s %s, clamping",
            stop_label,
            stop_value,
            start_label,
            start_value,
        )
        setattr(svc, stop_attr, start_value)

    @staticmethod
    def _clamp_surplus_thresholds(svc: Any) -> None:
        """Keep all configured stop surplus thresholds at or below their start threshold."""
        ServiceStateController._clamp_surplus_pair(
            svc,
            "auto_start_surplus_watts",
            "auto_stop_surplus_watts",
            "AutoStartSurplusWatts",
            "AutoStopSurplusWatts",
        )
        if hasattr(svc, "auto_high_soc_start_surplus_watts") and hasattr(svc, "auto_high_soc_stop_surplus_watts"):
            ServiceStateController._clamp_surplus_pair(
                svc,
                "auto_high_soc_start_surplus_watts",
                "auto_high_soc_stop_surplus_watts",
                "AutoHighSocStartSurplusWatts",
                "AutoHighSocStopSurplusWatts",
            )

    @staticmethod
    def _clamp_fraction(svc: Any, attr_name: str, label: str, default: float) -> None:
        """Clamp fractional smoothing values into the safe 0..1 range."""
        value = getattr(svc, attr_name)
        if 0 < value <= 1:
            return
        logging.warning("%s %s outside (0,1], clamping to %s", label, value, default)
        setattr(svc, attr_name, float(default))

    def validate_runtime_config(self) -> None:
        """Clamp invalid runtime config values to safe defaults."""
        svc = self.service
        self._clamp_min_int(svc, "poll_interval_ms", 100, "PollIntervalMs", " ms")
        self._clamp_min_int(svc, "sign_of_life_minutes", 1, "SignOfLifeLog", " minute")
        self._clamp_min_int(svc, "auto_pv_max_services", 1, "AutoPvMaxServices", "")
        self._clamp_interval_settings()
        self._validate_startup_retry_config(svc)
        self._validate_timeout_settings(svc)
        if hasattr(svc, "auto_policy"):
            validate_auto_policy(svc.auto_policy, svc)
        else:
            self._validate_legacy_auto_config(svc)

    @staticmethod
    def _validate_startup_retry_config(svc: Any) -> None:
        """Clamp retry counters that must stay non-negative."""
        if svc.startup_device_info_retries >= 0:
            return
        logging.warning(
            "StartupDeviceInfoRetries %s invalid, clamping to 0",
            svc.startup_device_info_retries,
        )
        svc.startup_device_info_retries = 0

    @staticmethod
    def _validate_optional_non_negative_int(svc: Any, attr_name: str, label: str) -> None:
        """Clamp one optional integer runtime setting to zero or above."""
        if not hasattr(svc, attr_name):
            return
        value = getattr(svc, attr_name)
        if value >= 0:
            return
        logging.warning("%s %s invalid, clamping to 0", label, value)
        setattr(svc, attr_name, 0)

    def _validate_timeout_settings(self, svc: Any) -> None:
        """Clamp request and audit timeout-style settings."""
        timeout_specs = (
            ("shelly_request_timeout_seconds", 2.0, "ShellyRequestTimeoutSeconds"),
            ("dbus_method_timeout_seconds", 1.0, "DbusMethodTimeoutSeconds"),
        )
        optional_specs = (
            ("auto_audit_log_max_age_hours", 168.0, "AutoAuditLogMaxAgeHours"),
            ("auto_audit_log_repeat_seconds", 30.0, "AutoAuditLogRepeatSeconds"),
        )
        for attr_name, default, label in timeout_specs:
            self._clamp_positive_timeout(svc, attr_name, default, label)
        for attr_name, default, label in optional_specs:
            if hasattr(svc, attr_name):
                self._clamp_positive_timeout(svc, attr_name, default, label)

    def _validate_legacy_auto_config(self, svc: Any) -> None:
        """Clamp legacy Auto-mode attributes when no structured policy is attached yet."""
        self._clamp_legacy_non_negative_auto_values(svc)
        self._clamp_legacy_reference_power(svc)
        self._clamp_soc_thresholds()
        self._clamp_surplus_thresholds(svc)
        self._clamp_legacy_fractional_values(svc)
        self._clamp_legacy_volatility_band(svc)
        self._validate_optional_non_negative_int(
            svc,
            "auto_phase_mismatch_lockout_count",
            "AutoPhaseMismatchLockoutCount",
        )
        self._validate_optional_non_negative_int(
            svc,
            "auto_contactor_fault_latch_count",
            "AutoContactorFaultLatchCount",
        )

    def _clamp_legacy_non_negative_auto_values(self, svc: Any) -> None:
        """Clamp non-negative legacy Auto settings."""
        for attr_name in (
            "auto_grid_recovery_start_seconds",
            "auto_stop_surplus_delay_seconds",
            "auto_stop_surplus_volatility_low_watts",
            "auto_stop_surplus_volatility_high_watts",
            "auto_phase_upshift_headroom_watts",
            "auto_phase_downshift_margin_watts",
            "auto_learn_charge_power_min_watts",
            "auto_learn_charge_power_start_delay_seconds",
            "auto_learn_charge_power_window_seconds",
            "auto_learn_charge_power_max_age_seconds",
            "auto_phase_mismatch_retry_seconds",
            "auto_phase_mismatch_lockout_seconds",
            "auto_contactor_fault_latch_seconds",
        ):
            if hasattr(svc, attr_name):
                self._clamp_non_negative_float(svc, attr_name)

    @staticmethod
    def _clamp_legacy_reference_power(svc: Any) -> None:
        """Clamp the legacy adaptive-learning reference power."""
        if not hasattr(svc, "auto_reference_charge_power_watts") or svc.auto_reference_charge_power_watts > 0:
            return
        logging.warning(
            "AutoReferenceChargePowerWatts %s invalid, clamping to 1900.0",
            svc.auto_reference_charge_power_watts,
        )
        svc.auto_reference_charge_power_watts = 1900.0

    def _clamp_legacy_fractional_values(self, svc: Any) -> None:
        """Clamp smoothing and learning alpha values into the safe range."""
        fraction_specs = (
            ("auto_stop_ewma_alpha", "AutoStopEwmaAlpha", 0.35),
            ("auto_stop_ewma_alpha_stable", "AutoStopEwmaAlphaStable", 0.55),
            ("auto_stop_ewma_alpha_volatile", "AutoStopEwmaAlphaVolatile", 0.15),
            ("auto_learn_charge_power_alpha", "AutoLearnChargePowerAlpha", 0.2),
        )
        for attr_name, label, default in fraction_specs:
            if hasattr(svc, attr_name):
                self._clamp_fraction(svc, attr_name, label, default)

    @staticmethod
    def _clamp_legacy_volatility_band(svc: Any) -> None:
        """Ensure the high volatility threshold never drops below the low threshold."""
        if not (
            hasattr(svc, "auto_stop_surplus_volatility_low_watts")
            and hasattr(svc, "auto_stop_surplus_volatility_high_watts")
            and svc.auto_stop_surplus_volatility_high_watts < svc.auto_stop_surplus_volatility_low_watts
        ):
            return
        logging.warning(
            "AutoStopSurplusVolatilityHighWatts %s below AutoStopSurplusVolatilityLowWatts %s, clamping",
            svc.auto_stop_surplus_volatility_high_watts,
            svc.auto_stop_surplus_volatility_low_watts,
        )
        svc.auto_stop_surplus_volatility_high_watts = svc.auto_stop_surplus_volatility_low_watts

    def load_config(self) -> configparser.ConfigParser:
        """Load configuration or raise if the minimal settings are missing."""
        config = configparser.ConfigParser()
        config.read(self.config_path())
        if "DEFAULT" not in config or "Host" not in config["DEFAULT"]:
            raise ValueError(
                "deploy/venus/config.shelly_wallbox.ini is missing or incomplete. "
                "Copy it from the documented deploy/venus/config.shelly_wallbox.ini template and set DEFAULT Host."
            )
        return self._apply_runtime_overrides_to_config(self.service, config)
