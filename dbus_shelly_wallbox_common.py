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
from typing import Any

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
}


def _health_code(reason: str) -> int:
    """Return a numeric health code for a given reason label."""
    return HEALTH_CODES.get(reason, 99)


def _age_seconds(timestamp: float | int | None, now: float | int | None = None) -> int:
    """Return the age of a timestamp in seconds, or -1 if unavailable."""
    if timestamp is None:
        return -1
    current = time.time() if now is None else float(now)
    return max(0, int(current - float(timestamp)))


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
    phase = str(phase).strip().upper()
    if phase == "1P":
        return "L1"
    if phase not in ("L1", "L2", "L3", "3P"):
        raise ValueError(f"Invalid Phase '{phase}'. Use L1, L2, L3 or 3P.")
    return phase


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
