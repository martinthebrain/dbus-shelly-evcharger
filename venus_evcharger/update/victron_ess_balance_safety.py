# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined,no-any-return"
# pyright: reportAttributeAccessIssue=false, reportReturnType=false
"""Victron ESS balance-bias safety helpers."""

from __future__ import annotations

from typing import Any, cast

from .victron_ess_balance_apply import _UpdateCycleVictronEssBalanceApplyMixin


class _UpdateCycleVictronEssBalanceSafetyMixin:
    def _populate_victron_ess_balance_runtime_safety_metrics(
        self,
        svc: Any,
        now: float,
        metrics: dict[str, Any],
    ) -> None:
        lockout_until = self._optional_float(getattr(svc, "_victron_ess_balance_oscillation_lockout_until", None))
        metrics["battery_discharge_balance_victron_bias_oscillation_lockout_enabled"] = int(
            bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled", True))
        )
        metrics["battery_discharge_balance_victron_bias_oscillation_lockout_active"] = int(
            bool(lockout_until is not None and float(now) < float(lockout_until))
        )
        metrics["battery_discharge_balance_victron_bias_oscillation_lockout_reason"] = str(
            getattr(svc, "_victron_ess_balance_oscillation_lockout_reason", "") or ""
        )
        metrics["battery_discharge_balance_victron_bias_oscillation_lockout_until"] = lockout_until
        metrics["battery_discharge_balance_victron_bias_oscillation_direction_change_count"] = int(
            self._victron_ess_balance_recent_direction_change_count(svc, now)
        )
        metrics["battery_discharge_balance_victron_bias_overshoot_cooldown_active"] = int(
            self._victron_ess_balance_overshoot_cooldown_active(svc, now)
        )
        metrics["battery_discharge_balance_victron_bias_overshoot_cooldown_reason"] = str(
            getattr(svc, "_victron_ess_balance_overshoot_cooldown_reason", "") or ""
        )
        metrics["battery_discharge_balance_victron_bias_overshoot_cooldown_until"] = self._optional_float(
            getattr(svc, "_victron_ess_balance_overshoot_cooldown_until", None)
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
        metrics["battery_discharge_balance_victron_bias_safe_state_active"] = int(
            bool(getattr(svc, "_victron_ess_balance_safe_state_active", False))
        )
        metrics["battery_discharge_balance_victron_bias_safe_state_reason"] = str(
            getattr(svc, "_victron_ess_balance_safe_state_reason", "") or ""
        )

    def _victron_ess_balance_telemetry_is_clean(
        self,
        svc: Any,
        cluster: dict[str, Any],
        source_error_w: float,
    ) -> tuple[bool, str]:
        if not bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_require_clean_phases", True)):
            return True, "clean_not_required"
        if bool(getattr(svc, "_auto_cached_inputs_used", False)):
            return False, "cached_inputs"
        if str(getattr(svc, "_phase_switch_state", "") or "").strip():
            return False, "phase_switch_active"
        if str(getattr(svc, "_contactor_fault_active_reason", "") or "").strip():
            return False, "contactor_fault_active"
        if str(getattr(svc, "_contactor_lockout_reason", "") or "").strip():
            return False, "contactor_lockout_active"
        grid_interaction_w = self._optional_float(cluster.get("battery_combined_grid_interaction_w"))
        if grid_interaction_w is None:
            return False, "grid_interaction_missing"
        last_grid_interaction_w = self._optional_float(
            getattr(svc, "_victron_ess_balance_telemetry_last_grid_interaction_w", None)
        )
        if last_grid_interaction_w is not None and abs(float(grid_interaction_w) - float(last_grid_interaction_w)) > 600.0:
            return False, "grid_unstable"
        ac_power_w = self._optional_float(cluster.get("battery_combined_ac_power_w"))
        last_ac_power_w = self._optional_float(getattr(svc, "_victron_ess_balance_telemetry_last_ac_power_w", None))
        if ac_power_w is not None and last_ac_power_w is not None and abs(float(ac_power_w) - float(last_ac_power_w)) > 900.0:
            return False, "foreign_power_event"
        ev_power_w = self._victron_ess_balance_ev_power_w(svc)
        last_ev_power_w = self._optional_float(getattr(svc, "_victron_ess_balance_telemetry_last_ev_power_w", None))
        if ev_power_w is not None and last_ev_power_w is not None and abs(float(ev_power_w) - float(last_ev_power_w)) > 500.0:
            return False, "ev_load_jump"
        deadband_w = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_deadband_watts", 0.0) or 0.0),
        )
        if abs(float(source_error_w)) < max(10.0, deadband_w):
            return False, "error_inside_deadband"
        return True, "clean"

    @staticmethod
    def _victron_ess_balance_recent_direction_change_count(svc: Any, now: float) -> int:
        window_seconds = max(
            0.0,
            float(
                getattr(
                    svc,
                    "auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds",
                    120.0,
                )
                or 120.0
            ),
        )
        raw_entries = getattr(svc, "_victron_ess_balance_recent_action_changes", None)
        entries = raw_entries if isinstance(raw_entries, list) else []
        cutoff = float(now) - window_seconds
        kept = [
            dict(entry)
            for entry in entries
            if isinstance(entry, dict)
            and _UpdateCycleVictronEssBalanceApplyMixin._optional_float(entry.get("at")) is not None
            and float(cast(float, _UpdateCycleVictronEssBalanceApplyMixin._optional_float(entry.get("at")))) >= cutoff
        ]
        svc._victron_ess_balance_recent_action_changes = kept
        return max(0, len(kept) - 1)

    def _victron_ess_balance_note_action_direction(self, svc: Any, action_direction: str, now: float) -> int:
        raw_action = str(action_direction or "").strip()
        if raw_action not in {"more_export", "less_export"}:
            return self._victron_ess_balance_recent_direction_change_count(svc, now)
        entries = getattr(svc, "_victron_ess_balance_recent_action_changes", None)
        if not isinstance(entries, list):
            entries = []
        last_direction = str(getattr(svc, "_victron_ess_balance_last_action_direction", "") or "").strip()
        if not entries:
            entries.append({"at": float(now), "action_direction": raw_action})
        elif last_direction != raw_action:
            entries.append({"at": float(now), "action_direction": raw_action})
        svc._victron_ess_balance_last_action_direction = raw_action
        svc._victron_ess_balance_recent_action_changes = entries
        direction_change_count = self._victron_ess_balance_recent_direction_change_count(svc, now)
        if (
            bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled", True))
            and direction_change_count
            >= max(
                1,
                int(
                    getattr(
                        svc,
                        "auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes",
                        3,
                    )
                    or 3
                ),
            )
        ):
            duration_seconds = max(
                0.0,
                float(
                    getattr(
                        svc,
                        "auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds",
                        180.0,
                    )
                    or 180.0
                ),
            )
            svc._victron_ess_balance_oscillation_lockout_until = float(now + duration_seconds)
            svc._victron_ess_balance_oscillation_lockout_reason = "direction_change_oscillation"
            self._reset_victron_ess_balance_pid_integral(svc, aggressive=True)
        return direction_change_count

    def _victron_ess_balance_oscillation_lockout_active(self, svc: Any, now: float) -> bool:
        lockout_until = self._optional_float(getattr(svc, "_victron_ess_balance_oscillation_lockout_until", None))
        return bool(lockout_until is not None and float(now) < float(lockout_until))

    def _victron_ess_balance_refresh_stable_tuning(self, svc: Any, metrics: dict[str, Any], now: float) -> None:
        confidence = self._optional_float(metrics.get("battery_discharge_balance_victron_bias_recommendation_confidence"))
        stability = self._optional_float(metrics.get("battery_discharge_balance_victron_bias_learning_profile_stability_score"))
        sample_count = max(
            0,
            int(metrics.get("battery_discharge_balance_victron_bias_learning_profile_sample_count", 0) or 0),
        )
        overshoot_count = max(
            0,
            int(metrics.get("battery_discharge_balance_victron_bias_learning_profile_overshoot_count", 0) or 0),
        )
        if (
            confidence is None
            or confidence < 0.8
            or stability is None
            or stability < 0.8
            or sample_count < 2
            or overshoot_count > 0
        ):
            return
        if not getattr(svc, "_victron_ess_balance_conservative_tuning", None):
            svc._victron_ess_balance_conservative_tuning = self._victron_ess_balance_current_tuning_snapshot(svc)
        svc._victron_ess_balance_last_stable_tuning = self._victron_ess_balance_current_tuning_snapshot(svc)
        svc._victron_ess_balance_last_stable_at = float(now)
        svc._victron_ess_balance_last_stable_profile_key = str(
            metrics.get("battery_discharge_balance_victron_bias_learning_profile_key", "") or ""
        ).strip()

    def _victron_ess_balance_should_rollback_stable_tuning(
        self,
        svc: Any,
        metrics: dict[str, Any],
        now: float,
    ) -> bool:
        if not bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_rollback_enabled", True)):
            return False
        observe_until = self._optional_float(getattr(svc, "_victron_ess_balance_auto_apply_observe_until", None))
        if observe_until is None or float(now) >= float(observe_until):
            return False
        if bool(metrics.get("battery_discharge_balance_victron_bias_oscillation_lockout_active")):
            return True
        if bool(metrics.get("battery_discharge_balance_victron_bias_overshoot_active")):
            return True
        if bool(metrics.get("battery_discharge_balance_victron_bias_overshoot_cooldown_active")):
            return True
        stability = self._optional_float(metrics.get("battery_discharge_balance_victron_bias_stability_score"))
        rollback_min_stability = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_rollback_min_stability_score", 0.45) or 0.45),
        )
        return bool(stability is not None and stability < rollback_min_stability)

    def _maybe_restore_victron_ess_balance_stable_tuning(
        self,
        svc: Any,
        metrics: dict[str, Any],
        reason: str,
    ) -> bool:
        metrics["battery_discharge_balance_victron_bias_rollback_enabled"] = int(
            bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_rollback_enabled", True))
        )
        stable = getattr(svc, "_victron_ess_balance_last_stable_tuning", None)
        if not isinstance(stable, dict) or not stable:
            conservative = getattr(svc, "_victron_ess_balance_conservative_tuning", None)
            if not isinstance(conservative, dict) or not conservative:
                metrics["battery_discharge_balance_victron_bias_rollback_reason"] = "no_stable_tuning"
                return False
            stable = conservative
            svc._victron_ess_balance_safe_state_active = True
            svc._victron_ess_balance_safe_state_reason = "conservative_fallback"
        else:
            svc._victron_ess_balance_safe_state_active = True
            svc._victron_ess_balance_safe_state_reason = str(reason)
        svc.auto_battery_discharge_balance_victron_bias_kp = float(stable.get("kp", 0.0) or 0.0)
        svc.auto_battery_discharge_balance_victron_bias_ki = float(stable.get("ki", 0.0) or 0.0)
        svc.auto_battery_discharge_balance_victron_bias_kd = float(stable.get("kd", 0.0) or 0.0)
        svc.auto_battery_discharge_balance_victron_bias_deadband_watts = float(stable.get("deadband_watts", 0.0) or 0.0)
        svc.auto_battery_discharge_balance_victron_bias_max_abs_watts = float(stable.get("max_abs_watts", 0.0) or 0.0)
        svc.auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second = float(
            stable.get("ramp_rate_watts_per_second", 0.0) or 0.0
        )
        activation_mode = str(stable.get("activation_mode", self._victron_ess_balance_activation_mode(svc)) or "always").strip()
        if activation_mode:
            svc.auto_battery_discharge_balance_victron_bias_activation_mode = activation_mode
        svc._victron_ess_balance_auto_apply_observe_until = None
        self._victron_ess_balance_suspend_auto_apply(
            svc,
            reason,
            self._optional_float(getattr(svc, "_victron_ess_balance_auto_apply_last_applied_at", None)),
        )
        metrics["battery_discharge_balance_victron_bias_rollback_active"] = 1
        metrics["battery_discharge_balance_victron_bias_rollback_reason"] = str(reason)
        metrics["battery_discharge_balance_victron_bias_rollback_stable_profile_key"] = str(
            getattr(svc, "_victron_ess_balance_last_stable_profile_key", "") or ""
        )
        metrics["battery_discharge_balance_victron_bias_safe_state_active"] = 1
        metrics["battery_discharge_balance_victron_bias_safe_state_reason"] = str(
            getattr(svc, "_victron_ess_balance_safe_state_reason", "") or ""
        )
        return True

    @staticmethod
    def _victron_ess_balance_ev_power_w(svc: Any) -> float | None:
        for attr_name in ("_last_charger_state_power_w", "_charger_estimated_power_w", "_last_power", "ac_power"):
            value = getattr(svc, attr_name, None)
            if isinstance(value, (int, float)):
                return float(value)
        learned_charge_power = getattr(svc, "learned_charge_power_watts", None)
        if isinstance(learned_charge_power, (int, float)) and int(getattr(svc, "virtual_startstop", 0) or 0):
            return float(learned_charge_power)
        return None

    @classmethod
    def _victron_ess_balance_ev_active(cls, svc: Any) -> bool:
        ev_power_w = cls._victron_ess_balance_ev_power_w(svc)
        if ev_power_w is not None and ev_power_w >= 200.0:
            return True
        if getattr(svc, "charging_started_at", None) is not None:
            return True
        return bool(int(getattr(svc, "virtual_startstop", 0) or 0))

    def _enter_victron_ess_balance_overshoot_cooldown(self, svc: Any, now: float, reason: str) -> None:
        response_delay = self._optional_float(getattr(svc, "_victron_ess_balance_telemetry_response_delay_seconds", None))
        cooldown_seconds = max(20.0, min(180.0, (response_delay or 10.0) * 4.0))
        svc._victron_ess_balance_overshoot_cooldown_until = float(now + cooldown_seconds)
        svc._victron_ess_balance_overshoot_cooldown_reason = str(reason)
        self._victron_ess_balance_suspend_auto_apply(svc, "overshoot_cooldown", now)

    def _victron_ess_balance_overshoot_cooldown_active(self, svc: Any, now: float) -> bool:
        cooldown_until = self._optional_float(getattr(svc, "_victron_ess_balance_overshoot_cooldown_until", None))
        return bool(cooldown_until is not None and float(now) < float(cooldown_until))

    def _victron_ess_balance_suspend_auto_apply(self, svc: Any, reason: str, now: float | None = None) -> None:
        effective_now = (
            float(now)
            if isinstance(now, (int, float))
            else (
                self._optional_float(getattr(svc, "_victron_ess_balance_telemetry_last_observed_at", None))
                or self._optional_float(getattr(svc, "_victron_ess_balance_auto_apply_last_applied_at", None))
                or 0.0
            )
        )
        observation_seconds = max(
            30.0,
            float(
                getattr(svc, "auto_battery_discharge_balance_victron_bias_observation_window_seconds", 30.0) or 30.0
            ),
        )
        svc._victron_ess_balance_auto_apply_suspend_until = float(effective_now + (2.0 * observation_seconds))
        svc._victron_ess_balance_auto_apply_suspend_reason = str(reason)

    def _victron_ess_balance_auto_apply_suspended(self, svc: Any, now: float) -> bool:
        suspend_until = self._optional_float(getattr(svc, "_victron_ess_balance_auto_apply_suspend_until", None))
        return bool(suspend_until is not None and float(now) < float(suspend_until))
