#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Collect PV, battery, and grid inputs for the Shelly wallbox in a helper process.

The helper exists so DBus discovery and polling cannot stall the main wallbox
service. It periodically writes a compact JSON snapshot that the main process
can consume safely, even if DBus becomes slow or temporarily inconsistent.
"""

import logging
import time
import xml.etree.ElementTree as xml_et
from typing import Any, cast

import dbus
from shelly_wallbox.core.shared import (
    configured_grid_paths,
    coerce_dbus_numeric,
    discovery_cache_valid,
    first_matching_prefixed_service,
    grid_values_complete_enough,
    prefixed_service_names,
    should_assume_zero_pv,
    sum_dbus_numeric,
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
                root = xml_et.fromstring(str(xml_data))
                child_nodes = [str(name) for node in root.findall("node") if (name := node.attrib.get("name"))]
                return child_nodes
            except Exception as error:  # pylint: disable=broad-except
                last_error = error
                self._reset_system_bus()
                if attempt == 0:
                    logging.debug("DBus introspection retry for %s %s after error: %s", service_name, path, error)
        assert last_error is not None
        raise last_error

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

    def _battery_service_has_soc(self: Any, service_name: str) -> bool:
        """Return whether the candidate battery service currently exposes SOC."""
        try:
            return self._get_dbus_value(service_name, self.auto_battery_soc_path) is not None
        except Exception:
            return False

    def _resolve_auto_battery_service(self: Any) -> str:
        """Resolve battery service from config or DBus discovery."""
        if self.auto_battery_service:
            try:
                if self._get_dbus_value(self.auto_battery_service, self.auto_battery_soc_path) is not None:
                    self._resolved_auto_battery_service = self.auto_battery_service
                    self._auto_battery_last_scan = time.time()
                    resolved_override: str = self._resolved_auto_battery_service
                    return resolved_override
            except Exception:
                pass
        now = time.time()
        if discovery_cache_valid(
            self._resolved_auto_battery_service,
            self._auto_battery_last_scan,
            self.auto_battery_scan_interval_seconds,
            now,
        ):
            cached_service: str = self._resolved_auto_battery_service
            return cached_service
        service_name = first_matching_prefixed_service(
            self._list_dbus_services(),
            self.auto_battery_service_prefix,
            self._battery_service_has_soc,
        )
        if service_name is not None:
            self._resolved_auto_battery_service = service_name
            self._auto_battery_last_scan = now
            resolved_service: str = self._resolved_auto_battery_service
            return resolved_service
        raise ValueError(f"No DBus service found with prefix '{self.auto_battery_service_prefix}'")

    def _get_battery_soc(self: Any) -> float | None:
        """Read battery SOC from the resolved battery service."""
        if not self._source_retry_ready("battery"):
            return None
        try:
            service_name = self._resolve_auto_battery_service()
            value = self._get_dbus_value(service_name, self.auto_battery_soc_path)
            value = coerce_dbus_numeric(value)
            if not isinstance(value, (int, float)):
                return None
            numeric_value = float(value)
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
        except Exception:
            self._invalidate_auto_battery_service()
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
        total = 0.0
        seen_value = False
        missing_paths = []
        for path in configured_paths:
            try:
                value = self._get_dbus_value(self.auto_grid_service, path)
            except Exception:
                value = None
            if value is not None:
                numeric_value = sum_dbus_numeric(value)
                if numeric_value is not None:
                    total += numeric_value
                    seen_value = True
                else:
                    missing_paths.append(path)
            else:
                missing_paths.append(path)
        if grid_values_complete_enough(seen_value, missing_paths, self.auto_grid_require_all_phases):
            return total
        self._delay_source_retry("grid")
        return None
