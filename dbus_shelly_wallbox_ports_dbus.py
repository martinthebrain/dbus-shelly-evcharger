# SPDX-License-Identifier: GPL-3.0-or-later
"""Small controller ports that decouple controllers from the full service object."""

from __future__ import annotations

from typing import Any


from dbus_shelly_wallbox_ports_base import _ControllerBoundPort

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


