# SPDX-License-Identifier: GPL-3.0-or-later
"""Generic HTTP/JSON template switch backend."""

from __future__ import annotations

from dataclasses import dataclass

from dbus_shelly_wallbox_backend_template_support import (
    TemplateHttpBackendBase,
    config_section,
    json_path_value,
    load_template_config,
    normalize_http_method,
    resolved_url,
)
from dbus_shelly_wallbox_backend_types import (
    PhaseSelection,
    SwitchCapabilities,
    SwitchState,
    SwitchingMode,
    normalize_phase_selection,
    normalize_phase_selection_tuple,
)
from dbus_shelly_wallbox_contracts import finite_float_or_none, normalize_binary_flag


@dataclass(frozen=True)
class TemplateSwitchSettings:
    """Normalized template-switch config loaded from one adapter file."""

    base_url: str
    timeout_seconds: float
    state_method: str
    state_url: str
    state_enabled_path: str
    state_phase_selection_path: str | None
    command_method: str
    command_url: str
    command_json_template: str | None
    phase_method: str
    phase_url: str | None
    phase_json_template: str | None
    switching_mode: SwitchingMode
    supported_phase_selections: tuple[PhaseSelection, ...]
    requires_charge_pause_for_phase_change: bool
    max_direct_switch_power_w: float | None


def _normalize_switching_mode(value: object, default: SwitchingMode = "direct") -> SwitchingMode:
    """Return one normalized switching mode."""
    mode = str(value).strip().lower() if value is not None else ""
    if mode == "contactor":
        return "contactor"
    if mode == "direct":
        return "direct"
    return default


def load_template_switch_settings(service: object, config_path: str) -> TemplateSwitchSettings:
    """Return normalized template-switch settings."""
    parser = load_template_config(str(config_path).strip())
    adapter = config_section(parser, "Adapter")
    capabilities = config_section(parser, "Capabilities")
    state_request = config_section(parser, "StateRequest")
    state_response = config_section(parser, "StateResponse")
    command_request = config_section(parser, "CommandRequest")
    phase_request = config_section(parser, "PhaseRequest")

    base_url = str(adapter.get("BaseUrl", "")).strip()
    timeout_seconds = finite_float_or_none(
        adapter.get("RequestTimeoutSeconds", getattr(service, "shelly_request_timeout_seconds", 2.0))
    )
    supported_phase_selections = normalize_phase_selection_tuple(
        capabilities.get("SupportedPhaseSelections", "P1"),
        ("P1",),
    )
    switching_mode = _normalize_switching_mode(capabilities.get("SwitchingMode", "direct"))
    max_direct_switch_power_w = (
        None
        if switching_mode == "contactor"
        else finite_float_or_none(capabilities.get("MaxDirectSwitchPowerWatts", None))
    )
    state_url = resolved_url(base_url, state_request.get("Url", ""))
    command_url = resolved_url(base_url, command_request.get("Url", ""))
    phase_url = resolved_url(base_url, phase_request.get("Url", ""))
    state_enabled_path = str(state_response.get("EnabledPath", "enabled")).strip()
    state_phase_selection_path = str(state_response.get("PhaseSelectionPath", "")).strip() or None
    command_json_template = str(command_request.get("JsonTemplate", "")).strip() or None
    phase_json_template = (
        str(phase_request.get("JsonTemplate", '{"phase_selection": "$phase_selection"}')).strip()
        if phase_url
        else None
    )

    if not state_url:
        raise ValueError("Template switch backend requires [StateRequest] Url")
    if not command_url:
        raise ValueError("Template switch backend requires [CommandRequest] Url")
    if not state_enabled_path:
        raise ValueError("Template switch backend requires [StateResponse] EnabledPath")
    if len(supported_phase_selections) > 1 and not phase_url:
        raise ValueError(
            "Template switch backend with multi-phase support requires [PhaseRequest] Url"
        )

    return TemplateSwitchSettings(
        base_url=base_url,
        timeout_seconds=2.0 if timeout_seconds is None or timeout_seconds <= 0.0 else float(timeout_seconds),
        state_method=normalize_http_method(state_request.get("Method", "GET"), "GET"),
        state_url=state_url,
        state_enabled_path=state_enabled_path,
        state_phase_selection_path=state_phase_selection_path,
        command_method=normalize_http_method(command_request.get("Method", "POST"), "POST"),
        command_url=command_url,
        command_json_template=command_json_template,
        phase_method=normalize_http_method(phase_request.get("Method", "POST"), "POST"),
        phase_url=phase_url or None,
        phase_json_template=phase_json_template,
        switching_mode=switching_mode,
        supported_phase_selections=supported_phase_selections,
        requires_charge_pause_for_phase_change=bool(
            normalize_binary_flag(capabilities.get("RequiresChargePauseForPhaseChange", 0))
        ),
        max_direct_switch_power_w=max_direct_switch_power_w,
    )


