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


def _displayable_timestamp_candidates(
    last_confirmed_at: Any,
    last_pm_at: Any,
    last_pm_confirmed: Any,
) -> tuple[float | None, float | None]:
    """Return preferred and fallback timestamps for confirmed-read display."""
    confirmed_timestamp = finite_float_or_none(last_confirmed_at)
    fallback_timestamp = finite_float_or_none(last_pm_at) if bool(last_pm_confirmed) else None
    return confirmed_timestamp, fallback_timestamp


def _timestamp_displayable(
    candidate: float | None,
    current: float | None,
    future_tolerance_seconds: float,
) -> bool:
    """Return whether one timestamp may be shown on outward-facing diagnostics."""
    return candidate is not None and (
        current is None or timestamp_not_future(candidate, current, future_tolerance_seconds)
    )


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
    for candidate in _displayable_timestamp_candidates(last_confirmed_at, last_pm_at, last_pm_confirmed):
        if _timestamp_displayable(candidate, current, future_tolerance_seconds):
            return candidate
    return None


AUTO_METRIC_NUMERIC_FIELDS = (
    "surplus",
    "grid",
    "start_threshold",
    "stop_threshold",
    "threshold_scale",
    "stop_alpha",
    "surplus_volatility",
)
AUTO_METRIC_TEXT_FIELDS = ("profile", "threshold_mode", "stop_alpha_stage")


def _normalized_metric_text(value: Any) -> str | None:
    """Return one outward-safe text metric value."""
    return None if value is None else str(value)


def _sanitize_numeric_auto_metrics(sanitized: dict[str, Any]) -> None:
    """Normalize the numeric outward-facing Auto metrics in place."""
    for field in AUTO_METRIC_NUMERIC_FIELDS:
        sanitized[field] = finite_float_or_none(sanitized.get(field))


def _sanitize_text_auto_metrics(sanitized: dict[str, Any]) -> None:
    """Normalize the text outward-facing Auto metrics in place."""
    for field in AUTO_METRIC_TEXT_FIELDS:
        sanitized[field] = _normalized_metric_text(sanitized.get(field))


def _sanitize_soc_metric(sanitized: dict[str, Any]) -> None:
    """Normalize the optional SOC field in place."""
    soc_value = finite_float_or_none(sanitized.get("soc"))
    sanitized["soc"] = soc_value if valid_battery_soc(soc_value) else None


def _sanitize_learning_metrics(sanitized: dict[str, Any]) -> None:
    """Normalize learned-power-related outward-facing Auto metrics in place."""
    sanitized["learned_charge_power"] = non_negative_float_or_none(sanitized.get("learned_charge_power"))
    learned_state = sanitized.get("learned_charge_power_state")
    sanitized["learned_charge_power_state"] = (
        normalize_learning_state(learned_state) if learned_state is not None else None
    )


def _sanitize_threshold_metrics(sanitized: dict[str, Any]) -> None:
    """Drop outward threshold metrics when they are missing or logically inverted."""
    if thresholds_ordered(sanitized.get("start_threshold"), sanitized.get("stop_threshold")):
        return
    sanitized["start_threshold"] = None
    sanitized["stop_threshold"] = None


def sanitized_auto_metrics(metrics: Any) -> dict[str, Any]:
    """Return one audit-safe auto-metric payload with normalized outward values."""
    if not isinstance(metrics, dict):
        return {}
    sanitized: dict[str, Any] = dict(cast(dict[str, Any], metrics))
    _sanitize_numeric_auto_metrics(sanitized)
    _sanitize_learning_metrics(sanitized)
    _sanitize_soc_metric(sanitized)
    _sanitize_text_auto_metrics(sanitized)
    _sanitize_threshold_metrics(sanitized)
    return sanitized


def _snapshot_mapping(snapshot: Any) -> dict[str, Any]:
    """Return one shallow snapshot mapping for normalization helpers."""
    return dict(cast(dict[str, Any], snapshot)) if isinstance(snapshot, dict) else {}


def _resolved_snapshot_captured_at(
    captured_at: float | None,
    pm_captured_at: float | None,
    current: float | None,
) -> float:
    """Return the best captured-at baseline before PM payload validation."""
    if captured_at is not None:
        return float(captured_at)
    if current is not None:
        return float(current)
    if pm_captured_at is not None:
        return float(pm_captured_at)
    return 0.0


def _clamped_snapshot_timestamp(
    timestamp: float,
    current: float | None,
    *,
    future_tolerance_seconds: float,
    clamp_future_timestamps: bool,
) -> float:
    """Return one snapshot timestamp, optionally clamped to ``now``."""
    if (
        current is not None
        and clamp_future_timestamps
        and not timestamp_not_future(timestamp, current, future_tolerance_seconds)
    ):
        return float(current)
    return float(timestamp)


def _valid_pm_snapshot_payload(pm_status: dict[str, Any] | None) -> bool:
    """Return whether one PM payload contains the minimum required state."""
    return isinstance(pm_status, dict) and "output" in pm_status


def _pm_snapshot_future_invalid(
    timestamp: float,
    current: float | None,
    *,
    future_tolerance_seconds: float,
    clamp_future_timestamps: bool,
) -> bool:
    """Return whether one PM timestamp invalidates the whole PM payload."""
    return bool(
        current is not None
        and clamp_future_timestamps
        and not timestamp_not_future(timestamp, current, future_tolerance_seconds)
    )


