# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared constants and helper functions for the Shelly wallbox service."""

from __future__ import annotations

from configparser import ConfigParser
from collections.abc import Iterable
from dataclasses import dataclass
import logging
import math
import os
import time
from datetime import date, datetime, timedelta
from typing import Any, cast

from shelly_wallbox.core.contracts import finite_float_or_none

PhaseMeasurements = dict[str, dict[str, float]]
TimeWindow = tuple[int, int]
MonthRange = tuple[int, int]
WeekdaySelection = tuple[int, ...]


def _kwh(_p: Any, value: Any) -> str:
    """Format a kWh value for Venus DBus display."""
    return f"{float(value):.2f}"


def _a(_p: Any, value: Any) -> str:
    """Format a current value for Venus DBus display."""
    return f"{float(value):.1f}A"


def _w(_p: Any, value: Any) -> str:
    """Format a power value for Venus DBus display."""
    return f"{float(value):.1f}W"


def _v(_p: Any, value: Any) -> str:
    """Format a voltage value for Venus DBus display."""
    return f"{float(value):.1f}V"


def _status_label(_p: Any, value: Any) -> str:
    """Map numeric EV charger status codes to human-readable labels."""
    labels = {
        0: "Getrennt",
        1: "Bereit",
        2: "Laden",
        3: "Fertig",
        4: "Warten auf PV",
        6: "Warten auf Start",
    }
    return labels.get(int(value), "Unbekannt")


HEALTH_CODES: dict[str, int] = {
    "init": 0,
    "inputs-missing": 1,
    "battery-soc-missing": 2,
    "battery-soc-missing-allowed": 3,
    "averaging": 4,
    "running": 5,
    "waiting": 6,
    "auto-start": 7,
    "auto-stop": 8,
    "night-lock": 9,
    "inputs-cached": 10,
    "shelly-offline": 11,
    "autostart-disabled": 12,
    "warmup": 13,
    "manual-override": 14,
    "waiting-offtime": 15,
    "waiting-daytime": 16,
    "waiting-surplus": 17,
    "waiting-grid": 18,
    "waiting-soc": 19,
    "disabled": 20,
    "grid-missing": 21,
    "mode-transition": 22,
    "waiting-grid-recovery": 23,
    "command-mismatch": 24,
    "relay-sync-failed": 25,
    "charger-fault": 26,
    "phase-switch-mismatch": 27,
    "contactor-interlock": 28,
    "contactor-feedback-mismatch": 29,
    "contactor-suspected-open": 30,
    "contactor-suspected-welded": 31,
    "contactor-lockout-open": 32,
    "contactor-lockout-welded": 33,
    "charger-transport-busy": 34,
    "charger-transport-ownership": 35,
    "charger-transport-timeout": 36,
    "charger-transport-offline": 37,
    "charger-transport-response": 38,
    "charger-transport-error": 39,
    "scheduled-night-charge": 40,
}

AUTO_STATE_CODES: dict[str, int] = {
    "idle": 0,
    "waiting": 1,
    "learning": 2,
    "charging": 3,
    "blocked": 4,
    "recovery": 5,
}
SCHEDULED_STATE_CODES: dict[str, int] = {
    "disabled": 0,
    "auto-window": 1,
    "inactive-day": 2,
    "waiting-fallback": 3,
    "night-boost": 4,
    "after-latest-end": 5,
}
WEEKDAY_LABELS: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAY_TOKEN_MAP: dict[str, int] = {
    "mon": 0,
    "monday": 0,
    "mo": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "tu": 1,
    "wed": 2,
    "wednesday": 2,
    "we": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "th": 3,
    "fri": 4,
    "friday": 4,
    "fr": 4,
    "sat": 5,
    "saturday": 5,
    "sa": 5,
    "sun": 6,
    "sunday": 6,
    "su": 6,
}
DEFAULT_SCHEDULED_ENABLED_DAYS: WeekdaySelection = (0, 1, 2, 3, 4)


@dataclass(frozen=True)
class ScheduledModeSnapshot:
    """Derived scheduled-mode status for one local timestamp."""

    state: str
    state_code: int
    night_boost_active: bool
    target_day_index: int
    target_day_label: str
    target_date_text: str
    target_day_enabled: bool
