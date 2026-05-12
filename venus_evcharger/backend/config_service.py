# SPDX-License-Identifier: GPL-3.0-or-later
"""Service-facing backend summary helpers."""

from __future__ import annotations

import configparser
from typing import Any

from venus_evcharger.topology.schema import EvChargerTopologyConfig

from .models import BackendRuntimeSummary
from .config import (
    DEFAULT_COMBINED_METER_TYPE,
    DEFAULT_COMBINED_SWITCH_TYPE,
    _build_runtime_summary,
    _label_or_default,
    _legacy_service_role_value,
    _legacy_view_role_from_runtime,
    _normalized_text_or_default,
    _role_field_name,
    _runtime_summary_from_legacy_service_attrs,
    _runtime_summary_from_topology,
    _service_has_legacy_backend_attrs,
    _summary_role_value,
    _topology_backend_label,
    _type_from_service_config,
    load_runtime_backend_summary,
    normalize_backend_mode,
    normalize_config_path,
    normalize_optional_backend_type,
)


def _runtime_backend_summary_from_service(service: Any) -> BackendRuntimeSummary | None:
    """Return one runtime-facing backend summary already attached to the service."""
    bundle = getattr(service, "_backend_bundle", None)
    runtime = getattr(bundle, "runtime", None)
    return runtime if isinstance(runtime, BackendRuntimeSummary) else runtime if runtime is not None else None


def _topology_from_service_runtime(service: Any) -> EvChargerTopologyConfig | None:
    """Return one normalized topology config already attached to the service."""
    topology = getattr(service, "_topology_config", None)
    return topology if isinstance(topology, EvChargerTopologyConfig) else None


def backend_mode_for_service(service: Any, default: str = "combined") -> str:
    """Return one outward backend mode preferring resolved runtime state."""
    runtime = _runtime_backend_summary_from_service(service)
    if runtime is not None:
        return _normalized_text_or_default(getattr(runtime, "backend_mode", default), default)
    topology = _topology_from_service_runtime(service)
    if topology is not None:
        return _runtime_summary_from_topology(topology).backend_mode
    service_config = getattr(service, "config", None)
    if isinstance(service_config, configparser.ConfigParser):
        return load_runtime_backend_summary(service_config).backend_mode
    return _normalized_text_or_default(getattr(service, "backend_mode", default), default)


def backend_type_for_service(service: Any, role: str, default: str = "") -> str:
    """Return one outward backend type preferring resolved runtime state."""
    normalized_role = role.strip().lower()
    runtime = _runtime_backend_summary_from_service(service)
    if runtime is not None:
        return _summary_role_value(runtime, normalized_role, default)
    topology = _topology_from_service_runtime(service)
    if topology is not None:
        return _label_or_default(_topology_backend_label(topology, normalized_role), default)
    service_config = getattr(service, "config", None)
    if isinstance(service_config, configparser.ConfigParser):
        return _type_from_service_config(service_config, normalized_role, default)
    return _legacy_service_role_value(service, normalized_role, default)


def runtime_summary_from_service(service: Any) -> BackendRuntimeSummary:
    """Return one normalized runtime summary from the service's current truth source."""
    runtime = _runtime_backend_summary_from_service(service)
    if runtime is not None:
        return runtime
    topology = _topology_from_service_runtime(service)
    if topology is not None:
        return _runtime_summary_from_topology(topology)
    service_config = getattr(service, "config", None)
    if isinstance(service_config, configparser.ConfigParser):
        return load_runtime_backend_summary(service_config)
    if _service_has_legacy_backend_attrs(service):
        return _runtime_summary_from_legacy_service_attrs(service)
    return _build_runtime_summary(
        backend_mode="combined",
        meter_type=DEFAULT_COMBINED_METER_TYPE,
        meter_config_path=None,
        switch_type=DEFAULT_COMBINED_SWITCH_TYPE,
        switch_config_path=None,
        charger_type=None,
        charger_config_path=None,
        legacy_host=getattr(service, "host", ""),
    )


def compat_legacy_backend_view_from_runtime(runtime: BackendRuntimeSummary | Any) -> dict[str, object] | None:
    """Return one legacy-shaped backend view reconstructed from runtime data."""
    if runtime is None or not hasattr(runtime, "backend_mode"):
        return None
    mode = normalize_backend_mode(getattr(runtime, "backend_mode", "combined"))
    return {
        "mode": mode,
        "meter_type": _legacy_view_role_from_runtime(mode, "meter", getattr(runtime, "meter_type", None)),
        "switch_type": _legacy_view_role_from_runtime(mode, "switch", getattr(runtime, "switch_type", None)),
        "charger_type": normalize_optional_backend_type(getattr(runtime, "charger_type", None)),
        "meter_config_path": normalize_config_path(getattr(runtime, "meter_config_path", "")),
        "switch_config_path": normalize_config_path(getattr(runtime, "switch_config_path", "")),
        "charger_config_path": normalize_config_path(getattr(runtime, "charger_config_path", "")),
    }


def compat_legacy_backend_view_from_config(config: configparser.ConfigParser) -> dict[str, object]:
    """Return one legacy-shaped backend view reconstructed from config."""
    return compat_legacy_backend_view_from_runtime(load_runtime_backend_summary(config)) or {}
