# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime-only learning helpers for observed external energy behaviour."""

from __future__ import annotations

import time
from typing import Any, Mapping, TypedDict

from .models import EnergyLearningProfile, EnergySourceSnapshot

_ACTIVE_POWER_THRESHOLD_W = 50.0
_GRID_ACTIVITY_THRESHOLD_W = 100.0
_DAY_START_HOUR = 6
_DAY_END_HOUR = 22


class _SampleUpdates(TypedDict):
    sample_count: int
    active_sample_count: int
    charge_sample_count: int
    discharge_sample_count: int
    import_support_sample_count: int
    import_charge_sample_count: int
    export_charge_sample_count: int
    export_discharge_sample_count: int
    export_idle_sample_count: int
    day_active_sample_count: int
    night_active_sample_count: int
    day_charge_sample_count: int
    night_charge_sample_count: int
    day_discharge_sample_count: int
    night_discharge_sample_count: int
    response_sample_count: int
    smoothing_sample_count: int


class _ObservationUpdates(TypedDict):
    observed_max_charge_power_w: float | None
    observed_max_discharge_power_w: float | None
    observed_max_ac_power_w: float | None
    observed_max_pv_input_power_w: float | None
    observed_max_grid_import_w: float | None
    observed_max_grid_export_w: float | None
    observed_min_discharge_soc: float | None
    observed_max_charge_soc: float | None


class _ActivityUpdates(TypedDict):
    average_active_charge_power_w: float | None
    average_active_discharge_power_w: float | None
    average_active_power_delta_w: float | None
    typical_response_delay_seconds: float | None
    direction_change_count: int
    last_direction: str
    last_activity_state: str
    last_active_at: float | None
    last_inactive_at: float | None
    last_change_at: float | None


class _ActivityMarkers(TypedDict):
    last_direction: str
    last_activity_state: str
    last_active_at: float | None
    last_inactive_at: float | None
    last_change_at: float | None


def update_energy_learning_profiles(
    existing: Mapping[str, EnergyLearningProfile | Mapping[str, Any]] | None,
    sources: tuple[EnergySourceSnapshot, ...],
    now: float,
) -> dict[str, EnergyLearningProfile]:
    """Update richer runtime-only behaviour metrics for each observed source."""
    profiles = {profile.source_id: profile for profile in _normalized_profile_iter(existing)}
    for source in sources:
        previous = profiles.get(source.source_id, EnergyLearningProfile(source_id=source.source_id))
        profiles[source.source_id] = _updated_learning_profile(previous, source, now)
    return profiles


def summarize_energy_learning_profiles(
    profiles: Mapping[str, EnergyLearningProfile | Mapping[str, Any]] | None,
) -> dict[str, float | int | None]:
    """Return one compact aggregate summary for learned source behaviour."""
    normalized_profiles = tuple(_normalized_profile_iter(profiles))
    counts = _profile_count_totals(normalized_profiles)
    power_summaries = _profile_power_summaries(normalized_profiles)
    average_summaries = _profile_average_summaries(normalized_profiles)
    bias_summaries = _profile_bias_summaries(counts)
    return {
        "profile_count": len(normalized_profiles),
        "sample_count": counts["sample_count"],
        "active_sample_count": counts["active_sample_count"],
        "direction_change_count": counts["direction_change_count"],
        "day_active_sample_count": counts["day_active_count"],
        "night_active_sample_count": counts["night_active_count"],
        **power_summaries,
        **average_summaries,
        **bias_summaries,
        **_profile_reserve_summaries(normalized_profiles),
    }


