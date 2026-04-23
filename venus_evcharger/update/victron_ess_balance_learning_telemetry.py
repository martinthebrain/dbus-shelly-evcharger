# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined,no-any-return"
# pyright: reportAttributeAccessIssue=false, reportReturnType=false
"""Victron ESS balance-bias telemetry-learning helpers."""

from __future__ import annotations

from typing import Any


class _UpdateCycleVictronEssBalanceLearningTelemetryMixin:
    def _update_victron_ess_balance_telemetry(
        self,
        svc: Any,
        now: float,
        cluster: dict[str, Any],
        source_error_w: float,
        metrics: dict[str, Any],
        profile_key: str,
    ) -> None:
        command_at = self._optional_float(getattr(svc, "_victron_ess_balance_telemetry_last_command_at", None))
        command_error_w = self._optional_float(getattr(svc, "_victron_ess_balance_telemetry_last_command_error_w", None))
        command_setpoint_w = self._optional_float(
            getattr(svc, "_victron_ess_balance_telemetry_last_command_setpoint_w", None)
        )
        command_profile_key = str(
            getattr(svc, "_victron_ess_balance_telemetry_last_command_profile_key", profile_key) or profile_key
        ).strip()
        command_response_recorded = bool(
            getattr(svc, "_victron_ess_balance_telemetry_command_response_recorded", False)
        )
        command_overshoot_recorded = bool(
            getattr(svc, "_victron_ess_balance_telemetry_command_overshoot_recorded", False)
        )
        command_settled_recorded = bool(
            getattr(svc, "_victron_ess_balance_telemetry_command_settled_recorded", False)
        )
        deadband_w = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_deadband_watts", 0.0) or 0.0),
        )
        base_setpoint_w = float(
            getattr(svc, "auto_battery_discharge_balance_victron_bias_base_setpoint_watts", 50.0) or 0.0
        )
        improvement_threshold_w = max(10.0, deadband_w * 0.5)
        active_episode = command_at is not None and command_error_w is not None and command_setpoint_w is not None
        overshoot_active = False
        settling_active = False
        telemetry_clean, telemetry_clean_reason = self._victron_ess_balance_telemetry_is_clean(
            svc,
            cluster,
            source_error_w,
        )
        metrics["battery_discharge_balance_victron_bias_telemetry_clean"] = int(telemetry_clean)
        metrics["battery_discharge_balance_victron_bias_telemetry_clean_reason"] = telemetry_clean_reason

        if active_episode and telemetry_clean:
            assert command_at is not None
            assert command_error_w is not None
            assert command_setpoint_w is not None
            command_at_f = float(command_at)
            command_error_w_f = float(command_error_w)
            command_setpoint_w_f = float(command_setpoint_w)
            initial_abs_error_w = abs(command_error_w_f)
            current_abs_error_w = abs(float(source_error_w))
            improvement_w = max(0.0, initial_abs_error_w - current_abs_error_w)
            setpoint_bias_w = abs(command_setpoint_w_f - base_setpoint_w)
            if not command_response_recorded and improvement_w >= improvement_threshold_w:
                response_delay_seconds = max(0.0, float(now) - command_at_f)
                samples = int(getattr(svc, "_victron_ess_balance_telemetry_delay_samples", 0) or 0)
                current_delay = self._optional_float(
                    getattr(svc, "_victron_ess_balance_telemetry_response_delay_seconds", None)
                )
                svc._victron_ess_balance_telemetry_response_delay_seconds = self._ewma_learned_value(
                    current_delay,
                    response_delay_seconds,
                    samples,
                )
                svc._victron_ess_balance_telemetry_delay_samples = samples + 1
                self._victron_ess_balance_update_profile_delay(
                    svc,
                    command_profile_key,
                    response_delay_seconds,
                )
                svc._victron_ess_balance_telemetry_command_response_recorded = True
            if improvement_w > 0.0 and setpoint_bias_w >= 1.0:
                gain_sample = improvement_w / setpoint_bias_w
                samples = int(getattr(svc, "_victron_ess_balance_telemetry_gain_samples", 0) or 0)
                current_gain = self._optional_float(getattr(svc, "_victron_ess_balance_telemetry_estimated_gain", None))
                svc._victron_ess_balance_telemetry_estimated_gain = self._ewma_learned_value(
                    current_gain,
                    gain_sample,
                    samples,
                )
                svc._victron_ess_balance_telemetry_gain_samples = samples + 1
                self._victron_ess_balance_update_profile_gain(
                    svc,
                    command_profile_key,
                    gain_sample,
                )
            if (
                not command_overshoot_recorded
                and command_error_w_f != 0.0
                and float(source_error_w) != 0.0
                and (command_error_w_f * float(source_error_w)) < 0.0
                and current_abs_error_w >= improvement_threshold_w
            ):
                svc._victron_ess_balance_telemetry_command_overshoot_recorded = True
                svc._victron_ess_balance_telemetry_overshoot_count = int(
                    getattr(svc, "_victron_ess_balance_telemetry_overshoot_count", 0) or 0
                ) + 1
                self._victron_ess_balance_increment_profile_counter(
                    svc,
                    command_profile_key,
                    "overshoot_count",
                )
                self._enter_victron_ess_balance_overshoot_cooldown(
                    svc,
                    now,
                    "overshoot_detected",
                )
                self._reset_victron_ess_balance_pid_integral(svc, aggressive=True)
                command_overshoot_recorded = True
            if not command_settled_recorded and current_abs_error_w <= deadband_w:
                svc._victron_ess_balance_telemetry_command_settled_recorded = True
                svc._victron_ess_balance_telemetry_settled_count = int(
                    getattr(svc, "_victron_ess_balance_telemetry_settled_count", 0) or 0
                ) + 1
                self._victron_ess_balance_increment_profile_counter(
                    svc,
                    command_profile_key,
                    "settled_count",
                )
                command_settled_recorded = True
            overshoot_active = bool(command_overshoot_recorded)
            settling_active = bool(not command_settled_recorded and not command_overshoot_recorded)

        svc._victron_ess_balance_telemetry_overshoot_active = overshoot_active
        svc._victron_ess_balance_telemetry_settling_active = settling_active
        svc._victron_ess_balance_telemetry_last_observed_error_w = float(source_error_w)
        svc._victron_ess_balance_telemetry_last_observed_at = float(now)
        svc._victron_ess_balance_telemetry_last_grid_interaction_w = self._optional_float(
            cluster.get("battery_combined_grid_interaction_w")
        )
        svc._victron_ess_balance_telemetry_last_ac_power_w = self._optional_float(
            cluster.get("battery_combined_ac_power_w")
        )
        svc._victron_ess_balance_telemetry_last_ev_power_w = self._victron_ess_balance_ev_power_w(svc)
        svc._victron_ess_balance_telemetry_stability_score = self._victron_ess_balance_stability_score(svc)
        self._victron_ess_balance_refresh_profile_stability(svc, command_profile_key or profile_key)
        self._populate_victron_ess_balance_telemetry_metrics(svc, metrics)

    @staticmethod
    def _ewma_learned_value(current: float | None, sample: float, samples: int) -> float:
        if current is None or samples <= 0:
            return float(sample)
        alpha = 0.25
        return (alpha * float(sample)) + ((1.0 - alpha) * float(current))

    @staticmethod
    def _victron_ess_balance_stability_score_values(
        settled_count: int,
        overshoot_count: int,
        estimated_gain: float | None,
        response_delay_seconds: float | None,
    ) -> float:
        total_outcomes = settled_count + overshoot_count
        settle_ratio = 1.0 if total_outcomes <= 0 else (float(settled_count) / float(total_outcomes))
        overshoot_penalty = 0.0 if total_outcomes <= 0 else min(0.6, float(overshoot_count) / float(total_outcomes))
        gain_bonus = 0.15 if estimated_gain is not None and estimated_gain > 0.0 else 0.0
        delay_penalty = (
            0.0
            if response_delay_seconds is None
            else min(0.2, max(0.0, response_delay_seconds - 5.0) / 50.0)
        )
        score = max(0.0, min(1.0, 0.45 + (0.4 * settle_ratio) + gain_bonus - overshoot_penalty - delay_penalty))
        return float(score)

    @classmethod
    def _victron_ess_balance_stability_score(cls, svc: Any) -> float:
        settled_count = max(0, int(getattr(svc, "_victron_ess_balance_telemetry_settled_count", 0) or 0))
        overshoot_count = max(0, int(getattr(svc, "_victron_ess_balance_telemetry_overshoot_count", 0) or 0))
        estimated_gain = cls._optional_float(getattr(svc, "_victron_ess_balance_telemetry_estimated_gain", None))
        response_delay_seconds = cls._optional_float(
            getattr(svc, "_victron_ess_balance_telemetry_response_delay_seconds", None)
        )
        return cls._victron_ess_balance_stability_score_values(
            settled_count,
            overshoot_count,
            estimated_gain,
            response_delay_seconds,
        )

    @staticmethod
    def _victron_ess_balance_variance_score(
        delay_mean: float | None,
        delay_mad: float | None,
        gain_mean: float | None,
        gain_mad: float | None,
    ) -> float:
        normalized_delay_mean = None if delay_mean is None else float(delay_mean)
        normalized_gain_mean = None if gain_mean is None else float(gain_mean)
        if normalized_delay_mean is None or normalized_delay_mean == 0.0 or delay_mad is None:
            delay_ratio = 1.0
        else:
            delay_ratio = max(0.0, 1.0 - (float(delay_mad) / max(1.0, normalized_delay_mean)))
        if normalized_gain_mean is None or normalized_gain_mean == 0.0 or gain_mad is None:
            gain_ratio = 1.0
        else:
            gain_ratio = max(0.0, 1.0 - (float(gain_mad) / max(0.1, normalized_gain_mean)))
        return max(0.0, min(1.0, (delay_ratio + gain_ratio) / 2.0))

    @classmethod
    def _victron_ess_balance_regime_consistency_score(cls, profile: dict[str, Any]) -> float:
        sample_count = cls._victron_ess_balance_profile_sample_count(profile)
        sample_score = min(1.0, float(sample_count) / 4.0)
        stability_score = cls._optional_float(profile.get("stability_score")) or 0.0
        variance_score = cls._optional_float(profile.get("response_variance_score")) or 0.0
        return max(0.0, min(1.0, (0.3 * sample_score) + (0.35 * stability_score) + (0.35 * variance_score)))

    @classmethod
    def _victron_ess_balance_reproducibility_score(cls, profile: dict[str, Any]) -> float:
        settled_count = max(0, int(profile.get("settled_count", 0) or 0))
        overshoot_count = max(0, int(profile.get("overshoot_count", 0) or 0))
        total = settled_count + overshoot_count
        settle_ratio = 1.0 if total <= 0 else float(settled_count) / float(total)
        variance_score = cls._optional_float(profile.get("response_variance_score")) or 0.0
        return max(0.0, min(1.0, (0.6 * settle_ratio) + (0.4 * variance_score)))

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
