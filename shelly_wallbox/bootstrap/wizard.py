# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional setup wizard for initial wallbox configuration."""

from __future__ import annotations

import argparse
import configparser
import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, cast

from shelly_wallbox.backend.config import load_backend_selection
from shelly_wallbox.backend.probe import (
    probe_meter_backend,
    probe_switch_backend,
    read_charger_backend,
    validate_wallbox_config,
)
from shelly_wallbox.bootstrap.wizard_cli import build_answers, build_parser, prompt_yes_no
from shelly_wallbox.bootstrap.wizard_cli_output import result_text
from shelly_wallbox.bootstrap.wizard_guidance import compatibility_warnings, probe_roles
from shelly_wallbox.bootstrap.wizard_layouts import generated_adapter_files
from shelly_wallbox.bootstrap.wizard_models import WizardAnswers, WizardResult, WizardTransportKind
from shelly_wallbox.bootstrap.wizard_persistence import persist_wizard_state
from shelly_wallbox.bootstrap.wizard_review import manual_review_items
from shelly_wallbox.bootstrap.wizard_support import host_from_input, transport_summary
from shelly_wallbox.core.common_values import normalize_phase


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_template_path() -> Path:
    return _repo_root() / "deploy" / "venus" / "config.shelly_wallbox.ini"

def default_config_path() -> Path:
    return default_template_path()


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _mode_value(policy_mode: str) -> str:
    return {"manual": "0", "auto": "1", "scheduled": "2"}[policy_mode]


def _replace_assignment(text: str, key: str, value: str) -> str:
    replaced = False
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith(f"{key}="):
            lines.append(f"{key}={value}")
            replaced = True
            continue
        lines.append(line)
    if not replaced:
        raise ValueError(f"Config template is missing required key '{key}'")
    return "\n".join(lines) + "\n"


def _remove_section(text: str, section_name: str) -> str:
    def is_header(line: str) -> bool:
        return line.startswith("[") and line.endswith("]")

    result: list[str] = []
    in_section = False
    for line in text.splitlines():
        if is_header(line):
            in_section = line == f"[{section_name}]"
            if in_section:
                continue
        if in_section:
            continue
        result.append(line)
    return "\n".join(result).rstrip() + "\n"


def _append_backends(text: str, lines: list[str]) -> str:
    if not lines:
        return _remove_section(text, "Backends")
    result = _remove_section(text, "Backends").rstrip()
    return f"{result}\n\n[Backends]\n" + "\n".join(lines) + "\n"


def render_wizard_config(template_text: str, answers: WizardAnswers) -> tuple[str, dict[str, str], dict[str, str]]:
    config_text = _replace_assignment(template_text, "Host", host_from_input(answers.host_input))
    config_text = _replace_assignment(config_text, "DeviceInstance", str(int(answers.device_instance)))
    config_text = _replace_assignment(config_text, "DigestAuth", "1" if answers.digest_auth else "0")
    config_text = _replace_assignment(config_text, "Username", answers.username.strip())
    config_text = _replace_assignment(config_text, "Password", answers.password.strip())
    config_text = _replace_assignment(config_text, "Phase", normalize_phase(answers.phase))
    config_text = _replace_assignment(config_text, "Mode", _mode_value(answers.policy_mode))
    config_text = _replace_optional_assignment(config_text, "AutoStartSurplusWatts", answers.auto_start_surplus_watts)
    config_text = _replace_optional_assignment(config_text, "AutoStopSurplusWatts", answers.auto_stop_surplus_watts)
    config_text = _replace_optional_assignment(config_text, "AutoMinSoc", answers.auto_min_soc)
    config_text = _replace_optional_assignment(config_text, "AutoResumeSoc", answers.auto_resume_soc)
    config_text = _replace_optional_assignment(config_text, "AutoScheduledEnabledDays", answers.scheduled_enabled_days)
    config_text = _replace_optional_assignment(config_text, "AutoScheduledLatestEndTime", answers.scheduled_latest_end_time)
    config_text = _replace_optional_assignment(config_text, "AutoScheduledNightCurrentAmps", answers.scheduled_night_current_amps)
    backend_lines, adapter_files, role_hosts = generated_adapter_files(
        profile=answers.profile,
        primary_host_input=answers.host_input,
        meter_host_input=answers.meter_host_input,
        switch_host_input=answers.switch_host_input,
        charger_host_input=answers.charger_host_input,
        split_preset=answers.split_preset,
        charger_backend=answers.charger_backend,
        request_timeout_seconds=answers.request_timeout_seconds,
        switch_group_supported_phase_selections=answers.switch_group_supported_phase_selections,
        transport_kind=answers.transport_kind,
        transport_host=answers.transport_host,
        transport_port=answers.transport_port,
        transport_device=answers.transport_device,
        transport_unit_id=answers.transport_unit_id,
    )
    return _append_backends(config_text, backend_lines), adapter_files, role_hosts


