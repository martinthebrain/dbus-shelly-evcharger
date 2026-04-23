# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import configparser
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable

from venus_evcharger.backend.config import load_backend_selection
from venus_evcharger.backend.probe import (
    probe_meter_backend,
    probe_switch_backend,
    read_charger_backend,
    validate_wallbox_config,
)
from venus_evcharger.bootstrap.wizard_layouts import generated_adapter_files
from venus_evcharger.bootstrap.wizard_models import WizardAnswers
from venus_evcharger.bootstrap.wizard_support import host_from_input
from venus_evcharger.core.common_values import normalize_phase


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


class CasePreservingConfigParser(configparser.ConfigParser):
    """Config parser that keeps option names exactly as written."""

    def optionxform(self, optionstr: str) -> str:
        return optionstr


def default_template_path() -> Path:
    return repo_root() / "deploy" / "venus" / "config.venus_evcharger.ini"


def default_config_path() -> Path:
    return default_template_path()


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def mode_value(policy_mode: str) -> str:
    return {"manual": "0", "auto": "1", "scheduled": "2"}[policy_mode]


def replace_assignment(text: str, key: str, value: str) -> str:
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


def replace_optional_assignment(text: str, key: str, value: object) -> str:
    if value is None:
        return text
    if isinstance(value, float):
        rendered = f"{value:g}"
    else:
        rendered = str(value)
    return replace_assignment(text, key, rendered)


def upsert_default_assignments(text: str, assignments: dict[str, str]) -> str:
    if not assignments:
        return text
    lines = text.splitlines()
    remaining = dict(assignments)
    rendered: list[str] = []
    inserted = False
    in_default_section = False

    def maybe_insert_before_section(line: str) -> None:
        nonlocal inserted
        if inserted or not in_default_section or not line.startswith("[") or not line.endswith("]"):
            return
        rendered.extend(f"{key}={value}" for key, value in remaining.items())
        remaining.clear()
        inserted = True

    for line in lines:
        if line == "[DEFAULT]":
            in_default_section = True
            rendered.append(line)
            continue
        maybe_insert_before_section(line)
        matched_key = next((key for key in remaining if line.startswith(f"{key}=")), None)
        if matched_key is None:
            rendered.append(line)
            continue
        rendered.append(f"{matched_key}={remaining.pop(matched_key)}")
    if remaining:
        if rendered and rendered[-1].strip():
            rendered.append("")
        rendered.extend(f"{key}={value}" for key, value in remaining.items())
    return "\n".join(rendered) + "\n"


def remove_section(text: str, section_name: str) -> str:
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


def append_backends(text: str, lines: list[str]) -> str:
    if not lines:
        return remove_section(text, "Backends")
    result = remove_section(text, "Backends").rstrip()
    return f"{result}\n\n[Backends]\n" + "\n".join(lines) + "\n"


def render_wizard_config(template_text: str, answers: WizardAnswers) -> tuple[str, dict[str, str], dict[str, str]]:
    config_text = replace_assignment(template_text, "Host", host_from_input(answers.host_input))
    config_text = replace_assignment(config_text, "DeviceInstance", str(int(answers.device_instance)))
    config_text = replace_assignment(config_text, "DigestAuth", "1" if answers.digest_auth else "0")
    config_text = replace_assignment(config_text, "Username", answers.username.strip())
    config_text = replace_assignment(config_text, "Password", answers.password.strip())
    config_text = replace_assignment(config_text, "Phase", normalize_phase(answers.phase))
    config_text = replace_assignment(config_text, "Mode", mode_value(answers.policy_mode))
    config_text = replace_optional_assignment(config_text, "AutoStartSurplusWatts", answers.auto_start_surplus_watts)
    config_text = replace_optional_assignment(config_text, "AutoStopSurplusWatts", answers.auto_stop_surplus_watts)
    config_text = replace_optional_assignment(config_text, "AutoMinSoc", answers.auto_min_soc)
    config_text = replace_optional_assignment(config_text, "AutoResumeSoc", answers.auto_resume_soc)
    config_text = replace_optional_assignment(config_text, "AutoScheduledEnabledDays", answers.scheduled_enabled_days)
    config_text = replace_optional_assignment(config_text, "AutoScheduledLatestEndTime", answers.scheduled_latest_end_time)
    config_text = replace_optional_assignment(config_text, "AutoScheduledNightCurrentAmps", answers.scheduled_night_current_amps)
    backend_lines, adapter_files, role_hosts = generated_adapter_files(
        profile=answers.profile,
        primary_host_input=answers.host_input,
        meter_host_input=answers.meter_host_input,
        switch_host_input=answers.switch_host_input,
        charger_host_input=answers.charger_host_input,
        split_preset=answers.split_preset,
        charger_backend=answers.charger_backend,
        charger_preset=answers.charger_preset,
        request_timeout_seconds=answers.request_timeout_seconds,
        switch_group_supported_phase_selections=answers.switch_group_supported_phase_selections,
        transport_kind=answers.transport_kind,
        transport_host=answers.transport_host,
        transport_port=answers.transport_port,
        transport_device=answers.transport_device,
        transport_unit_id=answers.transport_unit_id,
    )
    return append_backends(config_text, backend_lines), adapter_files, role_hosts


def materialized_config_text(config_text: str, output_dir: Path, adapter_files: dict[str, str]) -> str:
    rendered = config_text
    for relative_path in sorted(adapter_files):
        rendered = rendered.replace(f"={relative_path}\n", f"={output_dir / relative_path}\n")
    return rendered


def validate_rendered_setup(config_text: str, adapter_files: dict[str, str], config_name: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        materialized_text = materialized_config_text(config_text, temp_path, adapter_files)
        main_path = temp_path / config_name
        main_path.write_text(materialized_text, encoding="utf-8")
        for relative_path, content in adapter_files.items():
            (temp_path / relative_path).write_text(content, encoding="utf-8")
        return validate_wallbox_config(str(main_path))


def live_connectivity_payload(main_path: Path, selected_roles: tuple[str, ...] | None) -> dict[str, object]:
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


def live_check_rendered_setup(
    config_text: str,
    adapter_files: dict[str, str],
    config_name: str,
    selected_roles: tuple[str, ...] | None,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        materialized_text = materialized_config_text(config_text, temp_path, adapter_files)
        main_path = temp_path / config_name
        main_path.write_text(materialized_text, encoding="utf-8")
        for relative_path, content in adapter_files.items():
            (temp_path / relative_path).write_text(content, encoding="utf-8")
        return live_connectivity_payload(main_path, selected_roles)


def answer_defaults(answers: WizardAnswers) -> dict[str, object]:
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
        "charger_preset": answers.charger_preset,
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


def backup_path(target: Path) -> Path:
    return target.with_name(f"{target.name}.wizard-backup-{timestamp()}")


def write_with_backup(target: Path, content: str) -> str | None:
    backup_file: str | None = None
    if target.exists():
        destination = backup_path(target)
        shutil.copy2(target, destination)
        backup_file = str(destination)
    target.write_text(content, encoding="utf-8")
    return backup_file


def write_generated_files(config_path: Path, materialized_text: str, adapter_files: dict[str, str]) -> list[str]:
    backup_files: list[str] = []
    backup_file = write_with_backup(config_path, materialized_text)
    if backup_file is not None:
        backup_files.append(backup_file)
    for relative_path, content in sorted(adapter_files.items()):
        backup_file = write_with_backup(config_path.parent / relative_path, content)
        if backup_file is not None:
            backup_files.append(backup_file)
    return backup_files
