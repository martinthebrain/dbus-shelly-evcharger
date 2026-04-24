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
        metrics.update(self._victron_ess_balance_lockout_metrics(svc, now, lockout_until))
        metrics.update(self._victron_ess_balance_cooldown_metrics(svc, now))
        metrics.update(self._victron_ess_balance_auto_apply_suspend_metrics(svc, now))
        metrics.update(self._victron_ess_balance_safe_state_metrics(svc))

    def _victron_ess_balance_lockout_metrics(
        self,
        svc: Any,
        now: float,
        lockout_until: float | None,
    ) -> dict[str, Any]:
        return {
            "battery_discharge_balance_victron_bias_oscillation_lockout_enabled": int(
                bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled", True))
            ),
            "battery_discharge_balance_victron_bias_oscillation_lockout_active": int(
                bool(lockout_until is not None and float(now) < float(lockout_until))
            ),
            "battery_discharge_balance_victron_bias_oscillation_lockout_reason": str(
                getattr(svc, "_victron_ess_balance_oscillation_lockout_reason", "") or ""
            ),
            "battery_discharge_balance_victron_bias_oscillation_lockout_until": lockout_until,
            "battery_discharge_balance_victron_bias_oscillation_direction_change_count": int(
                self._victron_ess_balance_recent_direction_change_count(svc, now)
            ),
        }

    def _victron_ess_balance_cooldown_metrics(self, svc: Any, now: float) -> dict[str, Any]:
        return {
            "battery_discharge_balance_victron_bias_overshoot_cooldown_active": int(
                self._victron_ess_balance_overshoot_cooldown_active(svc, now)
            ),
            "battery_discharge_balance_victron_bias_overshoot_cooldown_reason": str(
                getattr(svc, "_victron_ess_balance_overshoot_cooldown_reason", "") or ""
            ),
            "battery_discharge_balance_victron_bias_overshoot_cooldown_until": self._optional_float(
                getattr(svc, "_victron_ess_balance_overshoot_cooldown_until", None)
            ),
        }

    def _victron_ess_balance_auto_apply_suspend_metrics(self, svc: Any, now: float) -> dict[str, Any]:
        return {
            "battery_discharge_balance_victron_bias_auto_apply_suspend_active": int(
                self._victron_ess_balance_auto_apply_suspended(svc, now)
            ),
            "battery_discharge_balance_victron_bias_auto_apply_suspend_reason": str(
                getattr(svc, "_victron_ess_balance_auto_apply_suspend_reason", "") or ""
            ),
            "battery_discharge_balance_victron_bias_auto_apply_suspend_until": self._optional_float(
                getattr(svc, "_victron_ess_balance_auto_apply_suspend_until", None)
            ),
        }

    @staticmethod
    def _victron_ess_balance_safe_state_metrics(svc: Any) -> dict[str, Any]:
        return {
            "battery_discharge_balance_victron_bias_safe_state_active": int(
                bool(getattr(svc, "_victron_ess_balance_safe_state_active", False))
            ),
            "battery_discharge_balance_victron_bias_safe_state_reason": str(
                getattr(svc, "_victron_ess_balance_safe_state_reason", "") or ""
            ),
        }

    def _victron_ess_balance_telemetry_is_clean(
        self,
        svc: Any,
        cluster: dict[str, Any],
        source_error_w: float,
    ) -> tuple[bool, str]:
        precheck = self._victron_ess_balance_telemetry_precheck_reason(svc)
        if precheck is not None:
            return precheck
        telemetry_reason = self._victron_ess_balance_telemetry_window_reason(svc, cluster)
        if telemetry_reason is not None:
            return False, telemetry_reason
        if self._victron_ess_balance_error_inside_deadband(svc, source_error_w):
            return False, "error_inside_deadband"
        return True, "clean"

    def _victron_ess_balance_telemetry_window_reason(
        self,
        svc: Any,
        cluster: dict[str, Any],
    ) -> str | None:
        grid_reason = self._victron_ess_balance_grid_window_reason(svc, cluster)
        if grid_reason is not None:
            return grid_reason
        power_reason = self._victron_ess_balance_power_window_reason(svc, cluster)
        if power_reason is not None:
            return power_reason
        return self._victron_ess_balance_ev_window_reason(svc)

    def _victron_ess_balance_grid_window_reason(
        self,
        svc: Any,
        cluster: dict[str, Any],
    ) -> str | None:
        grid_interaction_w = self._optional_float(cluster.get("battery_combined_grid_interaction_w"))
        if grid_interaction_w is None:
            return "grid_interaction_missing"
        last_grid_interaction_w = self._optional_float(
            getattr(svc, "_victron_ess_balance_telemetry_last_grid_interaction_w", None)
        )
        if self._victron_ess_balance_grid_interaction_unstable(grid_interaction_w, last_grid_interaction_w):
            return "grid_unstable"
        return None

    def _victron_ess_balance_power_window_reason(
        self,
        svc: Any,
        cluster: dict[str, Any],
    ) -> str | None:
        ac_power_w = self._optional_float(cluster.get("battery_combined_ac_power_w"))
        last_ac_power_w = self._optional_float(getattr(svc, "_victron_ess_balance_telemetry_last_ac_power_w", None))
        if self._victron_ess_balance_foreign_power_event(ac_power_w, last_ac_power_w):
            return "foreign_power_event"
        return None

    def _victron_ess_balance_ev_window_reason(self, svc: Any) -> str | None:
        ev_power_w = self._victron_ess_balance_ev_power_w(svc)
        last_ev_power_w = self._optional_float(getattr(svc, "_victron_ess_balance_telemetry_last_ev_power_w", None))
        if self._victron_ess_balance_ev_load_jump(ev_power_w, last_ev_power_w):
            return "ev_load_jump"
        return None

    @staticmethod
    def _victron_ess_balance_telemetry_precheck_reason(svc: Any) -> tuple[bool, str] | None:
        if not _UpdateCycleVictronEssBalanceSafetyMixin._victron_ess_balance_requires_clean_phases(svc):
            return True, "clean_not_required"
        cached_inputs_reason = _UpdateCycleVictronEssBalanceSafetyMixin._victron_ess_balance_cached_input_reason(svc)
        if cached_inputs_reason is not None:
            return False, cached_inputs_reason
        phase_switch_reason = _UpdateCycleVictronEssBalanceSafetyMixin._victron_ess_balance_phase_switch_reason(svc)
        if phase_switch_reason is not None:
            return False, phase_switch_reason
        contactor_reason = _UpdateCycleVictronEssBalanceSafetyMixin._victron_ess_balance_contactor_block_reason(svc)
        if contactor_reason is not None:
            return False, contactor_reason
        return None

    @staticmethod
    def _victron_ess_balance_requires_clean_phases(svc: Any) -> bool:
        return bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_require_clean_phases", True))

    @staticmethod
    def _victron_ess_balance_cached_input_reason(svc: Any) -> str | None:
        if bool(getattr(svc, "_auto_cached_inputs_used", False)):
            return "cached_inputs"
        return None

    @staticmethod
    def _victron_ess_balance_phase_switch_reason(svc: Any) -> str | None:
        if str(getattr(svc, "_phase_switch_state", "") or "").strip():
            return "phase_switch_active"
        return None

    @staticmethod
    def _victron_ess_balance_contactor_block_reason(svc: Any) -> str | None:
        if str(getattr(svc, "_contactor_fault_active_reason", "") or "").strip():
            return "contactor_fault_active"
        if str(getattr(svc, "_contactor_lockout_reason", "") or "").strip():
            return "contactor_lockout_active"
        return None

    @staticmethod
    def _victron_ess_balance_grid_interaction_unstable(
        grid_interaction_w: float | None,
        last_grid_interaction_w: float | None,
    ) -> bool:
        return (
            grid_interaction_w is not None
            and last_grid_interaction_w is not None
            and abs(float(grid_interaction_w) - float(last_grid_interaction_w)) > 600.0
        )

    @staticmethod
    def _victron_ess_balance_foreign_power_event(
        ac_power_w: float | None,
        last_ac_power_w: float | None,
    ) -> bool:
        return (
            ac_power_w is not None
            and last_ac_power_w is not None
            and abs(float(ac_power_w) - float(last_ac_power_w)) > 900.0
        )

    @staticmethod
    def _victron_ess_balance_ev_load_jump(
        ev_power_w: float | None,
        last_ev_power_w: float | None,
    ) -> bool:
        return (
            ev_power_w is not None
            and last_ev_power_w is not None
            and abs(float(ev_power_w) - float(last_ev_power_w)) > 500.0
        )

    @staticmethod
    def _victron_ess_balance_error_inside_deadband(svc: Any, source_error_w: float) -> bool:
        deadband_w = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_deadband_watts", 0.0) or 0.0),
        )
        return abs(float(source_error_w)) < max(10.0, deadband_w)

    @staticmethod
    def _victron_ess_balance_recent_direction_change_count(svc: Any, now: float) -> int:
        window_seconds = _UpdateCycleVictronEssBalanceSafetyMixin._victron_ess_balance_direction_change_window_seconds(svc)
        raw_entries = getattr(svc, "_victron_ess_balance_recent_action_changes", None)
        entries = raw_entries if isinstance(raw_entries, list) else []
        cutoff = float(now) - window_seconds
        kept = _UpdateCycleVictronEssBalanceSafetyMixin._victron_ess_balance_kept_action_changes(entries, cutoff)
        svc._victron_ess_balance_recent_action_changes = kept
        return max(0, len(kept) - 1)

    @staticmethod
    def _victron_ess_balance_kept_action_changes(entries: list[Any], cutoff: float) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_at = _UpdateCycleVictronEssBalanceApplyMixin._optional_float(entry.get("at"))
            if entry_at is None or float(cast(float, entry_at)) < cutoff:
                continue
            kept.append(dict(entry))
        return kept

    @staticmethod
    def _victron_ess_balance_direction_change_window_seconds(svc: Any) -> float:
        return max(
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

    def _victron_ess_balance_note_action_direction(self, svc: Any, action_direction: str, now: float) -> int:
        raw_action = str(action_direction or "").strip()
        if raw_action not in {"more_export", "less_export"}:
            return self._victron_ess_balance_recent_direction_change_count(svc, now)
        entries = self._victron_ess_balance_action_change_entries(svc)
        last_direction = self._victron_ess_balance_last_action_direction(svc)
        if self._victron_ess_balance_should_record_action_direction(entries, last_direction, raw_action):
            entries.append({"at": float(now), "action_direction": raw_action})
        svc._victron_ess_balance_last_action_direction = raw_action
        svc._victron_ess_balance_recent_action_changes = entries
        direction_change_count = self._victron_ess_balance_recent_direction_change_count(svc, now)
        if self._victron_ess_balance_should_enter_oscillation_lockout(svc, direction_change_count):
            duration_seconds = self._victron_ess_balance_oscillation_lockout_duration_seconds(svc)
            svc._victron_ess_balance_oscillation_lockout_until = float(now + duration_seconds)
            svc._victron_ess_balance_oscillation_lockout_reason = "direction_change_oscillation"
            self._reset_victron_ess_balance_pid_integral(svc, aggressive=True)
        return direction_change_count

    @staticmethod
    def _victron_ess_balance_action_change_entries(svc: Any) -> list[Any]:
        entries = getattr(svc, "_victron_ess_balance_recent_action_changes", None)
        return entries if isinstance(entries, list) else []

    @staticmethod
    def _victron_ess_balance_last_action_direction(svc: Any) -> str:
        return str(getattr(svc, "_victron_ess_balance_last_action_direction", "") or "").strip()

    @staticmethod
    def _victron_ess_balance_should_record_action_direction(
        entries: list[Any],
        last_direction: str,
        raw_action: str,
    ) -> bool:
        return not entries or last_direction != raw_action

    @staticmethod
    def _victron_ess_balance_should_enter_oscillation_lockout(
        svc: Any,
        direction_change_count: int,
    ) -> bool:
        if not bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled", True)):
            return False
        min_direction_changes = max(
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
        return direction_change_count >= min_direction_changes

    @staticmethod
    def _victron_ess_balance_oscillation_lockout_duration_seconds(svc: Any) -> float:
        return max(
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
        if not self._victron_ess_balance_can_refresh_stable_tuning(
            confidence,
            stability,
            sample_count,
            overshoot_count,
        ):
            return
        self._victron_ess_balance_ensure_conservative_tuning(svc)
        svc._victron_ess_balance_last_stable_tuning = self._victron_ess_balance_current_tuning_snapshot(svc)
        svc._victron_ess_balance_last_stable_at = float(now)
        svc._victron_ess_balance_last_stable_profile_key = str(
            metrics.get("battery_discharge_balance_victron_bias_learning_profile_key", "") or ""
        ).strip()

    @staticmethod
    def _victron_ess_balance_can_refresh_stable_tuning(
        confidence: float | None,
        stability: float | None,
        sample_count: int,
        overshoot_count: int,
    ) -> bool:
        return (
            _UpdateCycleVictronEssBalanceSafetyMixin._victron_ess_balance_has_minimum_confidence(confidence)
            and _UpdateCycleVictronEssBalanceSafetyMixin._victron_ess_balance_has_minimum_stability(stability)
            and sample_count >= 2
            and overshoot_count <= 0
        )

    @staticmethod
    def _victron_ess_balance_has_minimum_confidence(confidence: float | None) -> bool:
        return confidence is not None and confidence >= 0.8

    @staticmethod
    def _victron_ess_balance_has_minimum_stability(stability: float | None) -> bool:
        return stability is not None and stability >= 0.8

    def _victron_ess_balance_ensure_conservative_tuning(self, svc: Any) -> None:
        if not getattr(svc, "_victron_ess_balance_conservative_tuning", None):
            svc._victron_ess_balance_conservative_tuning = self._victron_ess_balance_current_tuning_snapshot(svc)

    def _victron_ess_balance_should_rollback_stable_tuning(
        self,
        svc: Any,
        metrics: dict[str, Any],
        now: float,
    ) -> bool:
        if not bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_rollback_enabled", True)):
            return False
        observe_until = self._optional_float(getattr(svc, "_victron_ess_balance_auto_apply_observe_until", None))
        if not self._victron_ess_balance_observation_window_active(now, observe_until):
            return False
        if self._victron_ess_balance_has_immediate_rollback_signal(metrics):
            return True
        stability = self._optional_float(metrics.get("battery_discharge_balance_victron_bias_stability_score"))
        rollback_min_stability = self._victron_ess_balance_rollback_min_stability_score(svc)
        return bool(stability is not None and stability < rollback_min_stability)

    @staticmethod
    def _victron_ess_balance_rollback_min_stability_score(svc: Any) -> float:
        return max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_rollback_min_stability_score", 0.45) or 0.45),
        )

    @staticmethod
    def _victron_ess_balance_observation_window_active(now: float, observe_until: float | None) -> bool:
        return observe_until is not None and float(now) < float(observe_until)

    @staticmethod
    def _victron_ess_balance_has_immediate_rollback_signal(metrics: dict[str, Any]) -> bool:
        return any(
            bool(metrics.get(key))
            for key in (
                "battery_discharge_balance_victron_bias_oscillation_lockout_active",
                "battery_discharge_balance_victron_bias_overshoot_active",
                "battery_discharge_balance_victron_bias_overshoot_cooldown_active",
            )
        )

    def _maybe_restore_victron_ess_balance_stable_tuning(
        self,
        svc: Any,
        metrics: dict[str, Any],
        reason: str,
    ) -> bool:
        metrics["battery_discharge_balance_victron_bias_rollback_enabled"] = int(
            bool(getattr(svc, "auto_battery_discharge_balance_victron_bias_rollback_enabled", True))
        )
        stable, safe_state_reason = self._victron_ess_balance_restore_target(svc, reason)
        if stable is None:
            metrics["battery_discharge_balance_victron_bias_rollback_reason"] = "no_stable_tuning"
            return False
        svc._victron_ess_balance_safe_state_active = True
        svc._victron_ess_balance_safe_state_reason = safe_state_reason
        self._apply_victron_ess_balance_restored_tuning(svc, stable)
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

    def _victron_ess_balance_restore_target(
        self,
        svc: Any,
        reason: str,
    ) -> tuple[dict[str, Any] | None, str]:
        stable = getattr(svc, "_victron_ess_balance_last_stable_tuning", None)
        if isinstance(stable, dict) and stable:
            return stable, str(reason)
        conservative = getattr(svc, "_victron_ess_balance_conservative_tuning", None)
        if isinstance(conservative, dict) and conservative:
            return conservative, "conservative_fallback"
        return None, ""

    def _apply_victron_ess_balance_restored_tuning(self, svc: Any, stable: dict[str, Any]) -> None:
        self._apply_victron_ess_balance_restored_pid_terms(svc, stable)
        self._apply_victron_ess_balance_restored_limits(svc, stable)
        activation_mode = self._victron_ess_balance_restored_activation_mode(svc, stable)
        if activation_mode:
            svc.auto_battery_discharge_balance_victron_bias_activation_mode = activation_mode

    @staticmethod
    def _apply_victron_ess_balance_restored_pid_terms(svc: Any, stable: dict[str, Any]) -> None:
        svc.auto_battery_discharge_balance_victron_bias_kp = float(stable.get("kp", 0.0) or 0.0)
        svc.auto_battery_discharge_balance_victron_bias_ki = float(stable.get("ki", 0.0) or 0.0)
        svc.auto_battery_discharge_balance_victron_bias_kd = float(stable.get("kd", 0.0) or 0.0)

    @staticmethod
    def _apply_victron_ess_balance_restored_limits(svc: Any, stable: dict[str, Any]) -> None:
        svc.auto_battery_discharge_balance_victron_bias_deadband_watts = float(stable.get("deadband_watts", 0.0) or 0.0)
        svc.auto_battery_discharge_balance_victron_bias_max_abs_watts = float(stable.get("max_abs_watts", 0.0) or 0.0)
        svc.auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second = float(
            stable.get("ramp_rate_watts_per_second", 0.0) or 0.0
        )

    def _victron_ess_balance_restored_activation_mode(self, svc: Any, stable: dict[str, Any]) -> str:
        return str(stable.get("activation_mode", self._victron_ess_balance_activation_mode(svc)) or "always").strip()

    @staticmethod
    def _victron_ess_balance_ev_power_w(svc: Any) -> float | None:
        direct = _UpdateCycleVictronEssBalanceSafetyMixin._victron_ess_balance_direct_ev_power_w(svc)
        if direct is not None:
            return direct
        learned_charge_power = getattr(svc, "learned_charge_power_watts", None)
        if isinstance(learned_charge_power, (int, float)) and int(getattr(svc, "virtual_startstop", 0) or 0):
            return float(learned_charge_power)
        return None

    @staticmethod
    def _victron_ess_balance_direct_ev_power_w(svc: Any) -> float | None:
        for attr_name in ("_last_charger_state_power_w", "_charger_estimated_power_w", "_last_power", "ac_power"):
            value = getattr(svc, attr_name, None)
            if isinstance(value, (int, float)):
                return float(value)
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
