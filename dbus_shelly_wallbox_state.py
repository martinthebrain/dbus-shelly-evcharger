# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration and RAM-only runtime-state helpers for the Shelly wallbox service."""

import configparser
import json
import logging
import os
from dbus_shelly_wallbox_auto_policy import validate_auto_policy
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

    def __init__(self, service, normalize_mode_func):
        self.service = service
        self._normalize_mode = normalize_mode_func

    @staticmethod
    def config_path():
        """Return the path to the local Shelly wallbox config file."""
        return os.path.join(os.path.dirname(os.path.realpath(__file__)), "config.shelly_wallbox.ini")

    def state_summary(self):
        """Return a compact runtime state summary for debug logging."""
        svc = self.service
        return (
            f"mode={getattr(svc, 'virtual_mode', 'na')} "
            f"enable={getattr(svc, 'virtual_enable', 'na')} "
            f"startstop={getattr(svc, 'virtual_startstop', 'na')} "
            f"autostart={getattr(svc, 'virtual_autostart', 'na')} "
            f"cutover={int(bool(getattr(svc, '_auto_mode_cutover_pending', False)))} "
            f"ignore_offtime={int(bool(getattr(svc, '_ignore_min_offtime_once', False)))} "
            f"health={getattr(svc, '_last_health_reason', 'na')}"
        )

    @staticmethod
    def coerce_runtime_int(value, default=0):
        """Convert persisted runtime values to int safely."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def coerce_runtime_float(value, default=0.0):
        """Convert persisted runtime values to float safely."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    def current_runtime_state(self):
        """Return the volatile runtime state that should survive service restarts."""
        svc = self.service
        return {
            "mode": int(svc.virtual_mode),
            "autostart": int(svc.virtual_autostart),
            "enable": int(svc.virtual_enable),
            "startstop": int(svc.virtual_startstop),
            "manual_override_until": float(svc.manual_override_until),
            "auto_mode_cutover_pending": 1 if svc._auto_mode_cutover_pending else 0,
            "ignore_min_offtime_once": 1 if svc._ignore_min_offtime_once else 0,
            "relay_last_changed_at": svc.relay_last_changed_at,
            "relay_last_off_at": svc.relay_last_off_at,
        }

    def _serialized_runtime_state(self):
        """Return the normalized JSON string used for RAM-only state persistence."""
        return compact_json(self.current_runtime_state())

    @staticmethod
    def _coerce_optional_runtime_float(value):
        """Convert optional persisted timestamps to float-or-None."""
        if value is None:
            return None
        return ServiceStateController.coerce_runtime_float(value)

    def load_runtime_state(self):
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
        svc._ignore_min_offtime_once = bool(
            self.coerce_runtime_int(state.get("ignore_min_offtime_once"), svc._ignore_min_offtime_once)
        )
        relay_last_changed_at = state.get("relay_last_changed_at", svc.relay_last_changed_at)
        relay_last_off_at = state.get("relay_last_off_at", svc.relay_last_off_at)
        svc.relay_last_changed_at = self._coerce_optional_runtime_float(relay_last_changed_at)
        svc.relay_last_off_at = self._coerce_optional_runtime_float(relay_last_off_at)
        svc._runtime_state_serialized = self._serialized_runtime_state()
        logging.info("Restored runtime state from %s: %s", path, self.state_summary())

    def save_runtime_state(self):
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
    def _clamp_min_int(svc, attr_name, minimum, label, unit):
        """Clamp integer-like config values to a minimum."""
        value = getattr(svc, attr_name)
        if value >= minimum:
            return
        logging.warning("%s %s too small, clamping to %s%s", label, value, minimum, unit)
        setattr(svc, attr_name, minimum)

    @staticmethod
    def _clamp_non_negative_float(svc, attr_name):
        """Clamp negative floating-point runtime settings to zero."""
        if not hasattr(svc, attr_name):
            return
        value = getattr(svc, attr_name)
        if value >= 0:
            return
        logging.warning("%s %s invalid, clamping to 0", attr_name, value)
        setattr(svc, attr_name, 0.0)

    @staticmethod
    def _clamp_positive_timeout(svc, attr_name, minimum, label):
        """Clamp timeout-style config values to a positive default."""
        value = getattr(svc, attr_name)
        if value > 0:
            return
        logging.warning("%s %s invalid, clamping to %s", label, value, minimum)
        setattr(svc, attr_name, minimum)

    @staticmethod
    def _clamp_percentage(svc, attr_name, label):
        """Clamp percentage settings to the inclusive 0..100 range."""
        value = getattr(svc, attr_name)
        if 0 <= value <= 100:
            return
        logging.warning("%s %s outside 0..100, clamping", label, value)
        setattr(svc, attr_name, min(100.0, max(0.0, value)))

    def _clamp_interval_settings(self):
        """Clamp all non-negative timing and retry intervals."""
        svc = self.service
        for attr_name in self.NON_NEGATIVE_INTERVAL_ATTRS:
            self._clamp_non_negative_float(svc, attr_name)

    def _clamp_soc_thresholds(self):
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
    def _clamp_surplus_pair(svc, start_attr, stop_attr, start_label, stop_label):
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
    def _clamp_surplus_thresholds(svc):
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
    def _clamp_fraction(svc, attr_name, label, default):
        """Clamp fractional smoothing values into the safe 0..1 range."""
        value = getattr(svc, attr_name)
        if 0 < value <= 1:
            return
        logging.warning("%s %s outside (0,1], clamping to %s", label, value, default)
        setattr(svc, attr_name, float(default))

    def validate_runtime_config(self):
        """Clamp invalid runtime config values to safe defaults."""
        svc = self.service
        self._clamp_min_int(svc, "poll_interval_ms", 100, "PollIntervalMs", " ms")
        self._clamp_min_int(svc, "sign_of_life_minutes", 1, "SignOfLifeLog", " minute")
        self._clamp_min_int(svc, "auto_pv_max_services", 1, "AutoPvMaxServices", "")
        self._clamp_interval_settings()
        if svc.startup_device_info_retries < 0:
            logging.warning(
                "StartupDeviceInfoRetries %s invalid, clamping to 0",
                svc.startup_device_info_retries,
            )
            svc.startup_device_info_retries = 0
        self._clamp_positive_timeout(
            svc,
            "shelly_request_timeout_seconds",
            2.0,
            "ShellyRequestTimeoutSeconds",
        )
        self._clamp_positive_timeout(
            svc,
            "dbus_method_timeout_seconds",
            1.0,
            "DbusMethodTimeoutSeconds",
        )
        if hasattr(svc, "auto_audit_log_max_age_hours"):
            self._clamp_positive_timeout(
                svc,
                "auto_audit_log_max_age_hours",
                168.0,
                "AutoAuditLogMaxAgeHours",
            )
        if hasattr(svc, "auto_audit_log_repeat_seconds"):
            self._clamp_positive_timeout(
                svc,
                "auto_audit_log_repeat_seconds",
                30.0,
                "AutoAuditLogRepeatSeconds",
            )
        if hasattr(svc, "auto_policy"):
            validate_auto_policy(svc.auto_policy, svc)
        else:
            for attr_name in (
                "auto_grid_recovery_start_seconds",
                "auto_stop_surplus_delay_seconds",
                "auto_stop_surplus_volatility_low_watts",
                "auto_stop_surplus_volatility_high_watts",
            ):
                if hasattr(svc, attr_name):
                    self._clamp_non_negative_float(svc, attr_name)
            self._clamp_soc_thresholds()
            self._clamp_surplus_thresholds(svc)
            if hasattr(svc, "auto_stop_ewma_alpha"):
                self._clamp_fraction(svc, "auto_stop_ewma_alpha", "AutoStopEwmaAlpha", 0.35)
            if hasattr(svc, "auto_stop_ewma_alpha_stable"):
                self._clamp_fraction(svc, "auto_stop_ewma_alpha_stable", "AutoStopEwmaAlphaStable", 0.55)
            if hasattr(svc, "auto_stop_ewma_alpha_volatile"):
                self._clamp_fraction(svc, "auto_stop_ewma_alpha_volatile", "AutoStopEwmaAlphaVolatile", 0.15)
            if (
                hasattr(svc, "auto_stop_surplus_volatility_low_watts")
                and hasattr(svc, "auto_stop_surplus_volatility_high_watts")
                and svc.auto_stop_surplus_volatility_high_watts < svc.auto_stop_surplus_volatility_low_watts
            ):
                logging.warning(
                    "AutoStopSurplusVolatilityHighWatts %s below AutoStopSurplusVolatilityLowWatts %s, clamping",
                    svc.auto_stop_surplus_volatility_high_watts,
                    svc.auto_stop_surplus_volatility_low_watts,
                )
                svc.auto_stop_surplus_volatility_high_watts = svc.auto_stop_surplus_volatility_low_watts

    def load_config(self):
        """Load configuration or raise if the minimal settings are missing."""
        config = configparser.ConfigParser()
        config.read(self.config_path())
        if "DEFAULT" not in config or "Host" not in config["DEFAULT"]:
            raise ValueError(
                "config.shelly_wallbox.ini is missing or incomplete. "
                "Copy it from config.shelly_wallbox.example and set DEFAULT Host."
            )
        return config