RECOVERY_AUTO_REASONS = {
    "inputs-missing",
    "battery-soc-missing",
    "battery-soc-missing-allowed",
    "grid-missing",
    "waiting-grid-recovery",
    "shelly-offline",
    "charger-fault",
    "command-mismatch",
    "relay-sync-failed",
    "phase-switch-mismatch",
    "contactor-feedback-mismatch",
    "contactor-suspected-open",
    "contactor-suspected-welded",
    "contactor-lockout-open",
    "contactor-lockout-welded",
    "charger-transport-busy",
    "charger-transport-ownership",
    "charger-transport-timeout",
    "charger-transport-offline",
    "charger-transport-response",
    "charger-transport-error",
}
BLOCKED_AUTO_REASONS = {
    "disabled",
    "autostart-disabled",
    "warmup",
    "manual-override",
    "waiting-offtime",
    "waiting-daytime",
    "waiting-grid",
    "waiting-soc",
    "night-lock",
    "mode-transition",
    "contactor-interlock",
}
CHARGING_AUTO_REASONS = {"running", "auto-start", "auto-stop", "scheduled-night-charge"}
WAITING_AUTO_REASONS = {"waiting", "waiting-surplus", "averaging"}
EVSE_FAULT_HEALTH_REASONS = {
    "charger-fault",
    "contactor-feedback-mismatch",
    "contactor-lockout-open",
    "contactor-lockout-welded",
}


def _health_code(reason: str) -> int:
    """Return a numeric health code for a given reason label."""
    return HEALTH_CODES.get(reason, 99)


def _normalize_auto_state(state: Any) -> str:
    """Normalize broad Auto-state labels to the supported state machine values."""
    normalized = str(state).strip().lower() if state is not None else "idle"
    if normalized in AUTO_STATE_CODES:
        return normalized
    return "idle"


def _auto_state_code(state: Any) -> int:
    """Return the numeric code for one broad Auto-state label."""
    return AUTO_STATE_CODES.get(_normalize_auto_state(state), 99)


def _base_auto_reason(reason: Any) -> str:
    """Return one normalized health reason without cached-input suffix."""
    return str(reason).replace("-cached", "") if reason is not None else "init"


def _evse_fault_reason(reason: Any) -> str | None:
    """Return one normalized EVSE-fault reason when the health state is fault-like."""
    normalized = _base_auto_reason(reason)
    return normalized if normalized in EVSE_FAULT_HEALTH_REASONS else None


def _normalized_learning_hint(learned_charge_power_state: Any) -> str:
    """Return the normalized learned-power hint used for broad Auto states."""
    return str(learned_charge_power_state).strip().lower() if learned_charge_power_state is not None else "unknown"


def _relay_on_auto_state(learned_state: str) -> str:
    """Return the broad Auto state when charging hardware is already on."""
    return "learning" if learned_state == "learning" else "charging"


def _reason_resolved_auto_state(state_from_reason: str, relay_on: bool, learned_state: str) -> str:
    """Return the final broad Auto state once one health reason already mapped to a broad state."""
    if state_from_reason == "charging":
        return "learning" if relay_on and learned_state == "learning" else "charging"
    return state_from_reason


def _derive_auto_state(
    reason: Any,
    *,
    relay_on: bool = False,
    learned_charge_power_state: Any = None,
) -> str:
    """Collapse detailed health reasons into one explicit broad Auto state."""
    base_reason = _base_auto_reason(reason)
    learned_state = _normalized_learning_hint(learned_charge_power_state)
    state_from_reason = _reason_auto_state(base_reason)
    if state_from_reason is not None:
        return _reason_resolved_auto_state(state_from_reason, relay_on, learned_state)
    if relay_on:
        return _relay_on_auto_state(learned_state)
    return "idle"


AUTO_REASON_STATE_GROUPS: tuple[tuple[set[str], str], ...] = (
    (RECOVERY_AUTO_REASONS, "recovery"),
    (BLOCKED_AUTO_REASONS, "blocked"),
    (CHARGING_AUTO_REASONS, "charging"),
    (WAITING_AUTO_REASONS, "waiting"),
)


def _reason_auto_state(base_reason: str) -> str | None:
    """Return the broad Auto state implied directly by one health reason."""
    if base_reason == "init":
        return "idle"
    for reasons, state in AUTO_REASON_STATE_GROUPS:
        if base_reason in reasons:
            return state
    return None


