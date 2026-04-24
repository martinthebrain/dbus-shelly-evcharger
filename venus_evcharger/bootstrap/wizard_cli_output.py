# SPDX-License-Identifier: GPL-3.0-or-later
"""Text rendering helpers for wizard results."""

from __future__ import annotations

from venus_evcharger.bootstrap.wizard_models import WizardResult


def result_text(result: WizardResult) -> str:
    lines = [
        *_result_header_lines(result),
        *_result_role_host_lines(result),
        *_result_generated_file_lines(result),
        *_result_artifact_lines(result),
        *_result_warning_lines(result),
        *_result_live_check_lines(result),
        *_result_suggested_energy_source_lines(result),
        *_result_suggested_energy_merge_lines(result),
        *_result_suggested_block_lines(result),
        "Manual review:",
        *(f"  - {item}" for item in result.manual_review),
    ]
    return "\n".join(lines)


def _result_header_lines(result: WizardResult) -> list[str]:
    return [
        f"Config written to: {result.config_path}" if not result.dry_run else f"Config preview for: {result.config_path}",
        _optional_header_line("Imported defaults", result.imported_from, "none"),
        _optional_header_line("Selected profile", result.profile, ""),
        _optional_header_line("Selected split preset", result.split_preset, "n/a"),
        _optional_header_line("Selected charger backend", result.charger_backend, "none"),
        _optional_header_line("Transport", result.transport_kind, "n/a"),
        "Validation: ok",
        f"Live connectivity: {_live_connectivity_label(result)}",
    ]


def _optional_header_line(label: str, value: object, fallback: str) -> str:
    return f"{label}: {value if value is not None else fallback}"


def _live_connectivity_label(result: WizardResult) -> str:
    if result.live_check is None:
        return "not run"
    return "ok" if result.live_check.get("ok") else "check reported issues"


def _result_role_host_lines(result: WizardResult) -> list[str]:
    lines = ["Role hosts:"]
    if result.role_hosts:
        lines.extend(f"  - {role}: {value}" for role, value in sorted(result.role_hosts.items()))
    else:
        lines.append("  - none")
    return lines


def _result_generated_file_lines(result: WizardResult) -> list[str]:
    lines = ["Generated files:", *(f"  - {item}" for item in result.generated_files)]
    if result.backup_files:
        lines.extend(["Backup files:", *(f"  - {item}" for item in result.backup_files)])
    return lines


def _result_artifact_lines(result: WizardResult) -> list[str]:
    lines: list[str] = []
    if result.result_path is not None:
        lines.append(f"Wizard result: {result.result_path}")
    if result.audit_path is not None:
        lines.append(f"Wizard audit: {result.audit_path}")
    if result.topology_summary_path is not None:
        lines.append(f"Topology summary: {result.topology_summary_path}")
    return lines


def _result_warning_lines(result: WizardResult) -> list[str]:
    if not result.warnings:
        return []
    return ["Warnings:", *(f"  - {item}" for item in result.warnings)]


def _result_live_check_lines(result: WizardResult) -> list[str]:
    if result.live_check is None:
        return []
    lines = ["Live connectivity roles:"]
    roles = result.live_check.get("roles")
    if isinstance(roles, dict) and roles:
        lines.extend(filter(None, (_result_live_check_role_line(role, payload) for role, payload in sorted(roles.items()))))
    return lines


def _result_live_check_role_line(role: str, payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    status = payload.get("status", "unknown")
    detail = payload.get("error") or payload.get("reason") or "ok"
    return f"  - {role}: {status} ({detail})"


def _result_suggested_block_lines(result: WizardResult) -> list[str]:
    if not result.suggested_blocks:
        return []
    lines = ["Suggested config blocks:"]
    for label, content in sorted(result.suggested_blocks.items()):
        lines.append(f"  - {label}:")
        lines.extend(f"    {line}" if line else "" for line in content.strip().splitlines())
    return lines


def _suggested_energy_source_summary(source: dict[str, object]) -> str:
    source_id = str(source.get("source_id", "unknown"))
    profile = str(source.get("profile", ""))
    config_path = str(source.get("configPath", source.get("config_path", "")))
    host = str(source.get("host", ""))
    port = source.get("port")
    unit_id = source.get("unitId", source.get("unit_id"))
    summary = f"  - {source_id}: profile={profile}"
    if config_path:
        summary += f", config={config_path}"
    if host:
        summary += f", host={host}"
    if port not in (None, ""):
        summary += f", port={port}"
    if unit_id not in (None, ""):
        summary += f", unit_id={unit_id}"
    return summary


def _result_suggested_energy_source_lines(result: WizardResult) -> list[str]:
    if not result.suggested_energy_sources:
        return []
    lines = ["Suggested energy sources:"]
    for source in result.suggested_energy_sources:
        lines.append(_suggested_energy_source_summary(source))
        capacity_key = str(source.get("capacityConfigKey", ""))
        if capacity_key:
            lines.append(f"    capacity follow-up: {capacity_key}=<set-me>")
    return lines


def _suggested_energy_merge_capacity_lines(merge: dict[str, object]) -> list[str]:
    capacity_follow_up = merge.get("capacity_follow_up")
    if not isinstance(capacity_follow_up, list) or not capacity_follow_up:
        return []
    lines = ["  - capacity follow-up:"]
    for item in capacity_follow_up:
        config_line = _suggested_energy_merge_capacity_line(item)
        if config_line is not None:
            lines.append(config_line)
    return lines


def _suggested_energy_merge_capacity_line(item: object) -> str | None:
    if not isinstance(item, dict):
        return None
    config_key = str(item.get("config_key", "")).strip()
    if not config_key:
        return None
    return f"    {config_key}=<set-me>"


def _merged_source_ids_line(merge: dict[str, object]) -> str | None:
    merged_source_ids = merge.get("merged_source_ids")
    if not isinstance(merged_source_ids, list) or not merged_source_ids:
        return None
    return "  - merged source ids: " + ",".join(str(item) for item in merged_source_ids)


def _helper_file_line(merge: dict[str, object]) -> str | None:
    helper_file = merge.get("helper_file")
    if isinstance(helper_file, str) and helper_file:
        return f"  - helper file: {helper_file}"
    return None


def _suggested_energy_merge_block_lines(block: object) -> list[str]:
    if not isinstance(block, str) or not block.strip():
        return []
    return ["  - merge block:", *(f"    {line}" if line else "" for line in block.strip().splitlines())]


def _suggested_energy_merge_header_lines(merge: dict[str, object]) -> list[str]:
    applied_to_config = bool(merge.get("applied_to_config"))
    lines = ["Suggested AutoEnergy merge:"]
    merged_source_ids_line = _merged_source_ids_line(merge)
    if merged_source_ids_line is not None:
        lines.append(merged_source_ids_line)
    helper_file_line = _helper_file_line(merge)
    if helper_file_line is not None:
        lines.append(helper_file_line)
    lines.append(f"  - applied to main config: {'yes' if applied_to_config else 'no'}")
    return lines


def _result_suggested_energy_merge_lines(result: WizardResult) -> list[str]:
    merge = result.suggested_energy_merge
    if not isinstance(merge, dict):
        return []
    return [
        *_suggested_energy_merge_header_lines(merge),
        *_suggested_energy_merge_capacity_lines(merge),
        *_suggested_energy_merge_block_lines(merge.get("merge_block")),
    ]
