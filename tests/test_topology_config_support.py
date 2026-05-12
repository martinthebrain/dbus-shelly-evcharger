# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from venus_evcharger.backend.config import (
    _adapter_type_from_config_path,
    _build_runtime_summary,
    _native_meter_type_for_actuator,
    _runtime_summary_from_topology,
    _topology_backend_label,
    backend_mode_for_service,
    backend_type_for_service,
    compat_legacy_backend_view_from_config,
    compat_legacy_backend_view_from_runtime,
    load_runtime_backend_summary,
    runtime_summary_from_service,
    runtime_summary_is_configured,
    runtime_summary_uses_legacy_primary_rpc,
)
from venus_evcharger.backend.models import BackendRuntimeSummary
from venus_evcharger.topology.config import (
    _legacy_hybrid_measurement_config,
    _legacy_measurement_config,
    _legacy_native_measurement_config,
    _legacy_runtime_values,
    _optional_text,
    _validate_measurement,
    TopologyConfigError,
    legacy_topology_from_config,
    parse_topology_config,
)
from venus_evcharger.topology.schema import (
    EvChargerTopologyConfig,
    MeasurementConfig,
    PolicyConfig,
    TopologyConfig,
)


__all__ = [name for name in globals() if not name.startswith("__")]