_CHARGER_TRANSPORT_REASONS = frozenset(
    {"busy", "ownership", "timeout", "offline", "response", "error"}
)


def _normalized_charger_transport_reason(reason: Any) -> str | None:
    """Return one normalized charger-transport reason label when supported."""
    normalized = str(reason).strip().lower() if reason is not None else ""
    return normalized if normalized in _CHARGER_TRANSPORT_REASONS else None


def _charger_transport_health_reason(reason: Any) -> str | None:
    """Return one health-reason label for the current charger-transport issue."""
    normalized = _normalized_charger_transport_reason(reason)
    return None if normalized is None else f"charger-transport-{normalized}"


def _charger_transport_max_age_seconds(svc: Any) -> float:
    """Return how fresh charger-transport diagnostics must be before they stay active."""
    candidates = [2.0]
    worker_poll_seconds = _positive_service_float(svc, "_worker_poll_interval_seconds")
    if worker_poll_seconds is not None:
        candidates.append(worker_poll_seconds * 2.0)
    live_publish_seconds = _positive_service_float(svc, "_dbus_live_publish_interval_seconds")
    if live_publish_seconds is not None:
        candidates.append(live_publish_seconds * 2.0)
    soft_fail_seconds = _positive_service_float(svc, "auto_shelly_soft_fail_seconds")
    if soft_fail_seconds is not None:
        candidates.append(soft_fail_seconds)
    return max(1.0, min(candidates))


def _charger_transport_now(svc: Any, now: float | int | None = None) -> float:
    """Return the timestamp used to judge charger-transport freshness."""
    if now is not None:
        return float(now)
    time_now = getattr(svc, "_time_now", None)
    if callable(time_now):
        raw_value = time_now()
        if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            return float(raw_value)
    return time.time()


def _fresh_charger_transport_timestamp(svc: Any, now: float | int | None = None) -> float | None:
    """Return the timestamp of one still-fresh charger-transport issue."""
    transport_at = _positive_service_float(svc, "_last_charger_transport_at")
    if transport_at is None:
        raw_value = getattr(svc, "_last_charger_transport_at", None)
        if raw_value is None:
            return None
        try:
            transport_at = float(raw_value)
        except (TypeError, ValueError):
            return None
    current = _charger_transport_now(svc, now)
    if abs(current - transport_at) > _charger_transport_max_age_seconds(svc):
        return None
    return float(transport_at)


def _fresh_charger_transport_reason(svc: Any, now: float | int | None = None) -> str | None:
    """Return one still-active charger-transport reason label when present."""
    if _fresh_charger_transport_timestamp(svc, now) is None:
        return None
    return _normalized_charger_transport_reason(getattr(svc, "_last_charger_transport_reason", None))


def _fresh_charger_transport_source(svc: Any, now: float | int | None = None) -> str | None:
    """Return the current charger-transport source label when its issue is still active."""
    if _fresh_charger_transport_timestamp(svc, now) is None:
        return None
    source = str(getattr(svc, "_last_charger_transport_source", "") or "").strip()
    return source or None


def _fresh_charger_transport_detail(svc: Any, now: float | int | None = None) -> str | None:
    """Return the current charger-transport detail text when its issue is still active."""
    if _fresh_charger_transport_timestamp(svc, now) is None:
        return None
    detail = str(getattr(svc, "_last_charger_transport_detail", "") or "").strip()
    return detail or None


def _charger_transport_retry_delay_seconds(svc: Any, reason: Any) -> float:
    """Return one charger-specific retry delay for the given transport failure reason."""
    normalized = _normalized_charger_transport_reason(reason)
    base_delay_seconds = _positive_service_float(svc, "auto_dbus_backoff_base_seconds") or 5.0
    soft_fail_seconds = _positive_service_float(svc, "auto_shelly_soft_fail_seconds") or 10.0
    if normalized == "busy":
        return max(1.0, min(base_delay_seconds, 5.0))
    if normalized == "ownership":
        return max(3.0, base_delay_seconds * 2.0)
    if normalized == "timeout":
        return max(2.0, min(soft_fail_seconds, max(base_delay_seconds * 1.5, 2.0)))
    if normalized == "offline":
        return max(10.0, soft_fail_seconds, base_delay_seconds * 4.0)
    if normalized == "response":
        return max(3.0, base_delay_seconds * 2.0)
    return max(2.0, base_delay_seconds * 2.0)


