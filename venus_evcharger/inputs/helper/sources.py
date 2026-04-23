#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Collect PV, battery, and grid inputs for the Venus EV charger service in a helper process.

The helper exists so DBus discovery and polling cannot stall the main wallbox
service. It periodically writes a compact JSON snapshot that the main process
can consume safely, even if DBus becomes slow or temporarily inconsistent.
"""

import logging
import time
import xml.etree.ElementTree as xml_et
from typing import Any, cast

import dbus
from venus_evcharger.core.shared import (
    configured_grid_paths,
    coerce_dbus_numeric,
    discovery_cache_valid,
    first_matching_prefixed_service,
    grid_values_complete_enough,
    prefixed_service_names,
    should_assume_zero_pv,
    sum_dbus_numeric,
)
from venus_evcharger.energy import (
    EnergySourceDefinition,
    EnergySourceSnapshot,
    aggregate_energy_sources,
    derive_discharge_balance_metrics,
    derive_discharge_control_metrics,
    derive_energy_forecast,
    read_energy_source_snapshot,
    summarize_energy_learning_profiles,
    update_energy_learning_profiles,
)



class _AutoInputHelperSourceMixin:
    def _get_dbus_value(self: Any, service_name: str, path: str) -> float | int | None:
        """Read one DBus value with a small retry on reconnect."""
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                obj = self._get_system_bus().get_object(service_name, path)
                dbus_module = cast(Any, dbus)
                interface = dbus_module.Interface(obj, "com.victronenergy.BusItem")
                value = interface.GetValue(timeout=self.dbus_method_timeout_seconds)
                numeric_value = coerce_dbus_numeric(value)
                return cast(float | int | None, numeric_value)
            except Exception as error:  # pylint: disable=broad-except
                last_error = error
                self._reset_system_bus()
                if attempt == 0:
                    logging.debug("DBus read retry for %s %s after error: %s", service_name, path, error)
        assert last_error is not None
        raise last_error

    def _get_dbus_child_nodes(self: Any, service_name: str, path: str) -> list[str]:
        """Return child nodes below a DBus path via introspection."""
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                obj = self._get_system_bus().get_object(service_name, path)
                dbus_module = cast(Any, dbus)
                interface = dbus_module.Interface(obj, "org.freedesktop.DBus.Introspectable")
                xml_data = interface.Introspect(timeout=self.dbus_method_timeout_seconds)
                return cast(list[str], self._child_nodes_from_introspection(xml_data))
            except Exception as error:  # pylint: disable=broad-except
                last_error = error
                self._reset_system_bus()
                if attempt == 0:
                    logging.debug("DBus introspection retry for %s %s after error: %s", service_name, path, error)
        assert last_error is not None
        raise last_error

    @staticmethod
    def _child_nodes_from_introspection(xml_data: object) -> list[str]:
        """Return child node names parsed from one DBus introspection payload."""
        root = xml_et.fromstring(str(xml_data))
        child_nodes: list[str] = [str(name) for node in root.findall("node") if (name := node.attrib.get("name"))]
        return child_nodes

    def _list_dbus_services(self: Any) -> list[str]:
        """Return all DBus service names with a small backoff on failure."""
        now = time.time()
        if now < self._dbus_list_backoff_until:
            return []
        try:
            dbus_proxy = self._get_system_bus().get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
            dbus_module = cast(Any, dbus)
            dbus_iface = dbus_module.Interface(dbus_proxy, "org.freedesktop.DBus")
            names = list(dbus_iface.ListNames(timeout=self.dbus_method_timeout_seconds))
            self._dbus_list_failures = 0
            self._dbus_list_backoff_until = 0.0
            return names
        except Exception as error:  # pylint: disable=broad-except
            self._reset_system_bus()
            self._dbus_list_failures += 1
            delay = self.auto_dbus_backoff_base_seconds * (2 ** max(0, self._dbus_list_failures - 1))
            if self.auto_dbus_backoff_max_seconds > 0:
                delay = min(delay, self.auto_dbus_backoff_max_seconds)
            self._dbus_list_backoff_until = now + max(0.0, delay)
            self._warning_throttled(
                "auto-helper-list-dbus-failed",
                max(5.0, self.auto_dbus_backoff_base_seconds or 5.0),
                "Auto input helper could not list DBus services: %s",
                error,
            )
            return []

    def _source_retry_ready(self: Any, key: str) -> bool:
        """Return True when a source may be queried again."""
        return time.time() >= float(self._source_retry_after.get(key, 0.0))

    def _delay_source_retry(self: Any, key: str) -> None:
        """Delay retries briefly after a failing source read."""
        delay = max(1.0, self.auto_dbus_backoff_base_seconds or 5.0)
        self._source_retry_after[key] = time.time() + delay

    def _invalidate_auto_pv_services(self: Any) -> None:
        """Force the next PV lookup to re-scan DBus services."""
        setattr(self, "_resolved_auto_pv_services", [])
        self._auto_pv_last_scan = 0.0

    def _resolve_auto_pv_services(self: Any) -> list[str]:
        """Resolve AC PV services from config or DBus discovery."""
        if self.auto_pv_service:
            return [self.auto_pv_service]
        now = time.time()
        if discovery_cache_valid(
            self._resolved_auto_pv_services,
            self._auto_pv_last_scan,
            self.auto_pv_scan_interval_seconds,
            now,
        ):
            return list(self._resolved_auto_pv_services)
        resolved = prefixed_service_names(
            self._list_dbus_services(),
            self.auto_pv_service_prefix,
            max_services=self.auto_pv_max_services,
            sort_names=False,
        )
        self._resolved_auto_pv_services = resolved[: self.auto_pv_max_services]
        self._auto_pv_last_scan = now
        return list(self._resolved_auto_pv_services)

    def _resolved_pv_service_names(self: Any) -> tuple[list[str], bool]:
        """Return resolved AC PV services plus the discovery-empty hint."""
        try:
            service_names = self._resolve_auto_pv_services()
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Auto helper AC PV service resolution failed: %s", error)
            return [], False
        no_auto_ac_services_found = not self.auto_pv_service and not service_names
        return service_names, no_auto_ac_services_found

    def _read_ac_pv_total(self: Any, service_names: list[str]) -> tuple[float, bool]:
        """Return accumulated AC PV power and whether any numeric value was seen."""
        total = 0.0
        seen_value = False
        for service_name in service_names:
            try:
                value = self._get_dbus_value(service_name, self.auto_pv_path)
            except Exception as error:  # pylint: disable=broad-except
                logging.debug("Auto helper PV read failed for %s %s: %s", service_name, self.auto_pv_path, error)
                self._invalidate_auto_pv_services()
                continue
            numeric_value = sum_dbus_numeric(value)
            if numeric_value is None:
                continue
            total += numeric_value
            seen_value = True
        return total, seen_value

    def _read_dc_pv_power(self: Any) -> float | None:
        """Return numeric DC PV power when configured and readable."""
        if not self.auto_use_dc_pv:
            return None
        try:
            dc_value = self._get_dbus_value(self.auto_dc_pv_service, self.auto_dc_pv_path)
        except Exception as error:  # pylint: disable=broad-except
            logging.debug(
                "Auto helper DC PV read failed for %s %s: %s",
                self.auto_dc_pv_service,
                self.auto_dc_pv_path,
                error,
            )
            return None
        return sum_dbus_numeric(dc_value)

    def _get_pv_power(self: Any) -> float | None:
        """Read total PV power from auto-discovered AC PV plus optional DC PV."""
        if not self._source_retry_ready("pv"):
            return None
        service_names, no_auto_ac_services_found = self._resolved_pv_service_names()
        total, seen_value = self._read_ac_pv_total(service_names)
        numeric_dc_value = self._read_dc_pv_power()
        if numeric_dc_value is not None:
            total += numeric_dc_value
            seen_value = True

        if seen_value:
            total_power: float = total
            return total_power
        if should_assume_zero_pv(
            self.auto_pv_service,
            service_names,
            no_auto_ac_services_found,
            self.auto_use_dc_pv,
            numeric_dc_value,
        ):
            return 0.0
        self._delay_source_retry("pv")
        return None

    def _invalidate_auto_battery_service(self: Any) -> None:
        """Force the next battery lookup to re-scan DBus services."""
        self._resolved_auto_battery_service = None
        self._auto_battery_last_scan = 0.0
        if isinstance(getattr(self, "_resolved_auto_energy_services", None), dict):
            self._resolved_auto_energy_services.pop("primary_battery", None)
        if isinstance(getattr(self, "_auto_energy_last_scan", None), dict):
            self._auto_energy_last_scan.pop("primary_battery", None)

    def _primary_energy_source(self: Any) -> EnergySourceDefinition:
        sources = tuple(getattr(self, "auto_energy_sources", ()) or ())
        if sources:
            return cast(EnergySourceDefinition, sources[0])
        return EnergySourceDefinition(
            source_id="primary_battery",
            role="battery",
            service_name=str(getattr(self, "auto_battery_service", "") or ""),
            service_prefix=str(getattr(self, "auto_battery_service_prefix", "") or ""),
            soc_path=str(getattr(self, "auto_battery_soc_path", "/Soc") or "/Soc"),
            usable_capacity_wh=getattr(self, "auto_battery_capacity_wh", None),
            battery_power_path=str(getattr(self, "auto_battery_power_path", "") or ""),
            ac_power_path=str(getattr(self, "auto_battery_ac_power_path", "") or ""),
            pv_power_path=str(getattr(self, "auto_battery_pv_power_path", "") or ""),
            grid_interaction_path=str(getattr(self, "auto_battery_grid_interaction_path", "") or ""),
            operating_mode_path=str(getattr(self, "auto_battery_operating_mode_path", "") or ""),
        )

    def _battery_service_has_soc(self: Any, service_name: str) -> bool:
        """Return whether the candidate battery service currently exposes SOC."""
        try:
            return self._get_dbus_value(service_name, self.auto_battery_soc_path) is not None
        except Exception:
            return False

    def _energy_service_has_readable_field(self: Any, service_name: str, path: str) -> bool:
        if not path:
            return False
        try:
            return self._get_dbus_value(service_name, path) is not None
        except Exception:
            return False

    def _energy_source_has_readable_data(self: Any, source: EnergySourceDefinition, service_name: str) -> bool:
        return any(
            (
                self._energy_service_has_readable_field(service_name, source.soc_path),
                self._energy_service_has_readable_field(service_name, source.battery_power_path),
                self._energy_service_has_readable_field(service_name, source.ac_power_path),
                self._energy_service_has_readable_field(service_name, source.pv_power_path),
                self._energy_service_has_readable_field(service_name, source.grid_interaction_path),
                self._energy_service_has_readable_field(service_name, source.operating_mode_path),
            )
        )

    def _resolve_auto_battery_service(self: Any) -> str:
        """Resolve battery service from config or DBus discovery."""
        now = time.time()
        configured_service = self._configured_auto_battery_service(now)
        if configured_service is not None:
            resolved_configured_service: str = configured_service
            return resolved_configured_service
        cached_service = self._cached_auto_battery_service(now)
        if cached_service is not None:
            resolved_cached_service: str = cached_service
            return resolved_cached_service
        discovered_service = self._discovered_auto_battery_service(now)
        resolved_discovered_service: str = discovered_service
        return resolved_discovered_service

    def _configured_auto_battery_service(self: Any, now: float) -> str | None:
        """Return configured battery override when it currently exposes SOC."""
        source = self._primary_energy_source()
        if not source.service_name:
            return None
        try:
            if self._energy_source_has_readable_data(source, source.service_name):
                self._resolved_auto_battery_service = source.service_name
                self._auto_battery_last_scan = now
                if not isinstance(getattr(self, "_resolved_auto_energy_services", None), dict):
                    self._resolved_auto_energy_services = {}
                if not isinstance(getattr(self, "_auto_energy_last_scan", None), dict):
                    self._auto_energy_last_scan = {}
                self._resolved_auto_energy_services[source.source_id] = source.service_name
                self._auto_energy_last_scan[source.source_id] = now
                return str(self._resolved_auto_battery_service)
        except Exception:
            return None
        return None

    def _cached_auto_battery_service(self: Any, now: float) -> str | None:
        """Return cached battery service while the discovery cache remains valid."""
        if discovery_cache_valid(
            self._resolved_auto_battery_service,
            self._auto_battery_last_scan,
            self.auto_battery_scan_interval_seconds,
            now,
        ):
            return str(self._resolved_auto_battery_service)
        return None

    def _discovered_auto_battery_service(self: Any, now: float) -> str:
        """Return one auto-discovered battery service exposing SOC."""
        source = self._primary_energy_source()
        battery_service_prefix = str(getattr(self, "auto_battery_service_prefix", "") or "")
        service_name = first_matching_prefixed_service(
            self._list_dbus_services(),
            source.service_prefix or battery_service_prefix,
            self._battery_service_has_soc,
        )
        if service_name is not None:
            self._resolved_auto_battery_service = service_name
            self._auto_battery_last_scan = now
            if not isinstance(getattr(self, "_resolved_auto_energy_services", None), dict):
                self._resolved_auto_energy_services = {}
            if not isinstance(getattr(self, "_auto_energy_last_scan", None), dict):
                self._auto_energy_last_scan = {}
            self._resolved_auto_energy_services[source.source_id] = service_name
            self._auto_energy_last_scan[source.source_id] = now
            resolved_service: str = self._resolved_auto_battery_service
            return resolved_service
        raise ValueError(f"No DBus service found with prefix '{source.service_prefix or battery_service_prefix}'")

    def _cached_energy_service(self: Any, source_id: str, now: float) -> str | None:
        resolved = getattr(self, "_resolved_auto_energy_services", {})
        scans = getattr(self, "_auto_energy_last_scan", {})
        cached_service = resolved.get(source_id) if isinstance(resolved, dict) else None
        cached_at = scans.get(source_id, 0.0) if isinstance(scans, dict) else 0.0
        if discovery_cache_valid(
            cached_service,
            cached_at,
            self.auto_battery_scan_interval_seconds,
            now,
        ):
            return cast(str | None, cached_service)
        return None

    def _resolve_energy_source_service(self: Any, source: EnergySourceDefinition) -> str:
        now = time.time()
        if source.source_id == self._primary_energy_source().source_id:
            primary_service = self._resolve_auto_battery_service()
            resolved_primary_service: str = primary_service
            return resolved_primary_service
        if not isinstance(getattr(self, "_resolved_auto_energy_services", None), dict):
            self._resolved_auto_energy_services = {}
        if not isinstance(getattr(self, "_auto_energy_last_scan", None), dict):
            self._auto_energy_last_scan = {}
        if source.service_name and self._energy_source_has_readable_data(source, source.service_name):
            self._resolved_auto_energy_services[source.source_id] = source.service_name
            self._auto_energy_last_scan[source.source_id] = now
            return source.service_name
        cached_service = self._cached_energy_service(source.source_id, now)
        if cached_service is not None:
            resolved_cached_service: str = cached_service
            return resolved_cached_service
        if not source.service_prefix:
            raise ValueError(f"No readable DBus service configured for energy source '{source.source_id}'")
        service_name = first_matching_prefixed_service(
            self._list_dbus_services(),
            source.service_prefix,
            lambda candidate: self._energy_source_has_readable_data(source, candidate),
        )
        if service_name is None:
            raise ValueError(f"No DBus service found for energy source '{source.source_id}'")
        self._resolved_auto_energy_services[source.source_id] = service_name
        self._auto_energy_last_scan[source.source_id] = now
        return service_name

    def _read_optional_energy_value(self: Any, service_name: str, path: str) -> float | None:
        if not path:
            return None
        value = self._get_dbus_value(service_name, path)
        return cast(float | None, self._battery_soc_numeric(value))

    def _read_optional_energy_text(self: Any, service_name: str, path: str) -> str:
        if not path:
            return ""
        value = self._get_dbus_value(service_name, path)
        return "" if value is None else str(value).strip()

    def _dbus_energy_source_snapshot(self: Any, source: EnergySourceDefinition, now: float) -> EnergySourceSnapshot:
        service_name = self._resolve_energy_source_service(source)
        try:
            soc_value = self._read_optional_energy_value(service_name, source.soc_path)
            net_battery_power = self._read_optional_energy_value(service_name, source.battery_power_path)
            ac_power = self._read_optional_energy_value(service_name, source.ac_power_path)
            pv_input_power = self._read_optional_energy_value(service_name, source.pv_power_path)
            grid_interaction = self._read_optional_energy_value(service_name, source.grid_interaction_path)
            operating_mode = self._read_optional_energy_text(service_name, source.operating_mode_path)
        except Exception:
            if source.source_id != self._primary_energy_source().source_id:
                raise
            self._invalidate_auto_battery_service()
            service_name = self._resolve_energy_source_service(source)
            soc_value = self._read_optional_energy_value(service_name, source.soc_path)
            net_battery_power = self._read_optional_energy_value(service_name, source.battery_power_path)
            ac_power = self._read_optional_energy_value(service_name, source.ac_power_path)
            pv_input_power = self._read_optional_energy_value(service_name, source.pv_power_path)
            grid_interaction = self._read_optional_energy_value(service_name, source.grid_interaction_path)
            operating_mode = self._read_optional_energy_text(service_name, source.operating_mode_path)
        if soc_value is not None and not 0.0 <= soc_value <= 100.0:
            self._warning_throttled(
                "auto-helper-battery-soc-invalid",
                max(5.0, self.auto_battery_scan_interval_seconds or 5.0),
                "Auto input helper ignored out-of-range battery SOC %s from %s %s",
                soc_value,
                service_name,
                source.soc_path,
            )
            self._delay_source_retry("battery")
            soc_value = None
        return EnergySourceSnapshot(
            source_id=source.source_id,
            role=source.role,
            service_name=service_name,
            soc=soc_value,
            usable_capacity_wh=source.usable_capacity_wh,
            net_battery_power_w=net_battery_power,
            ac_power_w=ac_power,
            pv_input_power_w=pv_input_power,
            grid_interaction_w=grid_interaction,
            operating_mode=operating_mode,
            online=True,
            confidence=1.0,
            captured_at=now,
        )

    def _get_battery_snapshot(self: Any) -> dict[str, object]:
        """Read combined battery data from one or more energy sources."""
        if not self._source_retry_ready("battery"):
            return {"battery_soc": None}
        try:
            now = time.time()
            source_snapshots = tuple(
                read_energy_source_snapshot(self, cast(EnergySourceDefinition, source), now)
                for source in tuple(getattr(self, "auto_energy_sources", ()) or (self._primary_energy_source(),))
            )
            cluster = aggregate_energy_sources(source_snapshots)
            primary_soc = cluster.sources[0].soc if cluster.sources else None
            effective_soc = cluster.effective_soc if bool(getattr(self, "auto_use_combined_battery_soc", True)) else primary_soc
            self._energy_learning_profiles = update_energy_learning_profiles(
                getattr(self, "_energy_learning_profiles", {}),
                cluster.sources,
                now,
            )
            learning_summary = summarize_energy_learning_profiles(getattr(self, "_energy_learning_profiles", {}))
            discharge_balance = derive_discharge_balance_metrics(
                cluster.sources,
                getattr(self, "_energy_learning_profiles", {}),
            )
            discharge_control = derive_discharge_control_metrics(
                cluster.sources,
                {
                    source.source_id: source
                    for source in tuple(getattr(self, "auto_energy_sources", ()) or (self._primary_energy_source(),))
                },
            )
            forecast = derive_energy_forecast(
                {
                    "battery_combined_charge_power_w": cluster.combined_charge_power_w,
                    "battery_combined_discharge_power_w": cluster.combined_discharge_power_w,
                    "battery_combined_charge_limit_power_w": cluster.combined_charge_limit_power_w,
                    "battery_combined_discharge_limit_power_w": cluster.combined_discharge_limit_power_w,
                    "battery_combined_grid_interaction_w": cluster.combined_grid_interaction_w,
                },
                learning_summary,
            )
            source_payloads = [source.as_dict() for source in cluster.sources]
            source_balance = cast(dict[str, dict[str, object]], discharge_balance.get("sources", {}))
            source_control = cast(dict[str, dict[str, object]], discharge_control.get("sources", {}))
            for source_payload in source_payloads:
                source_metrics = source_balance.get(str(source_payload.get("source_id", "")), {})
                source_payload.update(source_metrics)
                source_payload.update(source_control.get(str(source_payload.get("source_id", "")), {}))
            return {
                "battery_soc": effective_soc,
                "battery_combined_soc": cluster.combined_soc,
                "battery_combined_usable_capacity_wh": cluster.combined_usable_capacity_wh,
                "battery_combined_charge_power_w": cluster.combined_charge_power_w,
                "battery_combined_discharge_power_w": cluster.combined_discharge_power_w,
                "battery_combined_net_power_w": cluster.combined_net_battery_power_w,
                "battery_combined_ac_power_w": cluster.combined_ac_power_w,
                "battery_combined_pv_input_power_w": cluster.combined_pv_input_power_w,
                "battery_combined_grid_interaction_w": cluster.combined_grid_interaction_w,
                "battery_headroom_charge_w": forecast["battery_headroom_charge_w"],
                "battery_headroom_discharge_w": forecast["battery_headroom_discharge_w"],
                "expected_near_term_export_w": forecast["expected_near_term_export_w"],
                "expected_near_term_import_w": forecast["expected_near_term_import_w"],
                "battery_discharge_balance_mode": discharge_balance.get("mode"),
                "battery_discharge_balance_target_distribution_mode": discharge_balance.get("target_distribution_mode"),
                "battery_discharge_balance_error_w": discharge_balance.get("error_w"),
                "battery_discharge_balance_max_abs_error_w": discharge_balance.get("max_abs_error_w"),
                "battery_discharge_balance_total_discharge_w": discharge_balance.get("total_discharge_w"),
                "battery_discharge_balance_eligible_source_count": discharge_balance.get("eligible_source_count", 0),
                "battery_discharge_balance_active_source_count": discharge_balance.get("active_source_count", 0),
                "battery_discharge_balance_control_candidate_count": discharge_control.get("control_candidate_count", 0),
                "battery_discharge_balance_control_ready_count": discharge_control.get("control_ready_count", 0),
                "battery_discharge_balance_supported_control_source_count": discharge_control.get(
                    "supported_control_source_count",
                    0,
                ),
                "battery_discharge_balance_experimental_control_source_count": discharge_control.get(
                    "experimental_control_source_count",
                    0,
                ),
                "battery_average_confidence": cluster.average_confidence,
                "battery_source_count": cluster.source_count,
                "battery_online_source_count": cluster.online_source_count,
                "battery_valid_soc_source_count": cluster.valid_soc_source_count,
                "battery_battery_source_count": cluster.battery_source_count,
                "battery_hybrid_inverter_source_count": cluster.hybrid_inverter_source_count,
                "battery_inverter_source_count": cluster.inverter_source_count,
                "battery_sources": source_payloads,
                "battery_learning_profiles": {
                    source_id: profile.as_dict()
                    for source_id, profile in getattr(self, "_energy_learning_profiles", {}).items()
                },
            }
        except Exception:
            self._invalidate_auto_battery_service()
            self._delay_source_retry("battery")
            return {
                "battery_soc": None,
                "battery_combined_soc": None,
                "battery_combined_usable_capacity_wh": None,
                "battery_combined_charge_power_w": None,
                "battery_combined_discharge_power_w": None,
                "battery_combined_net_power_w": None,
                "battery_combined_ac_power_w": None,
                "battery_combined_pv_input_power_w": None,
                "battery_combined_grid_interaction_w": None,
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
                "battery_average_confidence": None,
                "battery_source_count": 0,
                "battery_online_source_count": 0,
                "battery_valid_soc_source_count": 0,
                "battery_battery_source_count": 0,
                "battery_hybrid_inverter_source_count": 0,
                "battery_inverter_source_count": 0,
                "battery_sources": [],
                "battery_learning_profiles": {},
            }

    def _get_battery_soc(self: Any) -> float | None:
        """Read battery SOC from the aggregated energy snapshot."""
        snapshot = self._get_battery_snapshot()
        battery_soc = snapshot.get("battery_soc")
        return None if battery_soc is None else float(battery_soc)

    @staticmethod
    def _battery_soc_numeric(value: object) -> float | None:
        """Return one numeric battery SOC value from raw DBus data."""
        numeric_value = coerce_dbus_numeric(value)
        if not isinstance(numeric_value, (int, float)):
            return None
        return float(numeric_value)

    def _validated_battery_soc(self: Any, numeric_value: float, service_name: str) -> float | None:
        """Return battery SOC when in range, otherwise warn and back off briefly."""
        if 0.0 <= numeric_value <= 100.0:
            return numeric_value
        self._warning_throttled(
            "auto-helper-battery-soc-invalid",
            max(5.0, self.auto_battery_scan_interval_seconds or 5.0),
            "Auto input helper ignored out-of-range battery SOC %s from %s %s",
            numeric_value,
            service_name,
            self.auto_battery_soc_path,
        )
        self._delay_source_retry("battery")
        return None

    def _get_grid_power(self: Any) -> float | None:
        """Read summed grid power from per-phase DBus paths."""
        if not self._source_retry_ready("grid"):
            return None
        configured_paths = configured_grid_paths(
            self.auto_grid_l1_path,
            self.auto_grid_l2_path,
            self.auto_grid_l3_path,
        )
        if not configured_paths:
            return None
        total, seen_value, missing_paths = self._grid_total_and_missing_paths(configured_paths)
        if grid_values_complete_enough(seen_value, missing_paths, self.auto_grid_require_all_phases):
            return float(total)
        self._delay_source_retry("grid")
        return None

    def _grid_total_and_missing_paths(self: Any, configured_paths: list[str]) -> tuple[float, bool, list[str]]:
        """Return summed grid total plus missing-path information."""
        total = 0.0
        seen_value = False
        missing_paths: list[str] = []
        for path in configured_paths:
            numeric_value = self._grid_path_numeric_value(path)
            if numeric_value is None:
                missing_paths.append(path)
                continue
            total += numeric_value
            seen_value = True
        return total, seen_value, missing_paths

    def _grid_path_numeric_value(self: Any, path: str) -> float | None:
        """Return one numeric grid reading for the given per-phase path."""
        try:
            value = self._get_dbus_value(self.auto_grid_service, path)
        except Exception:
            return None
        if value is None:
            return None
        return sum_dbus_numeric(value)
