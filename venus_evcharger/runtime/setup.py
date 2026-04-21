# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime, worker-state, and watchdog helpers for the Venus EV charger service.

This controller owns the "glue" state that keeps the service robust in the
field: cached worker snapshots, throttled warnings, watchdog recovery,
auto-audit logging, and safe persistence of runtime-only state.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import dbus
import requests
from venus_evcharger.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin

WorkerSnapshot = dict[str, Any]


def _first_existing_version_line(paths: tuple[str, ...]) -> str:
    """Return the first non-empty version line found in the candidate files."""
    for path in paths:
        version = _read_version_line(path)
        if version:
            return version
    return ""


def _read_version_line(path: str) -> str:
    """Return one stripped version line from a file when it exists."""
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.readline().strip()
    except OSError:
        return ""



class _RuntimeSupportSetupMixin(_ComposableControllerMixin):
    @staticmethod
    def _service_repo_root(service: Any) -> str:
        """Return the repository root inferred from the main entrypoint path."""
        script_path = getattr(type(service), "_script_path_value", getattr(service, "_script_path_value", ""))
        resolved = os.path.realpath(str(script_path or ""))
        return os.path.dirname(resolved) if resolved else ""

    @staticmethod
    def _system_uptime_seconds() -> float | None:
        """Return the current Linux uptime from ``/proc/uptime`` when available."""
        try:
            with open("/proc/uptime", "r", encoding="utf-8") as handle:
                first_field = handle.readline().split(" ", 1)[0].strip()
        except OSError:
            return None
        try:
            return max(0.0, float(first_field))
        except ValueError:
            return None

    @classmethod
    def _boot_delayed_update_due_at(cls, current_time: float, delay_seconds: float) -> float | None:
        """Return the due timestamp for the post-boot auto-update when applicable."""
        uptime_seconds = cls._system_uptime_seconds()
        if uptime_seconds is None or uptime_seconds >= delay_seconds:
            return None
        return float(current_time) + max(0.0, float(delay_seconds) - uptime_seconds)

    @staticmethod
    def _read_local_version(repo_root: str) -> str:
        """Return the locally installed wallbox version or an empty string."""
        candidates = (
            os.path.join(repo_root, ".bootstrap-state", "installed_version"),
            os.path.join(repo_root, "version.txt"),
        )
        return _first_existing_version_line(candidates)

    def initialize_runtime_support(self) -> None:
        """Initialize runtime caches and watchdog state kept in RAM only."""
        svc = self.service
        repo_root = self._service_repo_root(svc)
        started_at = time.time()
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
        svc._resolved_auto_energy_services = {}
        svc._auto_energy_last_scan = {}
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
        svc._last_combined_battery_soc_value = None
        svc._last_combined_battery_soc_at = None
        svc._last_energy_cluster = {}
        svc._last_energy_learning_profiles = {}
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
        svc._runtime_overrides_last_saved_at = None
        svc._runtime_overrides_pending_serialized = None
        svc._runtime_overrides_pending_values = None
        svc._runtime_overrides_pending_text = None
        svc._runtime_overrides_pending_due_at = None
        svc._runtime_overrides_active = False
        svc._runtime_overrides_values = {}
        svc.runtime_overrides_write_min_interval_seconds = 1.0
        svc._dbus_publish_state = {}
        svc._dbus_live_publish_interval_seconds = 1.0
        svc._dbus_slow_publish_interval_seconds = 5.0
        svc._last_auto_audit_key = None
        svc._last_auto_audit_event_at = None
        svc._last_auto_audit_cleanup_at = 0.0
        svc.started_at = started_at
        svc.software_update_repo_root = repo_root
        svc.software_update_install_script = os.path.join(repo_root, "install.sh") if repo_root else ""
        svc.software_update_restart_script = (
            os.path.join(repo_root, "deploy/venus/restart_venus_evcharger_service.sh") if repo_root else ""
        )
        svc.software_update_no_update_file = os.path.join(repo_root, "noUpdate") if repo_root else ""
        svc.software_update_log_path = "/var/volatile/log/dbus-venus-evcharger/software-update.log"
        svc.software_update_repo_slug = os.environ.get(
            "VENUS_EVCHARGER_REPO_SLUG",
            "martinthebrain/venus-evcharger-service",
        )
        svc.software_update_channel = os.environ.get("VENUS_EVCHARGER_CHANNEL", "main")
        svc.software_update_manifest_source = os.environ.get(
            "VENUS_EVCHARGER_MANIFEST_SOURCE",
            f"https://raw.githubusercontent.com/{svc.software_update_repo_slug}/{svc.software_update_channel}/deploy/venus/bootstrap_manifest.json",
        )
        svc.software_update_version_source = os.environ.get(
            "VENUS_EVCHARGER_VERSION_SOURCE",
            f"https://raw.githubusercontent.com/{svc.software_update_repo_slug}/{svc.software_update_channel}/version.txt",
        )
        svc._software_update_current_version = self._read_local_version(repo_root)
        svc._software_update_available_version = ""
        svc._software_update_available = False
        svc._software_update_state = "idle"
        svc._software_update_detail = ""
        svc._software_update_last_check_at = None
        svc._software_update_last_run_at = None
        svc._software_update_last_result = ""
        svc._software_update_process = None
        svc._software_update_process_log_handle = None
        svc._software_update_run_requested_at = None
        svc._software_update_no_update_active = int(
            bool(svc.software_update_no_update_file and os.path.isfile(svc.software_update_no_update_file))
        )
        svc._software_update_next_check_at = started_at + 300.0
        svc._software_update_boot_auto_due_at = self._boot_delayed_update_due_at(started_at, 3600.0)

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
            "battery_combined_soc": None,
            "battery_combined_usable_capacity_wh": None,
            "battery_combined_charge_power_w": None,
            "battery_combined_discharge_power_w": None,
            "battery_combined_net_power_w": None,
            "battery_combined_ac_power_w": None,
            "battery_source_count": 0,
            "battery_online_source_count": 0,
            "battery_valid_soc_source_count": 0,
            "battery_sources": [],
            "battery_learning_profiles": {},
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
        if isinstance(cloned.get("battery_sources"), list):
            cloned["battery_sources"] = [
                dict(item) if isinstance(item, dict) else item for item in cloned["battery_sources"]
            ]
        if isinstance(cloned.get("battery_learning_profiles"), dict):
            cloned["battery_learning_profiles"] = {
                str(key): dict(value) if isinstance(value, dict) else value
                for key, value in cloned["battery_learning_profiles"].items()
            }
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
