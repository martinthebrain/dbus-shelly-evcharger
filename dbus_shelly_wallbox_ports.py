# SPDX-License-Identifier: GPL-3.0-or-later
"""Small controller ports that decouple controllers from the full service object."""

from __future__ import annotations

from typing import Any


class _BaseServicePort:
    """Common forwarding helpers for controller-specific service ports."""

    _ALLOWED_ATTRS: set[str] = set()
    _MUTABLE_ATTRS: set[str] = set()
    _ALLOWED_METHODS: set[str] = set()

    def __init__(self, service: Any) -> None:
        object.__setattr__(self, "_service", service)

    def _resolve_compat_method_alias(self, name: str) -> Any:
        """Map legacy ``_method`` lookups to public ``method`` names when available."""
        if not name.startswith("_"):
            return None
        public_name = name[1:]
        descriptor = getattr(type(self), public_name, None)
        if descriptor is None:
            return None
        return getattr(self, public_name)

    def __getattr__(self, name: str) -> Any:
        alias = self._resolve_compat_method_alias(name)
        if alias is not None:
            return alias
        if name in self._ALLOWED_ATTRS or name in self._ALLOWED_METHODS:
            return getattr(self._service, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        descriptor = getattr(type(self), name, None)
        if isinstance(descriptor, property):
            object.__setattr__(self, name, value)
            return
        if name in self._MUTABLE_ATTRS:
            setattr(self._service, name, value)
            return
        raise AttributeError(name)


class _ControllerBoundPort(_BaseServicePort):
    """Base class for ports that also need controller override callbacks."""

    def __init__(self, service: Any) -> None:
        super().__init__(service)
        object.__setattr__(self, "_controller", None)

    def bind_controller(self, controller: Any) -> None:
        object.__setattr__(self, "_controller", controller)

    def _controller_or_override(self, name: str, controller_method: str) -> Any:
        override = self._service.__dict__.get(name)
        if override is not None:
            return override
        if self._controller is None:
            raise AttributeError(name)
        return getattr(self._controller, controller_method)


class WriteControllerPort(_BaseServicePort):
    """Expose only the write-path surface needed by ``DbusWriteController``."""

    _ALLOWED_ATTRS = {
        "virtual_mode",
        "virtual_autostart",
        "virtual_startstop",
        "virtual_enable",
        "virtual_set_current",
        "min_current",
        "max_current",
        "auto_start_condition_since",
        "auto_stop_condition_since",
        "manual_override_until",
    }
    _MUTABLE_ATTRS = _ALLOWED_ATTRS

    def __init__(self, service: Any) -> None:
        super().__init__(service)

    @property
    def auto_manual_override_seconds(self) -> Any:
        return self._service.auto_manual_override_seconds

    @property
    def auto_mode_cutover_pending(self) -> Any:
        return self._service._auto_mode_cutover_pending

    @auto_mode_cutover_pending.setter
    def auto_mode_cutover_pending(self, value: Any) -> None:
        self._service._auto_mode_cutover_pending = value

    @property
    def ignore_min_offtime_once(self) -> Any:
        return self._service._ignore_min_offtime_once

    @ignore_min_offtime_once.setter
    def ignore_min_offtime_once(self, value: Any) -> None:
        self._service._ignore_min_offtime_once = value

    def clear_auto_samples(self) -> Any:
        return self._service._clear_auto_samples()

    def queue_relay_command(self, relay_on: bool, current_time: float) -> Any:
        return self._service._queue_relay_command(relay_on, current_time)

    def publish_local_pm_status(self, relay_on: bool, current_time: float) -> Any:
        return self._service._publish_local_pm_status(relay_on, current_time)

    def get_worker_snapshot(self) -> Any:
        return self._service._get_worker_snapshot()

    def update_worker_snapshot(self, **kwargs: Any) -> Any:
        return self._service._update_worker_snapshot(**kwargs)

    def publish_dbus_path(self, path: str, value: Any, current_time: float, force: bool = False) -> Any:
        return self._service._publish_dbus_path(path, value, current_time, force=force)

    def time_now(self) -> float:
        return self._service._time_now()

    def normalize_mode(self, value: Any) -> int:
        return self._service._normalize_mode(value)

    def mode_uses_auto_logic(self, mode: Any) -> bool:
        return self._service._mode_uses_auto_logic(mode)

    def state_summary(self) -> str:
        return self._service._state_summary()

    def save_runtime_state(self) -> Any:
        return self._service._save_runtime_state()


class DbusInputPort(_ControllerBoundPort):
    """Expose the DBus-input surface needed by ``DbusInputController``."""

    _ALLOWED_ATTRS = {
        "auto_pv_service",
        "auto_pv_service_prefix",
        "_resolved_auto_pv_services",
        "_auto_pv_last_scan",
        "auto_pv_scan_interval_seconds",
        "auto_pv_max_services",
        "auto_pv_path",
        "auto_use_dc_pv",
        "auto_dc_pv_service",
        "auto_dc_pv_path",
        "_last_pv_missing_warning",
        "auto_battery_service",
        "auto_battery_service_prefix",
        "auto_battery_soc_path",
        "_resolved_auto_battery_service",
        "_auto_battery_last_scan",
        "auto_battery_scan_interval_seconds",
        "auto_grid_l1_path",
        "auto_grid_l2_path",
        "auto_grid_l3_path",
        "auto_grid_require_all_phases",
        "auto_grid_service",
        "_dbus_list_backoff_until",
        "_dbus_list_failures",
        "auto_dbus_backoff_base_seconds",
        "auto_dbus_backoff_max_seconds",
        "dbus_method_timeout_seconds",
        "_last_dbus_ok_at",
    }

    _MUTABLE_ATTRS = {
        "_resolved_auto_pv_services",
        "_auto_pv_last_scan",
        "_last_pv_missing_warning",
        "_resolved_auto_battery_service",
        "_auto_battery_last_scan",
        "_dbus_list_backoff_until",
        "_dbus_list_failures",
        "auto_dbus_backoff_base_seconds",
        "auto_dbus_backoff_max_seconds",
        "dbus_method_timeout_seconds",
        "_last_dbus_ok_at",
    }

    def source_retry_ready(self, source_key: str, now: float) -> Any:
        return self._service._source_retry_ready(source_key, now)

    def mark_recovery(self, source_key: str, message: str, *args: Any) -> Any:
        return self._service._mark_recovery(source_key, message, *args)

    def mark_failure(self, source_key: str) -> Any:
        return self._service._mark_failure(source_key)

    def delay_source_retry(self, source_key: str, now: float) -> Any:
        return self._service._delay_source_retry(source_key, now)

    def warning_throttled(
        self,
        warning_key: str,
        interval_seconds: float,
        warning_message: str,
        *args: Any,
    ) -> Any:
        return self._service._warning_throttled(warning_key, interval_seconds, warning_message, *args)

    def get_system_bus(self) -> Any:
        return self._service._get_system_bus()

    def reset_system_bus(self) -> Any:
        return self._service._reset_system_bus()

    def get_dbus_value(self, *args: Any, **kwargs: Any) -> Any:
        return self._controller_or_override("_get_dbus_value", "get_dbus_value")(*args, **kwargs)

    def list_dbus_services(self, *args: Any, **kwargs: Any) -> Any:
        return self._controller_or_override("_list_dbus_services", "list_dbus_services")(*args, **kwargs)

    def invalidate_auto_pv_services(self, *args: Any, **kwargs: Any) -> Any:
        return self._controller_or_override("_invalidate_auto_pv_services", "invalidate_auto_pv_services")(
            *args,
            **kwargs,
        )

    def resolve_auto_pv_services(self, *args: Any, **kwargs: Any) -> Any:
        return self._controller_or_override("_resolve_auto_pv_services", "resolve_auto_pv_services")(*args, **kwargs)

    def invalidate_auto_battery_service(self, *args: Any, **kwargs: Any) -> Any:
        return self._controller_or_override(
            "_invalidate_auto_battery_service",
            "invalidate_auto_battery_service",
        )(*args, **kwargs)

    def resolve_auto_battery_service(self, *args: Any, **kwargs: Any) -> Any:
        return self._controller_or_override(
            "_resolve_auto_battery_service",
            "resolve_auto_battery_service",
        )(*args, **kwargs)


class UpdateCyclePort(_BaseServicePort):
    """Expose the update-cycle surface needed by ``UpdateCycleController``."""

    _ALLOWED_ATTRS = {
        "_startup_manual_target",
        "virtual_mode",
        "auto_shelly_soft_fail_seconds",
        "_last_health_reason",
        "_last_health_code",
        "charging_started_at",
        "energy_at_start",
        "virtual_startstop",
        "virtual_enable",
        "phase",
        "voltage_mode",
        "last_status",
        "_last_pm_status",
        "_last_pm_status_at",
        "_last_voltage",
        "auto_input_cache_seconds",
        "_auto_cached_inputs_used",
        "_error_state",
        "_last_pv_value",
        "_last_pv_at",
        "_last_grid_value",
        "_last_grid_at",
        "_last_battery_soc_value",
        "_last_battery_soc_at",
        "auto_audit_log",
        "_last_auto_metrics",
        "charging_threshold_watts",
        "idle_status",
        "_last_successful_update_at",
        "_last_recovery_attempt_at",
        "last_update",
        "service_name",
        "_dbusservice",
    }

    _ALLOWED_METHODS = {
        "_mode_uses_auto_logic",
        "_queue_relay_command",
        "_mark_failure",
        "_warning_throttled",
        "_ensure_observability_state",
        "_publish_energy_time_measurements",
        "_publish_config_paths",
        "_publish_diagnostic_paths",
        "_time_now",
        "_save_runtime_state",
        "_watchdog_recover",
        "_ensure_auto_input_helper_process",
        "_refresh_auto_input_snapshot",
        "_get_worker_snapshot",
        "_set_health",
        "_publish_live_measurements",
        "_bump_update_index",
        "_safe_float",
        "_publish_local_pm_status",
        "_auto_decide_relay",
        "_state_summary",
    }

    _MUTABLE_ATTRS = {
        "_startup_manual_target",
        "_last_health_reason",
        "_last_health_code",
        "charging_started_at",
        "energy_at_start",
        "virtual_startstop",
        "last_status",
        "_last_pm_status",
        "_last_pm_status_at",
        "_last_voltage",
        "_auto_cached_inputs_used",
        "_last_pv_value",
        "_last_pv_at",
        "_last_grid_value",
        "_last_grid_at",
        "_last_battery_soc_value",
        "_last_battery_soc_at",
        "_last_successful_update_at",
        "_last_recovery_attempt_at",
        "last_update",
    }

    def __init__(self, service: Any) -> None:
        super().__init__(service)


class AutoDecisionPort(_ControllerBoundPort):
    """Expose the Auto-decision surface needed by ``AutoDecisionController``."""

    _ALLOWED_ATTRS = {
        "auto_samples",
        "auto_average_window_seconds",
        "relay_last_changed_at",
        "relay_last_off_at",
        "auto_start_condition_since",
        "auto_stop_condition_since",
        "auto_stop_condition_reason",
        "_last_health_reason",
        "_last_health_code",
        "auto_min_runtime_seconds",
        "auto_min_offtime_seconds",
        "_last_grid_at",
        "_grid_recovery_required",
        "_grid_recovery_since",
        "auto_grid_missing_stop_seconds",
        "auto_grid_recovery_start_seconds",
        "virtual_mode",
        "_auto_mode_cutover_pending",
        "_ignore_min_offtime_once",
        "_last_battery_allow_warning",
        "auto_allow_without_battery_soc",
        "auto_battery_scan_interval_seconds",
        "auto_resume_soc",
        "auto_min_soc",
        "auto_high_soc_threshold",
        "auto_high_soc_release_threshold",
        "auto_high_soc_start_surplus_watts",
        "auto_high_soc_stop_surplus_watts",
        "auto_stop_delay_seconds",
        "auto_stop_grid_import_watts",
        "auto_stop_surplus_delay_seconds",
        "auto_stop_ewma_alpha",
        "auto_stop_ewma_alpha_stable",
        "auto_stop_ewma_alpha_volatile",
        "auto_stop_surplus_volatility_low_watts",
        "auto_stop_surplus_volatility_high_watts",
        "auto_policy",
        "auto_night_lock_stop",
        "_last_auto_metrics",
        "_auto_high_soc_profile_active",
        "_stop_smoothed_surplus_power",
        "_stop_smoothed_grid_power",
        "started_at",
        "auto_startup_warmup_seconds",
        "manual_override_until",
        "virtual_autostart",
        "auto_start_delay_seconds",
        "auto_start_max_grid_import_watts",
        "auto_start_surplus_watts",
        "auto_stop_surplus_watts",
        "_auto_cached_inputs_used",
        "virtual_enable",
        "virtual_startstop",
        "auto_daytime_only",
        "auto_month_windows",
        "auto_audit_log",
    }

    _MUTABLE_ATTRS = _ALLOWED_ATTRS

    def clear_auto_samples(self, *args: Any, **kwargs: Any) -> Any:
        return self._controller_or_override("_clear_auto_samples", "clear_auto_samples")(*args, **kwargs)

    def set_health(self, *args: Any, **kwargs: Any) -> Any:
        return self._controller_or_override("_set_health", "set_health")(*args, **kwargs)

    def save_runtime_state(self, *args: Any, **kwargs: Any) -> Any:
        return self._service._save_runtime_state(*args, **kwargs)

    def write_auto_audit_event(self, *args: Any, **kwargs: Any) -> Any:
        return self._service._write_auto_audit_event(*args, **kwargs)

    def peek_pending_relay_command(self, *args: Any, **kwargs: Any) -> Any:
        return self._service._peek_pending_relay_command(*args, **kwargs)

    def is_within_auto_daytime_window(self, *args: Any, **kwargs: Any) -> Any:
        return self._controller_or_override(
            "_is_within_auto_daytime_window",
            "is_within_auto_daytime_window",
        )(*args, **kwargs)

    def get_available_surplus_watts(self, *args: Any, **kwargs: Any) -> Any:
        return self._controller_or_override(
            "_get_available_surplus_watts",
            "get_available_surplus_watts",
        )(*args, **kwargs)

    def add_auto_sample(self, *args: Any, **kwargs: Any) -> Any:
        return self._controller_or_override("_add_auto_sample", "add_auto_sample")(*args, **kwargs)

    def average_auto_metric(self, *args: Any, **kwargs: Any) -> Any:
        return self._controller_or_override("_average_auto_metric", "average_auto_metric")(*args, **kwargs)
