# SPDX-License-Identifier: GPL-3.0-or-later
"""Internal Auto-mode decision workflow helpers for the Venus EV charger service.

The Auto controller keeps the policy readable by splitting the decision tree
into many small helper methods. The high-level behavior is:
- gather fresh PV, grid, and battery inputs
- smooth the relevant values
- check hard safety gates first
- evaluate start/stop conditions
- return the desired relay state plus a diagnostic health reason
"""

from __future__ import annotations

import logging
import time
from typing import Any, Mapping, cast

from venus_evcharger.energy import derive_energy_forecast, summarize_energy_learning_profiles
from venus_evcharger.core.common import confirmed_relay_state_max_age_seconds as _confirmed_relay_state_max_age_seconds
from venus_evcharger.core.contracts import cutover_confirmed_off
from venus_evcharger.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin

AutoDecision = bool | object



class _AutoDecisionGatesMixin(_ComposableControllerMixin):
    def _pending_stop_or_running(
        self,
        now: float,
        stop_reason: str,
        cached_inputs: bool,
        running_reason: str,
        delay_seconds: float | None = None,
        stop_key: str | None = None,
    ) -> bool:
        """Arm a delayed stop or keep running with the supplied health reason."""
        decision = self._arm_or_fire_stop(
            now,
            stop_reason,
            cached_inputs,
            delay_seconds=delay_seconds,
            stop_key=stop_key,
        )
        if decision is self._NO_DECISION:
            return cast(bool, self._set_health_result(running_reason, cached_inputs, True))
        return False

    def _minimum_runtime_elapsed(self, now: float) -> bool:
        """Return True when the current relay-on period may be stopped."""
        svc = self.service
        return svc.relay_last_changed_at is None or (now - svc.relay_last_changed_at) >= svc.auto_min_runtime_seconds

    def _minimum_offtime_elapsed(self, now: float) -> bool:
        """Return True when the relay may be started again."""
        svc = self.service
        return (
            getattr(svc, "_ignore_min_offtime_once", False)
            or svc.relay_last_off_at is None
            or (now - svc.relay_last_off_at) >= svc.auto_min_offtime_seconds
        )

    def _grid_recently_read(self, grid_power: float | None, now: float) -> bool:
        """Return True when the grid reading is still fresh enough for Auto decisions."""
        svc = self.service
        grid_last_read_at = getattr(svc, "_last_grid_at", None)
        grid_missing_stop_seconds = float(getattr(svc, "auto_grid_missing_stop_seconds", 60.0))
        if grid_last_read_at is None:
            return grid_power is not None
        return (now - float(grid_last_read_at)) <= grid_missing_stop_seconds

    def _handle_non_auto_mode(self, relay_on: bool) -> bool:
        """Leave relay state untouched outside of Auto-like modes."""
        svc = self.service
        self._reset_auto_state()
        svc._auto_mode_cutover_pending = False
        svc._ignore_min_offtime_once = False
        self._set_auto_state("idle")
        svc._save_runtime_state()
        return relay_on

    def _handle_disabled_mode(self, cached_inputs: bool) -> bool:
        """Force relay off when Auto has been disabled by the GUI."""
        svc = self.service
        self._reset_auto_state()
        svc._auto_mode_cutover_pending = False
        svc._ignore_min_offtime_once = False
        self.set_health("disabled", cached_inputs, relay_intent=False)
        svc._save_runtime_state()
        return False

    def _normalized_battery_soc(self, battery_soc: float | int | None, now: float) -> float | None:
        """Return a validated battery SOC reading or None when unavailable/invalid."""
        svc = self.service
        if battery_soc is None:
            return None
        normalized_battery_soc = float(battery_soc)
        if 0.0 <= normalized_battery_soc <= 100.0:
            svc._last_battery_allow_warning = None
            return normalized_battery_soc
        warning_throttled = getattr(svc, "_warning_throttled", None)
        if callable(warning_throttled):
            warning_throttled(
                "battery-soc-invalid",
                max(1.0, float(getattr(svc, "auto_battery_scan_interval_seconds", 60.0) or 60.0)),
                "Auto mode ignored out-of-range battery SOC %s",
                normalized_battery_soc,
            )
        return None

    def _allowed_missing_battery_soc(
        self,
        relay_on: bool,
        now: float,
        cached_inputs: bool,
    ) -> tuple[float, AutoDecision]:
        """Return the fallback decision when missing battery SOC is explicitly allowed."""
        svc = self.service
        if (
            svc._last_battery_allow_warning is None
            or (now - svc._last_battery_allow_warning) > svc.auto_battery_scan_interval_seconds
        ):
            logging.warning("Auto mode: battery SOC missing, allowing Auto based on resume SOC.")
            svc._last_battery_allow_warning = now
        self.set_health("battery-soc-missing-allowed", cached_inputs, relay_intent=relay_on)
        return float(self._auto_policy().resume_soc), self._NO_DECISION

    def _blocked_missing_battery_soc(
        self,
        relay_on: bool,
        cached_inputs: bool,
    ) -> tuple[None, AutoDecision]:
        """Return the terminal decision when missing battery SOC must block Auto mode."""
        self._reset_auto_state()
        self.set_health("battery-soc-missing", cached_inputs, relay_intent=relay_on)
        return None, relay_on

    def _resolve_battery_soc(
        self,
        battery_soc: float | int | None,
        relay_on: bool,
        now: float,
        cached_inputs: bool,
    ) -> tuple[float | None, AutoDecision]:
        """Normalize battery SOC or return a terminal decision when it is unavailable."""
        svc = self.service
        normalized_battery_soc = self._normalized_battery_soc(battery_soc, now)
        if normalized_battery_soc is not None:
            return normalized_battery_soc, self._NO_DECISION
        if bool(getattr(svc, "auto_allow_without_battery_soc", False)):
            return self._allowed_missing_battery_soc(relay_on, now, cached_inputs)
        return self._blocked_missing_battery_soc(relay_on, cached_inputs)

    def _handle_cutover_pending(self, relay_on: bool, cached_inputs: bool) -> AutoDecision:
        """Honor the Manual -> Auto clean-cutover until the relay is confirmed off."""
        svc = self.service
        if not getattr(svc, "_auto_mode_cutover_pending", False):
            return cast(AutoDecision, self._NO_DECISION)

        now = self._learning_policy_now()
        self._reset_auto_state()
        if not self._cutover_relay_off_confirmed(relay_on, now):
            self.set_health("mode-transition", cached_inputs, relay_intent=False)
            return False

        self._complete_cutover_pending()
        return cast(AutoDecision, self._NO_DECISION)

    def _confirmed_cutover_pm_status(self) -> tuple[dict[str, Any] | None, float | None]:
        """Return the best confirmed Shelly PM status available for cutover checks."""
        svc = self.service
        confirmed_pm_status = getattr(svc, "_last_confirmed_pm_status", None)
        confirmed_pm_status_at = getattr(svc, "_last_confirmed_pm_status_at", None)
        if confirmed_pm_status is not None:
            return confirmed_pm_status, confirmed_pm_status_at
        if bool(getattr(svc, "_last_pm_status_confirmed", False)):
            return getattr(svc, "_last_pm_status", None), getattr(svc, "_last_pm_status_at", None)
        return None, None

    def _cutover_confirmed_sample_fresh(self, confirmed_pm_status_at: float | None, now: float) -> bool:
        """Return True when the confirmed relay sample is fresh enough for cutover release."""
        if confirmed_pm_status_at is None:
            return False
        return (float(now) - float(confirmed_pm_status_at)) <= float(
            _confirmed_relay_state_max_age_seconds(self.service)
        )

    def _cutover_confirmed_after_request(self, confirmed_pm_status_at: float | None) -> bool:
        """Return True when the confirmed relay sample happened after the cutover request."""
        relay_sync_requested_at = getattr(self.service, "_relay_sync_requested_at", None)
        if relay_sync_requested_at is None:
            return True
        if confirmed_pm_status_at is None:
            return False
        return float(confirmed_pm_status_at) >= float(relay_sync_requested_at)

    def _cutover_relay_off_confirmed(self, relay_on: bool, now: float) -> bool:
        """Return True when the relay-off cutover has been confirmed by Shelly."""
        svc = self.service
        pending_state, _ = svc._peek_pending_relay_command()
        if pending_state is not None or relay_on:
            return False
        confirmed_pm_status, confirmed_pm_status_at = self._confirmed_cutover_pm_status()
        if not (isinstance(confirmed_pm_status, dict) and "output" in confirmed_pm_status):
            return False
        return cutover_confirmed_off(
            relay_on=relay_on,
            pending_state=pending_state,
            confirmed_output=confirmed_pm_status.get("output"),
            confirmed_at=confirmed_pm_status_at,
            requested_at=getattr(svc, "_relay_sync_requested_at", None),
            now=now,
            max_age_seconds=_confirmed_relay_state_max_age_seconds(svc),
            future_tolerance_seconds=1.0,
        )

    def _complete_cutover_pending(self) -> None:
        """Finish one confirmed Manual -> Auto cutover."""
        svc = self.service
        svc._auto_mode_cutover_pending = False
        svc._ignore_min_offtime_once = True
        svc._save_runtime_state()

    @staticmethod
    def _stop_tracking_needs_reset(
        stop_since: float | None,
        current_stop_key: str | None,
        active_stop_key: str,
    ) -> bool:
        """Return whether the delayed-stop timer must restart for a new reason."""
        return stop_since is None or (current_stop_key is not None and current_stop_key != active_stop_key)

    @staticmethod
    def _stop_delay_elapsed(stop_since: float, now: float, delay_seconds: float) -> bool:
        """Return whether the delayed-stop timer already elapsed."""
        return (now - stop_since) >= delay_seconds

    @staticmethod
    def _active_stop_key(reason: str, stop_key: str | None) -> str:
        """Return the effective key used to track one delayed stop condition."""
        return reason if stop_key is None else stop_key

    @staticmethod
    def _effective_stop_delay(default_delay: float, delay_seconds: float | None) -> float:
        """Return the effective delayed-stop timeout for one stop reason."""
        return default_delay if delay_seconds is None else float(delay_seconds)

    def _reset_stop_tracking(self, now: float, active_stop_key: str) -> bool:
        """Start or restart delayed-stop tracking for the supplied stop key."""
        svc = self.service
        current_stop_key = getattr(svc, "auto_stop_condition_reason", None)
        if not self._stop_tracking_needs_reset(svc.auto_stop_condition_since, current_stop_key, active_stop_key):
            return False
        svc.auto_stop_condition_since = now
        svc.auto_stop_condition_reason = active_stop_key
        return True

    def _ensure_stop_tracking_reason(self, active_stop_key: str) -> None:
        """Ensure delayed-stop tracking keeps its stop reason once a timer is active."""
        svc = self.service
        if getattr(svc, "auto_stop_condition_reason", None) is None:
            svc.auto_stop_condition_reason = active_stop_key

    def _arm_or_fire_stop(
        self,
        now: float,
        reason: str,
        cached_inputs: bool,
        delay_seconds: float | None = None,
        stop_key: str | None = None,
    ) -> AutoDecision:
        """Track delayed stop conditions and stop once the configured delay elapsed."""
        svc = self.service
        active_stop_key = self._active_stop_key(reason, stop_key)
        if self._reset_stop_tracking(now, active_stop_key):
            return cast(AutoDecision, self._NO_DECISION)
        self._ensure_stop_tracking_reason(active_stop_key)
        effective_delay = self._effective_stop_delay(svc.auto_stop_delay_seconds, delay_seconds)
        assert svc.auto_stop_condition_since is not None
        if not self._stop_delay_elapsed(svc.auto_stop_condition_since, now, effective_delay):
            return cast(AutoDecision, self._NO_DECISION)
        self.set_health(reason, cached_inputs, relay_intent=False)
        return False

    def _handle_grid_missing(self, relay_on: bool, now: float, cached_inputs: bool) -> bool:
        """Fail safe when no fresh grid reading is available."""
        svc = self.service
        self._clear_auto_start_tracking(clear_samples=True)
        svc._grid_recovery_required = True
        svc._grid_recovery_since = None
        if not relay_on:
            return cast(bool, self._idle_result_with_health("grid-missing", cached_inputs))

        if not self._minimum_runtime_elapsed(now):
            return cast(bool, self._running_result_with_health("grid-missing", cached_inputs))
        return self._pending_stop_or_running(now, "grid-missing", cached_inputs, "grid-missing")

    def _handle_grid_recovery_start_gate(self, relay_on: bool, now: float, cached_inputs: bool) -> AutoDecision:
        """Require a short fresh-grid window before Auto may start after grid loss."""
        svc = self.service
        if not self._grid_recovery_gate_active(svc):
            return cast(AutoDecision, self._NO_DECISION)
        recovery_seconds = float(self._auto_policy().grid_recovery_start_seconds)
        if self._grid_recovery_completes_immediately(now, recovery_seconds):
            return cast(AutoDecision, self._NO_DECISION)
        if self._grid_recovery_waiting(now, relay_on, cached_inputs, recovery_seconds):
            return self._grid_recovery_wait_decision(relay_on, cached_inputs)
        svc._grid_recovery_required = False
        return cast(AutoDecision, self._NO_DECISION)

    @staticmethod
    def _grid_recovery_gate_active(svc: Any) -> bool:
        """Return whether the fresh-grid recovery gate is configured and active."""
        return (
            hasattr(svc, "_grid_recovery_since")
            and hasattr(svc, "_grid_recovery_required")
            and bool(getattr(svc, "_grid_recovery_required", False))
        )

    def _grid_recovery_completes_immediately(self, now: float, recovery_seconds: float) -> bool:
        """Return True when the recovery gate may be cleared immediately."""
        if recovery_seconds > 0:
            return False
        svc = self.service
        svc._grid_recovery_since = now
        svc._grid_recovery_required = False
        return True

    def _grid_recovery_waiting(
        self,
        now: float,
        relay_on: bool,
        cached_inputs: bool,
        recovery_seconds: float,
    ) -> bool:
        """Return whether Auto must keep waiting for the fresh-grid recovery window."""
        svc = self.service
        grid_recovery_since = getattr(svc, "_grid_recovery_since", None)
        if grid_recovery_since is None:
            svc._grid_recovery_since = now
            return True
        return (now - float(grid_recovery_since)) < recovery_seconds

    def _grid_recovery_wait_decision(self, relay_on: bool, cached_inputs: bool) -> AutoDecision:
        """Return the relay decision while the fresh-grid recovery window is still open."""
        if relay_on:
            return cast(AutoDecision, self._NO_DECISION)
        self._clear_auto_start_tracking()
        return cast(AutoDecision, self._set_health_result("waiting-grid-recovery", cached_inputs, False))

    def _policy_stop_reason(self, battery_soc: float, grid_power: float | None) -> str | None:
        """Return a stop reason caused by SOC or grid import thresholds."""
        policy = self._auto_policy()
        if battery_soc < policy.min_soc:
            return "auto-stop"
        if grid_power is not None and float(grid_power) >= policy.stop_grid_import_watts:
            return "auto-stop"
        return None

    def _known_missing_input_stop_reason(
        self,
        battery_soc: float,
        grid_power: float | None,
        daytime_window_open: bool,
    ) -> str | None:
        """Return a concrete stop reason when inputs are missing but stopping is still warranted."""
        svc = self.service
        if getattr(svc, "auto_night_lock_stop", False) and not daytime_window_open:
            return "night-lock"
        return self._policy_stop_reason(battery_soc, grid_power)

    def _handle_missing_inputs(
        self,
        relay_on: bool,
        battery_soc: float,
        grid_power: float | None,
        now: float,
        cached_inputs: bool,
    ) -> bool:
        """Preserve safe behavior when PV or grid inputs are incomplete."""
        svc = self.service
        self._clear_auto_start_tracking(clear_samples=True)

        if not relay_on:
            return cast(bool, self._idle_result_with_health("inputs-missing", cached_inputs))

        daytime_window_open = svc._is_within_auto_daytime_window()
        stop_reason = self._known_missing_input_stop_reason(battery_soc, grid_power, daytime_window_open)
        if not self._minimum_runtime_elapsed(now) or stop_reason is None:
            return cast(bool, self._running_result_with_health("inputs-missing", cached_inputs))
        return self._pending_stop_or_running(now, stop_reason, cached_inputs, "inputs-missing")

    def _update_average_metrics(
        self,
        now: float,
        pv_power: float,
        grid_power: float,
        battery_soc: float,
        relay_on: bool,
    ) -> tuple[float | None, float | None]:
        """Update rolling Auto metrics and return averaged surplus/grid values."""
        svc = self.service
        surplus_power = svc._get_available_surplus_watts(pv_power, grid_power)
        svc._add_auto_sample(now, surplus_power, float(grid_power))
        avg_surplus_power = svc._average_auto_metric(1)
        avg_grid_power = svc._average_auto_metric(2)
        if avg_surplus_power is None or avg_grid_power is None:
            return None, None

        threshold_metrics = self._surplus_threshold_metrics(battery_soc)
        smoothing_metrics = self._smoothing_metrics(relay_on, float(avg_surplus_power), float(avg_grid_power))
        smoothed_surplus = self._required_float(smoothing_metrics["surplus"])
        smoothed_grid = self._required_float(smoothing_metrics["grid"])
        battery_metrics = self._battery_adjusted_surplus_metrics(smoothed_surplus)
        learned_metrics = self._learned_threshold_metrics(now)
        svc._last_auto_metrics = self._auto_metrics_snapshot(
            avg_surplus_power=float(avg_surplus_power),
            avg_grid_power=float(avg_grid_power),
            battery_soc=float(battery_soc),
            threshold_metrics=threshold_metrics,
            smoothing_metrics=smoothing_metrics,
            battery_metrics=battery_metrics,
            learned_metrics=learned_metrics,
        )
        return self._required_float(battery_metrics["decision_surplus"]), smoothed_grid

    def _surplus_threshold_metrics(self, battery_soc: float) -> dict[str, float | str | None]:
        start_surplus_watts, stop_surplus_watts, threshold_profile = self._surplus_thresholds_for_soc(battery_soc)
        return {
            "start_threshold": float(start_surplus_watts),
            "stop_threshold": float(stop_surplus_watts),
            "profile": threshold_profile,
        }

    def _smoothing_metrics(
        self,
        relay_on: bool,
        avg_surplus_power: float,
        avg_grid_power: float,
    ) -> dict[str, float | str | None]:
        adaptive_alpha, adaptive_alpha_stage, surplus_volatility = self._adaptive_stop_alpha()
        decision_surplus_power, decision_grid_power = self._smoothed_decision_metrics(
            relay_on,
            avg_surplus_power,
            avg_grid_power,
            adaptive_alpha,
        )
        return {
            "surplus": float(decision_surplus_power),
            "grid": float(decision_grid_power),
            "stop_alpha": float(adaptive_alpha),
            "stop_alpha_stage": adaptive_alpha_stage,
            "surplus_volatility": float(surplus_volatility) if surplus_volatility is not None else None,
        }

    def _battery_adjusted_surplus_metrics(self, decision_surplus_power: float) -> dict[str, float | int | str | None]:
        battery_activity = self._combined_battery_activity_context()
        surplus_penalty_w = self._non_negative_optional_float(battery_activity.get("surplus_penalty_w")) or 0.0
        near_term_adjustment_w = self._near_term_grid_adjustment(battery_activity)
        learning_profile_count = battery_activity.get("learning_profile_count")
        normalized_learning_profile_count = int(learning_profile_count) if isinstance(learning_profile_count, int) else 0
        return {
            "decision_surplus": float(decision_surplus_power - surplus_penalty_w + near_term_adjustment_w),
            "raw_decision_surplus": float(decision_surplus_power),
            "surplus_penalty_w": surplus_penalty_w,
            "near_term_adjustment_w": near_term_adjustment_w,
            "learning_profile_count": normalized_learning_profile_count,
            **battery_activity,
        }

    def _learned_threshold_metrics(self, now: float) -> dict[str, float | str | None]:
        learned_charge_power = self._active_learned_charge_power(now)
        return {
            "learned_charge_power": learned_charge_power,
            "learned_charge_power_state": self._current_learned_charge_power_state(now),
            "threshold_scale": float(self._learned_charge_power_scale(now)),
            "threshold_mode": "adaptive" if learned_charge_power is not None else "static",
        }

    def _auto_metrics_snapshot(
        self,
        *,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
        threshold_metrics: Mapping[str, float | str | None],
        smoothing_metrics: Mapping[str, float | str | None],
        battery_metrics: Mapping[str, float | int | str | None],
        learned_metrics: Mapping[str, float | str | None],
    ) -> dict[str, float | int | str | None]:
        decision_surplus = self._required_float(battery_metrics["decision_surplus"])
        raw_decision_surplus = self._required_float(battery_metrics["raw_decision_surplus"])
        grid = self._required_float(smoothing_metrics["grid"])
        start_threshold = self._required_float(threshold_metrics["start_threshold"])
        stop_threshold = self._required_float(threshold_metrics["stop_threshold"])
        threshold_scale = self._required_float(learned_metrics["threshold_scale"])
        stop_alpha = self._required_float(smoothing_metrics["stop_alpha"])
        return {
            "surplus": decision_surplus,
            "grid": grid,
            "raw_surplus": avg_surplus_power,
            "decision_surplus_before_battery_penalty": raw_decision_surplus,
            "raw_grid": avg_grid_power,
            "soc": battery_soc,
            "profile": threshold_metrics["profile"],
            "start_threshold": start_threshold,
            "stop_threshold": stop_threshold,
            "learned_charge_power": learned_metrics["learned_charge_power"],
            "learned_charge_power_state": learned_metrics["learned_charge_power_state"],
            "threshold_scale": threshold_scale,
            "threshold_mode": learned_metrics["threshold_mode"],
            "stop_alpha": stop_alpha,
            "stop_alpha_stage": smoothing_metrics["stop_alpha_stage"],
            "surplus_volatility": smoothing_metrics["surplus_volatility"],
            "battery_surplus_penalty_w": battery_metrics["surplus_penalty_w"],
            "battery_near_term_adjustment_w": battery_metrics["near_term_adjustment_w"],
            "battery_support_mode": battery_metrics["mode"],
            "battery_charge_power_w": battery_metrics["charge_power_w"],
            "battery_discharge_power_w": battery_metrics["discharge_power_w"],
            "battery_charge_activity_ratio": battery_metrics["charge_activity_ratio"],
            "battery_discharge_activity_ratio": battery_metrics["discharge_activity_ratio"],
            "battery_learning_profile_count": battery_metrics["learning_profile_count"],
            "battery_observed_max_charge_power_w": battery_metrics["observed_max_charge_power_w"],
            "battery_observed_max_discharge_power_w": battery_metrics["observed_max_discharge_power_w"],
            "battery_typical_response_delay_seconds": battery_metrics["typical_response_delay_seconds"],
            "battery_support_bias": battery_metrics["support_bias"],
            "battery_day_support_bias": battery_metrics["day_support_bias"],
            "battery_night_support_bias": battery_metrics["night_support_bias"],
            "battery_import_support_bias": battery_metrics["import_support_bias"],
            "battery_export_bias": battery_metrics["export_bias"],
            "battery_battery_first_export_bias": battery_metrics["battery_first_export_bias"],
            "battery_power_smoothing_ratio": battery_metrics["power_smoothing_ratio"],
            "battery_reserve_band_floor_soc": battery_metrics["reserve_band_floor_soc"],
            "battery_reserve_band_ceiling_soc": battery_metrics["reserve_band_ceiling_soc"],
            "battery_reserve_band_width_soc": battery_metrics["reserve_band_width_soc"],
            "battery_headroom_charge_w": battery_metrics["battery_headroom_charge_w"],
            "battery_headroom_discharge_w": battery_metrics["battery_headroom_discharge_w"],
            "expected_near_term_export_w": battery_metrics["expected_near_term_export_w"],
            "expected_near_term_import_w": battery_metrics["expected_near_term_import_w"],
        }

    def _combined_battery_activity_context(self) -> dict[str, float | int | str | None]:
        """Return a conservative battery activity picture used to de-bias surplus decisions."""
        cluster, sources, profiles = self._battery_activity_inputs()
        learning_summary = summarize_energy_learning_profiles(profiles)
        if sources:
            charge_penalty, discharge_penalty, max_charge_ratio, max_discharge_ratio = self._source_activity_penalties(
                sources,
                profiles,
            )
        else:
            charge_penalty, discharge_penalty, max_charge_ratio, max_discharge_ratio = self._cluster_activity_penalties(
                cluster,
                learning_summary,
            )

        behavior = self._battery_learning_behavior(learning_summary)
        forecast = derive_energy_forecast(cluster, learning_summary)
        charge_penalty *= self._battery_penalty_multiplier(
            direction="charge",
            response_delay_seconds=behavior["response_delay_seconds"],
            support_bias=behavior["support_bias"],
            import_support_bias=behavior["import_support_bias"],
            export_bias=behavior["export_bias"],
        )
        discharge_penalty *= self._battery_penalty_multiplier(
            direction="discharge",
            response_delay_seconds=behavior["response_delay_seconds"],
            support_bias=behavior["support_bias"],
            import_support_bias=behavior["import_support_bias"],
            export_bias=behavior["export_bias"],
        )
        return {
            "surplus_penalty_w": float(charge_penalty + discharge_penalty),
            "charge_power_w": charge_penalty if charge_penalty > 0.0 else None,
            "discharge_power_w": discharge_penalty if discharge_penalty > 0.0 else None,
            "charge_activity_ratio": max_charge_ratio,
            "discharge_activity_ratio": max_discharge_ratio,
            "learning_profile_count": int(learning_summary.get("profile_count", 0) or 0),
            "observed_max_charge_power_w": self._non_negative_optional_float(
                learning_summary.get("observed_max_charge_power_w")
            ),
            "observed_max_discharge_power_w": self._non_negative_optional_float(
                learning_summary.get("observed_max_discharge_power_w")
            ),
            "typical_response_delay_seconds": behavior["response_delay_seconds"],
            "support_bias": behavior["support_bias"],
            "day_support_bias": behavior["day_support_bias"],
            "night_support_bias": behavior["night_support_bias"],
            "import_support_bias": behavior["import_support_bias"],
            "export_bias": behavior["export_bias"],
            "battery_first_export_bias": behavior["battery_first_export_bias"],
            "power_smoothing_ratio": behavior["power_smoothing_ratio"],
            "reserve_band_floor_soc": behavior["reserve_band_floor_soc"],
            "reserve_band_ceiling_soc": behavior["reserve_band_ceiling_soc"],
            "reserve_band_width_soc": behavior["reserve_band_width_soc"],
            "battery_headroom_charge_w": self._cluster_or_forecast_metric(
                cluster,
                forecast,
                "battery_headroom_charge_w",
            ),
            "battery_headroom_discharge_w": self._cluster_or_forecast_metric(
                cluster,
                forecast,
                "battery_headroom_discharge_w",
            ),
            "expected_near_term_export_w": self._cluster_or_forecast_metric(
                cluster,
                forecast,
                "expected_near_term_export_w",
            ),
            "expected_near_term_import_w": self._cluster_or_forecast_metric(
                cluster,
                forecast,
                "expected_near_term_import_w",
            ),
            "mode": self._battery_activity_mode(charge_penalty, discharge_penalty),
        }

    def _battery_activity_inputs(self) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        svc = self.service
        cluster = self._normalized_mapping(getattr(svc, "_last_energy_cluster", {}))
        raw_sources = cluster.get("battery_sources", [])
        sources = self._normalized_mapping_list(raw_sources)
        profiles = self._normalized_mapping(getattr(svc, "_last_energy_learning_profiles", {}))
        return cluster, sources, profiles

    @staticmethod
    def _normalized_mapping(raw_value: object) -> dict[str, Any]:
        return raw_value if isinstance(raw_value, dict) else {}

    def _normalized_mapping_list(self, raw_value: object) -> list[dict[str, Any]]:
        if not isinstance(raw_value, list):
            return []
        return [value for value in raw_value if isinstance(value, dict)]

    def _source_activity_penalties(
        self,
        sources: list[dict[str, Any]],
        profiles: dict[str, Any],
    ) -> tuple[float, float, float | None, float | None]:
        charge_penalty = 0.0
        discharge_penalty = 0.0
        max_charge_ratio: float | None = None
        max_discharge_ratio: float | None = None
        for source in sources:
            source_charge_penalty, source_discharge_penalty, charge_ratio, discharge_ratio = (
                self._source_activity_penalty(source, profiles)
            )
            charge_penalty += source_charge_penalty
            discharge_penalty += source_discharge_penalty
            max_charge_ratio = self._max_optional_ratio(max_charge_ratio, charge_ratio)
            max_discharge_ratio = self._max_optional_ratio(max_discharge_ratio, discharge_ratio)
        return charge_penalty, discharge_penalty, max_charge_ratio, max_discharge_ratio

    def _source_activity_penalty(
        self,
        source: dict[str, Any],
        profiles: dict[str, Any],
    ) -> tuple[float, float, float | None, float | None]:
        source_id = str(source.get("source_id", "")).strip()
        profile = profiles.get(source_id, {})
        charge_active, charge_ratio = self._active_battery_power(
            self._non_negative_optional_float(source.get("charge_power_w")),
            self._learning_observed_value(profile, "observed_max_charge_power_w"),
        )
        discharge_active, discharge_ratio = self._active_battery_power(
            self._non_negative_optional_float(source.get("discharge_power_w")),
            self._learning_observed_value(profile, "observed_max_discharge_power_w"),
        )
        return (
            0.0 if charge_active is None else charge_active,
            0.0 if discharge_active is None else discharge_active,
            charge_ratio,
            discharge_ratio,
        )

    def _cluster_activity_penalties(
        self,
        cluster: dict[str, Any],
        learning_summary: dict[str, float | int | None],
    ) -> tuple[float, float, float | None, float | None]:
        charge_active, charge_ratio = self._active_battery_power(
            self._non_negative_optional_float(cluster.get("battery_combined_charge_power_w")),
            self._non_negative_optional_float(learning_summary.get("observed_max_charge_power_w")),
        )
        discharge_active, discharge_ratio = self._active_battery_power(
            self._non_negative_optional_float(cluster.get("battery_combined_discharge_power_w")),
            self._non_negative_optional_float(learning_summary.get("observed_max_discharge_power_w")),
        )
        return (
            0.0 if charge_active is None else charge_active,
            0.0 if discharge_active is None else discharge_active,
            charge_ratio,
            discharge_ratio,
        )

    def _battery_learning_behavior(
        self,
        learning_summary: dict[str, float | int | None],
    ) -> dict[str, float | None]:
        day_support_bias = self._bounded_optional_float(learning_summary.get("day_support_bias"))
        night_support_bias = self._bounded_optional_float(learning_summary.get("night_support_bias"))
        return {
            "response_delay_seconds": self._non_negative_optional_float(
                learning_summary.get("typical_response_delay_seconds")
            ),
            "support_bias": self._support_bias_for_current_period(
                self._bounded_optional_float(learning_summary.get("support_bias")),
                day_support_bias,
                night_support_bias,
            ),
            "day_support_bias": day_support_bias,
            "night_support_bias": night_support_bias,
            "import_support_bias": self._bounded_optional_float(learning_summary.get("import_support_bias")),
            "export_bias": self._bounded_optional_float(learning_summary.get("export_bias")),
            "battery_first_export_bias": self._bounded_optional_float(learning_summary.get("battery_first_export_bias")),
            "power_smoothing_ratio": self._non_negative_optional_float(learning_summary.get("power_smoothing_ratio")),
            "reserve_band_floor_soc": self._non_negative_optional_float(learning_summary.get("reserve_band_floor_soc")),
            "reserve_band_ceiling_soc": self._non_negative_optional_float(
                learning_summary.get("reserve_band_ceiling_soc")
            ),
            "reserve_band_width_soc": self._non_negative_optional_float(learning_summary.get("reserve_band_width_soc")),
        }

    def _support_bias_for_current_period(
        self,
        default_bias: float | None,
        day_bias: float | None,
        night_bias: float | None,
    ) -> float | None:
        current_period = self._current_learning_period()
        if current_period == "day":
            return day_bias if day_bias is not None else default_bias
        if current_period == "night":
            return night_bias if night_bias is not None else default_bias
        return default_bias

    def _current_learning_period(self) -> str | None:
        service_now = getattr(self.service, "_time_now", None)
        raw_now = service_now() if callable(service_now) else None
        if not isinstance(raw_now, (int, float)):
            return None
        hour = time.localtime(float(raw_now)).tm_hour
        return "day" if 6 <= hour < 22 else "night"

    def _cluster_or_forecast_metric(
        self,
        cluster: dict[str, Any],
        forecast: Mapping[str, object],
        key: str,
    ) -> float | None:
        if key in cluster:
            return self._non_negative_optional_float(cluster.get(key))
        return self._non_negative_optional_float(forecast.get(key))

    @staticmethod
    def _battery_activity_mode(charge_penalty: float, discharge_penalty: float) -> str:
        if charge_penalty > 0.0 and discharge_penalty > 0.0:
            return "mixed"
        if discharge_penalty > 0.0:
            return "discharging"
        if charge_penalty > 0.0:
            return "charging"
        return "idle"

    @classmethod
    def _near_term_grid_adjustment(cls, battery_activity: dict[str, float | int | str | None]) -> float:
        expected_export_w = cls._non_negative_optional_float(battery_activity.get("expected_near_term_export_w")) or 0.0
        expected_import_w = cls._non_negative_optional_float(battery_activity.get("expected_near_term_import_w")) or 0.0
        export_credit_w = expected_export_w * 0.15
        import_penalty_w = expected_import_w * 0.15
        return float(export_credit_w - import_penalty_w)

    @staticmethod
    def _learning_observed_value(profile: object, key: str) -> float | None:
        if not isinstance(profile, dict):
            return None
        return _AutoDecisionGatesMixin._non_negative_optional_float(profile.get(key))

    @staticmethod
    def _active_battery_power(
        current_power_w: float | None,
        observed_max_power_w: float | None,
    ) -> tuple[float | None, float | None]:
        if current_power_w is None or current_power_w <= 0.0:
            return None, None
        ratio = _AutoDecisionGatesMixin._battery_activity_ratio(current_power_w, observed_max_power_w)
        if ratio is not None and ratio < 0.05:
            return None, ratio
        return float(current_power_w), ratio

    @staticmethod
    def _battery_activity_ratio(current_power_w: float, observed_max_power_w: float | None) -> float | None:
        if observed_max_power_w is None or observed_max_power_w <= 0.0:
            return None
        return float(current_power_w) / float(observed_max_power_w)

    @staticmethod
    def _non_negative_optional_float(value: object) -> float | None:
        if not isinstance(value, (int, float)):
            return None
        numeric_value = float(value)
        if numeric_value < 0.0:
            return None
        return numeric_value

    @staticmethod
    def _required_float(value: object) -> float:
        assert isinstance(value, (int, float))
        return float(value)

    @staticmethod
    def _max_optional_ratio(current: float | None, candidate: float | None) -> float | None:
        if candidate is None:
            return current
        if current is None:
            return float(candidate)
        return max(float(current), float(candidate))

    @staticmethod
    def _bounded_optional_float(value: object) -> float | None:
        if not isinstance(value, (int, float)):
            return None
        numeric_value = float(value)
        if numeric_value < -1.0:
            return -1.0
        if numeric_value > 1.0:
            return 1.0
        return numeric_value

    def _battery_penalty_multiplier(
        self,
        *,
        direction: str,
        response_delay_seconds: float | None,
        support_bias: float | None,
        import_support_bias: float | None,
        export_bias: float | None,
    ) -> float:
        multiplier = 1.0
        multiplier *= self._response_delay_penalty_factor(response_delay_seconds)
        if direction == "charge":
            multiplier *= self._charge_bias_penalty_factor(export_bias)
        elif direction == "discharge":
            multiplier *= self._discharge_bias_penalty_factor(import_support_bias, support_bias)
        return float(multiplier)

    @staticmethod
    def _response_delay_penalty_factor(response_delay_seconds: float | None) -> float:
        if response_delay_seconds is None or response_delay_seconds <= 0.0:
            return 1.0
        return 1.0 + min(float(response_delay_seconds), 60.0) / 120.0

    @staticmethod
    def _charge_bias_penalty_factor(export_bias: float | None) -> float:
        positive_export_bias = 0.0 if export_bias is None else max(0.0, float(export_bias))
        return 1.0 + (positive_export_bias * 0.25)

    @staticmethod
    def _discharge_bias_penalty_factor(
        import_support_bias: float | None,
        support_bias: float | None,
    ) -> float:
        discharge_bias = import_support_bias
        if discharge_bias is None and support_bias is not None:
            discharge_bias = max(0.0, float(support_bias))
        positive_discharge_bias = 0.0 if discharge_bias is None else max(0.0, float(discharge_bias))
        return 1.0 + (positive_discharge_bias * 0.25)

    def _smoothed_decision_metrics(
        self,
        relay_on: bool,
        avg_surplus_power: float,
        avg_grid_power: float,
        adaptive_alpha: float,
    ) -> tuple[float, float]:
        """Return the decision metrics after optional stop-side EWMA smoothing."""
        svc = self.service
        if not relay_on:
            svc._stop_smoothed_surplus_power = None
            svc._stop_smoothed_grid_power = None
            return avg_surplus_power, avg_grid_power
        decision_surplus_power = self._smooth_metric(
            getattr(svc, "_stop_smoothed_surplus_power", None),
            avg_surplus_power,
            adaptive_alpha,
        )
        decision_grid_power = self._smooth_metric(
            getattr(svc, "_stop_smoothed_grid_power", None),
            avg_grid_power,
            adaptive_alpha,
        )
        svc._stop_smoothed_surplus_power = decision_surplus_power
        svc._stop_smoothed_grid_power = decision_grid_power
        return float(decision_surplus_power), float(decision_grid_power)

    def _handle_common_runtime_gates(self, relay_on: bool, now: float, cached_inputs: bool) -> AutoDecision:
        """Honor startup warmup and manual override holdoff."""
        svc = self.service
        if (now - svc.started_at) < svc.auto_startup_warmup_seconds:
            self._reset_auto_state()
            return cast(AutoDecision, self._set_health_result("warmup", cached_inputs, relay_on))

        if now < svc.manual_override_until:
            self._reset_auto_state()
            return cast(AutoDecision, self._set_health_result("manual-override", cached_inputs, relay_on))

        return cast(AutoDecision, self._NO_DECISION)
