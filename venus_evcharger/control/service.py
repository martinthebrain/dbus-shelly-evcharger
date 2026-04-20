# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport-agnostic command mapping and dispatch for Control API v1."""

from __future__ import annotations

from typing import Any
from venus_evcharger.control.models import ControlCommand, ControlCommandName, ControlCommandSource


class ControlApiV1Service:
    """Map transport-level writes to canonical commands and dispatch them."""

    _COMMAND_NAMES: frozenset[ControlCommandName] = frozenset(
        {
            "legacy_unknown_write",
            "reset_contactor_lockout",
            "reset_phase_lockout",
            "set_auto_runtime_setting",
            "set_auto_start",
            "set_current_setting",
            "set_enable",
            "set_mode",
            "set_phase_selection",
            "set_start_stop",
            "trigger_software_update",
        }
    )
    _DIRECT_PATH_COMMANDS: dict[str, ControlCommandName] = {
        "/Mode": "set_mode",
        "/AutoStart": "set_auto_start",
        "/StartStop": "set_start_stop",
        "/Enable": "set_enable",
        "/PhaseSelection": "set_phase_selection",
        "/Auto/PhaseLockoutReset": "reset_phase_lockout",
        "/Auto/ContactorLockoutReset": "reset_contactor_lockout",
        "/Auto/SoftwareUpdateRun": "trigger_software_update",
    }
    _HANDLER_SPECS = {
        "legacy_unknown_write": ("_handle_unknown_write", True),
        "reset_contactor_lockout": ("_handle_contactor_lockout_reset_write", False),
        "reset_phase_lockout": ("_handle_phase_lockout_reset_write", False),
        "set_auto_runtime_setting": ("_handle_auto_runtime_setting_write", True),
        "set_auto_start": ("_handle_autostart_write", False),
        "set_current_setting": ("_handle_current_setting_write", True),
        "set_enable": ("_handle_enable_value_write", False),
        "set_mode": ("_handle_mode_value_write", False),
        "set_phase_selection": ("_handle_phase_selection_write", False),
        "set_start_stop": ("_handle_startstop_value_write", False),
        "trigger_software_update": ("_handle_software_update_run_write", False),
    }
    _COMMAND_DEFAULT_PATHS: dict[ControlCommandName, str] = {
        command_name: path
        for path, command_name in _DIRECT_PATH_COMMANDS.items()
    }

    def __init__(
        self,
        *,
        current_setting_paths: tuple[str, ...],
        auto_runtime_setting_paths: set[str],
    ) -> None:
        self._current_setting_paths = frozenset(current_setting_paths)
        self._auto_runtime_setting_paths = frozenset(auto_runtime_setting_paths)

    def command_for_write(
        self,
        path: str,
        value: Any,
        *,
        source: ControlCommandSource = "dbus",
    ) -> ControlCommand:
        """Translate one transport-level write into one canonical Control API command."""
        command_name = self._DIRECT_PATH_COMMANDS.get(path)
        if command_name is not None:
            return ControlCommand(name=command_name, path=path, value=value, source=source)
        if path in self._current_setting_paths:
            return ControlCommand(name="set_current_setting", path=path, value=value, source=source)
        if path in self._auto_runtime_setting_paths:
            return ControlCommand(name="set_auto_runtime_setting", path=path, value=value, source=source)
        return ControlCommand(
            name="legacy_unknown_write",
            path=path,
            value=value,
            source=source,
            detail="No canonical Control API command is registered for this write path.",
        )

    def command_for_dbus_write(self, path: str, value: Any) -> ControlCommand:
        """Translate one DBus write into one canonical Control API command."""
        return self.command_for_write(path, value, source="dbus")

    def command_from_payload(
        self,
        payload: dict[str, Any],
        *,
        source: ControlCommandSource = "http",
    ) -> ControlCommand:
        """Translate one structured API payload into one canonical command."""
        if "name" in payload:
            return self._command_from_named_payload(payload, source=source)
        if "path" in payload:
            return self.command_for_write(payload["path"], payload.get("value"), source=source)
        raise ValueError("Control command payload must include either 'name' or 'path'.")

    def _command_from_named_payload(
        self,
        payload: dict[str, Any],
        *,
        source: ControlCommandSource,
    ) -> ControlCommand:
        """Translate one canonical command payload into one concrete command."""
        raw_name = str(payload["name"]).strip()
        command_name = self._validated_command_name(raw_name)
        path = self._resolved_command_path(command_name, payload)
        detail = str(payload.get("detail", "")).strip()
        return ControlCommand(
            name=command_name,
            path=path,
            value=payload.get("value"),
            source=source,
            detail=detail,
            command_id=str(payload.get("command_id", "")).strip(),
            idempotency_key=str(payload.get("idempotency_key", "")).strip(),
        )

    def _validated_command_name(self, raw_name: str) -> ControlCommandName:
        if raw_name not in self._COMMAND_NAMES:
            raise ValueError(f"Unsupported control command '{raw_name}'.")
        return raw_name

    def _resolved_command_path(self, command_name: ControlCommandName, payload: dict[str, Any]) -> str:
        """Return the concrete write path required for one canonical command."""
        explicit_path = str(payload.get("path", "")).strip()
        if explicit_path:
            return explicit_path
        default_path = self._COMMAND_DEFAULT_PATHS.get(command_name)
        if default_path is not None:
            return default_path
        raise ValueError(f"Control command '{command_name}' requires an explicit 'path'.")

    def execute(self, controller: Any, command: ControlCommand) -> None:
        """Dispatch one canonical command onto the existing write controller."""
        handler_name, include_path = self._HANDLER_SPECS[command.name]
        handler = getattr(controller, handler_name)
        if include_path:
            handler(command.path, command.value)
            return
        handler(command.value)
