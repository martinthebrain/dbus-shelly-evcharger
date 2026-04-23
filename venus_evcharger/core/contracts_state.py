# SPDX-License-Identifier: GPL-3.0-or-later
"""Contracts for the local State API v1 payloads."""

from __future__ import annotations

from typing import Any, Mapping

from venus_evcharger.core.contracts_basic import (
    finite_float_or_none,
    non_negative_float_or_none,
    non_negative_int,
    normalize_binary_flag,
    normalized_auto_state_pair,
)
from venus_evcharger.core.contracts_outward import normalized_software_update_state_fields

STATE_API_VERSIONS = frozenset({"v1"})
STATE_API_KINDS = frozenset(
    {
        "build",
        "contracts",
        "healthz",
        "summary",
        "runtime",
        "operational",
        "dbus-diagnostics",
        "topology",
        "update",
        "victron-bias-recommendation",
        "config-effective",
        "health",
        "version",
    }
)


def _normalized_text(value: Any, default: str = "") -> str:
    text = "" if value is None else str(value).strip()
    return text or default


def _optional_float(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return float(value)


def normalized_state_api_version(value: Any) -> str:
    version = _normalized_text(value)
    return version if version in STATE_API_VERSIONS else "v1"


def normalized_state_api_kind(value: Any, *, default: str = "summary") -> str:
    normalized_default = default if default in STATE_API_KINDS else "summary"
    kind = _normalized_text(value).lower()
    return kind if kind in STATE_API_KINDS else normalized_default


def normalized_state_api_summary_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    return {
        "ok": bool(normalize_binary_flag(raw.get("ok", 1))),
        "api_version": normalized_state_api_version(raw.get("api_version")),
        "kind": normalized_state_api_kind(raw.get("kind"), default="summary"),
        "summary": _normalized_text(raw.get("summary")),
    }


def normalized_state_api_runtime_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    state = raw.get("state")
    return {
        "ok": bool(normalize_binary_flag(raw.get("ok", 1))),
        "api_version": normalized_state_api_version(raw.get("api_version")),
        "kind": normalized_state_api_kind(raw.get("kind"), default="runtime"),
        "state": dict(state) if isinstance(state, Mapping) else {},
    }


def _normalized_generic_mapping(value: Any) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized[str(key)] = item
    return normalized


def _normalized_state_mapping_fields(payload: Mapping[str, Any] | None, *, kind: str) -> dict[str, Any]:
    raw = dict(payload or {})
    return {
        "ok": bool(normalize_binary_flag(raw.get("ok", 1))),
        "api_version": normalized_state_api_version(raw.get("api_version")),
        "kind": normalized_state_api_kind(raw.get("kind"), default=kind),
        "state": _normalized_generic_mapping(raw.get("state")),
    }


def normalized_state_api_operational_state_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    auto_state, auto_state_code = normalized_auto_state_pair(raw.get("auto_state"), raw.get("auto_state_code"))
    software_update_state, software_update_state_code, software_update_available, software_update_no_update_active = (
        normalized_software_update_state_fields(
            raw.get("software_update_state"),
            raw.get("software_update_available"),
            raw.get("software_update_no_update_active"),
        )
    )
    return {
        "mode": non_negative_int(raw.get("mode")),
        "enable": normalize_binary_flag(raw.get("enable")),
        "startstop": normalize_binary_flag(raw.get("startstop")),
        "autostart": normalize_binary_flag(raw.get("autostart")),
        "active_phase_selection": _normalized_text(raw.get("active_phase_selection"), "P1"),
        "requested_phase_selection": _normalized_text(raw.get("requested_phase_selection"), "P1"),
        "backend_mode": _normalized_text(raw.get("backend_mode"), "combined"),
        "meter_backend": _normalized_text(raw.get("meter_backend"), "na"),
        "switch_backend": _normalized_text(raw.get("switch_backend"), "na"),
        "charger_backend": _normalized_text(raw.get("charger_backend"), "na"),
        "auto_state": auto_state,
        "auto_state_code": auto_state_code,
        "fault_active": normalize_binary_flag(raw.get("fault_active")),
        "fault_reason": _normalized_text(raw.get("fault_reason"), "na"),
        "software_update_state": software_update_state,
        "software_update_state_code": software_update_state_code,
        "software_update_available": software_update_available,
        "software_update_no_update_active": software_update_no_update_active,
        "runtime_overrides_active": normalize_binary_flag(raw.get("runtime_overrides_active")),
        "runtime_overrides_path": _normalized_text(raw.get("runtime_overrides_path")),
        "combined_battery_soc": non_negative_float_or_none(raw.get("combined_battery_soc")),
        "combined_battery_source_count": non_negative_int(raw.get("combined_battery_source_count")),
        "combined_battery_online_source_count": non_negative_int(raw.get("combined_battery_online_source_count")),
        "combined_battery_charge_power_w": non_negative_float_or_none(raw.get("combined_battery_charge_power_w")),
        "combined_battery_discharge_power_w": non_negative_float_or_none(raw.get("combined_battery_discharge_power_w")),
        "combined_battery_net_power_w": _optional_float(raw.get("combined_battery_net_power_w")),
        "combined_battery_ac_power_w": _optional_float(raw.get("combined_battery_ac_power_w")),
        "combined_battery_pv_input_power_w": non_negative_float_or_none(raw.get("combined_battery_pv_input_power_w")),
        "combined_battery_grid_interaction_w": _optional_float(raw.get("combined_battery_grid_interaction_w")),
        "combined_battery_headroom_charge_w": non_negative_float_or_none(raw.get("combined_battery_headroom_charge_w")),
        "combined_battery_headroom_discharge_w": non_negative_float_or_none(
            raw.get("combined_battery_headroom_discharge_w")
        ),
        "expected_near_term_export_w": non_negative_float_or_none(raw.get("expected_near_term_export_w")),
        "expected_near_term_import_w": non_negative_float_or_none(raw.get("expected_near_term_import_w")),
        "battery_discharge_balance_mode": _normalized_text(raw.get("battery_discharge_balance_mode")),
        "battery_discharge_balance_target_distribution_mode": _normalized_text(
            raw.get("battery_discharge_balance_target_distribution_mode")
        ),
        "battery_discharge_balance_error_w": non_negative_float_or_none(raw.get("battery_discharge_balance_error_w")),
        "battery_discharge_balance_max_abs_error_w": non_negative_float_or_none(
            raw.get("battery_discharge_balance_max_abs_error_w")
        ),
        "battery_discharge_balance_total_discharge_w": non_negative_float_or_none(
            raw.get("battery_discharge_balance_total_discharge_w")
        ),
        "battery_discharge_balance_eligible_source_count": non_negative_int(
            raw.get("battery_discharge_balance_eligible_source_count")
        ),
        "battery_discharge_balance_active_source_count": non_negative_int(
            raw.get("battery_discharge_balance_active_source_count")
        ),
        "battery_discharge_balance_control_candidate_count": non_negative_int(
            raw.get("battery_discharge_balance_control_candidate_count")
        ),
        "battery_discharge_balance_control_ready_count": non_negative_int(
            raw.get("battery_discharge_balance_control_ready_count")
        ),
        "battery_discharge_balance_supported_control_source_count": non_negative_int(
            raw.get("battery_discharge_balance_supported_control_source_count")
        ),
        "battery_discharge_balance_experimental_control_source_count": non_negative_int(
            raw.get("battery_discharge_balance_experimental_control_source_count")
        ),
        "battery_discharge_balance_policy_enabled": normalize_binary_flag(
            raw.get("battery_discharge_balance_policy_enabled")
        ),
        "battery_discharge_balance_warning_active": normalize_binary_flag(
            raw.get("battery_discharge_balance_warning_active")
        ),
        "battery_discharge_balance_warning_error_w": non_negative_float_or_none(
            raw.get("battery_discharge_balance_warning_error_w")
        ),
        "battery_discharge_balance_warn_threshold_w": non_negative_float_or_none(
            raw.get("battery_discharge_balance_warn_threshold_w")
        ),
        "battery_discharge_balance_bias_mode": _normalized_text(raw.get("battery_discharge_balance_bias_mode")),
        "battery_discharge_balance_bias_gate_active": normalize_binary_flag(
            raw.get("battery_discharge_balance_bias_gate_active")
        ),
        "battery_discharge_balance_bias_start_error_w": non_negative_float_or_none(
            raw.get("battery_discharge_balance_bias_start_error_w")
        ),
        "battery_discharge_balance_bias_penalty_w": non_negative_float_or_none(
            raw.get("battery_discharge_balance_bias_penalty_w")
        ),
        "battery_discharge_balance_coordination_policy_enabled": normalize_binary_flag(
            raw.get("battery_discharge_balance_coordination_policy_enabled")
        ),
        "battery_discharge_balance_coordination_support_mode": _normalized_text(
            raw.get("battery_discharge_balance_coordination_support_mode")
        ),
        "battery_discharge_balance_coordination_feasibility": _normalized_text(
            raw.get("battery_discharge_balance_coordination_feasibility")
        ),
        "battery_discharge_balance_coordination_gate_active": normalize_binary_flag(
            raw.get("battery_discharge_balance_coordination_gate_active")
        ),
        "battery_discharge_balance_coordination_start_error_w": non_negative_float_or_none(
            raw.get("battery_discharge_balance_coordination_start_error_w")
        ),
        "battery_discharge_balance_coordination_penalty_w": non_negative_float_or_none(
            raw.get("battery_discharge_balance_coordination_penalty_w")
        ),
        "battery_discharge_balance_coordination_advisory_active": normalize_binary_flag(
            raw.get("battery_discharge_balance_coordination_advisory_active")
        ),
        "battery_discharge_balance_coordination_advisory_reason": _normalized_text(
            raw.get("battery_discharge_balance_coordination_advisory_reason")
        ),
        "battery_discharge_balance_victron_bias_enabled": normalize_binary_flag(
            raw.get("battery_discharge_balance_victron_bias_enabled")
        ),
        "battery_discharge_balance_victron_bias_active": normalize_binary_flag(
            raw.get("battery_discharge_balance_victron_bias_active")
        ),
        "battery_discharge_balance_victron_bias_source_id": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_source_id")
        ),
        "battery_discharge_balance_victron_bias_topology_key": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_topology_key")
        ),
        "battery_discharge_balance_victron_bias_activation_mode": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_activation_mode")
        ),
        "battery_discharge_balance_victron_bias_activation_gate_active": normalize_binary_flag(
            raw.get("battery_discharge_balance_victron_bias_activation_gate_active")
        ),
        "battery_discharge_balance_victron_bias_support_mode": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_support_mode")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_key": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_key")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_action_direction": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_action_direction")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_site_regime": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_site_regime")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_direction": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_direction")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_day_phase": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_day_phase")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_reserve_phase": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_reserve_phase")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_ev_phase": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_ev_phase")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_pv_phase": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_pv_phase")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_battery_limit_phase": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_battery_limit_phase")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_sample_count": non_negative_int(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_sample_count")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_response_delay_seconds": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_response_delay_seconds")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_estimated_gain": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_estimated_gain")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_overshoot_count": non_negative_int(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_overshoot_count")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_settled_count": non_negative_int(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_settled_count")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_stability_score": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_stability_score")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_regime_consistency_score": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_regime_consistency_score")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_response_variance_score": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_response_variance_score")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_reproducibility_score": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_reproducibility_score")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second")
        ),
        "battery_discharge_balance_victron_bias_learning_profile_preferred_bias_limit_watts": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_learning_profile_preferred_bias_limit_watts")
        ),
        "battery_discharge_balance_victron_bias_source_error_w": finite_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_source_error_w")
        ),
        "battery_discharge_balance_victron_bias_pid_output_w": finite_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_pid_output_w")
        ),
        "battery_discharge_balance_victron_bias_setpoint_w": finite_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_setpoint_w")
        ),
        "battery_discharge_balance_victron_bias_telemetry_clean": normalize_binary_flag(
            raw.get("battery_discharge_balance_victron_bias_telemetry_clean")
        ),
        "battery_discharge_balance_victron_bias_telemetry_clean_reason": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_telemetry_clean_reason")
        ),
        "battery_discharge_balance_victron_bias_response_delay_seconds": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_response_delay_seconds")
        ),
        "battery_discharge_balance_victron_bias_estimated_gain": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_estimated_gain")
        ),
        "battery_discharge_balance_victron_bias_overshoot_active": normalize_binary_flag(
            raw.get("battery_discharge_balance_victron_bias_overshoot_active")
        ),
        "battery_discharge_balance_victron_bias_overshoot_count": non_negative_int(
            raw.get("battery_discharge_balance_victron_bias_overshoot_count")
        ),
        "battery_discharge_balance_victron_bias_overshoot_cooldown_active": normalize_binary_flag(
            raw.get("battery_discharge_balance_victron_bias_overshoot_cooldown_active")
        ),
        "battery_discharge_balance_victron_bias_overshoot_cooldown_reason": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_overshoot_cooldown_reason")
        ),
        "battery_discharge_balance_victron_bias_overshoot_cooldown_until": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_overshoot_cooldown_until")
        ),
        "battery_discharge_balance_victron_bias_settling_active": normalize_binary_flag(
            raw.get("battery_discharge_balance_victron_bias_settling_active")
        ),
        "battery_discharge_balance_victron_bias_settled_count": non_negative_int(
            raw.get("battery_discharge_balance_victron_bias_settled_count")
        ),
        "battery_discharge_balance_victron_bias_stability_score": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_stability_score")
        ),
        "battery_discharge_balance_victron_bias_oscillation_lockout_enabled": normalize_binary_flag(
            raw.get("battery_discharge_balance_victron_bias_oscillation_lockout_enabled")
        ),
        "battery_discharge_balance_victron_bias_oscillation_lockout_active": normalize_binary_flag(
            raw.get("battery_discharge_balance_victron_bias_oscillation_lockout_active")
        ),
        "battery_discharge_balance_victron_bias_oscillation_lockout_reason": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_oscillation_lockout_reason")
        ),
        "battery_discharge_balance_victron_bias_oscillation_lockout_until": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_oscillation_lockout_until")
        ),
        "battery_discharge_balance_victron_bias_oscillation_direction_change_count": non_negative_int(
            raw.get("battery_discharge_balance_victron_bias_oscillation_direction_change_count")
        ),
        "battery_discharge_balance_victron_bias_recommended_kp": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_recommended_kp")
        ),
        "battery_discharge_balance_victron_bias_recommended_ki": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_recommended_ki")
        ),
        "battery_discharge_balance_victron_bias_recommended_kd": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_recommended_kd")
        ),
        "battery_discharge_balance_victron_bias_recommended_deadband_watts": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_recommended_deadband_watts")
        ),
        "battery_discharge_balance_victron_bias_recommended_max_abs_watts": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_recommended_max_abs_watts")
        ),
        "battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second")
        ),
        "battery_discharge_balance_victron_bias_recommended_activation_mode": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_recommended_activation_mode")
        ),
        "battery_discharge_balance_victron_bias_recommendation_confidence": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_recommendation_confidence")
        ),
        "battery_discharge_balance_victron_bias_recommendation_regime_consistency_score": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_recommendation_regime_consistency_score")
        ),
        "battery_discharge_balance_victron_bias_recommendation_response_variance_score": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_recommendation_response_variance_score")
        ),
        "battery_discharge_balance_victron_bias_recommendation_reproducibility_score": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_recommendation_reproducibility_score")
        ),
        "battery_discharge_balance_victron_bias_recommendation_reason": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_recommendation_reason")
        ),
        "battery_discharge_balance_victron_bias_recommendation_profile_key": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_recommendation_profile_key")
        ),
        "battery_discharge_balance_victron_bias_recommendation_ini_snippet": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_recommendation_ini_snippet")
        ),
        "battery_discharge_balance_victron_bias_recommendation_hint": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_recommendation_hint")
        ),
        "battery_discharge_balance_victron_bias_auto_apply_enabled": normalize_binary_flag(
            raw.get("battery_discharge_balance_victron_bias_auto_apply_enabled")
        ),
        "battery_discharge_balance_victron_bias_auto_apply_active": normalize_binary_flag(
            raw.get("battery_discharge_balance_victron_bias_auto_apply_active")
        ),
        "battery_discharge_balance_victron_bias_auto_apply_reason": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_auto_apply_reason")
        ),
        "battery_discharge_balance_victron_bias_auto_apply_generation": non_negative_int(
            raw.get("battery_discharge_balance_victron_bias_auto_apply_generation")
        ),
        "battery_discharge_balance_victron_bias_auto_apply_observation_window_active": normalize_binary_flag(
            raw.get("battery_discharge_balance_victron_bias_auto_apply_observation_window_active")
        ),
        "battery_discharge_balance_victron_bias_auto_apply_observation_window_until": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_auto_apply_observation_window_until")
        ),
        "battery_discharge_balance_victron_bias_auto_apply_last_param": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_auto_apply_last_param")
        ),
        "battery_discharge_balance_victron_bias_auto_apply_suspend_active": normalize_binary_flag(
            raw.get("battery_discharge_balance_victron_bias_auto_apply_suspend_active")
        ),
        "battery_discharge_balance_victron_bias_auto_apply_suspend_reason": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_auto_apply_suspend_reason")
        ),
        "battery_discharge_balance_victron_bias_auto_apply_suspend_until": non_negative_float_or_none(
            raw.get("battery_discharge_balance_victron_bias_auto_apply_suspend_until")
        ),
        "battery_discharge_balance_victron_bias_rollback_enabled": normalize_binary_flag(
            raw.get("battery_discharge_balance_victron_bias_rollback_enabled")
        ),
        "battery_discharge_balance_victron_bias_rollback_active": normalize_binary_flag(
            raw.get("battery_discharge_balance_victron_bias_rollback_active")
        ),
        "battery_discharge_balance_victron_bias_rollback_reason": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_rollback_reason")
        ),
        "battery_discharge_balance_victron_bias_rollback_stable_profile_key": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_rollback_stable_profile_key")
        ),
        "battery_discharge_balance_victron_bias_safe_state_active": normalize_binary_flag(
            raw.get("battery_discharge_balance_victron_bias_safe_state_active")
        ),
        "battery_discharge_balance_victron_bias_safe_state_reason": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_safe_state_reason")
        ),
        "battery_discharge_balance_victron_bias_reason": _normalized_text(
            raw.get("battery_discharge_balance_victron_bias_reason")
        ),
        "combined_battery_average_confidence": non_negative_float_or_none(raw.get("combined_battery_average_confidence")),
        "combined_battery_battery_source_count": non_negative_int(raw.get("combined_battery_battery_source_count")),
        "combined_battery_hybrid_inverter_source_count": non_negative_int(
            raw.get("combined_battery_hybrid_inverter_source_count")
        ),
        "combined_battery_inverter_source_count": non_negative_int(raw.get("combined_battery_inverter_source_count")),
        "combined_battery_learning_profile_count": non_negative_int(raw.get("combined_battery_learning_profile_count")),
        "combined_battery_observed_max_charge_power_w": non_negative_float_or_none(
            raw.get("combined_battery_observed_max_charge_power_w")
        ),
        "combined_battery_observed_max_discharge_power_w": non_negative_float_or_none(
            raw.get("combined_battery_observed_max_discharge_power_w")
        ),
        "combined_battery_observed_max_ac_power_w": non_negative_float_or_none(
            raw.get("combined_battery_observed_max_ac_power_w")
        ),
        "combined_battery_observed_max_pv_input_power_w": non_negative_float_or_none(
            raw.get("combined_battery_observed_max_pv_input_power_w")
        ),
        "combined_battery_observed_max_grid_import_w": non_negative_float_or_none(
            raw.get("combined_battery_observed_max_grid_import_w")
        ),
        "combined_battery_observed_max_grid_export_w": non_negative_float_or_none(
            raw.get("combined_battery_observed_max_grid_export_w")
        ),
        "combined_battery_average_active_charge_power_w": non_negative_float_or_none(
            raw.get("combined_battery_average_active_charge_power_w")
        ),
        "combined_battery_average_active_discharge_power_w": non_negative_float_or_none(
            raw.get("combined_battery_average_active_discharge_power_w")
        ),
        "combined_battery_average_active_power_delta_w": non_negative_float_or_none(
            raw.get("combined_battery_average_active_power_delta_w")
        ),
        "combined_battery_power_smoothing_ratio": non_negative_float_or_none(
            raw.get("combined_battery_power_smoothing_ratio")
        ),
        "combined_battery_typical_response_delay_seconds": non_negative_float_or_none(
            raw.get("combined_battery_typical_response_delay_seconds")
        ),
        "combined_battery_support_bias": _optional_float(raw.get("combined_battery_support_bias")),
        "combined_battery_day_support_bias": _optional_float(raw.get("combined_battery_day_support_bias")),
        "combined_battery_night_support_bias": _optional_float(raw.get("combined_battery_night_support_bias")),
        "combined_battery_import_support_bias": _optional_float(raw.get("combined_battery_import_support_bias")),
        "combined_battery_export_bias": _optional_float(raw.get("combined_battery_export_bias")),
        "combined_battery_battery_first_export_bias": _optional_float(
            raw.get("combined_battery_battery_first_export_bias")
        ),
        "combined_battery_reserve_band_floor_soc": non_negative_float_or_none(
            raw.get("combined_battery_reserve_band_floor_soc")
        ),
        "combined_battery_reserve_band_ceiling_soc": non_negative_float_or_none(
            raw.get("combined_battery_reserve_band_ceiling_soc")
        ),
        "combined_battery_reserve_band_width_soc": non_negative_float_or_none(
            raw.get("combined_battery_reserve_band_width_soc")
        ),
        "combined_battery_direction_change_count": non_negative_int(raw.get("combined_battery_direction_change_count")),
    }


