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
import faulthandler
import logging
import os
import platform
import signal
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from dbus_shelly_wallbox_auto_controller import AutoDecisionController
from dbus_shelly_wallbox_auto_policy import load_auto_policy_from_config
from dbus_shelly_wallbox_auto_input_supervisor import AutoInputSupervisor
from dbus_shelly_wallbox_ports import AutoDecisionPort, UpdateCyclePort, WriteControllerPort
from dbus_shelly_wallbox_publisher import DbusPublishController
from dbus_shelly_wallbox_runtime_support import RuntimeSupportController
from dbus_shelly_wallbox_shelly_io import ShellyIoController
from dbus_shelly_wallbox_state import ServiceStateController
from dbus_shelly_wallbox_update_cycle import UpdateCycleController
from dbus_shelly_wallbox_write_controller import DbusWriteController
from vedbus import VeDbusService


PathSpec = tuple[Any, Callable[[Any, Any], str] | None]
PathMap = dict[str, PathSpec]

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


def _logging_level_from_config(config: configparser.ConfigParser, default: str = "INFO") -> str:
    """Read the configured log level from the DEFAULT section."""
    if "DEFAULT" not in config:
        return default
    return config["DEFAULT"].get("Logging", default).upper()


def _enable_fault_diagnostics() -> None:
    """Enable crash diagnostics when available."""
    try:
        faulthandler.enable(all_threads=True)
    except Exception as error:  # pylint: disable=broad-except
        logging.debug("faulthandler.enable() unavailable: %s", error)


def _install_signal_logging(quit_callback: Callable[[], None] | None = None) -> None:
    """Install signal handlers that log and request a clean GLib-loop shutdown."""

    def _log_signal(signum, _frame):
        logging.warning("Received signal %s in pid=%s", signum, os.getpid())
        if quit_callback is None:
            return
        try:
            quit_callback()
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Unable to request shutdown after signal %s: %s", signum, error)

    for signum in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None), getattr(signal, "SIGHUP", None)):
        if signum is None:
            continue
        try:
            signal.signal(signum, _log_signal)
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Unable to install signal handler for %s: %s", signum, error)


def _setup_dbus_mainloop() -> None:
    """Prepare dbus-python and GLib to run the Venus event loop."""
    from dbus.mainloop.glib import DBusGMainLoop  # pylint: disable=import-error
    import dbus.mainloop.glib as dbus_glib_mainloop  # pylint: disable=import-error

    try:
        dbus_glib_mainloop.threads_init()
    except AttributeError:
        logging.debug("dbus.mainloop.glib.threads_init() not available on this runtime")

    DBusGMainLoop(set_as_default=True)


def _request_mainloop_quit(gobject_module: Any, mainloop: Any) -> None:
    """Request a clean GLib shutdown, preferring idle_add when available."""
    idle_add = getattr(gobject_module, "idle_add", None)
    if callable(idle_add):
        try:
            idle_add(mainloop.quit)
            return
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Unable to schedule GLib shutdown via idle_add: %s", error)
    mainloop.quit()


def _run_service_loop(service_class: Callable[[], Any], gobject_module: Any) -> None:
    """Instantiate the service and enter the GLib main loop."""
    service_class()
    mainloop = gobject_module.MainLoop()
    _install_signal_logging(lambda: _request_mainloop_quit(gobject_module, mainloop))
    logging.info("Connected to dbus, and switching over to gobject.MainLoop() (= event based)")
    mainloop.run()