def _updated_learning_profile(
    previous: EnergyLearningProfile,
    source: EnergySourceSnapshot,
    now: float,
) -> EnergyLearningProfile:
    (
        direction,
        active,
        charge_power,
        discharge_power,
        observed_ac,
        observed_pv_input,
        grid_import,
        grid_export,
    ) = _source_learning_metrics(source)
    response_delay = _activation_response_delay(previous, active, direction, now)
    sample_updates = _profile_sample_updates(
        previous,
        active,
        direction,
        grid_import,
        grid_export,
        response_delay,
        _sample_period(now),
        _smoothing_delta(previous, direction, charge_power, discharge_power),
    )
    observation_updates = _profile_observation_updates(
        previous,
        source,
        direction,
        charge_power,
        discharge_power,
        observed_ac,
        observed_pv_input,
        grid_import,
        grid_export,
    )
    activity_updates = _profile_activity_updates(
        previous,
        direction,
        active,
        now,
        charge_power,
        discharge_power,
        response_delay,
        sample_updates["smoothing_sample_count"],
    )
    return EnergyLearningProfile(
        source_id=source.source_id,
        sample_count=sample_updates["sample_count"],
        active_sample_count=sample_updates["active_sample_count"],
        charge_sample_count=sample_updates["charge_sample_count"],
        discharge_sample_count=sample_updates["discharge_sample_count"],
        import_support_sample_count=sample_updates["import_support_sample_count"],
        import_charge_sample_count=sample_updates["import_charge_sample_count"],
        export_charge_sample_count=sample_updates["export_charge_sample_count"],
        export_discharge_sample_count=sample_updates["export_discharge_sample_count"],
        export_idle_sample_count=sample_updates["export_idle_sample_count"],
        day_active_sample_count=sample_updates["day_active_sample_count"],
        night_active_sample_count=sample_updates["night_active_sample_count"],
        day_charge_sample_count=sample_updates["day_charge_sample_count"],
        night_charge_sample_count=sample_updates["night_charge_sample_count"],
        day_discharge_sample_count=sample_updates["day_discharge_sample_count"],
        night_discharge_sample_count=sample_updates["night_discharge_sample_count"],
        response_sample_count=sample_updates["response_sample_count"],
        smoothing_sample_count=sample_updates["smoothing_sample_count"],
        observed_max_charge_power_w=observation_updates["observed_max_charge_power_w"],
        observed_max_discharge_power_w=observation_updates["observed_max_discharge_power_w"],
        observed_max_ac_power_w=observation_updates["observed_max_ac_power_w"],
        observed_max_pv_input_power_w=observation_updates["observed_max_pv_input_power_w"],
        observed_max_grid_import_w=observation_updates["observed_max_grid_import_w"],
        observed_max_grid_export_w=observation_updates["observed_max_grid_export_w"],
        observed_min_discharge_soc=observation_updates["observed_min_discharge_soc"],
        observed_max_charge_soc=observation_updates["observed_max_charge_soc"],
        average_active_charge_power_w=activity_updates["average_active_charge_power_w"],
        average_active_discharge_power_w=activity_updates["average_active_discharge_power_w"],
        average_active_power_delta_w=activity_updates["average_active_power_delta_w"],
        typical_response_delay_seconds=activity_updates["typical_response_delay_seconds"],
        direction_change_count=activity_updates["direction_change_count"],
        last_direction=activity_updates["last_direction"],
        last_activity_state=activity_updates["last_activity_state"],
        last_active_at=activity_updates["last_active_at"],
        last_inactive_at=activity_updates["last_inactive_at"],
        last_change_at=activity_updates["last_change_at"],
    )


def _source_learning_metrics(
    source: EnergySourceSnapshot,
) -> tuple[str, bool, float | None, float | None, float | None, float | None, float | None, float | None]:
    direction = _direction(source)
    return (
        direction,
        _is_active(source, direction),
        source.charge_power_w,
        source.discharge_power_w,
        _absolute_optional(source.ac_power_w),
        _positive_optional(source.pv_input_power_w),
        _positive_optional(source.grid_interaction_w),
        _positive_optional(None if source.grid_interaction_w is None else max(0.0, -float(source.grid_interaction_w))),
    )


def _grid_activity_match(
    current_direction: str,
    grid_value: float | None,
    expected_direction: str,
) -> bool:
    return current_direction == expected_direction and (grid_value or 0.0) >= _GRID_ACTIVITY_THRESHOLD_W