def _normalized_pm_snapshot_payload(
    *,
    pm_status: dict[str, Any] | None,
    pm_captured_at: float | None,
    pm_confirmed: bool,
    captured_at: float,
    current: float | None,
    future_tolerance_seconds: float,
    clamp_future_timestamps: bool,
) -> tuple[dict[str, Any] | None, float | None, bool, float]:
    """Return one normalized PM payload plus its effective capture timestamp."""
    if not _valid_pm_snapshot_payload(pm_status):
        return None, None, False, float(captured_at)
    resolved_pm_captured_at = float(captured_at) if pm_captured_at is None else float(pm_captured_at)
    if _pm_snapshot_future_invalid(
        resolved_pm_captured_at,
        current,
        future_tolerance_seconds=future_tolerance_seconds,
        clamp_future_timestamps=clamp_future_timestamps,
    ):
        return None, None, False, float(captured_at)
    return (
        pm_status,
        resolved_pm_captured_at,
        bool(pm_confirmed),
        max(float(captured_at), resolved_pm_captured_at),
    )


def normalized_worker_snapshot(
    snapshot: Any,
    *,
    now: float | None = None,
    future_tolerance_seconds: float = 1.0,
    clamp_future_timestamps: bool = True,
) -> dict[str, Any]:
    """Return one worker snapshot with consistent PM payload invariants."""
    normalized = _snapshot_mapping(snapshot)
    current = None if now is None else float(now)

    captured_at = non_negative_float_or_none(normalized.get("captured_at"))
    pm_status, pm_captured_at, pm_confirmed = _snapshot_pm_payload(normalized)

    resolved_captured_at = _resolved_snapshot_captured_at(captured_at, pm_captured_at, current)
    resolved_captured_at = _clamped_snapshot_timestamp(
        resolved_captured_at,
        current,
        future_tolerance_seconds=future_tolerance_seconds,
        clamp_future_timestamps=clamp_future_timestamps,
    )
    pm_status, pm_captured_at, pm_confirmed, resolved_captured_at = _normalized_pm_snapshot_payload(
        pm_status=pm_status,
        pm_captured_at=pm_captured_at,
        pm_confirmed=pm_confirmed,
        captured_at=resolved_captured_at,
        current=current,
        future_tolerance_seconds=future_tolerance_seconds,
        clamp_future_timestamps=clamp_future_timestamps,
    )

    _apply_normalized_pm_payload(
        normalized,
        resolved_captured_at=resolved_captured_at,
        pm_status=pm_status,
        pm_captured_at=pm_captured_at,
        pm_confirmed=pm_confirmed,
    )
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
    if _cutover_blocked_by_pending_or_active(
        pending_state=pending_state,
        relay_on=relay_on,
        confirmed_output=confirmed_output,
    ):
        return False
    if not _cutover_confirmation_recent(
        confirmed_at=confirmed_at,
        now=now,
        max_age_seconds=max_age_seconds,
        future_tolerance_seconds=future_tolerance_seconds,
    ):
        return False
    return _cutover_request_satisfied(confirmed_at=confirmed_at, requested_at=requested_at)


def _snapshot_pm_payload(normalized: dict[str, Any]) -> tuple[dict[str, Any] | None, float | None, bool]:
    """Return the raw PM payload pieces from one worker snapshot mapping."""
    pm_status_raw = normalized.get("pm_status")
    pm_status = dict(cast(dict[str, Any], pm_status_raw)) if isinstance(pm_status_raw, dict) else None
    pm_captured_at = non_negative_float_or_none(normalized.get("pm_captured_at"))
    pm_confirmed = bool(normalized.get("pm_confirmed", False))
    return pm_status, pm_captured_at, pm_confirmed


def _apply_normalized_pm_payload(
    normalized: dict[str, Any],
    *,
    resolved_captured_at: float,
    pm_status: dict[str, Any] | None,
    pm_captured_at: float | None,
    pm_confirmed: bool,
) -> None:
    """Persist one normalized PM payload back into the worker snapshot mapping."""
    normalized["captured_at"] = float(resolved_captured_at)
    normalized["pm_status"] = pm_status
    normalized["pm_captured_at"] = None if pm_captured_at is None else float(pm_captured_at)
    normalized["pm_confirmed"] = bool(pm_confirmed and pm_status is not None and pm_captured_at is not None)


def _cutover_blocked_by_pending_or_active(*, pending_state: Any, relay_on: bool, confirmed_output: Any) -> bool:
    """Return whether cutover is blocked by pending or still-active relay state."""
    return pending_state is not None or bool(relay_on) or bool(confirmed_output)


def _cutover_confirmation_recent(
    *,
    confirmed_at: Any,
    now: float,
    max_age_seconds: float,
    future_tolerance_seconds: float,
) -> bool:
    """Return whether one confirmed relay sample is recent enough for cutover."""
    return timestamp_age_within(
        confirmed_at,
        now,
        max_age_seconds,
        future_tolerance_seconds=future_tolerance_seconds,
    )


def _cutover_request_satisfied(*, confirmed_at: Any, requested_at: Any) -> bool:
    """Return whether cutover timing satisfies the requested relay-off timestamp."""
    return True if requested_at is None else _confirmed_after_requested(confirmed_at, requested_at)


def _confirmed_after_requested(confirmed_at: Any, requested_at: Any) -> bool:
    """Return whether one confirmed relay sample happened at or after the request time."""
    confirmed_timestamp = finite_float_or_none(confirmed_at)
    requested_timestamp = finite_float_or_none(requested_at)
    if confirmed_timestamp is None or requested_timestamp is None:
        return False
    return confirmed_timestamp >= requested_timestamp


def write_failure_is_reversible(side_effect_started: bool) -> bool:
    """Return whether one failed DBus write may still be rolled back safely."""
    return not bool(side_effect_started)
