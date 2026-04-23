# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from venus_evcharger.energy.recommendation_schema import (
    recommendation_bundle_manifest_path,
    validate_recommendation_bundle_manifest,
)


def existing_auto_energy_source_ids(config_path: Path) -> tuple[str, ...]:
    if not config_path.exists():
        return ()
    import configparser

    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")
    raw = ""
    if parser.defaults():
        raw = str(parser.defaults().get("AutoEnergySources", "")).strip()
    if not raw and parser.has_section("DEFAULT"):
        raw = str(parser["DEFAULT"].get("AutoEnergySources", "")).strip()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def optional_capacity_wh(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        capacity = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return capacity if capacity > 0.0 else None


def normalized_recommendation_prefixes(
    prefixes: str | tuple[str, ...] | list[str] | None,
) -> tuple[str, ...]:
    if prefixes is None:
        return ()
    items: tuple[str, ...]
    if isinstance(prefixes, str):
        items = (prefixes,)
    else:
        items = tuple(str(item) for item in prefixes)
    return tuple(item.strip() for item in items if item and item.strip())


def merged_recommendation_prefixes(
    *prefix_groups: str | tuple[str, ...] | list[str] | None,
) -> tuple[str, ...]:
    merged: list[str] = []
    for group in prefix_groups:
        for prefix in normalized_recommendation_prefixes(group):
            if prefix not in merged:
                merged.append(prefix)
    return tuple(merged)


def existing_auto_energy_assignments(config_path: Path) -> dict[str, str]:
    from .wizard_render import CasePreservingConfigParser

    if not config_path.exists():
        return {}
    parser = CasePreservingConfigParser()
    parser.read(config_path, encoding="utf-8")
    assignments: dict[str, str] = {}
    for key, value in parser.defaults().items():
        if key == "AutoUseCombinedBatterySoc" or key == "AutoEnergySources" or key.startswith("AutoEnergySource."):
            assignments[key] = str(value).strip()
    return assignments


def merge_energy_source_ids(existing_ids: tuple[str, ...], suggested_sources: tuple[dict[str, object], ...]) -> tuple[str, ...]:
    merged = list(existing_ids)
    for source in suggested_sources:
        source_id = str(source.get("source_id", "")).strip()
        if source_id and source_id not in merged:
            merged.append(source_id)
    return tuple(merged)


def existing_source_ids_from_assignments(assignments: dict[str, str]) -> tuple[str, ...]:
    source_ids = list(
        item.strip()
        for item in assignments.get("AutoEnergySources", "").split(",")
        if item.strip()
    )
    for key in assignments:
        if not key.startswith("AutoEnergySource."):
            continue
        source_id = key[len("AutoEnergySource.") :].split(".", 1)[0].strip()
        if source_id and source_id not in source_ids:
            source_ids.append(source_id)
    return tuple(source_ids)


def suggested_energy_sources_with_capacity(
    suggested_sources: tuple[dict[str, object], ...],
    capacity_wh: float | None,
) -> tuple[dict[str, object], ...]:
    capacity = optional_capacity_wh(capacity_wh)
    if capacity is None:
        return suggested_sources
    updated_sources: list[dict[str, object]] = []
    for source in suggested_sources:
        updated = dict(source)
        if str(updated.get("capacityConfigKey", "")).strip():
            updated["usableCapacityWh"] = capacity
        updated_sources.append(updated)
    return tuple(updated_sources)


def suggested_energy_sources_with_capacity_overrides(
    suggested_sources: tuple[dict[str, object], ...],
    capacity_overrides: dict[str, float],
) -> tuple[dict[str, object], ...]:
    if not capacity_overrides:
        return suggested_sources
    known_source_ids = {
        str(source.get("source_id", "")).strip()
        for source in suggested_sources
        if str(source.get("source_id", "")).strip()
    }
    unknown_source_ids = sorted(source_id for source_id in capacity_overrides if source_id not in known_source_ids)
    if unknown_source_ids:
        raise ValueError(
            "energy usable capacity overrides reference unknown source ids: "
            + ", ".join(unknown_source_ids)
        )
    updated_sources: list[dict[str, object]] = []
    for source in suggested_sources:
        updated = dict(source)
        source_id = str(updated.get("source_id", "")).strip()
        capacity = capacity_overrides.get(source_id)
        if capacity is not None and str(updated.get("capacityConfigKey", "")).strip():
            updated["usableCapacityWh"] = capacity
        updated_sources.append(updated)
    return tuple(updated_sources)


def validate_unique_suggested_energy_sources(
    suggested_sources: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for source in suggested_sources:
        source_id = str(source.get("source_id", "")).strip()
        if not source_id:
            continue
        seen[source_id] = seen.get(source_id, 0) + 1
        if seen[source_id] == 2:
            duplicates.append(source_id)
    if duplicates:
        raise ValueError(
            "multiple recommendation bundles resolved to the same source id: "
            + ", ".join(sorted(duplicates))
        )
    return suggested_sources


def energy_source_merge_lines(source: dict[str, object]) -> list[str]:
    source_id = str(source.get("source_id", "")).strip()
    if not source_id:
        return []
    mapping = (
        ("profile", "Profile"),
        ("configPath", "ConfigPath"),
        ("host", "Host"),
        ("port", "Port"),
        ("unitId", "UnitId"),
        ("usableCapacityWh", "UsableCapacityWh"),
    )
    lines: list[str] = []
    for source_key, config_key in mapping:
        value = source.get(source_key)
        if value in (None, ""):
            continue
        if isinstance(value, float):
            rendered_value = f"{value:g}"
        else:
            rendered_value = str(value)
        lines.append(f"AutoEnergySource.{source_id}.{config_key}={rendered_value}")
    return lines


def energy_source_capacity_follow_up(source: dict[str, object]) -> dict[str, object] | None:
    source_id = str(source.get("source_id", "")).strip()
    config_key = str(source.get("capacityConfigKey", "")).strip()
    hint = str(source.get("capacityHint", "")).strip()
    if not source_id or not config_key:
        return None
    configured_capacity = optional_capacity_wh(source.get("usableCapacityWh"))
    return {
        "source_id": source_id,
        "config_key": config_key,
        "placeholder": f"{configured_capacity:g}" if configured_capacity is not None else "<set-me>",
        "hint": hint or "Set usable battery capacity in Wh for weighted combined SOC.",
        "configured": configured_capacity is not None,
    }


def suggested_energy_assignments(
    existing_assignments: dict[str, str],
    suggested_sources: tuple[dict[str, object], ...],
) -> dict[str, str]:
    assignments = dict(existing_assignments)
    merged_ids = merge_energy_source_ids(existing_source_ids_from_assignments(assignments), suggested_sources)
    ordered: dict[str, str] = {
        "AutoUseCombinedBatterySoc": "1",
        "AutoEnergySources": ",".join(merged_ids),
    }
    for key, value in assignments.items():
        if key in ordered:
            continue
        ordered[key] = value
    for source in suggested_sources:
        for line in energy_source_merge_lines(source):
            key, value = line.split("=", 1)
            ordered[key] = value
    return ordered


def build_suggested_energy_merge(
    config_path: Path,
    suggested_sources: tuple[dict[str, object], ...],
) -> tuple[dict[str, object] | None, dict[str, str]]:
    if not suggested_sources:
        return None, {}
    existing_assignments = existing_auto_energy_assignments(config_path)
    existing_ids = existing_source_ids_from_assignments(existing_assignments)
    merged_ids = merge_energy_source_ids(existing_ids, suggested_sources)
    capacity_follow_up = tuple(
        item for item in (energy_source_capacity_follow_up(source) for source in suggested_sources) if item is not None
    )
    merge_lines = [
        "# Merge these lines into the main config when you want the suggested external energy source enabled.",
        "AutoUseCombinedBatterySoc=1",
        "AutoEnergySources=" + ",".join(merged_ids),
    ]
    for source in suggested_sources:
        merge_lines.extend(energy_source_merge_lines(source))
    if capacity_follow_up:
        merge_lines.append("# Optional but recommended for weighted combined SOC:")
        for item in capacity_follow_up:
            merge_lines.append(f"# {item['config_key']}={item['placeholder']}")
    merge_block = "\n".join(merge_lines)
    merge_payload: dict[str, object] = {
        "existing_source_ids": list(existing_ids),
        "merged_source_ids": list(merged_ids),
        "auto_use_combined_battery_soc": True,
        "helper_file": "wizard-auto-energy-merge.ini",
        "merge_block": merge_block,
        "capacity_follow_up": [dict(item) for item in capacity_follow_up],
        "applied_to_config": False,
    }
    return merge_payload, {"wizard-auto-energy-merge.ini": merge_block + "\n"}


def structured_energy_source_from_block(
    source_id: str,
    config_snippet: str,
) -> dict[str, object]:
    fields: dict[str, object] = {"source_id": source_id}
    prefix = f"AutoEnergySource.{source_id}."
    for raw_line in config_snippet.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or not line.startswith(prefix) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        field_name = key[len(prefix) :]
        parsed_value: object = value.strip()
        if field_name in {"Port", "UnitId"}:
            try:
                parsed_value = int(str(parsed_value))
            except ValueError:
                pass
        fields[field_name[0].lower() + field_name[1:]] = parsed_value
    return fields


def bundle_source_id(config_snippet: str, default_source_id: str) -> str:
    for raw_line in config_snippet.splitlines():
        line = raw_line.strip()
        if not line.startswith("AutoEnergySource.") or "=" not in line:
            continue
        remainder = line[len("AutoEnergySource.") :]
        source_id = remainder.split(".", 1)[0].strip()
        if source_id:
            return source_id
    return default_source_id


def bundle_target_names(source_id: str) -> dict[str, str]:
    normalized_source_id = source_id.strip() or "huawei"
    if normalized_source_id == "huawei":
        return {
            "ini": "wizard-huawei-energy.ini",
            "wizard": "wizard-huawei-energy.wizard.txt",
            "summary": "wizard-huawei-energy.summary.txt",
        }
    return {
        "ini": f"wizard-energy-{normalized_source_id}.ini",
        "wizard": f"wizard-energy-{normalized_source_id}.wizard.txt",
        "summary": f"wizard-energy-{normalized_source_id}.summary.txt",
    }


def bundle_labels(source_id: str) -> tuple[str, str]:
    normalized_source_id = source_id.strip() or "huawei"
    if normalized_source_id == "huawei":
        return (
            "External energy source integration",
            "Set usable battery capacity for weighted combined SOC",
        )
    return (
        f"External energy source integration ({normalized_source_id})",
        f"Set usable battery capacity for weighted combined SOC ({normalized_source_id})",
    )


def bundle_block_label(source_id: str) -> str:
    normalized_source_id = source_id.strip() or "huawei"
    if normalized_source_id == "huawei":
        return "External energy source"
    return f"External energy source ({normalized_source_id})"


def manual_review_union(
    existing_items: tuple[str, ...],
    new_items: tuple[str, ...],
) -> tuple[str, ...]:
    merged = list(existing_items)
    for item in new_items:
        if item not in merged:
            merged.append(item)
    return tuple(merged)


def huawei_bundle_files(
    prefix: str,
    *,
    source_id: str = "huawei",
) -> tuple[dict[str, str], tuple[str, ...], dict[str, str], tuple[dict[str, object], ...]]:
    base = Path(prefix)
    manifest_path = recommendation_bundle_manifest_path(str(base))
    if manifest_path.exists():
        manifest = validate_recommendation_bundle_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
        manifest_files = cast(dict[str, str], manifest["files"])
        source_paths = {
            "wizard-huawei-energy.ini": Path(manifest_files["config_snippet"]),
            "wizard-huawei-energy.wizard.txt": Path(manifest_files["wizard_hint"]),
            "wizard-huawei-energy.summary.txt": Path(manifest_files["summary"]),
        }
        source_id = str(manifest["source_id"])
    else:
        source_paths = {
            "wizard-huawei-energy.ini": Path(str(base) + ".ini"),
            "wizard-huawei-energy.wizard.txt": Path(str(base) + ".wizard.txt"),
            "wizard-huawei-energy.summary.txt": Path(str(base) + ".summary.txt"),
        }
    missing = [str(path) for path in source_paths.values() if not path.exists()]
    if missing:
        raise ValueError("Huawei recommendation bundle is incomplete: " + ", ".join(missing))
    source_contents = {
        "ini": source_paths["wizard-huawei-energy.ini"].read_text(encoding="utf-8"),
        "wizard": source_paths["wizard-huawei-energy.wizard.txt"].read_text(encoding="utf-8"),
        "summary": source_paths["wizard-huawei-energy.summary.txt"].read_text(encoding="utf-8"),
    }
    resolved_source_id = bundle_source_id(source_contents["ini"], source_id)
    target_names = bundle_target_names(resolved_source_id)
    rendered_files = {
        target_names["ini"]: source_contents["ini"],
        target_names["wizard"]: source_contents["wizard"],
        target_names["summary"]: source_contents["summary"],
    }
    structured_source = structured_energy_source_from_block(resolved_source_id, source_contents["ini"])
    structured_source["capacityRequiredForWeightedSoc"] = True
    structured_source["capacityConfigKey"] = f"AutoEnergySource.{resolved_source_id}.UsableCapacityWh"
    structured_source["capacityHint"] = "Set usable battery capacity in Wh for weighted combined SOC."
    review_items = bundle_labels(resolved_source_id)
    block_label = bundle_block_label(resolved_source_id)
    return (
        rendered_files,
        review_items,
        {block_label: source_contents["ini"]},
        (structured_source,),
    )
