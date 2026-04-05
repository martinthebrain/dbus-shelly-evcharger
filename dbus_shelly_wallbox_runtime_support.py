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

from dbus_shelly_wallbox_shared import write_text_atomically

WorkerSnapshot = dict[str, Any]
ErrorState = dict[str, int]
FailureState = dict[str, bool]
DefaultFactory = Callable[[], Any]


class RuntimeSupportController:
    """Encapsulate runtime caches, worker state, and observability/watchdog logic."""

    SOURCE_ERROR_KEYS: tuple[str, ...] = ("dbus", "shelly", "pv", "battery", "grid")

    def __init__(
        self,
        service: Any,
        age_seconds_func: Callable[[float | int | None, float | int | None], int],
        health_code_func: Callable[[str], int],
    ) -> None:
        self.service = service
        self._age_seconds = age_seconds_func
        self._health_code = health_code_func

    @classmethod
    def new_error_state(cls) -> ErrorState:
        """Return the default per-source error counters."""
        state = {key: 0 for key in cls.SOURCE_ERROR_KEYS}
        state["cache_hits"] = 0
        return state

    @classmethod
    def new_failure_state(cls) -> FailureState:
        """Return the default active/inactive failure flags."""
        return {key: False for key in cls.SOURCE_ERROR_KEYS}

    def initialize_runtime_support(self) -> None:
        """Initialize runtime caches and watchdog state kept in RAM only."""
        svc = self.service
        svc.last_update = 0
        svc.session = requests.Session()
        svc._system_bus = None
        svc._system_bus_state = threading.local()
        svc._system_bus_generation = 0
        svc._system_bus_generation_lock = threading.Lock()
        svc._resolved_auto_pv_services = []
        svc._auto_pv_last_scan = 0.0
        svc._last_pv_missing_warning = None
        svc._resolved_auto_battery_service = None
        svc._auto_battery_last_scan = 0.0
        svc._last_battery_missing_warning = None
        svc._last_battery_allow_warning = None
        svc._last_grid_missing_warning = None
        svc._dbus_list_backoff_until = 0.0
        svc._dbus_list_failures = 0
        svc._warning_state = {}
        svc._error_state = self.new_error_state()
        svc._failure_active = self.new_failure_state()
        svc._last_health_reason = "init"
        svc._last_health_code = self._health_code(svc._last_health_reason)
        svc._auto_cached_inputs_used = False
        svc._last_pv_value = None
        svc._last_pv_at = None
        svc._last_grid_value = None
        svc._last_grid_at = None
        svc._last_battery_soc_value = None
        svc._last_battery_soc_at = None
        svc._last_pm_status = None
        svc._last_pm_status_at = None
        svc._last_shelly_warning = None
        svc._last_auto_metrics = {
            "surplus": None,
            "grid": None,
            "soc": None,
            "profile": "normal",
            "start_threshold": None,
            "stop_threshold": None,
            "stop_alpha": None,
            "stop_alpha_stage": "base",
            "surplus_volatility": None,
        }
        svc._last_voltage = None
        svc._last_dbus_ok_at = None
        svc._last_successful_update_at = None
        svc._last_recovery_attempt_at = None
        svc._recovery_attempts = 0
        svc._runtime_state_serialized = None
        svc._dbus_publish_state = {}
        svc._dbus_live_publish_interval_seconds = 1.0
        svc._dbus_slow_publish_interval_seconds = 5.0
        svc._last_auto_audit_key = None
        svc._last_auto_audit_event_at = None
        svc._last_auto_audit_cleanup_at = 0.0
        svc.started_at = time.time()

    def reset_system_bus(self) -> None:
        """Invalidate cached DBus connections so each thread reconnects cleanly."""
        svc = self.service
        svc._ensure_system_bus_state()
        with svc._system_bus_generation_lock:
            svc._system_bus_generation += 1
        svc._system_bus = None
        svc._system_bus_state.bus = None
        svc._system_bus_state.generation = -1

    def ensure_system_bus_state(self) -> None:
        """Initialize per-thread DBus connection helpers for partial test instances."""
        svc = self.service
        if not hasattr(svc, "_system_bus_state"):
            svc._system_bus_state = threading.local()
        if not hasattr(svc, "_system_bus_generation"):
            svc._system_bus_generation = 0
        if not hasattr(svc, "_system_bus_generation_lock"):
            svc._system_bus_generation_lock = threading.Lock()

    @staticmethod
    def create_system_bus() -> Any:
        """Create a fresh DBus connection for the current thread."""
        return dbus.SystemBus(private=True)

    @staticmethod
    def empty_worker_snapshot() -> WorkerSnapshot:
        """Return a default RAM snapshot for the background I/O worker."""
        return {
            "captured_at": 0.0,
            "pm_captured_at": None,
            "pm_status": None,
            "pv_captured_at": None,
            "pv_power": None,
            "battery_captured_at": None,
            "battery_soc": None,
            "grid_captured_at": None,
            "grid_power": None,
            "auto_mode_active": False,
        }

    @staticmethod
    def clone_worker_snapshot(snapshot: WorkerSnapshot) -> WorkerSnapshot:
        """Copy the worker snapshot so the main loop sees a stable view."""
        cloned = dict(snapshot)
        if isinstance(cloned.get("pm_status"), dict):
            cloned["pm_status"] = dict(cloned["pm_status"])
        return cloned

    def init_worker_state(self) -> None:
        """Initialize the background I/O worker state kept in RAM."""
        svc = self.service
        svc._worker_poll_interval_seconds = max(0.2, svc.poll_interval_ms / 1000.0)
        svc._worker_snapshot_lock = threading.Lock()
        svc._relay_command_lock = threading.Lock()
        svc._worker_snapshot = self.empty_worker_snapshot()
        svc._worker_stop_event = threading.Event()
        svc._worker_session = requests.Session()
        svc._worker_thread = None
        svc._pending_relay_state = None
        svc._pending_relay_requested_at = None
        svc._auto_input_helper_process = None
        svc._auto_input_helper_last_start_at = 0.0
        svc._auto_input_helper_restart_requested_at = None
        svc._auto_input_snapshot_last_seen = None
        svc._auto_input_snapshot_mtime_ns = None

    def worker_state_defaults(self) -> dict[str, DefaultFactory]:
        """Return lazy default values for worker-side runtime state."""
        svc = self.service
        return {
            "_worker_poll_interval_seconds": lambda: max(
                0.2,
                getattr(svc, "poll_interval_ms", 1000) / 1000.0,
            ),
            "_worker_snapshot_lock": threading.Lock,
            "_relay_command_lock": threading.Lock,
            "_worker_snapshot": self.empty_worker_snapshot,
            "_worker_stop_event": threading.Event,
            "_worker_session": requests.Session,
            "_worker_thread": lambda: None,
            "_pending_relay_state": lambda: None,
            "_pending_relay_requested_at": lambda: None,
            "_auto_input_helper_process": lambda: None,
            "_auto_input_helper_last_start_at": lambda: 0.0,
            "_auto_input_helper_restart_requested_at": lambda: None,
            "_auto_input_snapshot_last_seen": lambda: None,
            "_auto_input_snapshot_mtime_ns": lambda: None,
            "auto_input_snapshot_path": lambda: (
                f"/run/dbus-shelly-wallbox-auto-{getattr(svc, 'deviceinstance', 0)}.json"
            ),
            "auto_input_helper_restart_seconds": lambda: 5.0,
            "auto_input_helper_stale_seconds": lambda: 15.0,
            "_auto_mode_cutover_pending": lambda: False,
            "_ignore_min_offtime_once": lambda: False,
        }

    @staticmethod
    def observability_state_defaults() -> dict[str, DefaultFactory]:
        """Return lazy default values for diagnostics and watchdog state."""
        return {
            "_warning_state": dict,
            "_error_state": RuntimeSupportController.new_error_state,
            "_failure_active": RuntimeSupportController.new_failure_state,
            "_last_dbus_ok_at": lambda: None,
            "_last_successful_update_at": lambda: None,
            "_last_recovery_attempt_at": lambda: None,
            "_recovery_attempts": lambda: 0,
            "_last_pm_status_at": lambda: None,
            "_last_pv_at": lambda: None,
            "_last_battery_soc_at": lambda: None,
            "_last_grid_at": lambda: None,
            "_grid_recovery_required": lambda: False,
            "_grid_recovery_since": lambda: None,
            "_source_retry_after": dict,
            "_last_auto_audit_key": lambda: None,
            "_last_auto_audit_event_at": lambda: None,
            "_last_auto_audit_cleanup_at": lambda: 0.0,
            "_auto_high_soc_profile_active": lambda: None,
            "started_at": time.time,
            "auto_watchdog_stale_seconds": lambda: 180.0,
            "auto_watchdog_recovery_seconds": lambda: 60.0,
            "auto_audit_log": lambda: False,
            "auto_audit_log_path": lambda: "/var/volatile/log/dbus-shelly-wallbox/auto-reasons.log",
            "auto_audit_log_max_age_hours": lambda: 168.0,
            "auto_audit_log_repeat_seconds": lambda: 30.0,
        }

    @staticmethod
    def ensure_missing_attributes(service: Any, defaults: dict[str, DefaultFactory]) -> None:
        """Populate any missing attributes from a lazy defaults mapping."""
        for name, factory in defaults.items():
            if hasattr(service, name):
                continue
            setattr(service, name, factory())

    def ensure_worker_state(self) -> None:
        """Initialize worker helpers for tests or partially built instances."""
        self.ensure_missing_attributes(self.service, self.worker_state_defaults())

    def set_worker_snapshot(self, snapshot: WorkerSnapshot) -> None:
        """Publish the latest raw I/O results for the GLib thread."""
        svc = self.service
        svc._ensure_worker_state()
        cloned = self.clone_worker_snapshot(snapshot)
        with svc._worker_snapshot_lock:
            svc._worker_snapshot = cloned

    def update_worker_snapshot(self, **fields: Any) -> None:
        """Merge fresh worker fields into the published RAM snapshot immediately."""
        svc = self.service
        svc._ensure_worker_state()
        with svc._worker_snapshot_lock:
            merged = self.clone_worker_snapshot(svc._worker_snapshot)
            merged.update(fields)
            svc._worker_snapshot = merged

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
        pm_status = getattr(svc, "_last_pm_status", None)
        if isinstance(pm_status, dict) and "output" in pm_status:
            return int(bool(pm_status.get("output")))
        return int(bool(getattr(svc, "virtual_startstop", 0)))

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

    @classmethod
    def _auto_audit_key(
        cls,
        svc: Any,
        reason: str,
        cached: bool,
    ) -> tuple[str, str | None, int, int, int, int, int, str | None, str | None]:
        """Return a de-duplication key for audit entries."""
        metrics = getattr(svc, "_last_auto_metrics", {}) or {}
        return (
            str(reason),
            cls._auto_audit_reason_detail(svc, reason),
            int(bool(cached)),
            cls._relay_state_for_audit(svc),
            int(getattr(svc, "virtual_mode", 0)),
            int(bool(getattr(svc, "virtual_enable", 0))),
            int(bool(getattr(svc, "virtual_autostart", 0))),
            str(metrics.get("profile")) if metrics.get("profile") is not None else None,
            str(metrics.get("stop_alpha_stage")) if metrics.get("stop_alpha_stage") is not None else None,
        )

    @classmethod
    def _format_auto_audit_line(cls, svc: Any, reason: str, cached: bool, now: float) -> str:
        """Return one human-readable audit line describing the current Auto state."""
        metrics = getattr(svc, "_last_auto_metrics", {}) or {}
        detail = cls._auto_audit_reason_detail(svc, reason)
        surplus_value = metrics.get("surplus")
        grid_value = metrics.get("grid")
        soc_value = metrics.get("soc")
        profile_value = metrics.get("profile")
        start_threshold_value = metrics.get("start_threshold")
        stop_threshold_value = metrics.get("stop_threshold")
        stop_alpha_value = metrics.get("stop_alpha")
        stop_alpha_stage_value = metrics.get("stop_alpha_stage")
        surplus_volatility_value = metrics.get("surplus_volatility")
        surplus_text = f"{float(surplus_value):.0f}W" if surplus_value is not None else "na"
        grid_text = f"{float(grid_value):.0f}W" if grid_value is not None else "na"
        soc_text = f"{float(soc_value):.1f}%" if soc_value is not None else "na"
        profile_text = str(profile_value) if profile_value is not None else "na"
        start_threshold_text = f"{float(start_threshold_value):.0f}W" if start_threshold_value is not None else "na"
        stop_threshold_text = f"{float(stop_threshold_value):.0f}W" if stop_threshold_value is not None else "na"
        stop_alpha_text = f"{float(stop_alpha_value):.2f}" if stop_alpha_value is not None else "na"
        stop_alpha_stage_text = str(stop_alpha_stage_value) if stop_alpha_stage_value is not None else "na"
        surplus_volatility_text = (
            f"{float(surplus_volatility_value):.0f}W" if surplus_volatility_value is not None else "na"
        )
        local_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        return (
            f"{int(now)}\t{local_time}\t"
            f"reason={reason}\t"
            f"detail={detail if detail is not None else 'na'}\t"
            f"cached={int(bool(cached))}\t"
            f"relay={cls._relay_state_for_audit(svc)}\t"
            f"mode={getattr(svc, 'virtual_mode', 'na')}\t"
            f"enable={int(bool(getattr(svc, 'virtual_enable', 0)))}\t"
            f"autostart={int(bool(getattr(svc, 'virtual_autostart', 0)))}\t"
            f"profile={profile_text}\t"
            f"start_threshold={start_threshold_text}\t"
            f"stop_threshold={stop_threshold_text}\t"
            f"stop_alpha={stop_alpha_text}\t"
            f"stop_alpha_stage={stop_alpha_stage_text}\t"
            f"surplus_volatility={surplus_volatility_text}\t"
            f"surplus={surplus_text}\t"
            f"grid={grid_text}\t"
            f"soc={soc_text}\n"
        )

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
        if not path:
            return
        if (now - float(getattr(svc, "_last_auto_audit_cleanup_at", 0.0))) < 300.0:
            return
        svc._last_auto_audit_cleanup_at = now
        max_age_hours = float(getattr(svc, "auto_audit_log_max_age_hours", 168.0))
        if max_age_hours <= 0:
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except FileNotFoundError:
            return
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Auto audit cleanup skipped for %s: %s", path, error)
            return
        cutoff_epoch = now - (max_age_hours * 3600.0)
        kept_lines = self._prune_auto_audit_payload(lines, cutoff_epoch)
        if kept_lines == lines:
            return
        try:
            write_text_atomically(path, "".join(kept_lines))
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Unable to prune auto audit log %s: %s", path, error)

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
        if audit_key == last_audit_key:
            if repeat_seconds <= 0:
                self._cleanup_auto_audit_log(now)
                return
            if last_audit_event_at is not None and (now - float(last_audit_event_at)) < repeat_seconds:
                self._cleanup_auto_audit_log(now)
                return
        self._cleanup_auto_audit_log(now)
        path = getattr(svc, "auto_audit_log_path", "").strip()
        if not path:
            return
        try:
            log_dir = os.path.dirname(path)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(self._format_auto_audit_line(svc, reason, cached, now))
            svc._last_auto_audit_key = audit_key
            svc._last_auto_audit_event_at = now
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Unable to write auto audit log %s: %s", path, error)

    def is_update_stale(self, now: float | None = None) -> bool:
        """Return True when no successful full update was completed recently."""
        svc = self.service
        svc._ensure_observability_state()
        current = time.time() if now is None else float(now)
        if svc.auto_watchdog_stale_seconds <= 0:
            return False
        if svc._last_successful_update_at is None:
            return (current - svc.started_at) > svc.auto_watchdog_stale_seconds
        return (current - svc._last_successful_update_at) > svc.auto_watchdog_stale_seconds

    @staticmethod
    def _watchdog_base_timestamp(svc: Any) -> float:
        """Return the timestamp used as the stale-age origin."""
        return svc._last_successful_update_at if svc._last_successful_update_at is not None else svc.started_at

    @staticmethod
    def _watchdog_recovery_suppressed(svc: Any, now: float) -> bool:
        """Return whether watchdog recovery is currently rate-limited."""
        if svc.auto_watchdog_recovery_seconds <= 0 and svc._last_recovery_attempt_at is not None:
            return True
        return (
            svc._last_recovery_attempt_at is not None
            and (now - svc._last_recovery_attempt_at) < svc.auto_watchdog_recovery_seconds
        )

    @staticmethod
    def _perform_watchdog_reset(svc: Any) -> None:
        """Reset lightweight in-memory discovery state during watchdog recovery."""
        svc._reset_system_bus()
        svc._invalidate_auto_pv_services()
        svc._invalidate_auto_battery_service()
        svc._dbus_list_backoff_until = 0.0

    def watchdog_recover(self, now: float) -> None:
        """Perform low-risk in-memory recovery steps after prolonged stale periods."""
        svc = self.service
        if not svc._is_update_stale(now):
            return
        if self._watchdog_recovery_suppressed(svc, now):
            return

        svc._last_recovery_attempt_at = now
        svc._recovery_attempts += 1
        self._perform_watchdog_reset(svc)
        logging.warning(
            "Watchdog recovery attempt %s after stale update period of %ss (%s)",
            svc._recovery_attempts,
            self._age_seconds(self._watchdog_base_timestamp(svc), now),
            svc._state_summary(),
        )

    def warning_throttled(
        self,
        key: str,
        interval_seconds: float,
        message: str,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Log a warning at most once per interval for the given key."""
        svc = self.service
        svc._ensure_observability_state()
        now = time.time()
        last_logged = svc._warning_state.get(key)
        if last_logged is None or (now - last_logged) > interval_seconds:
            logging.warning(message, *args, **kwargs)
            svc._warning_state[key] = now

    def mark_failure(self, key: str) -> None:
        """Track a failure streak for counters and later recovery logs."""
        svc = self.service
        svc._ensure_observability_state()
        if key in svc._error_state:
            svc._error_state[key] += 1
        if key in svc._failure_active:
            svc._failure_active[key] = True

    def mark_recovery(self, key: str, message: str, *args: Any) -> None:
        """Emit a one-time recovery log when a failing source becomes healthy again."""
        svc = self.service
        svc._ensure_observability_state()
        if svc._failure_active.get(key):
            logging.info(message, *args)
            svc._failure_active[key] = False
        svc._source_retry_after[key] = 0.0

    def source_retry_ready(self, key: str, now: float | None = None) -> bool:
        """Return True when a data source may be queried again."""
        svc = self.service
        svc._ensure_observability_state()
        current = time.time() if now is None else float(now)
        return current >= float(svc._source_retry_after.get(key, 0.0))

    def delay_source_retry(self, key: str, now: float | None = None) -> None:
        """Delay repeated retries for a failing data source to keep the main loop responsive."""
        svc = self.service
        svc._ensure_observability_state()
        current = time.time() if now is None else float(now)
        delay = max(1.0, float(getattr(svc, "auto_dbus_backoff_base_seconds", 5.0)))
        svc._source_retry_after[key] = current + delay