def _profile_count_totals(
    normalized_profiles: tuple[EnergyLearningProfile, ...],
) -> dict[str, int]:
    return {
        "sample_count": _sum_profile_int(normalized_profiles, "sample_count"),
        "active_sample_count": _sum_profile_int(normalized_profiles, "active_sample_count"),
        "direction_change_count": _sum_profile_int(normalized_profiles, "direction_change_count"),
        "day_active_count": _sum_profile_int(normalized_profiles, "day_active_sample_count"),
        "night_active_count": _sum_profile_int(normalized_profiles, "night_active_sample_count"),
        "charge_count": _sum_profile_int(normalized_profiles, "charge_sample_count"),
        "discharge_count": _sum_profile_int(normalized_profiles, "discharge_sample_count"),
        "import_support_count": _sum_profile_int(normalized_profiles, "import_support_sample_count"),
        "import_charge_count": _sum_profile_int(normalized_profiles, "import_charge_sample_count"),
        "export_charge_count": _sum_profile_int(normalized_profiles, "export_charge_sample_count"),
        "export_discharge_count": _sum_profile_int(normalized_profiles, "export_discharge_sample_count"),
        "export_idle_count": _sum_profile_int(normalized_profiles, "export_idle_sample_count"),
        "day_charge_count": _sum_profile_int(normalized_profiles, "day_charge_sample_count"),
        "night_charge_count": _sum_profile_int(normalized_profiles, "night_charge_sample_count"),
        "day_discharge_count": _sum_profile_int(normalized_profiles, "day_discharge_sample_count"),
        "night_discharge_count": _sum_profile_int(normalized_profiles, "night_discharge_sample_count"),
    }


def _mean_optional(values: Any) -> float | None:
    numeric_values = [float(value) for value in values if value is not None]
    if not numeric_values:
        return None
    return sum(numeric_values) / float(len(numeric_values))


def _bias_from_counts(positive_count: int, negative_count: int) -> float | None:
    total = int(positive_count) + int(negative_count)
    if total <= 0:
        return None
    return (float(positive_count) - float(negative_count)) / float(total)


def _profile_sample_updates(
    previous: EnergyLearningProfile,
    active: bool,
    direction: str,
    grid_import: float | None,
    grid_export: float | None,
    response_delay: float | None,
    period: str,
    smoothing_delta_w: float | None,
) -> _SampleUpdates:
    direction_increments = _direction_sample_increments(direction)
    grid_increments = _grid_sample_increments(direction, grid_import, grid_export)
    period_increments = _period_sample_increments(active, direction, period)
    return {
        "sample_count": int(previous.sample_count) + 1,
        "active_sample_count": int(previous.active_sample_count) + (1 if active else 0),
        "charge_sample_count": int(previous.charge_sample_count) + direction_increments["charge"],
        "discharge_sample_count": int(previous.discharge_sample_count) + direction_increments["discharge"],
        "import_support_sample_count": int(previous.import_support_sample_count) + grid_increments["import_support"],
        "import_charge_sample_count": int(previous.import_charge_sample_count) + grid_increments["import_charge"],
        "export_charge_sample_count": int(previous.export_charge_sample_count) + grid_increments["export_charge"],
        "export_discharge_sample_count": int(previous.export_discharge_sample_count) + grid_increments["export_discharge"],
        "export_idle_sample_count": int(previous.export_idle_sample_count) + grid_increments["export_idle"],
        "day_active_sample_count": int(previous.day_active_sample_count) + period_increments["day_active"],
        "night_active_sample_count": int(previous.night_active_sample_count) + period_increments["night_active"],
        "day_charge_sample_count": int(previous.day_charge_sample_count) + period_increments["day_charge"],
        "night_charge_sample_count": int(previous.night_charge_sample_count) + period_increments["night_charge"],
        "day_discharge_sample_count": int(previous.day_discharge_sample_count) + period_increments["day_discharge"],
        "night_discharge_sample_count": int(previous.night_discharge_sample_count) + period_increments["night_discharge"],
        "response_sample_count": int(previous.response_sample_count) + (1 if response_delay is not None else 0),
        "smoothing_sample_count": int(previous.smoothing_sample_count) + (1 if smoothing_delta_w is not None else 0),
    }