class TemplateSwitchBackend(TemplateHttpBackendBase):
    """Switch backend driven by one normalized HTTP/JSON adapter surface."""

    def __init__(self, service: object, config_path: str = "") -> None:
        self.service = service
        self.config_path = str(config_path).strip()
        self.settings = load_template_switch_settings(service, self.config_path)
        super().__init__(service, self.settings.timeout_seconds)
        default_selection = self.settings.supported_phase_selections[0]
        requested_selection: PhaseSelection = normalize_phase_selection(
            getattr(service, "requested_phase_selection", default_selection),
            default_selection,
        )
        self._selected_phase_selection: PhaseSelection = (
            requested_selection
            if requested_selection in self.settings.supported_phase_selections
            else default_selection
        )

    @staticmethod
    def _context(enabled: bool, phase_selection: PhaseSelection) -> dict[str, str]:
        """Return one stable template context for URL/body rendering."""
        return {
            "enabled_json": "true" if enabled else "false",
            "enabled_int": "1" if enabled else "0",
            "enabled_text": "on" if enabled else "off",
            "phase_selection": str(phase_selection),
        }

    @staticmethod
    def _enabled_state(value: object) -> bool:
        """Return one normalized boolean switch state from one response scalar."""
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "on", "yes", "enabled"}:
            return True
        if text in {"0", "false", "off", "no", "disabled"}:
            return False
        raise ValueError(f"Unsupported enabled-state value '{value}'")

    def capabilities(self) -> SwitchCapabilities:
        """Return configured template-switch capabilities."""
        return SwitchCapabilities(
            switching_mode=self.settings.switching_mode,
            supported_phase_selections=self.settings.supported_phase_selections,
            requires_charge_pause_for_phase_change=self.settings.requires_charge_pause_for_phase_change,
            max_direct_switch_power_w=self.settings.max_direct_switch_power_w,
        )

    def read_switch_state(self) -> SwitchState:
        """Read one normalized switch state from the configured state endpoint."""
        payload = self._perform_request(
            self.settings.state_method,
            self.settings.state_url,
            context=self._context(False, self._selected_phase_selection),
        )
        enabled = self._enabled_state(json_path_value(payload, self.settings.state_enabled_path))
        phase_selection = self._selected_phase_selection
        if self.settings.state_phase_selection_path:
            response_selection = json_path_value(payload, self.settings.state_phase_selection_path)
            phase_selection = normalize_phase_selection(response_selection, self._selected_phase_selection)
        if phase_selection not in self.settings.supported_phase_selections:
            phase_selection = self.settings.supported_phase_selections[0]
        self._selected_phase_selection = phase_selection
        return SwitchState(
            enabled=enabled,
            phase_selection=phase_selection,
        )

    def set_enabled(self, enabled: bool) -> None:
        """Apply one enable/disable request to the configured command endpoint."""
        self._perform_request(
            self.settings.command_method,
            self.settings.command_url,
            context=self._context(bool(enabled), self._selected_phase_selection),
            json_template=self.settings.command_json_template,
        )

    def set_phase_selection(self, selection: PhaseSelection) -> None:
        """Apply one supported phase selection to the optional phase endpoint."""
        if selection not in self.settings.supported_phase_selections:
            raise ValueError(f"Unsupported phase selection '{selection}' for template switch backend")
        if self.settings.phase_url is not None:
            self._perform_request(
                self.settings.phase_method,
                self.settings.phase_url,
                context=self._context(False, selection),
                json_template=self.settings.phase_json_template,
            )
        self._selected_phase_selection = selection
