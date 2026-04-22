# SPDX-License-Identifier: GPL-3.0-or-later
"""Config parsing helpers for normalized energy-source definitions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import ENERGY_SOURCE_CONNECTOR_TYPES, ENERGY_SOURCE_ROLES, EnergySourceDefinition


def _text(value: Any, default: str = "") -> str:
    normalized = "" if value is None else str(value).strip()
    return normalized or default


def _csv_items(value: Any) -> tuple[str, ...]:
    raw = _text(value)
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _float_or_none(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric > 0.0 else None


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _legacy_primary_source(defaults: Mapping[str, Any]) -> EnergySourceDefinition:
    return EnergySourceDefinition(
        source_id="primary_battery",
        role="battery",
        connector_type="dbus",
        service_name=_text(defaults.get("AutoBatteryService")),
        service_prefix=_text(defaults.get("AutoBatteryServicePrefix"), "com.victronenergy.battery"),
        soc_path=_text(defaults.get("AutoBatterySocPath"), "/Soc"),
        usable_capacity_wh=_float_or_none(defaults.get("AutoBatteryCapacityWh")),
        battery_power_path=_text(defaults.get("AutoBatteryPowerPath")),
        ac_power_path=_text(defaults.get("AutoBatteryAcPowerPath")),
        pv_power_path=_text(defaults.get("AutoBatteryPvPowerPath")),
        grid_interaction_path=_text(defaults.get("AutoBatteryGridInteractionPath")),
        operating_mode_path=_text(defaults.get("AutoBatteryOperatingModePath")),
    )


def _configured_source(defaults: Mapping[str, Any], source_id: str) -> EnergySourceDefinition:
    prefix = f"AutoEnergySource.{source_id}."
    role = _text(defaults.get(f"{prefix}Role"), "battery").lower()
    if role not in ENERGY_SOURCE_ROLES:
        role = "battery"
    connector_type = _text(defaults.get(f"{prefix}Type"), "dbus").lower()
    if connector_type not in ENERGY_SOURCE_CONNECTOR_TYPES:
        connector_type = "dbus"
    if connector_type == "template_http_energy":
        connector_type = "template_http"
    return EnergySourceDefinition(
        source_id=source_id,
        role=role,
        connector_type=connector_type,
        config_path=_text(defaults.get(f"{prefix}ConfigPath")),
        service_name=_text(defaults.get(f"{prefix}Service")),
        service_prefix=_text(defaults.get(f"{prefix}ServicePrefix")),
        soc_path=_text(defaults.get(f"{prefix}SocPath"), "/Soc"),
        usable_capacity_wh=_float_or_none(defaults.get(f"{prefix}UsableCapacityWh")),
        battery_power_path=_text(defaults.get(f"{prefix}BatteryPowerPath")),
        ac_power_path=_text(defaults.get(f"{prefix}AcPowerPath")),
        pv_power_path=_text(defaults.get(f"{prefix}PvPowerPath")),
        grid_interaction_path=_text(defaults.get(f"{prefix}GridInteractionPath")),
        operating_mode_path=_text(defaults.get(f"{prefix}OperatingModePath")),
    )


def load_energy_source_definitions(defaults: Mapping[str, Any]) -> tuple[EnergySourceDefinition, ...]:
    """Load configured energy sources or fall back to the legacy primary battery."""
    configured_ids = _csv_items(defaults.get("AutoEnergySources"))
    if not configured_ids:
        return (_legacy_primary_source(defaults),)
    return tuple(_configured_source(defaults, source_id) for source_id in configured_ids)


def load_energy_source_settings(defaults: Mapping[str, Any]) -> tuple[tuple[EnergySourceDefinition, ...], bool]:
    """Return normalized energy sources plus whether combined SOC should drive Auto mode."""
    definitions = load_energy_source_definitions(defaults)
    use_combined_soc = _bool(defaults.get("AutoUseCombinedBatterySoc"), True)
    return definitions, use_combined_soc