def _profile_observation_updates(
    previous: EnergyLearningProfile,
    source: EnergySourceSnapshot,
    direction: str,
    charge_power: float | None,
    discharge_power: float | None,
    observed_ac: float | None,
    observed_pv_input: float | None,
    grid_import: float | None,
    grid_export: float | None,
) -> _ObservationUpdates:
    return {
        "observed_max_charge_power_w": _max_optional(previous.observed_max_charge_power_w, charge_power),
        "observed_max_discharge_power_w": _max_optional(previous.observed_max_discharge_power_w, discharge_power),
        "observed_max_ac_power_w": _max_optional(previous.observed_max_ac_power_w, observed_ac),
        "observed_max_pv_input_power_w": _max_optional(previous.observed_max_pv_input_power_w, observed_pv_input),
        "observed_max_grid_import_w": _max_optional(previous.observed_max_grid_import_w, grid_import),
        "observed_max_grid_export_w": _max_optional(previous.observed_max_grid_export_w, grid_export),
        "observed_min_discharge_soc": _min_optional(
            previous.observed_min_discharge_soc,
            source.soc if direction == "discharge" else None,
        ),
        "observed_max_charge_soc": _max_optional(
            previous.observed_max_charge_soc,
            source.soc if direction == "charge" else None,
        ),
    }


def _profile_activity_updates(
    previous: EnergyLearningProfile,
    direction: str,
    active: bool,
    now: float,
    charge_power: float | None,
    discharge_power: float | None,
    response_delay: float | None,
    smoothing_sample_count: int,
) -> _ActivityUpdates:
    smoothing_delta_w = _smoothing_delta(previous, direction, charge_power, discharge_power)
    charge_average = _rolling_average(
        previous.average_active_charge_power_w,
        previous.charge_sample_count,
        charge_power if direction == "charge" else None,
    )
    discharge_average = _rolling_average(
        previous.average_active_discharge_power_w,
        previous.discharge_sample_count,
        discharge_power if direction == "discharge" else None,
    )
    markers = _activity_markers(previous, direction, active, now)
    return {
        "average_active_charge_power_w": charge_average,
        "average_active_discharge_power_w": discharge_average,
        "average_active_power_delta_w": _rolling_average(
            previous.average_active_power_delta_w,
            previous.smoothing_sample_count,
            smoothing_delta_w,
        ),
        "typical_response_delay_seconds": _rolling_average(
            previous.typical_response_delay_seconds,
            previous.response_sample_count,
            response_delay,
        ),
        "direction_change_count": int(previous.direction_change_count) + (1 if _direction_changed(previous, direction) else 0),
        "last_direction": markers["last_direction"],
        "last_activity_state": markers["last_activity_state"],
        "last_active_at": markers["last_active_at"],
        "last_inactive_at": markers["last_inactive_at"],
        "last_change_at": markers["last_change_at"],
    }


def _direction(source: EnergySourceSnapshot) -> str:
    charge_power = source.charge_power_w or 0.0
    discharge_power = source.discharge_power_w or 0.0
    dominant_direction = _dominant_direction(charge_power, discharge_power)
    if dominant_direction is not None:
        return dominant_direction
    return "idle"


def _profile_power_summaries(
    normalized_profiles: tuple[EnergyLearningProfile, ...],
) -> dict[str, float | None]:
    power_fields = (
        "observed_max_charge_power_w",
        "observed_max_discharge_power_w",
        "observed_max_ac_power_w",
        "observed_max_pv_input_power_w",
        "observed_max_grid_import_w",
        "observed_max_grid_export_w",
    )
    return {
        field_name: _sum_optional(getattr(profile, field_name) for profile in normalized_profiles)
        for field_name in power_fields
    }


def _profile_average_summaries(
    normalized_profiles: tuple[EnergyLearningProfile, ...],
) -> dict[str, float | None]:
    return {
        "average_active_charge_power_w": _weighted_profile_average(
            normalized_profiles,
            "average_active_charge_power_w",
            "charge_sample_count",
        ),
        "average_active_discharge_power_w": _weighted_profile_average(
            normalized_profiles,
            "average_active_discharge_power_w",
            "discharge_sample_count",
        ),
        "typical_response_delay_seconds": _mean_optional(
            profile.typical_response_delay_seconds for profile in normalized_profiles
        ),
        "average_active_power_delta_w": _weighted_profile_average(
            normalized_profiles,
            "average_active_power_delta_w",
            "smoothing_sample_count",
        ),
        "power_smoothing_ratio": _weighted_average(
            _weighted_profile_pairs(
                normalized_profiles,
                value_field="power_smoothing_ratio",
                weight_field="smoothing_sample_count",
            )
        ),
    }


