# SPDX-License-Identifier: GPL-3.0-or-later
"""Aggregation helpers for multi-source battery and inverter snapshots."""

from __future__ import annotations

from typing import Iterable

from .models import EnergyClusterSnapshot, EnergySourceSnapshot


def _sum_optional(values: Iterable[float | None]) -> float | None:
    numeric_values = [float(value) for value in values if value is not None]
    if not numeric_values:
        return None
    return sum(numeric_values)


def _weighted_soc(sources: tuple[EnergySourceSnapshot, ...]) -> tuple[float | None, float | None, int]:
    total_capacity = 0.0
    total_soc_energy = 0.0
    count = 0
    for source in sources:
        if source.soc is None or source.usable_capacity_wh is None or source.usable_capacity_wh <= 0.0:
            continue
        total_capacity += float(source.usable_capacity_wh)
        total_soc_energy += float(source.usable_capacity_wh) * float(source.soc)
        count += 1
    if total_capacity <= 0.0:
        return None, None, count
    return total_soc_energy / total_capacity, total_capacity, count


def aggregate_energy_sources(sources: Iterable[EnergySourceSnapshot]) -> EnergyClusterSnapshot:
    """Return one combined energy cluster snapshot from normalized source snapshots."""
    normalized_sources = tuple(sources)
    combined_soc, combined_capacity_wh, valid_soc_source_count = _weighted_soc(normalized_sources)
    online_source_count = sum(1 for source in normalized_sources if source.online)
    combined_charge_power_w = _sum_optional(source.charge_power_w for source in normalized_sources)
    combined_discharge_power_w = _sum_optional(source.discharge_power_w for source in normalized_sources)
    combined_net_battery_power_w = _sum_optional(source.net_battery_power_w for source in normalized_sources)
    combined_ac_power_w = _sum_optional(source.ac_power_w for source in normalized_sources)
    effective_soc = combined_soc
    if effective_soc is None:
        fallback_sources = [source for source in normalized_sources if source.online and source.soc is not None]
        if len(fallback_sources) == 1:
            fallback_soc = fallback_sources[0].soc
            if fallback_soc is not None:
                effective_soc = float(fallback_soc)
    return EnergyClusterSnapshot(
        effective_soc=effective_soc,
        combined_soc=combined_soc,
        combined_usable_capacity_wh=combined_capacity_wh,
        combined_charge_power_w=combined_charge_power_w,
        combined_discharge_power_w=combined_discharge_power_w,
        combined_net_battery_power_w=combined_net_battery_power_w,
        combined_ac_power_w=combined_ac_power_w,
        source_count=len(normalized_sources),
        online_source_count=online_source_count,
        valid_soc_source_count=valid_soc_source_count,
        sources=normalized_sources,
    )
