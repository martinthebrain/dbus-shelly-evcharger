# SPDX-License-Identifier: GPL-3.0-or-later
"""Bootstrap and service-registration helpers for the Venus EV charger service.

This module is the place to look first when you want to understand how the
service comes up:
- read config
- normalize and validate wallbox state
- build controller objects
- register DBus paths
- start the helper/worker processes
- hand control over to the GLib main loop

This module is effectively the assembly line for the service. It turns the
configuration file into a ready-to-run runtime object graph and fills the
service instance with the normalized attributes the rest of the codebase
expects.
"""

from __future__ import annotations

import configparser
from collections.abc import Callable
from typing import Any

from venus_evcharger.auto.policy import load_auto_policy_from_config
from venus_evcharger.backend.config import load_backend_selection
from venus_evcharger.core.common import DEFAULT_SCHEDULED_ENABLED_DAYS, normalize_hhmm_text, scheduled_enabled_days_text
from venus_evcharger.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin

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
# The month-window defaults are a practical baseline for daytime-oriented
# charging behavior. Installations can tune them freely, but keeping the full
# year visible in one table makes the default seasonal shape easy to review.


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
        """Load the on-disk config and map it onto service attributes.

        The loading order is important:

        1. identity and basic runtime paths
        2. backend topology
        3. DBus input sources
        4. Auto and Scheduled policy
        5. helper and timeout behavior

        That order mirrors how a person would usually think about a deployment:
        first "what is this service", then "what hardware is attached", then
        "how should it behave".
        """
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
            f"/run/dbus-venus-evcharger-{svc.deviceinstance}.json",
        ).strip()
        svc.runtime_overrides_path = defaults.get(
            "RuntimeOverridesPath",
            getattr(svc, "runtime_overrides_path", f"/run/dbus-venus-evcharger-overrides-{svc.deviceinstance}.ini"),
        ).strip()
        svc.control_api_enabled = defaults.get("ControlApiEnabled", "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        svc.control_api_host = defaults.get("ControlApiHost", "127.0.0.1").strip() or "127.0.0.1"
        svc.control_api_port = int(_config_value(defaults, "ControlApiPort", 8765))
        svc.control_api_auth_token = defaults.get("ControlApiAuthToken", "").strip()
        svc.control_api_read_token = defaults.get("ControlApiReadToken", "").strip()
        svc.control_api_control_token = defaults.get("ControlApiControlToken", "").strip()
        svc.control_api_admin_token = defaults.get("ControlApiAdminToken", "").strip()
        svc.control_api_update_token = defaults.get("ControlApiUpdateToken", "").strip()
        svc.control_api_audit_path = defaults.get(
            "ControlApiAuditPath",
            f"/run/dbus-venus-evcharger-control-audit-{svc.deviceinstance}.jsonl",
        ).strip()
        svc.control_api_audit_max_entries = int(_config_value(defaults, "ControlApiAuditMaxEntries", 200))
        svc.control_api_idempotency_path = defaults.get(
            "ControlApiIdempotencyPath",
            f"/run/dbus-venus-evcharger-idempotency-{svc.deviceinstance}.json",
        ).strip()
        svc.control_api_idempotency_max_entries = int(_config_value(defaults, "ControlApiIdempotencyMaxEntries", 200))
        svc.control_api_rate_limit_max_requests = int(_config_value(defaults, "ControlApiRateLimitMaxRequests", 30))
        svc.control_api_rate_limit_window_seconds = float(
            _config_value(defaults, "ControlApiRateLimitWindowSeconds", 5.0)
        )
        svc.control_api_critical_cooldown_seconds = float(
            _config_value(defaults, "ControlApiCriticalCooldownSeconds", 2.0)
        )
        svc.control_api_localhost_only = defaults.get("ControlApiLocalhostOnly", "1").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        svc.control_api_unix_socket_path = defaults.get("ControlApiUnixSocketPath", "").strip()
        svc.control_api_listen_host = ""
        svc.control_api_listen_port = 0
        svc.control_api_bound_unix_socket_path = ""

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
        """Load normalized meter/switch/charger backend selection.

        Backend selection is normalized early because many later config
        decisions depend on the topology shape. A charger-native setup and a
        relay-driven setup expose different valid combinations of meter,
        switch, and charger roles.
        """
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
            "/var/volatile/log/dbus-venus-evcharger/auto-reasons.log",
        ).strip()
        svc.auto_audit_log_max_age_hours = float(_config_value(defaults, "AutoAuditLogMaxAgeHours", 168))
        svc.auto_audit_log_repeat_seconds = float(_config_value(defaults, "AutoAuditLogRepeatSeconds", 30))

    def _load_auto_daytime_policy(self, defaults: configparser.SectionProxy) -> None:
        """Load seasonal day-window behavior for Auto mode.

        The same section also carries Scheduled-mode inputs because Scheduled is
        modeled as "Auto in the daytime window plus a target-day night boost".
        Keeping both in one loader makes that relationship easier to spot.
        """
        svc = self.service
        svc.auto_daytime_only = defaults.get("AutoDaytimeOnly", "1").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        svc.auto_month_windows = _seasonal_month_windows(svc.config, self._month_window)
        svc.auto_scheduled_night_start_delay_seconds = float(
            _config_value(defaults, "AutoScheduledNightStartDelaySeconds", 3600)
        )
        svc.auto_scheduled_enabled_days = scheduled_enabled_days_text(
            defaults.get("AutoScheduledEnabledDays", "Mon,Tue,Wed,Thu,Fri"),
            DEFAULT_SCHEDULED_ENABLED_DAYS,
        )
        svc.auto_scheduled_latest_end_time = normalize_hhmm_text(
            defaults.get("AutoScheduledLatestEndTime", "06:30"),
            "06:30",
        )
        svc.auto_scheduled_night_current_amps = float(
            _config_value(defaults, "AutoScheduledNightCurrentAmps", 0)
        )
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
            f"/run/dbus-venus-evcharger-auto-{svc.deviceinstance}.json",
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