def _profile_bias_summaries(counts: Mapping[str, int]) -> dict[str, float | None]:
    return {
        "support_bias": _bias_from_counts(counts["discharge_count"], counts["charge_count"]),
        "import_support_bias": _bias_from_counts(counts["import_support_count"], counts["import_charge_count"]),
        "export_bias": _bias_from_counts(counts["export_charge_count"], counts["export_discharge_count"]),
        "battery_first_export_bias": _bias_from_counts(
            counts["export_charge_count"],
            counts["export_discharge_count"] + counts["export_idle_count"],
        ),
        "day_support_bias": _bias_from_counts(counts["day_discharge_count"], counts["day_charge_count"]),
        "night_support_bias": _bias_from_counts(counts["night_discharge_count"], counts["night_charge_count"]),
    }


def _profile_reserve_summaries(
    normalized_profiles: tuple[EnergyLearningProfile, ...],
) -> dict[str, float | None]:
    reserve_floor = _max_known(_known_profile_values(normalized_profiles, "reserve_band_floor_soc"))
    reserve_ceiling = _min_known(_known_profile_values(normalized_profiles, "reserve_band_ceiling_soc"))
    reserve_width = _reserve_band_width(reserve_floor, reserve_ceiling)
    return {
        "reserve_band_floor_soc": reserve_floor,
        "reserve_band_ceiling_soc": reserve_ceiling,
        "reserve_band_width_soc": reserve_width,
    }


def _sum_profile_int(normalized_profiles: tuple[EnergyLearningProfile, ...], field_name: str) -> int:
    return sum(int(getattr(profile, field_name)) for profile in normalized_profiles)


def _direction_sample_increments(direction: str) -> dict[str, int]:
    return {
        "charge": 1 if direction == "charge" else 0,
        "discharge": 1 if direction == "discharge" else 0,
    }


def _grid_sample_increments(
    direction: str,
    grid_import: float | None,
    grid_export: float | None,
) -> dict[str, int]:
    return {
        "import_support": _grid_increment(direction, grid_import, "discharge"),
        "import_charge": _grid_increment(direction, grid_import, "charge"),
        "export_charge": _grid_increment(direction, grid_export, "charge"),
        "export_discharge": _grid_increment(direction, grid_export, "discharge"),
        "export_idle": _grid_idle_increment(direction, grid_export),
    }


def _period_sample_increments(active: bool, direction: str, period: str) -> dict[str, int]:
    return {
        "day_active": _period_increment(active, direction, period, expected_period="day"),
        "night_active": _period_increment(active, direction, period, expected_period="night"),
        "day_charge": _period_increment(active, direction, period, expected_direction="charge", expected_period="day"),
        "night_charge": _period_increment(
            active,
            direction,
            period,
            expected_direction="charge",
            expected_period="night",
        ),
        "day_discharge": _period_increment(
            active,
            direction,
            period,
            expected_direction="discharge",
            expected_period="day",
        ),
        "night_discharge": _period_increment(
            active,
            direction,
            period,
            expected_direction="discharge",
            expected_period="night",
        ),
    }


def _dominant_direction(charge_power: float, discharge_power: float) -> str | None:
    if charge_power >= _ACTIVE_POWER_THRESHOLD_W and charge_power > discharge_power:
        return "charge"
    if discharge_power >= _ACTIVE_POWER_THRESHOLD_W and discharge_power > charge_power:
        return "discharge"
    return None


def _is_active(source: EnergySourceSnapshot, direction: str) -> bool:
    if direction != "idle":
        return True
    return any(
        abs(float(value)) >= _ACTIVE_POWER_THRESHOLD_W
        for value in (source.ac_power_w, source.pv_input_power_w)
        if value is not None
    )


def _sample_period(now: float) -> str:
    hour = int(time.localtime(float(now)).tm_hour)
    return "day" if _DAY_START_HOUR <= hour < _DAY_END_HOUR else "night"


def _activation_response_delay(
    previous: EnergyLearningProfile,
    active: bool,
    direction: str,
    now: float,
) -> float | None:
    if not active:
        return None
    return _inactive_response_delay(previous, now) or _direction_change_response_delay(previous, direction, now)


