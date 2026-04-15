# SPDX-License-Identifier: GPL-3.0-or-later
"""Generic HTTP/JSON template charger backend."""

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
    ChargerState,
    PhaseSelection,
    normalize_phase_selection,
    normalize_phase_selection_tuple,
)
from dbus_shelly_wallbox_contracts import finite_float_or_none


@dataclass(frozen=True)
class TemplateChargerSettings:
    """Normalized template-charger config loaded from one adapter file."""

    base_url: str
    timeout_seconds: float
    supported_phase_selections: tuple[PhaseSelection, ...]
    state_method: str
    state_url: str | None
    state_enabled_path: str | None
    state_current_path: str | None
    state_phase_selection_path: str | None
    state_actual_current_path: str | None
    state_power_watts_path: str | None
    state_energy_kwh_path: str | None
    state_status_path: str | None
    state_fault_path: str | None
    enable_method: str
    enable_url: str
    enable_json_template: str | None
    current_method: str
    current_url: str
    current_json_template: str | None
    phase_method: str
    phase_url: str | None
    phase_json_template: str | None


def load_template_charger_settings(service: object, config_path: str) -> TemplateChargerSettings:
    """Return normalized template-charger settings."""
    parser = load_template_config(str(config_path).strip())
    adapter = config_section(parser, "Adapter")
    capabilities = config_section(parser, "Capabilities")
    state_request = config_section(parser, "StateRequest")
    state_response = config_section(parser, "StateResponse")
    enable_request = config_section(parser, "EnableRequest")
    current_request = config_section(parser, "CurrentRequest")
    phase_request = config_section(parser, "PhaseRequest")

    base_url = str(adapter.get("BaseUrl", "")).strip()
    timeout_seconds = finite_float_or_none(
        adapter.get("RequestTimeoutSeconds", getattr(service, "shelly_request_timeout_seconds", 2.0))
    )
    supported_phase_selections = normalize_phase_selection_tuple(
        capabilities.get("SupportedPhaseSelections", "P1"),
        ("P1",),
    )
    state_url = resolved_url(base_url, state_request.get("Url", ""))
    enable_url = resolved_url(base_url, enable_request.get("Url", ""))
    current_url = resolved_url(base_url, current_request.get("Url", ""))
    phase_url = resolved_url(base_url, phase_request.get("Url", ""))
    state_enabled_path = str(state_response.get("EnabledPath", "")).strip() or None
    state_current_path = str(state_response.get("CurrentPath", "")).strip() or None
    state_phase_selection_path = str(state_response.get("PhaseSelectionPath", "")).strip() or None
    state_actual_current_path = str(state_response.get("ActualCurrentPath", "")).strip() or None
    state_power_watts_path = str(state_response.get("PowerWattsPath", "")).strip() or None
    state_energy_kwh_path = str(state_response.get("EnergyKwhPath", "")).strip() or None
    state_status_path = str(state_response.get("StatusPath", "")).strip() or None
    state_fault_path = str(state_response.get("FaultPath", "")).strip() or None
    enable_json_template = (
        str(enable_request.get("JsonTemplate", '{"enabled": $enabled_json}')).strip() or None
    )
    current_json_template = (
        str(current_request.get("JsonTemplate", '{"amps": $amps}')).strip() or None
    )
    phase_json_template = (
        str(phase_request.get("JsonTemplate", '{"phase_selection": "$phase_selection"}')).strip()
        if phase_url
        else None
    )

    if not enable_url:
        raise ValueError("Template charger backend requires [EnableRequest] Url")
    if not current_url:
        raise ValueError("Template charger backend requires [CurrentRequest] Url")
    if len(supported_phase_selections) > 1 and not phase_url:
        raise ValueError(
            "Template charger backend with multi-phase support requires [PhaseRequest] Url"
        )

    return TemplateChargerSettings(
        base_url=base_url,
        timeout_seconds=2.0 if timeout_seconds is None or timeout_seconds <= 0.0 else float(timeout_seconds),
        supported_phase_selections=supported_phase_selections,
        state_method=normalize_http_method(state_request.get("Method", "GET"), "GET"),
        state_url=state_url or None,
        state_enabled_path=state_enabled_path,
        state_current_path=state_current_path,
        state_phase_selection_path=state_phase_selection_path,
        state_actual_current_path=state_actual_current_path,
        state_power_watts_path=state_power_watts_path,
        state_energy_kwh_path=state_energy_kwh_path,
        state_status_path=state_status_path,
        state_fault_path=state_fault_path,
        enable_method=normalize_http_method(enable_request.get("Method", "POST"), "POST"),
        enable_url=enable_url,
        enable_json_template=enable_json_template,
        current_method=normalize_http_method(current_request.get("Method", "POST"), "POST"),
        current_url=current_url,
        current_json_template=current_json_template,
        phase_method=normalize_http_method(phase_request.get("Method", "POST"), "POST"),
        phase_url=phase_url or None,
        phase_json_template=phase_json_template,
    )


