# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined,no-any-return"
# pyright: reportAttributeAccessIssue=false, reportReturnType=false
"""Victron ESS balance-bias application helpers."""

from __future__ import annotations

import logging
from typing import Any, cast

import dbus


class _UpdateCycleVictronEssBalanceApplyMixin:
    """Apply one optional Victron-side ESS balance bias through a GX DBus setpoint."""

    @staticmethod
    def _victron_ess_balance_default_metrics(reason: str = "disabled") -> dict[str, Any]:
        return {
            "battery_discharge_balance_victron_bias_enabled": 0,
            "battery_discharge_balance_victron_bias_active": 0,
            "battery_discharge_balance_victron_bias_source_id": "",
            "battery_discharge_balance_victron_bias_topology_key": "",
            "battery_discharge_balance_victron_bias_support_mode": "supported_only",
            "battery_discharge_balance_victron_bias_learning_profile_key": "",
            "battery_discharge_balance_victron_bias_learning_profile_action_direction": "",
            "battery_discharge_balance_victron_bias_learning_profile_site_regime": "",
            "battery_discharge_balance_victron_bias_learning_profile_direction": "",
            "battery_discharge_balance_victron_bias_learning_profile_day_phase": "",
            "battery_discharge_balance_victron_bias_learning_profile_reserve_phase": "",
            "battery_discharge_balance_victron_bias_learning_profile_ev_phase": "",
            "battery_discharge_balance_victron_bias_learning_profile_pv_phase": "",
            "battery_discharge_balance_victron_bias_learning_profile_battery_limit_phase": "",
            "battery_discharge_balance_victron_bias_learning_profile_sample_count": 0,
            "battery_discharge_balance_victron_bias_learning_profile_response_delay_seconds": None,
            "battery_discharge_balance_victron_bias_learning_profile_estimated_gain": None,
            "battery_discharge_balance_victron_bias_learning_profile_overshoot_count": 0,
            "battery_discharge_balance_victron_bias_learning_profile_settled_count": 0,
            "battery_discharge_balance_victron_bias_learning_profile_stability_score": None,
            "battery_discharge_balance_victron_bias_learning_profile_regime_consistency_score": None,
            "battery_discharge_balance_victron_bias_learning_profile_response_variance_score": None,
            "battery_discharge_balance_victron_bias_learning_profile_reproducibility_score": None,
            "battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second": None,
            "battery_discharge_balance_victron_bias_learning_profile_preferred_bias_limit_watts": None,
            "battery_discharge_balance_victron_bias_source_error_w": None,
            "battery_discharge_balance_victron_bias_pid_output_w": 0.0,
            "battery_discharge_balance_victron_bias_setpoint_w": None,
            "battery_discharge_balance_victron_bias_activation_mode": "always",
            "battery_discharge_balance_victron_bias_activation_gate_active": 0,
            "battery_discharge_balance_victron_bias_telemetry_clean": 0,
            "battery_discharge_balance_victron_bias_telemetry_clean_reason": "unknown",
            "battery_discharge_balance_victron_bias_response_delay_seconds": None,
            "battery_discharge_balance_victron_bias_estimated_gain": None,
            "battery_discharge_balance_victron_bias_overshoot_active": 0,
            "battery_discharge_balance_victron_bias_overshoot_count": 0,
            "battery_discharge_balance_victron_bias_overshoot_cooldown_active": 0,
            "battery_discharge_balance_victron_bias_overshoot_cooldown_reason": "",
            "battery_discharge_balance_victron_bias_overshoot_cooldown_until": None,
            "battery_discharge_balance_victron_bias_settling_active": 0,
            "battery_discharge_balance_victron_bias_settled_count": 0,
            "battery_discharge_balance_victron_bias_stability_score": None,
            "battery_discharge_balance_victron_bias_oscillation_lockout_enabled": 0,
            "battery_discharge_balance_victron_bias_oscillation_lockout_active": 0,
            "battery_discharge_balance_victron_bias_oscillation_lockout_reason": "",
            "battery_discharge_balance_victron_bias_oscillation_lockout_until": None,
            "battery_discharge_balance_victron_bias_oscillation_direction_change_count": 0,
            "battery_discharge_balance_victron_bias_recommended_kp": None,
            "battery_discharge_balance_victron_bias_recommended_ki": None,
            "battery_discharge_balance_victron_bias_recommended_kd": None,
            "battery_discharge_balance_victron_bias_recommended_deadband_watts": None,
            "battery_discharge_balance_victron_bias_recommended_max_abs_watts": None,
            "battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second": None,
            "battery_discharge_balance_victron_bias_recommended_activation_mode": "",
            "battery_discharge_balance_victron_bias_recommendation_confidence": None,
            "battery_discharge_balance_victron_bias_recommendation_regime_consistency_score": None,
            "battery_discharge_balance_victron_bias_recommendation_response_variance_score": None,
            "battery_discharge_balance_victron_bias_recommendation_reproducibility_score": None,
            "battery_discharge_balance_victron_bias_recommendation_reason": "disabled",
            "battery_discharge_balance_victron_bias_recommendation_profile_key": "",
            "battery_discharge_balance_victron_bias_recommendation_ini_snippet": "",
            "battery_discharge_balance_victron_bias_recommendation_hint": "",
            "battery_discharge_balance_victron_bias_auto_apply_enabled": 0,
            "battery_discharge_balance_victron_bias_auto_apply_active": 0,
            "battery_discharge_balance_victron_bias_auto_apply_reason": "disabled",
            "battery_discharge_balance_victron_bias_auto_apply_generation": 0,
            "battery_discharge_balance_victron_bias_auto_apply_observation_window_active": 0,
            "battery_discharge_balance_victron_bias_auto_apply_observation_window_until": None,
            "battery_discharge_balance_victron_bias_auto_apply_last_param": "",
            "battery_discharge_balance_victron_bias_auto_apply_suspend_active": 0,
            "battery_discharge_balance_victron_bias_auto_apply_suspend_reason": "",
            "battery_discharge_balance_victron_bias_auto_apply_suspend_until": None,
            "battery_discharge_balance_victron_bias_rollback_enabled": 0,
            "battery_discharge_balance_victron_bias_rollback_active": 0,
            "battery_discharge_balance_victron_bias_rollback_reason": "disabled",
            "battery_discharge_balance_victron_bias_rollback_stable_profile_key": "",
            "battery_discharge_balance_victron_bias_safe_state_active": 0,
            "battery_discharge_balance_victron_bias_safe_state_reason": "",
            "battery_discharge_balance_victron_bias_reason": reason,
        }

    def apply_victron_ess_balance_bias(self, svc: Any, now: float, auto_mode_active: bool) -> None:
        metrics = self._victron_ess_balance_default_metrics("disabled")
        self._initialize_victron_ess_balance_apply_metrics(svc, metrics)
        if not self._victron_ess_balance_enabled(svc):
            self._disable_victron_ess_balance(svc, metrics)
            return
        cluster, source_error_w, profile_key, prepare_reason = self._prepare_victron_ess_balance_tracking_state(
            svc,
            now,
            auto_mode_active,
            metrics,
        )
        if prepare_reason:
            self._restore_victron_ess_balance_base_setpoint(svc, now, metrics, prepare_reason)
            return
        assert source_error_w is not None
        assert profile_key is not None
        self._apply_victron_ess_balance_tracking(
            svc,
            now,
            cluster,
            source_error_w,
            profile_key,
            metrics,
        )

    def _initialize_victron_ess_balance_apply_metrics(self, svc: Any, metrics: dict[str, Any]) -> None:
        metrics["battery_discharge_balance_victron_bias_enabled"] = int(self._victron_ess_balance_enabled(svc))
        metrics["battery_discharge_balance_victron_bias_support_mode"] = self._victron_ess_balance_support_mode(svc)
        metrics["battery_discharge_balance_victron_bias_activation_mode"] = self._victron_ess_balance_activation_mode(svc)
        metrics["battery_discharge_balance_victron_bias_auto_apply_enabled"] = int(
            bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_enabled", False))
        )

    @staticmethod
    def _victron_ess_balance_enabled(svc: Any) -> bool:
        return bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_enabled", False))

    @staticmethod
    def _reset_victron_ess_balance_pid(svc: Any) -> None:
        svc._victron_ess_balance_pid_last_error_w = 0.0
        svc._victron_ess_balance_pid_last_at = None
        svc._victron_ess_balance_pid_integral_output_w = 0.0
        svc._victron_ess_balance_pid_last_output_w = 0.0

    @staticmethod
    def _reset_victron_ess_balance_pid_integral(svc: Any, aggressive: bool = False) -> None:
        svc._victron_ess_balance_pid_integral_output_w = 0.0
        if aggressive:
            svc._victron_ess_balance_pid_last_error_w = 0.0
            svc._victron_ess_balance_pid_last_output_w = 0.0

    def _record_victron_ess_balance_command(
        self,
        svc: Any,
        now: float,
        setpoint_w: float,
        source_error_w: float,
        profile_key: str,
    ) -> None:
        svc._victron_ess_balance_telemetry_last_command_at = float(now)
        svc._victron_ess_balance_telemetry_last_command_setpoint_w = float(setpoint_w)
        svc._victron_ess_balance_telemetry_last_command_error_w = float(source_error_w)
        svc._victron_ess_balance_telemetry_last_command_profile_key = str(profile_key or "").strip()
        svc._victron_ess_balance_telemetry_command_response_recorded = False
        svc._victron_ess_balance_telemetry_command_overshoot_recorded = False
        svc._victron_ess_balance_telemetry_command_settled_recorded = False
        svc._victron_ess_balance_telemetry_overshoot_active = False
        svc._victron_ess_balance_telemetry_settling_active = True

    @staticmethod
    def _clear_victron_ess_balance_tracking_episode(svc: Any) -> None:
        svc._victron_ess_balance_telemetry_last_command_at = None
        svc._victron_ess_balance_telemetry_last_command_setpoint_w = None
        svc._victron_ess_balance_telemetry_last_command_error_w = None
        svc._victron_ess_balance_telemetry_last_command_profile_key = ""
        svc._victron_ess_balance_telemetry_command_response_recorded = False
        svc._victron_ess_balance_telemetry_command_overshoot_recorded = False
        svc._victron_ess_balance_telemetry_command_settled_recorded = False
        svc._victron_ess_balance_telemetry_overshoot_active = False
        svc._victron_ess_balance_telemetry_settling_active = False

    def _disable_victron_ess_balance(self, svc: Any, metrics: dict[str, Any]) -> None:
        self._merge_victron_ess_balance_metrics(svc, metrics)
        self._reset_victron_ess_balance_pid(svc)

    def _victron_ess_balance_cluster_state(
        self,
        svc: Any,
        auto_mode_active: bool,
    ) -> tuple[dict[str, Any], str]:
        if not auto_mode_active:
            return {}, "auto-mode-inactive"
        cluster = self._normalized_mapping(getattr(svc, "_last_energy_cluster", {}))
        eligible_source_count = int(cluster.get("battery_discharge_balance_eligible_source_count", 0) or 0)
        if eligible_source_count < 2:
            return cluster, "insufficient-eligible-sources"
        return cluster, ""

    def _victron_ess_balance_source_state(
        self,
        cluster: dict[str, Any],
        svc: Any,
        metrics: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, float | None, str]:
        source, resolve_reason = self._victron_ess_balance_source(cluster, svc)
        if source is None:
            return None, None, resolve_reason
        source_id = str(source.get("source_id", "")).strip()
        metrics["battery_discharge_balance_victron_bias_source_id"] = source_id
        metrics["battery_discharge_balance_victron_bias_topology_key"] = self._victron_ess_balance_current_topology_key(
            svc,
            source_id,
        )
        if not bool(source.get("online", True)):
            return None, None, "victron-source-offline"
        source_error_w = self._optional_float(source.get("discharge_balance_error_w"))
        metrics["battery_discharge_balance_victron_bias_source_error_w"] = source_error_w
        if source_error_w is None:
            return None, None, "victron-source-error-missing"
        if not self._victron_ess_balance_source_support_allowed(source, svc):
            return None, None, "victron-source-support-blocked"
        return source, source_error_w, ""

    def _prepare_victron_ess_balance_learning_state(
        self,
        svc: Any,
        now: float,
        cluster: dict[str, Any],
        source: dict[str, Any],
        source_error_w: float,
        metrics: dict[str, Any],
    ) -> dict[str, str]:
        learning_profile = self._victron_ess_balance_learning_profile(svc, cluster, source, source_error_w)
        self._set_victron_ess_balance_active_profile(svc, learning_profile)
        self._merge_victron_ess_balance_learning_profile_metrics(svc, metrics, learning_profile["key"])
        self._victron_ess_balance_refresh_stable_tuning(svc, metrics, now)
        direction_change_count = self._victron_ess_balance_note_action_direction(
            svc,
            str(learning_profile.get("action_direction", "") or ""),
            now,
        )
        metrics["battery_discharge_balance_victron_bias_oscillation_direction_change_count"] = int(direction_change_count)
        self._populate_victron_ess_balance_runtime_safety_metrics(svc, now, metrics)
        return learning_profile

    def _prepare_victron_ess_balance_tracking_source(
        self,
        svc: Any,
        auto_mode_active: bool,
        metrics: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None, float | None, str]:
        cluster, block_reason = self._victron_ess_balance_cluster_state(svc, auto_mode_active)
        if block_reason:
            return cluster, None, None, block_reason
        source, source_error_w, source_reason = self._victron_ess_balance_source_state(cluster, svc, metrics)
        if source_reason:
            return cluster, None, None, source_reason
        return cluster, source, source_error_w, ""

    def _prepare_victron_ess_balance_tracking_profile(
        self,
        svc: Any,
        now: float,
        cluster: dict[str, Any],
        source: dict[str, Any],
        source_error_w: float,
        metrics: dict[str, Any],
    ) -> tuple[str | None, str]:
        learning_profile = self._prepare_victron_ess_balance_learning_state(
            svc,
            now,
            cluster,
            source,
            source_error_w,
            metrics,
        )
        safety_reason = self._victron_ess_balance_safety_block_reason(svc, now, metrics)
        if safety_reason:
            return None, safety_reason
        if not self._victron_ess_balance_activation_allowed(learning_profile, svc):
            return None, "activation-mode-blocked"
        return str(learning_profile["key"]), ""

    def _prepare_victron_ess_balance_tracking_state(
        self,
        svc: Any,
        now: float,
        auto_mode_active: bool,
        metrics: dict[str, Any],
    ) -> tuple[dict[str, Any], float | None, str | None, str]:
        cluster, source, source_error_w, source_reason = self._prepare_victron_ess_balance_tracking_source(
            svc,
            auto_mode_active,
            metrics,
        )
        if source_reason:
            return cluster, None, None, source_reason
        assert source is not None
        assert source_error_w is not None
        profile_key, profile_reason = self._prepare_victron_ess_balance_tracking_profile(
            svc,
            now,
            cluster,
            source,
            source_error_w,
            metrics,
        )
        if profile_reason:
            return cluster, None, None, profile_reason
        return cluster, source_error_w, profile_key, ""

    def _victron_ess_balance_safety_block_reason(self, svc: Any, now: float, metrics: dict[str, Any]) -> str:
        if self._victron_ess_balance_overshoot_cooldown_active(svc, now):
            self._maybe_restore_victron_ess_balance_stable_tuning(svc, metrics, "overshoot_cooldown")
            return "overshoot-cooldown-active"
        if self._victron_ess_balance_oscillation_lockout_active(svc, now):
            self._maybe_restore_victron_ess_balance_stable_tuning(svc, metrics, "oscillation_lockout")
            return "oscillation-lockout-active"
        svc._victron_ess_balance_safe_state_active = False
        svc._victron_ess_balance_safe_state_reason = ""
        return ""

    @staticmethod
    def _victron_ess_balance_base_setpoint_w(svc: Any) -> float:
        return float(getattr(svc, "auto_battery_discharge_balance_victron_bias_base_setpoint_watts", 50.0) or 0.0)

    def _victron_ess_balance_tracking_setpoint(
        self,
        svc: Any,
        now: float,
        source_error_w: float,
        metrics: dict[str, Any],
    ) -> float:
        metrics["battery_discharge_balance_victron_bias_activation_gate_active"] = 1
        output_w = self._victron_ess_balance_pid_output(svc, source_error_w, now)
        setpoint_w = float(self._victron_ess_balance_base_setpoint_w(svc) + output_w)
        metrics["battery_discharge_balance_victron_bias_pid_output_w"] = float(output_w)
        metrics["battery_discharge_balance_victron_bias_setpoint_w"] = float(setpoint_w)
        metrics["battery_discharge_balance_victron_bias_reason"] = "tracking"
        svc._victron_ess_balance_pid_last_output_w = float(output_w)
        return setpoint_w

    def _victron_ess_balance_update_tracking_telemetry(
        self,
        svc: Any,
        now: float,
        cluster: dict[str, Any],
        source_error_w: float,
        profile_key: str,
        metrics: dict[str, Any],
    ) -> None:
        self._update_victron_ess_balance_telemetry(
            svc,
            now,
            cluster,
            source_error_w,
            metrics,
            profile_key,
        )

    def _victron_ess_balance_apply_write_outcome(
        self,
        svc: Any,
        now: float,
        setpoint_w: float,
        source_error_w: float,
        profile_key: str,
        metrics: dict[str, Any],
    ) -> None:
        if self._victron_ess_balance_write_setpoint(
            svc,
            getattr(svc, "auto_battery_discharge_balance_victron_bias_service", ""),
            getattr(svc, "auto_battery_discharge_balance_victron_bias_path", ""),
            setpoint_w,
        ):
            svc._victron_ess_balance_last_write_at = float(now)
            svc._victron_ess_balance_last_setpoint_w = float(setpoint_w)
            self._record_victron_ess_balance_command(svc, now, setpoint_w, source_error_w, profile_key)
            metrics["battery_discharge_balance_victron_bias_active"] = 1
            metrics["battery_discharge_balance_victron_bias_reason"] = "applied"
            return
        metrics["battery_discharge_balance_victron_bias_reason"] = "write-failed"

    def _victron_ess_balance_tracking_write_state(
        self,
        svc: Any,
        now: float,
        setpoint_w: float,
        source_error_w: float,
        profile_key: str,
        metrics: dict[str, Any],
    ) -> None:
        if self._victron_ess_balance_should_write(svc, now, setpoint_w):
            self._victron_ess_balance_apply_write_outcome(
                svc,
                now,
                setpoint_w,
                source_error_w,
                profile_key,
                metrics,
            )
            return
        if self._victron_ess_balance_last_setpoint(svc) is not None:
            metrics["battery_discharge_balance_victron_bias_active"] = 1
            metrics["battery_discharge_balance_victron_bias_reason"] = "holding"

    def _finalize_victron_ess_balance_metrics(self, svc: Any, now: float, metrics: dict[str, Any]) -> None:
        self._maybe_auto_apply_victron_ess_balance_recommendation(svc, metrics, now)
        self._merge_victron_ess_balance_metrics(svc, metrics)

    def _apply_victron_ess_balance_tracking(
        self,
        svc: Any,
        now: float,
        cluster: dict[str, Any],
        source_error_w: float,
        profile_key: str,
        metrics: dict[str, Any],
    ) -> None:
        setpoint_w = self._victron_ess_balance_tracking_setpoint(svc, now, source_error_w, metrics)
        self._victron_ess_balance_update_tracking_telemetry(
            svc,
            now,
            cluster,
            source_error_w,
            profile_key,
            metrics,
        )
        self._victron_ess_balance_tracking_write_state(
            svc,
            now,
            setpoint_w,
            source_error_w,
            profile_key,
            metrics,
        )
        self._finalize_victron_ess_balance_metrics(svc, now, metrics)

    def _restore_victron_ess_balance_base_setpoint(
        self,
        svc: Any,
        now: float,
        metrics: dict[str, Any],
        reason: str,
    ) -> None:
        base_setpoint_w = self._victron_ess_balance_base_setpoint_w(svc)
        self._reset_victron_ess_balance_pid(svc)
        self._clear_victron_ess_balance_tracking_episode(svc)
        self._clear_victron_ess_balance_active_profile(svc)
        metrics["battery_discharge_balance_victron_bias_setpoint_w"] = float(base_setpoint_w)
        metrics["battery_discharge_balance_victron_bias_reason"] = reason
        metrics["battery_discharge_balance_victron_bias_activation_gate_active"] = 0
        if self._victron_ess_balance_last_setpoint(svc) is None:
            self._populate_victron_ess_balance_telemetry_metrics(svc, metrics)
            self._maybe_auto_apply_victron_ess_balance_recommendation(svc, metrics, now)
            self._merge_victron_ess_balance_metrics(svc, metrics)
            return
        if not self._victron_ess_balance_should_write(svc, now, base_setpoint_w):
            metrics["battery_discharge_balance_victron_bias_active"] = 1
            metrics["battery_discharge_balance_victron_bias_reason"] = f"{reason}-holding"
            self._populate_victron_ess_balance_telemetry_metrics(svc, metrics)
            self._maybe_auto_apply_victron_ess_balance_recommendation(svc, metrics, now)
            self._merge_victron_ess_balance_metrics(svc, metrics)
            return
        if self._victron_ess_balance_write_setpoint(
            svc,
            getattr(svc, "auto_battery_discharge_balance_victron_bias_service", ""),
            getattr(svc, "auto_battery_discharge_balance_victron_bias_path", ""),
            base_setpoint_w,
        ):
            svc._victron_ess_balance_last_write_at = float(now)
            svc._victron_ess_balance_last_setpoint_w = None
            metrics["battery_discharge_balance_victron_bias_reason"] = f"{reason}-restored"
        else:
            metrics["battery_discharge_balance_victron_bias_active"] = 1
            metrics["battery_discharge_balance_victron_bias_reason"] = f"{reason}-restore-failed"
        self._populate_victron_ess_balance_telemetry_metrics(svc, metrics)
        self._maybe_auto_apply_victron_ess_balance_recommendation(svc, metrics, now)
        self._merge_victron_ess_balance_metrics(svc, metrics)

    @staticmethod
    def _merge_victron_ess_balance_metrics(svc: Any, metrics: dict[str, Any]) -> None:
        last_metrics = getattr(svc, "_last_auto_metrics", None)
        if not isinstance(last_metrics, dict):
            svc._last_auto_metrics = dict(metrics)
            return
        last_metrics.update(metrics)

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if not isinstance(value, (int, float)):
            return None
        return float(value)

    @staticmethod
    def _normalized_mapping(raw_value: object) -> dict[str, Any]:
        return raw_value if isinstance(raw_value, dict) else {}

    @staticmethod
    def _victron_ess_balance_cluster_sources(cluster: dict[str, Any]) -> list[dict[str, Any]]:
        raw_sources = cluster.get("battery_sources", [])
        return [value for value in raw_sources if isinstance(value, dict)]

    @staticmethod
    def _victron_ess_balance_configured_source_id(svc: Any) -> str:
        return str(getattr(svc, "auto_battery_discharge_balance_victron_bias_source_id", "") or "").strip()

    @staticmethod
    def _victron_ess_balance_matching_source(
        sources: list[dict[str, Any]],
        configured_source_id: str,
    ) -> dict[str, Any] | None:
        for source in sources:
            if str(source.get("source_id", "")).strip() == configured_source_id:
                return source
        return None

    @staticmethod
    def _victron_ess_balance_dbus_source_candidates(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            source
            for source in sources
            if str(source.get("discharge_balance_control_connector_type", "")).strip().lower() == "dbus"
        ]

    def _victron_ess_balance_source(self, cluster: dict[str, Any], svc: Any) -> tuple[dict[str, Any] | None, str]:
        sources = self._victron_ess_balance_cluster_sources(cluster)
        configured_source_id = self._victron_ess_balance_configured_source_id(svc)
        if configured_source_id:
            source = self._victron_ess_balance_matching_source(sources, configured_source_id)
            if source is not None:
                return source, "configured-source"
            return None, "victron-source-not-found"
        candidates = self._victron_ess_balance_dbus_source_candidates(sources)
        if len(candidates) == 1:
            return candidates[0], "auto-detected-dbus-source"
        if not candidates:
            return None, "victron-source-not-detected"
        return None, "victron-source-ambiguous"

    def _victron_ess_balance_support_mode(self, svc: Any) -> str:
        raw_mode = str(
            getattr(svc, "auto_battery_discharge_balance_victron_bias_support_mode", "allow_experimental") or ""
        ).strip().lower()
        if raw_mode in {"supported_only", "allow_experimental"}:
            return raw_mode
        return "allow_experimental"

    def _victron_ess_balance_source_support_allowed(self, source: dict[str, Any], svc: Any) -> bool:
        support_mode = self._victron_ess_balance_support_mode(svc)
        support = str(source.get("discharge_balance_control_support", "")).strip().lower()
        if support_mode == "supported_only":
            return support in {"supported", ""}
        return support in {"supported", "experimental", ""}

    def _victron_ess_balance_activation_mode(self, svc: Any) -> str:
        raw_mode = str(
            getattr(svc, "auto_battery_discharge_balance_victron_bias_activation_mode", "always") or ""
        ).strip().lower()
        if raw_mode in {"always", "export_only", "above_reserve_band", "export_and_above_reserve_band"}:
            return raw_mode
        return "always"

    @staticmethod
    def _victron_ess_balance_activation_site_regime_matches(mode: str, site_regime: str) -> bool:
        if mode in {"export_only", "export_and_above_reserve_band"}:
            return site_regime == "export"
        return True

    @staticmethod
    def _victron_ess_balance_activation_reserve_phase_matches(mode: str, reserve_phase: str) -> bool:
        if mode in {"above_reserve_band", "export_and_above_reserve_band"}:
            return reserve_phase == "above_reserve_band"
        return True

    def _victron_ess_balance_activation_allowed(self, learning_profile: dict[str, str], svc: Any) -> bool:
        mode = self._victron_ess_balance_activation_mode(svc)
        if mode == "always":
            return True
        site_regime = str(learning_profile.get("site_regime", "") or "")
        reserve_phase = str(learning_profile.get("reserve_phase", "") or "")
        return self._victron_ess_balance_activation_site_regime_matches(
            mode,
            site_regime,
        ) and self._victron_ess_balance_activation_reserve_phase_matches(
            mode,
            reserve_phase,
        )

    @staticmethod
    def _victron_ess_balance_pid_gain_config(svc: Any) -> dict[str, float]:
        return {
            "kp": float(getattr(svc, "auto_battery_discharge_balance_victron_bias_kp", 0.0) or 0.0),
            "ki": float(getattr(svc, "auto_battery_discharge_balance_victron_bias_ki", 0.0) or 0.0),
            "kd": float(getattr(svc, "auto_battery_discharge_balance_victron_bias_kd", 0.0) or 0.0),
        }

    @staticmethod
    def _victron_ess_balance_pid_limit_config(svc: Any) -> dict[str, float]:
        return {
            "deadband_w": max(
                0.0,
                float(getattr(svc, "auto_battery_discharge_balance_victron_bias_deadband_watts", 0.0) or 0.0),
            ),
            "integral_limit_w": max(
                0.0,
                float(getattr(svc, "auto_battery_discharge_balance_victron_bias_integral_limit_watts", 0.0) or 0.0),
            ),
            "max_abs_w": max(
                0.0,
                float(getattr(svc, "auto_battery_discharge_balance_victron_bias_max_abs_watts", 0.0) or 0.0),
            ),
            "ramp_rate_w_per_second": max(
                0.0,
                float(
                    getattr(svc, "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second", 0.0) or 0.0
                ),
            ),
        }

    @staticmethod
    def _victron_ess_balance_pid_config(svc: Any) -> dict[str, float]:
        return {
            **_UpdateCycleVictronEssBalanceApplyMixin._victron_ess_balance_pid_gain_config(svc),
            **_UpdateCycleVictronEssBalanceApplyMixin._victron_ess_balance_pid_limit_config(svc),
        }

    @staticmethod
    def _victron_ess_balance_effective_error(raw_error_w: float, deadband_w: float) -> float:
        return 0.0 if abs(raw_error_w) < deadband_w else raw_error_w

    def _victron_ess_balance_pid_timing(self, svc: Any, now: float) -> tuple[float, float]:
        last_at = self._optional_float(getattr(svc, "_victron_ess_balance_pid_last_at", None))
        dt = 0.0 if last_at is None else max(0.0, float(now) - float(last_at))
        last_error_w = float(getattr(svc, "_victron_ess_balance_pid_last_error_w", 0.0) or 0.0)
        return dt, last_error_w

    @staticmethod
    def _victron_ess_balance_pid_integral_output(
        current_integral_output_w: float,
        effective_error_w: float,
        dt: float,
        ki: float,
        integral_limit_w: float,
    ) -> float:
        integral_output_w = float(current_integral_output_w)
        if dt > 0.0 and ki > 0.0:
            integral_output_w += ki * effective_error_w * dt
        if integral_limit_w > 0.0:
            integral_output_w = max(-integral_limit_w, min(integral_output_w, integral_limit_w))
        return integral_output_w

    @staticmethod
    def _victron_ess_balance_pid_derivative_w(effective_error_w: float, last_error_w: float, dt: float) -> float:
        return 0.0 if dt <= 0.0 else (effective_error_w - last_error_w) / dt

    @staticmethod
    def _victron_ess_balance_pid_target_output_w(
        effective_error_w: float,
        kp: float,
        integral_output_w: float,
        kd: float,
        derivative_w: float,
    ) -> float:
        return (kp * effective_error_w) + integral_output_w + (kd * derivative_w)

    @staticmethod
    def _victron_ess_balance_pid_clamped_output_w(target_output_w: float, max_abs_w: float) -> float:
        if max_abs_w <= 0.0:
            return float(target_output_w)
        return max(-max_abs_w, min(target_output_w, max_abs_w))

    @staticmethod
    def _victron_ess_balance_pid_ramped_output_w(
        last_output_w: float,
        target_output_w: float,
        dt: float,
        ramp_rate_w_per_second: float,
    ) -> float:
        if ramp_rate_w_per_second <= 0.0 or dt <= 0.0:
            return float(target_output_w)
        max_step_w = ramp_rate_w_per_second * dt
        delta_w = max(-max_step_w, min(target_output_w - last_output_w, max_step_w))
        return float(last_output_w + delta_w)

    @staticmethod
    def _victron_ess_balance_pid_store_state(
        svc: Any,
        effective_error_w: float,
        now: float,
        integral_output_w: float,
    ) -> None:
        svc._victron_ess_balance_pid_last_error_w = float(effective_error_w)
        svc._victron_ess_balance_pid_last_at = float(now)
        svc._victron_ess_balance_pid_integral_output_w = float(integral_output_w)

    def _victron_ess_balance_pid_output(self, svc: Any, error_w: float, now: float) -> float:
        config = self._victron_ess_balance_pid_config(svc)
        raw_error_w = float(error_w)
        effective_error_w = self._victron_ess_balance_effective_error(raw_error_w, config["deadband_w"])
        dt, last_error_w = self._victron_ess_balance_pid_timing(svc, now)
        integral_output_w = float(getattr(svc, "_victron_ess_balance_pid_integral_output_w", 0.0) or 0.0)
        if effective_error_w == 0.0:
            integral_output_w = 0.0
            target_output_w = 0.0
        else:
            integral_output_w = self._victron_ess_balance_pid_integral_output(
                integral_output_w,
                effective_error_w,
                dt,
                config["ki"],
                config["integral_limit_w"],
            )
            derivative_w = self._victron_ess_balance_pid_derivative_w(effective_error_w, last_error_w, dt)
            target_output_w = self._victron_ess_balance_pid_target_output_w(
                effective_error_w,
                config["kp"],
                integral_output_w,
                config["kd"],
                derivative_w,
            )
        target_output_w = self._victron_ess_balance_pid_clamped_output_w(target_output_w, config["max_abs_w"])
        last_output_w = float(getattr(svc, "_victron_ess_balance_pid_last_output_w", 0.0) or 0.0)
        output_w = self._victron_ess_balance_pid_ramped_output_w(
            last_output_w,
            target_output_w,
            dt,
            config["ramp_rate_w_per_second"],
        )
        self._victron_ess_balance_pid_store_state(svc, effective_error_w, now, integral_output_w)
        return float(output_w)

    def _victron_ess_balance_should_write(self, svc: Any, now: float, setpoint_w: float) -> bool:
        min_update_seconds = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_min_update_seconds", 0.0) or 0.0),
        )
        last_write_at = self._optional_float(getattr(svc, "_victron_ess_balance_last_write_at", None))
        if last_write_at is not None and (float(now) - float(last_write_at)) < min_update_seconds:
            return False
        last_setpoint_w = self._victron_ess_balance_last_setpoint(svc)
        if last_setpoint_w is None:
            return True
        return abs(float(setpoint_w) - float(last_setpoint_w)) >= 1.0

    @staticmethod
    def _victron_ess_balance_last_setpoint(svc: Any) -> float | None:
        return _UpdateCycleVictronEssBalanceApplyMixin._optional_float(
            getattr(svc, "_victron_ess_balance_last_setpoint_w", None)
        )

    @staticmethod
    def _victron_ess_balance_write_target(service_name: object, path: object) -> tuple[str, str]:
        return str(service_name or "").strip(), str(path or "").strip()

    @staticmethod
    def _victron_ess_balance_write_payload(dbus_module: Any, value: float) -> Any:
        return dbus_module.Double(float(value)) if hasattr(dbus_module, "Double") else float(value)

    def _victron_ess_balance_try_write_setpoint(
        self,
        svc: Any,
        normalized_service: str,
        normalized_path: str,
        value: float,
    ) -> None:
        bus = svc._get_system_bus()
        obj = bus.get_object(normalized_service, normalized_path)
        dbus_module = cast(Any, dbus)
        interface = dbus_module.Interface(obj, "com.victronenergy.BusItem")
        interface.SetValue(
            self._victron_ess_balance_write_payload(dbus_module, value),
            timeout=svc.dbus_method_timeout_seconds,
        )

    @staticmethod
    def _victron_ess_balance_log_write_retry(
        normalized_service: str,
        normalized_path: str,
        error: Exception,
    ) -> None:
        logging.debug(
            "Victron ESS balance-bias write retry for %s %s after error: %s",
            normalized_service,
            normalized_path,
            error,
        )

    def _victron_ess_balance_write_setpoint(
        self,
        svc: Any,
        service_name: object,
        path: object,
        value: float,
    ) -> bool:
        normalized_service, normalized_path = self._victron_ess_balance_write_target(service_name, path)
        if not normalized_service or not normalized_path:
            return False
        last_error = self._victron_ess_balance_write_error(
            svc,
            normalized_service,
            normalized_path,
            value,
        )
        if last_error is None:
            return True
        svc._warning_throttled(
            "victron-ess-balance-write-failed",
            self._victron_ess_balance_write_warning_interval_seconds(svc),
            "Victron ESS balance-bias write to %s %s failed: %s",
            normalized_service,
            normalized_path,
            last_error,
        )
        return False

    def _victron_ess_balance_write_error(
        self,
        svc: Any,
        normalized_service: str,
        normalized_path: str,
        value: float,
    ) -> Exception | None:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                self._victron_ess_balance_try_write_setpoint(
                    svc,
                    normalized_service,
                    normalized_path,
                    value,
                )
                return None
            except Exception as error:  # pylint: disable=broad-except
                last_error = error
                svc._reset_system_bus()
                if attempt == 0:
                    self._victron_ess_balance_log_write_retry(normalized_service, normalized_path, error)
        return last_error

    @staticmethod
    def _victron_ess_balance_write_warning_interval_seconds(svc: Any) -> float:
        return max(
            5.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_min_update_seconds", 2.0) or 2.0),
        )