def _activity_markers(
    previous: EnergyLearningProfile,
    direction: str,
    active: bool,
    now: float,
) -> _ActivityMarkers:
    return {
        "last_direction": direction,
        "last_activity_state": "active" if active else "idle",
        "last_active_at": float(now) if active else previous.last_active_at,
        "last_inactive_at": float(now) if not active else previous.last_inactive_at,
        "last_change_at": float(now),
    }


def _inactive_response_delay(previous: EnergyLearningProfile, now: float) -> float | None:
    if previous.last_activity_state == "active" or previous.last_inactive_at is None:
        return None
    return max(0.0, float(now) - float(previous.last_inactive_at))


def _direction_change_response_delay(
    previous: EnergyLearningProfile,
    direction: str,
    now: float,
) -> float | None:
    if not _direction_changed(previous, direction) or previous.last_change_at is None:
        return None
    return max(0.0, float(now) - float(previous.last_change_at))


def _direction_changed(previous: EnergyLearningProfile, direction: str) -> bool:
    if direction not in {"charge", "discharge"}:
        return False
    return previous.last_direction in {"charge", "discharge"} and previous.last_direction != direction


def _max_optional(current: float | None, candidate: float | None) -> float | None:
    if candidate is None:
        return current
    if current is None:
        return float(candidate)
    return max(float(current), float(candidate))


def _min_optional(current: float | None, candidate: float | None) -> float | None:
    if candidate is None:
        return current
    if current is None:
        return float(candidate)
    return min(float(current), float(candidate))


def _max_known(values: Any) -> float | None:
    normalized = [float(value) for value in values]
    if not normalized:
        return None
    return max(normalized)


def _min_known(values: Any) -> float | None:
    normalized = [float(value) for value in values]
    if not normalized:
        return None
    return min(normalized)


def _rolling_average(current: float | None, count: int, candidate: float | None) -> float | None:
    if candidate is None:
        return current
    if current is None or count <= 0:
        return float(candidate)
    return ((float(current) * float(count)) + float(candidate)) / float(count + 1)


def _weighted_average(values: tuple[tuple[float | None, float], ...]) -> float | None:
    weighted_total = 0.0
    weight_total = 0.0
    for value, weight in values:
        if value is None or weight <= 0.0:
            continue
        weighted_total += float(value) * float(weight)
        weight_total += float(weight)
    if weight_total <= 0.0:
        return None
    return weighted_total / weight_total


def _smoothing_delta(
    previous: EnergyLearningProfile,
    direction: str,
    charge_power: float | None,
    discharge_power: float | None,
) -> float | None:
    current_power, average_power = _directional_power_pair(previous, direction, charge_power, discharge_power)
    if current_power is None or average_power is None:
        return None
    return abs(float(current_power) - float(average_power))


def _absolute_optional(value: float | None) -> float | None:
    if value is None:
        return None
    return abs(float(value))


def _positive_optional(value: float | None) -> float | None:
    if value is None:
        return None
    normalized = float(value)
    return normalized if normalized > 0.0 else None


def _normalized_profile_iter(
    profiles: Mapping[str, EnergyLearningProfile | Mapping[str, Any]] | None,
) -> tuple[EnergyLearningProfile, ...]:
    normalized_profiles: list[EnergyLearningProfile] = []
    for source_id, raw_profile in dict(profiles or {}).items():
        if isinstance(raw_profile, EnergyLearningProfile):
            normalized_profiles.append(raw_profile)
            continue
        normalized_profiles.append(_coerce_learning_profile(source_id, raw_profile))
    return tuple(normalized_profiles)


