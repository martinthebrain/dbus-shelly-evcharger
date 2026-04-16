# SPDX-License-Identifier: GPL-3.0-or-later
"""Bootstrap and service-registration helpers for the Shelly wallbox service.

This module is the place to look first when you want to understand how the
service comes up:
- read config
- normalize and validate wallbox state
- build controller objects
- register DBus paths
- start the helper/worker processes
- hand control over to the GLib main loop
"""

from __future__ import annotations

import configparser
from collections.abc import Callable
from typing import Any

from shelly_wallbox.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin
from shelly_wallbox.auto.policy import load_auto_policy_from_config
from shelly_wallbox.backend.config import load_backend_selection

MONTH_WINDOW_DEFAULTS: dict[int, tuple[str, str]] = {
    1: ("09:00", "16:30"),
    2: ("08:30", "17:15"),
    3: ("08:00", "18:00"),
    4: ("07:30", "19:30"),
    5: ("07:00", "20:30"),
    6: ("07:00", "21:00"),
    7: ("07:00", "21:00"),
    8: ("07:00", "20:30"),
    9: ("07:30", "19:30"),
    10: ("08:00", "18:00"),
    11: ("08:30", "17:00"),
    12: ("09:00", "16:30"),
}


def _config_value(defaults: configparser.SectionProxy, key: str, fallback: Any) -> str:
    """Return a config value while keeping SectionProxy.get() mypy-friendly."""
    return defaults.get(key, str(fallback))


def _seasonal_month_windows(
    config: configparser.ConfigParser,
    month_window_func: Callable[[configparser.ConfigParser, int, str, str], Any],
) -> dict[int, Any]:
    """Return the configured monthly daytime windows with sensible defaults."""
    return {
        month: month_window_func(config, month, start, end)
        for month, (start, end) in MONTH_WINDOW_DEFAULTS.items()
    }


