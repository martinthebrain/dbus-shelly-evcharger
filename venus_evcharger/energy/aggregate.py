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


def _average_confidence(sources: tuple[EnergySourceSnapshot, ...]) -> float | None:
    confidence_values = [float(source.confidence) for source in sources if source.confidence >= 0.0]
    if not confidence_values:
        return None
    return sum(confidence_values) / float(len(confidence_values))


def _effective_soc(combined_soc: float | None, sources: tuple[EnergySourceSnapshot, ...]) -> float | None:
    if combined_soc is not None:
        return combined_soc
    fallback_sources = [source for source in sources if source.online and source.soc is not None]
    if len(fallback_sources) != 1:
        return None
    fallback_soc = fallback_sources[0].soc
    return None if fallback_soc is None else float(fallback_soc)


def _role_count(sources: tuple[EnergySourceSnapshot, ...], role: str) -> int:
    return sum(1 for source in sources if source.role == role)


def aggregate_energy_sources(sources: Iterable[EnergySourceSnapshot]) -> EnergyClusterSnapshot:
    """Return one combined energy cluster snapshot from normalized source snapshots."""
    normalized_sources = tuple(sources)
    combined_soc, combined_capacity_wh, valid_soc_source_count = _weighted_soc(normalized_sources)
    return EnergyClusterSnapshot(
        effective_soc=_effective_soc(combined_soc, normalized_sources),
        combined_soc=combined_soc,
        combined_usable_capacity_wh=combined_capacity_wh,
        combined_charge_power_w=_sum_optional(source.charge_power_w for source in normalized_sources),
        combined_discharge_power_w=_sum_optional(source.discharge_power_w for source in normalized_sources),
        combined_net_battery_power_w=_sum_optional(source.net_battery_power_w for source in normalized_sources),
        combined_ac_power_w=_sum_optional(source.ac_power_w for source in normalized_sources),
        combined_pv_input_power_w=_sum_optional(source.pv_input_power_w for source in normalized_sources),
        combined_grid_interaction_w=_sum_optional(source.grid_interaction_w for source in normalized_sources),
        average_confidence=_average_confidence(normalized_sources),
        source_count=len(normalized_sources),
        online_source_count=sum(1 for source in normalized_sources if source.online),
        valid_soc_source_count=valid_soc_source_count,
        battery_source_count=_role_count(normalized_sources, "battery"),
        hybrid_inverter_source_count=_role_count(normalized_sources, "hybrid-inverter"),
        inverter_source_count=_role_count(normalized_sources, "inverter"),
        sources=normalized_sources,
    )