class TemplateChargerBackend(TemplateHttpBackendBase):
    """Direct charger-control backend driven by one normalized HTTP/JSON surface."""

    def __init__(self, service: object, config_path: str = "") -> None:
        self.service = service
        self.config_path = str(config_path).strip()
        self.settings = load_template_charger_settings(service, self.config_path)
        super().__init__(service, self.settings.timeout_seconds)
        default_phase_selection = self.settings.supported_phase_selections[0]
        self._enabled_state_cache: bool | None = None
        self._current_amps_cache: float | None = None
        self._phase_selection_cache: PhaseSelection = normalize_phase_selection(
            getattr(service, "requested_phase_selection", default_phase_selection),
            default_phase_selection,
        )
        if self._phase_selection_cache not in self.settings.supported_phase_selections:
            self._phase_selection_cache = default_phase_selection

    @staticmethod
    def _context(
        *,
        enabled: bool = False,
        amps: float = 0.0,
        phase_selection: PhaseSelection = "P1",
    ) -> dict[str, str]:
        """Return one stable template context for charger command rendering."""
        return {
            "enabled_json": "true" if enabled else "false",
            "enabled_int": "1" if enabled else "0",
            "enabled_text": "on" if enabled else "off",
            "amps": f"{float(amps):g}",
            "phase_selection": str(phase_selection),
        }

    @staticmethod
    def _enabled_state(value: object) -> bool | None:
        """Return one normalized optional enabled-state from one response scalar."""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "on", "yes", "enabled"}:
            return True
        if text in {"0", "false", "off", "no", "disabled"}:
            return False
        return None

    @staticmethod
    def _optional_text(value: object) -> str | None:
        """Return one trimmed optional text field from arbitrary JSON scalars."""
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _normalized_phase_selection(
        value: object,
        default: PhaseSelection,
        supported: tuple[PhaseSelection, ...],
    ) -> PhaseSelection:
        """Return one supported charger phase selection."""
        normalized = normalize_phase_selection(value, default)
        return normalized if normalized in supported else default

    def read_charger_state(self) -> ChargerState:
        """Return one normalized charger state from readback or cached commands."""
        enabled = self._enabled_state_cache
        current_amps = self._current_amps_cache
        phase_selection = self._phase_selection_cache
        actual_current_amps = None
        power_w = None
        energy_kwh = None
        status_text = None
        fault_text = None

        if self.settings.state_url is not None:
            payload = self._perform_request(
                self.settings.state_method,
                self.settings.state_url,
                context=self._context(
                    enabled=bool(enabled) if enabled is not None else False,
                    amps=current_amps or 0.0,
                    phase_selection=phase_selection,
                ),
            )
            if self.settings.state_enabled_path is not None:
                enabled = self._enabled_state(json_path_value(payload, self.settings.state_enabled_path))
            if self.settings.state_current_path is not None:
                current_amps = finite_float_or_none(json_path_value(payload, self.settings.state_current_path))
            if self.settings.state_phase_selection_path is not None:
                phase_selection = self._normalized_phase_selection(
                    json_path_value(payload, self.settings.state_phase_selection_path),
                    phase_selection,
                    self.settings.supported_phase_selections,
                )
            if self.settings.state_actual_current_path is not None:
                actual_current_amps = finite_float_or_none(
                    json_path_value(payload, self.settings.state_actual_current_path)
                )
            if self.settings.state_power_watts_path is not None:
                power_w = finite_float_or_none(
                    json_path_value(payload, self.settings.state_power_watts_path)
                )
            if self.settings.state_energy_kwh_path is not None:
                energy_kwh = finite_float_or_none(
                    json_path_value(payload, self.settings.state_energy_kwh_path)
                )
            if self.settings.state_status_path is not None:
                status_text = self._optional_text(
                    json_path_value(payload, self.settings.state_status_path)
                )
            if self.settings.state_fault_path is not None:
                fault_text = self._optional_text(
                    json_path_value(payload, self.settings.state_fault_path)
                )

        self._enabled_state_cache = enabled
        self._current_amps_cache = current_amps
        self._phase_selection_cache = phase_selection
        return ChargerState(
            enabled=enabled,
            current_amps=current_amps,
            phase_selection=phase_selection,
            actual_current_amps=actual_current_amps,
            power_w=power_w,
            energy_kwh=energy_kwh,
            status_text=status_text,
            fault_text=fault_text,
        )

    def set_enabled(self, enabled: bool) -> None:
        """Apply one charger enable/disable request."""
        self._perform_request(
            self.settings.enable_method,
            self.settings.enable_url,
            context=self._context(enabled=bool(enabled)),
            json_template=self.settings.enable_json_template,
        )
        self._enabled_state_cache = bool(enabled)

    def set_current(self, amps: float) -> None:
        """Apply one charger current request."""
        normalized_amps = finite_float_or_none(amps)
        if normalized_amps is None or normalized_amps < 0.0:
            raise ValueError(f"Unsupported charger current '{amps}'")
        self._perform_request(
            self.settings.current_method,
            self.settings.current_url,
            context=self._context(amps=float(normalized_amps)),
            json_template=self.settings.current_json_template,
        )
        self._current_amps_cache = float(normalized_amps)

    def set_phase_selection(self, selection: PhaseSelection) -> None:
        """Apply one supported charger phase-selection request."""
        if selection not in self.settings.supported_phase_selections:
            raise ValueError(f"Unsupported phase selection '{selection}' for template charger backend")
        if self.settings.phase_url is None:
            return
        self._perform_request(
            self.settings.phase_method,
            self.settings.phase_url,
            context=self._context(phase_selection=selection),
            json_template=self.settings.phase_json_template,
        )
        self._phase_selection_cache = selection
