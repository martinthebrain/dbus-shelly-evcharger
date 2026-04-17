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
from datetime import datetime
from typing import Any, cast

from shelly_wallbox.backend.models import (
    PhaseSelection,
    normalize_phase_selection,
    switch_feedback_mismatch,
)
from shelly_wallbox.backend.modbus_transport import modbus_transport_issue_reason
from shelly_wallbox.core.common import (
    _charger_transport_health_reason,
    _charger_transport_retry_delay_seconds,
    _fresh_charger_retry_reason,
    _fresh_charger_retry_until,
    _fresh_charger_transport_reason,
    evse_fault_reason,
    fresh_confirmed_relay_output,
    mode_uses_scheduled_logic,
    scheduled_mode_snapshot,
)
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
    def _phase_selection_count(selection: object) -> int:
        """Return the normalized phase count represented by one phase selection."""
        normalized = normalize_phase_selection(selection, "P1")
        if normalized == "P1_P2_P3":
            return 3
        if normalized == "P1_P2":
            return 2
        return 1

    @classmethod
    def _phase_selection_is_upshift(
        cls,
        current_selection: PhaseSelection,
        target_selection: PhaseSelection,
    ) -> bool:
        """Return whether one target selection increases the active phase count."""
        return cls._phase_selection_count(target_selection) > cls._phase_selection_count(current_selection)

    @classmethod
    def _ordered_auto_phase_selections(cls, svc: Any) -> tuple[PhaseSelection, ...]:
        """Return supported phase selections ordered from the smallest to the largest layout."""
        raw_supported = tuple(getattr(svc, "supported_phase_selections", ("P1",)))
        ordered = cast(
            tuple[PhaseSelection, ...],
            tuple(
                sorted(
                    {normalize_phase_selection(selection, "P1") for selection in raw_supported},
                    key=cls._phase_selection_count,
                )
            ),
        )
        return ordered or ("P1",)

    @classmethod
    def _current_phase_selection(
        cls,
        svc: Any,
        supported: tuple[PhaseSelection, ...],
    ) -> PhaseSelection:
        """Return the current phase selection clamped into the supported set."""
        default_selection = supported[0]
        requested = normalize_phase_selection(getattr(svc, "requested_phase_selection", default_selection), default_selection)
        if requested in supported:
            return requested
        active = normalize_phase_selection(getattr(svc, "active_phase_selection", default_selection), default_selection)
        return active if active in supported else default_selection

    @staticmethod
    def _auto_phase_policy(svc: Any) -> Any | None:
        """Return the structured Auto phase policy when present."""
        auto_policy = getattr(svc, "auto_policy", None)
        if auto_policy is None:
            return None
        return getattr(auto_policy, "phase", None)

    @staticmethod
    def _auto_phase_metrics(svc: Any) -> dict[str, Any]:
        """Return the mutable Auto metrics mapping used for diagnostics."""
        metrics = getattr(svc, "_last_auto_metrics", None)
        if isinstance(metrics, dict):
            return metrics
        metrics = {}
        svc._last_auto_metrics = metrics
        return metrics

    @classmethod
    def _record_auto_phase_metrics(
        cls,
        svc: Any,
        *,
        current_selection: PhaseSelection,
        target_selection: PhaseSelection | None,
        phase_reason: str,
        threshold_watts: float | None,
    ) -> None:
        """Store the latest Auto phase-decision trace in the shared metrics mapping."""
        metrics = cls._auto_phase_metrics(svc)
        metrics["phase_current"] = current_selection
        metrics["phase_target"] = target_selection
        metrics["phase_reason"] = phase_reason
        metrics["phase_threshold_watts"] = threshold_watts
        metrics["phase_candidate"] = getattr(svc, "_auto_phase_target_candidate", None)
        metrics["phase_candidate_since"] = finite_float_or_none(getattr(svc, "_auto_phase_target_since", None))

    @staticmethod
    def _auto_phase_metric_surplus_watts(svc: Any) -> float | None:
        """Return the averaged Auto surplus metric used for phase decisions."""
        metrics = getattr(svc, "_last_auto_metrics", None)
        if not isinstance(metrics, dict):
            return None
        return finite_float_or_none(metrics.get("surplus"))

    @classmethod
    def _phase_selection_voltage(
        cls,
        svc: Any,
        selection: PhaseSelection,
        voltage: float,
    ) -> float | None:
        """Return the effective per-phase voltage for one phase layout."""
        phase_voltage = cls._phase_voltage(voltage, selection, getattr(svc, "voltage_mode", "phase"))
        return None if phase_voltage <= 0.0 else float(phase_voltage)

    @classmethod
    def _phase_selection_min_surplus_watts(
        cls,
        svc: Any,
        selection: PhaseSelection,
        voltage: float,
    ) -> float | None:
        """Return the minimum power needed to sustain one phase selection at MinCurrent."""
        min_current = finite_float_or_none(getattr(svc, "min_current", None))
        phase_voltage = cls._phase_selection_voltage(svc, selection, voltage)
        if min_current is None or min_current <= 0.0 or phase_voltage is None:
            return None
        return float(min_current) * phase_voltage * float(cls._phase_selection_count(selection))

    @classmethod
    def _auto_phase_target_selection(
        cls,
        svc: Any,
        supported: tuple[PhaseSelection, ...],
        current_selection: PhaseSelection,
        desired_relay: bool,
        relay_on: bool,
        voltage: float,
        now: float,
    ) -> tuple[PhaseSelection | None, str, float | None]:
        """Return the next phase-selection target plus the reason that chose it."""
        policy_state = cls._auto_phase_policy_state(svc, supported)
        if policy_state is not None:
            return policy_state

        phase_policy = cls._auto_phase_policy(svc)
        assert phase_policy is not None
        idle_target = cls._idle_auto_phase_target(
            phase_policy,
            supported,
            current_selection,
            desired_relay,
            relay_on,
        )
        if idle_target is not None:
            return idle_target
        return cls._surplus_auto_phase_target(
            svc,
            phase_policy,
            supported,
            current_selection,
            voltage,
            now,
        )

    @classmethod
    def _surplus_auto_phase_target(
        cls,
        svc: Any,
        phase_policy: Any,
        supported: tuple[PhaseSelection, ...],
        current_selection: PhaseSelection,
        voltage: float,
        now: float,
    ) -> tuple[PhaseSelection | None, str, float | None]:
        """Return one surplus-driven phase target once idle-mode shortcuts are excluded."""
        surplus_watts = cls._auto_phase_metric_surplus_watts(svc)
        if surplus_watts is None:
            return None, "phase-surplus-missing", None
        current_index = supported.index(current_selection)
        upshift_target = cls._upshift_auto_phase_target(
            svc,
            phase_policy,
            supported,
            current_index,
            current_selection,
            surplus_watts,
            voltage,
            now,
        )
        if upshift_target is not None:
            return upshift_target
        downshift_target = cls._downshift_auto_phase_target(
            svc,
            phase_policy,
            supported,
            current_selection,
            current_index,
            surplus_watts,
            voltage,
        )
        if downshift_target is not None:
            return downshift_target
        return None, "phase-hold", None

    @classmethod
    def _auto_phase_policy_state(
        cls,
        svc: Any,
        supported: tuple[PhaseSelection, ...],
    ) -> tuple[PhaseSelection | None, str, float | None] | None:
        """Return one terminal result when phase policy is disabled or unsupported."""
        phase_policy = cls._auto_phase_policy(svc)
        if phase_policy is None or not bool(getattr(phase_policy, "enabled", True)):
            return None, "phase-policy-disabled", None
        if len(supported) <= 1:
            return None, "single-phase-only", None
        return None

    @staticmethod
    def _idle_auto_phase_target(
        phase_policy: Any,
        supported: tuple[PhaseSelection, ...],
        current_selection: PhaseSelection,
        desired_relay: bool,
        relay_on: bool,
    ) -> tuple[PhaseSelection | None, str, float | None] | None:
        """Return the idle/off target when Auto prefers a low start phase."""
        if desired_relay or relay_on:
            return None
        lowest_selection = supported[0]
        if bool(getattr(phase_policy, "prefer_lowest_phase_when_idle", True)) and current_selection != lowest_selection:
            return lowest_selection, "idle-lowest-phase", None
        return None, "idle-hold-phase", None

    @classmethod
    def _upshift_auto_phase_target(
        cls,
        svc: Any,
        phase_policy: Any,
        supported: tuple[PhaseSelection, ...],
        current_index: int,
        current_selection: PhaseSelection,
        surplus_watts: float,
        voltage: float,
        now: float,
    ) -> tuple[PhaseSelection | None, str, float | None] | None:
        """Return the next larger phase layout when surplus clears the upshift threshold."""
        if current_index >= (len(supported) - 1):
            return None
        next_selection = supported[current_index + 1]
        next_min_surplus = cls._phase_selection_min_surplus_watts(svc, next_selection, voltage)
        if next_min_surplus is None:
            return None
        threshold = next_min_surplus + float(getattr(phase_policy, "upshift_headroom_watts", 250.0))
        if surplus_watts < threshold:
            return None
        if cls._phase_switch_lockout_active(svc, now, next_selection):
            return None, "phase-upshift-blocked-lockout", threshold
        if cls._phase_switch_mismatch_retry_active(svc, current_selection, next_selection, now):
            return None, "phase-upshift-blocked-mismatch", threshold
        return next_selection, "phase-upshift", threshold

    @classmethod
    def _phase_switch_mismatch_retry_active(
        cls,
        svc: Any,
        current_selection: PhaseSelection,
        target_selection: PhaseSelection,
        now: float,
    ) -> bool:
        """Return whether one recent mismatch should temporarily suppress this upshift."""
        if not cls._phase_selection_is_upshift(current_selection, target_selection):
            return False
        mismatch_selection = getattr(svc, "_phase_switch_last_mismatch_selection", None)
        if mismatch_selection is None:
            return False
        normalized_mismatch_selection = normalize_phase_selection(mismatch_selection, "P1")
        if normalized_mismatch_selection != target_selection:
            return False
        mismatch_at = finite_float_or_none(getattr(svc, "_phase_switch_last_mismatch_at", None))
        if mismatch_at is None:
            return False
        retry_seconds = cls._phase_switch_mismatch_retry_seconds(svc)
        if retry_seconds <= 0.0:
            return False
        elapsed_seconds = max(0.0, float(now) - mismatch_at)
        return elapsed_seconds < retry_seconds

    @classmethod
    def _phase_switch_mismatch_retry_seconds(cls, svc: Any) -> float:
        """Return the configured cooldown before retrying one mismatching upshift."""
        phase_policy = cls._auto_phase_policy(svc)
        if phase_policy is not None:
            return max(0.0, float(getattr(phase_policy, "mismatch_retry_seconds", 300.0)))
        return max(0.0, float(getattr(svc, "auto_phase_mismatch_retry_seconds", 300.0)))

    @classmethod
    def _phase_switch_lockout_threshold(cls, svc: Any) -> int:
        """Return how many confirmed mismatches trigger one longer lockout."""
        phase_policy = cls._auto_phase_policy(svc)
        if phase_policy is not None:
            return max(0, int(getattr(phase_policy, "mismatch_lockout_count", 3)))
        return max(0, int(getattr(svc, "auto_phase_mismatch_lockout_count", 3)))

    @classmethod
    def _phase_switch_lockout_seconds(cls, svc: Any) -> float:
        """Return the duration of the longer lockout after repeated mismatches."""
        phase_policy = cls._auto_phase_policy(svc)
        if phase_policy is not None:
            return max(0.0, float(getattr(phase_policy, "mismatch_lockout_seconds", 1800.0)))
        return max(0.0, float(getattr(svc, "auto_phase_mismatch_lockout_seconds", 1800.0)))

    @staticmethod
    def _phase_switch_mismatch_counts(svc: Any) -> dict[str, int]:
        """Return the mutable mismatch counter mapping keyed by target phase selection."""
        counts = getattr(svc, "_phase_switch_mismatch_counts", None)
        if isinstance(counts, dict):
            return cast(dict[str, int], counts)
        counts = {}
        svc._phase_switch_mismatch_counts = counts
        return counts

    @classmethod
    def _phase_switch_mismatch_count(cls, svc: Any, selection: PhaseSelection) -> int:
        """Return the confirmed mismatch count for one target selection."""
        return max(0, int(cls._phase_switch_mismatch_counts(svc).get(selection, 0)))

    @classmethod
    def _remember_phase_switch_mismatch(cls, svc: Any, selection: PhaseSelection, now: float) -> int:
        """Persist one newly confirmed mismatch attempt and return its updated count."""
        counts = cls._phase_switch_mismatch_counts(svc)
        next_count = cls._phase_switch_mismatch_count(svc, selection) + 1
        counts[selection] = next_count
        svc._phase_switch_mismatch_active = True
        svc._phase_switch_last_mismatch_selection = selection
        svc._phase_switch_last_mismatch_at = float(now)
        return next_count

    @classmethod
    def _clear_phase_switch_mismatch_tracking(
        cls,
        svc: Any,
        selection: PhaseSelection | None = None,
    ) -> None:
        """Clear remembered mismatch state globally or for one specific target selection."""
        svc._phase_switch_mismatch_active = False
        if selection is None:
            svc._phase_switch_mismatch_counts = {}
            svc._phase_switch_last_mismatch_selection = None
            svc._phase_switch_last_mismatch_at = None
            return
        counts = cls._phase_switch_mismatch_counts(svc)
        counts.pop(selection, None)
        if getattr(svc, "_phase_switch_last_mismatch_selection", None) == selection:
            svc._phase_switch_last_mismatch_selection = None
            svc._phase_switch_last_mismatch_at = None

    @staticmethod
    def _clear_phase_switch_lockout(svc: Any) -> None:
        """Clear one active phase-switch lockout."""
        svc._phase_switch_lockout_selection = None
        svc._phase_switch_lockout_reason = ""
        svc._phase_switch_lockout_at = None
        svc._phase_switch_lockout_until = None

    @classmethod
    def _engage_phase_switch_lockout(
        cls,
        svc: Any,
        selection: PhaseSelection,
        now: float,
    ) -> None:
        """Record one longer phase-switch lockout for a repeatedly failing target."""
        duration_seconds = cls._phase_switch_lockout_seconds(svc)
        if duration_seconds <= 0.0:
            cls._clear_phase_switch_lockout(svc)
            return
        svc._phase_switch_lockout_selection = selection
        svc._phase_switch_lockout_reason = "mismatch-threshold"
        svc._phase_switch_lockout_at = float(now)
        svc._phase_switch_lockout_until = float(now) + duration_seconds

    @classmethod
    def _phase_switch_lockout_active(
        cls,
        svc: Any,
        now: float,
        selection: PhaseSelection | None = None,
    ) -> bool:
        """Return whether one phase-switch lockout is active, optionally for one target."""
        lockout_selection = getattr(svc, "_phase_switch_lockout_selection", None)
        lockout_until = finite_float_or_none(getattr(svc, "_phase_switch_lockout_until", None))
        if lockout_selection is None or lockout_until is None:
            return False
        if float(now) >= lockout_until:
            cls._clear_phase_switch_lockout(svc)
            return False
        normalized_selection = normalize_phase_selection(lockout_selection, "P1")
        return selection is None or normalized_selection == selection

    @classmethod
    def _phase_switch_fallback_selection(
        cls,
        svc: Any,
        observed_selection: PhaseSelection | None,
        pending_selection: PhaseSelection,
    ) -> PhaseSelection:
        """Return the safe fallback phase layout after one failed phase-switch attempt."""
        if observed_selection is not None:
            return observed_selection
        active_selection = normalize_phase_selection(getattr(svc, "active_phase_selection", pending_selection), pending_selection)
        if active_selection:
            return active_selection
        return normalize_phase_selection(getattr(svc, "requested_phase_selection", pending_selection), pending_selection)

    @classmethod
    def _downshift_auto_phase_target(
        cls,
        svc: Any,
        phase_policy: Any,
        supported: tuple[PhaseSelection, ...],
        current_selection: PhaseSelection,
        current_index: int,
        surplus_watts: float,
        voltage: float,
    ) -> tuple[PhaseSelection | None, str, float | None] | None:
        """Return the next smaller phase layout when surplus falls below the sustain threshold."""
        if current_index <= 0:
            return None
        current_min_surplus = cls._phase_selection_min_surplus_watts(svc, current_selection, voltage)
        if current_min_surplus is None:
            return None
        threshold = max(
            0.0,
            current_min_surplus - float(getattr(phase_policy, "downshift_margin_watts", 150.0)),
        )
        if surplus_watts >= threshold:
            return None
        return supported[current_index - 1], "phase-downshift", threshold

    @staticmethod
    def _clear_auto_phase_candidate(svc: Any) -> None:
        """Clear the transient Auto phase-switch candidate tracking."""
        svc._auto_phase_target_candidate = None
        svc._auto_phase_target_since = None

    @classmethod
    def _auto_phase_switch_delay_seconds(
        cls,
        svc: Any,
        current_selection: PhaseSelection,
        target_selection: PhaseSelection,
    ) -> float:
        """Return the configured wait time before one phase switch may be applied."""
        phase_policy = cls._auto_phase_policy(svc)
        if phase_policy is None:
            return 0.0
        if cls._phase_selection_count(target_selection) > cls._phase_selection_count(current_selection):
            return max(0.0, float(getattr(phase_policy, "upshift_delay_seconds", 120.0)))
        return max(0.0, float(getattr(phase_policy, "downshift_delay_seconds", 30.0)))

    @classmethod
    def _auto_phase_candidate_ready(
        cls,
        svc: Any,
        current_selection: PhaseSelection,
        target_selection: PhaseSelection,
        now: float,
    ) -> bool:
        """Return whether one stable Auto phase candidate waited long enough to switch."""
        candidate = getattr(svc, "_auto_phase_target_candidate", None)
        if candidate != target_selection:
            svc._auto_phase_target_candidate = target_selection
            svc._auto_phase_target_since = float(now)
            return False
        candidate_since = finite_float_or_none(getattr(svc, "_auto_phase_target_since", None))
        if candidate_since is None:
            svc._auto_phase_target_since = float(now)
            return False
        return (float(now) - candidate_since) >= cls._auto_phase_switch_delay_seconds(
            svc,
            current_selection,
            target_selection,
        )

    @staticmethod
    def _stage_phase_switch(
        svc: Any,
        requested_selection: PhaseSelection,
        current_time: float,
        *,
        resume_relay: bool,
    ) -> None:
        """Record one staged phase switch before the relay-off confirmation window."""
        svc.requested_phase_selection = requested_selection
        svc._phase_switch_pending_selection = requested_selection
        svc._phase_switch_state = _UpdateCycleRelayMixin.PHASE_SWITCH_WAITING_STATE
        svc._phase_switch_requested_at = current_time
        svc._phase_switch_stable_until = None
        svc._phase_switch_resume_relay = bool(resume_relay)

    @classmethod
    def _phase_change_requires_staging(
        cls,
        svc: Any,
        relay_on: bool,
        now: float,
    ) -> bool:
        """Return whether a new phase selection must use the staged relay-off choreography."""
        if not bool(getattr(svc, "_phase_selection_requires_pause", lambda: False)()):
            return False
        pending_relay_state, _requested_at = svc._peek_pending_relay_command()
        if pending_relay_state is not None:
            return True
        confirmed_output = fresh_confirmed_relay_output(svc, now)
        if confirmed_output is not None:
            return bool(confirmed_output)
        return bool(relay_on)

    def _apply_auto_phase_target(
        self,
        svc: Any,
        target_selection: PhaseSelection,
        desired_relay: bool,
        relay_on: bool,
        now: float,
    ) -> bool | None:
        """Apply or stage one Auto-derived phase-selection target."""
        if self._phase_change_requires_staging(svc, relay_on, now):
            self._stage_phase_switch(
                svc,
                target_selection,
                now,
                resume_relay=bool(desired_relay),
            )
            svc._save_runtime_state()
            self._publish_local_pm_status_best_effort(False, now)
            self._clear_auto_phase_candidate(svc)
            return False
        try:
            applied_selection = svc._apply_phase_selection(target_selection)
        except Exception as error:  # pylint: disable=broad-except
            svc._mark_failure("shelly")
            svc._warning_throttled(
                "auto-phase-switch-failed",
                svc.auto_shelly_soft_fail_seconds,
                "Failed to apply Auto phase selection %s: %s",
                target_selection,
                error,
                exc_info=error,
            )
            self._clear_auto_phase_candidate(svc)
            return None
        svc.requested_phase_selection = applied_selection
        svc.active_phase_selection = applied_selection
        self._clear_phase_switch_mismatch_tracking(svc, applied_selection)
        lockout_selection = getattr(svc, "_phase_switch_lockout_selection", None)
        if lockout_selection is not None and normalize_phase_selection(lockout_selection, "P1") == applied_selection:
            self._clear_phase_switch_lockout(svc)
        svc._save_runtime_state()
        self._clear_auto_phase_candidate(svc)
        return None

    def maybe_apply_auto_phase_selection(
        self,
        svc: Any,
        desired_relay: bool,
        relay_on: bool,
        voltage: float,
        now: float,
        auto_mode_active: bool,
    ) -> bool | None:
        """Return an optional relay override after evaluating Auto phase-selection policy."""
        if self._auto_phase_selection_blocked(svc, auto_mode_active):
            return None
        phase_decision = self._auto_phase_selection_decision(
            svc,
            desired_relay,
            relay_on,
            voltage,
            now,
        )
        current_selection, target_selection, phase_reason, threshold_watts = phase_decision
        if target_selection is None or target_selection == current_selection:
            self._clear_auto_phase_candidate(svc)
            return None
        if not self._pending_auto_phase_target_ready(
            svc,
            current_selection,
            target_selection,
            now,
            phase_reason,
            threshold_watts,
        ):
            return None
        return self._apply_auto_phase_target(
            svc,
            target_selection,
            desired_relay,
            relay_on,
            now,
        )

    def _auto_phase_selection_blocked(self, svc: Any, auto_mode_active: bool) -> bool:
        """Return whether this cycle must skip Auto phase-selection changes entirely."""
        return any(
            (
                self._auto_phase_selection_inactive(svc, auto_mode_active),
                self._auto_phase_switch_already_active(svc),
            )
        )

    def _auto_phase_selection_decision(
        self,
        svc: Any,
        desired_relay: bool,
        relay_on: bool,
        voltage: float,
        now: float,
    ) -> tuple[PhaseSelection, PhaseSelection | None, str, float | None]:
        """Return the current Auto phase-selection decision plus diagnostics."""
        supported = self._ordered_auto_phase_selections(svc)
        current_selection = self._current_phase_selection(svc, supported)
        target_selection, phase_reason, threshold_watts = self._auto_phase_target_selection(
            svc,
            supported,
            current_selection,
            desired_relay,
            relay_on,
            voltage,
            now,
        )
        self._record_auto_phase_metrics(
            svc,
            current_selection=current_selection,
            target_selection=target_selection,
            phase_reason=phase_reason,
            threshold_watts=threshold_watts,
        )
        return current_selection, target_selection, phase_reason, threshold_watts

    def _auto_phase_selection_inactive(self, svc: Any, auto_mode_active: bool) -> bool:
        """Return whether Auto phase-selection logic should stay inactive this cycle."""
        if auto_mode_active:
            return False
        self._clear_auto_phase_candidate(svc)
        return True

    def _auto_phase_switch_already_active(self, svc: Any) -> bool:
        """Return whether one staged phase switch is already in flight."""
        pending_selection = self._pending_phase_switch_selection(svc)
        switch_state = str(getattr(svc, "_phase_switch_state", "") or "")
        return self._phase_switch_state_active(pending_selection, switch_state)

    def _pending_auto_phase_target_ready(
        self,
        svc: Any,
        current_selection: PhaseSelection,
        target_selection: PhaseSelection,
        now: float,
        phase_reason: str,
        threshold_watts: float | None,
    ) -> bool:
        """Return whether a pending Auto phase target may now be applied."""
        if self._auto_phase_candidate_ready(svc, current_selection, target_selection, now):
            return True
        self._record_auto_phase_metrics(
            svc,
            current_selection=current_selection,
            target_selection=target_selection,
            phase_reason=f"{phase_reason}-pending",
            threshold_watts=threshold_watts,
        )
        return False

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

    @staticmethod
    def _charger_readback_now(svc: Any, now: float | None = None) -> float:
        """Return the timestamp used to judge charger readback freshness."""
        if now is not None:
            return float(now)
        if callable(getattr(svc, "_time_now", None)):
            return float(svc._time_now())
        return time.time()

    @classmethod
    def _fresh_charger_state_timestamp(cls, svc: Any, now: float | None = None) -> float | None:
        """Return the fresh charger-state timestamp when native readback is usable."""
        if getattr(svc, "_charger_backend", None) is None:
            return None
        state_at = finite_float_or_none(getattr(svc, "_last_charger_state_at", None))
        if state_at is None:
            return None
        current = cls._charger_readback_now(svc, now)
        if abs(current - state_at) > cls._charger_state_max_age_seconds(svc):
            return None
        return float(state_at)

    @classmethod
    def _fresh_switch_feedback_timestamp(cls, svc: Any, now: float | None = None) -> float | None:
        """Return the fresh switch-feedback timestamp when explicit feedback is usable."""
        state_at = finite_float_or_none(getattr(svc, "_last_switch_feedback_at", None))
        if state_at is None:
            return None
        current = cls._charger_readback_now(svc, now)
        if abs(current - state_at) > cls._charger_state_max_age_seconds(svc):
            return None
        return float(state_at)

    @classmethod
    def _fresh_switch_feedback_closed(cls, svc: Any, now: float | None = None) -> bool | None:
        """Return fresh explicit contactor feedback when available."""
        if cls._fresh_switch_feedback_timestamp(svc, now) is None:
            return None
        raw_value = getattr(svc, "_last_switch_feedback_closed", None)
        if raw_value is None:
            return None
        return bool(raw_value)

    @classmethod
    def _fresh_switch_interlock_ok(cls, svc: Any, now: float | None = None) -> bool | None:
        """Return fresh external interlock state when available."""
        if cls._fresh_switch_feedback_timestamp(svc, now) is None:
            return None
        raw_value = getattr(svc, "_last_switch_interlock_ok", None)
        if raw_value is None:
            return None
        return bool(raw_value)

    @classmethod
    def _fresh_charger_enabled_readback(cls, svc: Any, now: float | None = None) -> bool | None:
        """Return fresh native charger enabled-state readback when available."""
        if cls._fresh_charger_state_timestamp(svc, now) is None:
            return None
        raw_enabled = getattr(svc, "_last_charger_state_enabled", None)
        if raw_enabled is None:
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
        if cls._fresh_charger_state_timestamp(svc, now) is None:
            return None
        value = finite_float_or_none(getattr(svc, attribute_name, None))
        if value is None:
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
        if cls._fresh_charger_state_timestamp(svc, now) is None:
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

    @staticmethod
    def _contactor_heuristic_delay_seconds(svc: Any) -> float:
        """Return the stabilization window used before one contactor heuristic becomes active."""
        return max(0.0, float(getattr(svc, "auto_shelly_soft_fail_seconds", 0.0)))

    @staticmethod
    def _contactor_lockout_threshold(svc: Any) -> int:
        """Return how many repeated contactor suspicion events latch one safety lockout."""
        return max(0, int(getattr(svc, "auto_contactor_fault_latch_count", 3)))

    @staticmethod
    def _contactor_lockout_persistence_seconds(svc: Any) -> float:
        """Return how long one continuous contactor suspicion may persist before latching."""
        return max(0.0, float(getattr(svc, "auto_contactor_fault_latch_seconds", 60.0)))

    @staticmethod
    def _contactor_power_threshold_w(svc: Any) -> float:
        """Return the minimum power considered a meaningful charging load."""
        configured = finite_float_or_none(getattr(svc, "charging_threshold_watts", None))
        return max(100.0, 0.0 if configured is None else float(configured))

    @staticmethod
    def _contactor_current_threshold_a(svc: Any) -> float:
        """Return the minimum current considered a meaningful charging load."""
        configured = finite_float_or_none(getattr(svc, "min_current", None))
        if configured is None:
            return 1.0
        return max(1.0, float(configured) / 4.0)

    @classmethod
    def _pm_load_active(
        cls,
        svc: Any,
        power: float | None,
        current: float | None,
        pm_confirmed: bool,
    ) -> bool:
        """Return whether the confirmed PM sample currently indicates meaningful load."""
        if not pm_confirmed:
            return False
        if power is not None and float(power) >= cls._contactor_power_threshold_w(svc):
            return True
        return current is not None and float(current) >= cls._contactor_current_threshold_a(svc)

    @classmethod
    def _charger_load_active(cls, svc: Any, now: float | None = None) -> bool:
        """Return whether charger-native readback currently indicates meaningful load."""
        power = cls._fresh_charger_power_readback(svc, now)
        if power is not None and float(power) >= cls._contactor_power_threshold_w(svc):
            return True
        current = cls._fresh_charger_actual_current_readback(svc, now)
        return current is not None and float(current) >= cls._contactor_current_threshold_a(svc)

    @classmethod
    def _charger_requests_load(cls, svc: Any, now: float | None = None) -> bool:
        """Return whether native charger readback suggests active charging demand."""
        if cls._charger_load_active(svc, now):
            return True
        tokens = cls._charger_text_tokens(cls._fresh_charger_text_readback(svc, "_last_charger_state_status", now))
        return bool(tokens & set(cls.CHARGER_STATUS_CHARGING_HINT_TOKENS))

    @classmethod
    def _observed_load_active(
        cls,
        svc: Any,
        power: float | None,
        current: float | None,
        pm_confirmed: bool,
        now: float | None = None,
    ) -> bool:
        """Return whether meter or charger readback currently shows meaningful charging load."""
        if cls._pm_load_active(svc, power, current, pm_confirmed):
            return True
        return cls._charger_load_active(svc, now)

    @classmethod
    def _heuristic_condition_age(
        cls,
        svc: Any,
        attribute_name: str,
        condition_active: bool,
        now: float | None,
    ) -> float | None:
        """Track one heuristic condition start time and return its current age when active."""
        current = cls._charger_readback_now(svc, now)
        if not condition_active:
            cls._set_runtime_attr(svc, attribute_name, None)
            return None
        started_at = finite_float_or_none(getattr(svc, attribute_name, None))
        if started_at is None:
            cls._set_runtime_attr(svc, attribute_name, current)
            return 0.0
        return max(0.0, current - float(started_at))

    @staticmethod
    def _set_runtime_attr(svc: Any, attribute_name: str, value: Any) -> None:
        """Persist one runtime-only attribute even when controller ports restrict setattr."""
        try:
            setattr(svc, attribute_name, value)
        except AttributeError:
            if hasattr(svc, "__dict__"):
                svc.__dict__[attribute_name] = value
                return
            raise

    @staticmethod
    def _charger_transport_detail(error: BaseException) -> str:
        """Return one compact charger-transport detail string."""
        detail = str(error).strip()
        return detail or error.__class__.__name__

    @classmethod
    def _remember_charger_transport_issue(
        cls,
        svc: Any,
        reason: str,
        source: str,
        error: BaseException,
        now: float | None = None,
    ) -> None:
        """Persist one normalized charger-transport issue for health and diagnostics."""
        captured_at = cls._charger_readback_now(svc, now)
        cls._set_runtime_attr(svc, "_last_charger_transport_reason", str(reason).strip() or None)
        cls._set_runtime_attr(svc, "_last_charger_transport_source", str(source).strip() or None)
        cls._set_runtime_attr(svc, "_last_charger_transport_detail", cls._charger_transport_detail(error))
        cls._set_runtime_attr(svc, "_last_charger_transport_at", captured_at)

    @classmethod
    def _clear_charger_transport_issue(cls, svc: Any) -> None:
        """Clear the remembered charger-transport issue after one successful request."""
        cls._set_runtime_attr(svc, "_last_charger_transport_reason", None)
        cls._set_runtime_attr(svc, "_last_charger_transport_source", None)
        cls._set_runtime_attr(svc, "_last_charger_transport_detail", None)
        cls._set_runtime_attr(svc, "_last_charger_transport_at", None)

    @classmethod
    def _remember_charger_retry(
        cls,
        svc: Any,
        reason: str,
        source: str,
        now: float | None = None,
    ) -> None:
        """Persist one charger retry-backoff window after a transport failure."""
        captured_at = cls._charger_readback_now(svc, now)
        delay_seconds = _charger_transport_retry_delay_seconds(svc, reason)
        delay_retry = getattr(svc, "_delay_source_retry", None)
        if callable(delay_retry):
            delay_retry("charger", captured_at, delay_seconds)
        elif isinstance(getattr(svc, "_source_retry_after", None), dict):
            svc._source_retry_after["charger"] = captured_at + delay_seconds
        cls._set_runtime_attr(svc, "_charger_retry_reason", str(reason).strip() or None)
        cls._set_runtime_attr(svc, "_charger_retry_source", str(source).strip() or None)
        cls._set_runtime_attr(svc, "_charger_retry_until", captured_at + delay_seconds)

    @classmethod
    def _clear_charger_retry(cls, svc: Any) -> None:
        """Clear the remembered charger retry-backoff state after recovery."""
        cls._set_runtime_attr(svc, "_charger_retry_reason", None)
        cls._set_runtime_attr(svc, "_charger_retry_source", None)
        cls._set_runtime_attr(svc, "_charger_retry_until", None)
        if isinstance(getattr(svc, "_source_retry_after", None), dict):
            svc._source_retry_after["charger"] = 0.0

    @classmethod
    def _charger_retry_active(cls, svc: Any, now: float | None = None) -> bool:
        """Return whether charger operations are currently paused by retry backoff."""
        return _fresh_charger_retry_until(svc, cls._charger_readback_now(svc, now)) is not None

    @staticmethod
    def _base_contactor_fault_reason(reason: object) -> str | None:
        """Return one normalized heuristic contactor-fault reason when supported."""
        normalized = str(reason).strip() if reason is not None else ""
        if normalized in {"contactor-suspected-open", "contactor-suspected-welded"}:
            return normalized
        return None

    @classmethod
    def _contactor_lockout_health_reason(cls, base_reason: object) -> str | None:
        """Return the latched health reason for one heuristic contactor-fault type."""
        normalized = cls._base_contactor_fault_reason(base_reason)
        if normalized == "contactor-suspected-open":
            return "contactor-lockout-open"
        if normalized == "contactor-suspected-welded":
            return "contactor-lockout-welded"
        return None

    @staticmethod
    def _contactor_fault_counts(svc: Any) -> dict[str, int]:
        """Return the mutable contactor-fault counter mapping keyed by heuristic reason."""
        counts = getattr(svc, "_contactor_fault_counts", None)
        if isinstance(counts, dict):
            return cast(dict[str, int], counts)
        counts = {}
        _UpdateCycleRelayMixin._set_runtime_attr(svc, "_contactor_fault_counts", counts)
        return counts

    @classmethod
    def _contactor_fault_count(cls, svc: Any, reason: object) -> int:
        """Return the remembered count for one heuristic contactor-fault reason."""
        normalized = cls._base_contactor_fault_reason(reason)
        if normalized is None:
            return 0
        return max(0, int(cls._contactor_fault_counts(svc).get(normalized, 0)))

    @classmethod
    def _clear_contactor_fault_active_state(cls, svc: Any) -> None:
        """Clear the currently active heuristic-fault episode without dropping counters."""
        cls._set_runtime_attr(svc, "_contactor_fault_active_reason", None)
        cls._set_runtime_attr(svc, "_contactor_fault_active_since", None)

    @classmethod
    def _clear_contactor_lockout(cls, svc: Any) -> None:
        """Clear one latched contactor-fault lockout."""
        cls._set_runtime_attr(svc, "_contactor_lockout_reason", "")
        cls._set_runtime_attr(svc, "_contactor_lockout_source", "")
        cls._set_runtime_attr(svc, "_contactor_lockout_at", None)

    @classmethod
    def _clear_contactor_fault_tracking(cls, svc: Any) -> None:
        """Clear all heuristic contactor-fault counters, active state, and lockout state."""
        cls._set_runtime_attr(svc, "_contactor_fault_counts", {})
        cls._clear_contactor_fault_active_state(svc)
        cls._clear_contactor_lockout(svc)
        cls._set_runtime_attr(svc, "_contactor_suspected_open_since", None)
        cls._set_runtime_attr(svc, "_contactor_suspected_welded_since", None)

    @classmethod
    def _engage_contactor_lockout(
        cls,
        svc: Any,
        base_reason: object,
        now: float | None,
        source: str,
    ) -> None:
        """Latch one safety lockout for a repeated or persistent heuristic contactor fault."""
        normalized = cls._base_contactor_fault_reason(base_reason)
        if normalized is None:
            cls._clear_contactor_lockout(svc)
            return
        current = cls._charger_readback_now(svc, now)
        cls._set_runtime_attr(svc, "_contactor_lockout_reason", normalized)
        cls._set_runtime_attr(svc, "_contactor_lockout_source", str(source).strip() or "count-threshold")
        cls._set_runtime_attr(svc, "_contactor_lockout_at", current)

    @classmethod
    def _active_contactor_lockout_health(cls, svc: Any) -> str | None:
        """Return the latched contactor-fault health override when one safety lockout is active."""
        return cls._contactor_lockout_health_reason(getattr(svc, "_contactor_lockout_reason", ""))

    @classmethod
    def _remember_contactor_fault(cls, svc: Any, reason: object, now: float | None) -> str | None:
        """Persist one heuristic contactor-fault episode and latch it when thresholds are exceeded."""
        normalized = cls._base_contactor_fault_reason(reason)
        if normalized is None:
            cls._clear_contactor_fault_active_state(svc)
            return None
        current = cls._charger_readback_now(svc, now)
        active_reason = cls._base_contactor_fault_reason(getattr(svc, "_contactor_fault_active_reason", None))
        active_since = finite_float_or_none(getattr(svc, "_contactor_fault_active_since", None))
        if active_reason != normalized or active_since is None:
            counts = cls._contactor_fault_counts(svc)
            counts[normalized] = cls._contactor_fault_count(svc, normalized) + 1
            cls._set_runtime_attr(svc, "_contactor_fault_active_reason", normalized)
            cls._set_runtime_attr(svc, "_contactor_fault_active_since", current)
            active_since = current
        current_count = cls._contactor_fault_count(svc, normalized)
        if cls._contactor_lockout_threshold(svc) > 0 and current_count >= cls._contactor_lockout_threshold(svc):
            cls._engage_contactor_lockout(svc, normalized, current, "count-threshold")
            return cls._active_contactor_lockout_health(svc)
        persistence_seconds = cls._contactor_lockout_persistence_seconds(svc)
        if persistence_seconds > 0.0 and (current - active_since) >= persistence_seconds:
            cls._engage_contactor_lockout(svc, normalized, current, "persistent")
            return cls._active_contactor_lockout_health(svc)
        return normalized

    @classmethod
    def charger_health_override(cls, svc: Any, now: float | None = None) -> str | None:
        """Return one charger-derived health override when readback reports a hard fault."""
        transport_reason = _fresh_charger_transport_reason(svc, now)
        if transport_reason is not None:
            return _charger_transport_health_reason(transport_reason)
        retry_reason = _fresh_charger_retry_reason(svc, now)
        if retry_reason is not None:
            return _charger_transport_health_reason(retry_reason)
        if cls._charger_text_indicates_fault(cls._fresh_charger_text_readback(svc, "_last_charger_state_fault", now)):
            return "charger-fault"
        if cls._charger_text_indicates_fault(
            cls._fresh_charger_text_readback(svc, "_last_charger_state_status", now)
        ):
            return "charger-fault"
        return None

    @classmethod
    def switch_feedback_health_override(
        cls,
        svc: Any,
        desired_relay: bool,
        relay_on: bool,
        now: float | None = None,
        *,
        power: float | None = None,
        current: float | None = None,
        pm_confirmed: bool = False,
    ) -> str | None:
        """Return one switch-feedback health override for contactor/interlock setups."""
        interlock_ok = cls._fresh_switch_interlock_ok(svc, now)
        if interlock_ok is False and (bool(desired_relay) or bool(relay_on)):
            cls._clear_contactor_fault_active_state(svc)
            cls._set_runtime_attr(svc, "_contactor_suspected_open_since", None)
            cls._set_runtime_attr(svc, "_contactor_suspected_welded_since", None)
            return "contactor-interlock"
        feedback_closed = cls._fresh_switch_feedback_closed(svc, now)
        if switch_feedback_mismatch(relay_on, feedback_closed):
            cls._clear_contactor_fault_active_state(svc)
            cls._set_runtime_attr(svc, "_contactor_suspected_open_since", None)
            cls._set_runtime_attr(svc, "_contactor_suspected_welded_since", None)
            return "contactor-feedback-mismatch"
        latched_lockout = cls._active_contactor_lockout_health(svc)
        if latched_lockout is not None:
            return latched_lockout
        observed_load = cls._observed_load_active(svc, power, current, pm_confirmed, now)
        demand_active = cls._charger_requests_load(svc, now)
        suspected_open_age = cls._heuristic_condition_age(
            svc,
            "_contactor_suspected_open_since",
            bool(relay_on) and demand_active and not observed_load,
            now,
        )
        suspected_welded_age = cls._heuristic_condition_age(
            svc,
            "_contactor_suspected_welded_since",
            not bool(relay_on) and observed_load,
            now,
        )
        delay_seconds = cls._contactor_heuristic_delay_seconds(svc)
        if suspected_welded_age is not None and suspected_welded_age >= delay_seconds:
            return cls._remember_contactor_fault(svc, "contactor-suspected-welded", now)
        if suspected_open_age is not None and suspected_open_age >= delay_seconds:
            return cls._remember_contactor_fault(svc, "contactor-suspected-open", now)
        cls._clear_contactor_fault_active_state(svc)
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
        return cls._charger_status_override_from_tokens(svc, tokens, auto_mode_active)

    @classmethod
    def _charger_status_override_from_tokens(
        cls,
        svc: Any,
        tokens: set[str],
        auto_mode_active: bool,
    ) -> tuple[int, str] | None:
        """Return the status override implied by already-tokenized charger status text."""
        for hint_tokens, status_code, status_source in cls._charger_status_token_rules(svc, auto_mode_active):
            if tokens & hint_tokens:
                return status_code, status_source
        return None

    @classmethod
    def _charger_status_token_rules(
        cls,
        svc: Any,
        auto_mode_active: bool,
    ) -> tuple[tuple[set[str], int, str], ...]:
        """Return ordered charger-status token rules for native status overrides."""
        return (
            (set(cls.CHARGER_STATUS_FINISHED_HINT_TOKENS), 3, "charger-status-finished"),
            (
                set(cls.CHARGER_STATUS_WAITING_HINT_TOKENS),
                4 if auto_mode_active else 6,
                "charger-status-waiting",
            ),
            (set(cls.CHARGER_STATUS_CHARGING_HINT_TOKENS), 2, "charger-status-charging"),
            (
                set(cls.CHARGER_STATUS_READY_HINT_TOKENS),
                int(getattr(svc, "idle_status", 1)),
                "charger-status-ready",
            ),
        )

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
        min_current, max_current = _UpdateCycleRelayMixin._charger_current_limits(svc)
        target = _UpdateCycleRelayMixin._apply_min_current_limit(target, min_current)
        target = _UpdateCycleRelayMixin._apply_max_current_limit(target, max_current)
        return target if target > 0.0 else None

    @staticmethod
    def _charger_current_limits(svc: Any) -> tuple[float | None, float | None]:
        """Return configured min/max current limits for charger current writes."""
        min_current = finite_float_or_none(getattr(svc, "min_current", None))
        max_current = finite_float_or_none(getattr(svc, "max_current", None))
        return min_current, max_current

    @staticmethod
    def _apply_min_current_limit(target: float, min_current: float | None) -> float:
        """Clamp a current target against the configured minimum when present."""
        return max(target, min_current) if min_current is not None else float(target)

    @staticmethod
    def _apply_max_current_limit(target: float, max_current: float | None) -> float:
        """Clamp a current target against the configured maximum when present."""
        if max_current is None or max_current <= 0.0:
            return float(target)
        return min(target, max_current)

    @classmethod
    def _stable_learned_current_inputs(
        cls,
        svc: Any,
    ) -> tuple[float, float, str, float, float | None] | None:
        """Return one validated stable learned-power snapshot for current derivation."""
        if not cls._stable_learned_current_state(svc):
            return None
        learned_inputs = cls._raw_stable_learned_current_inputs(svc)
        return cls._validated_stable_learned_current_inputs(learned_inputs)

    @staticmethod
    def _stable_learned_current_state(svc: Any) -> bool:
        """Return whether learned-power state currently permits current-target derivation."""
        return normalize_learning_state(getattr(svc, "learned_charge_power_state", "unknown")) == "stable"

    @staticmethod
    def _raw_stable_learned_current_inputs(
        svc: Any,
    ) -> tuple[float | None, float | None, str | None, float | None, float | None]:
        """Return raw learned-power inputs before validation for current derivation."""
        learned_power = finite_float_or_none(getattr(svc, "learned_charge_power_watts", None))
        learned_voltage = finite_float_or_none(getattr(svc, "learned_charge_power_voltage", None))
        learned_phase = normalize_learning_phase(
            getattr(svc, "learned_charge_power_phase", getattr(svc, "phase", "L1"))
        )
        updated_at = finite_float_or_none(getattr(svc, "learned_charge_power_updated_at", None))
        max_age_seconds = finite_float_or_none(
            getattr(svc, "auto_learn_charge_power_max_age_seconds", 21600.0)
        )
        return learned_power, learned_voltage, learned_phase, updated_at, max_age_seconds

    @staticmethod
    def _validated_stable_learned_current_inputs(
        learned_inputs: tuple[float | None, float | None, str | None, float | None, float | None],
    ) -> tuple[float, float, str, float, float | None] | None:
        """Return validated learned-power inputs for current derivation."""
        learned_power, learned_voltage, learned_phase, updated_at, max_age_seconds = learned_inputs
        resolved_power = _UpdateCycleRelayMixin._positive_learned_scalar(learned_power)
        resolved_voltage = _UpdateCycleRelayMixin._positive_learned_scalar(learned_voltage)
        if resolved_power is None or resolved_voltage is None:
            return None
        phase_and_timestamp = _UpdateCycleRelayMixin._learned_phase_and_timestamp(learned_phase, updated_at)
        if phase_and_timestamp is None:
            return None
        resolved_phase, resolved_updated_at = phase_and_timestamp
        return resolved_power, resolved_voltage, resolved_phase, resolved_updated_at, max_age_seconds

    @staticmethod
    def _positive_learned_scalar(value: float | None) -> float | None:
        """Return one learned scalar only when it is present and strictly positive."""
        if value is None or value <= 0.0:
            return None
        return float(value)

    @staticmethod
    def _learned_phase_and_timestamp(
        learned_phase: str | None,
        updated_at: float | None,
    ) -> tuple[str, float] | None:
        """Return learned phase and timestamp only when both are present."""
        if learned_phase is None or updated_at is None:
            return None
        return str(learned_phase), float(updated_at)

    @staticmethod
    def _learned_current_target_stale(now: float, updated_at: float, max_age_seconds: float | None) -> bool:
        """Return whether one learned-power snapshot is too old for current derivation."""
        return bool(max_age_seconds is not None and max_age_seconds > 0.0 and (float(now) - updated_at) > max_age_seconds)

    @staticmethod
    def _learned_phase_voltage(svc: Any, learned_phase: str, learned_voltage: float) -> float:
        """Return the effective per-phase voltage used for one learned current target."""
        if learned_phase != "3P" or str(getattr(svc, "voltage_mode", "phase")).strip().lower() == "phase":
            return float(learned_voltage)
        return float(learned_voltage) / math.sqrt(3.0) if learned_voltage > 0.0 else 0.0

    @staticmethod
    def _rounded_learned_current_target(
        learned_power: float,
        phase_voltage: float,
        phase_count: float,
    ) -> float | None:
        """Return one rounded current target derived from a learned power signature."""
        if phase_voltage <= 0.0 or phase_count <= 0.0:
            return None
        return finite_float_or_none(round(float(learned_power) / (phase_voltage * phase_count)))

    @staticmethod
    def _scheduled_night_charge_active(svc: Any, now: float) -> bool:
        """Return whether scheduled/plan mode should force nighttime max-current charging."""
        if not mode_uses_scheduled_logic(getattr(svc, "virtual_mode", 0)):
            return False
        return scheduled_mode_snapshot(
            datetime.fromtimestamp(float(now)),
            getattr(svc, "auto_month_windows", {}),
            getattr(svc, "auto_scheduled_enabled_days", "Mon,Tue,Wed,Thu,Fri"),
            delay_seconds=float(getattr(svc, "auto_scheduled_night_start_delay_seconds", 3600.0)),
            latest_end_time=getattr(svc, "auto_scheduled_latest_end_time", "06:30"),
        ).night_boost_active

    @staticmethod
    def _scheduled_night_current_amps(svc: Any) -> float | None:
        """Return one configured scheduled night-boost current, or MaxCurrent fallback."""
        configured = finite_float_or_none(getattr(svc, "auto_scheduled_night_current_amps", None))
        if configured is not None and configured > 0.0:
            return configured
        return finite_float_or_none(getattr(svc, "max_current", None))

    @classmethod
    def _derived_learned_current_target(cls, svc: Any, now: float) -> float | None:
        """Return one current target derived from a stable learned charging power."""
        learned_inputs = cls._stable_learned_current_inputs(svc)
        if learned_inputs is None:
            return None
        learned_power, learned_voltage, learned_phase, updated_at, max_age_seconds = learned_inputs
        if cls._learned_current_target_stale(now, updated_at, max_age_seconds):
            return None
        phase_voltage = cls._learned_phase_voltage(svc, learned_phase, learned_voltage)
        phase_count = 3.0 if learned_phase == "3P" else 1.0
        rounded_current = cls._rounded_learned_current_target(learned_power, phase_voltage, phase_count)
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
        if cls._scheduled_night_charge_active(svc, now):
            return cls._clamped_charger_current_target(svc, cls._scheduled_night_current_amps(svc))
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
        if cls._charger_current_reset_needed(svc, desired_relay, auto_mode_active):
            cls._reset_charger_current_target(svc)
            return None

        target_amps = cls._charger_current_target_amps(svc, desired_relay, now, auto_mode_active)
        if target_amps is None:
            return None

        last_target = finite_float_or_none(getattr(svc, "_charger_target_current_amps", None))
        if cls._charger_target_unchanged(last_target, target_amps):
            return cast(float, last_target)
        return cls._apply_new_charger_current_target(svc, backend, target_amps, now, last_target)

    @classmethod
    def _apply_new_charger_current_target(
        cls,
        svc: Any,
        backend: Any,
        target_amps: float,
        now: float,
        last_target: float | None,
    ) -> float | None:
        """Apply one new charger-current target and remember the result when successful."""
        if cls._charger_retry_active(svc, now):
            return last_target
        try:
            backend.set_current(float(target_amps))
        except Exception as error:  # pylint: disable=broad-except
            cls._handle_charger_current_target_failure(svc, error, now)
            return last_target
        cls._clear_charger_transport_issue(svc)
        cls._clear_charger_retry(svc)
        return cls._remember_charger_current_target(svc, target_amps, now)

    @staticmethod
    def _charger_current_reset_needed(svc: Any, desired_relay: bool, auto_mode_active: bool) -> bool:
        """Return whether charger-current state should be cleared instead of applied."""
        return not auto_mode_active or not bool(desired_relay)

    @staticmethod
    def _reset_charger_current_target(svc: Any) -> None:
        """Clear the remembered native charger-current target."""
        svc._charger_target_current_amps = None
        svc._charger_target_current_applied_at = None

    @staticmethod
    def _charger_target_unchanged(last_target: float | None, target_amps: float) -> bool:
        """Return whether a charger-current target is effectively unchanged."""
        return last_target is not None and abs(last_target - target_amps) < 0.01

    @staticmethod
    def _handle_charger_current_target_failure(svc: Any, error: Exception, now: float | None = None) -> None:
        """Record one failed native charger-current write."""
        transport_reason = modbus_transport_issue_reason(error)
        if transport_reason is not None:
            _UpdateCycleRelayMixin._remember_charger_transport_issue(svc, transport_reason, "current", error, now)
            _UpdateCycleRelayMixin._remember_charger_retry(svc, transport_reason, "current", now)
        svc._mark_failure("charger")
        svc._warning_throttled(
            "charger-current-failed",
            svc.auto_shelly_soft_fail_seconds,
            "Charger current request failed: %s",
            error,
            exc_info=error,
        )

    @staticmethod
    def _remember_charger_current_target(svc: Any, target_amps: float, now: float) -> float:
        """Persist one successfully applied charger-current target."""
        svc._charger_target_current_amps = float(target_amps)
        svc._charger_target_current_applied_at = float(now)
        svc._mark_recovery("charger", "Charger current writes recovered")
        return float(target_amps)

    @classmethod
    def _apply_enabled_target(cls, svc: Any, enabled: bool, now: float) -> bool:
        """Apply one on/off target through the native charger when available."""
        backend = cls._charger_enable_backend(svc)
        if backend is not None:
            if cls._charger_retry_active(svc, now):
                return False
            cast(Any, backend).set_enabled(bool(enabled))
            cls._clear_charger_transport_issue(svc)
            cls._clear_charger_retry(svc)
            svc._mark_recovery("charger", "Charger enable writes recovered")
            return True
        svc._queue_relay_command(bool(enabled), now)
        return True

    @staticmethod
    def _phase_tuple(raw_value: Any) -> tuple[float, float, float] | None:
        """Return one numeric three-phase tuple from PM metadata."""
        if not isinstance(raw_value, (tuple, list)) or len(raw_value) != 3:
            return None
        values: tuple[float | None, float | None, float | None] = (
            _UpdateCycleRelayMixin._phase_tuple_item(raw_value[0]),
            _UpdateCycleRelayMixin._phase_tuple_item(raw_value[1]),
            _UpdateCycleRelayMixin._phase_tuple_item(raw_value[2]),
        )
        return _UpdateCycleRelayMixin._resolved_phase_tuple(values)

    @staticmethod
    def _phase_tuple_item(raw_value: Any) -> float | None:
        """Return one numeric phase-tuple item when it is a valid scalar."""
        if not isinstance(raw_value, (int, float)) or isinstance(raw_value, bool):
            return None
        return float(raw_value)

    @staticmethod
    def _resolved_phase_tuple(
        values: tuple[float | None, float | None, float | None],
    ) -> tuple[float, float, float] | None:
        """Return one concrete phase tuple when all three values are present."""
        if None in values:
            return None
        first, second, third = values
        return cast(float, first), cast(float, second), cast(float, third)

    @staticmethod
    def _phase_voltage(voltage: float, selection: Any, voltage_mode: Any) -> float:
        """Return the per-line voltage implied by one backend phase selection."""
        normalized_selection = _UpdateCycleRelayMixin._normalized_phase_selection(selection)
        normalized_voltage_mode = _UpdateCycleRelayMixin._normalized_voltage_mode(voltage_mode)
        if not _UpdateCycleRelayMixin._selection_uses_line_to_line_voltage(
            normalized_selection,
            normalized_voltage_mode,
        ):
            return float(voltage)
        return float(voltage) / math.sqrt(3.0) if float(voltage) > 0.0 else 0.0

    @staticmethod
    def _normalized_phase_selection(selection: Any) -> str:
        """Return one normalized phase-selection token."""
        return str(selection).strip().upper() if selection is not None else ""

    @staticmethod
    def _normalized_voltage_mode(voltage_mode: Any) -> str:
        """Return one normalized voltage-mode token."""
        return str(voltage_mode).strip().lower() if voltage_mode is not None else "phase"

    @staticmethod
    def _selection_uses_line_to_line_voltage(selection: str, voltage_mode: str) -> bool:
        """Return whether one phase selection implies line-to-line voltage handling."""
        return selection == "P1_P2_P3" and voltage_mode != "phase"

    def _phase_data_for_pm_status(
        self,
        pm_status: dict[str, Any] | None,
        power: float,
        voltage: float,
        current: float,
    ) -> dict[str, dict[str, float]]:
        """Return per-line display values, preferring backend-provided phase metadata."""
        svc = self.service
        phase_data = self._phase_data_from_backend_metadata(pm_status, voltage, getattr(svc, "voltage_mode", "phase"))
        if phase_data is not None:
            return phase_data
        phase_values = cast(
            dict[str, dict[str, float]],
            self._phase_values(power, voltage, svc.phase, svc.voltage_mode),
        )
        return phase_values

    def _phase_data_from_backend_metadata(
        self,
        pm_status: dict[str, Any] | None,
        voltage: float,
        voltage_mode: Any,
    ) -> dict[str, dict[str, float]] | None:
        """Return per-phase measurements from backend metadata when available."""
        if not isinstance(pm_status, dict):
            return None
        phase_powers = self._phase_tuple(pm_status.get("_phase_powers_w"))
        if phase_powers is None:
            return None
        phase_currents = self._phase_tuple(pm_status.get("_phase_currents_a"))
        phase_voltage = self._phase_voltage(voltage, pm_status.get("_phase_selection"), voltage_mode)
        return self._phase_data_from_phase_tuples(phase_powers, phase_currents, phase_voltage)

    @staticmethod
    def _phase_measurement(
        phase_power: float,
        phase_current: float | None,
        phase_voltage: float,
    ) -> dict[str, float]:
        """Return one per-phase measurement mapping."""
        resolved_current = (
            float(phase_current)
            if phase_current is not None
            else (float(phase_power) / phase_voltage if phase_voltage else 0.0)
        )
        return {
            "power": float(phase_power),
            "voltage": phase_voltage,
            "current": resolved_current,
        }

    def _phase_data_from_phase_tuples(
        self,
        phase_powers: tuple[float, float, float],
        phase_currents: tuple[float, float, float] | None,
        phase_voltage: float,
    ) -> dict[str, dict[str, float]]:
        """Return one complete per-phase mapping from backend tuples."""
        phase_data: dict[str, dict[str, float]] = {}
        for phase_name, phase_power, phase_current in zip(
            ("L1", "L2", "L3"),
            phase_powers,
            phase_currents or (None, None, None),
        ):
            phase_data[phase_name] = self._phase_measurement(phase_power, phase_current, phase_voltage)
        return phase_data

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
        if self._relay_sync_confirmed_match(svc, relay_on, pm_confirmed, expected_relay):
            return None

        deadline_at = getattr(svc, "_relay_sync_deadline_at", None)
        if self._relay_sync_before_deadline(deadline_at, now):
            return self._relay_sync_pre_timeout_result(relay_on, pm_confirmed, expected_relay)
        self._record_relay_sync_timeout(svc, relay_on, pm_confirmed, expected_relay, deadline_at)
        self._clear_relay_sync_tracking(svc)
        return "relay-sync-failed"

    def _relay_sync_confirmed_match(
        self,
        svc: Any,
        relay_on: bool,
        pm_confirmed: bool,
        expected_relay: bool,
    ) -> bool:
        """Return whether relay confirmation already matched the pending expectation."""
        if not pm_confirmed or bool(relay_on) != expected_relay:
            return False
        if getattr(svc, "_relay_sync_failure_reported", False):
            svc._mark_recovery("shelly", "Shelly relay confirmation recovered")
        self._clear_relay_sync_tracking(svc)
        return True

    @staticmethod
    def _relay_sync_before_deadline(deadline_at: Any, now: float) -> bool:
        """Return whether relay confirmation is still inside its deadline window."""
        return deadline_at is None or float(now) < float(deadline_at)

    @staticmethod
    def _relay_sync_pre_timeout_result(
        relay_on: bool,
        pm_confirmed: bool,
        expected_relay: bool,
    ) -> str | None:
        """Return the health result before relay confirmation timed out."""
        if pm_confirmed and bool(relay_on) != expected_relay:
            return "command-mismatch"
        return None

    def _record_relay_sync_timeout(
        self,
        svc: Any,
        relay_on: bool,
        pm_confirmed: bool,
        expected_relay: bool,
        deadline_at: Any,
    ) -> None:
        """Record one timed-out relay confirmation, once per in-flight request."""
        if getattr(svc, "_relay_sync_failure_reported", False):
            return
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

    @staticmethod
    def _observed_phase_selection_from_pm_status(pm_status: dict[str, Any]) -> PhaseSelection | None:
        """Return the observed phase selection encoded in one PM payload when present."""
        observed = pm_status.get("_phase_selection")
        if observed is None:
            return None
        return normalize_phase_selection(observed, "P1")

    @classmethod
    def _observed_phase_selection(
        cls,
        svc: Any,
        pm_status: dict[str, Any],
        now: float,
    ) -> PhaseSelection | None:
        """Return the freshest observed phase selection from PM or charger readback."""
        observed = cls._observed_phase_selection_from_pm_status(pm_status)
        if observed is not None:
            return observed
        if cls._fresh_charger_state_timestamp(svc, now) is None:
            return None
        raw_phase_selection = getattr(svc, "_last_charger_state_phase_selection", None)
        if raw_phase_selection is None:
            return None
        return normalize_phase_selection(raw_phase_selection, "P1")

    @classmethod
    def _phase_switch_verification_deadline(cls, svc: Any) -> float | None:
        """Return the deadline after which an unconfirmed phase switch counts as mismatch."""
        stable_until = finite_float_or_none(getattr(svc, "_phase_switch_stable_until", None))
        soft_fail_seconds = max(0.0, float(getattr(svc, "auto_shelly_soft_fail_seconds", 10.0)))
        if stable_until is not None:
            return stable_until + soft_fail_seconds
        requested_at = finite_float_or_none(getattr(svc, "_phase_switch_requested_at", None))
        if requested_at is None:
            return None
        return (
            requested_at
            + cls._phase_switch_pause_seconds(svc)
            + cls._phase_switch_stabilization_seconds(svc)
            + soft_fail_seconds
        )

    @classmethod
    def _phase_switch_verification_expired(cls, svc: Any, now: float) -> bool:
        """Return whether a pending phase switch exceeded its confirmation deadline."""
        deadline = cls._phase_switch_verification_deadline(svc)
        return deadline is not None and float(now) >= float(deadline)

    def _report_phase_switch_mismatch(
        self,
        svc: Any,
        pending_selection: PhaseSelection,
        observed_selection: PhaseSelection | None,
        now: float,
    ) -> None:
        """Report one phase-switch confirmation timeout and keep the staged state active."""
        requested_at = finite_float_or_none(getattr(svc, "_phase_switch_requested_at", None))
        elapsed_seconds = (
            max(0.0, float(now) - requested_at)
            if requested_at is not None
            else max(0.0, float(getattr(svc, "auto_shelly_soft_fail_seconds", 10.0)))
        )
        observed_label = observed_selection if observed_selection is not None else "unknown"
        mismatch_count = self._remember_phase_switch_mismatch(svc, pending_selection, now)
        lockout_engaged = False
        lockout_threshold = self._phase_switch_lockout_threshold(svc)
        if lockout_threshold > 0 and mismatch_count >= lockout_threshold:
            self._engage_phase_switch_lockout(svc, pending_selection, now)
            lockout_engaged = self._phase_switch_lockout_active(svc, now, pending_selection)
        svc._mark_failure("shelly")
        svc._set_health("phase-switch-mismatch", cached=False)
        svc._warning_throttled(
            "phase-switch-mismatch",
            max(1.0, float(getattr(svc, "auto_shelly_soft_fail_seconds", 10.0))),
            "Phase selection %s did not confirm after %.1fs (observed=%s count=%s lockout=%s)",
            pending_selection,
            elapsed_seconds,
            observed_label,
            mismatch_count,
            int(lockout_engaged),
        )

    @classmethod
    def _clear_phase_switch_state(cls, svc: Any) -> None:
        """Clear the transient state used for staged phase switching."""
        svc._phase_switch_pending_selection = None
        svc._phase_switch_state = None
        svc._phase_switch_requested_at = None
        svc._phase_switch_stable_until = None
        svc._phase_switch_resume_relay = False
        svc._phase_switch_mismatch_active = False

    def _abort_phase_switch_after_mismatch(
        self,
        svc: Any,
        pending_selection: PhaseSelection,
        observed_selection: PhaseSelection | None,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
    ) -> tuple[bool, float, float, bool]:
        """Abort one failed phase-switch attempt and continue on the observed fallback phase."""
        fallback_selection = self._phase_switch_fallback_selection(svc, observed_selection, pending_selection)
        svc.requested_phase_selection = fallback_selection
        svc.active_phase_selection = fallback_selection
        self._clear_auto_phase_candidate(svc)
        return self._resume_after_phase_switch_pause(
            svc,
            relay_on,
            power,
            current,
            pm_confirmed,
            now,
            auto_mode_active,
        )

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
            applied = self._apply_enabled_target(svc, True, now)
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
        if not applied:
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
        pm_status: dict[str, Any],
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
        if not self._phase_switch_state_active(pending_selection, switch_state):
            self._clear_phase_switch_state(svc)
            return relay_on, power, current, pm_confirmed, None
        assert pending_selection is not None

        if switch_state == self.PHASE_SWITCH_WAITING_STATE:
            return self._orchestrate_waiting_phase_switch(
                svc,
                pending_selection,
                relay_on,
                power,
                current,
                pm_confirmed,
                now,
                auto_mode_active,
            )
        return self._orchestrate_stabilizing_phase_switch(
            svc,
            pending_selection,
            pm_status,
            relay_on,
            power,
            current,
            pm_confirmed,
            now,
            auto_mode_active,
        )

    def _phase_switch_state_active(self, pending_selection: PhaseSelection | None, switch_state: str) -> bool:
        """Return whether the current service state still represents one staged phase switch."""
        return pending_selection is not None and switch_state in {
            self.PHASE_SWITCH_WAITING_STATE,
            self.PHASE_SWITCH_STABILIZING_STATE,
        }

    def _phase_switch_waiting_ready(
        self,
        svc: Any,
        relay_on: bool,
        pm_confirmed: bool,
        now: float,
    ) -> bool:
        """Return whether a staged phase switch may advance from waiting to apply."""
        pending_relay_state, _requested_at = svc._peek_pending_relay_command()
        if bool(relay_on) or pending_relay_state is not None or not pm_confirmed:
            return False
        requested_at = getattr(svc, "_phase_switch_requested_at", None)
        return requested_at is None or (float(now) - float(requested_at)) >= self._phase_switch_pause_seconds(svc)

    def _apply_pending_phase_selection(
        self,
        svc: Any,
        pending_selection: PhaseSelection,
        now: float,
    ) -> tuple[bool, float, float, bool, bool | None]:
        """Apply one staged phase selection and enter stabilization."""
        applied_selection = svc._apply_phase_selection(pending_selection)
        svc._phase_switch_mismatch_active = False
        svc.requested_phase_selection = applied_selection
        svc._phase_switch_state = self.PHASE_SWITCH_STABILIZING_STATE
        svc._phase_switch_stable_until = float(now) + self._phase_switch_stabilization_seconds(svc)
        svc._save_runtime_state()
        return False, 0.0, 0.0, False, False

    def _orchestrate_waiting_phase_switch(
        self,
        svc: Any,
        pending_selection: PhaseSelection,
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
    ) -> tuple[bool, float, float, bool, bool | None]:
        """Advance one phase switch while waiting for confirmed relay-off pause."""
        if not self._phase_switch_waiting_ready(svc, relay_on, pm_confirmed, now):
            return relay_on, power, current, pm_confirmed, False
        try:
            return self._apply_pending_phase_selection(svc, pending_selection, now)
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

    def _orchestrate_stabilizing_phase_switch(
        self,
        svc: Any,
        pending_selection: PhaseSelection,
        pm_status: dict[str, Any],
        relay_on: bool,
        power: float,
        current: float,
        pm_confirmed: bool,
        now: float,
        auto_mode_active: bool,
    ) -> tuple[bool, float, float, bool, bool | None]:
        """Advance one phase switch while the new phase selection stabilizes."""
        observed_selection = self._observed_phase_selection(svc, pm_status, now)
        if observed_selection is not None:
            svc.active_phase_selection = observed_selection
        stable_until = getattr(svc, "_phase_switch_stable_until", None)
        if stable_until is not None and float(now) < float(stable_until):
            return False, 0.0, 0.0, False, False
        if observed_selection != pending_selection:
            if self._phase_switch_verification_expired(svc, now):
                self._report_phase_switch_mismatch(svc, pending_selection, observed_selection, now)
                relay_on, power, current, pm_confirmed = self._abort_phase_switch_after_mismatch(
                    svc,
                    pending_selection,
                    observed_selection,
                    relay_on,
                    power,
                    current,
                    pm_confirmed,
                    now,
                    auto_mode_active,
                )
                return relay_on, power, current, pm_confirmed, None
            return False, 0.0, 0.0, False, False
        svc.active_phase_selection = pending_selection
        self._clear_phase_switch_mismatch_tracking(svc, pending_selection)
        lockout_selection = getattr(svc, "_phase_switch_lockout_selection", None)
        if lockout_selection is not None and normalize_phase_selection(lockout_selection, "P1") == pending_selection:
            self._clear_phase_switch_lockout(svc)
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
        if self._relay_decision_noop(svc, desired_relay, relay_on):
            return relay_on, power, current, pm_confirmed

        if auto_mode_active and svc.auto_audit_log:
            self.log_auto_relay_change(svc, desired_relay)

        try:
            applied = self._apply_enabled_target(svc, desired_relay, now)
        except Exception as error:  # pylint: disable=broad-except
            self._handle_relay_decision_failure(svc, error)
            return relay_on, power, current, pm_confirmed
        if not applied:
            return relay_on, power, current, pm_confirmed

        relay_on = desired_relay
        power = 0.0
        current = 0.0
        self._publish_local_pm_status_best_effort(relay_on, now)
        return relay_on, power, current, False

    @classmethod
    def _relay_decision_noop(cls, svc: Any, desired_relay: bool, relay_on: bool) -> bool:
        """Return whether the requested relay decision is already satisfied or pending."""
        if desired_relay == relay_on:
            return True
        return getattr(svc, "_relay_sync_expected_state", None) == bool(desired_relay)

    @classmethod
    def _handle_relay_decision_failure(cls, svc: Any, error: Exception) -> None:
        """Record one failed relay/enable backend request during Auto update."""
        source_key = cls._enable_control_source_key(svc)
        source_label = cls._enable_control_label(svc)
        transport_reason = modbus_transport_issue_reason(error)
        if source_key == "charger" and transport_reason is not None:
            cls._remember_charger_transport_issue(svc, transport_reason, "enable", error)
            cls._remember_charger_retry(svc, transport_reason, "enable")
        svc._mark_failure(source_key)
        svc._warning_throttled(
            f"{source_key}-switch-failed",
            svc.auto_shelly_soft_fail_seconds,
            "%s switch request failed: %s",
            source_label,
            error,
            exc_info=error,
        )

    @classmethod
    def derive_status_code(
        cls,
        svc: Any,
        relay_on: bool,
        power: float,
        auto_mode_active: bool,
        now: float | None = None,
        health_reason: str | None = None,
    ) -> int:
        """Translate runtime state into the outward Venus EV charger status code.

        The ordering here is intentional and treated as a compatibility
        contract: hard EVSE-side faults first, then charger-native hard faults,
        then fresh charger-native status text, and only then fallback inference
        from relay/power state. This keeps the published truth stable when
        meter, switch, feedback, and charger signals disagree.
        """
        hard_fault_status = cls._hard_evse_fault_status_override(svc, health_reason)
        if hard_fault_status is not None:
            return hard_fault_status
        fault_status = cls._charger_fault_status_override(svc, now)
        if fault_status is not None:
            return fault_status
        status_override = cls._charger_status_override(svc, auto_mode_active, now)
        if status_override is not None:
            status_code, status_source = status_override
            svc._last_status_source = status_source
            return int(status_code)
        return cls._fallback_status_code(svc, relay_on, power, auto_mode_active, now)

    @classmethod
    def _charger_fault_status_override(cls, svc: Any, now: float | None = None) -> int | None:
        """Return one status code when charger-native readback reports a hard fault."""
        if cls.charger_health_override(svc, now) != "charger-fault":
            svc._last_charger_fault_active = 0
            return None
        svc._last_status_source = "charger-fault"
        svc._last_charger_fault_active = 1
        return 0

    @staticmethod
    def _evse_fault_status_source(reason: str) -> str:
        """Return the outward status-source label for one EVSE-side hard fault."""
        return {
            "contactor-feedback-mismatch": "contactor-feedback-fault",
            "contactor-lockout-open": "contactor-lockout-open",
            "contactor-lockout-welded": "contactor-lockout-welded",
        }.get(reason, "evse-fault")

    @classmethod
    def _hard_evse_fault_status_override(
        cls,
        svc: Any,
        health_reason: object | None = None,
    ) -> int | None:
        """Return one fault status code for hard EVSE-side faults other than charger-native faults."""
        fault_reason = evse_fault_reason(
            getattr(svc, "_last_health_reason", None) if health_reason is None else health_reason
        )
        if fault_reason not in {
            "contactor-feedback-mismatch",
            "contactor-lockout-open",
            "contactor-lockout-welded",
        }:
            return None
        svc._last_status_source = cls._evse_fault_status_source(fault_reason)
        return 0

    @classmethod
    def _fallback_status_code(
        cls,
        svc: Any,
        relay_on: bool,
        power: float,
        auto_mode_active: bool,
        now: float | None = None,
    ) -> int:
        """Return the fallback Venus status when no charger-native override applies."""
        enabled_state = cls._effective_enabled_state(svc, relay_on, now)
        if enabled_state:
            return cls._enabled_fallback_status_code(svc, power)
        return cls._disabled_fallback_status_code(svc, auto_mode_active)

    @staticmethod
    def _enabled_fallback_status_code(svc: Any, power: float) -> int:
        """Return fallback Venus status for an enabled charger without native overrides."""
        if power >= svc.charging_threshold_watts:
            svc._last_status_source = "charging"
            return 2
        svc._last_status_source = "enabled-idle"
        return int(svc.idle_status)

    @staticmethod
    def _disabled_fallback_status_code(svc: Any, auto_mode_active: bool) -> int:
        """Return fallback Venus status for a disabled charger without native overrides."""
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
