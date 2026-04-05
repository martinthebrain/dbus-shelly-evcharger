# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus write-handling helpers for the Shelly wallbox service.

Victron GUI writes arrive here through writable DBus paths such as /Mode,
/Enable, /StartStop, and /AutoStart. The controller translates those user
actions into wallbox-specific state changes and Shelly relay commands.
"""

from __future__ import annotations

import logging
from typing import Any


class DbusWriteController:
    """Encapsulate writable DBus path handling for the Shelly wallbox service."""

    def __init__(self, port: Any) -> None:
        self.port = port

    @staticmethod
    def _log_normalized_mode(requested_mode: int, applied_mode: int) -> None:
        """Log when an unsupported mode was normalized."""
        if applied_mode == requested_mode:
            return
        logging.info(
            "Unsupported mode %s requested on /Mode, normalizing to %s",
            requested_mode,
            applied_mode,
        )

    @staticmethod
    def _reset_auto_decision_state(svc: Any) -> None:
        """Clear pending auto timers and averaging when the mode changes."""
        svc.auto_start_condition_since = None
        svc.auto_stop_condition_since = None
        svc._clear_auto_samples()

    @staticmethod
    def _activate_auto_without_cutover(svc: Any) -> None:
        """Enter Auto mode without forcing a relay cutover."""
        svc.auto_mode_cutover_pending = False

    @staticmethod
    def _queue_auto_cutover(svc: Any, current_time: float) -> None:
        """Perform the clean manual-to-auto cutover while charging."""
        svc.virtual_enable = 1
        svc.virtual_startstop = 0
        svc.auto_mode_cutover_pending = True
        svc.ignore_min_offtime_once = False
        # Auto mode should take control from a known relay-off state. This
        # avoids inheriting a manual ON state and makes the next Auto start
        # decision explicit and visible in the logs.
        svc._queue_relay_command(False, current_time)
        svc._publish_local_pm_status(False, current_time)

    def _handle_mode_transition_to_auto(self, previous_mode: int, current_time: float) -> None:
        """Apply side effects when switching into an Auto-controlled mode."""
        port = self.port
        if port.mode_uses_auto_logic(previous_mode):
            return
        port.manual_override_until = 0.0
        if port.virtual_startstop:
            self._queue_auto_cutover(port, current_time)
            return
        self._activate_auto_without_cutover(port)

    @staticmethod
    def _snapshot_for_mode(svc: Any, current_time: float, auto_mode_active: bool) -> None:
        """Publish the mode-specific worker snapshot state."""
        snapshot = svc._get_worker_snapshot()
        svc._update_worker_snapshot(
            captured_at=current_time,
            auto_mode_active=auto_mode_active,
            pv_power=None if not auto_mode_active else snapshot.get("pv_power"),
            battery_soc=None if not auto_mode_active else snapshot.get("battery_soc"),
            grid_power=None if not auto_mode_active else snapshot.get("grid_power"),
        )

    @staticmethod
    def _auto_startstop_value(svc: Any) -> int:
        """Return the GUI StartStop value while Auto owns relay decisions."""
        return int(svc.virtual_enable or svc.virtual_startstop)

    @classmethod
    def _startstop_value_for_mode(cls, svc: Any, auto_mode_active: bool) -> int:
        """Return the mode-dependent StartStop display value."""
        return cls._auto_startstop_value(svc) if auto_mode_active else int(svc.virtual_startstop)

    @classmethod
    def _publish_startstop_enable(
        cls,
        svc: Any,
        current_time: float,
        auto_mode_active: bool | None = None,
    ) -> None:
        """Force-publish StartStop and Enable after a control-path write."""
        if auto_mode_active is None:
            auto_mode_active = svc._mode_uses_auto_logic(svc.virtual_mode)
        svc._publish_dbus_path(
            "/StartStop",
            cls._startstop_value_for_mode(svc, auto_mode_active),
            current_time,
            force=True,
        )
        svc._publish_dbus_path("/Enable", int(svc.virtual_enable), current_time, force=True)

    @staticmethod
    def _publish_mode_paths(svc: Any, current_time: float, auto_mode_active: bool) -> None:
        """Publish /Mode, /StartStop, and /Enable after a mode change."""
        svc._publish_dbus_path("/Mode", svc.virtual_mode, current_time, force=True)
        DbusWriteController._publish_startstop_enable(svc, current_time, auto_mode_active)

    @staticmethod
    def _apply_auto_disable(svc: Any, current_time: float) -> None:
        """Queue a relay-off transition after an Auto deny request."""
        svc._queue_relay_command(False, current_time)
        svc.virtual_startstop = 0
        svc._publish_local_pm_status(False, current_time)

    @staticmethod
    def _apply_manual_startstop_request(svc: Any, wanted_on: bool, current_time: float) -> None:
        """Apply direct relay control in Manual mode."""
        svc._queue_relay_command(wanted_on, current_time)
        svc.virtual_startstop = 1 if wanted_on else 0
        svc.virtual_enable = svc.virtual_startstop
        svc.manual_override_until = current_time + svc.auto_manual_override_seconds
        svc._publish_local_pm_status(wanted_on, current_time)

    @staticmethod
    def _apply_manual_enable_request(svc: Any, wanted_on: bool, current_time: float) -> None:
        """Apply direct enable/disable control in Manual mode."""
        svc.manual_override_until = current_time + svc.auto_manual_override_seconds
        svc._queue_relay_command(wanted_on, current_time)
        svc.virtual_startstop = 1 if wanted_on else 0
        svc._publish_local_pm_status(wanted_on, current_time)

    def _handle_mode_write(self, requested_mode: int) -> None:
        port = self.port
        previous_mode = int(port.virtual_mode)
        current_time = port.time_now()
        port.virtual_mode = port.normalize_mode(requested_mode)
        auto_mode_active = port.mode_uses_auto_logic(port.virtual_mode)
        self._log_normalized_mode(requested_mode, port.virtual_mode)
        self._reset_auto_decision_state(port)
        if auto_mode_active:
            self._handle_mode_transition_to_auto(previous_mode, current_time)
        self._snapshot_for_mode(port, current_time, auto_mode_active)
        self._publish_mode_paths(port, current_time, auto_mode_active)
        logging.info(
            "DBus write /Mode requested=%s previous=%s applied=%s %s",
            requested_mode,
            previous_mode,
            port.virtual_mode,
            port.state_summary(),
        )

    def _handle_autostart_write(self, value: Any) -> None:
        port = self.port
        port.virtual_autostart = int(value)
        port.publish_dbus_path("/AutoStart", port.virtual_autostart, port.time_now(), force=True)
        logging.info(
            "DBus write /AutoStart=%s %s",
            port.virtual_autostart,
            port.state_summary(),
        )

    def _handle_startstop_write(self, wanted_on: bool) -> None:
        port = self.port
        current_time = port.time_now()
        if port.mode_uses_auto_logic(port.virtual_mode):
            # In Auto, StartStop acts like an allow/deny request for the auto
            # controller. It must not bypass SOC/night/surplus checks by
            # forcing the relay on directly.
            port.virtual_enable = 1 if wanted_on else 0
            if not wanted_on:
                self._apply_auto_disable(port, current_time)
            self._publish_startstop_enable(port, current_time, auto_mode_active=True)
        else:
            # In Manual, StartStop remains direct relay control.
            self._apply_manual_startstop_request(port, wanted_on, current_time)
            self._publish_startstop_enable(port, current_time, auto_mode_active=False)
        logging.info(
            "DBus write /StartStop=%s auto_mode=%s %s",
            int(wanted_on),
            int(port.mode_uses_auto_logic(port.virtual_mode)),
            port.state_summary(),
        )

    def _handle_enable_write(self, wanted_on: bool) -> None:
        port = self.port
        current_time = port.time_now()
        port.virtual_enable = 1 if wanted_on else 0
        if port.mode_uses_auto_logic(port.virtual_mode):
            if not wanted_on:
                self._apply_auto_disable(port, current_time)
        else:
            self._apply_manual_enable_request(port, wanted_on, current_time)
        self._publish_startstop_enable(
            port,
            current_time,
            auto_mode_active=port.mode_uses_auto_logic(port.virtual_mode),
        )
        logging.info(
            "DBus write /Enable=%s auto_mode=%s %s",
            int(wanted_on),
            int(port.mode_uses_auto_logic(port.virtual_mode)),
            port.state_summary(),
        )

    def _handle_current_setting_write(self, path: str, value: Any) -> None:
        port = self.port
        current_time = port.time_now()
        if path == "/SetCurrent":
            # Shelly can be shown like an EVSE, but cannot set EV charging
            # current via CP/PWM.
            port.virtual_set_current = float(value)
            target_value = port.virtual_set_current
        elif path == "/MaxCurrent":
            port.max_current = float(value)
            target_value = port.max_current
        else:
            port.min_current = float(value)
            target_value = port.min_current
        port.publish_dbus_path(path, target_value, current_time, force=True)

    def handle_write(self, path: str, value: Any) -> bool:
        """Handle writable DBus path updates from Venus OS."""
        port = self.port
        try:
            if path == "/Mode":
                self._handle_mode_write(int(value))
            elif path == "/AutoStart":
                self._handle_autostart_write(value)
            elif path == "/StartStop":
                self._handle_startstop_write(bool(int(value)))
            elif path == "/Enable":
                self._handle_enable_write(bool(int(value)))
            elif path in ("/SetCurrent", "/MaxCurrent", "/MinCurrent"):
                self._handle_current_setting_write(path, value)
            port.save_runtime_state()
            return True
        except Exception as error:  # pylint: disable=broad-except
            logging.warning("Write to %s=%s failed: %s", path, value, error, exc_info=error)
            return False
