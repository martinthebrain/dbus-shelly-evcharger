# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined,no-any-return"
# pyright: reportAttributeAccessIssue=false, reportReturnType=false
"""Victron ESS balance-bias adaptive tuning helpers."""

from __future__ import annotations

from typing import Any


class _UpdateCycleVictronEssBalanceAdaptiveMixin:
    def _maybe_auto_apply_victron_ess_balance_recommendation(self, svc: Any, metrics: dict[str, Any], now: float) -> None:
        metrics["battery_discharge_balance_victron_bias_auto_apply_enabled"] = int(
            bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_enabled", False))
        )
        metrics["battery_discharge_balance_victron_bias_auto_apply_active"] = 0
        metrics["battery_discharge_balance_victron_bias_auto_apply_reason"] = "disabled"
        metrics["battery_discharge_balance_victron_bias_auto_apply_generation"] = int(
            getattr(svc, "_victron_ess_balance_auto_apply_generation", 0) or 0
        )
        metrics["battery_discharge_balance_victron_bias_auto_apply_observation_window_active"] = 0
        metrics["battery_discharge_balance_victron_bias_auto_apply_observation_window_until"] = self._optional_float(
            getattr(svc, "_victron_ess_balance_auto_apply_observe_until", None)
        )
        metrics["battery_discharge_balance_victron_bias_auto_apply_last_param"] = str(
            getattr(svc, "_victron_ess_balance_auto_apply_last_applied_param", "") or ""
        )
        metrics["battery_discharge_balance_victron_bias_auto_apply_suspend_active"] = int(
            self._victron_ess_balance_auto_apply_suspended(svc, now)
        )
        metrics["battery_discharge_balance_victron_bias_auto_apply_suspend_reason"] = str(
            getattr(svc, "_victron_ess_balance_auto_apply_suspend_reason", "") or ""
        )
        metrics["battery_discharge_balance_victron_bias_auto_apply_suspend_until"] = self._optional_float(
            getattr(svc, "_victron_ess_balance_auto_apply_suspend_until", None)
        )
        metrics["battery_discharge_balance_victron_bias_rollback_enabled"] = int(
            bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_rollback_enabled", True))
        )
        metrics["battery_discharge_balance_victron_bias_rollback_active"] = 0
        metrics["battery_discharge_balance_victron_bias_rollback_reason"] = "disabled"
        metrics["battery_discharge_balance_victron_bias_rollback_stable_profile_key"] = str(
            getattr(svc, "_victron_ess_balance_last_stable_profile_key", "") or ""
        )
        metrics["battery_discharge_balance_victron_bias_safe_state_active"] = int(
            bool(getattr(svc, "_victron_ess_balance_safe_state_active", False))
        )
        metrics["battery_discharge_balance_victron_bias_safe_state_reason"] = str(
            getattr(svc, "_victron_ess_balance_safe_state_reason", "") or ""
        )
        if not bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_enabled", False)):
            return
        if self._victron_ess_balance_auto_apply_suspended(svc, now):
            metrics["battery_discharge_balance_victron_bias_auto_apply_reason"] = "auto_apply_suspended"
            return
        if self._victron_ess_balance_should_rollback_stable_tuning(svc, metrics, now):
            if self._maybe_restore_victron_ess_balance_stable_tuning(svc, metrics, "unstable_observation_window"):
                return
        observe_until = self._optional_float(getattr(svc, "_victron_ess_balance_auto_apply_observe_until", None))
        if observe_until is not None and float(now) < float(observe_until):
            metrics["battery_discharge_balance_victron_bias_auto_apply_reason"] = "observation_window_active"
            metrics["battery_discharge_balance_victron_bias_auto_apply_observation_window_active"] = 1
            return
        confidence = self._optional_float(metrics.get("battery_discharge_balance_victron_bias_recommendation_confidence"))
        stability = self._optional_float(metrics.get("battery_discharge_balance_victron_bias_learning_profile_stability_score"))
        sample_count = max(
            0,
            int(metrics.get("battery_discharge_balance_victron_bias_learning_profile_sample_count", 0) or 0),
        )
        min_confidence = max(
            0.0,
            float(
                getattr(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence", 0.85) or 0.85
            ),
        )
        min_stability = max(
            0.0,
            float(
                getattr(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score", 0.75)
                or 0.75
            ),
        )
        min_samples = max(
            1,
            int(getattr(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples", 3) or 3),
        )
        if confidence is None or confidence < min_confidence:
            metrics["battery_discharge_balance_victron_bias_auto_apply_reason"] = "confidence_too_low"
            return
        if stability is None or stability < min_stability:
            metrics["battery_discharge_balance_victron_bias_auto_apply_reason"] = "stability_too_low"
            return
        if sample_count < min_samples:
            metrics["battery_discharge_balance_victron_bias_auto_apply_reason"] = "insufficient_profile_samples"
            return
        recommendation_profile_key = str(
            metrics.get("battery_discharge_balance_victron_bias_recommendation_profile_key", "") or ""
        ).strip()
        active_profile_key = str(
            metrics.get("battery_discharge_balance_victron_bias_learning_profile_key", "") or ""
        ).strip()
        if not recommendation_profile_key or recommendation_profile_key != active_profile_key:
            metrics["battery_discharge_balance_victron_bias_auto_apply_reason"] = "profile_mismatch"
            return
        blend = max(
            0.0,
            min(
                1.0,
                float(getattr(svc, "auto_battery_discharge_balance_victron_bias_auto_apply_blend", 0.25) or 0.25),
            ),
        )
        changed_param = self._apply_victron_ess_balance_recommended_tuning_step(svc, metrics, blend)
        if not changed_param:
            metrics["battery_discharge_balance_victron_bias_auto_apply_reason"] = "already_at_recommendation"
            return
        svc._victron_ess_balance_auto_apply_generation = max(
            0,
            int(getattr(svc, "_victron_ess_balance_auto_apply_generation", 0) or 0),
        ) + 1
        observation_seconds = max(
            0.0,
            float(
                getattr(svc, "auto_battery_discharge_balance_victron_bias_observation_window_seconds", 30.0) or 30.0
            ),
        )
        svc._victron_ess_balance_auto_apply_observe_until = (
            float(now + observation_seconds) if observation_seconds > 0.0 else None
        )
        svc._victron_ess_balance_auto_apply_last_applied_param = str(changed_param)
        svc._victron_ess_balance_auto_apply_last_applied_at = float(now)
        metrics["battery_discharge_balance_victron_bias_auto_apply_active"] = 1
        metrics["battery_discharge_balance_victron_bias_auto_apply_reason"] = "applied_step"
        metrics["battery_discharge_balance_victron_bias_auto_apply_generation"] = int(
            getattr(svc, "_victron_ess_balance_auto_apply_generation", 0) or 0
        )
        metrics["battery_discharge_balance_victron_bias_auto_apply_last_param"] = str(changed_param)
        metrics["battery_discharge_balance_victron_bias_auto_apply_observation_window_until"] = self._optional_float(
            getattr(svc, "_victron_ess_balance_auto_apply_observe_until", None)
        )
        save_runtime_state = getattr(svc, "_save_runtime_state", None)
        if callable(save_runtime_state):
            save_runtime_state()

    def _apply_victron_ess_balance_recommended_tuning_step(
        self,
        svc: Any,
        metrics: dict[str, Any],
        blend: float,
    ) -> str:
        for attr_name, recommendation_key in (
            (
                "auto_battery_discharge_balance_victron_bias_deadband_watts",
                "battery_discharge_balance_victron_bias_recommended_deadband_watts",
            ),
            (
                "auto_battery_discharge_balance_victron_bias_max_abs_watts",
                "battery_discharge_balance_victron_bias_recommended_max_abs_watts",
            ),
            (
                "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second",
                "battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second",
            ),
            ("auto_battery_discharge_balance_victron_bias_kp", "battery_discharge_balance_victron_bias_recommended_kp"),
            ("auto_battery_discharge_balance_victron_bias_ki", "battery_discharge_balance_victron_bias_recommended_ki"),
            ("auto_battery_discharge_balance_victron_bias_kd", "battery_discharge_balance_victron_bias_recommended_kd"),
        ):
            if self._blend_recommended_setting(svc, attr_name, metrics.get(recommendation_key), blend):
                return attr_name
        recommended_activation_mode = str(
            metrics.get("battery_discharge_balance_victron_bias_recommended_activation_mode", "") or ""
        ).strip()
        if recommended_activation_mode and recommended_activation_mode != self._victron_ess_balance_activation_mode(svc):
            svc.auto_battery_discharge_balance_victron_bias_activation_mode = recommended_activation_mode
            return "auto_battery_discharge_balance_victron_bias_activation_mode"
        return ""

    @staticmethod
    def _blend_recommended_setting(svc: Any, attr_name: str, recommendation: Any, blend: float) -> bool:
        if recommendation is None or not hasattr(svc, attr_name):
            return False
        current_value = float(getattr(svc, attr_name, 0.0) or 0.0)
        target_value = float(recommendation)
        if abs(current_value - target_value) < 1e-6:
            return False
        blended_value = ((1.0 - float(blend)) * current_value) + (float(blend) * target_value)
        setattr(svc, attr_name, float(blended_value))
        return True
