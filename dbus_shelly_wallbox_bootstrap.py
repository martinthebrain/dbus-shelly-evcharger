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
import signal
import time
from collections.abc import Callable
from types import FrameType
from typing import Any

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

    def _log_signal(signum: int, _frame: FrameType | None) -> None:
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


from dbus_shelly_wallbox_bootstrap_config import _ServiceBootstrapConfigMixin
from dbus_shelly_wallbox_bootstrap_paths import _ServiceBootstrapPathMixin
from dbus_shelly_wallbox_bootstrap_runtime import _ServiceBootstrapRuntimeMixin

_TEST_PATCH_EXPORTS = (time,)


class ServiceBootstrapController(
    _ServiceBootstrapConfigMixin,
    _ServiceBootstrapRuntimeMixin,
    _ServiceBootstrapPathMixin,
):
    """Bootstrap controller for the Shelly EV charger service."""

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

    def initialize_dbus_service(self) -> None:
        """Create the EV charger DBus service using the bootstrap module factory."""
        svc = self.service
        svc._dbusservice = VeDbusService(f"{svc.service_name}.http_{svc.deviceinstance}", register=False)


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
