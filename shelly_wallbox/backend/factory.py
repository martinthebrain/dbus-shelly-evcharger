# SPDX-License-Identifier: GPL-3.0-or-later
"""Factory for normalized meter/switch/charger backend objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from .base import ChargerBackend, MeterBackend, SwitchBackend
from .config import selection_from_service
from .registry import (
    create_charger_backend,
    create_meter_backend,
    create_switch_backend,
)
from .models import BackendSelection


@dataclass(frozen=True)
class ResolvedBackends:
    """One resolved backend bundle created from wallbox config."""

    selection: BackendSelection
    meter: MeterBackend | None
    switch: SwitchBackend | None
    charger: ChargerBackend | None


def _resolved_meter_backend(selection: BackendSelection, service: Any) -> MeterBackend | None:
    """Return the configured meter backend or validate that meterless mode is allowed."""
    if selection.meter_type == "none":
        if selection.mode != "split":
            raise ValueError("MeterType=none is only supported in split backend mode")
        return None
    return cast(
        MeterBackend,
        create_meter_backend(selection.meter_type, service, selection.meter_config_path),
    )


def _resolved_switch_backend(selection: BackendSelection, service: Any) -> SwitchBackend | None:
    """Return the configured switch backend or validate that switchless mode is allowed."""
    if selection.switch_type == "none":
        if selection.mode != "split":
            raise ValueError("SwitchType=none is only supported in split backend mode")
        return None
    return cast(
        SwitchBackend,
        create_switch_backend(selection.switch_type, service, selection.switch_config_path),
    )


def _resolved_charger_backend(selection: BackendSelection, service: Any) -> ChargerBackend | None:
    """Return the configured charger backend when present."""
    if selection.charger_type is None:
        return None
    return cast(
        ChargerBackend,
        create_charger_backend(selection.charger_type, service, selection.charger_config_path),
    )


def _validate_optional_split_backends(
    selection: BackendSelection,
    charger: ChargerBackend | None,
) -> None:
    """Ensure meterless or switchless split setups only run with a charger backend."""
    if selection.meter_type == "none" and charger is None:
        raise ValueError("MeterType=none requires a configured charger backend")
    if selection.switch_type == "none" and charger is None:
        raise ValueError("SwitchType=none requires a configured charger backend")


def build_service_backends(service: Any) -> ResolvedBackends:
    """Instantiate one normalized backend bundle from service config attrs."""
    selection = selection_from_service(service)
    meter = _resolved_meter_backend(selection, service)
    switch = _resolved_switch_backend(selection, service)
    charger = _resolved_charger_backend(selection, service)
    _validate_optional_split_backends(selection, charger)
    return ResolvedBackends(
        selection=selection,
        meter=meter,
        switch=switch,
        charger=charger,
    )
