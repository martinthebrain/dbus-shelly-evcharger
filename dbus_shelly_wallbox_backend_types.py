# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalized backend-facing result and selection types for the wallbox service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

PhaseSelection = Literal["P1", "P1_P2", "P1_P2_P3"]
SwitchingMode = Literal["direct", "contactor"]
BackendMode = Literal["combined", "split"]


def normalize_phase_selection(value: object, default: PhaseSelection = "P1") -> PhaseSelection:
    """Return one normalized phase-selection value."""
    selection = str(value).strip().upper() if value is not None else ""
    if selection in {"P1", "L1", "L2", "L3", "1P"}:
        return "P1"
    if selection in {"P1_P2", "P1+P2", "2P"}:
        return "P1_P2"
    if selection in {"P1_P2_P3", "P1+P2+P3", "3P"}:
        return "P1_P2_P3"
    return default


def normalize_phase_selection_tuple(
    values: object,
    default: tuple[PhaseSelection, ...] = ("P1",),
) -> tuple[PhaseSelection, ...]:
    """Return one normalized supported-phase tuple from arbitrary runtime data."""
    if isinstance(values, (tuple, list)):
        normalized = cast(
            tuple[PhaseSelection, ...],
            tuple(normalize_phase_selection(value, "P1") for value in values),
        )
        return normalized or default
    text = str(values).strip() if values is not None else ""
    if not text:
        return default
    normalized = cast(
        tuple[PhaseSelection, ...],
        tuple(normalize_phase_selection(part, "P1") for part in text.split(",")),
    )
    return normalized or default


@dataclass(frozen=True)
class MeterReading:
    """Normalized meter reading returned by one meter backend."""

    relay_on: bool | None
    power_w: float
    voltage_v: float | None
    current_a: float | None
    energy_kwh: float
    phase_selection: PhaseSelection
    phase_powers_w: tuple[float, float, float] | None = None
    phase_currents_a: tuple[float, float, float] | None = None


@dataclass(frozen=True)
class SwitchCapabilities:
    """Declarative switch-backend capabilities consumed by wallbox core logic."""

    switching_mode: SwitchingMode
    supported_phase_selections: tuple[PhaseSelection, ...]
    requires_charge_pause_for_phase_change: bool
    max_direct_switch_power_w: float | None


@dataclass(frozen=True)
class SwitchState:
    """Normalized switch state exposed by one switch backend."""

    enabled: bool
    phase_selection: PhaseSelection


@dataclass(frozen=True)
class ChargerState:
    """Normalized charger state exposed by one charger backend."""

    enabled: bool | None
    current_amps: float | None
    phase_selection: PhaseSelection | None
    actual_current_amps: float | None = None
    power_w: float | None = None
    energy_kwh: float | None = None
    status_text: str | None = None
    fault_text: str | None = None


@dataclass(frozen=True)
class BackendSelection:
    """Normalized backend selection derived from wallbox configuration."""

    mode: BackendMode
    meter_type: str
    switch_type: str
    charger_type: str | None
    meter_config_path: str
    switch_config_path: str
    charger_config_path: str
