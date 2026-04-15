# SPDX-License-Identifier: GPL-3.0-or-later
"""Shelly-backed normalized meter backend."""

from __future__ import annotations

from .shelly_support import (
    ShellyBackendBase,
    phase_currents_for_selection,
    phase_powers_for_selection,
)
from .models import MeterReading
from shelly_wallbox.core.contracts import finite_float_or_none


class ShellyMeterBackend(ShellyBackendBase):
    """Read normalized power, current, voltage, and energy from one Shelly target."""

    def read_meter(self) -> MeterReading:
        """Return one normalized Shelly meter reading."""
        pm_status = self._pm_status()
        relay_on = bool(pm_status.get("output", False)) if "output" in pm_status else None
        power_w = finite_float_or_none(pm_status.get("apower")) or 0.0
        voltage_v = finite_float_or_none(pm_status.get("voltage"))
        current_a = finite_float_or_none(pm_status.get("current"))
        aenergy = pm_status.get("aenergy", {})
        total_wh = finite_float_or_none(aenergy.get("total"))
        energy_kwh = 0.0 if total_wh is None else total_wh / 1000.0
        selection = self.settings.phase_selection
        return MeterReading(
            relay_on=relay_on,
            power_w=float(power_w),
            voltage_v=voltage_v,
            current_a=current_a,
            energy_kwh=float(energy_kwh),
            phase_selection=selection,
            phase_powers_w=phase_powers_for_selection(power_w, selection, getattr(self.service, "phase", "L1")),
            phase_currents_a=phase_currents_for_selection(current_a, selection, getattr(self.service, "phase", "L1")),
        )
