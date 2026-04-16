# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime, worker-state, and watchdog helpers for the Shelly wallbox service.

This controller owns the "glue" state that keeps the service robust in the
field: cached worker snapshots, throttled warnings, watchdog recovery,
auto-audit logging, and safe persistence of runtime-only state.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import dbus
import requests
from shelly_wallbox.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin

WorkerSnapshot = dict[str, Any]



class _RuntimeSupportSetupMixin(_ComposableControllerMixin):
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
        svc._last_auto_state = "idle"
        svc._last_auto_state_code = 0
        svc._auto_cached_inputs_used = False
        svc._last_pv_value = None
        svc._last_pv_at = None
        svc._last_grid_value = None
        svc._last_grid_at = None
        svc._last_battery_soc_value = None
        svc._last_battery_soc_at = None
        svc._last_pm_status = None
        svc._last_pm_status_at = None
        svc._last_pm_status_confirmed = False
        svc._last_shelly_warning = None
        svc._last_auto_metrics = {
            "state": "idle",
            "surplus": None,
            "grid": None,
            "soc": None,
            "profile": "normal",
            "start_threshold": None,
            "stop_threshold": None,
            "learned_charge_power": None,
            "threshold_scale": 1.0,
            "threshold_mode": "static",
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
        svc._runtime_overrides_serialized = None
        svc._runtime_overrides_active = False
        svc._runtime_overrides_values = {}
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
            "pm_confirmed": False,
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
        svc.relay_sync_timeout_seconds = max(2.0, svc._worker_poll_interval_seconds * 3.0)
        svc._relay_sync_expected_state = None
        svc._relay_sync_requested_at = None
        svc._relay_sync_deadline_at = None
        svc._relay_sync_failure_reported = False
        svc._auto_input_helper_process = None
        svc._auto_input_helper_last_start_at = 0.0
        svc._auto_input_helper_restart_requested_at = None
        svc._auto_input_snapshot_last_seen = None
        svc._auto_input_snapshot_mtime_ns = None
        svc._auto_input_snapshot_last_captured_at = None
        svc._auto_input_snapshot_version = None


__all__ = ["_RuntimeSupportSetupMixin"]