def _fresh_charger_retry_until(svc: Any, now: float | int | None = None) -> float | None:
    """Return the active charger retry-until timestamp when one is still in the future."""
    retry_until = finite_float_or_none(getattr(svc, "_charger_retry_until", None))
    if retry_until is None:
        return None
    current = _charger_transport_now(svc, now)
    return retry_until if retry_until > current else None


def _fresh_charger_retry_reason(svc: Any, now: float | int | None = None) -> str | None:
    """Return the active charger retry reason while a retry backoff is still active."""
    if _fresh_charger_retry_until(svc, now) is None:
        return None
    return _normalized_charger_transport_reason(getattr(svc, "_charger_retry_reason", None))


def _fresh_charger_retry_source(svc: Any, now: float | int | None = None) -> str | None:
    """Return the active charger retry source while a retry backoff is still active."""
    if _fresh_charger_retry_until(svc, now) is None:
        return None
    source = str(getattr(svc, "_charger_retry_source", "") or "").strip()
    return source or None


def _charger_retry_remaining_seconds(svc: Any, now: float | int | None = None) -> int:
    """Return how many whole seconds remain until the next charger retry may run."""
    retry_until = _fresh_charger_retry_until(svc, now)
    if retry_until is None:
        return -1
    current = _charger_transport_now(svc, now)
    return max(0, int(math.ceil(retry_until - current)))


def _age_seconds(timestamp: float | int | None, now: float | int | None = None) -> int:
    """Return the age of a timestamp in seconds, or -1 if unavailable."""
    if timestamp is None:
        return -1
    current = time.time() if now is None else float(now)
    return max(0, int(current - float(timestamp)))


def _positive_service_float(svc: Any, attr_name: str) -> float | None:
    """Return one positive float attribute from a service-like object."""
    raw_value = getattr(svc, attr_name, None)
    if raw_value is None:
        return None
    try:
        numeric_value = float(raw_value)
    except (TypeError, ValueError):
        return None
    return numeric_value if numeric_value > 0.0 else None


def _confirmed_relay_state_max_age_seconds(svc: Any) -> float:
    """Return how old a confirmed relay sample may be for broad state hints."""
    fallback_candidates = [5.0]
    worker_poll_seconds = _positive_service_float(svc, "_worker_poll_interval_seconds")
    if worker_poll_seconds is not None:
        fallback_candidates.append(worker_poll_seconds * 2.0)
    relay_sync_timeout_seconds = _positive_service_float(svc, "relay_sync_timeout_seconds")
    if relay_sync_timeout_seconds is not None:
        fallback_candidates.append(relay_sync_timeout_seconds)
    return max(1.0, min(fallback_candidates))


def _confirmed_relay_sample(svc: Any) -> tuple[dict[str, Any] | None, Any]:
    """Return the freshest confirmed relay sample candidate from runtime state."""
    pm_status = getattr(svc, "_last_confirmed_pm_status", None)
    captured_at = getattr(svc, "_last_confirmed_pm_status_at", None)
    if pm_status is None and bool(getattr(svc, "_last_pm_status_confirmed", False)):
        pm_status = getattr(svc, "_last_pm_status", None)
        captured_at = getattr(svc, "_last_pm_status_at", None)
    if not isinstance(pm_status, dict):
        return None, None
    return cast(dict[str, Any], pm_status), captured_at


def _confirmed_relay_sample_valid(
    pm_status: dict[str, Any] | None,
    captured_at: Any,
) -> bool:
    """Return whether one confirmed relay sample contains the fields needed for freshness checks."""
    return isinstance(pm_status, dict) and "output" in pm_status and captured_at is not None


def _confirmed_relay_sample_fresh(
    captured_at: float,
    current: float,
    max_age_seconds: float,
) -> bool:
    """Return whether one confirmed relay sample lies inside the accepted age window."""
    age_seconds = current - float(captured_at)
    return -1.0 <= age_seconds <= max_age_seconds


def _confirmed_relay_output_value(pm_status: dict[str, Any]) -> bool:
    """Return the normalized relay output flag from one confirmed PM snapshot."""
    return bool(pm_status.get("output"))


