# SPDX-License-Identifier: GPL-3.0-or-later
"""Internal Auto-mode decision workflow helpers for the Shelly wallbox service.

The Auto controller keeps the policy readable by splitting the decision tree
into many small helper methods. The high-level behavior is:
- gather fresh PV, grid, and battery inputs
- smooth the relevant values
- check hard safety gates first
- evaluate start/stop conditions
- return the desired relay state plus a diagnostic health reason
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import math
import time
from datetime import datetime
from typing import Any, Deque

from dbus_shelly_wallbox_auto_policy import AutoPolicy, validate_auto_policy
from dbus_shelly_wallbox_common import _auto_state_code, _confirmed_relay_state_max_age_seconds, _derive_auto_state
from dbus_shelly_wallbox_contracts import cutover_confirmed_off
from dbus_shelly_wallbox_split_mixins import _ComposableControllerMixin

AutoSample = tuple[float, float, float]
AutoDecision = bool | object
MonthWindow = tuple[tuple[int, int], tuple[int, int]]



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
            return self._set_health_result(running_reason, cached_inputs, True)
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
        return (now - grid_last_read_at) <= grid_missing_stop_seconds

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

    def _resolve_battery_soc(
        self,
        battery_soc: float | int | None,
        relay_on: bool,
        now: float,
        cached_inputs: bool,
    ) -> tuple[float | None, AutoDecision]:
        """Normalize battery SOC or return a terminal decision when it is unavailable."""
        svc = self.service
        if battery_soc is not None:
            normalized_battery_soc = float(battery_soc)
            if not 0.0 <= normalized_battery_soc <= 100.0:
                warning_throttled = getattr(svc, "_warning_throttled", None)
                if callable(warning_throttled):
                    warning_throttled(
                        "battery-soc-invalid",
                        max(1.0, float(getattr(svc, "auto_battery_scan_interval_seconds", 60.0) or 60.0)),
                        "Auto mode ignored out-of-range battery SOC %s",
                        normalized_battery_soc,
                    )
                battery_soc = None
            else:
                svc._last_battery_allow_warning = None
                return normalized_battery_soc, self._NO_DECISION

        if svc.auto_allow_without_battery_soc:
            if (
                svc._last_battery_allow_warning is None
                or (now - svc._last_battery_allow_warning) > svc.auto_battery_scan_interval_seconds
            ):
                logging.warning("Auto mode: battery SOC missing, allowing Auto based on resume SOC.")
                svc._last_battery_allow_warning = now
            self.set_health("battery-soc-missing-allowed", cached_inputs, relay_intent=relay_on)
            return float(self._auto_policy().resume_soc), self._NO_DECISION

        self._reset_auto_state()
        self.set_health("battery-soc-missing", cached_inputs, relay_intent=relay_on)
        return None, relay_on

    def _handle_cutover_pending(self, relay_on: bool, cached_inputs: bool) -> AutoDecision:
        """Honor the Manual -> Auto clean-cutover until the relay is confirmed off."""
        svc = self.service
        if not getattr(svc, "_auto_mode_cutover_pending", False):
            return self._NO_DECISION

        now = self._learning_policy_now()
        self._reset_auto_state()
        if not self._cutover_relay_off_confirmed(relay_on, now):
            self.set_health("mode-transition", cached_inputs, relay_intent=False)
            return False

        self._complete_cutover_pending()
        return self._NO_DECISION

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
        return (float(now) - float(confirmed_pm_status_at)) <= _confirmed_relay_state_max_age_seconds(self.service)

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
        active_stop_key = reason if stop_key is None else stop_key
        current_stop_key = getattr(svc, "auto_stop_condition_reason", None)
        if (
            svc.auto_stop_condition_since is None
            or (current_stop_key is not None and current_stop_key != active_stop_key)
        ):
            svc.auto_stop_condition_since = now
            svc.auto_stop_condition_reason = active_stop_key
            return self._NO_DECISION
        if current_stop_key is None:
            svc.auto_stop_condition_reason = active_stop_key
        effective_delay = svc.auto_stop_delay_seconds if delay_seconds is None else float(delay_seconds)
        if (now - svc.auto_stop_condition_since) >= effective_delay:
            self.set_health(reason, cached_inputs, relay_intent=False)
            return False
        return self._NO_DECISION

    def _handle_grid_missing(self, relay_on: bool, now: float, cached_inputs: bool) -> bool:
        """Fail safe when no fresh grid reading is available."""
        svc = self.service
        self._clear_auto_start_tracking(clear_samples=True)
        svc._grid_recovery_required = True
        svc._grid_recovery_since = None
        if not relay_on:
            return self._idle_result_with_health("grid-missing", cached_inputs)

        if not self._minimum_runtime_elapsed(now):
            return self._running_result_with_health("grid-missing", cached_inputs)
        return self._pending_stop_or_running(now, "grid-missing", cached_inputs, "grid-missing")

    def _handle_grid_recovery_start_gate(self, relay_on: bool, now: float, cached_inputs: bool) -> AutoDecision:
        """Require a short fresh-grid window before Auto may start after grid loss."""
        svc = self.service
        if not hasattr(svc, "_grid_recovery_since") or not hasattr(svc, "_grid_recovery_required"):
            return self._NO_DECISION
        if not bool(getattr(svc, "_grid_recovery_required", False)):
            return self._NO_DECISION

        recovery_seconds = float(self._auto_policy().grid_recovery_start_seconds)
        if recovery_seconds <= 0:
            svc._grid_recovery_since = now
            svc._grid_recovery_required = False
            return self._NO_DECISION

        grid_recovery_since = getattr(svc, "_grid_recovery_since", None)
        if grid_recovery_since is None:
            svc._grid_recovery_since = now
            if relay_on:
                return self._NO_DECISION
            self._clear_auto_start_tracking()
            return self._set_health_result("waiting-grid-recovery", cached_inputs, False)

        if (now - float(grid_recovery_since)) < recovery_seconds:
            if relay_on:
                return self._NO_DECISION
            self._clear_auto_start_tracking()
            return self._set_health_result("waiting-grid-recovery", cached_inputs, False)

        svc._grid_recovery_required = False
        return self._NO_DECISION

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
        policy = self._auto_policy()
        if battery_soc < policy.min_soc:
            return "auto-stop"
        if grid_power is not None and float(grid_power) >= policy.stop_grid_import_watts:
            return "auto-stop"
        return None

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
            return self._idle_result_with_health("inputs-missing", cached_inputs)

        daytime_window_open = svc._is_within_auto_daytime_window()
        stop_reason = self._known_missing_input_stop_reason(battery_soc, grid_power, daytime_window_open)
        if not self._minimum_runtime_elapsed(now) or stop_reason is None:
            return self._running_result_with_health("inputs-missing", cached_inputs)
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

        start_surplus_watts, stop_surplus_watts, threshold_profile = self._surplus_thresholds_for_soc(battery_soc)
        adaptive_alpha, adaptive_alpha_stage, surplus_volatility = self._adaptive_stop_alpha()
        decision_surplus_power = float(avg_surplus_power)
        decision_grid_power = float(avg_grid_power)
        if relay_on:
            decision_surplus_power = self._smooth_metric(
                getattr(svc, "_stop_smoothed_surplus_power", None),
                decision_surplus_power,
                adaptive_alpha,
            )
            decision_grid_power = self._smooth_metric(
                getattr(svc, "_stop_smoothed_grid_power", None),
                decision_grid_power,
                adaptive_alpha,
            )
            svc._stop_smoothed_surplus_power = decision_surplus_power
            svc._stop_smoothed_grid_power = decision_grid_power
        else:
            svc._stop_smoothed_surplus_power = None
            svc._stop_smoothed_grid_power = None

        learned_charge_power_state = self._current_learned_charge_power_state(now)
        learned_charge_power = self._active_learned_charge_power(now)
        threshold_scale = float(self._learned_charge_power_scale(now))
        svc._last_auto_metrics = {
            "surplus": float(decision_surplus_power),
            "grid": float(decision_grid_power),
            "raw_surplus": float(avg_surplus_power),
            "raw_grid": float(avg_grid_power),
            "soc": float(battery_soc),
            "profile": threshold_profile,
            "start_threshold": float(start_surplus_watts),
            "stop_threshold": float(stop_surplus_watts),
            "learned_charge_power": learned_charge_power,
            "learned_charge_power_state": learned_charge_power_state,
            "threshold_scale": threshold_scale,
            "threshold_mode": "adaptive" if learned_charge_power is not None else "static",
            "stop_alpha": float(adaptive_alpha),
            "stop_alpha_stage": adaptive_alpha_stage,
            "surplus_volatility": float(surplus_volatility) if surplus_volatility is not None else None,
        }
        return float(decision_surplus_power), float(decision_grid_power)

    def _handle_common_runtime_gates(self, relay_on: bool, now: float, cached_inputs: bool) -> AutoDecision:
        """Honor startup warmup and manual override holdoff."""
        svc = self.service
        if (now - svc.started_at) < svc.auto_startup_warmup_seconds:
            self._reset_auto_state()
            return self._set_health_result("warmup", cached_inputs, relay_on)

        if now < svc.manual_override_until:
            self._reset_auto_state()
            return self._set_health_result("manual-override", cached_inputs, relay_on)

        return self._NO_DECISION
