# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared constants, types, and display formatters for wallbox helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
SCHEDULED_REASON_CODES: dict[str, int] = {
    "disabled": 0,
    "daytime-auto": 1,
    "target-day-disabled": 2,
    "waiting-fallback-delay": 3,
    "night-boost-window": 4,
    "latest-end-reached": 5,
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
    reason: str
    reason_code: int
    night_boost_active: bool
    target_day_index: int
    target_day_label: str
    target_date_text: str
    target_day_enabled: bool
    fallback_start_text: str
    boost_until_text: str


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
