# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared lightweight contracts for normalized wallbox state and snapshots."""

from __future__ import annotations

import math
from typing import Any, cast


LEARNED_CHARGE_POWER_STATES = frozenset({"unknown", "learning", "stable", "stale"})
LEARNED_CHARGE_POWER_PHASES = frozenset({"L1", "L2", "L3", "3P"})
AUTO_STATE_CODES = {
    "idle": 0,
    "waiting": 1,
    "learning": 2,
    "charging": 3,
    "blocked": 4,
    "recovery": 5,
}


def finite_float_or_none(value: Any) -> float | None:
    """Return one finite float value, or ``None`` when the input is invalid."""
    if value is None or isinstance(value, bool):
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(normalized):
        return None
    return normalized


def non_negative_float_or_none(value: Any) -> float | None:
    """Return one finite non-negative float value, or ``None`` when invalid."""
    normalized = finite_float_or_none(value)
    if normalized is None or normalized < 0.0:
        return None
    return normalized


def non_negative_int(value: Any, default: int = 0) -> int:
    """Return one non-negative integer value."""
    if isinstance(value, bool):
        return int(default)
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return int(default)
    return max(0, normalized)


def normalize_binary_flag(value: Any, default: int = 0) -> int:
    """Return one normalized 0/1 flag value."""
    if isinstance(value, bool):
        return int(value)
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return 1 if bool(default) else 0
    return 0 if normalized <= 0 else 1


def normalize_optional_binary_state(value: Any) -> bool | None:
    """Return one optional relay-like boolean state."""
    if value is None:
        return None
    return bool(normalize_binary_flag(value))


def normalize_learning_state(value: Any) -> str:
    """Return one supported learned-power state string."""
    state = str(value).strip().lower() if value is not None else "unknown"
    return state if state in LEARNED_CHARGE_POWER_STATES else "unknown"


def normalize_learning_phase(value: Any) -> str | None:
    """Return one supported learned-power phase signature."""
    phase = str(value).strip().upper() if value is not None else ""
    return phase if phase in LEARNED_CHARGE_POWER_PHASES else None


def paired_optional_values(left: Any, right: Any) -> bool:
    """Return True when two optional values are both present or both absent."""
    return (left is None) == (right is None)


def valid_battery_soc(value: Any) -> bool:
    """Return whether one optional runtime SOC value lies inside 0..100."""
    normalized = finite_float_or_none(value)
    return normalized is None or 0.0 <= normalized <= 100.0


def timestamp_not_future(timestamp: Any, now: float, tolerance_seconds: float = 1.0) -> bool:
    """Return whether one timestamp is not implausibly ahead of ``now``."""
    normalized = finite_float_or_none(timestamp)
    return normalized is not None and normalized <= (float(now) + float(tolerance_seconds))


def timestamp_age_within(
    timestamp: Any,
    now: float,
    max_age_seconds: float,
    *,
    future_tolerance_seconds: float = 1.0,
) -> bool:
    """Return whether one timestamp is fresh enough and not implausibly future-dated."""
    normalized = finite_float_or_none(timestamp)
    if normalized is None:
        return False
    age_seconds = float(now) - normalized
    return -float(future_tolerance_seconds) <= age_seconds <= float(max_age_seconds)


def thresholds_ordered(start_watts: Any, stop_watts: Any) -> bool:
    """Return whether one start/stop threshold pair is finite, non-negative, and ordered."""
    start_value = non_negative_float_or_none(start_watts)
    stop_value = non_negative_float_or_none(stop_watts)
    return start_value is not None and stop_value is not None and stop_value <= start_value


def normalize_auto_state(value: Any) -> str:
    """Return one supported outward-facing Auto-state label."""
    state = str(value).strip().lower() if value is not None else "idle"
    return state if state in AUTO_STATE_CODES else "idle"


def normalized_auto_state_pair(state: Any, code: Any) -> tuple[str, int]:
    """Return one consistent outward-facing Auto-state text/code pair."""
    normalized_state = normalize_auto_state(state)
    normalized_code = AUTO_STATE_CODES[normalized_state]
    try:
        supplied_code = int(code)
    except (TypeError, ValueError):
        supplied_code = None
    if supplied_code != normalized_code:
        return normalized_state, normalized_code
    return normalized_state, normalized_code


def displayable_confirmed_read_timestamp(
    *,
    last_confirmed_at: Any,
    last_pm_at: Any,
    last_pm_confirmed: Any,
    now: float | int | None = None,
    future_tolerance_seconds: float = 1.0,
) -> float | None:
    """Return one safe timestamp for outward-facing confirmed Shelly-read diagnostics."""
    current = None if now is None else float(now)
    confirmed_timestamp = finite_float_or_none(last_confirmed_at)
    fallback_timestamp = finite_float_or_none(last_pm_at) if bool(last_pm_confirmed) else None
    for candidate in (confirmed_timestamp, fallback_timestamp):
        if candidate is None:
            continue
        if current is None or timestamp_not_future(candidate, current, future_tolerance_seconds):
            return candidate
    return None


