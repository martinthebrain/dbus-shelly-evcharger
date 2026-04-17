# SPDX-License-Identifier: GPL-3.0-or-later
"""Protocol definitions for meter, switch, and charger backends."""

from __future__ import annotations

from typing import Any, Protocol

from .models import (
    ChargerState,
    MeterReading,
    PhaseSelection,
    SwitchCapabilities,
    SwitchState,
)


class MeterBackend(Protocol):
    """Read normalized wallbox power and energy values."""

    def read_meter(self) -> MeterReading: ...


class SwitchBackend(Protocol):
    """Read and command normalized switching state."""

    def capabilities(self) -> SwitchCapabilities: ...

    def read_switch_state(self) -> SwitchState: ...

    def set_enabled(self, enabled: bool) -> None: ...

    def set_phase_selection(self, selection: PhaseSelection) -> None: ...


class ChargerBackend(Protocol):
    """Optional direct charger-control backend."""

    def read_charger_state(self) -> ChargerState: ...

    def set_enabled(self, enabled: bool) -> None: ...

    def set_current(self, amps: float) -> None: ...

    def set_phase_selection(self, selection: PhaseSelection) -> None: ...


class BackendConstructor(Protocol):
    """Constructor signature used by the backend registry/factory."""

    def __call__(self, service: Any, config_path: str = "") -> Any: ...