class ServiceBootstrapController:
    """Encapsulate config loading, DBus registration, and service startup."""

    WRITABLE_PATHS = {
        "/MinCurrent",
        "/MaxCurrent",
        "/SetCurrent",
        "/AutoStart",
        "/Mode",
        "/StartStop",
        "/Enable",
    }

    def __init__(
        self,
        service: Any,
        *,
        normalize_phase_func: Callable[[Any], str],
        normalize_mode_func: Callable[[Any], int],
        mode_uses_auto_logic_func: Callable[[Any], bool],
        month_window_func: Callable[[configparser.ConfigParser, int, str, str], Any],
        age_seconds_func: Callable[[float | int | None, float | int | None], int],
        health_code_func: Callable[[str], int],
        phase_values_func: Callable[..., Any],
        read_version_func: Callable[[str], str],
        gobject_module: Any,
        script_path: str,
        formatters: dict[str, Callable[[Any, Any], str] | None],
    ) -> None:
        self.service = service
        self._normalize_phase = normalize_phase_func
        self._normalize_mode = normalize_mode_func
        self._mode_uses_auto_logic = mode_uses_auto_logic_func
        self._month_window = month_window_func
        self._age_seconds = age_seconds_func
        self._health_code = health_code_func
        self._phase_values = phase_values_func
        self._read_version = read_version_func
        self._gobject = gobject_module
        self._script_path = script_path
        self._formatters = formatters

    def initialize_service(self) -> None:
        """Fully initialize the wallbox service instance."""
        self.load_runtime_configuration()
        self.initialize_controllers()
        self.initialize_virtual_state()
        self.restore_runtime_state()
        self.initialize_dbus_service()
        self.apply_device_metadata()
        self.register_paths()
        self.start_runtime_loops()

    def load_runtime_configuration(self) -> None:
        """Load the on-disk config and map it onto service attributes."""
        svc = self.service
        svc.config = svc._load_config()
        defaults = svc.config["DEFAULT"]
        self._load_identity_config(defaults)
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

    def _load_auto_policy_config(self, defaults: configparser.SectionProxy) -> None:
        """Load Auto thresholds, seasonal windows, and timing policy."""
        svc = self.service
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
        svc.auto_input_snapshot_path = defaults.get(
            "AutoInputSnapshotPath",
            f"/run/dbus-shelly-wallbox-auto-{svc.deviceinstance}.json",
        ).strip()
        svc.auto_input_helper_restart_seconds = float(_config_value(defaults, "AutoInputHelperRestartSeconds", 5))
        svc.auto_input_helper_stale_seconds = float(_config_value(defaults, "AutoInputHelperStaleSeconds", 15))
        svc.auto_shelly_soft_fail_seconds = float(_config_value(defaults, "AutoShellySoftFailSeconds", 10))
        svc.auto_watchdog_stale_seconds = float(_config_value(defaults, "AutoWatchdogStaleSeconds", 180))
        svc.auto_watchdog_recovery_seconds = float(_config_value(defaults, "AutoWatchdogRecoverySeconds", 60))
        svc.auto_startup_warmup_seconds = float(_config_value(defaults, "AutoStartupWarmupSeconds", 15))
        svc.auto_manual_override_seconds = float(_config_value(defaults, "AutoManualOverrideSeconds", 300))
        svc.startup_device_info_retries = int(_config_value(defaults, "StartupDeviceInfoRetries", 3))
        svc.startup_device_info_retry_seconds = float(_config_value(defaults, "StartupDeviceInfoRetrySeconds", 2))
        svc.shelly_request_timeout_seconds = float(_config_value(defaults, "ShellyRequestTimeoutSeconds", 2.0))
        svc.dbus_method_timeout_seconds = float(_config_value(defaults, "DbusMethodTimeoutSeconds", 1.0))

    def initialize_controllers(self) -> None:
        """Create the controller objects used by the service runtime."""
        svc = self.service
        svc._runtime_support_controller = RuntimeSupportController(svc, self._age_seconds, self._health_code)
        svc._runtime_support_controller.initialize_runtime_support()
        svc._auto_controller = AutoDecisionController(
            AutoDecisionPort(svc),
            self._health_code,
            self._mode_uses_auto_logic,
        )
        svc._dbus_publisher = DbusPublishController(svc, self._age_seconds)
        svc._shelly_io_controller = ShellyIoController(svc)
        if not hasattr(svc, "_state_controller") or svc._state_controller is None:
            svc._state_controller = ServiceStateController(svc, self._normalize_mode)
        svc._write_controller = DbusWriteController(WriteControllerPort(svc))
        svc._auto_input_supervisor = AutoInputSupervisor(svc)
        svc._update_controller = UpdateCycleController(
            UpdateCyclePort(svc),
            self._phase_values,
            self._health_code,
        )

    def initialize_virtual_state(self) -> None:
        """Initialize the writable EV charger state exposed on DBus."""
        svc = self.service
        defaults = svc.config["DEFAULT"]
        svc.manual_override_until = 0.0
        svc.virtual_mode = self._normalize_mode(defaults.get("Mode", 0))
        svc.virtual_autostart = int(defaults.get("AutoStart", 1))
        svc.virtual_startstop = int(defaults.get("StartStop", 1))
        svc.virtual_enable = int(defaults.get("Enable", defaults.get("StartStop", 1)))
        svc.virtual_set_current = float(defaults.get("SetCurrent", svc.max_current))
        svc.charging_started_at = None
        svc.energy_at_start = 0.0
        svc.last_status = 0
        svc.auto_start_condition_since = None
        svc.auto_stop_condition_since = None
        svc.auto_stop_condition_reason = None
        svc.auto_samples = deque()
        svc._auto_high_soc_profile_active = None
        svc._stop_smoothed_surplus_power = None
        svc._stop_smoothed_grid_power = None
        svc.relay_last_changed_at = None
        svc.relay_last_off_at = None
        svc._grid_recovery_required = False
        svc._grid_recovery_since = None
        svc._auto_mode_cutover_pending = False
        svc._ignore_min_offtime_once = False

    def restore_runtime_state(self) -> None:
        """Restore RAM-backed state and initialize worker bookkeeping."""
        svc = self.service
        svc._load_runtime_state()
        svc._startup_manual_target = (
            bool(svc.virtual_enable or svc.virtual_startstop)
            if not self._mode_uses_auto_logic(svc.virtual_mode)
            else None
        )
        svc._init_worker_state()

    def initialize_dbus_service(self) -> None:
        """Create the Venus EV charger DBus service shell."""
        svc = self.service
        svc._dbusservice = VeDbusService(f"{svc.service_name}.http_{svc.deviceinstance}", register=False)

    def apply_device_metadata(self) -> None:
        """Fetch Shelly metadata and apply UI-facing identity fields."""
        svc = self.service
        device_info = self.fetch_device_info_with_fallback()
        defaults = svc.config["DEFAULT"]
        svc.product_name = defaults.get("ProductName", "Shelly Wallbox Meter").strip()
        svc.custom_name = svc.custom_name_override or device_info.get("name") or "Shelly Wallbox"
        svc.serial = device_info.get("mac", svc.host.replace(".", ""))
        svc.firmware_version = device_info.get("fw_id", self._read_version("version.txt"))
        svc.hardware_version = device_info.get("model", "Shelly 1PM Gen4")

    def start_runtime_loops(self) -> None:
        """Register DBus paths, start background workers, and arm timers."""
        svc = self.service
        svc._start_io_worker()
        logging.info(
            "Initialized Shelly wallbox service pid=%s runtime_state=%s %s",
            os.getpid(),
            svc.runtime_state_path,
            svc._state_summary(),
        )
        self._gobject.timeout_add(svc.poll_interval_ms, svc._update)
        self._gobject.timeout_add(svc.sign_of_life_minutes * 60 * 1000, svc._sign_of_life)

    def fetch_device_info_with_fallback(self) -> dict[str, Any]:
        """Try to fetch Shelly device info, but start with generic metadata if that fails."""
        svc = self.service
        last_error = None
        attempts = svc.startup_device_info_retries + 1
        for attempt in range(attempts):
            try:
                return svc.fetch_rpc("Shelly.GetDeviceInfo")
            except Exception as error:  # pylint: disable=broad-except
                last_error = error
                if attempt < (attempts - 1) and svc.startup_device_info_retry_seconds > 0:
                    logging.warning(
                        "Shelly.GetDeviceInfo failed during startup (attempt %s/%s): %s",
                        attempt + 1,
                        attempts,
                        error,
                    )
                    time.sleep(svc.startup_device_info_retry_seconds)
        logging.warning(
            "Shelly.GetDeviceInfo unavailable during startup, continuing with generic metadata: %s",
            last_error,
        )
        return {}

    def register_paths(self) -> None:
        """Register all DBus paths exposed by the emulated EV charger."""
        svc = self.service
        self._register_management_paths()
        for path, (initial, formatter) in self._all_service_paths().items():
            logging.debug("Registering path: %s initial=%r formatter=%r", path, initial, formatter)
            try:
                svc._dbusservice.add_path(
                    path,
                    initial,
                    gettextcallback=formatter,
                    writeable=path in self.WRITABLE_PATHS,
                    onchangecallback=svc._handle_write,
                )
            except Exception as error:  # pylint: disable=broad-except
                logging.error("Failed to register path %s: %s", path, error, exc_info=error)
                raise
        svc._dbusservice.register()

    def _register_management_paths(self) -> None:
        """Register immutable management and identity DBus paths."""
        svc = self.service
        svc._dbusservice.add_path("/Mgmt/ProcessName", self._script_path)
        svc._dbusservice.add_path(
            "/Mgmt/ProcessVersion",
            "Unknown version, and running on Python " + platform.python_version(),
        )
        svc._dbusservice.add_path("/Mgmt/Connection", svc.connection_name)
        svc._dbusservice.add_path("/DeviceInstance", svc.deviceinstance)
        svc._dbusservice.add_path("/ProductId", 0xFFFF)
        svc._dbusservice.add_path("/ProductName", svc.product_name)
        svc._dbusservice.add_path("/CustomName", svc.custom_name)
        svc._dbusservice.add_path("/FirmwareVersion", svc.firmware_version)
        svc._dbusservice.add_path("/HardwareVersion", svc.hardware_version)
        svc._dbusservice.add_path("/Serial", svc.serial)
        svc._dbusservice.add_path("/Connected", 1)
        svc._dbusservice.add_path("/Position", svc.position)
        svc._dbusservice.add_path("/UpdateIndex", 0)

    def _measurement_paths(self) -> PathMap:
        """Return measurement and energy paths shown on the EV charger tile."""
        svc = self.service
        return {
            "/Ac/Power": (0.0, self._formatters["w"]),
            "/Ac/Voltage": (0.0, self._formatters["v"]),
            "/Ac/L1/Power": (0.0, self._formatters["w"]),
            "/Ac/L2/Power": (0.0, self._formatters["w"]),
            "/Ac/L3/Power": (0.0, self._formatters["w"]),
            "/Ac/L1/Voltage": (0.0, self._formatters["v"]),
            "/Ac/L2/Voltage": (0.0, self._formatters["v"]),
            "/Ac/L3/Voltage": (0.0, self._formatters["v"]),
            "/Ac/L1/Current": (0.0, self._formatters["a"]),
            "/Ac/L2/Current": (0.0, self._formatters["a"]),
            "/Ac/L3/Current": (0.0, self._formatters["a"]),
            "/Ac/Energy/Forward": (0.0, self._formatters["kwh"]),
            "/Ac/L1/Energy/Forward": (0.0, self._formatters["kwh"]),
            "/Ac/L2/Energy/Forward": (0.0, self._formatters["kwh"]),
            "/Ac/L3/Energy/Forward": (0.0, self._formatters["kwh"]),
            "/Session/Energy": (0.0, None),
            "/Session/Time": (0, None),
            "/Ac/Current": (0.0, self._formatters["a"]),
            "/Current": (0.0, self._formatters["a"]),
        }

    def _control_paths(self) -> PathMap:
        """Return writable and status-like EV charger control paths."""
        svc = self.service
        return {
            "/MinCurrent": (svc.min_current, self._formatters["a"]),
            "/MaxCurrent": (svc.max_current, self._formatters["a"]),
            "/SetCurrent": (svc.virtual_set_current, self._formatters["a"]),
            "/AutoStart": (svc.virtual_autostart, None),
            "/ChargingTime": (0, None),
            "/Mode": (svc.virtual_mode, None),
            "/StartStop": (svc.virtual_startstop, None),
            "/Enable": (svc.virtual_enable, None),
            "/Status": (0, self._formatters["status"]),
        }

    def _diagnostic_paths(self) -> PathMap:
        """Return Auto-diagnostic DBus paths published by the service."""
        svc = self.service
        return {
            "/Auto/Health": (svc._last_health_reason, None),
            "/Auto/HealthCode": (svc._last_health_code, None),
            "/Auto/ErrorCount": (0, None),
            "/Auto/DbusReadErrors": (0, None),
            "/Auto/ShellyReadErrors": (0, None),
            "/Auto/PvReadErrors": (0, None),
            "/Auto/BatteryReadErrors": (0, None),
            "/Auto/GridReadErrors": (0, None),
            "/Auto/InputCacheHits": (0, None),
            "/Auto/LastShellyReadAge": (-1, None),
            "/Auto/LastPvReadAge": (-1, None),
            "/Auto/LastBatteryReadAge": (-1, None),
            "/Auto/LastGridReadAge": (-1, None),
            "/Auto/LastDbusReadAge": (-1, None),
            "/Auto/LastSuccessfulUpdateAge": (-1, None),
            "/Auto/Stale": (0, None),
            "/Auto/StaleSeconds": (0, None),
            "/Auto/RecoveryAttempts": (0, None),
        }

    def _all_service_paths(self) -> PathMap:
        """Return the complete dynamic EV charger DBus path map."""
        return {
            **self._measurement_paths(),
            **self._control_paths(),
            **self._diagnostic_paths(),
        }


def run_service_main(service_class: Callable[[], Any], config_path: str, gobject_module: Any) -> None:
    """Entrypoint helper for running the wallbox service as a process."""
    config = configparser.ConfigParser()
    config.read(config_path)
    logging_level = _logging_level_from_config(config)
    logging.basicConfig(
        format="%(levelname)s [pid=%(process)d %(threadName)s] %(message)s",
        level=logging_level,
    )

    try:
        logging.info("Start Shelly wallbox service pid=%s", os.getpid())
        _enable_fault_diagnostics()
        _setup_dbus_mainloop()
        _run_service_loop(service_class, gobject_module)
    except Exception as error:  # pylint: disable=broad-except
        logging.critical("Error at main pid=%s", os.getpid(), exc_info=error)
