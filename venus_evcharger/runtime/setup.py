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
        svc._last_combined_battery_charge_power_w = None
        svc._last_combined_battery_charge_power_at = None
        svc._last_combined_battery_discharge_power_w = None
        svc._last_combined_battery_discharge_power_at = None
        svc._last_combined_battery_net_power_w = None
        svc._last_combined_battery_net_power_at = None
        svc._last_combined_battery_ac_power_w = None
        svc._last_combined_battery_ac_power_at = None
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
            "battery_surplus_penalty_w": 0.0,
            "battery_support_mode": "idle",
            "battery_charge_power_w": None,
            "battery_discharge_power_w": None,
            "battery_charge_activity_ratio": None,
            "battery_discharge_activity_ratio": None,
            "battery_learning_profile_count": 0,
            "battery_observed_max_charge_power_w": None,
            "battery_observed_max_discharge_power_w": None,
            "battery_discharge_balance_coordination_policy_enabled": 0,
            "battery_discharge_balance_coordination_support_mode": "supported_only",
            "battery_discharge_balance_coordination_feasibility": "not_needed",
            "battery_discharge_balance_coordination_gate_active": 0,
            "battery_discharge_balance_coordination_start_error_w": 0.0,
            "battery_discharge_balance_coordination_penalty_w": 0.0,
            "battery_discharge_balance_coordination_advisory_active": 0,
            "battery_discharge_balance_coordination_advisory_reason": "",
            "battery_discharge_balance_victron_bias_enabled": 0,
            "battery_discharge_balance_victron_bias_active": 0,
            "battery_discharge_balance_victron_bias_source_id": "",
            "battery_discharge_balance_victron_bias_topology_key": "",
            "battery_discharge_balance_victron_bias_activation_mode": "always",
            "battery_discharge_balance_victron_bias_activation_gate_active": 0,
            "battery_discharge_balance_victron_bias_support_mode": "supported_only",
            "battery_discharge_balance_victron_bias_learning_profile_key": "",
            "battery_discharge_balance_victron_bias_learning_profile_action_direction": "",
            "battery_discharge_balance_victron_bias_learning_profile_site_regime": "",
            "battery_discharge_balance_victron_bias_learning_profile_direction": "",
            "battery_discharge_balance_victron_bias_learning_profile_day_phase": "",
            "battery_discharge_balance_victron_bias_learning_profile_reserve_phase": "",
            "battery_discharge_balance_victron_bias_learning_profile_ev_phase": "",
            "battery_discharge_balance_victron_bias_learning_profile_pv_phase": "",
            "battery_discharge_balance_victron_bias_learning_profile_battery_limit_phase": "",
            "battery_discharge_balance_victron_bias_learning_profile_sample_count": 0,
            "battery_discharge_balance_victron_bias_learning_profile_response_delay_seconds": None,
            "battery_discharge_balance_victron_bias_learning_profile_estimated_gain": None,
            "battery_discharge_balance_victron_bias_learning_profile_overshoot_count": 0,
            "battery_discharge_balance_victron_bias_learning_profile_settled_count": 0,
            "battery_discharge_balance_victron_bias_learning_profile_stability_score": None,
            "battery_discharge_balance_victron_bias_learning_profile_regime_consistency_score": None,
            "battery_discharge_balance_victron_bias_learning_profile_response_variance_score": None,
            "battery_discharge_balance_victron_bias_learning_profile_reproducibility_score": None,
            "battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second": None,
            "battery_discharge_balance_victron_bias_learning_profile_preferred_bias_limit_watts": None,
            "battery_discharge_balance_victron_bias_source_error_w": None,
            "battery_discharge_balance_victron_bias_pid_output_w": 0.0,
            "battery_discharge_balance_victron_bias_setpoint_w": None,
            "battery_discharge_balance_victron_bias_telemetry_clean": 0,
            "battery_discharge_balance_victron_bias_telemetry_clean_reason": "unknown",
            "battery_discharge_balance_victron_bias_response_delay_seconds": None,
            "battery_discharge_balance_victron_bias_estimated_gain": None,
            "battery_discharge_balance_victron_bias_overshoot_active": 0,
            "battery_discharge_balance_victron_bias_overshoot_count": 0,
            "battery_discharge_balance_victron_bias_overshoot_cooldown_active": 0,
            "battery_discharge_balance_victron_bias_overshoot_cooldown_reason": "",
            "battery_discharge_balance_victron_bias_overshoot_cooldown_until": None,
            "battery_discharge_balance_victron_bias_settling_active": 0,
            "battery_discharge_balance_victron_bias_settled_count": 0,
            "battery_discharge_balance_victron_bias_stability_score": None,
            "battery_discharge_balance_victron_bias_oscillation_lockout_enabled": 0,
            "battery_discharge_balance_victron_bias_oscillation_lockout_active": 0,
            "battery_discharge_balance_victron_bias_oscillation_lockout_reason": "",
            "battery_discharge_balance_victron_bias_oscillation_lockout_until": None,
            "battery_discharge_balance_victron_bias_oscillation_direction_change_count": 0,
            "battery_discharge_balance_victron_bias_recommended_kp": None,
            "battery_discharge_balance_victron_bias_recommended_ki": None,
            "battery_discharge_balance_victron_bias_recommended_kd": None,
            "battery_discharge_balance_victron_bias_recommended_deadband_watts": None,
            "battery_discharge_balance_victron_bias_recommended_max_abs_watts": None,
            "battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second": None,
            "battery_discharge_balance_victron_bias_recommended_activation_mode": "",
            "battery_discharge_balance_victron_bias_recommendation_confidence": None,
            "battery_discharge_balance_victron_bias_recommendation_regime_consistency_score": None,
            "battery_discharge_balance_victron_bias_recommendation_response_variance_score": None,
            "battery_discharge_balance_victron_bias_recommendation_reproducibility_score": None,
            "battery_discharge_balance_victron_bias_recommendation_reason": "disabled",
            "battery_discharge_balance_victron_bias_recommendation_profile_key": "",
            "battery_discharge_balance_victron_bias_recommendation_ini_snippet": "",
            "battery_discharge_balance_victron_bias_recommendation_hint": "",
            "battery_discharge_balance_victron_bias_auto_apply_enabled": 0,
            "battery_discharge_balance_victron_bias_auto_apply_active": 0,
            "battery_discharge_balance_victron_bias_auto_apply_reason": "disabled",
            "battery_discharge_balance_victron_bias_auto_apply_generation": 0,
            "battery_discharge_balance_victron_bias_auto_apply_observation_window_active": 0,
            "battery_discharge_balance_victron_bias_auto_apply_observation_window_until": None,
            "battery_discharge_balance_victron_bias_auto_apply_last_param": "",
            "battery_discharge_balance_victron_bias_auto_apply_suspend_active": 0,
            "battery_discharge_balance_victron_bias_auto_apply_suspend_reason": "",
            "battery_discharge_balance_victron_bias_auto_apply_suspend_until": None,
            "battery_discharge_balance_victron_bias_rollback_enabled": 0,
            "battery_discharge_balance_victron_bias_rollback_active": 0,
            "battery_discharge_balance_victron_bias_rollback_reason": "disabled",
            "battery_discharge_balance_victron_bias_rollback_stable_profile_key": "",
            "battery_discharge_balance_victron_bias_safe_state_active": 0,
            "battery_discharge_balance_victron_bias_safe_state_reason": "",
            "battery_discharge_balance_victron_bias_reason": "disabled",
        }
        svc._victron_ess_balance_pid_last_error_w = 0.0
        svc._victron_ess_balance_pid_last_at = None
        svc._victron_ess_balance_pid_integral_output_w = 0.0
        svc._victron_ess_balance_pid_last_output_w = 0.0
        svc._victron_ess_balance_last_write_at = None
        svc._victron_ess_balance_last_setpoint_w = None
        svc._victron_ess_balance_active_learning_profile_key = ""
        svc._victron_ess_balance_active_learning_profile_action_direction = ""
        svc._victron_ess_balance_active_learning_profile_site_regime = ""
        svc._victron_ess_balance_active_learning_profile_direction = ""
        svc._victron_ess_balance_active_learning_profile_day_phase = ""
        svc._victron_ess_balance_active_learning_profile_reserve_phase = ""
        svc._victron_ess_balance_active_learning_profile_ev_phase = ""
        svc._victron_ess_balance_active_learning_profile_pv_phase = ""
        svc._victron_ess_balance_active_learning_profile_battery_limit_phase = ""
        svc._victron_ess_balance_learning_profiles = {}
        svc._victron_ess_balance_auto_apply_generation = 0
        svc._victron_ess_balance_auto_apply_observe_until = None
        svc._victron_ess_balance_auto_apply_last_applied_param = ""
        svc._victron_ess_balance_auto_apply_last_applied_at = None
        svc._victron_ess_balance_recent_action_changes = []
        svc._victron_ess_balance_last_action_direction = ""
        svc._victron_ess_balance_oscillation_lockout_until = None
        svc._victron_ess_balance_oscillation_lockout_reason = ""
        svc._victron_ess_balance_last_stable_tuning = {}
        svc._victron_ess_balance_last_stable_at = None
        svc._victron_ess_balance_last_stable_profile_key = ""
        svc._victron_ess_balance_conservative_tuning = {}
        svc._victron_ess_balance_auto_apply_suspend_until = None
        svc._victron_ess_balance_auto_apply_suspend_reason = ""
        svc._victron_ess_balance_safe_state_active = False
        svc._victron_ess_balance_safe_state_reason = ""
        svc._victron_ess_balance_telemetry_last_command_at = None
        svc._victron_ess_balance_telemetry_last_command_setpoint_w = None
        svc._victron_ess_balance_telemetry_last_command_error_w = None
        svc._victron_ess_balance_telemetry_last_command_profile_key = ""
        svc._victron_ess_balance_telemetry_command_response_recorded = False
        svc._victron_ess_balance_telemetry_command_overshoot_recorded = False
        svc._victron_ess_balance_telemetry_command_settled_recorded = False
        svc._victron_ess_balance_telemetry_response_delay_seconds = None
        svc._victron_ess_balance_telemetry_delay_samples = 0
        svc._victron_ess_balance_telemetry_estimated_gain = None
        svc._victron_ess_balance_telemetry_gain_samples = 0
        svc._victron_ess_balance_telemetry_last_observed_error_w = None
        svc._victron_ess_balance_telemetry_last_observed_at = None
        svc._victron_ess_balance_telemetry_overshoot_active = False
        svc._victron_ess_balance_telemetry_overshoot_count = 0
        svc._victron_ess_balance_overshoot_cooldown_until = None
        svc._victron_ess_balance_overshoot_cooldown_reason = ""
        svc._victron_ess_balance_telemetry_settling_active = False
        svc._victron_ess_balance_telemetry_settled_count = 0
        svc._victron_ess_balance_telemetry_stability_score = None
        svc._victron_ess_balance_telemetry_last_grid_interaction_w = None
        svc._victron_ess_balance_telemetry_last_ac_power_w = None
        svc._victron_ess_balance_telemetry_last_ev_power_w = None
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

    def get_system_bus(self) -> Any:
        """Return the current thread-local DBus connection, reconnecting after resets."""
        svc = self.service
        svc._ensure_system_bus_state()
        state = svc._system_bus_state
        generation = int(getattr(svc, "_system_bus_generation", 0))
        bus = getattr(state, "bus", None)
        bus_generation = int(getattr(state, "generation", -1))
        if bus is None or bus_generation != generation:
            bus = self.create_system_bus()
            state.bus = bus
            state.generation = generation
        return bus

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
            "battery_headroom_charge_w": None,
            "battery_headroom_discharge_w": None,
            "expected_near_term_export_w": None,
            "expected_near_term_import_w": None,
            "battery_discharge_balance_mode": "",
            "battery_discharge_balance_target_distribution_mode": "",
            "battery_discharge_balance_error_w": None,
            "battery_discharge_balance_max_abs_error_w": None,
            "battery_discharge_balance_total_discharge_w": None,
            "battery_discharge_balance_eligible_source_count": 0,
            "battery_discharge_balance_active_source_count": 0,
            "battery_discharge_balance_control_candidate_count": 0,
            "battery_discharge_balance_control_ready_count": 0,
            "battery_discharge_balance_supported_control_source_count": 0,
            "battery_discharge_balance_experimental_control_source_count": 0,
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
        _clone_worker_status_payload(cloned)
        _clone_worker_battery_sources_payload(cloned)
        _clone_worker_learning_profiles_payload(cloned)
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


def _clone_worker_status_payload(snapshot: WorkerSnapshot) -> None:
    pm_status = snapshot.get("pm_status")
    if isinstance(pm_status, dict):
        snapshot["pm_status"] = dict(pm_status)


def _clone_worker_battery_sources_payload(snapshot: WorkerSnapshot) -> None:
    battery_sources = snapshot.get("battery_sources")
    if isinstance(battery_sources, list):
        snapshot["battery_sources"] = [
            dict(item) if isinstance(item, dict) else item
            for item in battery_sources
        ]


def _clone_worker_learning_profiles_payload(snapshot: WorkerSnapshot) -> None:
    learning_profiles = snapshot.get("battery_learning_profiles")
    if isinstance(learning_profiles, dict):
        snapshot["battery_learning_profiles"] = {
            str(key): dict(value) if isinstance(value, dict) else value
            for key, value in learning_profiles.items()
        }


__all__ = ["_RuntimeSupportSetupMixin"]
