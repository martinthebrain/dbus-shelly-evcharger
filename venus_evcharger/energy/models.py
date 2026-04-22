# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalized energy-source data models used by helper and service code."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ENERGY_SOURCE_ROLES = frozenset({"battery", "hybrid-inverter", "inverter"})
ENERGY_SOURCE_CONNECTOR_TYPES = frozenset(
    {"dbus", "template_http", "template_http_energy", "modbus", "command_json"}
)


@dataclass(frozen=True)
class EnergySourceDefinition:
    """Describe one battery or inverter-like DBus-backed energy source."""

    source_id: str
    role: str = "battery"
    connector_type: str = "dbus"
    config_path: str = ""
    service_name: str = ""
    service_prefix: str = ""
    soc_path: str = "/Soc"
    usable_capacity_wh: float | None = None
    battery_power_path: str = ""
    ac_power_path: str = ""


@dataclass(frozen=True)
class EnergySourceSnapshot:
    """Runtime snapshot for one external or Victron-backed energy source."""

    source_id: str
    role: str
    service_name: str
    soc: float | None = None
    usable_capacity_wh: float | None = None
    net_battery_power_w: float | None = None
    ac_power_w: float | None = None
    online: bool = False
    confidence: float = 0.0
    captured_at: float | None = None

    @property
    def charge_power_w(self) -> float | None:
        if self.net_battery_power_w is None:
            return None
        return max(0.0, -float(self.net_battery_power_w))

    @property
    def discharge_power_w(self) -> float | None:
        if self.net_battery_power_w is None:
            return None
        return max(0.0, float(self.net_battery_power_w))

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "role": self.role,
            "service_name": self.service_name,
            "soc": self.soc,
            "usable_capacity_wh": self.usable_capacity_wh,
            "net_battery_power_w": self.net_battery_power_w,
            "charge_power_w": self.charge_power_w,
            "discharge_power_w": self.discharge_power_w,
            "ac_power_w": self.ac_power_w,
            "online": self.online,
            "confidence": self.confidence,
            "captured_at": self.captured_at,
        }


@dataclass(frozen=True)
class EnergyClusterSnapshot:
    """Aggregated energy picture computed from normalized source snapshots."""

    effective_soc: float | None = None
    combined_soc: float | None = None
    combined_usable_capacity_wh: float | None = None
    combined_charge_power_w: float | None = None
    combined_discharge_power_w: float | None = None
    combined_net_battery_power_w: float | None = None
    combined_ac_power_w: float | None = None
    source_count: int = 0
    online_source_count: int = 0
    valid_soc_source_count: int = 0
    sources: tuple[EnergySourceSnapshot, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "effective_soc": self.effective_soc,
            "combined_soc": self.combined_soc,
            "combined_usable_capacity_wh": self.combined_usable_capacity_wh,
            "combined_charge_power_w": self.combined_charge_power_w,
            "combined_discharge_power_w": self.combined_discharge_power_w,
            "combined_net_battery_power_w": self.combined_net_battery_power_w,
            "combined_ac_power_w": self.combined_ac_power_w,
            "source_count": self.source_count,
            "online_source_count": self.online_source_count,
            "valid_soc_source_count": self.valid_soc_source_count,
            "sources": [source.as_dict() for source in self.sources],
        }


@dataclass(frozen=True)
class EnergyLearningProfile:
    """Simple runtime-learned behaviour metrics for one energy source."""

    source_id: str
    sample_count: int = 0
    observed_max_charge_power_w: float | None = None
    observed_max_discharge_power_w: float | None = None
    observed_max_ac_power_w: float | None = None
    last_change_at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "sample_count": self.sample_count,
            "observed_max_charge_power_w": self.observed_max_charge_power_w,
            "observed_max_discharge_power_w": self.observed_max_discharge_power_w,
            "observed_max_ac_power_w": self.observed_max_ac_power_w,
            "last_change_at": self.last_change_at,
        }
