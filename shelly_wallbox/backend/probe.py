# SPDX-License-Identifier: GPL-3.0-or-later
"""CLI helper to validate and probe wallbox backend adapter configs."""

from __future__ import annotations

import argparse
import configparser
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import requests

from .factory import build_service_backends
from .config import load_backend_selection
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


def _probe_service_from_wallbox_config(config: configparser.ConfigParser) -> Any:
    """Return one small service stub seeded from a full wallbox config file."""
    defaults = config["DEFAULT"]
    selection = load_backend_selection(config)
    return SimpleNamespace(
        session=requests.Session(),
        host=defaults.get("Host", "").strip(),
        username=defaults.get("Username", "").strip(),
        password=defaults.get("Password", "").strip(),
        use_digest_auth=defaults.get("DigestAuth", "0").strip().lower() in ("1", "true", "yes", "on"),
        shelly_request_timeout_seconds=float(defaults.get("ShellyRequestTimeoutSeconds", "2.0") or 2.0),
        pm_component=defaults.get("ShellyComponent", "Switch").strip(),
        pm_id=int(defaults.get("ShellyId", "0") or 0),
        phase=defaults.get("Phase", "L1").strip(),
        max_current=float(defaults.get("MaxCurrent", "16.0") or 16.0),
        _last_voltage=None,
        backend_mode=selection.mode,
        meter_backend_type=selection.meter_type,
        switch_backend_type=selection.switch_type,
        charger_backend_type=selection.charger_type,
        meter_backend_config_path=selection.meter_config_path,
        switch_backend_config_path=selection.switch_config_path,
        charger_backend_config_path=selection.charger_config_path,
    )


def _json_ready(value: Any) -> Any:
    """Convert dataclasses recursively to JSON-friendly structures."""
    if is_dataclass(value):
        return _json_ready(asdict(cast(Any, value)))
    if isinstance(value, Path):
        return str(value)
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


def validate_wallbox_config(path: str) -> dict[str, object]:
    """Validate one full wallbox config including backend selection compatibility."""
    config = _config(path)
    service = _probe_service_from_wallbox_config(config)
    resolved = build_service_backends(service)
    return {
        "path": path,
        "selection": _json_ready(resolved.selection),
        "resolved_roles": {
            "meter": resolved.meter is not None,
            "switch": resolved.switch is not None,
            "charger": resolved.charger is not None,
        },
    }


def probe_meter_backend(path: str) -> dict[str, object]:
    """Read one sample meter result from the configured backend."""
    adapter_type = _adapter_type(path)
    constructor = METER_BACKENDS.get(adapter_type)
    if constructor is None:
        raise ValueError(f"Backend type '{adapter_type}' is not a meter backend")
    backend = constructor(_probe_service(), config_path=path)
    settings = getattr(backend, "settings", None)
    return {
        "path": path,
        "type": adapter_type,
        "shelly_profile": getattr(settings, "profile_name", None),
        "component": getattr(settings, "component", None),
        "device_id": getattr(settings, "device_id", None),
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
        "shelly_profile": getattr(settings, "profile_name", None),
        "component": getattr(settings, "component", None),
        "device_id": getattr(settings, "device_id", None),
        "capabilities": _json_ready(backend.capabilities()),
        "phase_switch_targets": _json_ready(getattr(settings, "phase_switch_targets", {})),
        "phase_members": _json_ready(getattr(settings, "phase_members", {})),
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
        "profile_name": getattr(settings, "profile_name", None),
        "transport_kind": getattr(getattr(settings, "transport_settings", None), "transport_kind", None),
        "transport_unit_id": getattr(getattr(settings, "transport_settings", None), "unit_id", None),
        "transport_device": getattr(getattr(settings, "transport_settings", None), "device", None),
        "transport_timeout_seconds": getattr(getattr(settings, "transport_settings", None), "timeout_seconds", None),
        "transport_serial_port_owner": getattr(
            getattr(settings, "transport_settings", None), "serial_port_owner", None
        ),
        "transport_serial_retry_count": getattr(
            getattr(settings, "transport_settings", None), "serial_retry_count", None
        ),
        "transport_serial_retry_delay_seconds": getattr(
            getattr(settings, "transport_settings", None), "serial_retry_delay_seconds", None
        ),
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


def read_charger_backend(path: str) -> dict[str, object]:
    """Read one live charger-state sample through the configured backend."""
    payload = probe_charger_backend(path)
    adapter_type = _adapter_type(path)
    constructor = CHARGER_BACKENDS.get(adapter_type)
    if constructor is None:
        raise ValueError(f"Backend type '{adapter_type}' is not a charger backend")
    backend = constructor(_probe_service(), config_path=path)
    payload["charger_state"] = _json_ready(backend.read_charger_state())
    return payload


def main(argv: list[str] | None = None) -> int:
    """Run the backend probe CLI."""
    parser = argparse.ArgumentParser(description="Validate or probe wallbox backend configs")
    parser.add_argument(
        "command",
        choices=("validate", "validate-wallbox", "probe-meter", "probe-switch", "probe-charger", "read-charger"),
    )
    parser.add_argument("config_path")
    args = parser.parse_args(argv)

    if args.command == "validate":
        payload = validate_backend_config(args.config_path)
    elif args.command == "validate-wallbox":
        payload = validate_wallbox_config(args.config_path)
    elif args.command == "probe-meter":
        payload = probe_meter_backend(args.config_path)
    elif args.command == "probe-switch":
        payload = probe_switch_backend(args.config_path)
    elif args.command == "read-charger":
        payload = read_charger_backend(args.config_path)
    else:
        payload = probe_charger_backend(args.config_path)

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
