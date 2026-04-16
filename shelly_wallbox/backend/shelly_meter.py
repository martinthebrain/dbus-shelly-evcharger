# SPDX-License-Identifier: GPL-3.0-or-later
"""Shelly-backed normalized meter backend."""

from __future__ import annotations

from typing import Mapping

from .models import MeterReading
from .shelly_support import (
    ShellyBackendBase,
    phase_currents_for_selection,
    phase_powers_for_selection,
    validate_shelly_profile_role,
)
from shelly_wallbox.core.contracts import finite_float_or_none


def _payload_value(payload: Mapping[str, object], path: str) -> object | None:
    """Return one dotted payload value when present."""
    current: object = payload
    for part in str(path).split("."):
        token = part.strip()
        if not token or not isinstance(current, Mapping) or token not in current:
            return None
        current = current[token]
    return current


def _payload_float(payload: Mapping[str, object], *paths: str) -> float | None:
    """Return the first finite float found at the given payload paths."""
    for path in paths:
        value = finite_float_or_none(_payload_value(payload, path))
        if value is not None:
            return float(value)
    return None


def _phase_values(
    payload: Mapping[str, object],
    *,
    suffixes: tuple[str, ...],
) -> tuple[float, float, float] | None:
    """Return per-phase values when the payload exposes them explicitly."""
    values: list[float] = []
    found = False
    for prefix in ("a", "b", "c"):
        value = _payload_float(payload, *(f"{prefix}_{suffix}" for suffix in suffixes))
        if value is not None:
            found = True
        values.append(0.0 if value is None else float(value))
    if not found:
        return None
    return float(values[0]), float(values[1]), float(values[2])


def _average_nonzero(values: tuple[float, float, float] | None) -> float | None:
    """Return the average of present non-zero values."""
    if values is None:
        return None
    present = [float(value) for value in values if float(value) > 0.0]
    if not present:
        return None
    return sum(present) / float(len(present))


class ShellyMeterBackend(ShellyBackendBase):
    """Read normalized power, current, voltage, and energy from one Shelly target."""

    def __init__(self, service: object, config_path: str = "") -> None:
        super().__init__(service, config_path=config_path)
        validate_shelly_profile_role(self.settings.profile_name, "meter")

    def read_meter(self) -> MeterReading:
        """Return one normalized Shelly meter reading."""
        pm_status = self._pm_status()
        relay_on = bool(pm_status.get("output", False)) if "output" in pm_status else None

        phase_powers = _phase_values(pm_status, suffixes=("act_power", "apower", "power"))
        phase_currents = _phase_values(pm_status, suffixes=("current",))
        phase_voltages = _phase_values(pm_status, suffixes=("voltage",))
        phase_energies_wh = _phase_values(pm_status, suffixes=("total_act_energy", "total_energy"))

        power_w = _payload_float(pm_status, "apower", "total_act_power", "act_power", "power")
        if power_w is None and phase_powers is not None:
            power_w = sum(phase_powers)

        current_a = _payload_float(pm_status, "current", "total_current")
        if current_a is None and phase_currents is not None:
            current_a = sum(phase_currents)

        voltage_v = _payload_float(pm_status, "voltage")
        if voltage_v is None:
            voltage_v = _average_nonzero(phase_voltages)

        total_wh = _payload_float(pm_status, "aenergy.total", "total_act_energy", "total_energy")
        if total_wh is None and phase_energies_wh is not None:
            total_wh = sum(phase_energies_wh)
        energy_kwh = 0.0 if total_wh is None else float(total_wh) / 1000.0

        selection = self.settings.phase_selection
        normalized_phase_powers = (
            phase_powers
            if phase_powers is not None
            else phase_powers_for_selection(power_w or 0.0, selection, getattr(self.service, "phase", "L1"))
        )
        normalized_phase_currents = (
            phase_currents
            if phase_currents is not None
            else phase_currents_for_selection(current_a, selection, getattr(self.service, "phase", "L1"))
        )
        return MeterReading(
            relay_on=relay_on,
            power_w=float(power_w or 0.0),
            voltage_v=voltage_v,
            current_a=current_a,
            energy_kwh=float(energy_kwh),
            phase_selection=selection,
            phase_powers_w=normalized_phase_powers,
            phase_currents_a=normalized_phase_currents,
        )
