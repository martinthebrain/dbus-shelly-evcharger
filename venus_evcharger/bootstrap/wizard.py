# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional setup wizard for initial wallbox configuration."""

from __future__ import annotations

import argparse
import configparser
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, cast

from venus_evcharger.backend.config import load_backend_selection
from venus_evcharger.backend.probe import probe_meter_backend, probe_switch_backend, read_charger_backend
from venus_evcharger.bootstrap.wizard_cli import build_answers, build_parser, prompt_yes_no
from venus_evcharger.bootstrap.wizard_cli_output import result_text
from venus_evcharger.bootstrap.wizard_energy import (
    build_suggested_energy_merge,
    existing_auto_energy_assignments,
    huawei_bundle_files,
    manual_review_union,
    merged_recommendation_prefixes,
    optional_capacity_wh,
    suggested_energy_assignments,
    suggested_energy_sources_with_capacity,
    suggested_energy_sources_with_capacity_overrides,
    validate_unique_suggested_energy_sources,
)
from venus_evcharger.bootstrap.wizard_guidance import compatibility_warnings, probe_roles
from venus_evcharger.bootstrap.wizard_models import WizardAnswers, WizardResult, WizardTransportKind
from venus_evcharger.bootstrap.wizard_persistence import persist_wizard_state
from venus_evcharger.bootstrap.wizard_render import (
    answer_defaults,
    append_backends,
    default_config_path,
    default_template_path,
    materialized_config_text,
    replace_assignment,
    render_wizard_config,
    upsert_default_assignments,
    validate_rendered_setup,
    write_generated_files,
)
from venus_evcharger.bootstrap.wizard_review import manual_review_items
from venus_evcharger.bootstrap.wizard_support import transport_summary

_replace_assignment = replace_assignment
_append_backends = append_backends
_write_generated_files = write_generated_files


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
        materialized_text = materialized_config_text(config_text, temp_path, adapter_files)
        main_path = temp_path / config_name
        main_path.write_text(materialized_text, encoding="utf-8")
        for relative_path, content in adapter_files.items():
            (temp_path / relative_path).write_text(content, encoding="utf-8")
        return _live_connectivity_payload(main_path, selected_roles)


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
        answer_defaults=answer_defaults(answers),
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


def _suggested_energy_state(
    config_path: Path,
    config_text: str,
    adapter_files: dict[str, str],
    manual_review: tuple[str, ...],
    recommendation_prefixes: tuple[str, ...],
    *,
    apply_suggested_energy_merge: bool,
    suggested_energy_capacity_wh: float | None,
    suggested_energy_capacity_overrides: dict[str, float] | None,
) -> tuple[
    str,
    dict[str, str],
    tuple[str, ...],
    dict[str, str],
    tuple[dict[str, object], ...],
    dict[str, object] | None,
]:
    suggested_blocks: dict[str, str] = {}
    suggested_energy_sources: tuple[dict[str, object], ...] = tuple()
    suggested_energy_merge: dict[str, object] | None = None
    if not recommendation_prefixes:
        return (
            config_text,
            adapter_files,
            manual_review,
            suggested_blocks,
            suggested_energy_sources,
            suggested_energy_merge,
        )
    bundle_files, manual_review, suggested_blocks, suggested_energy_sources = _merged_recommendation_bundle_state(
        recommendation_prefixes,
        manual_review,
    )
    adapter_files = {**adapter_files, **bundle_files}
    suggested_energy_sources = _suggested_energy_sources_with_requested_capacity(
        suggested_energy_sources,
        suggested_energy_capacity_wh,
        suggested_energy_capacity_overrides,
    )
    suggested_energy_merge, merge_files = build_suggested_energy_merge(config_path, suggested_energy_sources)
    adapter_files = {**adapter_files, **merge_files}
    if apply_suggested_energy_merge and suggested_energy_merge is not None:
        config_text, suggested_energy_merge = _config_text_with_suggested_energy_merge(
            config_path,
            config_text,
            suggested_energy_sources,
            suggested_energy_merge,
        )
    return (
        config_text,
        adapter_files,
        manual_review,
        suggested_blocks,
        suggested_energy_sources,
        suggested_energy_merge,
    )


