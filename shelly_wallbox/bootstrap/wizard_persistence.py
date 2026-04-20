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
        *_warning_lines(payload),
    ]


def _topology_summary_text(payload: dict[str, object]) -> str:
    lines = [
        f"config_path: {_summary_value(payload, 'config_path', '')}",
        f"profile: {_summary_value(payload, 'profile', '')}",
        f"split_preset: {_summary_value(payload, 'split_preset', 'n/a')}",
        f"charger_backend: {_summary_value(payload, 'charger_backend', 'none')}",
        f"policy_mode: {_summary_value(payload, 'policy_mode', '')}",
        f"transport_kind: {_summary_value(payload, 'transport_kind', 'n/a')}",
        "role_hosts:",
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
