# SPDX-License-Identifier: GPL-3.0-or-later
"""Factory for normalized meter/switch/charger backend objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


def build_service_backends(service: Any) -> ResolvedBackends:
    """Instantiate one normalized backend bundle from service config attrs."""
    selection = selection_from_service(service)
    meter = None
    if selection.meter_type != "none":
        meter = create_meter_backend(selection.meter_type, service, selection.meter_config_path)
    elif selection.mode != "split":
        raise ValueError("MeterType=none is only supported in split backend mode")
    charger = (
        create_charger_backend(selection.charger_type, service, selection.charger_config_path)
        if selection.charger_type is not None
        else None
    )
    switch = None
    if selection.switch_type != "none":
        switch = create_switch_backend(selection.switch_type, service, selection.switch_config_path)
    elif selection.mode != "split":
        raise ValueError("SwitchType=none is only supported in split backend mode")
    if selection.meter_type == "none" and charger is None:
        raise ValueError("MeterType=none requires a configured charger backend")
    if selection.switch_type == "none" and charger is None:
        raise ValueError("SwitchType=none requires a configured charger backend")
    return ResolvedBackends(
        selection=selection,
        meter=meter,
        switch=switch,
        charger=charger,
    )
