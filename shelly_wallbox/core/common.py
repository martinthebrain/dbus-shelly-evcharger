# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared constants and helper functions for the Shelly wallbox service."""

from __future__ import annotations

from configparser import ConfigParser
from collections.abc import Iterable
import logging
import math
import os
import time
from datetime import datetime
from typing import Any, cast

PhaseMeasurements = dict[str, dict[str, float]]
TimeWindow = tuple[int, int]
MonthRange = tuple[int, int]


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
}

AUTO_STATE_CODES: dict[str, int] = {
    "idle": 0,
    "waiting": 1,
    "learning": 2,
    "charging": 3,
    "blocked": 4,
    "recovery": 5,
}
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
}
CHARGING_AUTO_REASONS = {"running", "auto-start", "auto-stop"}
WAITING_AUTO_REASONS = {"waiting", "waiting-surplus", "averaging"}


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


def _derive_auto_state(
    reason: Any,
    *,
    relay_on: bool = False,
    learned_charge_power_state: Any = None,
) -> str:
    """Collapse detailed health reasons into one explicit broad Auto state."""
    base_reason = str(reason).replace("-cached", "") if reason is not None else "init"
    learned_state = str(learned_charge_power_state).strip().lower() if learned_charge_power_state is not None else "unknown"
    state_from_reason = _reason_auto_state(base_reason)
    if state_from_reason is not None:
        if state_from_reason == "charging" and relay_on and learned_state == "learning":
            return "learning"
        return state_from_reason
    if relay_on:
        return "learning" if learned_state == "learning" else "charging"
    return "idle"


def _reason_auto_state(base_reason: str) -> str | None:
    """Return the broad Auto state implied directly by one health reason."""
    if base_reason in RECOVERY_AUTO_REASONS:
        return "recovery"
    if base_reason in BLOCKED_AUTO_REASONS:
        return "blocked"
    if base_reason in CHARGING_AUTO_REASONS:
        return "charging"
    if base_reason in WAITING_AUTO_REASONS:
        return "waiting"
    if base_reason == "init":
        return "idle"
    return None


def _age_seconds(timestamp: float | int | None, now: float | int | None = None) -> int:
    """Return the age of a timestamp in seconds, or -1 if unavailable."""
    if timestamp is None:
        return -1
    current = time.time() if now is None else float(now)
    return max(0, int(current - float(timestamp)))


def _confirmed_relay_state_max_age_seconds(svc: Any) -> float:
    """Return how old a confirmed relay sample may be for broad state hints."""
    fallback_candidates = [5.0]
    worker_poll_seconds = getattr(svc, "_worker_poll_interval_seconds", None)
    if worker_poll_seconds is not None:
        try:
            worker_poll_seconds = float(worker_poll_seconds)
        except (TypeError, ValueError):
            worker_poll_seconds = None
        if worker_poll_seconds is not None and worker_poll_seconds > 0:
            fallback_candidates.append(worker_poll_seconds * 2.0)
    relay_sync_timeout_seconds = getattr(svc, "relay_sync_timeout_seconds", None)
    if relay_sync_timeout_seconds is not None:
        try:
            relay_sync_timeout_seconds = float(relay_sync_timeout_seconds)
        except (TypeError, ValueError):
            relay_sync_timeout_seconds = None
        if relay_sync_timeout_seconds is not None and relay_sync_timeout_seconds > 0:
            fallback_candidates.append(relay_sync_timeout_seconds)
    return max(1.0, min(fallback_candidates))


def _fresh_confirmed_relay_output(svc: Any, now: float | int | None = None) -> bool | None:
    """Return a fresh confirmed relay output, or ``None`` when the state is unknown."""
    current = time.time() if now is None else float(now)
    max_age_seconds = _confirmed_relay_state_max_age_seconds(svc)
    pm_status = getattr(svc, "_last_confirmed_pm_status", None)
    captured_at = getattr(svc, "_last_confirmed_pm_status_at", None)
    if pm_status is None and bool(getattr(svc, "_last_pm_status_confirmed", False)):
        pm_status = getattr(svc, "_last_pm_status", None)
        captured_at = getattr(svc, "_last_pm_status_at", None)
    if not (isinstance(pm_status, dict) and "output" in pm_status and captured_at is not None):
        return None
    age_seconds = current - float(captured_at)
    if age_seconds < -1.0:
        return None
    if age_seconds > max_age_seconds:
        return None
    return bool(cast(dict[str, Any], pm_status).get("output"))


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


def month_in_ranges(month: int, ranges: Iterable[MonthRange]) -> bool:
    """Check whether a month is contained in one or more (start, end) ranges."""
    for start_month, end_month in ranges:
        if start_month <= end_month:
            if start_month <= month <= end_month:
                return True
        elif month >= start_month or month <= end_month:
            return True
    return False


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
fresh_confirmed_relay_output = _fresh_confirmed_relay_output