def _fresh_confirmed_relay_sample(
    svc: Any,
    current: float,
) -> tuple[dict[str, Any], float] | None:
    """Return one fresh confirmed relay sample when the state is currently trustworthy."""
    max_age_seconds = _confirmed_relay_state_max_age_seconds(svc)
    pm_status, captured_at = _confirmed_relay_sample(svc)
    if not _confirmed_relay_sample_valid(pm_status, captured_at):
        return None
    assert pm_status is not None
    assert captured_at is not None
    resolved_captured_at = float(captured_at)
    if not _confirmed_relay_sample_fresh(resolved_captured_at, current, max_age_seconds):
        return None
    return pm_status, resolved_captured_at


def _fresh_confirmed_relay_output(svc: Any, now: float | int | None = None) -> bool | None:
    """Return a fresh confirmed relay output, or ``None`` when the state is unknown."""
    current = time.time() if now is None else float(now)
    sample = _fresh_confirmed_relay_sample(svc, current)
    if sample is None:
        return None
    pm_status, _captured_at = sample
    return _confirmed_relay_output_value(pm_status)


def read_version(file_name: str) -> str:
    """Read the version file shipped with the script, if present."""
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(current_dir, file_name)
        with open(file_path, "r", encoding="utf-8") as file:
            line = file.readline()
            return line.split(":")[-1].strip()
    except FileNotFoundError:
        logging.error("File %s not found in the current directory.", file_name)
        return "0.1"


def phase_values(
    total_power: float | int,
    voltage: float | int,
    phase: str,
    voltage_mode: str,
) -> PhaseMeasurements:
    """Split total power into per-phase values and currents."""
    total_power = float(total_power)
    voltage = float(voltage)
    if phase == "3P":
        per_phase_power = total_power / 3.0
        if voltage_mode == "phase":
            phase_voltage = voltage
        else:
            phase_voltage = voltage / math.sqrt(3)
        if not phase_voltage:
            raise ValueError("Invalid 3-phase voltage")
        phase_current = per_phase_power / phase_voltage
        return {
            "L1": {"power": per_phase_power, "voltage": phase_voltage, "current": phase_current},
            "L2": {"power": per_phase_power, "voltage": phase_voltage, "current": phase_current},
            "L3": {"power": per_phase_power, "voltage": phase_voltage, "current": phase_current},
        }

    result = {
        "L1": {"power": 0.0, "voltage": voltage, "current": 0.0},
        "L2": {"power": 0.0, "voltage": voltage, "current": 0.0},
        "L3": {"power": 0.0, "voltage": voltage, "current": 0.0},
    }
    result[phase] = {"power": total_power, "voltage": voltage, "current": total_power / voltage if voltage else 0.0}
    return result


def normalize_phase(phase: Any) -> str:
    """Normalize phase configuration values and validate them."""
    phase_name = str(phase).strip().upper()
    if phase_name == "1P":
        return "L1"
    if phase_name not in ("L1", "L2", "L3", "3P"):
        raise ValueError(f"Invalid Phase '{phase_name}'. Use L1, L2, L3 or 3P.")
    return phase_name


def normalize_mode(mode: Any) -> int:
    """Normalize supported charger modes to a known integer range."""
    try:
        mode = int(mode)
    except (TypeError, ValueError):
        return 0
    return mode if mode in (0, 1, 2) else 0


def mode_uses_auto_logic(mode: Any) -> bool:
    """Return True when the selected mode should follow Auto surplus logic."""
    return normalize_mode(mode) in (1, 2)


def mode_uses_scheduled_logic(mode: Any) -> bool:
    """Return True when the selected mode should follow scheduled/plan logic."""
    return normalize_mode(mode) == 2


def _window_minutes_for_date(
    window_date: date,
    month_windows: dict[int, tuple[TimeWindow, TimeWindow]] | None,
) -> tuple[int, int]:
    """Return configured day-window minutes for one date, with a safe fallback."""
    windows = month_windows or {}
    start_window, end_window = windows.get(window_date.month, ((8, 0), (18, 0)))
    start_hour, start_minute = start_window
    end_hour, end_minute = end_window
    return (start_hour * 60) + start_minute, (end_hour * 60) + end_minute