class _ServiceBootstrapConfigMixin(_ComposableControllerMixin):
    def load_runtime_configuration(self) -> None:
        """Load the on-disk config and map it onto service attributes."""
        svc = self.service
        svc.config = svc._load_config()
        defaults = svc.config["DEFAULT"]
        self._load_identity_config(defaults)
        self._load_backend_config()
        self._load_auto_source_config(defaults)
        self._load_auto_policy_config(defaults)
        self._load_helper_and_timeout_config(defaults)
        svc._validate_runtime_config()

    def _load_identity_config(self, defaults: configparser.SectionProxy) -> None:
        """Load generic device, HTTP, and EV charger presentation settings."""
        svc = self.service
        svc.deviceinstance = int(_config_value(defaults, "DeviceInstance", 60))
        svc.host = defaults["Host"].strip()
        svc.phase = self._normalize_phase(defaults.get("Phase", "L1"))
        svc.position = int(_config_value(defaults, "Position", 1))
        svc.poll_interval_ms = int(_config_value(defaults, "PollIntervalMs", 1000))
        svc.sign_of_life_minutes = int(_config_value(defaults, "SignOfLifeLog", 10))
        svc.max_current = float(_config_value(defaults, "MaxCurrent", 16))
        svc.min_current = float(_config_value(defaults, "MinCurrent", 6))
        svc.display_learned_set_current = defaults.get("DisplayLearnedSetCurrent", "1").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        svc.charging_threshold_watts = float(_config_value(defaults, "ChargingThresholdWatts", 100))
        svc.idle_status = int(_config_value(defaults, "IdleStatus", 6))
        svc.voltage_mode = defaults.get("ThreePhaseVoltageMode", "phase").strip().lower()
        svc.username = defaults.get("Username", "").strip()
        svc.password = defaults.get("Password", "").strip()
        svc.use_digest_auth = defaults.get("DigestAuth", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        svc.pm_component = defaults.get("ShellyComponent", "Switch").strip()
        svc.pm_id = int(_config_value(defaults, "ShellyId", 0))
        svc.custom_name_override = defaults.get("Name", "").strip()
        svc.service_name = defaults.get("ServiceName", "com.victronenergy.evcharger").strip()
        svc.connection_name = defaults.get("Connection", "Shelly 1PM Gen4 RPC").strip()
        svc.runtime_state_path = defaults.get(
            "RuntimeStatePath",
            f"/run/dbus-shelly-wallbox-{svc.deviceinstance}.json",
        ).strip()

    def _load_auto_source_config(self, defaults: configparser.SectionProxy) -> None:
        """Load PV, battery, and grid source configuration for Auto mode."""
        svc = self.service
        svc.auto_pv_service = defaults.get("AutoPvService", "").strip()
        svc.auto_pv_service_prefix = defaults.get(
            "AutoPvServicePrefix",
            "com.victronenergy.pvinverter",
        ).strip()
        svc.auto_pv_path = defaults.get("AutoPvPath", "/Ac/Power").strip()
        svc.auto_pv_max_services = int(_config_value(defaults, "AutoPvMaxServices", 10))
        svc.auto_pv_scan_interval_seconds = float(_config_value(defaults, "AutoPvScanIntervalSeconds", 60))
        svc.auto_use_dc_pv = defaults.get("AutoUseDcPv", "1").strip().lower() in ("1", "true", "yes", "on")
        svc.auto_dc_pv_service = defaults.get("AutoDcPvService", "com.victronenergy.system").strip()
        svc.auto_dc_pv_path = defaults.get("AutoDcPvPath", "/Dc/Pv/Power").strip()
        svc.auto_battery_service = defaults.get(
            "AutoBatteryService",
            "com.victronenergy.battery.socketcan_can1",
        ).strip()
        svc.auto_battery_soc_path = defaults.get("AutoBatterySocPath", "/Soc").strip()
        svc.auto_battery_service_prefix = defaults.get(
            "AutoBatteryServicePrefix",
            "com.victronenergy.battery",
        ).strip()
        svc.auto_battery_scan_interval_seconds = float(_config_value(defaults, "AutoBatteryScanIntervalSeconds", 60))
        svc.auto_allow_without_battery_soc = defaults.get(
            "AutoAllowWithoutBatterySoc",
            "1",
        ).strip().lower() in ("1", "true", "yes", "on")
        svc.auto_dbus_backoff_base_seconds = float(_config_value(defaults, "AutoDbusBackoffBaseSeconds", 5))
        svc.auto_dbus_backoff_max_seconds = float(_config_value(defaults, "AutoDbusBackoffMaxSeconds", 60))
        svc.auto_grid_service = defaults.get("AutoGridService", "com.victronenergy.system").strip()
        svc.auto_grid_l1_path = defaults.get("AutoGridL1Path", "/Ac/Grid/L1/Power").strip()
        svc.auto_grid_l2_path = defaults.get("AutoGridL2Path", "/Ac/Grid/L2/Power").strip()
        svc.auto_grid_l3_path = defaults.get("AutoGridL3Path", "/Ac/Grid/L3/Power").strip()
        svc.auto_grid_require_all_phases = defaults.get(
            "AutoGridRequireAllPhases",
            "1",
        ).strip().lower() in ("1", "true", "yes", "on")
        svc.auto_grid_missing_stop_seconds = float(_config_value(defaults, "AutoGridMissingStopSeconds", 60))
        svc.auto_grid_recovery_start_seconds = float(
            _config_value(defaults, "AutoGridRecoveryStartSeconds", _config_value(defaults, "AutoStartDelaySeconds", 10))
        )

    def _load_backend_config(self) -> None:
        """Load normalized meter/switch/charger backend selection."""
        svc = self.service
        selection = load_backend_selection(svc.config)
        svc.backend_mode = selection.mode
        svc.meter_backend_type = selection.meter_type
        svc.switch_backend_type = selection.switch_type
        svc.charger_backend_type = selection.charger_type
        svc.meter_backend_config_path = selection.meter_config_path
        svc.switch_backend_config_path = selection.switch_config_path
        svc.charger_backend_config_path = selection.charger_config_path

    def _load_auto_policy_config(self, defaults: configparser.SectionProxy) -> None:
        """Load Auto thresholds, seasonal windows, and timing policy."""
        self._load_auto_surplus_thresholds(defaults)
        self._load_auto_timing_policy(defaults)
        self._load_auto_daytime_policy(defaults)

    def _load_auto_surplus_thresholds(self, defaults: configparser.SectionProxy) -> None:
        """Load Auto thresholds around surplus, SOC, and grid import."""
        svc = self.service
        load_auto_policy_from_config(defaults, svc)

    def _load_auto_timing_policy(self, defaults: configparser.SectionProxy) -> None:
        """Load averaging, runtime, and delay settings for Auto mode."""
        svc = self.service
        svc.auto_average_window_seconds = float(_config_value(defaults, "AutoAverageWindowSeconds", 30))
        svc.auto_min_runtime_seconds = float(_config_value(defaults, "AutoMinRuntimeSeconds", 300))
        svc.auto_min_offtime_seconds = float(_config_value(defaults, "AutoMinOfftimeSeconds", 120))
        svc.auto_start_delay_seconds = float(_config_value(defaults, "AutoStartDelaySeconds", 10))
        svc.auto_stop_delay_seconds = float(_config_value(defaults, "AutoStopDelaySeconds", 10))
        svc.auto_input_cache_seconds = float(_config_value(defaults, "AutoInputCacheSeconds", 120))
        svc.auto_audit_log = defaults.get("AutoAuditLog", "1").strip().lower() in ("1", "true", "yes", "on")
        svc.auto_audit_log_path = defaults.get(
            "AutoAuditLogPath",
            "/var/volatile/log/dbus-shelly-wallbox/auto-reasons.log",
        ).strip()
        svc.auto_audit_log_max_age_hours = float(_config_value(defaults, "AutoAuditLogMaxAgeHours", 168))
        svc.auto_audit_log_repeat_seconds = float(_config_value(defaults, "AutoAuditLogRepeatSeconds", 30))

    def _load_auto_daytime_policy(self, defaults: configparser.SectionProxy) -> None:
        """Load seasonal day-window behavior for Auto mode."""
        svc = self.service
        svc.auto_daytime_only = defaults.get("AutoDaytimeOnly", "1").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        svc.auto_month_windows = _seasonal_month_windows(svc.config, self._month_window)
        svc.auto_night_lock_stop = defaults.get("AutoNightLockStop", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    def _load_helper_and_timeout_config(self, defaults: configparser.SectionProxy) -> None:
        """Load helper-process, watchdog, and request timeout settings."""
        svc = self.service
        auto_input_poll_interval_ms = float(
            _config_value(
                defaults,
                "AutoInputPollIntervalMs",
                _config_value(defaults, "PollIntervalMs", 1000),
            )
        )
        svc.auto_pv_poll_interval_seconds = max(
            0.2,
            float(_config_value(defaults, "AutoPvPollIntervalMs", auto_input_poll_interval_ms)) / 1000.0,
        )
        svc.auto_grid_poll_interval_seconds = max(
            0.2,
            float(_config_value(defaults, "AutoGridPollIntervalMs", auto_input_poll_interval_ms)) / 1000.0,
        )
        svc.auto_battery_poll_interval_seconds = max(
            0.2,
            float(_config_value(defaults, "AutoBatteryPollIntervalMs", auto_input_poll_interval_ms)) / 1000.0,
        )
        svc.auto_input_validation_poll_seconds = max(
            5.0,
            float(_config_value(defaults, "AutoInputValidationPollSeconds", 30)),
        )
        svc.auto_input_snapshot_path = defaults.get(
            "AutoInputSnapshotPath",
            f"/run/dbus-shelly-wallbox-auto-{svc.deviceinstance}.json",
        ).strip()
        svc.auto_input_helper_restart_seconds = float(_config_value(defaults, "AutoInputHelperRestartSeconds", 5))
        svc.auto_input_helper_stale_seconds = float(_config_value(defaults, "AutoInputHelperStaleSeconds", 15))
        svc.auto_shelly_soft_fail_seconds = float(_config_value(defaults, "AutoShellySoftFailSeconds", 10))
        svc.auto_contactor_fault_latch_count = int(_config_value(defaults, "AutoContactorFaultLatchCount", 3))
        svc.auto_contactor_fault_latch_seconds = float(_config_value(defaults, "AutoContactorFaultLatchSeconds", 60))
        svc.auto_watchdog_stale_seconds = float(_config_value(defaults, "AutoWatchdogStaleSeconds", 180))
        svc.auto_watchdog_recovery_seconds = float(_config_value(defaults, "AutoWatchdogRecoverySeconds", 60))
        svc.auto_startup_warmup_seconds = float(_config_value(defaults, "AutoStartupWarmupSeconds", 15))
        svc.auto_manual_override_seconds = float(_config_value(defaults, "AutoManualOverrideSeconds", 300))
        svc.startup_device_info_retries = int(_config_value(defaults, "StartupDeviceInfoRetries", 3))
        svc.startup_device_info_retry_seconds = float(_config_value(defaults, "StartupDeviceInfoRetrySeconds", 2))
        svc.shelly_request_timeout_seconds = float(_config_value(defaults, "ShellyRequestTimeoutSeconds", 2.0))
        svc.dbus_method_timeout_seconds = float(_config_value(defaults, "DbusMethodTimeoutSeconds", 1.0))
