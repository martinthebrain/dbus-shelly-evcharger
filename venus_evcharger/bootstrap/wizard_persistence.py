# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistence helpers for wizard result and audit files."""

from __future__ import annotations

import json
from pathlib import Path


def result_path(target: Path) -> Path:
    return target.with_name(f"{target.name}.wizard-result.json")


def audit_path(target: Path) -> Path:
    return target.with_name(f"{target.name}.wizard-audit.jsonl")


def topology_summary_path(target: Path) -> Path:
    return target.with_name(f"{target.name}.wizard-topology.txt")


def inventory_path(target: Path) -> Path:
    return target.with_name(f"{target.name}.wizard-inventory.ini")


def _summary_value(payload: dict[str, object], key: str, fallback: str) -> str:
    value = payload.get(key)
    return fallback if value in (None, "") else str(value)


def _role_host_lines(role_hosts: object) -> list[str]:
    if isinstance(role_hosts, dict) and role_hosts:
        return [f"  - {role}: {value}" for role, value in sorted(role_hosts.items())]
    return ["  - none"]


def _optional_scalar_lines(payload: dict[str, object]) -> list[str]:
    validation = payload.get("validation")
    if not isinstance(validation, dict):
        return []
    return [f"resolved_roles: {validation.get('resolved_roles')}"]


def _optional_live_check_lines(payload: dict[str, object]) -> list[str]:
    live_check = payload.get("live_check")
    if not isinstance(live_check, dict):
        return []
    return [f"live_check_ok: {live_check.get('ok')}"]


def _warning_lines(payload: dict[str, object]) -> list[str]:
    warnings = payload.get("warnings")
    if not isinstance(warnings, list) or not warnings:
        return []
    return ["warnings:", *(f"  - {item}" for item in warnings)]


def _summary_optional_lines(payload: dict[str, object]) -> list[str]:
    return [
        *_optional_scalar_lines(payload),
        *_optional_live_check_lines(payload),
        *_inventory_lines(payload),
        *_suggested_energy_source_lines(payload),
        *_suggested_energy_merge_lines(payload),
        *_suggested_block_lines(payload),
        *_warning_lines(payload),
    ]


def _inventory_lines(payload: dict[str, object]) -> list[str]:
    inventory = payload.get("device_inventory")
    if not isinstance(inventory, dict) or not inventory:
        return []
    lines = [_inventory_counts_line(inventory)]
    inventory_file = payload.get("inventory_path")
    if isinstance(inventory_file, str) and inventory_file:
        lines.append(f"inventory_path: {inventory_file}")
    return lines


def _inventory_counts_line(inventory: dict[str, object]) -> str:
    """Return one summary line with rendered inventory counts."""
    return (
        "inventory_counts: "
        f"profiles={_inventory_list_count(inventory, 'profiles')} "
        f"devices={_inventory_list_count(inventory, 'devices')} "
        f"bindings={_inventory_list_count(inventory, 'bindings')}"
    )


def _inventory_list_count(inventory: dict[str, object], key: str) -> int:
    """Return the length of one list value in the inventory payload."""
    value = inventory.get(key)
    return len(value) if isinstance(value, list) else 0


def _suggested_block_lines(payload: dict[str, object]) -> list[str]:
    blocks = payload.get("suggested_blocks")
    if not isinstance(blocks, dict) or not blocks:
        return []
    return [f"suggested_blocks: {', '.join(sorted(str(key) for key in blocks))}"]


def _suggested_energy_source_lines(payload: dict[str, object]) -> list[str]:
    sources = payload.get("suggested_energy_sources")
    if not isinstance(sources, list) or not sources:
        return []
    source_ids = _suggested_energy_source_ids(sources)
    return [f"suggested_energy_sources: {', '.join(source_ids)}"] if source_ids else []


def _suggested_energy_merge_lines(payload: dict[str, object]) -> list[str]:
    merge = payload.get("suggested_energy_merge")
    if not isinstance(merge, dict):
        return []
    merged_source_ids = _merged_source_ids(merge)
    if not merged_source_ids:
        return []
    lines = [f"suggested_energy_merge: {','.join(str(item) for item in merged_source_ids)}"]
    applied_line = _suggested_energy_merge_applied_line(merge)
    if applied_line is not None:
        lines.append(applied_line)
    return lines


def _suggested_energy_source_ids(sources: list[object]) -> list[str]:
    source_ids: list[str] = []
    for item in sources:
        if isinstance(item, dict):
            source_ids.append(str(item.get("source_id", "unknown")))
    return source_ids


def _merged_source_ids(merge: dict[str, object]) -> list[object]:
    merged_source_ids = merge.get("merged_source_ids")
    if isinstance(merged_source_ids, list):
        return merged_source_ids
    return []


def _suggested_energy_merge_applied_line(merge: dict[str, object]) -> str | None:
    if "applied_to_config" not in merge:
        return None
    return f"suggested_energy_merge_applied: {bool(merge.get('applied_to_config'))}"


def _topology_summary_text(payload: dict[str, object]) -> str:
    lines = [
        f"config_path: {_summary_value(payload, 'config_path', '')}",
        f"setup: {_summary_value(payload, 'profile', '')}",
        f"topology_preset: {_summary_value(payload, 'topology_preset', 'n/a')}",
        f"charger_backend: {_summary_value(payload, 'charger_backend', 'none')}",
        f"charger_preset: {_summary_value(payload, 'charger_preset', 'n/a')}",
        f"policy_mode: {_summary_value(payload, 'policy_mode', '')}",
        f"transport_kind: {_summary_value(payload, 'transport_kind', 'n/a')}",
        "role_endpoints:",
    ]
    lines.extend(_role_host_lines(payload.get("role_hosts")))
    lines.extend(_summary_optional_lines(payload))
    return "\n".join(lines) + "\n"


def persist_wizard_state(config_path: Path, payload: dict[str, object]) -> tuple[str, str, str]:
    current_result_path = result_path(config_path)
    current_audit_path = audit_path(config_path)
    current_topology_summary_path = topology_summary_path(config_path)
    persisted_payload = dict(payload)
    persisted_payload["result_path"] = str(current_result_path)
    persisted_payload["audit_path"] = str(current_audit_path)
    persisted_payload["topology_summary_path"] = str(current_topology_summary_path)
    current_result_path.write_text(json.dumps(persisted_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with current_audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(persisted_payload, sort_keys=True) + "\n")
    current_topology_summary_path.write_text(_topology_summary_text(persisted_payload), encoding="utf-8")
    return str(current_result_path), str(current_audit_path), str(current_topology_summary_path)


def persist_inventory_sidecar(config_path: Path, content: str) -> str:
    current_inventory_path = inventory_path(config_path)
    current_inventory_path.write_text(content, encoding="utf-8")
    return str(current_inventory_path)
