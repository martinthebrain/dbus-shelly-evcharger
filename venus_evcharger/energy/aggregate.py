# SPDX-License-Identifier: GPL-3.0-or-later
"""Aggregation helpers for multi-source battery and inverter snapshots."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .models import EnergyClusterSnapshot, EnergySourceDefinition, EnergySourceSnapshot
from .profiles import energy_source_profile_details


def _sum_optional(values: Iterable[float | None]) -> float | None:
    numeric_values = [float(value) for value in values if value is not None]
    if not numeric_values:
        return None
    return sum(numeric_values)


def _sum_scoped_values(sources: tuple[EnergySourceSnapshot, ...], value_attr: str, scope_attr: str) -> float | None:
    numeric_values: list[float] = []
    seen_scope_keys: set[str] = set()
    for source in sources:
        value = getattr(source, value_attr)
        if value is None:
            continue
        scope_key = str(getattr(source, scope_attr, "") or "").strip()
        if scope_key:
            if scope_key in seen_scope_keys:
                continue
            seen_scope_keys.add(scope_key)
        numeric_values.append(float(value))
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


def _optional_float(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return float(value)


def _non_negative_optional_float(value: Any) -> float | None:
    numeric = _optional_float(value)
    if numeric is None:
        return None
    return max(0.0, numeric)


def _balance_profile_for_source(
    learning_profiles: Mapping[str, Any],
    source_id: str,
) -> Mapping[str, Any]:
    raw_profile = learning_profiles.get(source_id)
    return raw_profile if isinstance(raw_profile, Mapping) else {}


def _discharge_balance_weight(
    source: EnergySourceSnapshot,
    reserve_floor_soc: float | None,
) -> tuple[float, float | None, str]:
    capacity_wh = _non_negative_optional_float(source.usable_capacity_wh)
    source_soc = _non_negative_optional_float(source.soc)
    normalized_floor = 0.0 if reserve_floor_soc is None else max(0.0, float(reserve_floor_soc))
    available_energy_wh: float | None = None
    if capacity_wh is not None and capacity_wh > 0.0 and source_soc is not None:
        available_fraction = max(0.0, float(source_soc) - normalized_floor) / 100.0
        available_energy_wh = capacity_wh * available_fraction
    if available_energy_wh is not None and available_energy_wh > 0.0:
        return available_energy_wh, available_energy_wh, "available_energy_above_reserve"
    if capacity_wh is not None and capacity_wh > 0.0:
        return capacity_wh, available_energy_wh, "usable_capacity_fallback"
    return 1.0, available_energy_wh, "uniform_fallback"


def derive_discharge_balance_metrics(
    sources: Iterable[EnergySourceSnapshot],
    learning_profiles: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return fairness diagnostics for concurrent ESS discharge behavior."""
    normalized_sources = tuple(sources)
    profiles = dict(learning_profiles or {})
    eligible_sources: list[dict[str, Any]] = []
    total_discharge_w = 0.0
    for source in normalized_sources:
        if source.role not in {"battery", "hybrid-inverter"}:
            continue
        if not bool(source.online):
            continue
        profile = _balance_profile_for_source(profiles, source.source_id)
        reserve_floor_soc = _non_negative_optional_float(profile.get("reserve_band_floor_soc"))
        weight, available_energy_wh, weight_basis = _discharge_balance_weight(source, reserve_floor_soc)
        actual_discharge_w = max(0.0, float(source.discharge_power_w or 0.0))
        total_discharge_w += actual_discharge_w
        eligible_sources.append(
            {
                "source_id": source.source_id,
                "weight": float(weight),
                "weight_basis": weight_basis,
                "available_energy_wh": available_energy_wh,
                "reserve_floor_soc": reserve_floor_soc,
                "actual_discharge_w": actual_discharge_w,
            }
        )
    if not eligible_sources:
        return {
            "mode": "capacity_reserve_weighted",
            "target_distribution_mode": "capacity_reserve_weighted",
            "eligible_source_count": 0,
            "active_source_count": 0,
            "total_discharge_w": None,
            "error_w": None,
            "max_abs_error_w": None,
            "sources": {},
        }
    total_weight = sum(float(item["weight"]) for item in eligible_sources)
    if total_weight <= 0.0:
        equal_weight = 1.0 / float(len(eligible_sources))
        for item in eligible_sources:
            item["weight"] = equal_weight
        total_weight = 1.0
    total_abs_error_w = 0.0
    max_abs_error_w = 0.0
    source_metrics: dict[str, dict[str, Any]] = {}
    for item in eligible_sources:
        target_share = float(item["weight"]) / float(total_weight)
        target_discharge_w = float(total_discharge_w) * target_share
        error_w = float(item["actual_discharge_w"]) - float(target_discharge_w)
        total_abs_error_w += abs(error_w)
        max_abs_error_w = max(max_abs_error_w, abs(error_w))
        source_metrics[str(item["source_id"])] = {
            "discharge_balance_eligible": True,
            "discharge_balance_weight": float(item["weight"]),
            "discharge_balance_weight_basis": str(item["weight_basis"]),
            "discharge_balance_available_energy_wh": item["available_energy_wh"],
            "discharge_balance_reserve_floor_soc": item["reserve_floor_soc"],
            "discharge_balance_target_distribution_mode": "capacity_reserve_weighted",
            "discharge_balance_target_share": target_share,
            "discharge_balance_target_power_w": target_discharge_w,
            "discharge_balance_actual_power_w": float(item["actual_discharge_w"]),
            "discharge_balance_error_w": error_w,
            "discharge_balance_relative_error": (
                error_w / float(total_discharge_w) if total_discharge_w > 0.0 else None
            ),
        }
    return {
        "mode": "capacity_reserve_weighted",
        "target_distribution_mode": "capacity_reserve_weighted",
        "eligible_source_count": len(eligible_sources),
        "active_source_count": sum(1 for item in eligible_sources if float(item["actual_discharge_w"]) > 0.0),
        "total_discharge_w": float(total_discharge_w),
        "error_w": total_abs_error_w / 2.0,
        "max_abs_error_w": max_abs_error_w,
        "sources": source_metrics,
    }