def _coerce_learning_profile(source_id: str, raw_profile: Mapping[str, Any]) -> EnergyLearningProfile:
    int_fields = _coerced_learning_int_fields(raw_profile)
    float_fields = _coerced_learning_float_fields(raw_profile)
    return EnergyLearningProfile(
        source_id=str(raw_profile.get("source_id", source_id)),
        sample_count=int_fields["sample_count"],
        active_sample_count=int_fields["active_sample_count"],
        charge_sample_count=int_fields["charge_sample_count"],
        discharge_sample_count=int_fields["discharge_sample_count"],
        import_support_sample_count=int_fields["import_support_sample_count"],
        import_charge_sample_count=int_fields["import_charge_sample_count"],
        export_charge_sample_count=int_fields["export_charge_sample_count"],
        export_discharge_sample_count=int_fields["export_discharge_sample_count"],
        export_idle_sample_count=int_fields["export_idle_sample_count"],
        day_active_sample_count=int_fields["day_active_sample_count"],
        night_active_sample_count=int_fields["night_active_sample_count"],
        day_charge_sample_count=int_fields["day_charge_sample_count"],
        night_charge_sample_count=int_fields["night_charge_sample_count"],
        day_discharge_sample_count=int_fields["day_discharge_sample_count"],
        night_discharge_sample_count=int_fields["night_discharge_sample_count"],
        response_sample_count=int_fields["response_sample_count"],
        smoothing_sample_count=int_fields["smoothing_sample_count"],
        observed_max_charge_power_w=float_fields["observed_max_charge_power_w"],
        observed_max_discharge_power_w=float_fields["observed_max_discharge_power_w"],
        observed_max_ac_power_w=float_fields["observed_max_ac_power_w"],
        observed_max_pv_input_power_w=float_fields["observed_max_pv_input_power_w"],
        observed_max_grid_import_w=float_fields["observed_max_grid_import_w"],
        observed_max_grid_export_w=float_fields["observed_max_grid_export_w"],
        observed_min_discharge_soc=float_fields["observed_min_discharge_soc"],
        observed_max_charge_soc=float_fields["observed_max_charge_soc"],
        average_active_charge_power_w=float_fields["average_active_charge_power_w"],
        average_active_discharge_power_w=float_fields["average_active_discharge_power_w"],
        average_active_power_delta_w=float_fields["average_active_power_delta_w"],
        typical_response_delay_seconds=float_fields["typical_response_delay_seconds"],
        direction_change_count=int_fields["direction_change_count"],
        last_direction=_normalized_direction(raw_profile.get("last_direction")),
        last_activity_state=_normalized_activity_state(raw_profile.get("last_activity_state")),
        last_active_at=float_fields["last_active_at"],
        last_inactive_at=float_fields["last_inactive_at"],
        last_change_at=float_fields["last_change_at"],
    )


def _coerced_learning_int_fields(raw_profile: Mapping[str, Any]) -> dict[str, int]:
    return {
        "sample_count": _coerced_int(raw_profile, "sample_count"),
        "active_sample_count": _coerced_int(raw_profile, "active_sample_count"),
        "charge_sample_count": _coerced_int(raw_profile, "charge_sample_count"),
        "discharge_sample_count": _coerced_int(raw_profile, "discharge_sample_count"),
        "import_support_sample_count": _coerced_int(raw_profile, "import_support_sample_count"),
        "import_charge_sample_count": _coerced_int(raw_profile, "import_charge_sample_count"),
        "export_charge_sample_count": _coerced_int(raw_profile, "export_charge_sample_count"),
        "export_discharge_sample_count": _coerced_int(raw_profile, "export_discharge_sample_count"),
        "export_idle_sample_count": _coerced_int(raw_profile, "export_idle_sample_count"),
        "day_active_sample_count": _coerced_int(raw_profile, "day_active_sample_count"),
        "night_active_sample_count": _coerced_int(raw_profile, "night_active_sample_count"),
        "day_charge_sample_count": _coerced_int(raw_profile, "day_charge_sample_count"),
        "night_charge_sample_count": _coerced_int(raw_profile, "night_charge_sample_count"),
        "day_discharge_sample_count": _coerced_int(raw_profile, "day_discharge_sample_count"),
        "night_discharge_sample_count": _coerced_int(raw_profile, "night_discharge_sample_count"),
        "response_sample_count": _coerced_int(raw_profile, "response_sample_count"),
        "smoothing_sample_count": _coerced_int(raw_profile, "smoothing_sample_count"),
        "direction_change_count": _coerced_int(raw_profile, "direction_change_count"),
    }


