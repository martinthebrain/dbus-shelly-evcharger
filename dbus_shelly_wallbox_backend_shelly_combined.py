# SPDX-License-Identifier: GPL-3.0-or-later
"""Compatibility backend that wraps the current combined Shelly behavior."""

from __future__ import annotations

from typing import Any, cast

from dbus_shelly_wallbox_backend_types import MeterReading, PhaseSelection, SwitchCapabilities, SwitchState
from dbus_shelly_wallbox_contracts import finite_float_or_none
from dbus_shelly_wallbox_shelly_io import ShellyPmStatus


class ShellyCombinedBackend:
    """Expose the current Shelly-based meter/switch behavior behind one backend seam."""

    def __init__(self, service: Any, config_path: str = "") -> None:
        self.service = service
        self.config_path = str(config_path).strip()

    @staticmethod
    def _phase_selection_for_service(phase: object) -> PhaseSelection:
        """Map legacy phase config to one normalized phase-selection shape."""
        phase_name = str(phase).strip().upper() if phase is not None else "L1"
        return "P1_P2_P3" if phase_name == "3P" else "P1"

    @staticmethod
    def _phase_powers(power_w: float, phase: object) -> tuple[float, float, float]:
        """Return per-line powers for the configured display wiring."""
        phase_name = str(phase).strip().upper() if phase is not None else "L1"
        if phase_name == "3P":
            per_phase = float(power_w) / 3.0
            return per_phase, per_phase, per_phase
        if phase_name == "L2":
            return 0.0, float(power_w), 0.0
        if phase_name == "L3":
            return 0.0, 0.0, float(power_w)
        return float(power_w), 0.0, 0.0

    @staticmethod
    def _phase_currents(current_a: float | None, phase: object) -> tuple[float, float, float] | None:
        """Return per-line currents for the configured display wiring."""
        if current_a is None:
            return None
        phase_name = str(phase).strip().upper() if phase is not None else "L1"
        if phase_name == "3P":
            per_phase = float(current_a) / 3.0
            return per_phase, per_phase, per_phase
        if phase_name == "L2":
            return 0.0, float(current_a), 0.0
        if phase_name == "L3":
            return 0.0, 0.0, float(current_a)
        return float(current_a), 0.0, 0.0

    def _pm_status(self) -> ShellyPmStatus:
        """Return the current Shelly PM status from the service wrapper."""
        return cast(ShellyPmStatus, self.service.fetch_pm_status())

    def read_meter(self) -> MeterReading:
        """Read one normalized Shelly meter snapshot."""
        pm_status = self._pm_status()
        relay_on = bool(pm_status.get("output", False)) if "output" in pm_status else None
        power_w = finite_float_or_none(pm_status.get("apower")) or 0.0
        voltage_v = finite_float_or_none(pm_status.get("voltage"))
        current_a = finite_float_or_none(pm_status.get("current"))
        aenergy = pm_status.get("aenergy", {})
        total_wh = finite_float_or_none(aenergy.get("total"))
        energy_kwh = 0.0 if total_wh is None else total_wh / 1000.0
        phase = getattr(self.service, "phase", "L1")
        return MeterReading(
            relay_on=relay_on,
            power_w=float(power_w),
            voltage_v=voltage_v,
            current_a=current_a,
            energy_kwh=float(energy_kwh),
            phase_selection=self._phase_selection_for_service(phase),
            phase_powers_w=self._phase_powers(power_w, phase),
            phase_currents_a=self._phase_currents(current_a, phase),
        )

    def capabilities(self) -> SwitchCapabilities:
        """Return conservative direct-switch capabilities for the current Shelly setup."""
        max_current = finite_float_or_none(getattr(self.service, "max_current", None))
        voltage = finite_float_or_none(getattr(self.service, "_last_voltage", None))
        max_direct_switch_power_w = None
        if max_current is not None and voltage is not None and max_current > 0 and voltage > 0:
            max_direct_switch_power_w = max_current * voltage
        return SwitchCapabilities(
            switching_mode="direct",
            supported_phase_selections=("P1",),
            requires_charge_pause_for_phase_change=False,
            max_direct_switch_power_w=max_direct_switch_power_w,
        )

    def read_switch_state(self) -> SwitchState:
        """Return the current normalized switch state."""
        pending_relay, _requested_at = self.service._peek_pending_relay_command()
        if pending_relay is not None:
            enabled = bool(pending_relay)
        else:
            last_pm_status = cast(dict[str, object] | None, getattr(self.service, "_last_pm_status", None))
            enabled = bool(last_pm_status.get("output", False)) if isinstance(last_pm_status, dict) else False
        return SwitchState(enabled=enabled, phase_selection="P1")

    def set_enabled(self, enabled: bool) -> None:
        """Switch the Shelly relay output directly."""
        self.service.set_relay(bool(enabled))

    def set_phase_selection(self, selection: PhaseSelection) -> None:
        """Reject unsupported multi-phase requests for the current Shelly backend."""
        if selection != "P1":
            raise ValueError("ShellyCombinedBackend only supports single-phase switching")
