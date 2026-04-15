# SPDX-License-Identifier: GPL-3.0-or-later
"""Generic HTTP/JSON template meter backend."""

from __future__ import annotations

from dataclasses import dataclass

from .shelly_support import (
    phase_currents_for_selection,
    phase_powers_for_selection,
)
from .template_support import (
    TemplateHttpBackendBase,
    config_section,
    json_path_value,
    load_template_config,
    normalize_http_method,
    resolved_url,
)
from .models import MeterReading, PhaseSelection, normalize_phase_selection
from shelly_wallbox.core.contracts import finite_float_or_none


@dataclass(frozen=True)
class TemplateMeterSettings:
    """Normalized template-meter config loaded from one adapter file."""

    base_url: str
    timeout_seconds: float
    meter_method: str
    meter_url: str
    relay_enabled_path: str | None
    power_path: str
    voltage_path: str | None
    current_path: str | None
    energy_kwh_path: str | None
    energy_wh_path: str | None
    phase_selection: PhaseSelection
    phase_selection_path: str | None
    phase_powers_path: str | None
    phase_currents_path: str | None


def _phase_vector(value: object) -> tuple[float, float, float] | None:
    """Return one 3-value phase vector when the response payload is valid."""
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    numbers = tuple(finite_float_or_none(item) for item in value)
    if any(number is None for number in numbers):
        return None
    return float(numbers[0] or 0.0), float(numbers[1] or 0.0), float(numbers[2] or 0.0)


def _optional_path(section_value: object) -> str | None:
    """Return one normalized optional dotted response path."""
    return str(section_value).strip() or None


def load_template_meter_settings(service: object, config_path: str) -> TemplateMeterSettings:
    """Return normalized template-meter settings."""
    parser = load_template_config(str(config_path).strip())
    adapter = config_section(parser, "Adapter")
    phase = config_section(parser, "Phase")
    meter_request = config_section(parser, "MeterRequest")
    meter_response = config_section(parser, "MeterResponse")

    base_url = str(adapter.get("BaseUrl", "")).strip()
    timeout_seconds = finite_float_or_none(
        adapter.get("RequestTimeoutSeconds", getattr(service, "shelly_request_timeout_seconds", 2.0))
    )
    meter_url = resolved_url(base_url, meter_request.get("Url", ""))
    power_path = str(meter_response.get("PowerPath", "power_w")).strip()
    if not meter_url:
        raise ValueError("Template meter backend requires [MeterRequest] Url")
    if not power_path:
        raise ValueError("Template meter backend requires [MeterResponse] PowerPath")

    default_phase = normalize_phase_selection(
        phase.get("MeasuredPhaseSelection", phase.get("MeasuredPhase", getattr(service, "phase", "L1"))),
        "P1",
    )
    return TemplateMeterSettings(
        base_url=base_url,
        timeout_seconds=2.0 if timeout_seconds is None or timeout_seconds <= 0.0 else float(timeout_seconds),
        meter_method=normalize_http_method(meter_request.get("Method", "GET"), "GET"),
        meter_url=meter_url,
        relay_enabled_path=_optional_path(meter_response.get("RelayEnabledPath", "")),
        power_path=power_path,
        voltage_path=_optional_path(meter_response.get("VoltagePath", "")),
        current_path=_optional_path(meter_response.get("CurrentPath", "")),
        energy_kwh_path=_optional_path(meter_response.get("EnergyKwhPath", "")),
        energy_wh_path=_optional_path(meter_response.get("EnergyWhPath", "")),
        phase_selection=default_phase,
        phase_selection_path=_optional_path(meter_response.get("PhaseSelectionPath", "")),
        phase_powers_path=_optional_path(meter_response.get("PhasePowersPath", "")),
        phase_currents_path=_optional_path(meter_response.get("PhaseCurrentsPath", "")),
    )


class TemplateMeterBackend(TemplateHttpBackendBase):
    """Meter backend driven by one normalized HTTP/JSON adapter surface."""

    def __init__(self, service: object, config_path: str = "") -> None:
        self.service = service
        self.config_path = str(config_path).strip()
        self.settings = load_template_meter_settings(service, self.config_path)
        super().__init__(service, self.settings.timeout_seconds)

    @staticmethod
    def _enabled_state(value: object) -> bool | None:
        """Return one normalized optional relay state from one response scalar."""
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

    def read_meter(self) -> MeterReading:
        """Read one normalized meter reading from the configured template endpoint."""
        payload = self._perform_request(
            self.settings.meter_method,
            self.settings.meter_url,
        )
        relay_on = (
            None
            if self.settings.relay_enabled_path is None
            else self._enabled_state(json_path_value(payload, self.settings.relay_enabled_path))
        )
        power_w = finite_float_or_none(json_path_value(payload, self.settings.power_path))
        if power_w is None:
            raise ValueError(f"Invalid meter power value at '{self.settings.power_path}'")
        voltage_v = (
            None
            if self.settings.voltage_path is None
            else finite_float_or_none(json_path_value(payload, self.settings.voltage_path))
        )
        current_a = (
            None
            if self.settings.current_path is None
            else finite_float_or_none(json_path_value(payload, self.settings.current_path))
        )
        energy_kwh = 0.0
        if self.settings.energy_kwh_path is not None:
            energy_value = finite_float_or_none(json_path_value(payload, self.settings.energy_kwh_path))
            energy_kwh = 0.0 if energy_value is None else float(energy_value)
        elif self.settings.energy_wh_path is not None:
            energy_wh = finite_float_or_none(json_path_value(payload, self.settings.energy_wh_path))
            energy_kwh = 0.0 if energy_wh is None else float(energy_wh) / 1000.0

        phase_selection = self.settings.phase_selection
        if self.settings.phase_selection_path is not None:
            phase_selection = normalize_phase_selection(
                json_path_value(payload, self.settings.phase_selection_path),
                phase_selection,
            )

        phase_powers_w = (
            None
            if self.settings.phase_powers_path is None
            else _phase_vector(json_path_value(payload, self.settings.phase_powers_path))
        )
        phase_currents_a = (
            None
            if self.settings.phase_currents_path is None
            else _phase_vector(json_path_value(payload, self.settings.phase_currents_path))
        )

        if phase_powers_w is None:
            phase_powers_w = phase_powers_for_selection(
                float(power_w),
                phase_selection,
                getattr(self.service, "phase", "L1"),
            )
        if phase_currents_a is None:
            phase_currents_a = phase_currents_for_selection(
                current_a,
                phase_selection,
                getattr(self.service, "phase", "L1"),
            )

        return MeterReading(
            relay_on=relay_on,
            power_w=float(power_w),
            voltage_v=voltage_v,
            current_a=current_a,
            energy_kwh=float(energy_kwh),
            phase_selection=phase_selection,
            phase_powers_w=phase_powers_w,
            phase_currents_a=phase_currents_a,
        )
