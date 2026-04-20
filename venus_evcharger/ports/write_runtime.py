# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code=attr-defined
# pyright: reportAttributeAccessIssue=false
"""Runtime-facing property surface for the DBus write controller port."""

from __future__ import annotations

import time
from typing import Any, cast

from venus_evcharger.backend.models import (
    PhaseSelection,
    effective_supported_phase_selections,
    normalize_phase_selection,
    normalize_phase_selection_tuple,
)
from venus_evcharger.core.common import DEFAULT_SCHEDULED_ENABLED_DAYS, normalize_hhmm_text, scheduled_enabled_days_text
from venus_evcharger.core.contracts import finite_float_or_none, non_negative_int, normalize_binary_flag


class _WriteControllerRuntimePortMixin:
    @property
    def virtual_mode(self) -> int:
        return non_negative_int(getattr(self._service, "virtual_mode", 0))

    @virtual_mode.setter
    def virtual_mode(self, value: Any) -> None:
        normalize_mode = getattr(self._service, "_normalize_mode", None)
        self._service.virtual_mode = normalize_mode(value) if callable(normalize_mode) else non_negative_int(value)

    @property
    def virtual_autostart(self) -> int:
        return normalize_binary_flag(getattr(self._service, "virtual_autostart", 1), default=1)

    @virtual_autostart.setter
    def virtual_autostart(self, value: Any) -> None:
        self._service.virtual_autostart = normalize_binary_flag(value)

    @property
    def virtual_startstop(self) -> int:
        return normalize_binary_flag(getattr(self._service, "virtual_startstop", 1), default=1)

    @virtual_startstop.setter
    def virtual_startstop(self, value: Any) -> None:
        self._service.virtual_startstop = normalize_binary_flag(value)

    @property
    def virtual_enable(self) -> int:
        return normalize_binary_flag(getattr(self._service, "virtual_enable", 1), default=1)

    @virtual_enable.setter
    def virtual_enable(self, value: Any) -> None:
        self._service.virtual_enable = normalize_binary_flag(value)

    @property
    def auto_manual_override_seconds(self) -> Any:
        return self._service.auto_manual_override_seconds

    @property
    def virtual_set_current(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "virtual_set_current", 0.0)) or 0.0)

    @virtual_set_current.setter
    def virtual_set_current(self, value: Any) -> None:
        self._service.virtual_set_current = float(finite_float_or_none(value) or 0.0)

    @property
    def min_current(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "min_current", 0.0)) or 0.0)

    @min_current.setter
    def min_current(self, value: Any) -> None:
        self._service.min_current = float(finite_float_or_none(value) or 0.0)

    @property
    def max_current(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "max_current", 0.0)) or 0.0)

    @max_current.setter
    def max_current(self, value: Any) -> None:
        self._service.max_current = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_start_surplus_watts(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_start_surplus_watts", 0.0)) or 0.0)

    @auto_start_surplus_watts.setter
    def auto_start_surplus_watts(self, value: Any) -> None:
        self._service.auto_start_surplus_watts = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_stop_surplus_watts(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_stop_surplus_watts", 0.0)) or 0.0)

    @auto_stop_surplus_watts.setter
    def auto_stop_surplus_watts(self, value: Any) -> None:
        self._service.auto_stop_surplus_watts = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_min_soc(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_min_soc", 0.0)) or 0.0)

    @auto_min_soc.setter
    def auto_min_soc(self, value: Any) -> None:
        self._service.auto_min_soc = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_resume_soc(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_resume_soc", 0.0)) or 0.0)

    @auto_resume_soc.setter
    def auto_resume_soc(self, value: Any) -> None:
        self._service.auto_resume_soc = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_start_delay_seconds(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_start_delay_seconds", 0.0)) or 0.0)

    @auto_start_delay_seconds.setter
    def auto_start_delay_seconds(self, value: Any) -> None:
        self._service.auto_start_delay_seconds = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_stop_delay_seconds(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_stop_delay_seconds", 0.0)) or 0.0)

    @auto_stop_delay_seconds.setter
    def auto_stop_delay_seconds(self, value: Any) -> None:
        self._service.auto_stop_delay_seconds = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_scheduled_enabled_days(self) -> str:
        return scheduled_enabled_days_text(
            getattr(self._service, "auto_scheduled_enabled_days", DEFAULT_SCHEDULED_ENABLED_DAYS),
            DEFAULT_SCHEDULED_ENABLED_DAYS,
        )

    @auto_scheduled_enabled_days.setter
    def auto_scheduled_enabled_days(self, value: Any) -> None:
        self._service.auto_scheduled_enabled_days = scheduled_enabled_days_text(value, DEFAULT_SCHEDULED_ENABLED_DAYS)

    @property
    def auto_scheduled_night_start_delay_seconds(self) -> float:
        value = getattr(self._service, "auto_scheduled_night_start_delay_seconds", 0.0)
        return float(finite_float_or_none(value) or 0.0)

    @auto_scheduled_night_start_delay_seconds.setter
    def auto_scheduled_night_start_delay_seconds(self, value: Any) -> None:
        self._service.auto_scheduled_night_start_delay_seconds = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_scheduled_latest_end_time(self) -> str:
        return normalize_hhmm_text(getattr(self._service, "auto_scheduled_latest_end_time", "06:30"), "06:30")

    @auto_scheduled_latest_end_time.setter
    def auto_scheduled_latest_end_time(self, value: Any) -> None:
        self._service.auto_scheduled_latest_end_time = normalize_hhmm_text(value, "06:30")

    @property
    def auto_scheduled_night_current_amps(self) -> float:
        value = getattr(self._service, "auto_scheduled_night_current_amps", 0.0)
        return float(finite_float_or_none(value) or 0.0)

    @auto_scheduled_night_current_amps.setter
    def auto_scheduled_night_current_amps(self, value: Any) -> None:
        self._service.auto_scheduled_night_current_amps = float(finite_float_or_none(value) or 0.0)

    @property
    def _software_update_run_requested_at(self) -> float | None:
        return finite_float_or_none(getattr(self._service, "_software_update_run_requested_at", None))

    @_software_update_run_requested_at.setter
    def _software_update_run_requested_at(self, value: Any) -> None:
        self._service._software_update_run_requested_at = finite_float_or_none(value)

    @property
    def auto_dbus_backoff_base_seconds(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_dbus_backoff_base_seconds", 0.0)) or 0.0)

    @auto_dbus_backoff_base_seconds.setter
    def auto_dbus_backoff_base_seconds(self, value: Any) -> None:
        self._service.auto_dbus_backoff_base_seconds = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_dbus_backoff_max_seconds(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_dbus_backoff_max_seconds", 0.0)) or 0.0)

    @auto_dbus_backoff_max_seconds.setter
    def auto_dbus_backoff_max_seconds(self, value: Any) -> None:
        self._service.auto_dbus_backoff_max_seconds = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_grid_recovery_start_seconds(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_grid_recovery_start_seconds", 0.0)) or 0.0)

    @auto_grid_recovery_start_seconds.setter
    def auto_grid_recovery_start_seconds(self, value: Any) -> None:
        self._service.auto_grid_recovery_start_seconds = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_stop_surplus_delay_seconds(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_stop_surplus_delay_seconds", 0.0)) or 0.0)

    @auto_stop_surplus_delay_seconds.setter
    def auto_stop_surplus_delay_seconds(self, value: Any) -> None:
        self._service.auto_stop_surplus_delay_seconds = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_stop_surplus_volatility_low_watts(self) -> float:
        value = getattr(self._service, "auto_stop_surplus_volatility_low_watts", 0.0)
        return float(finite_float_or_none(value) or 0.0)

    @auto_stop_surplus_volatility_low_watts.setter
    def auto_stop_surplus_volatility_low_watts(self, value: Any) -> None:
        self._service.auto_stop_surplus_volatility_low_watts = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_stop_surplus_volatility_high_watts(self) -> float:
        value = getattr(self._service, "auto_stop_surplus_volatility_high_watts", 0.0)
        return float(finite_float_or_none(value) or 0.0)

    @auto_stop_surplus_volatility_high_watts.setter
    def auto_stop_surplus_volatility_high_watts(self, value: Any) -> None:
        self._service.auto_stop_surplus_volatility_high_watts = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_reference_charge_power_watts(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_reference_charge_power_watts", 0.0)) or 0.0)

    @auto_reference_charge_power_watts.setter
    def auto_reference_charge_power_watts(self, value: Any) -> None:
        self._service.auto_reference_charge_power_watts = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_learn_charge_power_enabled(self) -> int:
        return normalize_binary_flag(getattr(self._service, "auto_learn_charge_power_enabled", 1), default=1)

    @auto_learn_charge_power_enabled.setter
    def auto_learn_charge_power_enabled(self, value: Any) -> None:
        self._service.auto_learn_charge_power_enabled = bool(normalize_binary_flag(value))

    @property
    def auto_learn_charge_power_min_watts(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_learn_charge_power_min_watts", 0.0)) or 0.0)

    @auto_learn_charge_power_min_watts.setter
    def auto_learn_charge_power_min_watts(self, value: Any) -> None:
        self._service.auto_learn_charge_power_min_watts = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_learn_charge_power_alpha(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_learn_charge_power_alpha", 0.0)) or 0.0)

    @auto_learn_charge_power_alpha.setter
    def auto_learn_charge_power_alpha(self, value: Any) -> None:
        self._service.auto_learn_charge_power_alpha = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_learn_charge_power_start_delay_seconds(self) -> float:
        value = getattr(self._service, "auto_learn_charge_power_start_delay_seconds", 0.0)
        return float(finite_float_or_none(value) or 0.0)

    @auto_learn_charge_power_start_delay_seconds.setter
    def auto_learn_charge_power_start_delay_seconds(self, value: Any) -> None:
        self._service.auto_learn_charge_power_start_delay_seconds = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_learn_charge_power_window_seconds(self) -> float:
        value = getattr(self._service, "auto_learn_charge_power_window_seconds", 0.0)
        return float(finite_float_or_none(value) or 0.0)

    @auto_learn_charge_power_window_seconds.setter
    def auto_learn_charge_power_window_seconds(self, value: Any) -> None:
        self._service.auto_learn_charge_power_window_seconds = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_learn_charge_power_max_age_seconds(self) -> float:
        value = getattr(self._service, "auto_learn_charge_power_max_age_seconds", 0.0)
        return float(finite_float_or_none(value) or 0.0)

    @auto_learn_charge_power_max_age_seconds.setter
    def auto_learn_charge_power_max_age_seconds(self, value: Any) -> None:
        self._service.auto_learn_charge_power_max_age_seconds = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_phase_switching_enabled(self) -> int:
        return normalize_binary_flag(getattr(self._service, "auto_phase_switching_enabled", 1), default=1)

    @auto_phase_switching_enabled.setter
    def auto_phase_switching_enabled(self, value: Any) -> None:
        self._service.auto_phase_switching_enabled = bool(normalize_binary_flag(value))

    @property
    def auto_phase_prefer_lowest_when_idle(self) -> int:
        return normalize_binary_flag(getattr(self._service, "auto_phase_prefer_lowest_when_idle", 1), default=1)

    @auto_phase_prefer_lowest_when_idle.setter
    def auto_phase_prefer_lowest_when_idle(self, value: Any) -> None:
        self._service.auto_phase_prefer_lowest_when_idle = bool(normalize_binary_flag(value))

    @property
    def auto_phase_upshift_delay_seconds(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_phase_upshift_delay_seconds", 0.0)) or 0.0)

    @auto_phase_upshift_delay_seconds.setter
    def auto_phase_upshift_delay_seconds(self, value: Any) -> None:
        self._service.auto_phase_upshift_delay_seconds = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_phase_downshift_delay_seconds(self) -> float:
        value = getattr(self._service, "auto_phase_downshift_delay_seconds", 0.0)
        return float(finite_float_or_none(value) or 0.0)

    @auto_phase_downshift_delay_seconds.setter
    def auto_phase_downshift_delay_seconds(self, value: Any) -> None:
        self._service.auto_phase_downshift_delay_seconds = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_phase_upshift_headroom_watts(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_phase_upshift_headroom_watts", 0.0)) or 0.0)

    @auto_phase_upshift_headroom_watts.setter
    def auto_phase_upshift_headroom_watts(self, value: Any) -> None:
        self._service.auto_phase_upshift_headroom_watts = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_phase_downshift_margin_watts(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_phase_downshift_margin_watts", 0.0)) or 0.0)

    @auto_phase_downshift_margin_watts.setter
    def auto_phase_downshift_margin_watts(self, value: Any) -> None:
        self._service.auto_phase_downshift_margin_watts = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_phase_mismatch_retry_seconds(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_phase_mismatch_retry_seconds", 0.0)) or 0.0)

    @auto_phase_mismatch_retry_seconds.setter
    def auto_phase_mismatch_retry_seconds(self, value: Any) -> None:
        self._service.auto_phase_mismatch_retry_seconds = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_phase_mismatch_lockout_count(self) -> int:
        return non_negative_int(getattr(self._service, "auto_phase_mismatch_lockout_count", 0))

    @auto_phase_mismatch_lockout_count.setter
    def auto_phase_mismatch_lockout_count(self, value: Any) -> None:
        self._service.auto_phase_mismatch_lockout_count = non_negative_int(value)

    @property
    def auto_phase_mismatch_lockout_seconds(self) -> float:
        value = getattr(self._service, "auto_phase_mismatch_lockout_seconds", 0.0)
        return float(finite_float_or_none(value) or 0.0)

    @auto_phase_mismatch_lockout_seconds.setter
    def auto_phase_mismatch_lockout_seconds(self, value: Any) -> None:
        self._service.auto_phase_mismatch_lockout_seconds = float(finite_float_or_none(value) or 0.0)

    @property
    def supported_phase_selections(self) -> tuple[str, ...]:
        normalized: tuple[PhaseSelection, ...] = normalize_phase_selection_tuple(
            getattr(self._service, "supported_phase_selections", ("P1",)),
            ("P1",),
        )
        current_time = self.time_now() if callable(getattr(self._service, "_time_now", None)) else time.time()
        return effective_supported_phase_selections(
            normalized,
            lockout_selection=getattr(self._service, "_phase_switch_lockout_selection", None),
            lockout_until=getattr(self._service, "_phase_switch_lockout_until", None),
            now=current_time,
        )

    @supported_phase_selections.setter
    def supported_phase_selections(self, value: Any) -> None:
        self._service.supported_phase_selections = normalize_phase_selection_tuple(value, ("P1",))

    @property
    def requested_phase_selection(self) -> str:
        normalized: PhaseSelection = normalize_phase_selection(
            getattr(self._service, "requested_phase_selection", self.supported_phase_selections[0]),
            cast(PhaseSelection, self.supported_phase_selections[0]),
        )
        return str(normalized)

    @requested_phase_selection.setter
    def requested_phase_selection(self, value: Any) -> None:
        fallback = cast(PhaseSelection, self.supported_phase_selections[0])
        self._service.requested_phase_selection = normalize_phase_selection(value, fallback)

    @property
    def active_phase_selection(self) -> str:
        normalized: PhaseSelection = normalize_phase_selection(
            getattr(self._service, "active_phase_selection", self.requested_phase_selection),
            cast(PhaseSelection, self.requested_phase_selection),
        )
        return str(normalized)

    @active_phase_selection.setter
    def active_phase_selection(self, value: Any) -> None:
        fallback = cast(PhaseSelection, self.requested_phase_selection)
        self._service.active_phase_selection = normalize_phase_selection(value, fallback)

    @property
    def auto_mode_cutover_pending(self) -> bool:
        return bool(getattr(self._service, "_auto_mode_cutover_pending", False))

    @auto_mode_cutover_pending.setter
    def auto_mode_cutover_pending(self, value: Any) -> None:
        self._service._auto_mode_cutover_pending = bool(value)

    @property
    def ignore_min_offtime_once(self) -> bool:
        return bool(getattr(self._service, "_ignore_min_offtime_once", False))

    @ignore_min_offtime_once.setter
    def ignore_min_offtime_once(self, value: Any) -> None:
        self._service._ignore_min_offtime_once = bool(value)
