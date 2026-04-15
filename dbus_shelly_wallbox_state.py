# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration and RAM-only runtime-state helpers for the Shelly wallbox service."""

import configparser
import json
import logging
import os
import time
from typing import Any, Callable

from dbus_shelly_wallbox_auto_policy import validate_auto_policy
from dbus_shelly_wallbox_backend_types import (
    PhaseSelection,
    normalize_phase_selection,
    normalize_phase_selection_tuple,
)
from dbus_shelly_wallbox_contracts import (
    finite_float_or_none,
    non_negative_float_or_none,
    non_negative_int,
    normalize_learning_phase,
    normalize_learning_state,
)
from dbus_shelly_wallbox_shared import compact_json, write_text_atomically


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
        return os.path.join(os.path.dirname(os.path.realpath(__file__)), "config.shelly_wallbox.ini")

    def state_summary(self) -> str:
        """Return a compact runtime state summary for debug logging."""
        svc = self.service
        charger_target = finite_float_or_none(getattr(svc, "_charger_target_current_amps", None))
        charger_target_text = "na" if charger_target is None else f"{charger_target:.1f}"
        charger_status = str(getattr(svc, "_last_charger_state_status", "") or "").strip() or "na"
        charger_fault = str(getattr(svc, "_last_charger_state_fault", "") or "").strip() or "na"
        status_source = str(getattr(svc, "_last_status_source", "") or "").strip() or "unknown"
        return (
            f"mode={getattr(svc, 'virtual_mode', 'na')} "
            f"enable={getattr(svc, 'virtual_enable', 'na')} "
            f"startstop={getattr(svc, 'virtual_startstop', 'na')} "
            f"autostart={getattr(svc, 'virtual_autostart', 'na')} "
            f"cutover={int(bool(getattr(svc, '_auto_mode_cutover_pending', False)))} "
            f"ignore_offtime={int(bool(getattr(svc, '_ignore_min_offtime_once', False)))} "
            f"phase={getattr(svc, 'active_phase_selection', 'na')} "
            f"phase_req={getattr(svc, 'requested_phase_selection', 'na')} "
            f"phase_switch={getattr(svc, '_phase_switch_state', 'na') or 'idle'} "
            f"backend={getattr(svc, 'backend_mode', 'combined')} "
            f"meter_backend={getattr(svc, 'meter_backend_type', 'shelly_combined')} "
            f"switch_backend={getattr(svc, 'switch_backend_type', 'shelly_combined')} "
            f"charger_backend={getattr(svc, 'charger_backend_type', None) or 'na'} "
            f"charger_target={charger_target_text} "
            f"charger_status={charger_status} "
            f"charger_fault={charger_fault} "
            f"status_source={status_source} "
            f"auto_state={getattr(svc, '_last_auto_state', 'na')} "
            f"health={getattr(svc, '_last_health_reason', 'na')}"
        )

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
            "relay_last_changed_at": svc.relay_last_changed_at,
            "relay_last_off_at": svc.relay_last_off_at,
        }

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

    def load_runtime_state(self) -> None:
        """Restore volatile runtime state from a RAM-backed file if present."""
        svc = self.service
        path = getattr(svc, "runtime_state_path", "").strip()
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                state = json.load(handle)
        except FileNotFoundError:
            return
        except Exception as error:  # pylint: disable=broad-except
            logging.warning("Unable to read runtime state from %s: %s", path, error)
            return

        time_now = getattr(svc, "_time_now", None)
        raw_current_time: object = time_now() if callable(time_now) else time.time()
        current_time = self.coerce_runtime_float(raw_current_time, time.time())
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
        if svc._phase_switch_state is None or svc._phase_switch_pending_selection is None:
            svc._phase_switch_pending_selection = None
            svc._phase_switch_state = None
            svc._phase_switch_requested_at = None
            svc._phase_switch_stable_until = None
            svc._phase_switch_resume_relay = False
        relay_last_changed_at = state.get("relay_last_changed_at", svc.relay_last_changed_at)
        relay_last_off_at = state.get("relay_last_off_at", svc.relay_last_off_at)
        svc.relay_last_changed_at = self._coerce_optional_runtime_past_time(relay_last_changed_at, current_time)
        svc.relay_last_off_at = self._coerce_optional_runtime_past_time(relay_last_off_at, current_time)
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

    def _clamp_legacy_non_negative_auto_values(self, svc: Any) -> None:
        """Clamp non-negative legacy Auto settings."""
        for attr_name in (
            "auto_grid_recovery_start_seconds",
            "auto_stop_surplus_delay_seconds",
            "auto_stop_surplus_volatility_low_watts",
            "auto_stop_surplus_volatility_high_watts",
            "auto_learn_charge_power_min_watts",
            "auto_learn_charge_power_start_delay_seconds",
            "auto_learn_charge_power_window_seconds",
            "auto_learn_charge_power_max_age_seconds",
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
                "config.shelly_wallbox.ini is missing or incomplete. "
                "Copy it from config.shelly_wallbox.example and set DEFAULT Host."
            )
        return config
