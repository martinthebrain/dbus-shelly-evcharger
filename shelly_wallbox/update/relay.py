# SPDX-License-Identifier: GPL-3.0-or-later
"""Virtual-state publishing and update-cycle helpers for the Shelly wallbox service.

The update cycle is the heartbeat of the wallbox integration. Every pass reads
the latest Shelly snapshot, lets Auto mode decide whether the relay should be
on, applies corrections if needed, and then publishes the resulting charger
state back to Venus OS.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, cast

from shelly_wallbox.backend.models import PhaseSelection, normalize_phase_selection
from shelly_wallbox.core.contracts import (
    finite_float_or_none,
    normalize_learning_phase,
    normalize_learning_state,
)
from shelly_wallbox.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin



class _UpdateCycleRelayMixin(_ComposableControllerMixin):
    PHASE_SWITCH_WAITING_STATE = "waiting-relay-off"
    PHASE_SWITCH_STABILIZING_STATE = "stabilizing"
    CHARGER_FAULT_HINT_TOKENS = frozenset(
        {"fault", "error", "failed", "failure", "alarm", "offline", "unavailable", "lockout", "tripped"}
    )
    CHARGER_STATUS_CHARGING_HINT_TOKENS = frozenset({"charging"})
    CHARGER_STATUS_READY_HINT_TOKENS = frozenset({"ready", "connected", "available", "idle"})
    CHARGER_STATUS_WAITING_HINT_TOKENS = frozenset({"paused", "waiting", "suspended", "sleeping"})
    CHARGER_STATUS_FINISHED_HINT_TOKENS = frozenset({"complete", "completed", "finished", "done"})

    @staticmethod
    def _charger_enable_backend(svc: Any) -> object | None:
        """Return the configured charger backend when it can enable/disable charging."""
        backend = getattr(svc, "_charger_backend", None)
        return backend if hasattr(backend, "set_enabled") else None

    @staticmethod
    def _charger_current_backend(svc: Any) -> object | None:
        """Return the configured charger backend when it can accept current setpoints."""
        backend = getattr(svc, "_charger_backend", None)
        return backend if hasattr(backend, "set_current") else None

    @staticmethod
    def _charger_state_max_age_seconds(svc: Any) -> float:
        """Return how fresh charger readback must be before it overrides runtime status."""
        candidates = [2.0]
        worker_poll_interval = finite_float_or_none(getattr(svc, "_worker_poll_interval_seconds", None))
        if worker_poll_interval is not None and worker_poll_interval > 0.0:
            candidates.append(float(worker_poll_interval) * 2.0)
        soft_fail_seconds = finite_float_or_none(getattr(svc, "auto_shelly_soft_fail_seconds", 10.0))
        if soft_fail_seconds is not None and soft_fail_seconds > 0.0:
            candidates.append(float(soft_fail_seconds))
        return max(1.0, min(candidates))

    @classmethod
    def _fresh_charger_enabled_readback(cls, svc: Any, now: float | None = None) -> bool | None:
        """Return fresh native charger enabled-state readback when available."""
        if getattr(svc, "_charger_backend", None) is None:
            return None
        state_at = finite_float_or_none(getattr(svc, "_last_charger_state_at", None))
        if state_at is None:
            return None
        raw_enabled = getattr(svc, "_last_charger_state_enabled", None)
        if raw_enabled is None:
            return None
        current = (
            float(now)
            if now is not None
            else (
                float(svc._time_now())
                if callable(getattr(svc, "_time_now", None))
                else time.time()
            )
        )
        if abs(current - state_at) > cls._charger_state_max_age_seconds(svc):
            return None
        return bool(raw_enabled)

    @classmethod
    def _fresh_charger_float_readback(
        cls,
        svc: Any,
        attribute_name: str,
        now: float | None = None,
    ) -> float | None:
        """Return one fresh numeric charger readback attribute when available."""
        if getattr(svc, "_charger_backend", None) is None:
            return None
        state_at = finite_float_or_none(getattr(svc, "_last_charger_state_at", None))
        if state_at is None:
            return None
        value = finite_float_or_none(getattr(svc, attribute_name, None))
        if value is None:
            return None
        current = (
            float(now)
            if now is not None
            else (
                float(svc._time_now())
                if callable(getattr(svc, "_time_now", None))
                else time.time()
            )
        )
        if abs(current - state_at) > cls._charger_state_max_age_seconds(svc):
            return None
        return float(value)

    @classmethod
    def _fresh_charger_power_readback(cls, svc: Any, now: float | None = None) -> float | None:
        """Return fresh native charger power readback when available."""
        power_w = cls._fresh_charger_float_readback(svc, "_last_charger_state_power_w", now)
        return None if power_w is None else max(0.0, float(power_w))

    @classmethod
    def _fresh_charger_actual_current_readback(cls, svc: Any, now: float | None = None) -> float | None:
        """Return fresh native charger measured current readback when available."""
        current_amps = cls._fresh_charger_float_readback(
            svc,
            "_last_charger_state_actual_current_amps",
            now,
        )
        return None if current_amps is None else max(0.0, float(current_amps))

    @classmethod
    def _fresh_charger_energy_readback(cls, svc: Any, now: float | None = None) -> float | None:
        """Return fresh native charger total energy readback when available."""
        energy_kwh = cls._fresh_charger_float_readback(svc, "_last_charger_state_energy_kwh", now)
        return None if energy_kwh is None else max(0.0, float(energy_kwh))

    @classmethod
    def _fresh_charger_text_readback(
        cls,
        svc: Any,
        attribute_name: str,
        now: float | None = None,
    ) -> str | None:
        """Return one fresh charger readback text field when available."""
        if getattr(svc, "_charger_backend", None) is None:
            return None
        state_at = finite_float_or_none(getattr(svc, "_last_charger_state_at", None))
        if state_at is None:
            return None
        current = (
            float(now)
            if now is not None
            else (
                float(svc._time_now())
                if callable(getattr(svc, "_time_now", None))
                else time.time()
            )
        )
        if abs(current - state_at) > cls._charger_state_max_age_seconds(svc):
            return None
        raw_value = getattr(svc, attribute_name, None)
        if raw_value is None:
            return None
        text = str(raw_value).strip()
        return text or None

    @classmethod
    def _charger_text_tokens(cls, value: str | None) -> set[str]:
        """Return normalized word-like tokens from one charger text field."""
        if value is None:
            return set()
        normalized = str(value).strip().lower()
        for separator in ("-", "_", "/", ".", ",", ";", ":"):
            normalized = normalized.replace(separator, " ")
        return {token for token in normalized.split() if token}

    @classmethod
    def _charger_text_indicates_fault(cls, value: str | None) -> bool:
        """Return whether one charger text field looks like a hard device fault."""
        tokens = cls._charger_text_tokens(value)
        if not tokens or "no" in tokens:
            return False
        return bool(tokens & set(cls.CHARGER_FAULT_HINT_TOKENS))

    @classmethod
    def charger_health_override(cls, svc: Any, now: float | None = None) -> str | None:
        """Return one charger-derived health override when readback reports a hard fault."""
        if cls._charger_text_indicates_fault(cls._fresh_charger_text_readback(svc, "_last_charger_state_fault", now)):
            return "charger-fault"
        if cls._charger_text_indicates_fault(
            cls._fresh_charger_text_readback(svc, "_last_charger_state_status", now)
        ):
            return "charger-fault"
        return None

    @classmethod
    def _charger_status_override(
        cls,
        svc: Any,
        auto_mode_active: bool,
        now: float | None = None,
    ) -> tuple[int, str] | None:
        """Return one charger-native status override derived from fresh text readback."""
        status_text = cls._fresh_charger_text_readback(svc, "_last_charger_state_status", now)
        tokens = cls._charger_text_tokens(status_text)
        if not tokens:
            return None
        if tokens & set(cls.CHARGER_STATUS_FINISHED_HINT_TOKENS):
            return 3, "charger-status-finished"
        if tokens & set(cls.CHARGER_STATUS_WAITING_HINT_TOKENS):
            return (4 if auto_mode_active else 6), "charger-status-waiting"
        if tokens & set(cls.CHARGER_STATUS_CHARGING_HINT_TOKENS):
            return 2, "charger-status-charging"
        if tokens & set(cls.CHARGER_STATUS_READY_HINT_TOKENS):
            return int(getattr(svc, "idle_status", 1)), "charger-status-ready"
        return None

    @classmethod
    def _effective_enabled_state(cls, svc: Any, relay_on: bool, now: float | None = None) -> bool:
        """Return the best-known enabled state, preferring fresh native charger readback."""
        charger_enabled = cls._fresh_charger_enabled_readback(svc, now)
        return bool(relay_on) if charger_enabled is None else bool(charger_enabled)

    @classmethod
    def _enable_control_source_key(cls, svc: Any) -> str:
        """Return the observability source key for enable/disable failures."""
        return "charger" if cls._charger_enable_backend(svc) is not None else "shelly"

    @classmethod
    def _enable_control_label(cls, svc: Any) -> str:
        """Return one human-readable label for the active enable/disable backend."""
        return "charger backend" if cls._charger_enable_backend(svc) is not None else "Shelly relay"

    @staticmethod
    def _clamped_charger_current_target(svc: Any, value: float | None) -> float | None:
        """Clamp one charger-current target into the configured min/max window."""
        if value is None:
            return None
        target = float(value)
        min_current = finite_float_or_none(getattr(svc, "min_current", None))
        max_current = finite_float_or_none(getattr(svc, "max_current", None))
        if min_current is not None:
            target = max(target, min_current)
        if max_current is not None and max_current > 0.0:
            target = min(target, max_current)
        return target if target > 0.0 else None

    @classmethod
    def _derived_learned_current_target(cls, svc: Any, now: float) -> float | None:
        """Return one current target derived from a stable learned charging power."""
        if normalize_learning_state(getattr(svc, "learned_charge_power_state", "unknown")) != "stable":
            return None
        learned_power = finite_float_or_none(getattr(svc, "learned_charge_power_watts", None))
        learned_voltage = finite_float_or_none(getattr(svc, "learned_charge_power_voltage", None))
        learned_phase = normalize_learning_phase(
            getattr(svc, "learned_charge_power_phase", getattr(svc, "phase", "L1"))
        )
        updated_at = finite_float_or_none(getattr(svc, "learned_charge_power_updated_at", None))
        max_age_seconds = finite_float_or_none(
            getattr(svc, "auto_learn_charge_power_max_age_seconds", 21600.0)
        )
        if (
            learned_power is None
            or learned_power <= 0.0
            or learned_voltage is None
            or learned_voltage <= 0.0
            or learned_phase is None
            or updated_at is None
        ):
            return None
        if max_age_seconds is not None and max_age_seconds > 0.0 and (float(now) - updated_at) > max_age_seconds:
            return None

        phase_voltage = float(learned_voltage)
        if learned_phase == "3P" and str(getattr(svc, "voltage_mode", "phase")).strip().lower() != "phase":
            phase_voltage = phase_voltage / math.sqrt(3.0) if phase_voltage > 0.0 else 0.0
        phase_count = 3.0 if learned_phase == "3P" else 1.0
        if phase_voltage <= 0.0:
            return None

        rounded_current = finite_float_or_none(round(float(learned_power) / (phase_voltage * phase_count)))
        return cls._clamped_charger_current_target(svc, rounded_current)

    @classmethod
    def _charger_current_target_amps(
        cls,
        svc: Any,
        desired_relay: bool,
        now: float,
        auto_mode_active: bool,
    ) -> float | None:
        """Return one charger current target for the current Auto cycle."""
        if not auto_mode_active or not bool(desired_relay):
            return None
        if cls._charger_current_backend(svc) is None:
            return None
        learned_target = cls._derived_learned_current_target(svc, now)
        if learned_target is not None:
            return learned_target
        fallback_target = finite_float_or_none(getattr(svc, "virtual_set_current", None))
        return cls._clamped_charger_current_target(svc, fallback_target)

    @classmethod
    def apply_charger_current_target(
        cls,
        svc: Any,
        desired_relay: bool,
        now: float,
        auto_mode_active: bool,
    ) -> float | None:
        """Apply one Auto-mode charger current setpoint when a native charger is configured."""
        backend = cls._charger_current_backend(svc)
        if backend is None:
            return None
        if not auto_mode_active or not bool(desired_relay):
            svc._charger_target_current_amps = None
            svc._charger_target_current_applied_at = None
            return None

        target_amps = cls._charger_current_target_amps(svc, desired_relay, now, auto_mode_active)
        if target_amps is None:
            return None

        last_target = finite_float_or_none(getattr(svc, "_charger_target_current_amps", None))
        if last_target is not None and abs(last_target - target_amps) < 0.01:
            return float(last_target)

        try:
            cast(Any, backend).set_current(float(target_amps))
        except Exception as error:  # pylint: disable=broad-except
            svc._mark_failure("charger")
            svc._warning_throttled(
                "charger-current-failed",
                svc.auto_shelly_soft_fail_seconds,
                "Charger current request failed: %s",
                error,
                exc_info=error,
            )
            return last_target

        svc._charger_target_current_amps = float(target_amps)
        svc._charger_target_current_applied_at = float(now)
        svc._mark_recovery("charger", "Charger current writes recovered")
        return float(target_amps)

    @classmethod
    def _apply_enabled_target(cls, svc: Any, enabled: bool, now: float) -> None:
        """Apply one on/off target through the native charger when available."""
        backend = cls._charger_enable_backend(svc)
        if backend is not None:
            cast(Any, backend).set_enabled(bool(enabled))
            return
        svc._queue_relay_command(bool(enabled), now)

    @staticmethod
    def _phase_tuple(raw_value: Any) -> tuple[float, float, float] | None:
        """Return one numeric three-phase tuple from PM metadata."""
        if not isinstance(raw_value, (tuple, list)) or len(raw_value) != 3:
            return None
        values: list[float] = []
        for item in raw_value:
            if not isinstance(item, (int, float)) or isinstance(item, bool):
                return None
            values.append(float(item))
        return values[0], values[1], values[2]

    @staticmethod
    def _phase_voltage(voltage: float, selection: Any, voltage_mode: Any) -> float:
        """Return the per-line voltage implied by one backend phase selection."""
        normalized_selection = str(selection).strip().upper() if selection is not None else ""
        normalized_voltage_mode = str(voltage_mode).strip().lower() if voltage_mode is not None else "phase"
        if normalized_selection == "P1_P2_P3" and normalized_voltage_mode != "phase":
            return float(voltage) / math.sqrt(3.0) if float(voltage) > 0.0 else 0.0
        return float(voltage)

    def _phase_data_for_pm_status(
        self,
        pm_status: dict[str, Any] | None,
        power: float,
        voltage: float,
        current: float,
    ) -> dict[str, dict[str, float]]:
        """Return per-line display values, preferring backend-provided phase metadata."""
        svc = self.service
        if isinstance(pm_status, dict):
            phase_powers = self._phase_tuple(pm_status.get("_phase_powers_w"))
            if phase_powers is not None:
                phase_currents = self._phase_tuple(pm_status.get("_phase_currents_a"))
                phase_voltage = self._phase_voltage(voltage, pm_status.get("_phase_selection"), getattr(svc, "voltage_mode", "phase"))
                phase_data: dict[str, dict[str, float]] = {}
                for phase_name, phase_power, phase_current in zip(
                    ("L1", "L2", "L3"),
                    phase_powers,
                    phase_currents or (None, None, None),
                ):
                    resolved_current = (
                        float(phase_current)
                        if phase_current is not None
                        else (float(phase_power) / phase_voltage if phase_voltage else 0.0)
                    )
                    phase_data[phase_name] = {
                        "power": float(phase_power),
                        "voltage": phase_voltage,
                        "current": resolved_current,
                    }
                return phase_data
        phase_values = cast(
            dict[str, dict[str, float]],
            self._phase_values(power, voltage, svc.phase, svc.voltage_mode),
        )
        return phase_values

    @staticmethod
    def log_auto_relay_change(svc: Any, desired_relay: bool) -> None:
        """Log the current averaged Auto metrics when Auto changes relay state."""
        metrics = svc._last_auto_metrics
        logging.info(
            "Auto relay %s reason=%s surplus=%sW grid=%sW soc=%s%%",
            "ON" if desired_relay else "OFF",
            svc._last_health_reason,
            f"{metrics.get('surplus'):.0f}" if metrics.get("surplus") is not None else "na",
            f"{metrics.get('grid'):.0f}" if metrics.get("grid") is not None else "na",
            f"{metrics.get('soc'):.1f}" if metrics.get("soc") is not None else "na",
        )

    @staticmethod
    def _clear_relay_sync_tracking(svc: Any) -> None:
        """Clear any outstanding relay-confirmation tracking."""
        svc._relay_sync_expected_state = None
        svc._relay_sync_requested_at = None
        svc._relay_sync_deadline_at = None
        svc._relay_sync_failure_reported = False

    @staticmethod
    def _pm_status_confirmed(pm_status: dict[str, Any]) -> bool:
        """Return whether a Shelly state originated from a confirmed device read."""
        return bool(pm_status.get("_pm_confirmed", False))

    def _publish_local_pm_status_best_effort(self, relay_on: bool, now: float) -> None:
        """Publish one optimistic placeholder after a queued relay change without aborting the cycle."""
        svc = self.service
        try:
            svc._publish_local_pm_status(relay_on, now)
        except Exception as error:  # pylint: disable=broad-except
            svc._warning_throttled(
                "relay-placeholder-publish-failed",
                max(1.0, float(getattr(svc, "relay_sync_timeout_seconds", 2.0) or 2.0)),
                "Local relay placeholder publish failed after queueing relay=%s: %s",
                int(bool(relay_on)),
                error,
                exc_info=error,
            )

    def relay_sync_health_override(self, relay_on: bool, pm_confirmed: bool, now: float) -> str | None:
        """Return an explicit health reason for pending relay confirmations."""
        svc = self.service
        expected_state = getattr(svc, "_relay_sync_expected_state", None)
        if expected_state is None:
            return None

        expected_relay = bool(expected_state)
        if pm_confirmed and bool(relay_on) == expected_relay:
            if getattr(svc, "_relay_sync_failure_reported", False):
                svc._mark_recovery("shelly", "Shelly relay confirmation recovered")
            self._clear_relay_sync_tracking(svc)
            return None

        deadline_at = getattr(svc, "_relay_sync_deadline_at", None)
        if deadline_at is None or float(now) < float(deadline_at):
            if pm_confirmed and bool(relay_on) != expected_relay:
                return "command-mismatch"
            return None

        if not getattr(svc, "_relay_sync_failure_reported", False):
            svc._relay_sync_failure_reported = True
            timeout_seconds = max(
                0.0,
                float(deadline_at) - float(getattr(svc, "_relay_sync_requested_at", deadline_at)),
            )
            svc._mark_failure("shelly")
            svc._warning_throttled(
                "relay-sync-failed",
                max(1.0, timeout_seconds),
                "Shelly relay state did not confirm to %s within %.1fs (actual=%s confirmed=%s)",
                expected_relay,
                timeout_seconds,
                bool(relay_on),
                int(bool(pm_confirmed)),
            )
        # A timed-out confirmation must not block a fresh retry of the same
        # relay target on the next decision cycle.
        self._clear_relay_sync_tracking(svc)
        return "relay-sync-failed"

    @staticmethod
    def _phase_switch_pause_seconds(svc: Any) -> float:
        """Return the minimum relay-off pause before a phase change is applied."""
        return max(0.0, float(getattr(svc, "phase_switch_pause_seconds", 1.0) or 1.0))

    @staticmethod
    def _phase_switch_stabilization_seconds(svc: Any) -> float:
        """Return the stabilization holdoff after one applied phase change."""
        return max(0.0, float(getattr(svc, "phase_switch_stabilization_seconds", 2.0) or 2.0))

    @staticmethod
    def _pending_phase_switch_selection(svc: Any) -> PhaseSelection | None:
        """Return the normalized pending phase selection when one is staged."""
        pending = getattr(svc, "_phase_switch_pending_selection", None)
        if pending is None:
            return None
        return normalize_phase_selection(pending, normalize_phase_selection("P1"))

    @classmethod
    def _clear_phase_switch_state(cls, svc: Any) -> None:
        """Clear the transient state used for staged phase switching."""
        svc._phase_switch_pending_selection = None
        svc._phase_switch_state = None
        svc._phase_switch_requested_at = None
        svc._phase_switch_stable_until = None
        svc._phase_switch_resume_relay = False

    def _resume_after_phase_switch_pause(
        self,
        svc: Any,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
    ) -> tuple[bool, float, float, bool]:
        """Resume charging best-effort after a staged phase change completes."""
        resume_relay = bool(getattr(svc, "_phase_switch_resume_relay", False))
        self._clear_phase_switch_state(svc)
        if not resume_relay:
            svc._save_runtime_state()
            return relay_on, power, current, pm_confirmed
        if auto_mode_active:
            svc._ignore_min_offtime_once = True
            svc._save_runtime_state()
            return relay_on, power, current, pm_confirmed
        try:
            self._apply_enabled_target(svc, True, now)
        except Exception as error:  # pylint: disable=broad-except
            source_key = self._enable_control_source_key(svc)
            source_label = self._enable_control_label(svc)
            svc._mark_failure(source_key)
            svc._warning_throttled(
                "phase-switch-resume-failed",
                svc.auto_shelly_soft_fail_seconds,
                "Failed to resume %s after phase switch: %s",
                source_label,
                error,
                exc_info=error,
            )
            svc._save_runtime_state()
            return relay_on, power, current, pm_confirmed
        relay_on = True
        power = 0.0
        current = 0.0
        pm_confirmed = False
        self._publish_local_pm_status_best_effort(True, now)
        svc._save_runtime_state()
        return relay_on, power, current, pm_confirmed

    def _abort_pending_phase_switch(
        self,
        svc: Any,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
        error: Exception,
    ) -> tuple[bool, float, float, bool]:
        """Abort one pending phase switch after an apply failure."""
        svc.requested_phase_selection = getattr(
            svc,
            "active_phase_selection",
            getattr(svc, "requested_phase_selection", "P1"),
        )
        svc._mark_failure("shelly")
        svc._warning_throttled(
            "phase-switch-apply-failed",
            svc.auto_shelly_soft_fail_seconds,
            "Failed to apply phase selection %s: %s",
            getattr(svc, "_phase_switch_pending_selection", None),
            error,
            exc_info=error,
        )
        return self._resume_after_phase_switch_pause(
            svc,
            relay_on,
            power,
            current,
            pm_confirmed,
            now,
            auto_mode_active,
        )

    def orchestrate_pending_phase_switch(
        self,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
    ) -> tuple[bool, float, float, bool, bool | None]:
        """Advance one staged phase switch and optionally override relay intent."""
        svc = self.service
        pending_selection = self._pending_phase_switch_selection(svc)
        switch_state = str(getattr(svc, "_phase_switch_state", "") or "")
        if pending_selection is None or switch_state not in {
            self.PHASE_SWITCH_WAITING_STATE,
            self.PHASE_SWITCH_STABILIZING_STATE,
        }:
            self._clear_phase_switch_state(svc)
            return relay_on, power, current, pm_confirmed, None

        pending_relay_state, _requested_at = svc._peek_pending_relay_command()
        if switch_state == self.PHASE_SWITCH_WAITING_STATE:
            if bool(relay_on) or pending_relay_state is not None or not pm_confirmed:
                return relay_on, power, current, pm_confirmed, False
            requested_at = getattr(svc, "_phase_switch_requested_at", None)
            pause_elapsed = (
                requested_at is None
                or (float(now) - float(requested_at)) >= self._phase_switch_pause_seconds(svc)
            )
            if not pause_elapsed:
                return relay_on, power, current, pm_confirmed, False
            try:
                applied_selection = svc._apply_phase_selection(pending_selection)
            except Exception as error:  # pylint: disable=broad-except
                relay_on, power, current, pm_confirmed = self._abort_pending_phase_switch(
                    svc,
                    relay_on,
                    power,
                    current,
                    pm_confirmed,
                    now,
                    auto_mode_active,
                    error,
                )
                return relay_on, power, current, pm_confirmed, None
            svc.requested_phase_selection = applied_selection
            svc.active_phase_selection = applied_selection
            svc._phase_switch_state = self.PHASE_SWITCH_STABILIZING_STATE
            svc._phase_switch_stable_until = float(now) + self._phase_switch_stabilization_seconds(svc)
            svc._save_runtime_state()
            return False, 0.0, 0.0, False, False

        stable_until = getattr(svc, "_phase_switch_stable_until", None)
        if stable_until is not None and float(now) < float(stable_until):
            return False, 0.0, 0.0, False, False

        relay_on, power, current, pm_confirmed = self._resume_after_phase_switch_pause(
            svc,
            relay_on,
            power,
            current,
            pm_confirmed,
            now,
            auto_mode_active,
        )
        return relay_on, power, current, pm_confirmed, None

    def apply_relay_decision(
        self,
        desired_relay: bool,
        relay_on: bool,
        pm_status: dict[str, Any],
        power: float,
        current: float,
        now: float,
        auto_mode_active: bool,
    ) -> tuple[bool, float, float, bool]:
        """Queue relay changes and update optimistic local Shelly state."""
        svc = self.service
        pm_confirmed = self._pm_status_confirmed(pm_status)
        if desired_relay == relay_on:
            return relay_on, power, current, pm_confirmed

        if getattr(svc, "_relay_sync_expected_state", None) == bool(desired_relay):
            return relay_on, power, current, pm_confirmed

        if auto_mode_active and svc.auto_audit_log:
            self.log_auto_relay_change(svc, desired_relay)

        try:
            self._apply_enabled_target(svc, desired_relay, now)
        except Exception as error:  # pylint: disable=broad-except
            source_key = self._enable_control_source_key(svc)
            source_label = self._enable_control_label(svc)
            svc._mark_failure(source_key)
            svc._warning_throttled(
                f"{source_key}-switch-failed",
                svc.auto_shelly_soft_fail_seconds,
                "%s switch request failed: %s",
                source_label,
                error,
                exc_info=error,
            )
            return relay_on, power, current, pm_confirmed

        relay_on = desired_relay
        power = 0.0
        current = 0.0
        self._publish_local_pm_status_best_effort(relay_on, now)
        return relay_on, power, current, False

    @classmethod
    def derive_status_code(
        cls,
        svc: Any,
        relay_on: bool,
        power: float,
        auto_mode_active: bool,
        now: float | None = None,
    ) -> int:
        """Translate relay/power state into the Venus EV charger status code."""
        if cls.charger_health_override(svc, now) == "charger-fault":
            svc._last_status_source = "charger-fault"
            svc._last_charger_fault_active = 1
            return 0

        svc._last_charger_fault_active = 0
        status_override = cls._charger_status_override(svc, auto_mode_active, now)
        if status_override is not None:
            status_code, status_source = status_override
            svc._last_status_source = status_source
            return int(status_code)
        enabled_state = cls._effective_enabled_state(svc, relay_on, now)
        if enabled_state and power >= svc.charging_threshold_watts:
            svc._last_status_source = "charging"
            return 2
        if enabled_state:
            svc._last_status_source = "enabled-idle"
            return int(svc.idle_status)
        svc._last_status_source = "auto-waiting" if auto_mode_active else "manual-off"
        return 4 if auto_mode_active else 6

    def publish_online_update(
        self,
        pm_status: dict[str, Any],
        status: int,
        energy_forward: float,
        relay_on: bool,
        power: float,
        voltage: float,
        now: float,
    ) -> bool:
        """Publish live measurements and derived runtime state for an online Shelly status."""
        svc = self.service
        resolved_power = self._fresh_charger_power_readback(svc, now)
        if resolved_power is None:
            resolved_power = float(power)
        resolved_current = self._fresh_charger_actual_current_readback(svc, now)
        if resolved_current is None:
            resolved_current = 0.0
        resolved_energy_forward = self._fresh_charger_energy_readback(svc, now)
        if resolved_energy_forward is None:
            resolved_energy_forward = float(energy_forward)

        phase_data = self._phase_data_for_pm_status(pm_status, resolved_power, voltage, resolved_current)
        total_current = self._total_phase_current(phase_data)
        if resolved_current > 0.0:
            total_current = float(resolved_current)

        changed = False
        changed |= svc._publish_live_measurements(resolved_power, voltage, total_current, phase_data, now)
        changed |= self.update_virtual_state(status, resolved_energy_forward, relay_on)
        return bool(changed)
