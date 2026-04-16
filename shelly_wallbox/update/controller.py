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
from typing import Any, cast


from shelly_wallbox.core.contracts import (
    normalize_learning_phase,
    normalize_learning_state,
    normalized_worker_snapshot,
    timestamp_age_within,
    timestamp_not_future,
)
from shelly_wallbox.update.learning import _UpdateCycleLearningMixin
from shelly_wallbox.update.relay import _UpdateCycleRelayMixin
from shelly_wallbox.update.state import _UpdateCycleStateMixin


class UpdateCycleController(
    _UpdateCycleStateMixin,
    _UpdateCycleRelayMixin,
    _UpdateCycleLearningMixin,
):
    """Encapsulate the periodic Shelly/Auto update pipeline."""

    LEARNED_POWER_STABLE_MIN_SAMPLES = 3
    LEARNED_POWER_STABLE_MIN_SECONDS = 15.0
    LEARNED_POWER_STABLE_TOLERANCE_WATTS = 150.0
    LEARNED_POWER_STABLE_TOLERANCE_RATIO = 0.08
    LEARNED_POWER_SIGNATURE_MISMATCH_SESSIONS = 2
    LEARNED_POWER_VOLTAGE_TOLERANCE_VOLTS = 10.0
    FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS = 1.0
    def __init__(self, service: Any, phase_values_func: Any, health_code_func: Any) -> None:
        self.service = service
        self._phase_values = phase_values_func
        self._health_code = health_code_func

    @staticmethod
    def _worker_pm_snapshot_data(
        worker_snapshot: dict[str, Any],
        now: float,
    ) -> tuple[dict[str, Any] | None, bool, float]:
        """Return normalized worker PM data plus confirmation and timestamp."""
        normalized_snapshot = normalized_worker_snapshot(worker_snapshot, now=now, clamp_future_timestamps=False)
        pm_status = normalized_snapshot.get("pm_status")
        if pm_status is None:
            return None, False, float(now)
        pm_status = dict(pm_status)
        pm_confirmed = bool(normalized_snapshot.get("pm_confirmed", False))
        snapshot_at = normalized_snapshot.get("pm_captured_at", normalized_snapshot.get("captured_at", now))
        return pm_status, pm_confirmed, float(now if snapshot_at is None else snapshot_at)

    @staticmethod
    def _remember_pm_snapshot(svc: Any, pm_status: dict[str, Any], snapshot_at: float, pm_confirmed: bool) -> None:
        """Persist the freshest known PM status for short read-soft-fail reuse."""
        remembered = dict(pm_status)
        remembered["_pm_confirmed"] = pm_confirmed
        svc._last_pm_status = remembered
        svc._last_pm_status_at = snapshot_at
        svc._last_pm_status_confirmed = pm_confirmed
        if pm_confirmed:
            svc._last_confirmed_pm_status = dict(remembered)
            svc._last_confirmed_pm_status_at = snapshot_at

    @staticmethod
    def _cached_pm_status_for_soft_fail(svc: Any, now: float, soft_fail_seconds: float) -> dict[str, Any] | None:
        """Return the last remembered PM status when it is still inside soft-fail budget."""
        if (
            svc._last_pm_status is None
            or svc._last_pm_status_at is None
            or not timestamp_age_within(
                svc._last_pm_status_at,
                now,
                soft_fail_seconds,
                future_tolerance_seconds=UpdateCycleController.FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS,
            )
        ):
            return None
        pm_status = dict(svc._last_pm_status)
        pm_status["_pm_confirmed"] = bool(getattr(svc, "_last_pm_status_confirmed", False))
        return pm_status

    @staticmethod
    def _direct_pm_snapshot_max_age_seconds(svc: Any) -> float:
        """Return the minimum freshness window for directly supplied worker PM snapshots."""
        candidates = [1.0]
        worker_poll_seconds = getattr(svc, "_worker_poll_interval_seconds", None)
        if worker_poll_seconds is not None:
            try:
                worker_poll_seconds = float(worker_poll_seconds)
            except (TypeError, ValueError):
                worker_poll_seconds = None
            if worker_poll_seconds is not None and worker_poll_seconds > 0:
                candidates.append(worker_poll_seconds * 2.0)
        return max(1.0, min(candidates))

    @staticmethod
    def resolve_pm_status_for_update(svc: Any, worker_snapshot: dict[str, Any], now: float) -> dict[str, Any] | None:
        """Return the freshest Shelly status, including short soft-fail reuse."""
        soft_fail_seconds = float(getattr(svc, "auto_shelly_soft_fail_seconds", 10.0))
        pm_status, pm_confirmed, snapshot_at = UpdateCycleController._worker_pm_snapshot_data(worker_snapshot, now)
        if pm_status is None:
            return UpdateCycleController._cached_pm_status_for_soft_fail(svc, now, soft_fail_seconds)
        pm_status["_pm_confirmed"] = pm_confirmed
        if UpdateCycleController._pm_snapshot_falls_back_to_cache(snapshot_at, now):
            return UpdateCycleController._cached_pm_status_for_soft_fail(svc, now, soft_fail_seconds)

        should_remember, within_soft_fail = UpdateCycleController._pm_snapshot_storage_decision(
            svc,
            now,
            snapshot_at,
            soft_fail_seconds,
        )
        if should_remember:
            UpdateCycleController._remember_pm_snapshot(svc, pm_status, snapshot_at, pm_confirmed)
        if within_soft_fail:
            return pm_status
        return UpdateCycleController._cached_pm_status_for_soft_fail(svc, now, soft_fail_seconds)

    @staticmethod
    def _pm_snapshot_from_future(snapshot_at: float, now: float) -> bool:
        """Return True when a worker PM snapshot timestamp lies implausibly in the future."""
        return not timestamp_not_future(
            snapshot_at,
            now,
            UpdateCycleController.FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS,
        )

    @staticmethod
    def _pm_snapshot_falls_back_to_cache(snapshot_at: float, now: float) -> bool:
        """Return True when a PM snapshot must immediately fall back to cached state."""
        return UpdateCycleController._pm_snapshot_from_future(snapshot_at, now)

    @staticmethod
    def _pm_snapshot_within_soft_fail_budget(
        svc: Any,
        now: float,
        snapshot_at: float,
        soft_fail_seconds: float,
    ) -> bool:
        """Return True when a PM snapshot is still usable before soft-fail fallback."""
        direct_snapshot_max_age = UpdateCycleController._direct_pm_snapshot_max_age_seconds(svc)
        return (float(now) - snapshot_at) <= max(soft_fail_seconds, direct_snapshot_max_age)

    @staticmethod
    def _pm_snapshot_newer_than_last(svc: Any, snapshot_at: float) -> bool:
        """Return True when a PM snapshot is at least as new as the stored one."""
        last_snapshot_at = getattr(svc, "_last_pm_status_at", None)
        return last_snapshot_at is None or snapshot_at >= float(last_snapshot_at)

    @staticmethod
    def _pm_snapshot_storage_decision(
        svc: Any,
        now: float,
        snapshot_at: float,
        soft_fail_seconds: float,
    ) -> tuple[bool, bool]:
        """Return whether to remember a PM snapshot and whether it stays directly usable."""
        within_soft_fail = UpdateCycleController._pm_snapshot_within_soft_fail_budget(
            svc,
            now,
            snapshot_at,
            soft_fail_seconds,
        )
        should_remember = within_soft_fail or UpdateCycleController._pm_snapshot_newer_than_last(svc, snapshot_at)
        return should_remember, within_soft_fail

    @staticmethod
    def _offline_confirmed_relay_max_age_seconds(svc: Any) -> float:
        """Return how old a confirmed relay sample may be for offline publishing."""
        candidates = [2.0]
        worker_poll_seconds = getattr(svc, "_worker_poll_interval_seconds", None)
        if worker_poll_seconds is not None and float(worker_poll_seconds) > 0:
            candidates.append(float(worker_poll_seconds) * 2.0)
        relay_sync_timeout_seconds = getattr(svc, "relay_sync_timeout_seconds", None)
        if relay_sync_timeout_seconds is not None and float(relay_sync_timeout_seconds) > 0:
            candidates.append(float(relay_sync_timeout_seconds))
        return max(1.0, min(candidates))

    @classmethod
    def _offline_confirmed_relay_state(cls, svc: Any, now: float) -> bool:
        """Return the last fresh confirmed relay state, defaulting to OFF when unknown."""
        raw_pm_status = getattr(svc, "_last_confirmed_pm_status", None)
        raw_captured_at = getattr(svc, "_last_confirmed_pm_status_at", None)
        if not cls._offline_confirmed_relay_sample_present(raw_pm_status, raw_captured_at):
            return False
        pm_status = raw_pm_status
        captured_at = raw_captured_at
        assert isinstance(pm_status, dict)
        assert captured_at is not None
        if not cls._offline_confirmed_relay_sample_fresh(svc, now, float(captured_at)):
            return False
        return bool(pm_status.get("output"))

    @staticmethod
    def _offline_confirmed_relay_sample_present(
        pm_status: Any,
        captured_at: float | None,
    ) -> bool:
        """Return True when offline publishing has one confirmed relay sample to inspect."""
        return isinstance(pm_status, dict) and "output" in pm_status and captured_at is not None

    @classmethod
    def _offline_confirmed_relay_sample_fresh(cls, svc: Any, now: float, captured_at: float) -> bool:
        """Return True when one confirmed relay sample is fresh enough for offline status."""
        max_age_seconds = cls._offline_confirmed_relay_max_age_seconds(svc)
        return bool(
            timestamp_age_within(
            captured_at,
            now,
            max_age_seconds,
            future_tolerance_seconds=cls.FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS,
            )
        )

    def publish_offline_update(self, now: float) -> bool:
        """Publish a disconnected Shelly state when no recent status is available."""
        svc = self.service
        voltage = self._offline_voltage(svc)
        offline_pm_status = self._fresh_offline_pm_status(svc, now)
        relay_on = self._offline_confirmed_relay_state(svc, now)
        power, energy_forward, status = self._offline_power_state()
        self._mark_offline_status_state(svc)
        phase_data = self._phase_data_for_pm_status(offline_pm_status, power, voltage, 0.0)
        svc._set_health("shelly-offline", cached=False)
        total_current = self._total_phase_current(phase_data)

        changed = self._publish_offline_live_state(
            svc,
            power=power,
            voltage=voltage,
            total_current=total_current,
            phase_data=phase_data,
            now=now,
            status=status,
            energy_forward=energy_forward,
            relay_on=relay_on,
        )
        if changed:
            svc._bump_update_index(now)
        svc.last_update = svc._time_now()
        return True

    @staticmethod
    def _offline_voltage(svc: Any) -> float:
        """Return the fallback voltage used for one offline publish."""
        return float(svc._last_voltage) if getattr(svc, "_last_voltage", None) else 230.0

    @staticmethod
    def _offline_confirmed_pm_status_timestamp(raw_timestamp: Any) -> float | None:
        """Return the numeric timestamp of the last confirmed PM status when valid."""
        if not isinstance(raw_timestamp, (int, float)) or isinstance(raw_timestamp, bool):
            return None
        return float(raw_timestamp)

    @classmethod
    def _fresh_offline_pm_status(cls, svc: Any, now: float) -> dict[str, Any] | None:
        """Return the last confirmed PM status only while it stays fresh for offline use."""
        offline_pm_status = getattr(svc, "_last_confirmed_pm_status", None)
        offline_pm_status_at = cls._offline_confirmed_pm_status_timestamp(
            getattr(svc, "_last_confirmed_pm_status_at", None)
        )
        if offline_pm_status_at is None:
            return None
        if not cls._offline_confirmed_relay_sample_present(offline_pm_status, offline_pm_status_at):
            return None
        if not cls._offline_confirmed_relay_sample_fresh(svc, now, offline_pm_status_at):
            return None
        return cast(dict[str, Any], offline_pm_status)

    @staticmethod
    def _offline_power_state() -> tuple[float, float, int]:
        """Return the fixed power/energy/status tuple used for offline publishing."""
        return 0.0, 0.0, 0

    @staticmethod
    def _mark_offline_status_state(svc: Any) -> None:
        """Mark service observability fields for one offline Shelly publish."""
        svc._last_status_source = "shelly-offline"
        svc._last_charger_fault_active = 0

    def _publish_offline_live_state(
        self,
        svc: Any,
        *,
        power: float,
        voltage: float,
        total_current: float,
        phase_data: dict[str, dict[str, float]],
        now: float,
        status: int,
        energy_forward: float,
        relay_on: bool,
    ) -> bool:
        """Publish one offline measurement set and matching virtual charger state."""
        changed = False
        changed |= svc._publish_live_measurements(power, voltage, total_current, phase_data, now)
        changed |= self.update_virtual_state(status, energy_forward, relay_on)
        return bool(changed)

    @staticmethod
    def extract_pm_measurements(svc: Any, pm_status: dict[str, Any]) -> tuple[bool, float, float, float, float]:
        """Extract normalized relay/power/current/energy values from a Shelly status dict."""
        relay_on = bool(pm_status.get("output", False))
        power = svc._safe_float(pm_status.get("apower", 0.0), 0.0)
        voltage = svc._safe_float(pm_status.get("voltage", 0.0), 0.0)
        current = svc._safe_float(pm_status.get("current", 0.0), 0.0)
        energy_forward = svc._safe_float(pm_status.get("aenergy", {}).get("total", 0.0), 0.0) / 1000.0
        return relay_on, power, voltage, current, energy_forward

    @staticmethod
    def resolve_cached_input_value(
        svc: Any,
        value: Any,
        snapshot_at: float | None,
        last_value_attr: str,
        last_at_attr: str,
        now: float,
        max_age_seconds: float | None = None,
    ) -> tuple[Any, bool]:
        """Use fresh input values immediately and short-lived cached values as fallback."""
        cache_max_age = float(svc.auto_input_cache_seconds)
        if max_age_seconds is not None:
            cache_max_age = min(cache_max_age, float(max_age_seconds))
        value, snapshot_at = UpdateCycleController._discard_invalid_snapshot_input(
            value,
            snapshot_at,
            now,
            max_age_seconds,
        )
        if value is not None:
            setattr(svc, last_value_attr, value)
            setattr(svc, last_at_attr, now if snapshot_at is None else float(snapshot_at))
            return value, False

        return UpdateCycleController._cached_input_from_service(svc, last_value_attr, last_at_attr, now, cache_max_age)

    @staticmethod
    def _discard_invalid_snapshot_input(
        value: Any,
        snapshot_at: float | None,
        now: float,
        max_age_seconds: float | None,
    ) -> tuple[Any, float | None]:
        """Drop future or over-age source values before cache fallback is considered."""
        if value is None or snapshot_at is None:
            return value, snapshot_at
        snapshot_time = float(snapshot_at)
        if UpdateCycleController._snapshot_input_from_future(snapshot_time, now):
            return None, None
        if UpdateCycleController._snapshot_input_too_old(snapshot_time, now, max_age_seconds):
            return None, None
        return value, snapshot_time

    @staticmethod
    def _snapshot_input_from_future(snapshot_time: float, now: float) -> bool:
        """Return True when one helper-fed source timestamp lies in the future."""
        return not timestamp_not_future(
            snapshot_time,
            now,
            UpdateCycleController.FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS,
        )

    @staticmethod
    def _snapshot_input_too_old(
        snapshot_time: float,
        now: float,
        max_age_seconds: float | None,
    ) -> bool:
        """Return True when one helper-fed source timestamp exceeds its max age."""
        return max_age_seconds is not None and (float(now) - snapshot_time) > float(max_age_seconds)

    @staticmethod
    def _cached_input_from_service(
        svc: Any,
        last_value_attr: str,
        last_at_attr: str,
        now: float,
        cache_max_age: float,
    ) -> tuple[Any, bool]:
        """Return a recent cached helper-fed value when direct input is unavailable."""
        last_value = getattr(svc, last_value_attr)
        last_at = getattr(svc, last_at_attr)
        if (
            last_value is not None
            and last_at is not None
            and last_at <= (float(now) + UpdateCycleController.FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS)
            and (now - last_at) <= cache_max_age
        ):
            return last_value, True
        return None, False

    @staticmethod
    def _auto_input_source_max_age_seconds(svc: Any, poll_interval_attr: str) -> float:
        """Return the maximum tolerated age for one helper-fed source value."""
        poll_interval_seconds = max(0.0, float(getattr(svc, poll_interval_attr, 0.0) or 0.0))
        validation_seconds = max(0.0, float(getattr(svc, "auto_input_validation_poll_seconds", 30.0) or 30.0))
        freshness_limit = validation_seconds if poll_interval_seconds <= 0.0 else min(
            validation_seconds,
            poll_interval_seconds * 2.0,
        )
        return max(1.0, freshness_limit)

    def resolve_auto_inputs(
        self,
        worker_snapshot: dict[str, Any],
        now: float,
        auto_mode_active: bool,
    ) -> tuple[Any, Any, Any]:
        """Resolve Auto inputs from helper snapshots with short cache fallback."""
        svc = self.service
        if not auto_mode_active:
            svc._auto_cached_inputs_used = False
            return None, None, None

        pv_power, pv_cached = self.resolve_cached_input_value(
            svc,
            worker_snapshot.get("pv_power"),
            worker_snapshot.get("pv_captured_at", worker_snapshot.get("captured_at")),
            "_last_pv_value",
            "_last_pv_at",
            now,
            max_age_seconds=self._auto_input_source_max_age_seconds(svc, "auto_pv_poll_interval_seconds"),
        )
        grid_power, grid_cached = self.resolve_cached_input_value(
            svc,
            worker_snapshot.get("grid_power"),
            worker_snapshot.get("grid_captured_at", worker_snapshot.get("captured_at")),
            "_last_grid_value",
            "_last_grid_at",
            now,
            max_age_seconds=self._auto_input_source_max_age_seconds(svc, "auto_grid_poll_interval_seconds"),
        )
        battery_soc, battery_cached = self.resolve_cached_input_value(
            svc,
            worker_snapshot.get("battery_soc"),
            worker_snapshot.get("battery_captured_at", worker_snapshot.get("captured_at")),
            "_last_battery_soc_value",
            "_last_battery_soc_at",
            now,
            max_age_seconds=self._auto_input_source_max_age_seconds(svc, "auto_battery_poll_interval_seconds"),
        )
        svc._auto_cached_inputs_used = pv_cached or grid_cached or battery_cached
        if svc._auto_cached_inputs_used:
            svc._error_state["cache_hits"] += 1
        return pv_power, battery_soc, grid_power

    @staticmethod
    def _normalize_learned_charge_power_state(value: Any) -> str:
        """Return one supported learned-power state string."""
        return str(normalize_learning_state(value))

    @staticmethod
    def _normalize_learned_charge_power_phase(value: Any) -> str | None:
        """Return one supported phase signature for a learned charging profile."""
        normalized = normalize_learning_phase(value)
        return None if normalized is None else str(normalized)

    @staticmethod
    def _set_learning_tracking(
        svc: Any,
        *,
        state: str,
        learned_power: float | None,
        updated_at: float | None,
        learning_since: float | None,
        sample_count: int,
        phase_signature: str | None,
        voltage_signature: float | None,
        signature_mismatch_sessions: int,
        checked_session_started_at: float | None,
    ) -> bool:
        """Apply one coherent learned-power snapshot and report whether it changed."""
        normalized = UpdateCycleController._normalized_learning_tracking_values(
            state=state,
            learned_power=learned_power,
            updated_at=updated_at,
            learning_since=learning_since,
            sample_count=sample_count,
            phase_signature=phase_signature,
            voltage_signature=voltage_signature,
            signature_mismatch_sessions=signature_mismatch_sessions,
            checked_session_started_at=checked_session_started_at,
        )
        attr_map = {
            "state": "learned_charge_power_state",
            "power": "learned_charge_power_watts",
            "updated_at": "learned_charge_power_updated_at",
            "learning_since": "learned_charge_power_learning_since",
            "sample_count": "learned_charge_power_sample_count",
            "phase_signature": "learned_charge_power_phase",
            "voltage_signature": "learned_charge_power_voltage",
            "signature_mismatch_sessions": "learned_charge_power_signature_mismatch_sessions",
            "checked_session_started_at": "learned_charge_power_signature_checked_session_started_at",
        }
        changed = False
        for key, attr_name in attr_map.items():
            if getattr(svc, attr_name, None) != normalized[key]:
                changed = True
            setattr(svc, attr_name, normalized[key])
        return changed

    @staticmethod
    def _normalized_learning_power_value(value: Any) -> float | None:
        """Return the normalized learned charging power in watts."""
        return None if value is None else round(float(value), 1)

    @staticmethod
    def _normalized_learning_timestamp(value: Any) -> float | None:
        """Return one normalized learned-power timestamp."""
        return None if value is None else float(value)

    @staticmethod
    def _normalized_learning_count(value: Any) -> int:
        """Return one normalized non-negative learning counter."""
        return max(0, int(value))

    @staticmethod
    def _normalized_learning_tracking_values(**values: Any) -> dict[str, Any]:
        """Normalize one coherent learned-power snapshot before storing it on the service."""
        return {
            "state": UpdateCycleController._normalize_learned_charge_power_state(values["state"]),
            "power": UpdateCycleController._normalized_learning_power_value(values["learned_power"]),
            "updated_at": UpdateCycleController._normalized_learning_timestamp(values["updated_at"]),
            "learning_since": UpdateCycleController._normalized_learning_timestamp(values["learning_since"]),
            "sample_count": UpdateCycleController._normalized_learning_count(values["sample_count"]),
            "phase_signature": UpdateCycleController._normalize_learned_charge_power_phase(values["phase_signature"]),
            "voltage_signature": UpdateCycleController._normalized_learning_power_value(values["voltage_signature"]),
            "signature_mismatch_sessions": UpdateCycleController._normalized_learning_count(
                values["signature_mismatch_sessions"]
            ),
            "checked_session_started_at": UpdateCycleController._normalized_learning_timestamp(
                values["checked_session_started_at"]
            ),
        }

    @classmethod
    def _learning_stability_tolerance(cls, reference_power: float) -> float:
        """Return the allowed measurement deviation before learning restarts."""
        return max(
            float(cls.LEARNED_POWER_STABLE_TOLERANCE_WATTS),
            abs(float(reference_power)) * float(cls.LEARNED_POWER_STABLE_TOLERANCE_RATIO),
        )

    @staticmethod
    def _learning_phase_count(phase: str) -> float:
        """Return the configured number of charging phases for plausibility checks."""
        return 3.0 if str(phase).strip().upper() == "3P" else 1.0

    def _current_learning_phase_signature(self) -> str | None:
        """Return the configured phase signature used for learned-power validation."""
        return self._normalize_learned_charge_power_phase(getattr(self.service, "phase", "L1"))

    def _current_learning_voltage_signature(self, voltage: float) -> float | None:
        """Return the best current voltage signature for learned-power tracking."""
        if float(voltage) > 0:
            return float(voltage)
        last_voltage = getattr(self.service, "_last_voltage", None)
        if last_voltage is None or float(last_voltage) <= 0:
            return None
        return float(last_voltage)

    @classmethod
    def _voltage_signature_tolerance(cls, reference_voltage: float) -> float:
        """Return the allowed voltage drift before a learned signature counts as changed."""
        return max(
            float(cls.LEARNED_POWER_VOLTAGE_TOLERANCE_VOLTS),
            abs(float(reference_voltage)) * float(cls.LEARNED_POWER_STABLE_TOLERANCE_RATIO),
        )

    def _plausible_learning_power_max(self, voltage: float) -> float:
        """Return a conservative upper bound for a valid charging-power sample."""
        svc = self.service
        effective_voltage = float(voltage) if float(voltage) > 0 else float(getattr(svc, "_last_voltage", 230.0) or 230.0)
        if self._learning_phase_count(getattr(svc, "phase", "L1")) == 3.0 and str(
            getattr(svc, "voltage_mode", "phase")
        ).strip().lower() != "phase":
            effective_voltage = effective_voltage / math.sqrt(3.0)
        phase_count = self._learning_phase_count(getattr(svc, "phase", "L1"))
        max_current = max(float(getattr(svc, "max_current", 16.0)), 0.0)
        # Allow modest sensor drift while still rejecting obviously impossible spikes.
        return max_current * effective_voltage * phase_count * 1.1

    def _is_learned_charge_power_stale(self, now: float) -> bool:
        """Return True when the persisted learned value is too old for reuse."""
        svc = self.service
        max_age_seconds = float(getattr(svc, "auto_learn_charge_power_max_age_seconds", 21600.0))
        if max_age_seconds <= 0:
            return False
        updated_at = getattr(svc, "learned_charge_power_updated_at", None)
        if updated_at is None:
            return True
        return (float(now) - float(updated_at)) > max_age_seconds

    @staticmethod
    def complete_update_cycle(
        svc: Any,
        changed: bool,
        now: float,
        relay_on: bool,
        power: float,
        current: float,
        status: int,
        pv_power: Any,
        battery_soc: Any,
        grid_power: Any,
    ) -> None:
        """Finalize a successful update cycle and log the current state."""
        if changed:
            svc._bump_update_index(now)
        completed_at = svc._time_now()
        svc._last_successful_update_at = completed_at
        svc._last_recovery_attempt_at = None
        svc.last_update = completed_at
        logging.debug(
            "Wallbox relay=%s power=%sW current=%sA status=%s pv=%sW soc=%s%% grid=%sW mode=%s",
            relay_on,
            power,
            current,
            status,
            pv_power,
            battery_soc,
            grid_power,
            svc.virtual_mode,
        )

    def sign_of_life(self) -> bool:
        """Periodic heartbeat log for troubleshooting."""
        svc = self.service
        logging.info("[%s] Last '/Ac/Power': %s", svc.service_name, svc._dbusservice["/Ac/Power"])
        return True

    def update(self) -> bool:
        """Periodic update loop: read Shelly, compute auto logic, update DBus."""
        svc = self.service
        try:
            return self._run_update_cycle()
        except Exception as error:  # pylint: disable=broad-except
            logging.warning(
                "Error updating Shelly wallbox data: %s (%s)",
                error,
                svc._state_summary(),
                exc_info=error,
            )
        return True

    def _run_update_cycle(self) -> bool:
        """Execute one full update cycle and report whether the loop should continue."""
        svc = self.service
        now = svc._time_now()
        worker_snapshot = self.prepare_update_cycle(svc, now)
        pm_status = self.resolve_pm_status_for_update(svc, worker_snapshot, now)
        if pm_status is None:
            return self.publish_offline_update(now)
        self._run_online_update_cycle(pm_status, worker_snapshot, now)
        return True

    def _run_online_update_cycle(
        self,
        pm_status: dict[str, Any],
        worker_snapshot: dict[str, Any],
        now: float,
    ) -> None:
        """Execute the online portion of one update cycle."""
        (
            pm_status,
            relay_on,
            power,
            voltage,
            current,
            energy_forward,
            pm_confirmed,
            auto_mode_active,
        ) = self._prepared_online_update_state(pm_status, now)
        learning_state_changed = self._refresh_learning_before_decision(
            relay_on,
            power,
            voltage,
            now,
            pm_confirmed,
        )
        pv_power, battery_soc, grid_power = self.resolve_auto_inputs(worker_snapshot, now, auto_mode_active)
        relay_on, power, current, pm_confirmed, desired_relay, charger_health = self._resolved_relay_decision(
            pm_status,
            relay_on,
            power,
            voltage,
            current,
            pm_confirmed,
            now,
            auto_mode_active,
            pv_power,
            battery_soc,
            grid_power,
        )
        relay_on, power, current, relay_confirmed = self.apply_relay_decision(
            desired_relay,
            relay_on,
            pm_status,
            power,
            current,
            now,
            auto_mode_active,
        )
        effective_power, status = self._status_after_relay_decision(
            relay_on,
            power,
            auto_mode_active,
            charger_health,
            now,
        )
        self._apply_post_decision_health(relay_on, relay_confirmed, now, charger_health)
        changed = self.publish_online_update(pm_status, status, energy_forward, relay_on, power, voltage, now)
        learning_updated = self.update_learned_charge_power(
            relay_on,
            status,
            effective_power,
            voltage,
            now,
            pm_confirmed=relay_confirmed,
        )
        if learning_state_changed or learning_updated:
            self.service._save_runtime_state()
        self.complete_update_cycle(
            self.service,
            changed,
            now,
            relay_on,
            power,
            current,
            status,
            pv_power,
            battery_soc,
            grid_power,
        )

    def _resolved_relay_decision(
        self,
        pm_status: dict[str, Any],
        relay_on: bool,
        power: float,
        voltage: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
        pv_power: Any,
        battery_soc: Any,
        grid_power: Any,
    ) -> tuple[bool, float, float, bool, bool, str | None]:
        """Return relay-state context plus the desired relay target for this cycle."""
        svc = self.service
        relay_on, power, current, pm_confirmed, phase_switch_override = self.orchestrate_pending_phase_switch(
            pm_status,
            relay_on,
            power,
            current,
            pm_confirmed,
            now,
            auto_mode_active,
        )
        desired_relay = self._desired_relay_target(
            svc,
            relay_on,
            phase_switch_override,
            pv_power,
            battery_soc,
            grid_power,
        )
        switch_health = self._blocking_switch_feedback_health(
            desired_relay,
            relay_on,
            power,
            current,
            pm_confirmed,
            now,
        )
        if switch_health is not None:
            desired_relay = False
        charger_health = self._blocking_charger_health(desired_relay, relay_on, now)
        if charger_health is not None:
            desired_relay = False
        phase_override = self.maybe_apply_auto_phase_selection(
            svc,
            desired_relay,
            relay_on,
            voltage,
            now,
            auto_mode_active,
        )
        if phase_override is not None:
            desired_relay = bool(phase_override)
        self.apply_charger_current_target(svc, desired_relay, now, auto_mode_active)
        return relay_on, power, current, pm_confirmed, desired_relay, (switch_health or charger_health)

    @staticmethod
    def _desired_relay_target(
        svc: Any,
        relay_on: bool,
        phase_switch_override: bool | None,
        pv_power: Any,
        battery_soc: Any,
        grid_power: Any,
    ) -> bool:
        """Return the desired relay state before charger-health overrides are applied."""
        if phase_switch_override is not None:
            return bool(phase_switch_override)
        return bool(svc._auto_decide_relay(relay_on, pv_power, battery_soc, grid_power))

    def _blocking_charger_health(self, desired_relay: bool, relay_on: bool, now: float) -> str | None:
        """Return a charger-health override and emit one warning when it blocks charging."""
        svc = self.service
        charger_health = self.charger_health_override(svc, now)
        if charger_health is None:
            return None
        if bool(desired_relay) or bool(relay_on):
            svc._warning_throttled(
                "charger-health-blocking",
                svc.auto_shelly_soft_fail_seconds,
                "Native charger health override %s blocks charging (status=%s fault=%s)",
                charger_health,
                getattr(svc, "_last_charger_state_status", None),
                getattr(svc, "_last_charger_state_fault", None),
            )
        return charger_health

    def _blocking_switch_feedback_health(
        self,
        desired_relay: bool,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
    ) -> str | None:
        """Return one switch-feedback override and emit one warning when it blocks charging."""
        svc = self.service
        switch_health = self.switch_feedback_health_override(
            svc,
            desired_relay,
            relay_on,
            now,
            power=power,
            current=current,
            pm_confirmed=pm_confirmed,
        )
        if switch_health is None:
            return None
        if switch_health == "contactor-interlock":
            svc._warning_throttled(
                "switch-interlock-blocking",
                svc.auto_shelly_soft_fail_seconds,
                "Switch interlock blocks charging (desired=%s relay=%s interlock_ok=%s)",
                int(bool(desired_relay)),
                int(bool(relay_on)),
                getattr(svc, "_last_switch_interlock_ok", None),
            )
            return switch_health
        if switch_health == "contactor-suspected-open":
            svc._warning_throttled(
                "switch-suspected-open-blocking",
                svc.auto_shelly_soft_fail_seconds,
                "Contactor heuristics suspect OPEN state (relay=%s power=%.1f current=%.1f charger_status=%s)",
                int(bool(relay_on)),
                float(power),
                float(current),
                getattr(svc, "_last_charger_state_status", None),
            )
            return switch_health
        if switch_health == "contactor-suspected-welded":
            svc._warning_throttled(
                "switch-suspected-welded-blocking",
                svc.auto_shelly_soft_fail_seconds,
                "Contactor heuristics suspect WELDED state (relay=%s power=%.1f current=%.1f)",
                int(bool(relay_on)),
                float(power),
                float(current),
            )
            return switch_health
        if switch_health == "contactor-lockout-open":
            svc._warning_throttled(
                "switch-lockout-open-blocking",
                svc.auto_shelly_soft_fail_seconds,
                "Latched contactor OPEN lockout blocks charging (count=%s source=%s)",
                int(getattr(svc, "_contactor_fault_counts", {}).get("contactor-suspected-open", 0)),
                getattr(svc, "_contactor_lockout_source", ""),
            )
            return switch_health
        if switch_health == "contactor-lockout-welded":
            svc._warning_throttled(
                "switch-lockout-welded-blocking",
                svc.auto_shelly_soft_fail_seconds,
                "Latched contactor WELDED lockout blocks charging (count=%s source=%s)",
                int(getattr(svc, "_contactor_fault_counts", {}).get("contactor-suspected-welded", 0)),
                getattr(svc, "_contactor_lockout_source", ""),
            )
            return switch_health
        svc._warning_throttled(
            "switch-feedback-blocking",
            svc.auto_shelly_soft_fail_seconds,
            "Switch feedback mismatch blocks charging (relay=%s feedback_closed=%s)",
            int(bool(relay_on)),
            getattr(svc, "_last_switch_feedback_closed", None),
        )
        return switch_health

    def _status_after_relay_decision(
        self,
        relay_on: bool,
        power: float,
        auto_mode_active: bool,
        health_reason: str | None,
        now: float,
    ) -> tuple[float, int]:
        """Return effective power and derived Venus status after relay application."""
        svc = self.service
        effective_power = self._fresh_charger_power_readback(svc, now)
        if effective_power is None:
            effective_power = power
        status = self.derive_status_code(
            svc,
            relay_on,
            effective_power,
            auto_mode_active,
            health_reason=health_reason,
            now=now,
        )
        return effective_power, status

    def _apply_post_decision_health(
        self,
        relay_on: bool,
        relay_confirmed: bool,
        now: float,
        charger_health: str | None,
    ) -> None:
        """Apply relay-sync or charger-derived health after one relay decision."""
        relay_sync_health = self._apply_relay_sync_health(relay_on, relay_confirmed, now)
        if relay_sync_health is None and charger_health is not None:
            self.service._set_health(charger_health, cached=False)

    def _prepared_online_update_state(
        self,
        pm_status: dict[str, Any],
        now: float,
    ) -> tuple[dict[str, Any], bool, float, float, float, float, bool, bool]:
        """Return normalized online-update state after startup target handling."""
        svc = self.service
        relay_on, power, voltage, current, energy_forward = self.extract_pm_measurements(svc, pm_status)
        pm_status = self.apply_startup_manual_target(pm_status, now)
        relay_on, power, voltage, current, energy_forward = self.extract_pm_measurements(svc, pm_status)
        pm_confirmed = self._pm_status_confirmed(pm_status)
        if voltage > 0.0:
            svc._last_voltage = voltage
        auto_mode_active = svc._mode_uses_auto_logic(svc.virtual_mode)
        return pm_status, relay_on, power, voltage, current, energy_forward, pm_confirmed, auto_mode_active

    def _refresh_learning_before_decision(
        self,
        relay_on: bool,
        power: float,
        voltage: float,
        now: float,
        pm_confirmed: bool,
    ) -> bool:
        """Refresh learned-power state before Auto decides on relay changes."""
        learning_state_changed = self.refresh_learned_charge_power_state(now)
        learning_state_changed |= self.reconcile_learned_charge_power_signature(
            relay_on,
            power,
            voltage,
            now,
            pm_confirmed=pm_confirmed,
        )
        return bool(learning_state_changed)

    def _apply_relay_sync_health(self, relay_on: bool, relay_confirmed: bool, now: float) -> str | None:
        """Publish one relay-sync health override when needed and return the applied reason."""
        relay_sync_health = self.relay_sync_health_override(
            relay_on,
            relay_confirmed,
            now,
        )
        if relay_sync_health is not None:
            self.service._set_health(relay_sync_health, cached=False)
        return relay_sync_health
