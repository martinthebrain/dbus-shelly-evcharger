# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for the local State API v1 payloads."""

from __future__ import annotations

from typing import Any, Mapping

from venus_evcharger.core.contracts_basic import (
    non_negative_float_or_none,
    non_negative_int,
    normalize_binary_flag,
    normalized_auto_state_pair,
)
from venus_evcharger.core.contracts_outward import normalized_software_update_state_fields

STATE_API_VERSIONS = frozenset({"v1"})
STATE_API_KINDS = frozenset(
    {
        "build",
        "contracts",
        "healthz",
        "summary",
        "runtime",
        "operational",
        "dbus-diagnostics",
        "topology",
        "update",
        "config-effective",
        "health",
        "version",
    }
)


def _normalized_text(value: Any, default: str = "") -> str:
    text = "" if value is None else str(value).strip()
    return text or default


def normalized_state_api_version(value: Any) -> str:
    version = _normalized_text(value)
    return version if version in STATE_API_VERSIONS else "v1"


def normalized_state_api_kind(value: Any, *, default: str = "summary") -> str:
    normalized_default = default if default in STATE_API_KINDS else "summary"
    kind = _normalized_text(value).lower()
    return kind if kind in STATE_API_KINDS else normalized_default


def normalized_state_api_summary_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    return {
        "ok": bool(normalize_binary_flag(raw.get("ok", 1))),
        "api_version": normalized_state_api_version(raw.get("api_version")),
        "kind": normalized_state_api_kind(raw.get("kind"), default="summary"),
        "summary": _normalized_text(raw.get("summary")),
    }


def normalized_state_api_runtime_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    state = raw.get("state")
    return {
        "ok": bool(normalize_binary_flag(raw.get("ok", 1))),
        "api_version": normalized_state_api_version(raw.get("api_version")),
        "kind": normalized_state_api_kind(raw.get("kind"), default="runtime"),
        "state": dict(state) if isinstance(state, Mapping) else {},
    }


def _normalized_generic_mapping(value: Any) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized[str(key)] = item
    return normalized


def _normalized_state_mapping_fields(payload: Mapping[str, Any] | None, *, kind: str) -> dict[str, Any]:
    raw = dict(payload or {})
    return {
        "ok": bool(normalize_binary_flag(raw.get("ok", 1))),
        "api_version": normalized_state_api_version(raw.get("api_version")),
        "kind": normalized_state_api_kind(raw.get("kind"), default=kind),
        "state": _normalized_generic_mapping(raw.get("state")),
    }


def normalized_state_api_operational_state_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    auto_state, auto_state_code = normalized_auto_state_pair(raw.get("auto_state"), raw.get("auto_state_code"))
    software_update_state, software_update_state_code, software_update_available, software_update_no_update_active = (
        normalized_software_update_state_fields(
            raw.get("software_update_state"),
            raw.get("software_update_available"),
            raw.get("software_update_no_update_active"),
        )
    )
    return {
        "mode": non_negative_int(raw.get("mode")),
        "enable": normalize_binary_flag(raw.get("enable")),
        "startstop": normalize_binary_flag(raw.get("startstop")),
        "autostart": normalize_binary_flag(raw.get("autostart")),
        "active_phase_selection": _normalized_text(raw.get("active_phase_selection"), "P1"),
        "requested_phase_selection": _normalized_text(raw.get("requested_phase_selection"), "P1"),
        "backend_mode": _normalized_text(raw.get("backend_mode"), "combined"),
        "meter_backend": _normalized_text(raw.get("meter_backend"), "na"),
        "switch_backend": _normalized_text(raw.get("switch_backend"), "na"),
        "charger_backend": _normalized_text(raw.get("charger_backend"), "na"),
        "auto_state": auto_state,
        "auto_state_code": auto_state_code,
        "fault_active": normalize_binary_flag(raw.get("fault_active")),
        "fault_reason": _normalized_text(raw.get("fault_reason"), "na"),
        "software_update_state": software_update_state,
        "software_update_state_code": software_update_state_code,
        "software_update_available": software_update_available,
        "software_update_no_update_active": software_update_no_update_active,
        "runtime_overrides_active": normalize_binary_flag(raw.get("runtime_overrides_active")),
        "runtime_overrides_path": _normalized_text(raw.get("runtime_overrides_path")),
        "combined_battery_soc": non_negative_float_or_none(raw.get("combined_battery_soc")),
        "combined_battery_source_count": non_negative_int(raw.get("combined_battery_source_count")),
        "combined_battery_online_source_count": non_negative_int(raw.get("combined_battery_online_source_count")),
        "combined_battery_charge_power_w": non_negative_float_or_none(raw.get("combined_battery_charge_power_w")),
        "combined_battery_discharge_power_w": non_negative_float_or_none(raw.get("combined_battery_discharge_power_w")),
    }


def normalized_state_api_operational_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    state = raw.get("state")
    return {
        "ok": bool(normalize_binary_flag(raw.get("ok", 1))),
        "api_version": normalized_state_api_version(raw.get("api_version")),
        "kind": normalized_state_api_kind(raw.get("kind"), default="operational"),
        "state": normalized_state_api_operational_state_fields(state if isinstance(state, Mapping) else {}),
    }


def normalized_state_api_dbus_diagnostics_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    state = raw.get("state")
    normalized_state = _normalized_generic_mapping(state)
    return {
        "ok": bool(normalize_binary_flag(raw.get("ok", 1))),
        "api_version": normalized_state_api_version(raw.get("api_version")),
        "kind": normalized_state_api_kind(raw.get("kind"), default="dbus-diagnostics"),
        "state": normalized_state,
    }


def normalized_state_api_topology_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return _normalized_state_mapping_fields(payload, kind="topology")


def _normalized_state_update_mapping(value: Any) -> dict[str, Any]:
    state = _normalized_generic_mapping(value)
    for key in (
        "last_check_at",
        "last_run_at",
        "next_check_at",
        "boot_auto_due_at",
        "run_requested_at",
    ):
        if key in state:
            state[key] = non_negative_float_or_none(state.get(key))
    for key in ("available", "no_update_active"):
        if key in state:
            state[key] = bool(normalize_binary_flag(state.get(key)))
    return state


def normalized_state_api_update_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = _normalized_state_mapping_fields(payload, kind="update")
    normalized["state"] = _normalized_state_update_mapping(normalized.get("state"))
    return normalized


def normalized_state_api_config_effective_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return _normalized_state_mapping_fields(payload, kind="config-effective")


def _normalized_state_health_mapping(value: Any) -> dict[str, Any]:
    state = _normalized_generic_mapping(value)
    for key in ("health_code", "listen_port"):
        if key in state:
            state[key] = non_negative_int(state.get(key))
    for key in (
        "fault_active",
        "runtime_overrides_active",
        "control_api_enabled",
        "control_api_running",
        "control_api_localhost_only",
        "update_stale",
    ):
        if key in state:
            state[key] = bool(normalize_binary_flag(state.get(key)))
    return state


def normalized_state_api_health_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = _normalized_state_mapping_fields(payload, kind="health")
    normalized["state"] = _normalized_state_health_mapping(normalized.get("state"))
    return normalized
