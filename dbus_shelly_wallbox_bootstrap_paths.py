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



class _ServiceBootstrapPathMixin(_ComposableControllerMixin):
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
            "/Auto/State": (getattr(svc, "_last_auto_state", "idle"), None),
            "/Auto/StateCode": (getattr(svc, "_last_auto_state_code", 0), None),
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