def _merged_recommendation_bundle_state(
    recommendation_prefixes: tuple[str, ...],
    manual_review: tuple[str, ...],
) -> tuple[dict[str, str], tuple[str, ...], dict[str, str], tuple[dict[str, object], ...]]:
    bundle_files: dict[str, str] = {}
    bundle_blocks: dict[str, str] = {}
    bundle_sources: list[dict[str, object]] = []
    for recommendation_prefix in recommendation_prefixes:
        huawei_files, huawei_review_items, huawei_blocks, huawei_sources = huawei_bundle_files(recommendation_prefix)
        bundle_files.update(huawei_files)
        bundle_blocks.update(huawei_blocks)
        bundle_sources.extend(dict(source) for source in huawei_sources)
        manual_review = manual_review_union(manual_review, huawei_review_items)
    suggested_energy_sources = validate_unique_suggested_energy_sources(tuple(bundle_sources))
    return bundle_files, manual_review, dict(bundle_blocks), suggested_energy_sources


def _suggested_energy_sources_with_requested_capacity(
    suggested_energy_sources: tuple[dict[str, object], ...],
    suggested_energy_capacity_wh: float | None,
    suggested_energy_capacity_overrides: dict[str, float] | None,
) -> tuple[dict[str, object], ...]:
    if suggested_energy_capacity_wh is not None and len(suggested_energy_sources) == 1:
        suggested_energy_sources = suggested_energy_sources_with_capacity(
            suggested_energy_sources,
            suggested_energy_capacity_wh,
        )
    return suggested_energy_sources_with_capacity_overrides(
        suggested_energy_sources,
        suggested_energy_capacity_overrides or {},
    )


def _config_text_with_suggested_energy_merge(
    config_path: Path,
    config_text: str,
    suggested_energy_sources: tuple[dict[str, object], ...],
    suggested_energy_merge: dict[str, object],
) -> tuple[str, dict[str, object]]:
    config_text = upsert_default_assignments(
        config_text,
        suggested_energy_assignments(existing_auto_energy_assignments(config_path), suggested_energy_sources),
    )
    updated_merge = dict(suggested_energy_merge)
    updated_merge["applied_to_config"] = True
    return config_text, updated_merge


def _wizard_persisted_result(result: WizardResult, config_path: Path, materialized_text: str, adapter_files: dict[str, str]) -> WizardResult:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    backup_files = tuple(_write_generated_files(config_path, materialized_text, adapter_files))
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
        backup_files=backup_files,
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
    recommendation_prefixes = merged_recommendation_prefixes(
        energy_recommendation_prefix,
        huawei_recommendation_prefix,
    )
    (
        config_text,
        adapter_files,
        manual_review,
        suggested_blocks,
        suggested_energy_sources,
        suggested_energy_merge,
    ) = _suggested_energy_state(
        config_path,
        config_text,
        adapter_files,
        manual_review,
        recommendation_prefixes,
        apply_suggested_energy_merge=apply_suggested_energy_merge,
        suggested_energy_capacity_wh=suggested_energy_capacity_wh,
        suggested_energy_capacity_overrides=suggested_energy_capacity_overrides,
    )
    validation = validate_rendered_setup(config_text, adapter_files, config_path.name)
    live_check_payload = (
        _live_check_rendered_setup(config_text, adapter_files, config_path.name, selected_probe_roles) if live_check else None
    )
    materialized_text = materialized_config_text(config_text, config_path.parent, adapter_files)
    generated_files = (config_path.name,) + tuple(sorted(adapter_files))
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
    return _wizard_persisted_result(result, config_path, materialized_text, adapter_files)


