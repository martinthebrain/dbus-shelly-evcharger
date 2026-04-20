# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared Shelly backend dataclasses reused by meter and switch helpers."""

from __future__ import annotations

from dataclasses import dataclass

from .models import PhaseSelection, SwitchingMode


@dataclass(frozen=True)
class ShellySignalReadbackSettings:
    """Optional Shelly RPC signal readback for feedback/interlock semantics."""

    component: str
    device_id: int
    value_path: str
    invert: bool


@dataclass(frozen=True)
class ShellyBackendSettings:
    """Normalized Shelly backend config independent from service defaults."""

    profile_name: str | None
    host: str
    component: str
    device_id: int
    timeout_seconds: float
    username: str
    password: str
    use_digest_auth: bool
    phase_selection: PhaseSelection
    switching_mode: SwitchingMode
    supported_phase_selections: tuple[PhaseSelection, ...]
    requires_charge_pause_for_phase_change: bool
    max_direct_switch_power_w: float | None
    phase_switch_targets: dict[PhaseSelection, tuple[int, ...]]
    feedback_readback: ShellySignalReadbackSettings | None
    interlock_readback: ShellySignalReadbackSettings | None
