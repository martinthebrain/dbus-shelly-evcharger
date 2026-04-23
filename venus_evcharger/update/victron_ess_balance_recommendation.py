# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined,no-any-return"
# pyright: reportAttributeAccessIssue=false, reportReturnType=false
"""Victron ESS balance-bias recommendation helpers."""

from __future__ import annotations

from typing import Any


class _UpdateCycleVictronEssBalanceRecommendationMixin:
    def _populate_victron_ess_balance_telemetry_metrics(self, svc: Any, metrics: dict[str, Any]) -> None:
        metrics["battery_discharge_balance_victron_bias_response_delay_seconds"] = self._optional_float(
            getattr(svc, "_victron_ess_balance_telemetry_response_delay_seconds", None)
        )
        metrics["battery_discharge_balance_victron_bias_estimated_gain"] = self._optional_float(
            getattr(svc, "_victron_ess_balance_telemetry_estimated_gain", None)
        )
        metrics["battery_discharge_balance_victron_bias_overshoot_active"] = int(
            bool(getattr(svc, "_victron_ess_balance_telemetry_overshoot_active", False))
        )
        metrics["battery_discharge_balance_victron_bias_overshoot_count"] = int(
            getattr(svc, "_victron_ess_balance_telemetry_overshoot_count", 0) or 0
        )
        metrics["battery_discharge_balance_victron_bias_overshoot_cooldown_active"] = int(
            self._victron_ess_balance_overshoot_cooldown_active(
                svc,
                self._optional_float(getattr(svc, "_victron_ess_balance_telemetry_last_observed_at", None)) or 0.0,
            )
        )
        metrics["battery_discharge_balance_victron_bias_overshoot_cooldown_reason"] = str(
            getattr(svc, "_victron_ess_balance_overshoot_cooldown_reason", "") or ""
        )
        metrics["battery_discharge_balance_victron_bias_overshoot_cooldown_until"] = self._optional_float(
            getattr(svc, "_victron_ess_balance_overshoot_cooldown_until", None)
        )
        metrics["battery_discharge_balance_victron_bias_settling_active"] = int(
            bool(getattr(svc, "_victron_ess_balance_telemetry_settling_active", False))
        )
        metrics["battery_discharge_balance_victron_bias_settled_count"] = int(
            getattr(svc, "_victron_ess_balance_telemetry_settled_count", 0) or 0
        )
        metrics["battery_discharge_balance_victron_bias_stability_score"] = self._optional_float(
            getattr(svc, "_victron_ess_balance_telemetry_stability_score", None)
        )
        self._populate_victron_ess_balance_runtime_safety_metrics(
            svc,
            self._optional_float(getattr(svc, "_victron_ess_balance_telemetry_last_observed_at", None)) or 0.0,
            metrics,
        )
        active_profile_key = str(getattr(svc, "_victron_ess_balance_active_learning_profile_key", "") or "").strip()
        self._merge_victron_ess_balance_learning_profile_metrics(svc, metrics, active_profile_key)
        metrics.update(self._victron_ess_balance_recommendation_metrics(svc))

    def _victron_ess_balance_recommendation_metrics(self, svc: Any) -> dict[str, Any]:
        enabled = bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_enabled", False))
        if not enabled:
            return {
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
            }
        current_kp = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_kp", 0.0) or 0.0),
        )
        current_ki = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_ki", 0.0) or 0.0),
        )
        current_kd = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_kd", 0.0) or 0.0),
        )
        current_deadband = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_deadband_watts", 0.0) or 0.0),
        )
        current_max_abs = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_max_abs_watts", 0.0) or 0.0),
        )
        current_ramp = max(
            0.0,
            float(
                getattr(svc, "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second", 0.0) or 0.0
            ),
        )
        active_profile_key = str(getattr(svc, "_victron_ess_balance_active_learning_profile_key", "") or "").strip()
        active_profile = self._victron_ess_balance_learning_profile_state(svc, active_profile_key)
        use_profile = self._victron_ess_balance_profile_sample_count(active_profile) > 0
        response_delay_seconds = self._optional_float(
            active_profile.get("response_delay_seconds")
            if use_profile
            else getattr(svc, "_victron_ess_balance_telemetry_response_delay_seconds", None)
        )
        estimated_gain = self._optional_float(
            active_profile.get("estimated_gain")
            if use_profile
            else getattr(svc, "_victron_ess_balance_telemetry_estimated_gain", None)
        )
        stability_score = self._optional_float(
            active_profile.get("stability_score")
            if use_profile
            else getattr(svc, "_victron_ess_balance_telemetry_stability_score", None)
        )
        overshoot_count = max(
            0,
            int(
                active_profile.get("overshoot_count", 0)
                if use_profile
                else getattr(svc, "_victron_ess_balance_telemetry_overshoot_count", 0) or 0
            ),
        )
        settled_count = max(
            0,
            int(
                active_profile.get("settled_count", 0)
                if use_profile
                else getattr(svc, "_victron_ess_balance_telemetry_settled_count", 0) or 0
            ),
        )
        delay_samples = max(
            0,
            int(
                active_profile.get("delay_samples", 0)
                if use_profile
                else getattr(svc, "_victron_ess_balance_telemetry_delay_samples", 0) or 0
            ),
        )
        gain_samples = max(
            0,
            int(
                active_profile.get("gain_samples", 0)
                if use_profile
                else getattr(svc, "_victron_ess_balance_telemetry_gain_samples", 0) or 0
            ),
        )
        regime_consistency_score = self._optional_float(
            active_profile.get("regime_consistency_score") if use_profile else None
        )
        response_variance_score = self._optional_float(
            active_profile.get("response_variance_score") if use_profile else None
        )
        reproducibility_score = self._optional_float(
            active_profile.get("reproducibility_score") if use_profile else None
        )
        confidence = self._victron_ess_balance_recommendation_confidence(
            delay_samples,
            gain_samples,
            stability_score,
            regime_consistency_score,
            response_variance_score,
            reproducibility_score,
        )
        recommended_kp = current_kp
        recommended_ki = current_ki
        recommended_kd = current_kd
        recommended_deadband = current_deadband
        recommended_max_abs = current_max_abs
        recommended_ramp = current_ramp
        recommended_activation_mode = self._victron_ess_balance_recommended_activation_mode(
            active_profile if use_profile else {},
            svc,
        )
        reason = "insufficient_telemetry"
        if response_delay_seconds is None or estimated_gain is None or confidence < 0.35:
            reason = "insufficient_telemetry"
        elif overshoot_count > 0 or (stability_score is not None and stability_score < 0.55):
            recommended_kp = current_kp * 0.8
            recommended_ki = current_ki * 0.5
            recommended_kd = max(current_kd, 0.02)
            recommended_deadband = current_deadband * 1.25 if current_deadband > 0.0 else 125.0
            recommended_max_abs = current_max_abs * 0.8 if current_max_abs > 0.0 else 350.0
            recommended_ramp = current_ramp * 0.7 if current_ramp > 0.0 else 25.0
            reason = "overshoot_risk"
        elif response_delay_seconds > 8.0 or estimated_gain < 0.75:
            recommended_kp = current_kp * 0.9
            recommended_ki = current_ki * 0.6
            recommended_kd = max(current_kd, 0.01)
            recommended_deadband = current_deadband * 1.1 if current_deadband > 0.0 else 110.0
            recommended_max_abs = current_max_abs * 0.9 if current_max_abs > 0.0 else 425.0
            recommended_ramp = current_ramp * 0.8 if current_ramp > 0.0 else 35.0
            reason = "slow_response"
        elif (
            stability_score is not None
            and stability_score >= 0.8
            and settled_count >= 2
            and overshoot_count == 0
            and response_delay_seconds <= 5.0
            and estimated_gain >= 0.75
        ):
            recommended_kp = current_kp * 1.15
            recommended_ki = current_ki * 1.1
            recommended_kd = current_kd * 0.8 if current_kd > 0.0 else 0.0
            recommended_deadband = current_deadband * 0.9 if current_deadband > 0.0 else 80.0
            recommended_max_abs = current_max_abs * 1.1 if current_max_abs > 0.0 else 550.0
            recommended_ramp = current_ramp * 1.1 if current_ramp > 0.0 else 60.0
            reason = "can_relax_conservatism"
        else:
            reason = "telemetry_nominal"
        recommended_deadband = max(0.0, float(recommended_deadband))
        recommended_max_abs = max(0.0, float(recommended_max_abs))
        ini_snippet = self._victron_ess_balance_recommendation_ini_snippet(
            recommended_kp,
            recommended_ki,
            recommended_kd,
            recommended_deadband,
            recommended_max_abs,
            recommended_ramp,
            recommended_activation_mode,
        )
        hint = self._victron_ess_balance_recommendation_hint(reason, confidence)
        return {
            "battery_discharge_balance_victron_bias_recommended_kp": round(float(recommended_kp), 4),
            "battery_discharge_balance_victron_bias_recommended_ki": round(float(recommended_ki), 4),
            "battery_discharge_balance_victron_bias_recommended_kd": round(float(recommended_kd), 4),
            "battery_discharge_balance_victron_bias_recommended_deadband_watts": round(float(recommended_deadband), 4),
            "battery_discharge_balance_victron_bias_recommended_max_abs_watts": round(float(recommended_max_abs), 4),
            "battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second": round(
                float(recommended_ramp), 4
            ),
            "battery_discharge_balance_victron_bias_recommended_activation_mode": recommended_activation_mode,
            "battery_discharge_balance_victron_bias_recommendation_confidence": round(float(confidence), 4),
            "battery_discharge_balance_victron_bias_recommendation_regime_consistency_score": regime_consistency_score,
            "battery_discharge_balance_victron_bias_recommendation_response_variance_score": response_variance_score,
            "battery_discharge_balance_victron_bias_recommendation_reproducibility_score": reproducibility_score,
            "battery_discharge_balance_victron_bias_recommendation_reason": reason,
            "battery_discharge_balance_victron_bias_recommendation_profile_key": active_profile_key if use_profile else "",
            "battery_discharge_balance_victron_bias_recommendation_ini_snippet": ini_snippet,
            "battery_discharge_balance_victron_bias_recommendation_hint": hint,
        }

    @staticmethod
    def _victron_ess_balance_recommendation_confidence(
        delay_samples: int,
        gain_samples: int,
        stability_score: float | None,
        regime_consistency_score: float | None,
        response_variance_score: float | None,
        reproducibility_score: float | None,
    ) -> float:
        sample_component = min(0.55, (0.12 * float(delay_samples)) + (0.12 * float(gain_samples)))
        stability_component = 0.0 if stability_score is None else min(0.35, 0.35 * max(0.0, float(stability_score)))
        consistency_component = (
            0.0
            if regime_consistency_score is None
            else min(0.2, 0.2 * max(0.0, float(regime_consistency_score)))
        )
        variance_component = (
            0.0
            if response_variance_score is None
            else min(0.15, 0.15 * max(0.0, float(response_variance_score)))
        )
        reproducibility_component = (
            0.0
            if reproducibility_score is None
            else min(0.2, 0.2 * max(0.0, float(reproducibility_score)))
        )
        return max(
            0.1,
            min(
                1.0,
                0.1
                + sample_component
                + stability_component
                + consistency_component
                + variance_component
                + reproducibility_component,
            ),
        )

    @staticmethod
    def _victron_ess_balance_recommendation_ini_snippet(
        recommended_kp: float,
        recommended_ki: float,
        recommended_kd: float,
        recommended_deadband: float,
        recommended_max_abs: float,
        recommended_ramp: float,
        recommended_activation_mode: str,
    ) -> str:
        return "\n".join(
            (
                "AutoBatteryDischargeBalanceVictronBiasKp=" f"{float(recommended_kp):g}",
                "AutoBatteryDischargeBalanceVictronBiasKi=" f"{float(recommended_ki):g}",
                "AutoBatteryDischargeBalanceVictronBiasKd=" f"{float(recommended_kd):g}",
                "AutoBatteryDischargeBalanceVictronBiasDeadbandWatts=" f"{float(recommended_deadband):g}",
                "AutoBatteryDischargeBalanceVictronBiasMaxAbsWatts=" f"{float(recommended_max_abs):g}",
                "AutoBatteryDischargeBalanceVictronBiasRampRateWattsPerSecond=" f"{float(recommended_ramp):g}",
                "AutoBatteryDischargeBalanceVictronBiasActivationMode="
                f"{str(recommended_activation_mode or 'always')}",
            )
        )

    @staticmethod
    def _victron_ess_balance_recommendation_hint(reason: str, confidence: float) -> str:
        confidence_text = f"{float(confidence):.2f}"
        if reason == "can_relax_conservatism":
            return (
                "Telemetry looks stable; you can cautiously relax the current "
                f"Victron bias tuning (confidence {confidence_text})."
            )
        if reason == "overshoot_risk":
            return (
                "Telemetry shows overshoot risk; use more conservative Victron "
                f"bias tuning (confidence {confidence_text})."
            )
        if reason == "slow_response":
            return (
                "Telemetry suggests a slow site response; reduce aggressiveness "
                f"and ramp more gently (confidence {confidence_text})."
            )
        if reason == "telemetry_nominal":
            return (
                "Telemetry looks broadly nominal; keep tuning close to the "
                f"current values (confidence {confidence_text})."
            )
        return (
            "Telemetry is still too thin for a strong tuning recommendation "
            f"(confidence {confidence_text})."
        )

    def _victron_ess_balance_recommended_activation_mode(
        self,
        active_profile: dict[str, Any],
        svc: Any,
    ) -> str:
        reserve_phase = str(active_profile.get("reserve_phase", "") or "")
        site_regime = str(active_profile.get("site_regime", "") or "")
        current_mode = self._victron_ess_balance_activation_mode(svc)
        if site_regime == "export":
            if reserve_phase == "above_reserve_band":
                return "export_and_above_reserve_band"
            return "export_only"
        if reserve_phase == "above_reserve_band":
            return "above_reserve_band"
        return current_mode
