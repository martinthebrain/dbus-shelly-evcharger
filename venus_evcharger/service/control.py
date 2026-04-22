# SPDX-License-Identifier: GPL-3.0-or-later
"""Control API mixin for the Venus EV charger service."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, cast

from venus_evcharger.control import (
    ControlApiAuditTrail,
    ControlApiIdempotencyStore,
    ControlApiRateLimiter,
    CONTROL_API_COMMAND_SCOPE_REQUIREMENTS,
    ControlCommand,
    LocalControlApiHttpServer,
)
from venus_evcharger.control.events import ControlApiEventBus
from venus_evcharger.core.common import evse_fault_reason
from venus_evcharger.energy import summarize_energy_learning_profiles
from venus_evcharger.core.contracts import (
    CONTROL_API_ENDPOINTS,
    CONTROL_API_EXPERIMENTAL_ENDPOINTS,
    CONTROL_API_STATE_ENDPOINTS,
    CONTROL_API_STABLE_ENDPOINTS,
    CONTROL_COMMAND_NAMES,
    CONTROL_COMMAND_SOURCES,
    normalized_control_api_capabilities_fields,
    normalized_fault_state,
    normalized_software_update_state_fields,
    normalized_state_api_config_effective_fields,
    normalized_state_api_dbus_diagnostics_fields,
    normalized_state_api_health_fields,
    normalized_state_api_kind,
    normalized_state_api_operational_fields,
    normalized_state_api_runtime_fields,
    normalized_state_api_summary_fields,
    normalized_state_api_topology_fields,
    normalized_state_api_update_fields,
)
from .factory import ServiceControllerFactoryMixin


class ControlApiMixin(ServiceControllerFactoryMixin):
    """Expose canonical command building and optional local HTTP control transport."""

    def _control_command_from_payload(self, payload: dict[str, Any], source: str = "http") -> ControlCommand:
        self._ensure_write_controller()
        return cast(
            ControlCommand,
            self._write_controller.build_control_command_from_payload(payload, source=source),
        )

    def _state_api_summary_payload(self) -> dict[str, Any]:
        summary = getattr(self, "_state_summary")()
        return normalized_state_api_summary_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "summary",
                "summary": summary,
            }
        )

    def _state_api_runtime_payload(self) -> dict[str, Any]:
        runtime_state = getattr(self, "_current_runtime_state")()
        return normalized_state_api_runtime_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "runtime",
                "state": runtime_state,
            }
        )

    def _state_api_operational_payload(self) -> dict[str, Any]:
        auto_state = getattr(self, "_last_auto_state", "idle")
        auto_state_code = getattr(self, "_last_auto_state_code", 0)
        get_worker_snapshot = getattr(self, "_get_worker_snapshot", None)
        raw_worker_snapshot = get_worker_snapshot() if callable(get_worker_snapshot) else {}
        worker_snapshot = cast(dict[str, Any], raw_worker_snapshot if isinstance(raw_worker_snapshot, dict) else {})
        learning_profiles = worker_snapshot.get("battery_learning_profiles")
        learning_summary = summarize_energy_learning_profiles(learning_profiles if isinstance(learning_profiles, dict) else {})
        fault_reason, fault_active = normalized_fault_state(evse_fault_reason(getattr(self, "_last_health_reason", "")))
        software_update_state, software_update_state_code, software_update_available, software_update_no_update_active = (
            normalized_software_update_state_fields(
                getattr(self, "_software_update_state", "idle"),
                getattr(self, "_software_update_available", False),
                getattr(self, "_software_update_no_update_active", False),
            )
        )
        return normalized_state_api_operational_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "operational",
                "state": {
                    "mode": getattr(self, "virtual_mode", 0),
                    "enable": getattr(self, "virtual_enable", 0),
                    "startstop": getattr(self, "virtual_startstop", 0),
                    "autostart": getattr(self, "virtual_autostart", 0),
                    "active_phase_selection": getattr(self, "active_phase_selection", "P1"),
                    "requested_phase_selection": getattr(self, "requested_phase_selection", "P1"),
                    "backend_mode": getattr(self, "backend_mode", "combined"),
                    "meter_backend": getattr(self, "meter_backend_type", "na"),
                    "switch_backend": getattr(self, "switch_backend_type", "na"),
                    "charger_backend": getattr(self, "charger_backend_type", "na"),
                    "auto_state": auto_state,
                    "auto_state_code": auto_state_code,
                    "fault_active": fault_active,
                    "fault_reason": fault_reason,
                    "software_update_state": software_update_state,
                    "software_update_state_code": software_update_state_code,
                    "software_update_available": software_update_available,
                    "software_update_no_update_active": software_update_no_update_active,
                    "runtime_overrides_active": getattr(self, "_runtime_overrides_active", False),
                    "runtime_overrides_path": getattr(self, "runtime_overrides_path", ""),
                    "combined_battery_soc": worker_snapshot.get("battery_combined_soc"),
                    "combined_battery_source_count": worker_snapshot.get("battery_source_count", 0),
                    "combined_battery_online_source_count": worker_snapshot.get("battery_online_source_count", 0),
                    "combined_battery_charge_power_w": worker_snapshot.get("battery_combined_charge_power_w"),
                    "combined_battery_discharge_power_w": worker_snapshot.get("battery_combined_discharge_power_w"),
                    "combined_battery_net_power_w": worker_snapshot.get("battery_combined_net_power_w"),
                    "combined_battery_ac_power_w": worker_snapshot.get("battery_combined_ac_power_w"),
                    "combined_battery_pv_input_power_w": worker_snapshot.get("battery_combined_pv_input_power_w"),
                    "combined_battery_grid_interaction_w": worker_snapshot.get("battery_combined_grid_interaction_w"),
                    "combined_battery_headroom_charge_w": worker_snapshot.get("battery_headroom_charge_w"),
                    "combined_battery_headroom_discharge_w": worker_snapshot.get("battery_headroom_discharge_w"),
                    "expected_near_term_export_w": worker_snapshot.get("expected_near_term_export_w"),
                    "expected_near_term_import_w": worker_snapshot.get("expected_near_term_import_w"),
                    "combined_battery_average_confidence": worker_snapshot.get("battery_average_confidence"),
                    "combined_battery_battery_source_count": worker_snapshot.get("battery_battery_source_count", 0),
                    "combined_battery_hybrid_inverter_source_count": worker_snapshot.get(
                        "battery_hybrid_inverter_source_count",
                        0,
                    ),
                    "combined_battery_inverter_source_count": worker_snapshot.get("battery_inverter_source_count", 0),
                    "combined_battery_learning_profile_count": learning_summary.get("profile_count", 0),
                    "combined_battery_observed_max_charge_power_w": learning_summary.get(
                        "observed_max_charge_power_w"
                    ),
                    "combined_battery_observed_max_discharge_power_w": learning_summary.get(
                        "observed_max_discharge_power_w"
                    ),
                    "combined_battery_observed_max_ac_power_w": learning_summary.get("observed_max_ac_power_w"),
                    "combined_battery_observed_max_pv_input_power_w": learning_summary.get(
                        "observed_max_pv_input_power_w"
                    ),
                    "combined_battery_observed_max_grid_import_w": learning_summary.get(
                        "observed_max_grid_import_w"
                    ),
                    "combined_battery_observed_max_grid_export_w": learning_summary.get(
                        "observed_max_grid_export_w"
                    ),
                    "combined_battery_average_active_charge_power_w": learning_summary.get(
                        "average_active_charge_power_w"
                    ),
                    "combined_battery_average_active_discharge_power_w": learning_summary.get(
                        "average_active_discharge_power_w"
                    ),
                    "combined_battery_average_active_power_delta_w": learning_summary.get(
                        "average_active_power_delta_w"
                    ),
                    "combined_battery_power_smoothing_ratio": learning_summary.get("power_smoothing_ratio"),
                    "combined_battery_typical_response_delay_seconds": learning_summary.get(
                        "typical_response_delay_seconds"
                    ),
                    "combined_battery_support_bias": learning_summary.get("support_bias"),
                    "combined_battery_day_support_bias": learning_summary.get("day_support_bias"),
                    "combined_battery_night_support_bias": learning_summary.get("night_support_bias"),
                    "combined_battery_import_support_bias": learning_summary.get("import_support_bias"),
                    "combined_battery_export_bias": learning_summary.get("export_bias"),
                    "combined_battery_battery_first_export_bias": learning_summary.get(
                        "battery_first_export_bias"
                    ),
                    "combined_battery_reserve_band_floor_soc": learning_summary.get("reserve_band_floor_soc"),
                    "combined_battery_reserve_band_ceiling_soc": learning_summary.get("reserve_band_ceiling_soc"),
                    "combined_battery_reserve_band_width_soc": learning_summary.get("reserve_band_width_soc"),
                    "combined_battery_direction_change_count": learning_summary.get("direction_change_count", 0),
                },
            }
        )

    def _state_api_dbus_diagnostics_payload(self) -> dict[str, Any]:
        self._ensure_dbus_publisher()
        now_func = getattr(self, "_time_now", None)
        raw_now = now_func() if callable(now_func) else time.time()
        now = float(raw_now) if isinstance(raw_now, (int, float)) else time.time()
        counters = cast(dict[str, Any], self._dbus_publisher._diagnostic_counter_values(now))
        ages = cast(dict[str, Any], self._dbus_publisher._diagnostic_age_values(now))
        return normalized_state_api_dbus_diagnostics_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "dbus-diagnostics",
                "state": {
                    **counters,
                    **ages,
                },
            }
        )

    def _state_api_topology_payload(self) -> dict[str, Any]:
        supported = tuple(getattr(self, "supported_phase_selections", ("P1",)) or ("P1",))
        return normalized_state_api_topology_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "topology",
                "state": {
                    "backend_mode": getattr(self, "backend_mode", "combined"),
                    "meter_backend": getattr(self, "meter_backend_type", "na"),
                    "switch_backend": getattr(self, "switch_backend_type", "na"),
                    "charger_backend": getattr(self, "charger_backend_type", "na"),
                    "active_phase_selection": getattr(self, "active_phase_selection", "P1"),
                    "requested_phase_selection": getattr(self, "requested_phase_selection", "P1"),
                    "supported_phase_selections": list(supported),
                    "available_modes": [0, 1, 2],
                    "service_name": getattr(self, "service_name", ""),
                    "connection_name": getattr(self, "connection_name", ""),
                },
            }
        )

    def _state_api_update_payload(self) -> dict[str, Any]:
        return normalized_state_api_update_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "update",
                "state": {
                    "current_version": getattr(self, "_software_update_current_version", ""),
                    "available_version": getattr(self, "_software_update_available_version", ""),
                    "available": getattr(self, "_software_update_available", False),
                    "state": getattr(self, "_software_update_state", "idle"),
                    "detail": getattr(self, "_software_update_detail", ""),
                    "last_check_at": getattr(self, "_software_update_last_check_at", None),
                    "last_run_at": getattr(self, "_software_update_last_run_at", None),
                    "last_result": getattr(self, "_software_update_last_result", ""),
                    "run_requested_at": getattr(self, "_software_update_run_requested_at", None),
                    "next_check_at": getattr(self, "_software_update_next_check_at", None),
                    "boot_auto_due_at": getattr(self, "_software_update_boot_auto_due_at", None),
                    "no_update_active": getattr(self, "_software_update_no_update_active", False),
                },
            }
        )

    def _state_api_config_effective_payload(self) -> dict[str, Any]:
        return normalized_state_api_config_effective_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "config-effective",
                "state": {
                    "deviceinstance": getattr(self, "deviceinstance", 0),
                    "host": getattr(self, "host", ""),
                    "phase": getattr(self, "phase", "L1"),
                    "service_name": getattr(self, "service_name", ""),
                    "connection_name": getattr(self, "connection_name", ""),
                    "runtime_state_path": getattr(self, "runtime_state_path", ""),
                    "runtime_overrides_path": getattr(self, "runtime_overrides_path", ""),
                    "control_api_enabled": bool(getattr(self, "control_api_enabled", False)),
                    "control_api_host": getattr(self, "control_api_host", "127.0.0.1"),
                    "control_api_port": int(getattr(self, "control_api_port", 0)),
                    "control_api_localhost_only": bool(getattr(self, "control_api_localhost_only", True)),
                    "control_api_unix_socket_path": getattr(self, "control_api_unix_socket_path", ""),
                    "control_api_audit_path": getattr(self, "control_api_audit_path", ""),
                    "control_api_idempotency_path": getattr(self, "control_api_idempotency_path", ""),
                    "control_api_rate_limit_max_requests": int(getattr(self, "control_api_rate_limit_max_requests", 0)),
                    "control_api_rate_limit_window_seconds": float(
                        getattr(self, "control_api_rate_limit_window_seconds", 0.0)
                    ),
                    "control_api_critical_cooldown_seconds": float(
                        getattr(self, "control_api_critical_cooldown_seconds", 0.0)
                    ),
                    "control_api_read_token_configured": bool(str(getattr(self, "control_api_read_token", "")).strip()),
                    "control_api_control_token_configured": bool(
                        str(getattr(self, "control_api_control_token", "")).strip()
                    ),
                    "control_api_admin_token_configured": bool(str(getattr(self, "control_api_admin_token", "")).strip()),
                    "control_api_update_token_configured": bool(str(getattr(self, "control_api_update_token", "")).strip()),
                    "companion_dbus_bridge_enabled": bool(getattr(self, "companion_dbus_bridge_enabled", False)),
                    "companion_battery_service_enabled": bool(
                        getattr(self, "companion_battery_service_enabled", False)
                    ),
                    "companion_pvinverter_service_enabled": bool(
                        getattr(self, "companion_pvinverter_service_enabled", False)
                    ),
                    "companion_source_services_enabled": bool(
                        getattr(self, "companion_source_services_enabled", False)
                    ),
                    "companion_battery_deviceinstance": int(getattr(self, "companion_battery_deviceinstance", 0)),
                    "companion_pvinverter_deviceinstance": int(
                        getattr(self, "companion_pvinverter_deviceinstance", 0)
                    ),
                    "companion_source_battery_deviceinstance_base": int(
                        getattr(self, "companion_source_battery_deviceinstance_base", 0)
                    ),
                    "companion_source_pvinverter_deviceinstance_base": int(
                        getattr(self, "companion_source_pvinverter_deviceinstance_base", 0)
                    ),
                    "companion_battery_service_name": getattr(self, "companion_battery_service_name", ""),
                    "companion_pvinverter_service_name": getattr(self, "companion_pvinverter_service_name", ""),
                    "companion_source_battery_service_prefix": getattr(
                        self,
                        "companion_source_battery_service_prefix",
                        "",
                    ),
                    "companion_source_pvinverter_service_prefix": getattr(
                        self,
                        "companion_source_pvinverter_service_prefix",
                        "",
                    ),
                    "backend_mode": getattr(self, "backend_mode", "combined"),
                    "meter_backend": getattr(self, "meter_backend_type", "na"),
                    "switch_backend": getattr(self, "switch_backend_type", "na"),
                    "charger_backend": getattr(self, "charger_backend_type", "na"),
                    "max_current": getattr(self, "max_current", 0.0),
                    "min_current": getattr(self, "min_current", 0.0),
                    "auto_daytime_only": bool(getattr(self, "auto_daytime_only", False)),
                    "auto_use_combined_battery_soc": bool(getattr(self, "auto_use_combined_battery_soc", True)),
                    "auto_energy_source_ids": list(getattr(self, "auto_energy_source_ids", ())),
                    "auto_energy_source_count": len(tuple(getattr(self, "auto_energy_source_ids", ()))),
                    "auto_scheduled_enabled_days": getattr(self, "auto_scheduled_enabled_days", ""),
                    "auto_scheduled_latest_end_time": getattr(self, "auto_scheduled_latest_end_time", ""),
                    "auto_scheduled_night_current_amps": getattr(self, "auto_scheduled_night_current_amps", 0.0),
                },
            }
        )

    def _state_api_healthz_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": "v1",
            "kind": normalized_state_api_kind("healthz", default="healthz"),
            "state": {
                "alive": True,
                "control_api_enabled": bool(getattr(self, "control_api_enabled", False)),
                "control_api_running": bool(getattr(self, "_control_api_server", None)),
            },
        }

    def _state_api_version_payload(self) -> dict[str, Any]:
        current_version = str(getattr(self, "_software_update_current_version", "")).strip()
        return {
            "ok": True,
            "api_version": "v1",
            "kind": normalized_state_api_kind("version", default="version"),
            "state": {
                "service_version": current_version or str(getattr(self, "firmware_version", "")).strip(),
                "api_version": "v1",
                "product_name": str(getattr(self, "product_name", "")).strip(),
                "service_name": str(getattr(self, "service_name", "")).strip(),
            },
        }

    def _state_api_build_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": "v1",
            "kind": normalized_state_api_kind("build", default="build"),
            "state": {
                "product_name": str(getattr(self, "product_name", "")).strip(),
                "hardware_version": str(getattr(self, "hardware_version", "")).strip(),
                "firmware_version": str(getattr(self, "firmware_version", "")).strip(),
                "connection_name": str(getattr(self, "connection_name", "")).strip(),
                "runtime_state_path": str(getattr(self, "runtime_state_path", "")).strip(),
            },
        }

    def _state_api_contracts_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "api_version": "v1",
            "kind": normalized_state_api_kind("contracts", default="contracts"),
            "state": {
                "active_api_version": "v1",
                "openapi_endpoint": "/v1/openapi.json",
                "capabilities_endpoint": "/v1/capabilities",
                "versioning_document": "API_VERSIONING.md",
                "control_document": "CONTROL_API.md",
                "state_document": "STATE_API.md",
                "stable_endpoints": sorted(CONTROL_API_STABLE_ENDPOINTS),
                "experimental_endpoints": sorted(CONTROL_API_EXPERIMENTAL_ENDPOINTS),
            },
        }

    def _state_api_health_payload(self) -> dict[str, Any]:
        now = time.time()
        stale = False
        is_update_stale = getattr(self, "_is_update_stale", None)
        if callable(is_update_stale):
            stale = bool(is_update_stale(now))
        fault_reason, fault_active = normalized_fault_state(evse_fault_reason(getattr(self, "_last_health_reason", "")))
        return normalized_state_api_health_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "health",
                "state": {
                    "health_reason": getattr(self, "_last_health_reason", "init"),
                    "health_code": getattr(self, "_last_health_code", 0),
                    "fault_active": fault_active,
                    "fault_reason": fault_reason,
                    "runtime_overrides_active": getattr(self, "_runtime_overrides_active", False),
                    "control_api_enabled": getattr(self, "control_api_enabled", False),
                    "control_api_running": bool(getattr(self, "_control_api_server", None)),
                    "control_api_transport": "http",
                    "listen_host": getattr(self, "control_api_listen_host", ""),
                    "listen_port": getattr(self, "control_api_listen_port", 0),
                    "unix_socket_path": getattr(self, "control_api_bound_unix_socket_path", ""),
                    "control_api_localhost_only": getattr(self, "control_api_localhost_only", True),
                    "command_audit_entries": self._control_api_audit_trail().count(),
                    "command_audit_path": getattr(self, "control_api_audit_path", ""),
                    "idempotency_entries": self._control_api_idempotency_store().count(),
                    "idempotency_path": getattr(self, "control_api_idempotency_path", ""),
                    "update_stale": stale,
                    "last_successful_update_at": getattr(self, "_last_successful_update_at", None),
                    "last_recovery_attempt_at": getattr(self, "_last_recovery_attempt_at", None),
                },
            }
        )

    def _state_api_event_snapshot_payload(self) -> dict[str, Any]:
        return {
            "summary": self._state_api_summary_payload(),
            "operational": self._state_api_operational_payload(),
            "health": self._state_api_health_payload(),
            "update": self._state_api_update_payload(),
            "topology": self._state_api_topology_payload(),
        }

    def _control_api_capabilities_payload(self) -> dict[str, Any]:
        supported_phase_selections = tuple(getattr(self, "supported_phase_selections", ("P1",)) or ("P1",))
        topology = {
            "backend_mode": getattr(self, "backend_mode", "combined"),
            "meter_backend": getattr(self, "meter_backend_type", "na"),
            "switch_backend": getattr(self, "switch_backend_type", "na"),
            "charger_backend": getattr(self, "charger_backend_type", "na"),
        }
        features = {
            "command_audit_trail": True,
            "dbus_diagnostics_state": True,
            "event_stream": True,
            "event_kind_filters": True,
            "event_retry_hints": True,
            "http_control_command": True,
            "idempotency_tracking": True,
            "optimistic_concurrency": True,
            "per_command_request_schemas": True,
            "rate_limiting": True,
            "runtime_only_idempotency_persistence": True,
            "multi_phase_selection": len(supported_phase_selections) > 1,
            "phase_selection_write": bool(supported_phase_selections),
            "read_api": True,
            "runtime_override_write": True,
            "software_update_trigger": True,
            "state_reads": True,
        }
        read_token = str(getattr(self, "control_api_read_token", "")).strip()
        control_token = str(getattr(self, "control_api_control_token", "")).strip()
        legacy_token = str(getattr(self, "control_api_auth_token", "")).strip()
        effective_control_token = control_token or legacy_token
        effective_read_token = read_token or effective_control_token
        return normalized_control_api_capabilities_fields(
            {
                "ok": True,
                "api_version": "v1",
                "transport": "http",
                "auth_required": bool(effective_read_token or effective_control_token),
                "read_auth_required": bool(effective_read_token),
                "control_auth_required": bool(effective_control_token),
                "localhost_only": bool(getattr(self, "control_api_localhost_only", True)),
                "unix_socket_path": getattr(self, "control_api_bound_unix_socket_path", ""),
                "auth_header": "Authorization: Bearer <token>",
                "auth_scopes": ["read", "control_basic", "control_admin", "update_admin"],
                "command_names": sorted(CONTROL_COMMAND_NAMES),
                "command_scope_requirements": dict(CONTROL_API_COMMAND_SCOPE_REQUIREMENTS),
                "command_sources": sorted(CONTROL_COMMAND_SOURCES),
                "state_endpoints": sorted(CONTROL_API_STATE_ENDPOINTS),
                "endpoints": sorted(CONTROL_API_ENDPOINTS),
                "available_modes": [0, 1, 2],
                "supported_phase_selections": list(supported_phase_selections),
                "features": features,
                "topology": topology,
                "versioning": {
                    "stable_endpoints": sorted(CONTROL_API_STABLE_ENDPOINTS),
                    "experimental_endpoints": sorted(CONTROL_API_EXPERIMENTAL_ENDPOINTS),
                    "breaking_change_policy": (
                        "Stable v1 endpoints require a version bump for breaking changes; "
                        "experimental endpoints may evolve within v1."
                    ),
                },
            }
        )

    def _control_api_state_token_payload(self) -> dict[str, Any]:
        return self._state_api_event_snapshot_payload()

    def _control_api_state_token(self) -> str:
        encoded = json.dumps(
            self._control_api_state_token_payload(),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _control_api_audit_trail(self) -> ControlApiAuditTrail:
        audit_trail = getattr(self, "_control_api_audit_trail_instance", None)
        if audit_trail is None:
            audit_trail = ControlApiAuditTrail(
                history_limit=int(getattr(self, "control_api_audit_max_entries", 200)),
                path=str(getattr(self, "control_api_audit_path", "")).strip(),
            )
            self._control_api_audit_trail_instance = audit_trail
        return cast(ControlApiAuditTrail, audit_trail)

    def _record_control_api_command_audit(
        self,
        *,
        command: Any,
        result: Any,
        error: dict[str, Any] | None,
        replayed: bool,
        scope: str,
        client_host: str,
        status_code: int,
        transport: str = "http",
    ) -> dict[str, Any]:
        return self._control_api_audit_trail().append(
            {
                "timestamp": time.time(),
                "transport": transport,
                "scope": scope,
                "client_host": client_host,
                "status_code": status_code,
                "replayed": replayed,
                "command": self._audit_command_payload(command, transport),
                "result": self._audit_result_payload(result),
                "error": dict(error or {}),
            }
        )

    @staticmethod
    def _audit_command_payload(command: Any, transport: str) -> dict[str, Any]:
        if isinstance(command, dict):
            return dict(command)
        if command is None:
            return {}
        return {
            "name": getattr(command, "name", ""),
            "path": getattr(command, "path", ""),
            "value": getattr(command, "value", None),
            "source": getattr(command, "source", transport),
            "detail": getattr(command, "detail", ""),
            "command_id": getattr(command, "command_id", ""),
            "idempotency_key": getattr(command, "idempotency_key", ""),
        }

    @staticmethod
    def _audit_result_payload(result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            return dict(result)
        if result is None:
            return {}
        return {
            "status": getattr(result, "status", ""),
            "accepted": getattr(result, "accepted", False),
            "applied": getattr(result, "applied", False),
            "persisted": getattr(result, "persisted", False),
            "reversible_failure": getattr(result, "reversible_failure", False),
            "external_side_effect_started": getattr(result, "external_side_effect_started", False),
            "detail": getattr(result, "detail", ""),
        }

    def _control_api_idempotency_store(self) -> ControlApiIdempotencyStore:
        idempotency_store = getattr(self, "_control_api_idempotency_store_instance", None)
        if idempotency_store is None:
            idempotency_store = ControlApiIdempotencyStore(
                history_limit=int(getattr(self, "control_api_idempotency_max_entries", 200)),
                path=str(getattr(self, "control_api_idempotency_path", "")).strip(),
            )
            self._control_api_idempotency_store_instance = idempotency_store
        return cast(ControlApiIdempotencyStore, idempotency_store)

    def _control_api_rate_limiter(self) -> ControlApiRateLimiter:
        rate_limiter = getattr(self, "_control_api_rate_limiter_instance", None)
        if rate_limiter is None:
            rate_limiter = ControlApiRateLimiter(
                max_requests=int(getattr(self, "control_api_rate_limit_max_requests", 30)),
                window_seconds=float(getattr(self, "control_api_rate_limit_window_seconds", 5.0)),
                critical_cooldown_seconds=float(getattr(self, "control_api_critical_cooldown_seconds", 2.0)),
            )
            self._control_api_rate_limiter_instance = rate_limiter
        return cast(ControlApiRateLimiter, rate_limiter)

    def _control_api_event_bus(self) -> ControlApiEventBus:
        event_bus = getattr(self, "_control_api_event_bus_instance", None)
        if event_bus is None:
            event_bus = ControlApiEventBus()
            self._control_api_event_bus_instance = event_bus
        return cast(ControlApiEventBus, event_bus)

    def _publish_control_api_command_event(self, command: Any, result: Any, *, replayed: bool = False) -> None:
        self._control_api_event_bus().publish(
            "command",
            {
                "command": getattr(command, "__dict__", {}) or {
                    "name": command.name,
                    "path": command.path,
                    "value": command.value,
                    "source": command.source,
                    "detail": command.detail,
                    "command_id": getattr(command, "command_id", ""),
                    "idempotency_key": getattr(command, "idempotency_key", ""),
                },
                "result": getattr(result, "__dict__", {}) or {
                    "status": result.status,
                    "accepted": result.accepted,
                    "applied": result.applied,
                    "persisted": result.persisted,
                    "reversible_failure": result.reversible_failure,
                    "external_side_effect_started": result.external_side_effect_started,
                    "detail": result.detail,
                },
                "replayed": replayed,
            },
        )

    def _publish_control_api_state_event(self) -> None:
        self._control_api_event_bus().publish("snapshot", self._state_api_event_snapshot_payload())

    def _start_control_api_server(self) -> None:
        if not bool(getattr(self, "control_api_enabled", False)):
            return
        server = getattr(self, "_control_api_server", None)
        if server is None:
            server = LocalControlApiHttpServer(
                self,
                host=str(getattr(self, "control_api_host", "127.0.0.1")),
                port=int(getattr(self, "control_api_port", 0)),
                auth_token=str(getattr(self, "control_api_auth_token", "")),
                read_token=str(getattr(self, "control_api_read_token", "")),
                control_token=str(getattr(self, "control_api_control_token", "")),
                admin_token=str(getattr(self, "control_api_admin_token", "")),
                update_token=str(getattr(self, "control_api_update_token", "")),
                localhost_only=bool(getattr(self, "control_api_localhost_only", True)),
                unix_socket_path=str(getattr(self, "control_api_unix_socket_path", "")),
            )
            self._control_api_server = server
        server.start()
        self.control_api_listen_host = server.bound_host
        self.control_api_listen_port = server.bound_port
        self.control_api_bound_unix_socket_path = getattr(server, "bound_unix_socket_path", "")
        self._publish_control_api_state_event()

    def _stop_control_api_server(self) -> None:
        server = getattr(self, "_control_api_server", None)
        if server is None:
            return
        server.stop()
