# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-only learning helpers for observed external energy behaviour."""

from __future__ import annotations

from typing import Any, Mapping

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


def summarize_energy_learning_profiles(
    profiles: Mapping[str, EnergyLearningProfile | Mapping[str, Any]] | None,
) -> dict[str, float | int | None]:
    """Return one compact aggregate summary for learned source behaviour."""
    normalized_profiles = tuple(_normalized_profile_iter(profiles))
    return {
        "profile_count": len(normalized_profiles),
        "observed_max_charge_power_w": _sum_optional(
            profile.observed_max_charge_power_w for profile in normalized_profiles
        ),
        "observed_max_discharge_power_w": _sum_optional(
            profile.observed_max_discharge_power_w for profile in normalized_profiles
        ),
        "observed_max_ac_power_w": _sum_optional(
            profile.observed_max_ac_power_w for profile in normalized_profiles
        ),
        "sample_count": sum(int(profile.sample_count) for profile in normalized_profiles),
    }


def _max_optional(current: float | None, candidate: float | None) -> float | None:
    if candidate is None:
        return current
    if current is None:
        return float(candidate)
    return max(float(current), float(candidate))


def _normalized_profile_iter(
    profiles: Mapping[str, EnergyLearningProfile | Mapping[str, Any]] | None,
) -> tuple[EnergyLearningProfile, ...]:
    normalized_profiles: list[EnergyLearningProfile] = []
    for source_id, raw_profile in dict(profiles or {}).items():
        if isinstance(raw_profile, EnergyLearningProfile):
            normalized_profiles.append(raw_profile)
            continue
        normalized_profiles.append(
            EnergyLearningProfile(
                source_id=str(raw_profile.get("source_id", source_id)),
                sample_count=int(raw_profile.get("sample_count", 0) or 0),
                observed_max_charge_power_w=_optional_float(raw_profile.get("observed_max_charge_power_w")),
                observed_max_discharge_power_w=_optional_float(raw_profile.get("observed_max_discharge_power_w")),
                observed_max_ac_power_w=_optional_float(raw_profile.get("observed_max_ac_power_w")),
                last_change_at=_optional_float(raw_profile.get("last_change_at")),
            )
        )
    return tuple(normalized_profiles)


def _sum_optional(values: Any) -> float | None:
    numeric_values = [float(value) for value in values if value is not None]
    if not numeric_values:
        return None
    return sum(numeric_values)


def _optional_float(value: object) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return float(value)
