# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers for normalized backend selection from wallbox configuration."""

from __future__ import annotations

import configparser
from pathlib import Path
from typing import Any

from .models import BackendMode, BackendSelection


def normalize_backend_mode(value: object) -> BackendMode:
    """Return one supported backend mode string."""
    mode = str(value).strip().lower() if value is not None else ""
    return "split" if mode == "split" else "combined"


def normalize_backend_type(value: object, fallback: str) -> str:
    """Return one normalized backend type name."""
    normalized = str(value).strip().lower() if value is not None else ""
    return normalized or fallback


def normalize_optional_backend_type(value: object) -> str | None:
    """Return one optional backend type name."""
    normalized = str(value).strip().lower() if value is not None else ""
    return normalized or None


def normalize_config_path(value: object) -> Path | None:
    """Return one normalized optional config path."""
    normalized = str(value).strip() if value is not None else ""
    if not normalized:
        return None
    return Path(normalized)


def _backends_section(config: configparser.ConfigParser) -> configparser.SectionProxy:
    """Return the preferred config section for backend selection."""
    return config["Backends"] if config.has_section("Backends") else config["DEFAULT"]


def validate_backend_selection(selection: BackendSelection) -> BackendSelection:
    """Return one validated backend selection or raise on unsupported combinations.

    This is the normative topology gate for the service. Runtime overrides may
    tune policy and behavior, but they must not widen this structural backend
    space implicitly. If a topology should stay forbidden in the product, it
    should be rejected here explicitly.
    """
    for message in _backend_selection_validation_errors(selection):
        raise ValueError(message)
    return selection


def _backend_selection_validation_errors(selection: BackendSelection) -> tuple[str, ...]:
    """Return all structural backend validation errors for one selection."""
    errors: list[str] = []
    if selection.meter_type == "none":
        errors.extend(_missing_backend_errors(selection.mode, selection.charger_type, "MeterType"))
    if selection.switch_type == "none":
        errors.extend(_missing_backend_errors(selection.mode, selection.charger_type, "SwitchType"))
    return tuple(errors)


def _missing_backend_errors(mode: BackendMode, charger_type: str | None, field_name: str) -> list[str]:
    """Return validation errors for one backend field set to ``none``."""
    errors: list[str] = []
    if mode != "split":
        errors.append(f"{field_name}=none is only supported in split backend mode")
    if charger_type is None:
        errors.append(f"{field_name}=none requires a configured charger backend")
    return errors


def load_backend_selection(config: configparser.ConfigParser) -> BackendSelection:
    """Return normalized backend selection from wallbox config."""
    section = _backends_section(config)
    return validate_backend_selection(
        BackendSelection(
            mode=normalize_backend_mode(section.get("Mode", "combined")),
            meter_type=normalize_backend_type(section.get("MeterType", "shelly_combined"), "shelly_combined"),
            switch_type=normalize_backend_type(section.get("SwitchType", "shelly_combined"), "shelly_combined"),
            charger_type=normalize_optional_backend_type(section.get("ChargerType", "")),
            meter_config_path=normalize_config_path(section.get("MeterConfigPath", "")),
            switch_config_path=normalize_config_path(section.get("SwitchConfigPath", "")),
            charger_config_path=normalize_config_path(section.get("ChargerConfigPath", "")),
        )
    )


def selection_from_service(service: Any) -> BackendSelection:
    """Return normalized backend selection from service attributes."""
    return validate_backend_selection(
        BackendSelection(
            mode=normalize_backend_mode(getattr(service, "backend_mode", "combined")),
            meter_type=normalize_backend_type(getattr(service, "meter_backend_type", "shelly_combined"), "shelly_combined"),
            switch_type=normalize_backend_type(getattr(service, "switch_backend_type", "shelly_combined"), "shelly_combined"),
            charger_type=normalize_optional_backend_type(getattr(service, "charger_backend_type", None)),
            meter_config_path=normalize_config_path(getattr(service, "meter_backend_config_path", "")),
            switch_config_path=normalize_config_path(getattr(service, "switch_backend_config_path", "")),
            charger_config_path=normalize_config_path(getattr(service, "charger_backend_config_path", "")),
        )
    )