def _datetime_for_minutes(window_date: date, minute_of_day: int) -> datetime:
    """Return a naive local datetime for one date and minute-of-day value."""
    hour, minute = divmod(int(minute_of_day), 60)
    return datetime(window_date.year, window_date.month, window_date.day, hour, minute)


def _normalized_weekday_candidates(value: Any) -> list[str]:
    """Return lowercase weekday tokens from a string-like input."""
    if value is None:
        return []
    raw_text = str(value).strip()
    if not raw_text:
        return []
    lowered = raw_text.lower().replace(";", ",")
    for separator in ("|", "/", "\\"):
        lowered = lowered.replace(separator, ",")
    return [token.strip() for token in lowered.replace(" ", ",").split(",") if token.strip()]


def normalize_scheduled_enabled_days(
    value: Any,
    fallback: WeekdaySelection = DEFAULT_SCHEDULED_ENABLED_DAYS,
) -> WeekdaySelection:
    """Return one canonical tuple of enabled target weekdays for scheduled mode."""
    if isinstance(value, (tuple, list, set, frozenset)):
        tokens = [str(item).strip().lower() for item in value]
    else:
        tokens = _normalized_weekday_candidates(value)
    if not tokens:
        return tuple(fallback)
    if len(tokens) == 1:
        special = tokens[0]
        if special in {"all", "daily", "everyday", "*"}:
            return tuple(range(7))
        if special in {"weekdays", "weekday", "workdays", "workday", "mon-fri", "mo-fr"}:
            return DEFAULT_SCHEDULED_ENABLED_DAYS
        if special in {"weekend", "weekends", "sat-sun", "sa-su"}:
            return (5, 6)

    normalized: list[int] = []
    for token in tokens:
        if token in WEEKDAY_TOKEN_MAP:
            weekday = WEEKDAY_TOKEN_MAP[token]
            if weekday not in normalized:
                normalized.append(weekday)
            continue
        if "-" not in token:
            continue
        start_token, end_token = [part.strip() for part in token.split("-", 1)]
        start_day = WEEKDAY_TOKEN_MAP.get(start_token)
        end_day = WEEKDAY_TOKEN_MAP.get(end_token)
        if start_day is None or end_day is None:
            continue
        current = start_day
        while True:
            if current not in normalized:
                normalized.append(current)
            if current == end_day:
                break
            current = (current + 1) % 7
    return tuple(normalized) if normalized else tuple(fallback)


def scheduled_enabled_days_text(
    value: Any,
    fallback: WeekdaySelection = DEFAULT_SCHEDULED_ENABLED_DAYS,
) -> str:
    """Return one stable CSV form of scheduled target weekdays."""
    return ",".join(WEEKDAY_LABELS[index] for index in normalize_scheduled_enabled_days(value, fallback))


def normalize_hhmm_text(value: Any, fallback: str = "06:30") -> str:
    """Return one canonical HH:MM text or the fallback when invalid."""
    fallback_window = parse_hhmm(fallback, (6, 30))
    hour, minute = parse_hhmm(value, fallback_window)
    return f"{hour:02d}:{minute:02d}"


def _scheduled_target_day(when: datetime, month_windows: dict[int, tuple[TimeWindow, TimeWindow]] | None) -> date:
    """Return the target morning date controlled by one local timestamp."""
    current_minutes = when.hour * 60 + when.minute
    today = when.date()
    start_minutes, end_minutes = _window_minutes_for_date(today, month_windows)
    if start_minutes < end_minutes and current_minutes >= end_minutes:
        return today + timedelta(days=1)
    return today