def derive_discharge_control_metrics(
    sources: Iterable[EnergySourceSnapshot],
    source_definitions: Mapping[str, EnergySourceDefinition] | None = None,
) -> dict[str, Any]:
    """Return diagnostic write-capability hints for per-source discharge coordination."""
    normalized_sources = tuple(sources)
    definitions = dict(source_definitions or {})
    source_metrics: dict[str, dict[str, Any]] = {}
    candidate_count = 0
    ready_count = 0
    supported_count = 0
    experimental_count = 0
    for source in normalized_sources:
        definition = definitions.get(source.source_id)
        profile_name = ""
        connector_type = source.role
        role = source.role
        if definition is not None:
            profile_name = str(definition.profile_name or "").strip()
            connector_type = str(definition.connector_type or "").strip().lower() or connector_type
            role = str(definition.role or role).strip().lower() or role
        profile_details = energy_source_profile_details(profile_name)
        write_support = str(profile_details.get("write_support", "unsupported") or "unsupported").strip().lower()
        controllable_role = role in {"battery", "hybrid-inverter"}
        control_candidate = controllable_role and write_support in {"supported", "experimental"}
        control_ready = control_candidate and bool(source.online)
        if control_candidate:
            candidate_count += 1
        if control_ready:
            ready_count += 1
        if controllable_role and write_support == "supported":
            supported_count += 1
        if controllable_role and write_support == "experimental":
            experimental_count += 1
        reason = "profile_write_unsupported"
        if not controllable_role:
            reason = "role_not_targeted"
        elif write_support == "supported":
            reason = "profile_write_supported"
        elif write_support == "experimental":
            reason = "profile_write_experimental"
        source_metrics[source.source_id] = {
            "discharge_balance_control_profile_name": profile_name,
            "discharge_balance_control_connector_type": connector_type,
            "discharge_balance_control_support": write_support,
            "discharge_balance_control_candidate": control_candidate,
            "discharge_balance_control_ready": control_ready,
            "discharge_balance_control_reason": reason,
        }
    return {
        "control_candidate_count": candidate_count,
        "control_ready_count": ready_count,
        "supported_control_source_count": supported_count,
        "experimental_control_source_count": experimental_count,
        "sources": source_metrics,
    }


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
        combined_charge_limit_power_w=_sum_optional(source.charge_limit_power_w for source in normalized_sources),
        combined_discharge_limit_power_w=_sum_optional(
            source.discharge_limit_power_w for source in normalized_sources
        ),
        combined_net_battery_power_w=_sum_optional(source.net_battery_power_w for source in normalized_sources),
        combined_ac_power_w=_sum_scoped_values(normalized_sources, "ac_power_w", "ac_power_scope_key"),
        combined_pv_input_power_w=_sum_scoped_values(normalized_sources, "pv_input_power_w", "pv_input_power_scope_key"),
        combined_grid_interaction_w=_sum_scoped_values(
            normalized_sources,
            "grid_interaction_w",
            "grid_interaction_scope_key",
        ),
        average_confidence=_average_confidence(normalized_sources),
        source_count=len(normalized_sources),
        online_source_count=sum(1 for source in normalized_sources if source.online),
        valid_soc_source_count=valid_soc_source_count,
        battery_source_count=_role_count(normalized_sources, "battery"),
        hybrid_inverter_source_count=_role_count(normalized_sources, "hybrid-inverter"),
        inverter_source_count=_role_count(normalized_sources, "inverter"),
        sources=normalized_sources,
    )
