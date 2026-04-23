# SPDX-License-Identifier: GPL-3.0-or-later
"""CLI rendering and bundle writing helpers for energy probe output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

from .recommendation_schema import recommendation_bundle_manifest, recommendation_bundle_manifest_path


def _render_payload(args: argparse.Namespace, payload: Mapping[str, object]) -> str:
    if args.command != "validate-huawei-energy" or str(args.emit) == "json":
        return json.dumps(payload, indent=2, sort_keys=True)
    recommendation = payload.get("recommendation")
    if not isinstance(recommendation, Mapping):
        return json.dumps(payload, indent=2, sort_keys=True)
    emit_mode = str(args.emit)
    if emit_mode == "ini":
        return _render_recommendation_field(recommendation, "config_snippet", payload)
    if emit_mode == "wizard-hint":
        return _render_recommendation_field(recommendation, "wizard_hint_block", payload)
    if emit_mode == "summary":
        return _render_recommendation_field(recommendation, "summary", payload)
    return json.dumps(payload, indent=2, sort_keys=True)


def _render_recommendation_field(
    recommendation: Mapping[str, object],
    field_name: str,
    payload: Mapping[str, object],
) -> str:
    value = recommendation.get(field_name)
    if isinstance(value, str) and value.strip():
        return value
    return json.dumps(payload, indent=2, sort_keys=True)


def _payload_with_written_files(args: argparse.Namespace, payload: Mapping[str, object]) -> dict[str, object]:
    prefix = str(getattr(args, "write_recommendation_prefix", "") or "").strip()
    if args.command != "validate-huawei-energy" or not prefix:
        return dict(payload)
    recommendation = payload.get("recommendation")
    if not isinstance(recommendation, Mapping):
        return dict(payload)
    enriched = dict(payload)
    enriched["written_files"] = _write_recommendation_bundle(prefix, recommendation)
    return enriched


def _write_recommendation_bundle(prefix: str, recommendation: Mapping[str, object]) -> dict[str, str]:
    base_prefix = str(Path(prefix))
    targets = {
        "config_snippet": Path(base_prefix + ".ini"),
        "wizard_hint": Path(base_prefix + ".wizard.txt"),
        "summary": Path(base_prefix + ".summary.txt"),
    }
    contents = {
        "config_snippet": _recommendation_text(recommendation, "config_snippet"),
        "wizard_hint": _recommendation_text(recommendation, "wizard_hint_block"),
        "summary": _recommendation_text(recommendation, "summary"),
    }
    for path in targets.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    for key, path in targets.items():
        path.write_text(contents[key], encoding="utf-8")
    written_files = {key: str(path) for key, path in targets.items()}
    manifest_path = recommendation_bundle_manifest_path(base_prefix)
    manifest = recommendation_bundle_manifest(
        source_id=_bundle_source_id_from_recommendation(recommendation),
        profile=str(recommendation.get("suggested_profile", "")).strip(),
        config_path=str(recommendation.get("suggested_config_path", "")).strip(),
        written_files=written_files,
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**written_files, "manifest": str(manifest_path)}


def _bundle_source_id_from_recommendation(recommendation: Mapping[str, object]) -> str:
    config_snippet = str(recommendation.get("config_snippet", "") or "")
    for raw_line in config_snippet.splitlines():
        line = raw_line.strip()
        if not line.startswith("AutoEnergySource.") or "=" not in line:
            continue
        source_id = line[len("AutoEnergySource.") :].split(".", 1)[0].strip()
        if source_id:
            return source_id
    return "huawei"


def _recommendation_text(recommendation: Mapping[str, object], field_name: str) -> str:
    value = recommendation.get(field_name)
    return value if isinstance(value, str) else ""
