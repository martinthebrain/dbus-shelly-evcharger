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

from venus_evcharger.backend.config import load_backend_selection
from venus_evcharger.backend.probe import (
    probe_meter_backend,
    probe_switch_backend,
    read_charger_backend,
    validate_wallbox_config,
)
from venus_evcharger.bootstrap.wizard_cli import build_answers, build_parser, prompt_yes_no
from venus_evcharger.bootstrap.wizard_cli_output import result_text
from venus_evcharger.bootstrap.wizard_guidance import compatibility_warnings, probe_roles
from venus_evcharger.bootstrap.wizard_layouts import generated_adapter_files
from venus_evcharger.bootstrap.wizard_models import WizardAnswers, WizardResult, WizardTransportKind
from venus_evcharger.bootstrap.wizard_persistence import persist_wizard_state
from venus_evcharger.bootstrap.wizard_review import manual_review_items
from venus_evcharger.bootstrap.wizard_support import host_from_input, transport_summary
from venus_evcharger.core.common_values import normalize_phase


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


class _CasePreservingConfigParser(configparser.ConfigParser):
    """Config parser that keeps option names exactly as written."""

    def optionxform(self, optionstr: str) -> str:
        return optionstr


def default_template_path() -> Path:
    return _repo_root() / "deploy" / "venus" / "config.venus_evcharger.ini"

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


