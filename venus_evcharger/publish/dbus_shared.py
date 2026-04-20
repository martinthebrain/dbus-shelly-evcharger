# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared DBus publish types and learned-current inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias

PublishStateEntry: TypeAlias = dict[str, Any]
PhaseMeasurement: TypeAlias = dict[str, float]
PhaseData: TypeAlias = dict[str, PhaseMeasurement]
PublishServiceValueSnapshot: TypeAlias = tuple[bool, Any]


@dataclass(frozen=True)
class _LearnedDisplayCurrentInputs:
    """Stable learned charging-power inputs used for SetCurrent display derivation."""

    power_w: float
    phase_voltage_v: float
    phase_count: float