def _existing_output_paths(config_path: Path, generated_files: tuple[str, ...]) -> tuple[str, ...]:
    existing = []
    for relative_path in generated_files:
        candidate = config_path.parent / relative_path
        if candidate.exists():
            existing.append(str(candidate))
    return tuple(existing)


def _interactive_write_confirmed(preview: WizardResult, existing_files: tuple[str, ...]) -> bool:
    print(result_text(preview))
    prompt = "Write config now and create backups of existing files?" if existing_files else "Write config now?"
    return prompt_yes_no(prompt, not bool(existing_files))


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


def _confirm_write(namespace: object, preview: WizardResult, existing_files: tuple[str, ...]) -> None:
    if _skip_write_confirmation(namespace, existing_files):
        return
    if not _interactive_write_confirmed(preview, existing_files):
        raise ValueError("Wizard write cancelled by user")


def _resolved_energy_capacity_wh(
    namespace: argparse.Namespace,
    recommendation_prefixes: tuple[str, ...],
) -> float | None:
    direct = _direct_energy_capacity_wh(namespace)
    if direct is not None or getattr(namespace, "non_interactive", False):
        return direct
    if not _can_prompt_energy_capacity(recommendation_prefixes):
        return None
    if not prompt_yes_no("Set usable battery capacity for the suggested energy source now?", False):
        return None
    return optional_capacity_wh(input("Usable battery capacity in Wh [skip]: ").strip())


def _resolved_energy_capacity_overrides(namespace: argparse.Namespace) -> dict[str, float]:
    raw_values = getattr(namespace, "energy_usable_capacity_wh", None)
    if not raw_values:
        return {}
    overrides: dict[str, float] = {}
    for raw_value in raw_values:
        normalized_source_id, capacity = _parsed_energy_capacity_override(raw_value)
        overrides[normalized_source_id] = capacity
    return overrides


def _direct_energy_capacity_wh(namespace: argparse.Namespace) -> float | None:
    direct = optional_capacity_wh(getattr(namespace, "energy_default_usable_capacity_wh", None))
    if direct is not None:
        return direct
    return optional_capacity_wh(getattr(namespace, "huawei_usable_capacity_wh", None))


def _can_prompt_energy_capacity(recommendation_prefixes: tuple[str, ...]) -> bool:
    return len(recommendation_prefixes) == 1


def _parsed_energy_capacity_override(raw_value: object) -> tuple[str, float]:
    item = str(raw_value).strip()
    if "=" not in item:
        raise ValueError(
            "energy usable capacity overrides must use source_id=Wh, for example huawei_a=15360"
        )
    source_id, capacity_text = item.split("=", 1)
    normalized_source_id = source_id.strip()
    capacity = optional_capacity_wh(capacity_text)
    if not normalized_source_id or capacity is None:
        raise ValueError(
            "energy usable capacity overrides must use source_id=Wh with a positive Wh value"
        )
    return normalized_source_id, capacity


def _resolve_live_check(namespace: argparse.Namespace) -> bool:
    if namespace.live_check or getattr(namespace, "probe_roles", None):
        return True
    if namespace.non_interactive or namespace.yes:
        return False
    return prompt_yes_no("Run optional live connectivity checks now?", False)


def _run_wizard(namespace: argparse.Namespace) -> WizardResult:
    answers, imported = build_answers(namespace)
    imported_from = imported.imported_from if imported is not None else None
    live_check = _resolve_live_check(namespace)
    selected_probe_roles = probe_roles(namespace)
    recommendation_prefixes = merged_recommendation_prefixes(
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


__all__ = [
    "WizardAnswers",
    "configure_wallbox",
    "default_template_path",
    "default_config_path",
    "main",
    "_live_connectivity_payload",
    "_live_check_rendered_setup",
    "_replace_assignment",
    "_append_backends",
    "_write_generated_files",
    "load_backend_selection",
    "probe_meter_backend",
    "probe_switch_backend",
    "read_charger_backend",
    "prompt_yes_no",
    "_interactive_write_confirmed",
]


if __name__ == "__main__":
    raise SystemExit(main())
