# SPDX-License-Identifier: GPL-3.0-or-later
"""Schema helpers for external energy recommendation bundles."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

RECOMMENDATION_BUNDLE_SCHEMA_TYPE = "energy-recommendation-bundle"
RECOMMENDATION_BUNDLE_SCHEMA_VERSION = 1


def recommendation_bundle_manifest_path(prefix: str) -> Path:
    """Return the manifest path for one recommendation bundle prefix."""
    return Path(str(Path(prefix)) + ".manifest.json")


def recommendation_bundle_manifest(
    *,
    source_id: str,
    profile: str,
    config_path: str,
    written_files: Mapping[str, str],
) -> dict[str, object]:
    """Build a versioned manifest for one recommendation bundle."""
    return {
        "schema_type": RECOMMENDATION_BUNDLE_SCHEMA_TYPE,
        "schema_version": RECOMMENDATION_BUNDLE_SCHEMA_VERSION,
        "source_id": source_id,
        "profile": profile,
        "config_path": config_path,
        "files": {
            "config_snippet": str(written_files["config_snippet"]),
            "wizard_hint": str(written_files["wizard_hint"]),
            "summary": str(written_files["summary"]),
        },
    }


def validate_recommendation_bundle_manifest(payload: Mapping[str, object]) -> dict[str, object]:
    """Validate and normalize one recommendation bundle manifest."""
    schema_type = str(payload.get("schema_type", "")).strip()
    if schema_type != RECOMMENDATION_BUNDLE_SCHEMA_TYPE:
        raise ValueError(f"Unsupported recommendation bundle schema type '{schema_type}'")
    schema_version = payload.get("schema_version")
    if schema_version != RECOMMENDATION_BUNDLE_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported recommendation bundle schema version "
            f"'{schema_version}' (expected {RECOMMENDATION_BUNDLE_SCHEMA_VERSION})"
        )
    source_id = str(payload.get("source_id", "")).strip()
    profile = str(payload.get("profile", "")).strip()
    config_path = str(payload.get("config_path", "")).strip()
    files = payload.get("files")
    if not source_id:
        raise ValueError("Recommendation bundle manifest is missing source_id")
    if not profile:
        raise ValueError("Recommendation bundle manifest is missing profile")
    if not config_path:
        raise ValueError("Recommendation bundle manifest is missing config_path")
    if not isinstance(files, Mapping):
        raise ValueError("Recommendation bundle manifest is missing files")
    normalized_files = {
        "config_snippet": str(files.get("config_snippet", "")).strip(),
        "wizard_hint": str(files.get("wizard_hint", "")).strip(),
        "summary": str(files.get("summary", "")).strip(),
    }
    for key, value in normalized_files.items():
        if not value:
            raise ValueError(f"Recommendation bundle manifest is missing files.{key}")
    return {
        "schema_type": RECOMMENDATION_BUNDLE_SCHEMA_TYPE,
        "schema_version": RECOMMENDATION_BUNDLE_SCHEMA_VERSION,
        "source_id": source_id,
        "profile": profile,
        "config_path": config_path,
        "files": normalized_files,
    }
