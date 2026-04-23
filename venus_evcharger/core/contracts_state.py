# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for the local State API v1 payloads."""

from __future__ import annotations

from venus_evcharger.core.contracts_state_endpoints import (
    _normalized_state_health_mapping,
    _normalized_state_update_mapping,
    normalized_state_api_config_effective_fields,
    normalized_state_api_dbus_diagnostics_fields,
    normalized_state_api_health_fields,
    normalized_state_api_topology_fields,
    normalized_state_api_update_fields,
)
from venus_evcharger.core.contracts_state_operational import (
    normalized_state_api_operational_fields,
    normalized_state_api_operational_state_fields,
)
from venus_evcharger.core.contracts_state_shared import (
    STATE_API_KINDS,
    STATE_API_VERSIONS,
    _normalized_generic_mapping,
    _normalized_state_mapping_fields,
    _normalized_text,
    _optional_float,
    normalized_state_api_kind,
    normalized_state_api_runtime_fields,
    normalized_state_api_summary_fields,
    normalized_state_api_version,
)


__all__ = [
    "STATE_API_KINDS",
    "STATE_API_VERSIONS",
    "_normalized_generic_mapping",
    "_normalized_state_health_mapping",
    "_normalized_state_mapping_fields",
    "_normalized_state_update_mapping",
    "_normalized_text",
    "_optional_float",
    "normalized_state_api_config_effective_fields",
    "normalized_state_api_dbus_diagnostics_fields",
    "normalized_state_api_health_fields",
    "normalized_state_api_kind",
    "normalized_state_api_operational_fields",
    "normalized_state_api_operational_state_fields",
    "normalized_state_api_runtime_fields",
    "normalized_state_api_summary_fields",
    "normalized_state_api_topology_fields",
    "normalized_state_api_update_fields",
    "normalized_state_api_version",
]