def sanitized_auto_metrics(metrics: Any) -> dict[str, Any]:
    """Return one audit-safe auto-metric payload with normalized outward values."""
    if not isinstance(metrics, dict):
        return {}
    sanitized: dict[str, Any] = dict(cast(dict[str, Any], metrics))
    numeric_fields = (
        "surplus",
        "grid",
        "start_threshold",
        "stop_threshold",
        "threshold_scale",
        "stop_alpha",
        "surplus_volatility",
    )
    for field in numeric_fields:
        sanitized[field] = finite_float_or_none(sanitized.get(field))
    sanitized["learned_charge_power"] = non_negative_float_or_none(sanitized.get("learned_charge_power"))
    soc_value = finite_float_or_none(sanitized.get("soc"))
    sanitized["soc"] = soc_value if valid_battery_soc(soc_value) else None
    learned_state = sanitized.get("learned_charge_power_state")
    sanitized["learned_charge_power_state"] = (
        normalize_learning_state(learned_state) if learned_state is not None else None
    )
    for field in ("profile", "threshold_mode", "stop_alpha_stage"):
        value = sanitized.get(field)
        sanitized[field] = None if value is None else str(value)
    if not thresholds_ordered(sanitized.get("start_threshold"), sanitized.get("stop_threshold")):
        sanitized["start_threshold"] = None
        sanitized["stop_threshold"] = None
    return sanitized


def normalized_worker_snapshot(
    snapshot: Any,
    *,
    now: float | None = None,
    future_tolerance_seconds: float = 1.0,
    clamp_future_timestamps: bool = True,
) -> dict[str, Any]:
    """Return one worker snapshot with consistent PM payload invariants."""
    normalized: dict[str, Any] = dict(cast(dict[str, Any], snapshot)) if isinstance(snapshot, dict) else {}
    current = None if now is None else float(now)

    captured_at = non_negative_float_or_none(normalized.get("captured_at"))
    pm_status_raw = normalized.get("pm_status")
    pm_status = dict(cast(dict[str, Any], pm_status_raw)) if isinstance(pm_status_raw, dict) else None
    pm_captured_at = non_negative_float_or_none(normalized.get("pm_captured_at"))
    pm_confirmed = bool(normalized.get("pm_confirmed", False))

    if captured_at is None:
        if current is not None:
            captured_at = float(current)
        else:
            captured_at = pm_captured_at if pm_captured_at is not None else 0.0
    if (
        current is not None
        and clamp_future_timestamps
        and not timestamp_not_future(captured_at, current, future_tolerance_seconds)
    ):
        captured_at = float(current)

    if not (isinstance(pm_status, dict) and "output" in pm_status):
        pm_status = None
        pm_captured_at = None
        pm_confirmed = False
    else:
        if pm_captured_at is None:
            pm_captured_at = captured_at
        if (
            current is not None
            and clamp_future_timestamps
            and not timestamp_not_future(pm_captured_at, current, future_tolerance_seconds)
        ):
            pm_status = None
            pm_captured_at = None
            pm_confirmed = False
        else:
            captured_at = max(float(captured_at), float(pm_captured_at))

    normalized["captured_at"] = float(captured_at)
    normalized["pm_status"] = pm_status
    normalized["pm_captured_at"] = None if pm_captured_at is None else float(pm_captured_at)
    normalized["pm_confirmed"] = bool(pm_confirmed and pm_status is not None and pm_captured_at is not None)
    return normalized


def normalized_auto_decision_trace(
    *,
    health_reason: Any,
    cached_inputs: bool,
    relay_intent: Any,
    learned_charge_power_state: Any,
    metrics: Any,
    health_code_func: Any,
    derive_auto_state_func: Any,
) -> dict[str, Any]:
    """Return one outward-facing Auto decision trace with normalized postconditions."""
    base_reason = str(health_reason).replace("-cached", "") if health_reason is not None else "init"
    cached = bool(cached_inputs)
    normalized_reason = f"{base_reason}-cached" if cached else base_reason
    normalized_health_code = int(health_code_func(base_reason)) + (100 if cached else 0)
    normalized_relay_intent = normalize_binary_flag(relay_intent)
    normalized_metrics = sanitized_auto_metrics(metrics)
    normalized_metrics["relay_intent"] = normalized_relay_intent
    learned_state = normalized_metrics.get("learned_charge_power_state")
    if learned_state is None:
        learned_state = normalize_learning_state(learned_charge_power_state)
    normalized_state = derive_auto_state_func(
        base_reason,
        relay_on=bool(normalized_relay_intent),
        learned_charge_power_state=learned_state,
    )
    normalized_state, normalized_state_code = normalized_auto_state_pair(normalized_state, None)
    normalized_metrics["state"] = normalized_state
    return {
        "health_reason": normalized_reason,
        "health_code": normalized_health_code,
        "state": normalized_state,
        "state_code": normalized_state_code,
        "metrics": normalized_metrics,
    }


def cutover_confirmed_off(
    *,
    relay_on: bool,
    pending_state: Any,
    confirmed_output: Any,
    confirmed_at: Any,
    requested_at: Any,
    now: float,
    max_age_seconds: float,
    future_tolerance_seconds: float = 1.0,
) -> bool:
    """Return whether Manual->Auto cutover may finish from one confirmed OFF sample."""
    if pending_state is not None or relay_on or bool(confirmed_output):
        return False
    if not timestamp_age_within(
        confirmed_at,
        now,
        max_age_seconds,
        future_tolerance_seconds=future_tolerance_seconds,
    ):
        return False
    if requested_at is None:
        return True
    confirmed_timestamp = finite_float_or_none(confirmed_at)
    requested_timestamp = finite_float_or_none(requested_at)
    if confirmed_timestamp is None or requested_timestamp is None:
        return False
    return confirmed_timestamp >= requested_timestamp


def write_failure_is_reversible(side_effect_started: bool) -> bool:
    """Return whether one failed DBus write may still be rolled back safely."""
    return not bool(side_effect_started)
