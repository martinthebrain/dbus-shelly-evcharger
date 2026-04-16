# SPDX-License-Identifier: GPL-3.0-or-later
"""CLI helper to validate and probe wallbox backend adapter configs."""

from __future__ import annotations

import argparse
import configparser
import json
from dataclasses import asdict, is_dataclass
from types import SimpleNamespace
from typing import Any, cast

import requests

from .registry import CHARGER_BACKENDS, METER_BACKENDS, SWITCH_BACKENDS


def _config(path: str) -> configparser.ConfigParser:
    """Load one backend config file."""
    parser = configparser.ConfigParser()
    read_files = parser.read(path)
    if not read_files:
        raise FileNotFoundError(path)
    return parser


def _adapter_type(path: str) -> str:
    """Return one normalized adapter type from config."""
    parser = _config(path)
    if parser.has_section("Adapter"):
        return parser["Adapter"].get("Type", "shelly_combined").strip().lower()
    return parser["DEFAULT"].get("Type", "shelly_combined").strip().lower()


def _probe_service() -> Any:
    """Return one small service stub for standalone backend probing."""
    return SimpleNamespace(
        session=requests.Session(),
        host="",
        username="",
        password="",
        use_digest_auth=False,
        shelly_request_timeout_seconds=2.0,
        pm_component="Switch",
        pm_id=0,
        phase="L1",
        max_current=16.0,
        _last_voltage=None,
    )


def _json_ready(value: Any) -> Any:
    """Convert dataclasses recursively to JSON-friendly structures."""
    if is_dataclass(value):
        return asdict(cast(Any, value))
    if isinstance(value, dict):
        return _json_ready_mapping(value)
    if isinstance(value, (list, tuple)):
        return _json_ready_sequence(value)
    return value


def _json_ready_mapping(value: dict[Any, Any]) -> dict[str, Any]:
    """Convert JSON object payloads recursively to stable string-keyed dicts."""
    return {str(key): _json_ready(item) for key, item in value.items()}


def _json_ready_sequence(value: list[Any] | tuple[Any, ...]) -> list[Any]:
    """Convert JSON arrays recursively to plain Python lists."""
    return [_json_ready(item) for item in value]


def validate_backend_config(path: str) -> dict[str, object]:
    """Validate backend config type and role compatibility without network I/O."""
    adapter_type = _adapter_type(path)
    valid_roles: list[str] = []
    if adapter_type in METER_BACKENDS:
        METER_BACKENDS[adapter_type](_probe_service(), config_path=path)
        valid_roles.append("meter")
    if adapter_type in SWITCH_BACKENDS:
        SWITCH_BACKENDS[adapter_type](_probe_service(), config_path=path)
        valid_roles.append("switch")
    if adapter_type in CHARGER_BACKENDS:
        CHARGER_BACKENDS[adapter_type](_probe_service(), config_path=path)
        valid_roles.append("charger")
    if not valid_roles:
        raise ValueError(f"Unsupported backend type '{adapter_type}'")
    return {
        "path": path,
        "type": adapter_type,
        "roles": valid_roles,
    }


def probe_meter_backend(path: str) -> dict[str, object]:
    """Read one sample meter result from the configured backend."""
    adapter_type = _adapter_type(path)
    constructor = METER_BACKENDS.get(adapter_type)
    if constructor is None:
        raise ValueError(f"Backend type '{adapter_type}' is not a meter backend")
    backend = constructor(_probe_service(), config_path=path)
    return {
        "path": path,
        "type": adapter_type,
        "meter": _json_ready(backend.read_meter()),
    }


def probe_switch_backend(path: str) -> dict[str, object]:
    """Read one sample switch state and capabilities from the configured backend."""
    adapter_type = _adapter_type(path)
    constructor = SWITCH_BACKENDS.get(adapter_type)
    if constructor is None:
        raise ValueError(f"Backend type '{adapter_type}' is not a switch backend")
    backend = constructor(_probe_service(), config_path=path)
    settings = getattr(backend, "settings", None)
    return {
        "path": path,
        "type": adapter_type,
        "capabilities": _json_ready(backend.capabilities()),
        "phase_switch_targets": _json_ready(getattr(settings, "phase_switch_targets", {})),
        "feedback_readback": _json_ready(getattr(settings, "feedback_readback", None)),
        "interlock_readback": _json_ready(getattr(settings, "interlock_readback", None)),
        "switch_state": _json_ready(backend.read_switch_state()),
    }


def probe_charger_backend(path: str) -> dict[str, object]:
    """Return normalized non-destructive charger backend config details."""
    adapter_type = _adapter_type(path)
    constructor = CHARGER_BACKENDS.get(adapter_type)
    if constructor is None:
        raise ValueError(f"Backend type '{adapter_type}' is not a charger backend")
    backend = constructor(_probe_service(), config_path=path)
    settings = getattr(backend, "settings", None)
    return {
        "path": path,
        "type": adapter_type,
        "supported_phase_selections": _json_ready(
            getattr(settings, "supported_phase_selections", ("P1",))
        ),
        "state_url": getattr(settings, "state_url", None),
        "state_actual_current_path": getattr(settings, "state_actual_current_path", None),
        "state_power_watts_path": getattr(settings, "state_power_watts_path", None),
        "state_energy_kwh_path": getattr(settings, "state_energy_kwh_path", None),
        "state_status_path": getattr(settings, "state_status_path", None),
        "state_fault_path": getattr(settings, "state_fault_path", None),
        "enable_url": getattr(settings, "enable_url", None),
        "current_url": getattr(settings, "current_url", None),
        "phase_url": getattr(settings, "phase_url", None),
    }


def main(argv: list[str] | None = None) -> int:
    """Run the backend probe CLI."""
    parser = argparse.ArgumentParser(description="Validate or probe wallbox backend configs")
    parser.add_argument("command", choices=("validate", "probe-meter", "probe-switch", "probe-charger"))
    parser.add_argument("config_path")
    args = parser.parse_args(argv)

    if args.command == "validate":
        payload = validate_backend_config(args.config_path)
    elif args.command == "probe-meter":
        payload = probe_meter_backend(args.config_path)
    elif args.command == "probe-switch":
        payload = probe_switch_backend(args.config_path)
    else:
        payload = probe_charger_backend(args.config_path)

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