def scheduled_mode_snapshot(
    when: datetime,
    month_windows: dict[int, tuple[TimeWindow, TimeWindow]] | None,
    enabled_days: Any,
    delay_seconds: float = 3600.0,
    latest_end_time: Any = "06:30",
) -> ScheduledModeSnapshot:
    """Return the scheduled-mode policy state for one local timestamp."""
    target_date = _scheduled_target_day(when, month_windows)
    target_day_index = int(target_date.weekday())
    enabled_tuple = normalize_scheduled_enabled_days(enabled_days)
    target_enabled = target_day_index in enabled_tuple
    start_minutes, end_minutes = _window_minutes_for_date(target_date, month_windows)
    current_minutes = when.hour * 60 + when.minute
    if start_minutes < end_minutes and start_minutes <= current_minutes < end_minutes and when.date() == target_date:
        return ScheduledModeSnapshot(
            state="auto-window",
            state_code=SCHEDULED_STATE_CODES["auto-window"],
            night_boost_active=False,
            target_day_index=target_day_index,
            target_day_label=WEEKDAY_LABELS[target_day_index],
            target_date_text=target_date.isoformat(),
            target_day_enabled=target_enabled,
        )

    if not target_enabled:
        return ScheduledModeSnapshot(
            state="inactive-day",
            state_code=SCHEDULED_STATE_CODES["inactive-day"],
            night_boost_active=False,
            target_day_index=target_day_index,
            target_day_label=WEEKDAY_LABELS[target_day_index],
            target_date_text=target_date.isoformat(),
            target_day_enabled=False,
        )

    delay_seconds = max(0.0, float(delay_seconds))
    latest_end_hour, latest_end_minute = parse_hhmm(latest_end_time, (6, 30))
    latest_end_dt = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        latest_end_hour,
        latest_end_minute,
    )
    previous_day = target_date - timedelta(days=1)
    _previous_start_minutes, previous_end_minutes = _window_minutes_for_date(previous_day, month_windows)
    fallback_start = _datetime_for_minutes(previous_day, previous_end_minutes) + timedelta(seconds=delay_seconds)
    daytime_start = _datetime_for_minutes(target_date, start_minutes)
    night_boost_end = min(latest_end_dt, daytime_start)

    if when < fallback_start:
        state = "waiting-fallback"
    elif when < night_boost_end:
        state = "night-boost"
    elif when.date() == target_date and (start_minutes < end_minutes and current_minutes >= start_minutes):
        state = "auto-window"
    else:
        state = "after-latest-end"
    return ScheduledModeSnapshot(
        state=state,
        state_code=SCHEDULED_STATE_CODES[state],
        night_boost_active=(state == "night-boost"),
        target_day_index=target_day_index,
        target_day_label=WEEKDAY_LABELS[target_day_index],
        target_date_text=target_date.isoformat(),
        target_day_enabled=True,
    )


def scheduled_night_window_active(
    when: datetime,
    month_windows: dict[int, tuple[TimeWindow, TimeWindow]] | None,
    delay_seconds: float = 3600.0,
) -> bool:
    """Return whether plan mode should force nighttime charging for one local time."""
    return scheduled_mode_snapshot(
        when,
        month_windows,
        DEFAULT_SCHEDULED_ENABLED_DAYS,
        delay_seconds=delay_seconds,
        latest_end_time="23:59",
    ).night_boost_active


def parse_hhmm(value: Any, fallback: TimeWindow) -> TimeWindow:
    """Parse HH:MM time strings with validation and fallback."""
    try:
        hour_text, minute_text = str(value).strip().split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (AttributeError, TypeError, ValueError):
        pass
    return fallback


def _month_in_range(month: int, start_month: int, end_month: int) -> bool:
    """Return whether one month lies inside one possibly wrapping month range."""
    if start_month <= end_month:
        return start_month <= month <= end_month
    return month >= start_month or month <= end_month


def month_in_ranges(month: int, ranges: Iterable[MonthRange]) -> bool:
    """Check whether a month is contained in one or more (start, end) ranges."""
    return any(_month_in_range(month, start_month, end_month) for start_month, end_month in ranges)


def month_window(
    config: ConfigParser,
    month: int,
    default_start: str,
    default_end: str,
) -> tuple[TimeWindow, TimeWindow]:
    """Read monthly time window settings from config with defaults."""
    month_name = datetime(2000, month, 1).strftime("%b")
    start = parse_hhmm(config["DEFAULT"].get(f"Auto{month_name}Start", default_start), parse_hhmm(default_start, (8, 0)))
    end = parse_hhmm(config["DEFAULT"].get(f"Auto{month_name}End", default_end), parse_hhmm(default_end, (18, 0)))
    return start, end


auto_state_code = _auto_state_code
confirmed_relay_state_max_age_seconds = _confirmed_relay_state_max_age_seconds
derive_auto_state = _derive_auto_state
evse_fault_reason = _evse_fault_reason
fresh_confirmed_relay_output = _fresh_confirmed_relay_output
