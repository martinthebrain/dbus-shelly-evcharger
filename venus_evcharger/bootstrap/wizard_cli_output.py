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
