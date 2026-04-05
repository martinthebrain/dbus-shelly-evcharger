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

AutoSample = tuple[float, float, float]
AutoDecision = bool | object
MonthWindow = tuple[tuple[int, int], tuple[int, int]]


class AutoDecisionWorkflowMixin:
    """Provide the detailed Auto-mode decision flow used by AutoDecisionController."""

    service: Any
    _NO_DECISION: object
    _health_code: Callable[[str], int]
    _mode_uses_auto_logic: Callable[[Any], bool]

    @staticmethod
    def get_available_surplus_watts(pv_power: float | int, grid_power: float | int) -> float:
        """Compute PV-backed export as available charging surplus."""
        pv_power = max(0.0, float(pv_power))
        export_power = max(0.0, -float(grid_power))
        return min(pv_power, export_power)

    def add_auto_sample(self, now: float, surplus_power: float, grid_power: float) -> None:
        """Append a sample for averaging and prune old data."""
        samples: Deque[AutoSample] = self.service.auto_samples
        samples.append((now, float(surplus_power), float(grid_power)))
        cutoff = now - self.service.auto_average_window_seconds
        while samples and samples[0][0] < cutoff:
            samples.popleft()

    def clear_auto_samples(self) -> None:
        """Clear all auto averaging samples."""
        self.service.auto_samples.clear()
        self.service._stop_smoothed_surplus_power = None
        self.service._stop_smoothed_grid_power = None

    def average_auto_metric(self, index: int) -> float | None:
        """Compute the mean of one field from the sample buffer."""
        if not self.service.auto_samples:
            return None
        return sum(sample[index] for sample in self.service.auto_samples) / len(self.service.auto_samples)

    @staticmethod
    def _smooth_metric(previous: float | None, current: float, alpha: float) -> float:
        """Apply EWMA smoothing, falling back to the current value on first sample."""
        if previous is None:
            return float(current)
        return float(previous) + (float(alpha) * (float(current) - float(previous)))

    def _surplus_thresholds_for_soc(self, battery_soc: float) -> tuple[float, float, str]:
        """Return the active start/stop surplus thresholds for the current battery SOC."""
        svc = self.service
        policy = self._auto_policy()
        profile, active, profile_name = policy.resolve_threshold_profile(
            battery_soc,
            getattr(svc, "_auto_high_soc_profile_active", None),
        )
        svc._auto_high_soc_profile_active = bool(active)
        return float(profile.start_surplus_watts), float(profile.stop_surplus_watts), profile_name

    def _auto_policy(self) -> AutoPolicy:
        """Return the structured AutoPolicy, synthesizing it from legacy attrs when needed."""
        svc = self.service
        policy = getattr(svc, "auto_policy", None)
        if policy is None:
            policy = validate_auto_policy(AutoPolicy.from_service(svc))
            try:
                svc.auto_policy = policy
            except AttributeError:
                pass
        return policy

    def _stop_surplus_volatility(self) -> float | None:
        """Return the population standard deviation of recent raw surplus samples."""
        samples: Deque[AutoSample] = self.service.auto_samples
        if len(samples) < 2:
            return None
        surplus_values = [float(sample[1]) for sample in samples]
        mean_value = sum(surplus_values) / len(surplus_values)
        variance = sum((value - mean_value) ** 2 for value in surplus_values) / len(surplus_values)
        return math.sqrt(variance)

    def _adaptive_stop_alpha(self) -> tuple[float, str, float | None]:
        """Return an adaptive EWMA alpha based on recent surplus volatility."""
        volatility = self._stop_surplus_volatility()
        return self._auto_policy().ewma.adaptive_alpha(volatility)

    def mark_relay_changed(self, relay_on: bool, now: float | None = None) -> None:
        """Record the last relay state change for minimum on/off logic."""
        changed_at = time.time() if now is None else float(now)
        self.service.relay_last_changed_at = changed_at
        if not relay_on:
            self.service.relay_last_off_at = changed_at

    def is_within_auto_daytime_window(self, current_dt: datetime | None = None) -> bool:
        """Return True if current time is inside the seasonal day window."""
        if not getattr(self.service, "auto_daytime_only", False):
            return True

        current_dt = datetime.now() if current_dt is None else current_dt
        current_minutes = current_dt.hour * 60 + current_dt.minute
        month = current_dt.month
        month_windows: dict[int, MonthWindow] = getattr(self.service, "auto_month_windows", {})
        start_window, end_window = month_windows.get(month, ((8, 0), (18, 0)))
        start_hour, start_minute = start_window
        end_hour, end_minute = end_window
        start_minutes = start_hour * 60 + start_minute
        end_minutes = end_hour * 60 + end_minute

        if start_minutes == end_minutes:
            return True
        if start_minutes < end_minutes:
            return start_minutes <= current_minutes < end_minutes
        return current_minutes >= start_minutes or current_minutes < end_minutes

    def set_health(self, reason: str, cached: bool = False) -> None:
        """Store health reason and numeric code, optionally marking cached inputs."""
        if cached:
            reason = f"{reason}-cached"
        self.service._last_health_reason = reason
        base_reason = reason.replace("-cached", "")
        code = self._health_code(base_reason)
        self.service._last_health_code = code + (100 if cached else 0)
        if getattr(self.service, "auto_audit_log", False):
            self.service._write_auto_audit_event(base_reason, cached)

    def _reset_auto_state(self) -> None:
        """Reset Auto start/stop timers and rolling samples."""
        svc = self.service
        svc.auto_start_condition_since = None
        svc.auto_stop_condition_since = None
        svc._clear_auto_samples()

    def _clear_auto_start_tracking(self, clear_samples: bool = False) -> None:
        """Clear pending Auto-start tracking, optionally including average samples."""
        self.service.auto_start_condition_since = None
        if clear_samples:
            self.service._clear_auto_samples()

    def _clear_auto_stop_tracking(self) -> None:
        """Clear pending Auto-stop tracking."""
        self.service.auto_stop_condition_since = None
        self.service.auto_stop_condition_reason = None

    def _set_health_result(self, reason: str, cached_inputs: bool, result: bool) -> bool:
        """Set a health reason and return the corresponding decision result."""
        self.service._set_health(reason, cached_inputs)
        return result

    def _idle_result_with_health(self, reason: str, cached_inputs: bool) -> bool:
        """Return the standard idle/relay-off result with a health reason."""
        self._clear_auto_start_tracking()
        return self._set_health_result(reason, cached_inputs, False)

    def _running_result_with_health(self, reason: str, cached_inputs: bool) -> bool:
        """Return the standard running/relay-on result with a health reason."""
        self._clear_auto_stop_tracking()
        return self._set_health_result(reason, cached_inputs, True)

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
        svc._save_runtime_state()
        return relay_on

    def _handle_disabled_mode(self, cached_inputs: bool) -> bool:
        """Force relay off when Auto has been disabled by the GUI."""
        svc = self.service
        self._reset_auto_state()
        svc._auto_mode_cutover_pending = False
        svc._ignore_min_offtime_once = False
        svc._set_health("disabled", cached_inputs)
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
            svc._last_battery_allow_warning = None
            return float(battery_soc), self._NO_DECISION

        if svc.auto_allow_without_battery_soc:
            if (
                svc._last_battery_allow_warning is None
                or (now - svc._last_battery_allow_warning) > svc.auto_battery_scan_interval_seconds
            ):
                logging.warning("Auto mode: battery SOC missing, allowing Auto based on resume SOC.")
                svc._last_battery_allow_warning = now
            svc._set_health("battery-soc-missing-allowed", cached_inputs)
            return float(self._auto_policy().resume_soc), self._NO_DECISION

        self._reset_auto_state()
        svc._set_health("battery-soc-missing", cached_inputs)
        return None, relay_on

    def _handle_cutover_pending(self, relay_on: bool, cached_inputs: bool) -> AutoDecision:
        """Honor the Manual -> Auto clean-cutover until the relay is confirmed off."""
        svc = self.service
        if not getattr(svc, "_auto_mode_cutover_pending", False):
            return self._NO_DECISION

        pending_state, _ = svc._peek_pending_relay_command()
        self._reset_auto_state()
        if relay_on or pending_state is not None:
            svc._set_health("mode-transition", cached_inputs)
            return False

        svc._auto_mode_cutover_pending = False
        svc._ignore_min_offtime_once = True
        svc._save_runtime_state()
        return self._NO_DECISION

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
            svc._set_health(reason, cached_inputs)
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

        svc._last_auto_metrics = {
            "surplus": float(decision_surplus_power),
            "grid": float(decision_grid_power),
            "raw_surplus": float(avg_surplus_power),
            "raw_grid": float(avg_grid_power),
            "soc": float(battery_soc),
            "profile": threshold_profile,
            "start_threshold": float(start_surplus_watts),
            "stop_threshold": float(stop_surplus_watts),
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

    def _handle_relay_on(
        self,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
        daytime_window_open: bool,
        now: float,
        cached_inputs: bool,
    ) -> bool:
        """Evaluate Auto stop conditions while the relay is already on."""
        svc = self.service
        svc.auto_start_condition_since = None
        minimum_runtime_elapsed = self._minimum_runtime_elapsed(now)

        stop_reason = self._relay_on_stop_reason(
            avg_surplus_power,
            avg_grid_power,
            battery_soc,
            daytime_window_open,
            minimum_runtime_elapsed,
        )

        if stop_reason is None:
            return self._running_result_with_health("running", cached_inputs)
        stop_delay_seconds = svc.auto_stop_delay_seconds
        reported_reason = stop_reason
        if stop_reason == "auto-stop-surplus":
            stop_delay_seconds = float(self._auto_policy().stop_surplus_delay_seconds)
            reported_reason = "auto-stop"
        elif stop_reason in ("auto-stop-grid", "auto-stop-soc"):
            reported_reason = "auto-stop"
        return self._pending_stop_or_running(
            now,
            reported_reason,
            cached_inputs,
            "running",
            delay_seconds=stop_delay_seconds,
            stop_key=stop_reason,
        )

    def _relay_on_stop_reason(
        self,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
        daytime_window_open: bool,
        minimum_runtime_elapsed: bool,
    ) -> str | None:
        """Return the concrete stop reason while Auto is already running."""
        svc = self.service
        _, stop_surplus_watts, _ = self._surplus_thresholds_for_soc(battery_soc)
        if getattr(svc, "auto_night_lock_stop", False) and not daytime_window_open and minimum_runtime_elapsed:
            return "night-lock"
        if not minimum_runtime_elapsed:
            return None
        policy = self._auto_policy()
        if battery_soc < policy.min_soc:
            return "auto-stop-soc"
        if avg_grid_power >= policy.stop_grid_import_watts:
            return "auto-stop-grid"
        if avg_surplus_power <= stop_surplus_watts:
            return "auto-stop-surplus"
        return None

    @staticmethod
    def _relay_off_start_conditions_met(
        minimum_offtime_elapsed: bool,
        daytime_window_open: bool,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
        start_surplus_watts: float,
        svc: Any,
    ) -> bool:
        """Return whether all Auto start gates are satisfied."""
        return (
            minimum_offtime_elapsed
            and daytime_window_open
            and avg_surplus_power >= start_surplus_watts
            and avg_grid_power <= svc.auto_start_max_grid_import_watts
            and battery_soc >= svc.auto_resume_soc
        )

    def _arm_or_fire_start(self, now: float, cached_inputs: bool) -> bool:
        """Track delayed start conditions and start once the configured delay elapsed."""
        svc = self.service
        if svc.auto_start_condition_since is None:
            svc.auto_start_condition_since = now
            return False
        if (now - svc.auto_start_condition_since) >= svc.auto_start_delay_seconds:
            svc._ignore_min_offtime_once = False
            svc._save_runtime_state()
            svc._set_health("auto-start", cached_inputs)
            return True
        return False

    def _set_waiting_health(
        self,
        minimum_offtime_elapsed: bool,
        daytime_window_open: bool,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
        cached_inputs: bool,
    ) -> None:
        """Set the most useful waiting reason while Auto is idle."""
        svc = self.service
        start_surplus_watts, _, _ = self._surplus_thresholds_for_soc(battery_soc)
        if not minimum_offtime_elapsed:
            reason = "waiting-offtime"
        elif not daytime_window_open:
            reason = "waiting-daytime"
        elif avg_surplus_power < start_surplus_watts:
            reason = "waiting-surplus"
        elif avg_grid_power > svc.auto_start_max_grid_import_watts:
            reason = "waiting-grid"
        elif battery_soc < svc.auto_resume_soc:
            reason = "waiting-soc"
        else:
            reason = "waiting"
        svc._set_health(reason, cached_inputs)

    def _handle_relay_off(
        self,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
        daytime_window_open: bool,
        now: float,
        cached_inputs: bool,
    ) -> bool:
        """Evaluate Auto start conditions while the relay is off."""
        svc = self.service
        start_surplus_watts, _, _ = self._surplus_thresholds_for_soc(battery_soc)
        svc.auto_stop_condition_since = None
        if not svc.virtual_autostart:
            return self._idle_result_with_health("autostart-disabled", cached_inputs)

        minimum_offtime_elapsed = self._minimum_offtime_elapsed(now)
        if self._relay_off_start_conditions_met(
            minimum_offtime_elapsed,
            daytime_window_open,
            avg_surplus_power,
            avg_grid_power,
            battery_soc,
            start_surplus_watts,
            svc,
        ):
            return self._arm_or_fire_start(now, cached_inputs)

        self._clear_auto_start_tracking()
        self._set_waiting_health(
            minimum_offtime_elapsed,
            daytime_window_open,
            avg_surplus_power,
            avg_grid_power,
            battery_soc,
            cached_inputs,
        )
        return False

    def _pre_average_decision(
        self,
        relay_on: bool,
        pv_power: float | None,
        battery_soc: float | int | None,
        grid_power: float | None,
        now: float,
        cached_inputs: bool,
    ) -> tuple[AutoDecision, float | None]:
        """Handle all early exits before rolling-average metrics are considered."""
        svc = self.service
        if not self._mode_uses_auto_logic(svc.virtual_mode):
            return self._handle_non_auto_mode(relay_on), None

        if not bool(getattr(svc, "virtual_enable", 1)):
            return self._handle_disabled_mode(cached_inputs), None

        decision = self._handle_cutover_pending(relay_on, cached_inputs)
        if decision is not self._NO_DECISION:
            return decision, None

        if not self._grid_recently_read(grid_power, now):
            return self._handle_grid_missing(relay_on, now, cached_inputs), None

        battery_soc, decision = self._resolve_battery_soc(battery_soc, relay_on, now, cached_inputs)
        if decision is not self._NO_DECISION:
            return decision, None

        decision = self._handle_grid_recovery_start_gate(relay_on, now, cached_inputs)
        if decision is not self._NO_DECISION:
            return decision, None

        if pv_power is None or grid_power is None:
            assert battery_soc is not None
            return self._handle_missing_inputs(relay_on, battery_soc, grid_power, now, cached_inputs), None

        return self._NO_DECISION, battery_soc

    def _post_average_decision(
        self,
        relay_on: bool,
        avg_surplus_power: float,
        avg_grid_power: float,
        battery_soc: float,
        now: float,
        cached_inputs: bool,
    ) -> tuple[AutoDecision, bool | None]:
        """Handle warmup/manual override gates after averages are available."""
        decision = self._handle_common_runtime_gates(relay_on, now, cached_inputs)
        if decision is not self._NO_DECISION:
            return decision, None
        return self._NO_DECISION, self.service._is_within_auto_daytime_window()

    def auto_decide_relay(
        self,
        relay_on: bool,
        pv_power: float | None,
        battery_soc: float | int | None,
        grid_power: float | None,
    ) -> bool:
        """Return desired relay state based on auto surplus logic."""
        svc = self.service
        cached_inputs = bool(getattr(svc, "_auto_cached_inputs_used", False))
        now = time.time()
        decision, battery_soc = self._pre_average_decision(
            relay_on,
            pv_power,
            battery_soc,
            grid_power,
            now,
            cached_inputs,
        )
        if decision is not self._NO_DECISION:
            assert isinstance(decision, bool)
            return decision

        assert pv_power is not None
        assert grid_power is not None
        assert battery_soc is not None

        avg_surplus_power, avg_grid_power = self._update_average_metrics(
            now,
            pv_power,
            grid_power,
            battery_soc,
            relay_on,
        )
        if avg_surplus_power is None or avg_grid_power is None:
            svc._set_health("averaging", cached_inputs)
            return relay_on

        decision, daytime_window_open = self._post_average_decision(
            relay_on,
            avg_surplus_power,
            avg_grid_power,
            battery_soc,
            now,
            cached_inputs,
        )
        if decision is not self._NO_DECISION:
            assert isinstance(decision, bool)
            return decision

        assert daytime_window_open is not None

        if relay_on:
            return self._handle_relay_on(
                avg_surplus_power,
                avg_grid_power,
                battery_soc,
                daytime_window_open,
                now,
                cached_inputs,
            )

        return self._handle_relay_off(
            avg_surplus_power,
            avg_grid_power,
            battery_soc,
            daytime_window_open,
            now,
            cached_inputs,
        )
