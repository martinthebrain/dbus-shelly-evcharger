# SPDX-License-Identifier: GPL-3.0-or-later
"""Named energy-source profile defaults for common external-source setups."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EnergySourceProfile:
    """Default connector and path settings for one named energy-source profile."""

    profile_name: str
    role: str
    connector_type: str
    vendor_name: str = ""
    platform: str = ""
    access_mode: str = ""
    firmware_class: str = ""
    service_prefix: str = ""
    soc_path: str = ""
    battery_power_path: str = ""
    ac_power_path: str = ""
    pv_power_path: str = ""
    grid_interaction_path: str = ""
    operating_mode_path: str = ""
    default_host: str = ""
    default_port_candidates: tuple[int, ...] = ()
    default_unit_id_candidates: tuple[int, ...] = ()
    read_support: str = "supported"
    write_support: str = "unsupported"
    probe_required: bool = False


_PROFILES = {
    "dbus-battery": EnergySourceProfile(
        profile_name="dbus-battery",
        role="battery",
        connector_type="dbus",
        service_prefix="com.victronenergy.battery",
        soc_path="/Soc",
        battery_power_path="/Dc/0/Power",
    ),
    "dbus-hybrid": EnergySourceProfile(
        profile_name="dbus-hybrid",
        role="hybrid-inverter",
        connector_type="dbus",
        soc_path="/Soc",
        battery_power_path="/Dc/0/Power",
        ac_power_path="/Ac/Power",
        pv_power_path="/Pv/Power",
        grid_interaction_path="/Grid/Power",
        operating_mode_path="/Mode",
    ),
    "template-http-hybrid": EnergySourceProfile(
        profile_name="template-http-hybrid",
        role="hybrid-inverter",
        connector_type="template_http",
    ),
    "modbus-hybrid": EnergySourceProfile(
        profile_name="modbus-hybrid",
        role="hybrid-inverter",
        connector_type="modbus",
    ),
    "command-json-hybrid": EnergySourceProfile(
        profile_name="command-json-hybrid",
        role="hybrid-inverter",
        connector_type="command_json",
    ),
    "huawei_ma_native_ap": EnergySourceProfile(
        profile_name="huawei_ma_native_ap",
        role="hybrid-inverter",
        connector_type="modbus",
        vendor_name="Huawei",
        platform="MA",
        access_mode="native_ap",
        firmware_class="local_ap_6607",
        default_host="192.168.200.1",
        default_port_candidates=(6607, 502),
        default_unit_id_candidates=(0, 1),
        read_support="supported",
        write_support="experimental",
        probe_required=True,
    ),
    "huawei_ma_native_lan": EnergySourceProfile(
        profile_name="huawei_ma_native_lan",
        role="hybrid-inverter",
        connector_type="modbus",
        vendor_name="Huawei",
        platform="MA",
        access_mode="native_lan",
        firmware_class="legacy_lan_open",
        default_port_candidates=(502, 6607),
        default_unit_id_candidates=(0, 1),
        read_support="supported",
        write_support="experimental",
        probe_required=True,
    ),
    "huawei_ma_sdongle": EnergySourceProfile(
        profile_name="huawei_ma_sdongle",
        role="hybrid-inverter",
        connector_type="modbus",
        vendor_name="Huawei",
        platform="MA",
        access_mode="sdongle",
        firmware_class="sdongle_third_party",
        default_port_candidates=(502, 6607),
        default_unit_id_candidates=(0, 1),
        read_support="supported",
        write_support="experimental",
        probe_required=True,
    ),
    "huawei_mb_native_ap": EnergySourceProfile(
        profile_name="huawei_mb_native_ap",
        role="hybrid-inverter",
        connector_type="modbus",
        vendor_name="Huawei",
        platform="MB",
        access_mode="native_ap",
        firmware_class="local_ap_6607",
        default_host="192.168.200.1",
        default_port_candidates=(6607, 502),
        default_unit_id_candidates=(0, 1),
        read_support="supported",
        write_support="experimental",
        probe_required=True,
    ),
    "huawei_mb_native_lan": EnergySourceProfile(
        profile_name="huawei_mb_native_lan",
        role="hybrid-inverter",
        connector_type="modbus",
        vendor_name="Huawei",
        platform="MB",
        access_mode="native_lan",
        firmware_class="legacy_lan_open",
        default_port_candidates=(502, 6607),
        default_unit_id_candidates=(0, 1),
        read_support="supported",
        write_support="experimental",
        probe_required=True,
    ),
    "huawei_mb_sdongle": EnergySourceProfile(
        profile_name="huawei_mb_sdongle",
        role="hybrid-inverter",
        connector_type="modbus",
        vendor_name="Huawei",
        platform="MB",
        access_mode="sdongle",
        firmware_class="sdongle_third_party",
        default_port_candidates=(502, 6607),
        default_unit_id_candidates=(0, 1),
        read_support="supported",
        write_support="experimental",
        probe_required=True,
    ),
    "huawei_smartlogger_modbus_tcp": EnergySourceProfile(
        profile_name="huawei_smartlogger_modbus_tcp",
        role="hybrid-inverter",
        connector_type="modbus",
        vendor_name="Huawei",
        platform="smartlogger",
        access_mode="smartlogger",
        firmware_class="smartlogger_502",
        default_port_candidates=(502,),
        default_unit_id_candidates=(0, 1),
        read_support="supported",
        write_support="experimental",
        probe_required=True,
    ),
}

_ALIASES = {
    "battery": "dbus-battery",
    "hybrid": "dbus-hybrid",
    "template-http": "template-http-hybrid",
    "http-hybrid": "template-http-hybrid",
    "modbus": "modbus-hybrid",
    "command-json": "command-json-hybrid",
    "helper": "command-json-hybrid",
    "huawei_l1_native_ap": "huawei_ma_native_ap",
    "huawei_lc0_native_ap": "huawei_ma_native_ap",
    "huawei_lb0_native_ap": "huawei_ma_native_ap",
    "huawei_m1_native_ap": "huawei_ma_native_ap",
    "huawei_l1_native_lan": "huawei_ma_native_lan",
    "huawei_lc0_native_lan": "huawei_ma_native_lan",
    "huawei_lb0_native_lan": "huawei_ma_native_lan",
    "huawei_m1_native_lan": "huawei_ma_native_lan",
    "huawei_l1_sdongle": "huawei_ma_sdongle",
    "huawei_lc0_sdongle": "huawei_ma_sdongle",
    "huawei_lb0_sdongle": "huawei_ma_sdongle",
    "huawei_m1_sdongle": "huawei_ma_sdongle",
    "huawei_map0_native_ap": "huawei_mb_native_ap",
    "huawei_mb0_native_ap": "huawei_mb_native_ap",
    "huawei_map0_native_lan": "huawei_mb_native_lan",
    "huawei_mb0_native_lan": "huawei_mb_native_lan",
    "huawei_map0_sdongle": "huawei_mb_sdongle",
    "huawei_mb0_sdongle": "huawei_mb_sdongle",
}


def available_energy_source_profiles() -> tuple[str, ...]:
    """Return canonical profile names in stable display order."""
    return tuple(_PROFILES)


def resolve_energy_source_profile(profile_name: object) -> EnergySourceProfile | None:
    """Return the normalized profile for a configured profile name or alias."""
    normalized = str(profile_name).strip().lower()
    if not normalized:
        return None
    canonical_name = _ALIASES.get(normalized, normalized)
    return _PROFILES.get(canonical_name)


def energy_source_profile_defaults(profile_name: object) -> Mapping[str, str]:
    """Expose one profile as a mapping suitable for config overlay logic."""
    profile = resolve_energy_source_profile(profile_name)
    if profile is None:
        return {}
    return {
        "Profile": profile.profile_name,
        "Role": profile.role,
        "Type": profile.connector_type,
        "ServicePrefix": profile.service_prefix,
        "SocPath": profile.soc_path,
        "BatteryPowerPath": profile.battery_power_path,
        "AcPowerPath": profile.ac_power_path,
        "PvPowerPath": profile.pv_power_path,
        "GridInteractionPath": profile.grid_interaction_path,
        "OperatingModePath": profile.operating_mode_path,
    }


def energy_source_profile_details(profile_name: object) -> Mapping[str, Any]:
    """Return stable outward-facing metadata for one configured profile."""
    profile = resolve_energy_source_profile(profile_name)
    if profile is None:
        return {}
    return {
        "profile_name": profile.profile_name,
        "vendor_name": profile.vendor_name,
        "platform": profile.platform,
        "access_mode": profile.access_mode,
        "firmware_class": profile.firmware_class,
        "connector_type": profile.connector_type,
        "role": profile.role,
        "default_host": profile.default_host,
        "default_port_candidates": list(profile.default_port_candidates),
        "default_unit_id_candidates": list(profile.default_unit_id_candidates),
        "read_support": profile.read_support,
        "write_support": profile.write_support,
        "probe_required": profile.probe_required,
    }


def energy_source_profile_probe_plan(
    profile_name: object,
    *,
    configured_host: object = "",
    configured_port: object = None,
    configured_unit_id: object = None,
) -> Mapping[str, Any]:
    """Return an effective probe plan for profiles that need field validation."""
    profile = resolve_energy_source_profile(profile_name)
    if profile is None:
        return {}
    host = str(configured_host).strip() or profile.default_host
    ports = _candidate_values(configured_port, profile.default_port_candidates)
    unit_ids = _candidate_values(configured_unit_id, profile.default_unit_id_candidates)
    return {
        "profile_name": profile.profile_name,
        "connector_type": profile.connector_type,
        "host": host,
        "port_candidates": ports,
        "unit_id_candidates": unit_ids,
        "probe_required": profile.probe_required,
    }


def _candidate_values(configured_value: object, defaults: tuple[int, ...]) -> list[int]:
    if isinstance(configured_value, bool):
        configured_value = None
    if isinstance(configured_value, int):
        return [int(configured_value)]
    if isinstance(configured_value, str) and configured_value.strip():
        try:
            return [int(configured_value.strip())]
        except ValueError:
            return list(defaults)
    return list(defaults)
