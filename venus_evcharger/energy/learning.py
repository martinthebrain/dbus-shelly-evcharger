# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-only learning helpers for observed external energy behaviour."""

from __future__ import annotations

from typing import Mapping

from .models import EnergyLearningProfile, EnergySourceSnapshot


def update_energy_learning_profiles(
    existing: Mapping[str, EnergyLearningProfile] | None,
    sources: tuple[EnergySourceSnapshot, ...],
    now: float,
) -> dict[str, EnergyLearningProfile]:
    """Update simple observed maxima for charge/discharge/ac power per source."""
    profiles = dict(existing or {})
    for source in sources:
        previous = profiles.get(source.source_id, EnergyLearningProfile(source_id=source.source_id))
        observed_charge = source.charge_power_w
        observed_discharge = source.discharge_power_w
        observed_ac = None if source.ac_power_w is None else abs(float(source.ac_power_w))
        profiles[source.source_id] = EnergyLearningProfile(
            source_id=source.source_id,
            sample_count=int(previous.sample_count) + 1,
            observed_max_charge_power_w=_max_optional(previous.observed_max_charge_power_w, observed_charge),
            observed_max_discharge_power_w=_max_optional(previous.observed_max_discharge_power_w, observed_discharge),
            observed_max_ac_power_w=_max_optional(previous.observed_max_ac_power_w, observed_ac),
            last_change_at=float(now),
        )
    return profiles


def _max_optional(current: float | None, candidate: float | None) -> float | None:
    if candidate is None:
        return current
    if current is None:
        return float(candidate)
    return max(float(current), float(candidate))
