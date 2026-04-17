# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime, worker-state, and watchdog helpers for the Shelly wallbox service.

This controller owns the "glue" state that keeps the service robust in the
field: cached worker snapshots, throttled warnings, watchdog recovery,
auto-audit logging, and safe persistence of runtime-only state.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import os
import time
from typing import Any, cast

from shelly_wallbox.core.common import (
    _fresh_charger_retry_reason,
    _fresh_charger_retry_source,
    _fresh_charger_transport_reason,
    _fresh_charger_transport_source,
    _fresh_confirmed_relay_output,
    evse_fault_reason,
)
from shelly_wallbox.backend.models import effective_supported_phase_selections, switch_feedback_mismatch
from shelly_wallbox.core.contracts import (
    finite_float_or_none,
    normalized_auto_state_pair,
    normalized_worker_snapshot,
    sanitized_auto_metrics,
)
from shelly_wallbox.core.shared import write_text_atomically
from shelly_wallbox.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin

WorkerSnapshot = dict[str, Any]
ErrorState = dict[str, int]
FailureState = dict[str, bool]
DefaultFactory = Callable[[], Any]



class _RuntimeSupportAuditMixin(_ComposableControllerMixin):
    @staticmethod
    def _backend_value(svc: Any, attribute_name: str, default: str = "") -> str:
        """Return one normalized backend label for logs and audit payloads."""
        raw_value = getattr(svc, attribute_name, default)
        normalized = str(raw_value).strip() if raw_value is not None else ""
        return normalized or default

    @classmethod
    def _charger_target_for_audit(cls, svc: Any) -> float | None:
        """Return the last applied native-charger target for diagnostics."""
        return finite_float_or_none(getattr(svc, "_charger_target_current_amps", None))

    @staticmethod
    def _charger_transport_reason_for_audit(svc: Any) -> str | None:
        """Return the active charger-transport reason when one is currently fresh."""
        return _fresh_charger_transport_reason(svc)

    @staticmethod
    def _charger_transport_source_for_audit(svc: Any) -> str | None:
        """Return the active charger-transport source when one is currently fresh."""
        return _fresh_charger_transport_source(svc)

    @staticmethod
    def _charger_retry_reason_for_audit(svc: Any) -> str | None:
        """Return the active charger-retry reason while retry backoff is active."""
        return _fresh_charger_retry_reason(svc)

    @staticmethod
    def _charger_retry_source_for_audit(svc: Any) -> str | None:
        """Return the active charger-retry source while retry backoff is active."""
        return _fresh_charger_retry_source(svc)

    @staticmethod
    def _observed_phase_for_audit(svc: Any) -> str | None:
        """Return the last observed phase selection from PM status or charger readback."""
        confirmed_pm_status = getattr(svc, "_last_confirmed_pm_status", None)
        if isinstance(confirmed_pm_status, dict):
            observed = _normalized_optional_audit_text(confirmed_pm_status.get("_phase_selection"))
            if observed is not None:
                return observed
        return _normalized_optional_audit_text(getattr(svc, "_last_charger_state_phase_selection", None))

    @staticmethod
    def _phase_mismatch_active_for_audit(svc: Any) -> bool:
        """Return whether a phase-switch mismatch is currently active."""
        if bool(getattr(svc, "_phase_switch_mismatch_active", False)):
            return True
        return str(getattr(svc, "_last_health_reason", "")) == "phase-switch-mismatch"

    @classmethod
    def _phase_lockout_active_for_audit(cls, svc: Any) -> bool:
        """Return whether a phase-switch lockout is currently active."""
        current_time = cls._callable_time_or_none(getattr(svc, "_time_now", None))
        if current_time is None:
            current_time = time.time()
        lockout_selection = getattr(svc, "_phase_switch_lockout_selection", None)
        lockout_until = finite_float_or_none(getattr(svc, "_phase_switch_lockout_until", None))
        return lockout_selection is not None and lockout_until is not None and float(current_time) < lockout_until

    @classmethod
    def _phase_lockout_target_for_audit(cls, svc: Any) -> str | None:
        """Return the currently locked-out target phase selection when active."""
        if not cls._phase_lockout_active_for_audit(svc):
            return None
        selection = getattr(svc, "_phase_switch_lockout_selection", None)
        if selection is None:
            return None
        normalized = str(selection).strip()
        return normalized or None

    @classmethod
    def _phase_supported_effective_for_audit(cls, svc: Any) -> str:
        """Return the effective supported phase layouts for audit output."""
        current_time = cls._callable_time_or_none(getattr(svc, "_time_now", None))
        effective_supported = effective_supported_phase_selections(
            getattr(svc, "supported_phase_selections", ("P1",)),
            lockout_selection=getattr(svc, "_phase_switch_lockout_selection", None),
            lockout_until=getattr(svc, "_phase_switch_lockout_until", None),
            now=current_time,
        )
        return ",".join(effective_supported)

    @classmethod
    def _phase_degraded_active_for_audit(cls, svc: Any) -> bool:
        """Return whether runtime phase support is currently degraded."""
        configured = ",".join(tuple(getattr(svc, "supported_phase_selections", ("P1",))))
        return configured != cls._phase_supported_effective_for_audit(svc)

    @staticmethod
    def _switch_feedback_closed_for_audit(svc: Any) -> bool | None:
        """Return the last explicit switch-feedback state when available."""
        value = getattr(svc, "_last_switch_feedback_closed", None)
        return None if value is None else bool(value)

    @staticmethod
    def _switch_interlock_ok_for_audit(svc: Any) -> bool | None:
        """Return the last explicit switch interlock state when available."""
        value = getattr(svc, "_last_switch_interlock_ok", None)
        return None if value is None else bool(value)

    @classmethod
    def _switch_feedback_mismatch_for_audit(cls, svc: Any) -> bool:
        """Return whether explicit switch feedback currently disagrees with relay state."""
        current_time = cls._callable_time_or_none(getattr(svc, "_time_now", None))
        relay_on = _fresh_confirmed_relay_output(svc, current_time)
        feedback_closed = cls._switch_feedback_closed_for_audit(svc)
        if feedback_closed is None:
            return str(getattr(svc, "_last_health_reason", "")) == "contactor-feedback-mismatch"
        return switch_feedback_mismatch(relay_on, feedback_closed)

    @staticmethod
    def _contactor_lockout_reason_for_audit(svc: Any) -> str | None:
        """Return the latched contactor-fault reason when present."""
        reason = str(getattr(svc, "_contactor_lockout_reason", "") or "").strip()
        return reason or None

    @classmethod
    def _contactor_lockout_active_for_audit(cls, svc: Any) -> bool:
        """Return whether a contactor-fault lockout is currently latched."""
        return cls._contactor_lockout_reason_for_audit(svc) is not None

    @classmethod
    def _contactor_fault_count_for_audit(cls, svc: Any) -> int:
        """Return the current contactor-fault counter for the active or latched reason."""
        counts = getattr(svc, "_contactor_fault_counts", None)
        if not isinstance(counts, dict):
            return 0
        reason = cls._contactor_lockout_reason_for_audit(svc)
        if reason is None:
            reason = _normalized_optional_audit_text(getattr(svc, "_contactor_fault_active_reason", ""))
        return 0 if reason is None else int(counts.get(reason, 0))

    @staticmethod
    def _evse_fault_reason_for_audit(svc: Any) -> str | None:
        """Return the active hard EVSE-fault reason when present."""
        return evse_fault_reason(getattr(svc, "_last_health_reason", ""))

    @classmethod
    def _evse_fault_active_for_audit(cls, svc: Any) -> bool:
        """Return whether one hard EVSE fault is currently active."""
        return cls._evse_fault_reason_for_audit(svc) is not None

    @staticmethod
    def _recovery_active_for_audit(svc: Any) -> bool:
        """Return whether the broad Auto state is currently in recovery mode."""
        state, _state_code = normalized_auto_state_pair(
            getattr(svc, "_last_auto_state", "idle"),
            getattr(svc, "_last_auto_state_code", 0),
        )
        return state == "recovery"

    @staticmethod
    def _callable_time_or_none(time_func: Any) -> float | None:
        """Return one numeric current time when a service helper provides it."""
        if not callable(time_func):
            return None
        raw_value = time_func()
        if not isinstance(raw_value, (int, float)):
            return None
        return float(raw_value)

    def _normalized_worker_snapshot(self, snapshot: WorkerSnapshot) -> WorkerSnapshot:
        """Return one worker snapshot normalized to the internal PM invariants."""
        current = self._callable_time_or_none(getattr(self.service, "_time_now", None))
        return normalized_worker_snapshot(snapshot, now=current)

    def ensure_worker_state(self) -> None:
        """Initialize worker helpers for tests or partially built instances."""
        self.ensure_missing_attributes(self.service, self.worker_state_defaults())

    def set_worker_snapshot(self, snapshot: WorkerSnapshot) -> None:
        """Publish the latest raw I/O results for the GLib thread."""
        svc = self.service
        svc._ensure_worker_state()
        cloned = self.clone_worker_snapshot(self._normalized_worker_snapshot(snapshot))
        with svc._worker_snapshot_lock:
            svc._worker_snapshot = cloned

    def update_worker_snapshot(self, **fields: Any) -> None:
        """Merge fresh worker fields into the published RAM snapshot immediately."""
        svc = self.service
        svc._ensure_worker_state()
        with svc._worker_snapshot_lock:
            merged = self.clone_worker_snapshot(svc._worker_snapshot)
            merged.update(fields)
            svc._worker_snapshot = self.clone_worker_snapshot(self._normalized_worker_snapshot(merged))

    def get_worker_snapshot(self) -> WorkerSnapshot:
        """Return the latest raw I/O results without blocking on network or DBus."""
        svc = self.service
        svc._ensure_worker_state()
        with svc._worker_snapshot_lock:
            return cast(WorkerSnapshot, self.clone_worker_snapshot(svc._worker_snapshot))

    def ensure_observability_state(self) -> None:
        """Initialize observability state for tests or partially constructed instances."""
        self.ensure_missing_attributes(self.service, self.observability_state_defaults())

    @staticmethod
    def _relay_state_for_audit(svc: Any) -> int:
        """Return the best-known relay state for audit output."""
        current_time = _RuntimeSupportAuditMixin._callable_time_or_none(getattr(svc, "_time_now", None))
        relay_on = _fresh_confirmed_relay_output(svc, current_time)
        return int(bool(relay_on))

    @staticmethod
    def _auto_audit_reason_detail(svc: Any, reason: str) -> str | None:
        """Return an optional audit detail for broad health reasons."""
        if reason != "auto-stop":
            return None
        stop_reason = getattr(svc, "auto_stop_condition_reason", None)
        if not isinstance(stop_reason, str):
            return None
        detail_map = {
            "auto-stop-surplus": "surplus",
            "auto-stop-grid": "grid",
            "auto-stop-soc": "soc",
        }
        return detail_map.get(stop_reason)

    @staticmethod
    def _bucket_metric(value: Any, *, step: float) -> float | None:
        """Bucket one metric so audit dedupe notices only material changes."""
        if value is None:
            return None
        if step <= 0:
            return float(value)
        return round(float(value) / step) * step

    @classmethod
    def _auto_audit_key(
        cls,
        svc: Any,
        reason: str,
        cached: bool,
    ) -> tuple[object, ...]:
        """Return a de-duplication key for audit entries."""
        metrics = cls._normalized_auto_audit_metrics(svc)
        state, _state_code = normalized_auto_state_pair(
            getattr(svc, "_last_auto_state", None),
            getattr(svc, "_last_auto_state_code", None),
        )
        return (
            str(reason),
            cls._auto_audit_reason_detail(svc, reason),
            int(bool(cached)),
            cls._relay_state_for_audit(svc),
            int(getattr(svc, "virtual_mode", 0)),
            int(bool(getattr(svc, "virtual_enable", 0))),
            int(bool(getattr(svc, "virtual_autostart", 0))),
            cls._string_metric(state),
            cls._string_metric(metrics.get("profile")),
            cls._string_metric(metrics.get("stop_alpha_stage")),
            cls._string_metric(metrics.get("threshold_mode")),
            cls._string_metric(metrics.get("learned_charge_power_state")),
            cls._bucket_metric(metrics.get("start_threshold"), step=50.0),
            cls._bucket_metric(metrics.get("stop_threshold"), step=50.0),
            cls._bucket_metric(metrics.get("threshold_scale"), step=0.02),
            cls._backend_value(svc, "backend_mode", "combined"),
            cls._backend_value(svc, "meter_backend_type", "shelly_combined"),
            cls._backend_value(svc, "switch_backend_type", "shelly_combined"),
            cls._string_metric(getattr(svc, "charger_backend_type", None)),
            cls._bucket_metric(cls._charger_target_for_audit(svc), step=1.0),
            cls._string_metric(cls._charger_transport_reason_for_audit(svc)),
            cls._string_metric(cls._charger_transport_source_for_audit(svc)),
            cls._string_metric(cls._charger_retry_reason_for_audit(svc)),
            cls._string_metric(cls._charger_retry_source_for_audit(svc)),
            cls._string_metric(cls._observed_phase_for_audit(svc)),
            int(cls._phase_mismatch_active_for_audit(svc)),
            cls._string_metric(cls._phase_lockout_target_for_audit(svc)),
            int(cls._phase_lockout_active_for_audit(svc)),
            cls._string_metric(cls._phase_supported_effective_for_audit(svc)),
            int(cls._phase_degraded_active_for_audit(svc)),
            metrics.get("switch_feedback"),
            metrics.get("switch_interlock"),
            int(metrics.get("switch_feedback_mismatch", 0)),
            int(metrics.get("contactor_fault_count", 0)),
            cls._string_metric(metrics.get("contactor_lockout_reason")),
            int(metrics.get("contactor_lockout", 0)),
            int(metrics.get("fault", 0)),
            cls._string_metric(metrics.get("fault_reason")),
            int(metrics.get("recovery", 0)),
        )

    @staticmethod
    def _string_metric(value: Any) -> str | None:
        """Return one optional metric value as normalized text."""
        return None if value is None else str(value)

    @classmethod
    def _format_auto_audit_line(cls, svc: Any, reason: str, cached: bool, now: float) -> str:
        """Return one human-readable audit line describing the current Auto state."""
        fields = cls._auto_audit_display_fields(svc)
        state, _state_code = normalized_auto_state_pair(
            getattr(svc, "_last_auto_state", None),
            getattr(svc, "_last_auto_state_code", None),
        )
        local_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        return (
            f"{int(now)}\t{local_time}\t"
            f"reason={reason}\t"
            f"detail={cls._auto_audit_reason_detail(svc, reason) or 'na'}\t"
            f"cached={int(bool(cached))}\t"
            f"state={state}\t"
            f"relay={cls._relay_state_for_audit(svc)}\t"
            f"mode={getattr(svc, 'virtual_mode', 'na')}\t"
            f"enable={int(bool(getattr(svc, 'virtual_enable', 0)))}\t"
            f"autostart={int(bool(getattr(svc, 'virtual_autostart', 0)))}\t"
            f"profile={fields['profile']}\t"
            f"start_threshold={fields['start_threshold']}\t"
            f"stop_threshold={fields['stop_threshold']}\t"
            f"learned_charge_power={fields['learned_charge_power']}\t"
            f"learned_charge_power_state={fields['learned_charge_power_state']}\t"
            f"threshold_scale={fields['threshold_scale']}\t"
            f"threshold_mode={fields['threshold_mode']}\t"
            f"backend_mode={fields['backend_mode']}\t"
            f"meter_backend={fields['meter_backend']}\t"
            f"switch_backend={fields['switch_backend']}\t"
            f"charger_backend={fields['charger_backend']}\t"
            f"charger_target={fields['charger_target']}\t"
            f"charger_transport_reason={fields['charger_transport_reason']}\t"
            f"charger_transport_source={fields['charger_transport_source']}\t"
            f"charger_retry_reason={fields['charger_retry_reason']}\t"
            f"charger_retry_source={fields['charger_retry_source']}\t"
            f"phase_observed={fields['phase_observed']}\t"
            f"phase_mismatch={fields['phase_mismatch']}\t"
            f"phase_lockout_target={fields['phase_lockout_target']}\t"
            f"phase_lockout={fields['phase_lockout']}\t"
            f"phase_effective={fields['phase_effective']}\t"
            f"phase_degraded={fields['phase_degraded']}\t"
            f"switch_feedback={fields['switch_feedback']}\t"
            f"switch_interlock={fields['switch_interlock']}\t"
            f"switch_feedback_mismatch={fields['switch_feedback_mismatch']}\t"
            f"contactor_fault_count={fields['contactor_fault_count']}\t"
            f"contactor_lockout_reason={fields['contactor_lockout_reason']}\t"
            f"contactor_lockout={fields['contactor_lockout']}\t"
            f"fault={fields['fault']}\t"
            f"fault_reason={fields['fault_reason']}\t"
            f"recovery={fields['recovery']}\t"
            f"stop_alpha={fields['stop_alpha']}\t"
            f"stop_alpha_stage={fields['stop_alpha_stage']}\t"
            f"surplus_volatility={fields['surplus_volatility']}\t"
            f"surplus={fields['surplus']}\t"
            f"grid={fields['grid']}\t"
            f"soc={fields['soc']}\n"
        )

    @classmethod
    def _auto_audit_display_fields(cls, svc: Any) -> dict[str, str]:
        """Return the formatted metric values used in one audit line."""
        metrics = cls._normalized_auto_audit_metrics(svc)
        specs = {
            "surplus": ("surplus", "{:.0f}W"),
            "grid": ("grid", "{:.0f}W"),
            "soc": ("soc", "{:.1f}%"),
            "profile": ("profile", None),
            "start_threshold": ("start_threshold", "{:.0f}W"),
            "stop_threshold": ("stop_threshold", "{:.0f}W"),
            "learned_charge_power": ("learned_charge_power", "{:.0f}W"),
            "learned_charge_power_state": ("learned_charge_power_state", None),
            "threshold_scale": ("threshold_scale", "{:.3f}"),
            "threshold_mode": ("threshold_mode", None),
            "backend_mode": ("backend_mode", None),
            "meter_backend": ("meter_backend", None),
            "switch_backend": ("switch_backend", None),
            "charger_backend": ("charger_backend", None),
            "charger_target": ("charger_target", "{:.1f}A"),
            "charger_transport_reason": ("charger_transport_reason", None),
            "charger_transport_source": ("charger_transport_source", None),
            "charger_retry_reason": ("charger_retry_reason", None),
            "charger_retry_source": ("charger_retry_source", None),
            "phase_observed": ("phase_observed", None),
            "phase_mismatch": ("phase_mismatch", None),
            "phase_lockout_target": ("phase_lockout_target", None),
            "phase_lockout": ("phase_lockout", None),
            "phase_effective": ("phase_effective", None),
            "phase_degraded": ("phase_degraded", None),
            "switch_feedback": ("switch_feedback", None),
            "switch_interlock": ("switch_interlock", None),
            "switch_feedback_mismatch": ("switch_feedback_mismatch", None),
            "contactor_fault_count": ("contactor_fault_count", None),
            "contactor_lockout_reason": ("contactor_lockout_reason", None),
            "contactor_lockout": ("contactor_lockout", None),
            "fault": ("fault", None),
            "fault_reason": ("fault_reason", None),
            "recovery": ("recovery", None),
            "stop_alpha": ("stop_alpha", "{:.2f}"),
            "stop_alpha_stage": ("stop_alpha_stage", None),
            "surplus_volatility": ("surplus_volatility", "{:.0f}W"),
        }
        return {name: cls._auto_audit_value_text(metrics.get(key), fmt) for name, (key, fmt) in specs.items()}

    @staticmethod
    def _normalized_auto_audit_metrics(svc: Any) -> dict[str, Any]:
        """Return one sanitized metric payload suitable for outward audit formatting."""
        metrics = sanitized_auto_metrics(getattr(svc, "_last_auto_metrics", {}) or {})
        metrics["backend_mode"] = _RuntimeSupportAuditMixin._backend_value(svc, "backend_mode", "combined")
        metrics["meter_backend"] = _RuntimeSupportAuditMixin._backend_value(
            svc,
            "meter_backend_type",
            "shelly_combined",
        )
        metrics["switch_backend"] = _RuntimeSupportAuditMixin._backend_value(
            svc,
            "switch_backend_type",
            "shelly_combined",
        )
        metrics["charger_backend"] = getattr(svc, "charger_backend_type", None) or "na"
        metrics["charger_target"] = _RuntimeSupportAuditMixin._charger_target_for_audit(svc)
        metrics["charger_transport_reason"] = _RuntimeSupportAuditMixin._charger_transport_reason_for_audit(svc)
        metrics["charger_transport_source"] = _RuntimeSupportAuditMixin._charger_transport_source_for_audit(svc)
        metrics["charger_retry_reason"] = _RuntimeSupportAuditMixin._charger_retry_reason_for_audit(svc)
        metrics["charger_retry_source"] = _RuntimeSupportAuditMixin._charger_retry_source_for_audit(svc)
        metrics["phase_observed"] = _RuntimeSupportAuditMixin._observed_phase_for_audit(svc)
        metrics["phase_mismatch"] = int(_RuntimeSupportAuditMixin._phase_mismatch_active_for_audit(svc))
        metrics["phase_lockout_target"] = _RuntimeSupportAuditMixin._phase_lockout_target_for_audit(svc)
        metrics["phase_lockout"] = int(_RuntimeSupportAuditMixin._phase_lockout_active_for_audit(svc))
        metrics["phase_effective"] = _RuntimeSupportAuditMixin._phase_supported_effective_for_audit(svc)
        metrics["phase_degraded"] = int(_RuntimeSupportAuditMixin._phase_degraded_active_for_audit(svc))
        switch_feedback = _RuntimeSupportAuditMixin._switch_feedback_closed_for_audit(svc)
        switch_interlock = _RuntimeSupportAuditMixin._switch_interlock_ok_for_audit(svc)
        metrics["switch_feedback"] = None if switch_feedback is None else int(switch_feedback)
        metrics["switch_interlock"] = None if switch_interlock is None else int(switch_interlock)
        metrics["switch_feedback_mismatch"] = int(_RuntimeSupportAuditMixin._switch_feedback_mismatch_for_audit(svc))
        metrics["contactor_fault_count"] = _RuntimeSupportAuditMixin._contactor_fault_count_for_audit(svc)
        metrics["contactor_lockout_reason"] = _RuntimeSupportAuditMixin._contactor_lockout_reason_for_audit(svc)
        metrics["contactor_lockout"] = int(_RuntimeSupportAuditMixin._contactor_lockout_active_for_audit(svc))
        metrics["fault"] = int(_RuntimeSupportAuditMixin._evse_fault_active_for_audit(svc))
        metrics["fault_reason"] = _RuntimeSupportAuditMixin._evse_fault_reason_for_audit(svc)
        metrics["recovery"] = int(_RuntimeSupportAuditMixin._recovery_active_for_audit(svc))
        return metrics

    @staticmethod
    def _auto_audit_value_text(value: Any, fmt: str | None) -> str:
        """Return the human-readable text for one audit metric value."""
        if value is None:
            return "na"
        if fmt is None:
            return str(value)
        return fmt.format(float(value))

    @staticmethod
    def _prune_auto_audit_payload(lines: list[str], cutoff_epoch: float) -> list[str]:
        """Keep only audit entries newer than the supplied cutoff epoch."""
        kept_lines = []
        for line in lines:
            if not line.strip():
                continue
            try:
                epoch_text = line.split("\t", 1)[0]
                if float(epoch_text) >= cutoff_epoch:
                    kept_lines.append(line)
            except (TypeError, ValueError):
                kept_lines.append(line)
        return kept_lines

    def _cleanup_auto_audit_log(self, now: float) -> None:
        """Prune old audit entries on a throttled cadence."""
        svc = self.service
        path = getattr(svc, "auto_audit_log_path", "").strip()
        if not self._auto_audit_cleanup_due(path, now):
            return
        svc._last_auto_audit_cleanup_at = now
        cutoff_epoch = self._auto_audit_cutoff_epoch(svc, now)
        if cutoff_epoch is None:
            return
        lines = self._load_auto_audit_lines(path)
        if lines is None:
            return
        kept_lines = self._prune_auto_audit_payload(lines, cutoff_epoch)
        if kept_lines == lines:
            return
        self._write_pruned_auto_audit_lines(path, kept_lines)

    def _auto_audit_cleanup_due(self, path: str, now: float) -> bool:
        """Return whether audit cleanup should run on this cycle."""
        svc = self.service
        if not path:
            return False
        return (now - float(getattr(svc, "_last_auto_audit_cleanup_at", 0.0))) >= 300.0

    @staticmethod
    def _auto_audit_cutoff_epoch(svc: Any, now: float) -> float | None:
        """Return the cutoff epoch for retained audit entries."""
        max_age_hours = float(getattr(svc, "auto_audit_log_max_age_hours", 168.0))
        if max_age_hours <= 0:
            return None
        return now - (max_age_hours * 3600.0)

    @staticmethod
    def _load_auto_audit_lines(path: str) -> list[str] | None:
        """Load existing audit lines, ignoring missing or unreadable files."""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.readlines()
        except FileNotFoundError:
            return None
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Auto audit cleanup skipped for %s: %s", path, error)
            return None

    @staticmethod
    def _write_pruned_auto_audit_lines(path: str, kept_lines: list[str]) -> None:
        """Persist one pruned audit payload with debug-only error handling."""
        try:
            write_text_atomically(path, "".join(kept_lines))
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Unable to prune auto audit log %s: %s", path, error)

    @staticmethod
    def _auto_audit_repeat_suppressed(
        audit_key: tuple[Any, ...],
        last_audit_key: tuple[Any, ...] | None,
        repeat_seconds: float,
        last_audit_event_at: float | None,
        now: float,
    ) -> bool:
        """Return whether one duplicate audit event should stay suppressed."""
        if audit_key != last_audit_key:
            return False
        if repeat_seconds <= 0:
            return True
        return last_audit_event_at is not None and (now - float(last_audit_event_at)) < repeat_seconds

    @staticmethod
    def _write_auto_audit_line(path: str, line: str) -> None:
        """Append one formatted audit line to disk."""
        log_dir = os.path.dirname(path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)

    def write_auto_audit_event(self, reason: str, cached: bool = False) -> None:
        """Append one audit entry when the Auto reason changes or stays active for long."""
        svc = self.service
        self.ensure_observability_state()
        if not getattr(svc, "auto_audit_log", False):
            return
        now = svc._time_now()
        audit_key = self._auto_audit_key(svc, reason, cached)
        repeat_seconds = float(getattr(svc, "auto_audit_log_repeat_seconds", 30.0))
        last_audit_key = getattr(svc, "_last_auto_audit_key", None)
        last_audit_event_at = getattr(svc, "_last_auto_audit_event_at", None)
        if self._auto_audit_repeat_suppressed(
            audit_key,
            last_audit_key,
            repeat_seconds,
            last_audit_event_at,
            now,
        ):
            self._cleanup_auto_audit_log(now)
            return
        self._cleanup_auto_audit_log(now)
        path = getattr(svc, "auto_audit_log_path", "").strip()
        if not path:
            return
        try:
            self._write_auto_audit_line(path, self._format_auto_audit_line(svc, reason, cached, now))
            svc._last_auto_audit_key = audit_key
            svc._last_auto_audit_event_at = now
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Unable to write auto audit log %s: %s", path, error)


def _normalized_optional_audit_text(value: object) -> str | None:
    """Return one stripped audit text or ``None`` when empty."""
    normalized = "" if value is None else str(value).strip()
    return normalized or None


__all__ = ["_RuntimeSupportAuditMixin"]
