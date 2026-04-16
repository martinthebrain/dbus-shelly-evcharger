# SPDX-License-Identifier: GPL-3.0-or-later
"""Small controller ports that decouple controllers from the full service object."""

from __future__ import annotations

from typing import Any


from shelly_wallbox.core.contracts import (
    non_negative_int,
    normalize_binary_flag,
    normalize_learning_state,
    normalize_optional_binary_state,
    normalized_auto_state_pair,
)

from .base import _BaseServicePort

class UpdateCyclePort(_BaseServicePort):
    """Expose the update-cycle surface needed by ``UpdateCycleController``."""

    _ALLOWED_ATTRS = {
        "_startup_manual_target",
        "auto_policy",
        "virtual_mode",
        "auto_shelly_soft_fail_seconds",
        "_last_health_reason",
        "_last_health_code",
        "_last_auto_state",
        "_last_auto_state_code",
        "_last_status_source",
        "_last_charger_fault_active",
        "charging_started_at",
        "energy_at_start",
        "virtual_startstop",
        "virtual_enable",
        "phase",
        "voltage_mode",
        "max_current",
        "last_status",
        "_last_pm_status",
        "_last_pm_status_at",
        "_last_pm_status_confirmed",
        "_last_confirmed_pm_status",
        "_last_confirmed_pm_status_at",
        "_last_voltage",
        "auto_input_cache_seconds",
        "_auto_cached_inputs_used",
        "_error_state",
        "_last_pv_value",
        "_last_pv_at",
        "_last_grid_value",
        "_last_grid_at",
        "_last_battery_soc_value",
        "_last_battery_soc_at",
        "auto_audit_log",
        "_last_auto_metrics",
        "charging_threshold_watts",
        "idle_status",
        "min_current",
        "_last_successful_update_at",
        "_last_recovery_attempt_at",
        "last_update",
        "service_name",
        "_dbusservice",
        "learned_charge_power_watts",
        "learned_charge_power_updated_at",
        "learned_charge_power_state",
        "learned_charge_power_learning_since",
        "learned_charge_power_sample_count",
        "learned_charge_power_phase",
        "learned_charge_power_voltage",
        "learned_charge_power_signature_mismatch_sessions",
        "learned_charge_power_signature_checked_session_started_at",
        "auto_learn_charge_power_enabled",
        "auto_learn_charge_power_min_watts",
        "auto_learn_charge_power_alpha",
        "auto_learn_charge_power_start_delay_seconds",
        "auto_learn_charge_power_window_seconds",
        "auto_learn_charge_power_max_age_seconds",
        "relay_sync_timeout_seconds",
        "_relay_sync_expected_state",
        "_relay_sync_requested_at",
        "_relay_sync_deadline_at",
        "_relay_sync_failure_reported",
        "_ignore_min_offtime_once",
        "requested_phase_selection",
        "active_phase_selection",
        "supported_phase_selections",
        "_phase_switch_pending_selection",
        "_phase_switch_state",
        "_phase_switch_requested_at",
        "_phase_switch_stable_until",
        "_phase_switch_resume_relay",
        "_auto_phase_target_candidate",
        "_auto_phase_target_since",
    }

    _ALLOWED_METHODS = {
        "_mode_uses_auto_logic",
        "_queue_relay_command",
        "_mark_failure",
        "_mark_recovery",
        "_warning_throttled",
        "_ensure_observability_state",
        "_publish_energy_time_measurements",
        "_publish_config_paths",
        "_publish_diagnostic_paths",
        "_time_now",
        "_save_runtime_state",
        "_watchdog_recover",
        "_ensure_auto_input_helper_process",
        "_refresh_auto_input_snapshot",
        "_get_worker_snapshot",
        "_set_health",
        "_publish_live_measurements",
        "_bump_update_index",
        "_safe_float",
        "_publish_local_pm_status",
        "_auto_decide_relay",
        "_state_summary",
        "_peek_pending_relay_command",
        "_apply_phase_selection",
        "_phase_selection_requires_pause",
    }

    _MUTABLE_ATTRS = {
        "_startup_manual_target",
        "_last_health_reason",
        "_last_health_code",
        "_last_auto_state",
        "_last_auto_state_code",
        "_last_status_source",
        "_last_charger_fault_active",
        "charging_started_at",
        "energy_at_start",
        "virtual_startstop",
        "last_status",
        "_last_pm_status",
        "_last_pm_status_at",
        "_last_pm_status_confirmed",
        "_last_confirmed_pm_status",
        "_last_confirmed_pm_status_at",
        "_last_voltage",
        "_auto_cached_inputs_used",
        "_last_pv_value",
        "_last_pv_at",
        "_last_grid_value",
        "_last_grid_at",
        "_last_battery_soc_value",
        "_last_battery_soc_at",
        "_last_successful_update_at",
        "_last_recovery_attempt_at",
        "last_update",
        "learned_charge_power_watts",
        "learned_charge_power_updated_at",
        "learned_charge_power_state",
        "learned_charge_power_learning_since",
        "learned_charge_power_sample_count",
        "learned_charge_power_phase",
        "learned_charge_power_voltage",
        "learned_charge_power_signature_mismatch_sessions",
        "learned_charge_power_signature_checked_session_started_at",
        "_relay_sync_expected_state",
        "_relay_sync_requested_at",
        "_relay_sync_deadline_at",
        "_relay_sync_failure_reported",
        "_ignore_min_offtime_once",
        "requested_phase_selection",
        "active_phase_selection",
        "supported_phase_selections",
        "_phase_switch_pending_selection",
        "_phase_switch_state",
        "_phase_switch_requested_at",
        "_phase_switch_stable_until",
        "_phase_switch_resume_relay",
        "_auto_phase_target_candidate",
        "_auto_phase_target_since",
    }

    def __init__(self, service: Any) -> None:
        super().__init__(service)

    @property
    def _startup_manual_target(self) -> bool | None:
        return normalize_optional_binary_state(getattr(self._service, "_startup_manual_target", None))

    @_startup_manual_target.setter
    def _startup_manual_target(self, value: Any) -> None:
        self._service._startup_manual_target = normalize_optional_binary_state(value)

    @property
    def virtual_mode(self) -> int:
        return non_negative_int(getattr(self._service, "virtual_mode", 0))

    @virtual_mode.setter
    def virtual_mode(self, value: Any) -> None:
        normalize_mode = getattr(self._service, "_normalize_mode", None)
        self._service.virtual_mode = (
            normalize_mode(value) if callable(normalize_mode) else non_negative_int(value)
        )

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
    def _last_auto_state(self) -> str:
        state, _code = normalized_auto_state_pair(
            getattr(self._service, "_last_auto_state", "idle"),
            getattr(self._service, "_last_auto_state_code", 0),
        )
        return state

    @_last_auto_state.setter
    def _last_auto_state(self, value: Any) -> None:
        state, code = normalized_auto_state_pair(value, getattr(self._service, "_last_auto_state_code", 0))
        self._service._last_auto_state = state
        self._service._last_auto_state_code = code

    @property
    def _last_auto_state_code(self) -> int:
        _state, code = normalized_auto_state_pair(
            getattr(self._service, "_last_auto_state", "idle"),
            getattr(self._service, "_last_auto_state_code", 0),
        )
        return code

    @_last_auto_state_code.setter
    def _last_auto_state_code(self, value: Any) -> None:
        state, code = normalized_auto_state_pair(getattr(self._service, "_last_auto_state", "idle"), value)
        self._service._last_auto_state = state
        self._service._last_auto_state_code = code

    @property
    def _last_pm_status_confirmed(self) -> bool:
        return bool(getattr(self._service, "_last_pm_status_confirmed", False))

    @_last_pm_status_confirmed.setter
    def _last_pm_status_confirmed(self, value: Any) -> None:
        self._service._last_pm_status_confirmed = bool(value)

    @property
    def learned_charge_power_state(self) -> str:
        return normalize_learning_state(getattr(self._service, "learned_charge_power_state", "unknown"))

    @learned_charge_power_state.setter
    def learned_charge_power_state(self, value: Any) -> None:
        self._service.learned_charge_power_state = normalize_learning_state(value)
