# SPDX-License-Identifier: GPL-3.0-or-later
"""Huawei recommendation helpers for energy probe output."""

from __future__ import annotations

from typing import Mapping

from .profiles import resolve_energy_source_profile
from .recommendation_schema import RECOMMENDATION_BUNDLE_SCHEMA_TYPE, RECOMMENDATION_BUNDLE_SCHEMA_VERSION


def _huawei_recommendation(
    profile_name: str,
    *,
    detection: Mapping[str, object],
    required_fields_ok: bool,
    meter_block_detected: bool,
    source_id: str,
) -> dict[str, object]:
    normalized_source_id = _normalized_source_id(source_id)
    detected = detection.get("detected")
    details = detection.get("profile_details")
    profile_details = details if isinstance(details, Mapping) else {}
    detected_mapping = detected if isinstance(detected, Mapping) else {}
    template_name = _recommended_huawei_template(profile_name)
    config_path = _recommended_huawei_config_path(template_name)
    notes = [
        "Huawei meter block detected" if meter_block_detected else "Huawei meter block not detected",
        "Configured Huawei energy fields responded successfully"
        if required_fields_ok
        else "One or more required Huawei energy fields did not respond",
    ]
    return {
        "status": "ready" if required_fields_ok else "incomplete",
        "bundle_schema_type": RECOMMENDATION_BUNDLE_SCHEMA_TYPE,
        "bundle_schema_version": RECOMMENDATION_BUNDLE_SCHEMA_VERSION,
        "suggested_profile": profile_name,
        "suggested_template": template_name,
        "suggested_config_path": config_path,
        "host": detected_mapping.get("host", ""),
        "port": detected_mapping.get("port"),
        "unit_id": detected_mapping.get("unit_id"),
        "platform": profile_details.get("platform", ""),
        "access_mode": profile_details.get("access_mode", ""),
        "meter_block_detected": meter_block_detected,
        "required_fields_ok": required_fields_ok,
        "capacity_required_for_weighted_soc": True,
        "capacity_config_key": f"AutoEnergySource.{normalized_source_id}.UsableCapacityWh",
        "capacity_hint": "Set usable battery capacity in Wh when you want weighted combined SOC.",
        "summary": _recommendation_summary(profile_name, detected_mapping, meter_block_detected, template_name),
        "config_snippet": _recommendation_config_snippet(
            profile_name,
            detected_mapping,
            template_name=template_name,
            config_path=config_path,
            source_id=normalized_source_id,
        ),
        "wizard_hint_block": _recommendation_wizard_hint_block(
            profile_name,
            detected_mapping,
            meter_block_detected=meter_block_detected,
            template_name=template_name,
            config_path=config_path,
            source_id=normalized_source_id,
        ),
        "notes": notes,
    }


def _normalized_source_id(source_id: str) -> str:
    cleaned = str(source_id).strip()
    return cleaned or "huawei"


def _recommended_huawei_template(profile_name: str) -> str:
    profile = resolve_energy_source_profile(profile_name)
    normalized = profile.profile_name if profile is not None else str(profile_name).strip().lower()
    if normalized.endswith("_unit1"):
        return "deploy/venus/template-energy-source-huawei-mb-unit1-modbus.ini"
    if normalized.endswith("_unit2"):
        return "deploy/venus/template-energy-source-huawei-mb-unit2-modbus.ini"
    if profile is not None and str(profile.platform).upper() == "MA":
        return "deploy/venus/template-energy-source-huawei-ma-modbus.ini"
    if normalized.startswith("huawei_ma_"):
        return "deploy/venus/template-energy-source-huawei-ma-modbus.ini"
    return "deploy/venus/template-energy-source-huawei-mb-modbus.ini"


def _recommended_huawei_config_path(template_name: str) -> str:
    filename = str(template_name).strip().rsplit("/", 1)[-1]
    if filename.startswith("template-energy-source-"):
        filename = filename[len("template-energy-source-") :]
    return f"/data/etc/{filename}"


def _recommendation_summary(
    profile_name: str,
    detected: Mapping[str, object],
    meter_block_detected: bool,
    template_name: str,
) -> str:
    host = str(detected.get("host", "") or "")
    port = detected.get("port")
    unit_id = detected.get("unit_id")
    location_text = f"host={host}" if host else "host=unknown"
    if port is not None:
        location_text += f", port={port}"
    if unit_id is not None:
        location_text += f", unit={unit_id}"
    meter_text = "present" if meter_block_detected else "missing"
    return f"Use profile {profile_name} with template {template_name}; {location_text}; meter block {meter_text}."


def _recommendation_config_snippet(
    profile_name: str,
    detected: Mapping[str, object],
    *,
    template_name: str,
    config_path: str,
    source_id: str,
) -> str:
    host = str(detected.get("host", "") or "").strip()
    port = _optional_int(detected.get("port"))
    unit_id = _optional_int(detected.get("unit_id"))
    lines = [
        "# Add the source id to AutoEnergySources in your main config.",
        f"# Example: AutoEnergySources=victron,{source_id}",
        f"AutoEnergySource.{source_id}.Profile={profile_name}",
        f"AutoEnergySource.{source_id}.ConfigPath={config_path}",
    ]
    if host:
        lines.append(f"AutoEnergySource.{source_id}.Host={host}")
    if port is not None:
        lines.append(f"AutoEnergySource.{source_id}.Port={port}")
    if unit_id is not None:
        lines.append(f"AutoEnergySource.{source_id}.UnitId={unit_id}")
    lines.extend(
        (
            "# Copy the matching starter template to the ConfigPath above:",
            f"# {template_name}",
            "# Optional but recommended when you want weighted combined SOC:",
            f"# AutoEnergySource.{source_id}.UsableCapacityWh=<set-me>",
        )
    )
    return "\n".join(lines)


def _recommendation_wizard_hint_block(
    profile_name: str,
    detected: Mapping[str, object],
    *,
    meter_block_detected: bool,
    template_name: str,
    config_path: str,
    source_id: str,
) -> str:
    host = str(detected.get("host", "") or "").strip() or "unknown"
    port_text = str(_optional_int(detected.get("port")) or "unknown")
    unit_text = str(_optional_int(detected.get("unit_id")) or "unknown")
    meter_text = "present" if meter_block_detected else "not detected"
    return "\n".join(
        (
            "Huawei recommendation",
            f"- profile: {profile_name}",
            f"- template: {template_name}",
            f"- config path: {config_path}",
            f"- host: {host}",
            f"- port: {port_text}",
            f"- unit id: {unit_text}",
            f"- meter block: {meter_text}",
            f"- source id: {source_id}",
            f"- capacity follow-up: set AutoEnergySource.{source_id}.UsableCapacityWh for weighted combined SOC",
            "- next step: copy the template, then paste the config snippet into the main config",
        )
    )


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
