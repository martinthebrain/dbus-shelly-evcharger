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
from dbus_shelly_wallbox_split_mixins import _ComposableControllerMixin
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


def _config_value(defaults: configparser.SectionProxy, key: str, fallback: Any) -> str:  # pragma: no cover
    """Return a config value while keeping SectionProxy.get() mypy-friendly."""
    return defaults.get(key, str(fallback))


def _seasonal_month_windows(  # pragma: no cover
    config: configparser.ConfigParser,
    month_window_func: Callable[[configparser.ConfigParser, int, str, str], Any],
) -> dict[int, Any]:
    """Return the configured monthly daytime windows with sensible defaults."""
    return {
        month: month_window_func(config, month, start, end)
        for month, (start, end) in MONTH_WINDOW_DEFAULTS.items()
    }


def _logging_level_from_config(config: configparser.ConfigParser, default: str = "INFO") -> str:  # pragma: no cover
    """Read the configured log level from the DEFAULT section."""
    if "DEFAULT" not in config:
        return default
    return config["DEFAULT"].get("Logging", default).upper()


def _enable_fault_diagnostics() -> None:  # pragma: no cover
    """Enable crash diagnostics when available."""
    try:
        faulthandler.enable(all_threads=True)
    except Exception as error:  # pylint: disable=broad-except
        logging.debug("faulthandler.enable() unavailable: %s", error)


def _install_signal_logging(quit_callback: Callable[[], None] | None = None) -> None:  # pragma: no cover
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


def _setup_dbus_mainloop() -> None:  # pragma: no cover
    """Prepare dbus-python and GLib to run the Venus event loop."""
    from dbus.mainloop.glib import DBusGMainLoop  # pylint: disable=import-error
    import dbus.mainloop.glib as dbus_glib_mainloop  # pylint: disable=import-error

    try:
        dbus_glib_mainloop.threads_init()
    except AttributeError:
        logging.debug("dbus.mainloop.glib.threads_init() not available on this runtime")

    DBusGMainLoop(set_as_default=True)


def _request_mainloop_quit(gobject_module: Any, mainloop: Any) -> None:  # pragma: no cover
    """Request a clean GLib shutdown, preferring idle_add when available."""
    idle_add = getattr(gobject_module, "idle_add", None)
    if callable(idle_add):
        try:
            idle_add(mainloop.quit)
            return
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Unable to schedule GLib shutdown via idle_add: %s", error)
    mainloop.quit()


def _run_service_loop(service_class: Callable[[], Any], gobject_module: Any) -> None:  # pragma: no cover
    """Instantiate the service and enter the GLib main loop."""
    service_class()
    mainloop = gobject_module.MainLoop()
    _install_signal_logging(lambda: _request_mainloop_quit(gobject_module, mainloop))
    logging.info("Connected to dbus, and switching over to gobject.MainLoop() (= event based)")
    mainloop.run()



class _ServiceBootstrapRuntimeMixin(_ComposableControllerMixin):
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
        svc.learned_charge_power_watts = None
        svc.learned_charge_power_updated_at = None
        svc.learned_charge_power_state = "unknown"
        svc.learned_charge_power_learning_since = None
        svc.learned_charge_power_sample_count = 0
        svc.learned_charge_power_phase = None
        svc.learned_charge_power_voltage = None
        svc.learned_charge_power_signature_mismatch_sessions = 0
        svc.learned_charge_power_signature_checked_session_started_at = None
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

    def initialize_dbus_service(self) -> None:  # pragma: no cover
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