def normalized_state_api_operational_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    state = raw.get("state")
    return {
        "ok": bool(normalize_binary_flag(raw.get("ok", 1))),
        "api_version": normalized_state_api_version(raw.get("api_version")),
        "kind": normalized_state_api_kind(raw.get("kind"), default="operational"),
        "state": normalized_state_api_operational_state_fields(state if isinstance(state, Mapping) else {}),
    }


def normalized_state_api_dbus_diagnostics_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    state = raw.get("state")
    normalized_state = _normalized_generic_mapping(state)
    return {
        "ok": bool(normalize_binary_flag(raw.get("ok", 1))),
        "api_version": normalized_state_api_version(raw.get("api_version")),
        "kind": normalized_state_api_kind(raw.get("kind"), default="dbus-diagnostics"),
        "state": normalized_state,
    }


def normalized_state_api_topology_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return _normalized_state_mapping_fields(payload, kind="topology")


def _normalized_state_update_mapping(value: Any) -> dict[str, Any]:
    state = _normalized_generic_mapping(value)
    for key in (
        "last_check_at",
        "last_run_at",
        "next_check_at",
        "boot_auto_due_at",
        "run_requested_at",
    ):
        if key in state:
            state[key] = non_negative_float_or_none(state.get(key))
    for key in ("available", "no_update_active"):
        if key in state:
            state[key] = bool(normalize_binary_flag(state.get(key)))
    return state


def normalized_state_api_update_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = _normalized_state_mapping_fields(payload, kind="update")
    normalized["state"] = _normalized_state_update_mapping(normalized.get("state"))
    return normalized


def normalized_state_api_config_effective_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return _normalized_state_mapping_fields(payload, kind="config-effective")


def _normalized_state_health_mapping(value: Any) -> dict[str, Any]:
    state = _normalized_generic_mapping(value)
    for key in ("health_code", "listen_port"):
        if key in state:
            state[key] = non_negative_int(state.get(key))
    for key in (
        "fault_active",
        "runtime_overrides_active",
        "control_api_enabled",
        "control_api_running",
        "control_api_localhost_only",
        "update_stale",
    ):
        if key in state:
            state[key] = bool(normalize_binary_flag(state.get(key)))
    return state


def normalized_state_api_health_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = _normalized_state_mapping_fields(payload, kind="health")
    normalized["state"] = _normalized_state_health_mapping(normalized.get("state"))
    return normalized
