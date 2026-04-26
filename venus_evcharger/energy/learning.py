# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-only learning helpers for observed external energy behaviour."""

from __future__ import annotations

import time
from typing import Any, Mapping

from .learning_coercion import _normalized_profile_iter
from .learning_summary import _summarize_normalized_energy_learning_profiles
from .learning_update import _build_updated_learning_profile
from .models import EnergyLearningProfile, EnergySourceSnapshot

_DAY_START_HOUR = 6
_DAY_END_HOUR = 22


def update_energy_learning_profiles(
    existing: Mapping[str, EnergyLearningProfile | Mapping[str, Any]] | None,
    sources: tuple[EnergySourceSnapshot, ...],
    now: float,
) -> dict[str, EnergyLearningProfile]:
    """Update richer runtime-only behaviour metrics for each observed source."""
    profiles = {profile.source_id: profile for profile in _normalized_profile_iter(existing)}
    for source in sources:
        previous = profiles.get(source.source_id, EnergyLearningProfile(source_id=source.source_id))
        profiles[source.source_id] = _build_updated_learning_profile(previous, source, now, _sample_period)
    return profiles


def summarize_energy_learning_profiles(
    profiles: Mapping[str, EnergyLearningProfile | Mapping[str, Any]] | None,
) -> dict[str, float | int | None]:
    """Return one compact aggregate summary for learned source behaviour."""
    return _summarize_normalized_energy_learning_profiles(_normalized_profile_iter(profiles))


def _sample_period(now: float) -> str:
    hour = int(time.localtime(float(now)).tm_hour)
    return "day" if _DAY_START_HOUR <= hour < _DAY_END_HOUR else "night"