def _upsert_default_assignments(text: str, assignments: dict[str, str]) -> str:
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
        charger_preset=answers.charger_preset,
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
    manual_review: tuple[str, ...],
    suggested_blocks: dict[str, str],
    suggested_energy_sources: tuple[dict[str, object], ...],
    suggested_energy_merge: dict[str, object] | None,
) -> WizardResult:
    return WizardResult(
        created_at=created_at,
        config_path=str(config_path),
        imported_from=imported_from,
        profile=answers.profile,
        policy_mode=answers.policy_mode,
        split_preset=answers.split_preset,
        charger_backend=answers.charger_backend,
        charger_preset=answers.charger_preset,
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
        manual_review=manual_review,
        dry_run=dry_run,
        suggested_blocks=suggested_blocks,
        suggested_energy_sources=suggested_energy_sources,
        suggested_energy_merge=suggested_energy_merge,
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


def _existing_auto_energy_source_ids(config_path: Path) -> tuple[str, ...]:
    if not config_path.exists():
        return ()
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")
    raw = ""
    if parser.defaults():
        raw = str(parser.defaults().get("AutoEnergySources", "")).strip()
    if not raw and parser.has_section("DEFAULT"):
        raw = str(parser["DEFAULT"].get("AutoEnergySources", "")).strip()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _optional_capacity_wh(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        capacity = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return capacity if capacity > 0.0 else None


def _normalized_recommendation_prefixes(
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


def _merged_recommendation_prefixes(
    *prefix_groups: str | tuple[str, ...] | list[str] | None,
) -> tuple[str, ...]:
    merged: list[str] = []
    for group in prefix_groups:
        for prefix in _normalized_recommendation_prefixes(group):
            if prefix not in merged:
                merged.append(prefix)
    return tuple(merged)


def _existing_auto_energy_assignments(config_path: Path) -> dict[str, str]:
    if not config_path.exists():
        return {}
    parser = _CasePreservingConfigParser()
    parser.read(config_path, encoding="utf-8")
    assignments: dict[str, str] = {}
    for key, value in parser.defaults().items():
        if key == "AutoUseCombinedBatterySoc" or key == "AutoEnergySources" or key.startswith("AutoEnergySource."):
            assignments[key] = str(value).strip()
    return assignments


def _merge_energy_source_ids(existing_ids: tuple[str, ...], suggested_sources: tuple[dict[str, object], ...]) -> tuple[str, ...]:
    merged = list(existing_ids)
    for source in suggested_sources:
        source_id = str(source.get("source_id", "")).strip()
        if source_id and source_id not in merged:
            merged.append(source_id)
    return tuple(merged)


def _existing_source_ids_from_assignments(assignments: dict[str, str]) -> tuple[str, ...]:
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


def _suggested_energy_sources_with_capacity(
    suggested_sources: tuple[dict[str, object], ...],
    capacity_wh: float | None,
) -> tuple[dict[str, object], ...]:
    capacity = _optional_capacity_wh(capacity_wh)
    if capacity is None:
        return suggested_sources
    updated_sources: list[dict[str, object]] = []
    for source in suggested_sources:
        updated = dict(source)
        if str(updated.get("capacityConfigKey", "")).strip():
            updated["usableCapacityWh"] = capacity
        updated_sources.append(updated)
    return tuple(updated_sources)


def _suggested_energy_sources_with_capacity_overrides(
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


def _validate_unique_suggested_energy_sources(
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


def _energy_source_merge_lines(source: dict[str, object]) -> list[str]:
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


def _energy_source_capacity_follow_up(source: dict[str, object]) -> dict[str, object] | None:
    source_id = str(source.get("source_id", "")).strip()
    config_key = str(source.get("capacityConfigKey", "")).strip()
    hint = str(source.get("capacityHint", "")).strip()
    if not source_id or not config_key:
        return None
    configured_capacity = _optional_capacity_wh(source.get("usableCapacityWh"))
    return {
        "source_id": source_id,
        "config_key": config_key,
        "placeholder": f"{configured_capacity:g}" if configured_capacity is not None else "<set-me>",
        "hint": hint or "Set usable battery capacity in Wh for weighted combined SOC.",
        "configured": configured_capacity is not None,
    }


def _suggested_energy_assignments(
    existing_assignments: dict[str, str],
    suggested_sources: tuple[dict[str, object], ...],
) -> dict[str, str]:
    assignments = dict(existing_assignments)
    merged_ids = _merge_energy_source_ids(_existing_source_ids_from_assignments(assignments), suggested_sources)
    ordered: dict[str, str] = {
        "AutoUseCombinedBatterySoc": "1",
        "AutoEnergySources": ",".join(merged_ids),
    }
    for key, value in assignments.items():
        if key in ordered:
            continue
        ordered[key] = value
    for source in suggested_sources:
        for line in _energy_source_merge_lines(source):
            key, value = line.split("=", 1)
            ordered[key] = value
    return ordered


def _build_suggested_energy_merge(
    config_path: Path,
    suggested_sources: tuple[dict[str, object], ...],
) -> tuple[dict[str, object] | None, dict[str, str]]:
    if not suggested_sources:
        return None, {}
    existing_assignments = _existing_auto_energy_assignments(config_path)
    existing_ids = _existing_source_ids_from_assignments(existing_assignments)
    merged_ids = _merge_energy_source_ids(existing_ids, suggested_sources)
    capacity_follow_up = tuple(
        item for item in (_energy_source_capacity_follow_up(source) for source in suggested_sources) if item is not None
    )
    merge_lines = [
        "# Merge these lines into the main config when you want the suggested external energy source enabled.",
        "AutoUseCombinedBatterySoc=1",
        "AutoEnergySources=" + ",".join(merged_ids),
    ]
    for source in suggested_sources:
        merge_lines.extend(_energy_source_merge_lines(source))
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


def _structured_energy_source_from_block(
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


def _bundle_source_id(config_snippet: str, default_source_id: str) -> str:
    for raw_line in config_snippet.splitlines():
        line = raw_line.strip()
        if not line.startswith("AutoEnergySource.") or "=" not in line:
            continue
        remainder = line[len("AutoEnergySource.") :]
        source_id = remainder.split(".", 1)[0].strip()
        if source_id:
            return source_id
    return default_source_id


def _bundle_target_names(source_id: str) -> dict[str, str]:
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


def _bundle_labels(source_id: str) -> tuple[str, str]:
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


def _bundle_block_label(source_id: str) -> str:
    normalized_source_id = source_id.strip() or "huawei"
    if normalized_source_id == "huawei":
        return "External energy source"
    return f"External energy source ({normalized_source_id})"


def _manual_review_union(
    existing_items: tuple[str, ...],
    new_items: tuple[str, ...],
) -> tuple[str, ...]:
    merged = list(existing_items)
    for item in new_items:
        if item not in merged:
            merged.append(item)
    return tuple(merged)


def _huawei_bundle_files(
    prefix: str,
    *,
    source_id: str = "huawei",
) -> tuple[dict[str, str], tuple[str, ...], dict[str, str], tuple[dict[str, object], ...]]:
    base = Path(prefix)
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
    resolved_source_id = _bundle_source_id(source_contents["ini"], source_id)
    target_names = _bundle_target_names(resolved_source_id)
    rendered_files = {
        target_names["ini"]: source_contents["ini"],
        target_names["wizard"]: source_contents["wizard"],
        target_names["summary"]: source_contents["summary"],
    }
    structured_source = _structured_energy_source_from_block(resolved_source_id, source_contents["ini"])
    structured_source["capacityRequiredForWeightedSoc"] = True
    structured_source["capacityConfigKey"] = f"AutoEnergySource.{resolved_source_id}.UsableCapacityWh"
    structured_source["capacityHint"] = "Set usable battery capacity in Wh for weighted combined SOC."
    review_items = _bundle_labels(resolved_source_id)
    block_label = _bundle_block_label(resolved_source_id)
    return (
        rendered_files,
        review_items,
        {block_label: source_contents["ini"]},
        (structured_source,),
    )


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
        charger_preset=result.charger_preset,
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
        suggested_blocks=result.suggested_blocks,
        suggested_energy_sources=result.suggested_energy_sources,
        suggested_energy_merge=result.suggested_energy_merge,
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
    energy_recommendation_prefix: str | tuple[str, ...] | list[str] | None = None,
    huawei_recommendation_prefix: str | tuple[str, ...] | list[str] | None = None,
    apply_suggested_energy_merge: bool = False,
    suggested_energy_capacity_wh: float | None = None,
    suggested_energy_capacity_overrides: dict[str, float] | None = None,
) -> WizardResult:
    template_text = template_path.read_text(encoding="utf-8")
    created_at = datetime.now().isoformat(timespec="seconds")
    config_text, adapter_files, role_hosts = render_wizard_config(template_text, answers)
    manual_review = manual_review_items(
        answers.profile,
        answers.policy_mode,
        answers.charger_backend,
        answers.transport_kind,
        answers.split_preset,
    )
    suggested_blocks: dict[str, str] = {}
    suggested_energy_sources: tuple[dict[str, object], ...] = tuple()
    suggested_energy_merge: dict[str, object] | None = None
    recommendation_prefixes = _merged_recommendation_prefixes(
        energy_recommendation_prefix,
        huawei_recommendation_prefix,
    )
    if recommendation_prefixes:
        bundle_files: dict[str, str] = {}
        bundle_blocks: dict[str, str] = {}
        bundle_sources: list[dict[str, object]] = []
        for recommendation_prefix in recommendation_prefixes:
            huawei_files, huawei_review_items, huawei_blocks, huawei_sources = _huawei_bundle_files(recommendation_prefix)
            bundle_files.update(huawei_files)
            bundle_blocks.update(huawei_blocks)
            bundle_sources.extend(dict(source) for source in huawei_sources)
            manual_review = _manual_review_union(manual_review, huawei_review_items)
        adapter_files = {**adapter_files, **bundle_files}
        suggested_blocks = dict(bundle_blocks)
        suggested_energy_sources = _validate_unique_suggested_energy_sources(tuple(bundle_sources))
        if suggested_energy_capacity_wh is not None and len(suggested_energy_sources) == 1:
            suggested_energy_sources = _suggested_energy_sources_with_capacity(
                suggested_energy_sources,
                suggested_energy_capacity_wh,
            )
        suggested_energy_sources = _suggested_energy_sources_with_capacity_overrides(
            suggested_energy_sources,
            suggested_energy_capacity_overrides or {},
        )
        suggested_energy_merge, merge_files = _build_suggested_energy_merge(config_path, suggested_energy_sources)
        adapter_files = {**adapter_files, **merge_files}
        if apply_suggested_energy_merge and suggested_energy_merge is not None:
            config_text = _upsert_default_assignments(
                config_text,
                _suggested_energy_assignments(_existing_auto_energy_assignments(config_path), suggested_energy_sources),
            )
            suggested_energy_merge = dict(suggested_energy_merge)
            suggested_energy_merge["applied_to_config"] = True
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
        charger_preset=answers.charger_preset,
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
        manual_review,
        suggested_blocks,
        suggested_energy_sources,
        suggested_energy_merge,
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
        charger_preset=result.charger_preset,
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
        suggested_blocks=result.suggested_blocks,
        suggested_energy_sources=result.suggested_energy_sources,
        suggested_energy_merge=result.suggested_energy_merge,
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
    recommendation_prefixes = _merged_recommendation_prefixes(
        getattr(namespace, "energy_recommendation_prefix", None),
        getattr(namespace, "huawei_recommendation_prefix", None),
    )
    suggested_energy_capacity_wh = _resolved_energy_capacity_wh(namespace, recommendation_prefixes)
    suggested_energy_capacity_overrides = _resolved_energy_capacity_overrides(namespace)
    preview = configure_wallbox(
        answers,
        config_path=Path(namespace.config_path),
        template_path=Path(namespace.template_path),
        dry_run=True,
        live_check=live_check,
        selected_probe_roles=selected_probe_roles,
        imported_from=imported_from,
        energy_recommendation_prefix=recommendation_prefixes,
        huawei_recommendation_prefix=recommendation_prefixes,
        apply_suggested_energy_merge=getattr(namespace, "apply_energy_merge", False),
        suggested_energy_capacity_wh=suggested_energy_capacity_wh,
        suggested_energy_capacity_overrides=suggested_energy_capacity_overrides,
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
        energy_recommendation_prefix=recommendation_prefixes,
        huawei_recommendation_prefix=recommendation_prefixes,
        apply_suggested_energy_merge=getattr(namespace, "apply_energy_merge", False),
        suggested_energy_capacity_wh=suggested_energy_capacity_wh,
        suggested_energy_capacity_overrides=suggested_energy_capacity_overrides,
    )


def _resolved_energy_capacity_wh(
    namespace: argparse.Namespace,
    recommendation_prefixes: tuple[str, ...],
) -> float | None:
    direct = _optional_capacity_wh(getattr(namespace, "energy_default_usable_capacity_wh", None))
    if direct is None:
        direct = _optional_capacity_wh(getattr(namespace, "huawei_usable_capacity_wh", None))
    if direct is not None or getattr(namespace, "non_interactive", False):
        return direct
    if len(recommendation_prefixes) != 1:
        return None
    if not prompt_yes_no("Set usable battery capacity for the suggested energy source now?", False):
        return None
    return _optional_capacity_wh(input("Usable battery capacity in Wh [skip]: ").strip())


def _resolved_energy_capacity_overrides(namespace: argparse.Namespace) -> dict[str, float]:
    raw_values = getattr(namespace, "energy_usable_capacity_wh", None)
    if not raw_values:
        return {}
    overrides: dict[str, float] = {}
    for raw_value in raw_values:
        item = str(raw_value).strip()
        if "=" not in item:
            raise ValueError(
                "energy usable capacity overrides must use source_id=Wh, for example huawei_a=15360"
            )
        source_id, capacity_text = item.split("=", 1)
        normalized_source_id = source_id.strip()
        capacity = _optional_capacity_wh(capacity_text)
        if not normalized_source_id or capacity is None:
            raise ValueError(
                "energy usable capacity overrides must use source_id=Wh with a positive Wh value"
            )
        overrides[normalized_source_id] = capacity
    return overrides


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