def _coerced_learning_float_fields(raw_profile: Mapping[str, Any]) -> dict[str, float | None]:
    return {
        "observed_max_charge_power_w": _optional_float(raw_profile.get("observed_max_charge_power_w")),
        "observed_max_discharge_power_w": _optional_float(raw_profile.get("observed_max_discharge_power_w")),
        "observed_max_ac_power_w": _optional_float(raw_profile.get("observed_max_ac_power_w")),
        "observed_max_pv_input_power_w": _optional_float(raw_profile.get("observed_max_pv_input_power_w")),
        "observed_max_grid_import_w": _optional_float(raw_profile.get("observed_max_grid_import_w")),
        "observed_max_grid_export_w": _optional_float(raw_profile.get("observed_max_grid_export_w")),
        "observed_min_discharge_soc": _optional_float(raw_profile.get("observed_min_discharge_soc")),
        "observed_max_charge_soc": _optional_float(raw_profile.get("observed_max_charge_soc")),
        "average_active_charge_power_w": _optional_float(raw_profile.get("average_active_charge_power_w")),
        "average_active_discharge_power_w": _optional_float(raw_profile.get("average_active_discharge_power_w")),
        "average_active_power_delta_w": _optional_float(raw_profile.get("average_active_power_delta_w")),
        "typical_response_delay_seconds": _optional_float(raw_profile.get("typical_response_delay_seconds")),
        "last_active_at": _optional_float(raw_profile.get("last_active_at")),
        "last_inactive_at": _optional_float(raw_profile.get("last_inactive_at")),
        "last_change_at": _optional_float(raw_profile.get("last_change_at")),
    }


def _sum_optional(values: Any) -> float | None:
    numeric_values = [float(value) for value in values if value is not None]
    if not numeric_values:
        return None
    return sum(numeric_values)


def _optional_float(value: object) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return float(value)


def _coerced_int(raw_profile: Mapping[str, Any], key: str) -> int:
    return int(raw_profile.get(key, 0) or 0)


def _normalized_direction(value: object) -> str:
    normalized = str(value).strip().lower()
    return normalized if normalized in {"idle", "charge", "discharge"} else "idle"


def _normalized_activity_state(value: object) -> str:
    normalized = str(value).strip().lower()
    return normalized if normalized in {"idle", "active"} else "idle"


def _weighted_profile_average(
    normalized_profiles: tuple[EnergyLearningProfile, ...],
    value_field: str,
    weight_field: str,
) -> float | None:
    return _weighted_average(
        _weighted_profile_pairs(
            normalized_profiles,
            value_field=value_field,
            weight_field=weight_field,
        )
    )


def _weighted_profile_pairs(
    normalized_profiles: tuple[EnergyLearningProfile, ...],
    *,
    value_field: str,
    weight_field: str,
) -> tuple[tuple[float | None, float], ...]:
    return tuple(
        (getattr(profile, value_field), float(getattr(profile, weight_field)))
        for profile in normalized_profiles
    )


def _known_profile_values(
    normalized_profiles: tuple[EnergyLearningProfile, ...],
    field_name: str,
) -> tuple[float, ...]:
    return tuple(
        float(value)
        for profile in normalized_profiles
        for value in (getattr(profile, field_name),)
        if value is not None
    )


def _reserve_band_width(reserve_floor: float | None, reserve_ceiling: float | None) -> float | None:
    if reserve_floor is None or reserve_ceiling is None or reserve_ceiling < reserve_floor:
        return None
    return float(reserve_ceiling) - float(reserve_floor)


def _grid_increment(direction: str, grid_value: float | None, expected_direction: str) -> int:
    return 1 if _grid_activity_match(direction, grid_value, expected_direction) else 0


def _grid_idle_increment(direction: str, grid_export: float | None) -> int:
    return 1 if direction == "idle" and (grid_export or 0.0) >= _GRID_ACTIVITY_THRESHOLD_W else 0


def _period_increment(
    active: bool,
    direction: str,
    period: str,
    *,
    expected_direction: str | None = None,
    expected_period: str,
) -> int:
    if not active or period != expected_period:
        return 0
    if expected_direction is None:
        return 1
    return 1 if direction == expected_direction else 0


def _directional_power_pair(
    previous: EnergyLearningProfile,
    direction: str,
    charge_power: float | None,
    discharge_power: float | None,
) -> tuple[float | None, float | None]:
    if direction == "charge":
        return charge_power, previous.average_active_charge_power_w
    if direction == "discharge":
        return discharge_power, previous.average_active_discharge_power_w
    return None, None
