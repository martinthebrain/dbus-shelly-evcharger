# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure bootstrap helper implementations with injectable runtime dependencies."""

from __future__ import annotations

import configparser
from collections.abc import Callable
from types import FrameType
from typing import Any


def logging_level_from_config(config: configparser.ConfigParser, default: str = "INFO") -> str:
    """Read the configured log level from the DEFAULT section."""
    if "DEFAULT" not in config:
        return default
    return config["DEFAULT"].get("Logging", default).upper()


def enable_fault_diagnostics(faulthandler_module: Any, logging_module: Any) -> None:
    """Enable crash diagnostics when available."""
    try:
        faulthandler_module.enable(all_threads=True)
    except Exception as error:  # pylint: disable=broad-except
        logging_module.debug("faulthandler.enable() unavailable: %s", error)


def install_signal_logging(
    signal_module: Any,
    logging_module: Any,
    os_module: Any,
    quit_callback: Callable[[], None] | None = None,
) -> None:
    """Install signal handlers that log and request a clean GLib-loop shutdown."""

    def _log_signal(signum: int, _frame: FrameType | None) -> None:
        logging_module.warning("Received signal %s in pid=%s", signum, os_module.getpid())
        if quit_callback is None:
            return
        try:
            quit_callback()
        except Exception as error:  # pylint: disable=broad-except
            logging_module.debug("Unable to request shutdown after signal %s: %s", signum, error)

    for signum in (
        getattr(signal_module, "SIGTERM", None),
        getattr(signal_module, "SIGINT", None),
        getattr(signal_module, "SIGHUP", None),
    ):
        if signum is None:
            continue
        try:
            signal_module.signal(signum, _log_signal)
        except Exception as error:  # pylint: disable=broad-except
            logging_module.debug("Unable to install signal handler for %s: %s", signum, error)


def setup_dbus_mainloop(logging_module: Any) -> None:
    """Prepare dbus-python and GLib to run the Venus event loop."""
    from dbus.mainloop.glib import DBusGMainLoop  # pylint: disable=import-error
    import dbus.mainloop.glib as dbus_glib_mainloop  # pylint: disable=import-error

    try:
        dbus_glib_mainloop.threads_init()
    except AttributeError:
        logging_module.debug("dbus.mainloop.glib.threads_init() not available on this runtime")

    DBusGMainLoop(set_as_default=True)


def request_mainloop_quit(gobject_module: Any, mainloop: Any, logging_module: Any) -> None:
    """Request a clean GLib shutdown, preferring idle_add when available."""
    idle_add = getattr(gobject_module, "idle_add", None)
    if callable(idle_add):
        try:
            idle_add(mainloop.quit)
            return
        except Exception as error:  # pylint: disable=broad-except
            logging_module.debug("Unable to schedule GLib shutdown via idle_add: %s", error)
    mainloop.quit()


def run_service_loop(
    service_class: Callable[[], Any],
    gobject_module: Any,
    install_signal_logging_func: Callable[[Callable[[], None] | None], None],
    request_mainloop_quit_func: Callable[[Any, Any], None],
    logging_module: Any,
) -> None:
    """Instantiate the service and enter the GLib main loop."""
    service_class()
    mainloop = gobject_module.MainLoop()
    install_signal_logging_func(lambda: request_mainloop_quit_func(gobject_module, mainloop))
    logging_module.info("Connected to dbus, and switching over to gobject.MainLoop() (= event based)")
    mainloop.run()
