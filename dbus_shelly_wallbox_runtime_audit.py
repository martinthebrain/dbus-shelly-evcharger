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
import threading
import time
from typing import Any

import dbus
import requests

from dbus_shelly_wallbox_common import _fresh_confirmed_relay_output
from dbus_shelly_wallbox_contracts import (
    normalized_auto_state_pair,
    normalized_worker_snapshot,
    sanitized_auto_metrics,
)
from dbus_shelly_wallbox_shared import write_text_atomically
from dbus_shelly_wallbox_split_mixins import _ComposableControllerMixin

WorkerSnapshot = dict[str, Any]
ErrorState = dict[str, int]
FailureState = dict[str, bool]
DefaultFactory = Callable[[], Any]



class _RuntimeSupportAuditMixin(_ComposableControllerMixin):
    def _normalized_worker_snapshot(self, snapshot: WorkerSnapshot) -> WorkerSnapshot:
        """Return one worker snapshot normalized to the internal PM invariants."""
        time_now = getattr(self.service, "_time_now", None)
        current = float(time_now()) if callable(time_now) else None
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
            return self.clone_worker_snapshot(svc._worker_snapshot)

    def ensure_observability_state(self) -> None:
        """Initialize observability state for tests or partially constructed instances."""
        self.ensure_missing_attributes(self.service, self.observability_state_defaults())

    @staticmethod
    def _relay_state_for_audit(svc: Any) -> int:
        """Return the best-known relay state for audit output."""
        time_now = getattr(svc, "_time_now", None)
        current_time = float(time_now()) if callable(time_now) else None
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
    ) -> tuple[
        str,
        str | None,
        int,
        int,
        int,
        int,
        int,
        str | None,
        str | None,
        str | None,
        str | None,
        str | None,
        float | None,
        float | None,
        float | None,
    ]:
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
            "stop_alpha": ("stop_alpha", "{:.2f}"),
            "stop_alpha_stage": ("stop_alpha_stage", None),
            "surplus_volatility": ("surplus_volatility", "{:.0f}W"),
        }
        return {name: cls._auto_audit_value_text(metrics.get(key), fmt) for name, (key, fmt) in specs.items()}

    @staticmethod
    def _normalized_auto_audit_metrics(svc: Any) -> dict[str, Any]:
        """Return one sanitized metric payload suitable for outward audit formatting."""
        return sanitized_auto_metrics(getattr(svc, "_last_auto_metrics", {}) or {})

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