def _replace_optional_assignment(text: str, key: str, value: object) -> str:
    if value is None:
        return text
    if isinstance(value, float):
        rendered = f"{value:g}"
    else:
        rendered = str(value)
    return _replace_assignment(text, key, rendered)


def _materialized_config_text(config_text: str, output_dir: Path, adapter_files: dict[str, str]) -> str:
    rendered = config_text
    for relative_path in sorted(adapter_files):
        rendered = rendered.replace(f"={relative_path}\n", f"={output_dir / relative_path}\n")
    return rendered


def _validate_rendered_setup(config_text: str, adapter_files: dict[str, str], config_name: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        materialized_text = _materialized_config_text(config_text, temp_path, adapter_files)
        main_path = temp_path / config_name
        main_path.write_text(materialized_text, encoding="utf-8")
        for relative_path, content in adapter_files.items():
            (temp_path / relative_path).write_text(content, encoding="utf-8")
        return validate_wallbox_config(str(main_path))


def _live_connectivity_payload(main_path: Path, selected_roles: tuple[str, ...] | None) -> dict[str, object]:
    parser = configparser.ConfigParser()
    parser.read(main_path, encoding="utf-8")
    selection = load_backend_selection(parser)
    role_results: dict[str, dict[str, object]] = {}
    ok = True

    def run_role(
        role: str,
        config_path: Path | None,
        probe: Callable[[str], dict[str, object]],
    ) -> None:
        nonlocal ok
        if selected_roles is not None and role not in selected_roles:
            role_results[role] = {"status": "skipped", "reason": "not requested"}
            return
        if config_path is None:
            role_results[role] = {"status": "skipped", "reason": "not configured"}
            return
        resolved_path = config_path if config_path.is_absolute() else main_path.parent / config_path
        try:
            role_results[role] = {"status": "ok", "payload": probe(str(resolved_path))}
        except Exception as exc:
            ok = False
            role_results[role] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    run_role("meter", selection.meter_config_path, probe_meter_backend)
    run_role("switch", selection.switch_config_path, probe_switch_backend)
    run_role("charger", selection.charger_config_path, read_charger_backend)
    checked_roles = tuple(role for role, payload in role_results.items() if payload.get("status") != "skipped")
    return {
        "ok": ok,
        "checked_roles": checked_roles,
        "roles": role_results,
    }


def _live_check_rendered_setup(
    config_text: str,
    adapter_files: dict[str, str],
    config_name: str,
    selected_roles: tuple[str, ...] | None,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        materialized_text = _materialized_config_text(config_text, temp_path, adapter_files)
        main_path = temp_path / config_name
        main_path.write_text(materialized_text, encoding="utf-8")
        for relative_path, content in adapter_files.items():
            (temp_path / relative_path).write_text(content, encoding="utf-8")
        return _live_connectivity_payload(main_path, selected_roles)


def _answer_defaults(answers: WizardAnswers) -> dict[str, object]:
    return {
        "profile": answers.profile,
        "host_input": answers.host_input,
        "meter_host_input": answers.meter_host_input,
        "switch_host_input": answers.switch_host_input,
        "charger_host_input": answers.charger_host_input,
        "device_instance": answers.device_instance,
        "phase": answers.phase,
        "policy_mode": answers.policy_mode,
        "digest_auth": answers.digest_auth,
        "username": answers.username,
        "password_present": bool(answers.password),
        "split_preset": answers.split_preset,
        "charger_backend": answers.charger_backend,
        "request_timeout_seconds": answers.request_timeout_seconds,
        "switch_group_supported_phase_selections": answers.switch_group_supported_phase_selections,
        "auto_start_surplus_watts": answers.auto_start_surplus_watts,
        "auto_stop_surplus_watts": answers.auto_stop_surplus_watts,
        "auto_min_soc": answers.auto_min_soc,
        "auto_resume_soc": answers.auto_resume_soc,
        "scheduled_enabled_days": answers.scheduled_enabled_days,
        "scheduled_latest_end_time": answers.scheduled_latest_end_time,
        "scheduled_night_current_amps": answers.scheduled_night_current_amps,
        "transport_kind": answers.transport_kind,
        "transport_host": answers.transport_host,
        "transport_port": answers.transport_port,
        "transport_device": answers.transport_device,
        "transport_unit_id": answers.transport_unit_id,
    }


def _backup_path(target: Path) -> Path:
    return target.with_name(f"{target.name}.wizard-backup-{_timestamp()}")


def _write_with_backup(target: Path, content: str) -> str | None:
    backup_path: str | None = None
    if target.exists():
        destination = _backup_path(target)
        shutil.copy2(target, destination)
        backup_path = str(destination)
    target.write_text(content, encoding="utf-8")
    return backup_path


def _preview_result(
    answers: WizardAnswers,
    config_path: Path,
    created_at: str,
    validation: dict[str, object],
    live_check_payload: dict[str, object] | None,
    role_hosts: dict[str, str],
    generated_files: tuple[str, ...],
    warnings: tuple[str, ...],
    imported_from: str | None,
    dry_run: bool,
) -> WizardResult:
    return WizardResult(
        created_at=created_at,
        config_path=str(config_path),
        imported_from=imported_from,
        profile=answers.profile,
        policy_mode=answers.policy_mode,
        split_preset=answers.split_preset,
        charger_backend=answers.charger_backend,
        transport_kind=cast(WizardTransportKind | None, transport_summary(answers.charger_backend, answers.transport_kind)),
        role_hosts=role_hosts,
        validation=validation,
        live_check=live_check_payload,
        warnings=warnings,
        answer_defaults=_answer_defaults(answers),
        generated_files=generated_files,
        backup_files=tuple(),
        result_path=None,
        audit_path=None,
        topology_summary_path=None,
        manual_review=manual_review_items(
            answers.profile,
            answers.policy_mode,
            answers.charger_backend,
            answers.transport_kind,
            answers.split_preset,
        ),
        dry_run=dry_run,
    )


def _write_generated_files(config_path: Path, materialized_text: str, adapter_files: dict[str, str]) -> list[str]:
    backup_files: list[str] = []
    backup_path = _write_with_backup(config_path, materialized_text)
    if backup_path is not None:
        backup_files.append(backup_path)
    for relative_path, content in sorted(adapter_files.items()):
        backup_path = _write_with_backup(config_path.parent / relative_path, content)
        if backup_path is not None:
            backup_files.append(backup_path)
    return backup_files


def _persisted_result(result: WizardResult) -> WizardResult:
    result_path, audit_path, topology_summary_path = persist_wizard_state(Path(result.config_path), result.as_dict())
    return WizardResult(
        created_at=result.created_at,
        config_path=result.config_path,
        imported_from=result.imported_from,
        profile=result.profile,
        policy_mode=result.policy_mode,
        split_preset=result.split_preset,
        charger_backend=result.charger_backend,
        transport_kind=result.transport_kind,
        role_hosts=result.role_hosts,
        validation=result.validation,
        live_check=result.live_check,
        warnings=result.warnings,
        answer_defaults=result.answer_defaults,
        generated_files=result.generated_files,
        backup_files=result.backup_files,
        result_path=result_path,
        audit_path=audit_path,
        topology_summary_path=topology_summary_path,
        manual_review=result.manual_review,
        dry_run=result.dry_run,
    )


def configure_wallbox(
    answers: WizardAnswers,
    *,
    config_path: Path,
    template_path: Path,
    dry_run: bool = False,
    live_check: bool = False,
    selected_probe_roles: tuple[str, ...] | None = None,
    imported_from: str | None = None,
) -> WizardResult:
    template_text = template_path.read_text(encoding="utf-8")
    created_at = datetime.now().isoformat(timespec="seconds")
    config_text, adapter_files, role_hosts = render_wizard_config(template_text, answers)
    validation = _validate_rendered_setup(config_text, adapter_files, config_path.name)
    live_check_payload = (
        _live_check_rendered_setup(config_text, adapter_files, config_path.name, selected_probe_roles) if live_check else None
    )
    materialized_text = _materialized_config_text(config_text, config_path.parent, adapter_files)
    generated_files = (config_path.name,) + tuple(sorted(adapter_files))
    backup_files: list[str] = []
    warnings = compatibility_warnings(
        profile=answers.profile,
        split_preset=answers.split_preset,
        charger_backend=answers.charger_backend,
        primary_host_input=answers.host_input,
        role_hosts=role_hosts,
        transport_kind=answers.transport_kind,
        transport_host=answers.transport_host,
        switch_group_supported_phase_selections=answers.switch_group_supported_phase_selections,
    )
    result = _preview_result(
        answers,
        config_path,
        created_at,
        validation,
        live_check_payload,
        role_hosts,
        generated_files,
        warnings,
        imported_from,
        dry_run,
    )
    if dry_run:
        return result
    config_path.parent.mkdir(parents=True, exist_ok=True)
    backup_files.extend(_write_generated_files(config_path, materialized_text, adapter_files))
    result = WizardResult(
        created_at=result.created_at,
        config_path=result.config_path,
        imported_from=result.imported_from,
        profile=result.profile,
        policy_mode=result.policy_mode,
        split_preset=result.split_preset,
        charger_backend=result.charger_backend,
        transport_kind=result.transport_kind,
        role_hosts=result.role_hosts,
        validation=result.validation,
        live_check=result.live_check,
        warnings=result.warnings,
        answer_defaults=result.answer_defaults,
        generated_files=result.generated_files,
        backup_files=tuple(backup_files),
        result_path=None,
        audit_path=None,
        topology_summary_path=None,
        manual_review=result.manual_review,
        dry_run=result.dry_run,
    )
    return _persisted_result(result)


def _existing_output_paths(config_path: Path, generated_files: tuple[str, ...]) -> tuple[str, ...]:
    existing = []
    for relative_path in generated_files:
        candidate = config_path.parent / relative_path
        if candidate.exists():
            existing.append(str(candidate))
    return tuple(existing)


def _confirm_write(namespace: object, preview: WizardResult, existing_files: tuple[str, ...]) -> None:
    if _skip_write_confirmation(namespace, existing_files):
        return
    if not _interactive_write_confirmed(preview, existing_files):
        raise ValueError("Wizard write cancelled by user")


def _non_interactive_write_allowed(namespace: object, existing_files: tuple[str, ...]) -> bool:
    if not getattr(namespace, "non_interactive"):
        return False
    if existing_files and not getattr(namespace, "force"):
        raise ValueError(
            "Refusing to overwrite existing files in --non-interactive mode without --force: "
            + ", ".join(existing_files)
        )
    return True


def _skip_write_confirmation(namespace: object, existing_files: tuple[str, ...]) -> bool:
    if getattr(namespace, "dry_run"):
        return True
    if _non_interactive_write_allowed(namespace, existing_files):
        return True
    return bool(getattr(namespace, "yes"))


def _interactive_write_confirmed(preview: WizardResult, existing_files: tuple[str, ...]) -> bool:
    print(result_text(preview))
    prompt = "Write config now and create backups of existing files?" if existing_files else "Write config now?"
    return prompt_yes_no(prompt, not bool(existing_files))


def _run_wizard(namespace: argparse.Namespace) -> WizardResult:
    answers, imported = build_answers(namespace)
    imported_from = imported.imported_from if imported is not None else None
    live_check = _resolve_live_check(namespace)
    selected_probe_roles = probe_roles(namespace)
    preview = configure_wallbox(
        answers,
        config_path=Path(namespace.config_path),
        template_path=Path(namespace.template_path),
        dry_run=True,
        live_check=live_check,
        selected_probe_roles=selected_probe_roles,
        imported_from=imported_from,
    )
    existing_files = _existing_output_paths(Path(namespace.config_path), preview.generated_files)
    _confirm_write(namespace, preview, existing_files)
    if namespace.dry_run:
        return preview
    return configure_wallbox(
        answers,
        config_path=Path(namespace.config_path),
        template_path=Path(namespace.template_path),
        dry_run=False,
        live_check=live_check,
        selected_probe_roles=selected_probe_roles,
        imported_from=imported_from,
    )


def _resolve_live_check(namespace: argparse.Namespace) -> bool:
    if namespace.live_check or getattr(namespace, "probe_roles", None):
        return True
    if namespace.non_interactive or namespace.yes:
        return False
    return prompt_yes_no("Run optional live connectivity checks now?", False)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser(str(default_config_path()), str(default_template_path()))
    namespace = parser.parse_args(argv)
    try:
        result = _run_wizard(namespace)
    except ValueError as exc:
        if getattr(namespace, "json", False):
            print(json.dumps({"error": str(exc)}, indent=2, sort_keys=True))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 2
    if namespace.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        print(result_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
