# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared DBus input-reading helpers for the Shelly wallbox service."""

from __future__ import annotations

import logging
import time
from typing import Any

import dbus
from dbus_shelly_wallbox_shared import (
    coerce_dbus_numeric,
    configured_grid_paths,
    discovery_cache_valid,
    first_matching_prefixed_service,
    grid_values_complete_enough,
    prefixed_service_names,
    should_assume_zero_pv,
    sum_dbus_numeric,
)
from dbus_shelly_wallbox_split_mixins import _ComposableControllerMixin


class _DbusInputPvMixin(_ComposableControllerMixin):
    @staticmethod
    def _coerce_dbus_value(value: Any) -> float | int | None:
        """Convert raw DBus values to numbers where possible."""
        return coerce_dbus_numeric(value)

    @staticmethod
    def _numeric_sum(value: Any) -> float | None:
        """Return a usable numeric sum for scalar or sequence power values."""
        return sum_dbus_numeric(value)

    @staticmethod
    def _mark_dbus_success(svc: Any) -> None:
        """Record a successful DBus read or listing operation."""
        svc._last_dbus_ok_at = time.time()
        svc._mark_recovery("dbus", "DBus reads recovered")

    def _source_retry_ready(self, source_key: str, now: float) -> bool:
        """Return whether a logical input source may currently be queried."""
        return self.service._source_retry_ready(source_key, now)

    def _mark_source_recovery(self, source_key: str, message: str, *args: Any) -> None:
        """Record that one logical input source has recovered."""
        self.service._mark_recovery(source_key, message, *args)

    def _handle_source_failure(
        self,
        source_key: str,
        now: float,
        warning_key: str,
        warning_interval: float,
        warning_message: str,
        *args: Any,
    ) -> float | None:
        """Apply the standard failure/backoff/warning flow for one input source."""
        svc = self.service
        svc._mark_failure(source_key)
        svc._delay_source_retry(source_key, now)
        svc._warning_throttled(warning_key, warning_interval, warning_message, *args)
        return None

    def get_dbus_value(self, service_name: str, path: str) -> float | int | None:
        """Read a DBus value via com.victronenergy.BusItem."""
        svc = self.service
        last_error: Exception | None = None
        timeout = getattr(svc, "dbus_method_timeout_seconds", 1.0)
        for attempt in range(2):
            try:
                bus = svc._get_system_bus()
                obj = bus.get_object(service_name, path)
                interface = dbus.Interface(obj, "com.victronenergy.BusItem")
                value = interface.GetValue(timeout=timeout)
                self._mark_dbus_success(svc)
                return self._coerce_dbus_value(value)
            except Exception as error:  # pylint: disable=broad-except
                last_error = error
                svc._reset_system_bus()
                if attempt == 0:
                    svc._mark_failure("dbus")
                    logging.debug(
                        "DBus read retry for %s %s after error: %s",
                        service_name,
                        path,
                        error,
                    )
        assert last_error is not None
        raise last_error

    def list_dbus_services(self) -> list[str]:
        """List DBus services with exponential backoff for unstable buses."""
        svc = self.service
        if not hasattr(svc, "_dbus_list_backoff_until"):
            svc._dbus_list_backoff_until = 0.0
        if not hasattr(svc, "_dbus_list_failures"):
            svc._dbus_list_failures = 0
        if not hasattr(svc, "auto_dbus_backoff_base_seconds"):
            svc.auto_dbus_backoff_base_seconds = 5.0
        if not hasattr(svc, "auto_dbus_backoff_max_seconds"):
            svc.auto_dbus_backoff_max_seconds = 60.0
        if not hasattr(svc, "dbus_method_timeout_seconds"):
            svc.dbus_method_timeout_seconds = 1.0

        now = time.time()
        if now < svc._dbus_list_backoff_until:
            raise RuntimeError("DBus list backoff active")
        bus = svc._get_system_bus()
        dbus_obj = bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
        dbus_if = dbus.Interface(dbus_obj, "org.freedesktop.DBus")
        try:
            names = [str(name) for name in dbus_if.ListNames(timeout=svc.dbus_method_timeout_seconds)]
            svc._dbus_list_failures = 0
            svc._dbus_list_backoff_until = 0.0
            self._mark_dbus_success(svc)
            return names
        except Exception:  # pylint: disable=broad-except
            svc._reset_system_bus()
            svc._dbus_list_failures += 1
            svc._mark_failure("dbus")
            delay = min(
                svc.auto_dbus_backoff_max_seconds,
                svc.auto_dbus_backoff_base_seconds * (2 ** (svc._dbus_list_failures - 1)),
            )
            svc._dbus_list_backoff_until = now + delay
            raise

    def invalidate_auto_pv_services(self) -> None:
        """Clear cached PV service discovery so the next read performs a fresh scan."""
        svc = self.service
        svc._resolved_auto_pv_services = []
        svc._auto_pv_last_scan = 0.0

    def invalidate_auto_battery_service(self) -> None:
        """Clear cached battery service discovery so the next read performs a fresh scan."""
        svc = self.service
        svc._resolved_auto_battery_service = None
        svc._auto_battery_last_scan = 0.0

    def resolve_auto_pv_services(self) -> list[str]:
        """Resolve AC PV services (or use explicit override) for Auto mode."""
        svc = self.service
        if svc.auto_pv_service:
            return [svc.auto_pv_service]

        now = time.time()
        if discovery_cache_valid(
            svc._resolved_auto_pv_services,
            svc._auto_pv_last_scan,
            svc.auto_pv_scan_interval_seconds,
            now,
        ):
            return svc._resolved_auto_pv_services

        service_names = prefixed_service_names(
            svc._list_dbus_services(),
            svc.auto_pv_service_prefix,
            max_services=svc.auto_pv_max_services,
            sort_names=True,
        )
        svc._resolved_auto_pv_services = service_names
        svc._auto_pv_last_scan = now
        logging.debug("Auto PV services resolved: %s", svc._resolved_auto_pv_services)
        if not svc._resolved_auto_pv_services:
            raise ValueError(f"No DBus service found with prefix '{svc.auto_pv_service_prefix}'")
        return svc._resolved_auto_pv_services

    def _resolve_pv_service_names(self) -> tuple[list[str], bool]:
        """Resolve current AC PV service names and whether auto-discovery found none."""
        svc = self.service
        try:
            return svc._resolve_auto_pv_services(), False
        except ValueError as error:
            logging.debug("Auto AC PV service resolution found no services: %s", error)
            return [], not bool(svc.auto_pv_service)
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Auto AC PV service resolution failed: %s", error)
            return [], False

    @staticmethod
    def _should_rescan_pv_services(
        svc: Any,
        service_names: list[str],
        seen_value: bool,
        saw_ac_service_failure: bool,
    ) -> bool:
        """Return whether a one-time rescan of cached AC PV services is worthwhile."""
        return (
            saw_ac_service_failure
            and not seen_value
            and not svc.auto_pv_service
            and bool(service_names)
        )

    def _read_pv_service_values(
        self,
        service_names: list[str],
        log_suffix: str = "",
    ) -> tuple[float, bool, bool]:
        """Read AC PV values from the provided services."""
        svc = self.service
        total = 0.0
        seen_value = False
        saw_failure = False
        for service_name in service_names:
            try:
                value = svc._get_dbus_value(service_name, svc.auto_pv_path)
            except Exception as error:  # pylint: disable=broad-except
                logging.debug(
                    "Auto PV read failed%s for %s %s: %s",
                    log_suffix,
                    service_name,
                    svc.auto_pv_path,
                    error,
                )
                saw_failure = True
                continue
            if value is not None:
                numeric_value = self._numeric_sum(value)
                if numeric_value is not None:
                    total += numeric_value
                    seen_value = True
        return total, seen_value, saw_failure

    def _read_rescanned_pv_services(self) -> tuple[float, bool]:
        """Refresh cached AC PV services once and retry reading them."""
        svc = self.service
        svc._invalidate_auto_pv_services()
        try:
            rescanned_services = svc._resolve_auto_pv_services()
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Auto AC PV rescan failed: %s", error)
            return 0.0, False
        total, seen_value, _ = self._read_pv_service_values(
            rescanned_services,
            log_suffix=" after rescan",
        )
        return total, seen_value

    def _read_dc_pv_value(self) -> tuple[Any, bool]:
        """Read optional DC PV power."""
        svc = self.service
        if not svc.auto_use_dc_pv:
            return None, False
        try:
            return svc._get_dbus_value(svc.auto_dc_pv_service, svc.auto_dc_pv_path), False
        except Exception as error:  # pylint: disable=broad-except
            logging.debug(
                "Auto DC PV read failed for %s %s: %s",
                svc.auto_dc_pv_service,
                svc.auto_dc_pv_path,
                error,
            )
            return None, True

    def _should_assume_zero_pv(
        self,
        service_names: list[str],
        no_auto_ac_services_found: bool,
        dc_value: Any,
    ) -> bool:
        """Return whether missing PV data should be treated as 0 W."""
        svc = self.service
        return should_assume_zero_pv(
            svc.auto_pv_service,
            service_names,
            no_auto_ac_services_found,
            svc.auto_use_dc_pv,
            dc_value,
        )

    def _handle_missing_pv_power(
        self,
        service_names: list[str],
        no_auto_ac_services_found: bool,
        dc_value: Any,
        dc_read_failed: bool,
        now: float,
    ) -> float | None:
        """Handle the fallback when neither AC nor DC PV yielded a usable value."""
        svc = self.service
        if self._should_assume_zero_pv(service_names, no_auto_ac_services_found, dc_value):
            logging.debug("No readable AC/DC PV values discovered; assuming 0 W PV power.")
            svc._last_pv_missing_warning = None
            if not dc_read_failed:
                self._mark_source_recovery("pv", "No readable PV values discovered, assuming 0 W")
            return 0.0

        return self._handle_source_failure(
            "pv",
            now,
            "pv-missing",
            svc.auto_pv_scan_interval_seconds,
            "Auto mode could not read any PV power values from %s (or DC PV).",
            svc.auto_pv_service or svc.auto_pv_service_prefix,
        )

    def get_pv_power(self) -> float | None:
        """Return summed PV power from all AC PV services and optional DC PV."""
        svc = self.service
        now = time.time()
        if not self._source_retry_ready("pv", now):
            return None
        service_names, no_auto_ac_services_found = self._resolve_pv_service_names()
        total, seen_value, saw_ac_service_failure = self._read_pv_service_values(service_names)

        if self._should_rescan_pv_services(svc, service_names, seen_value, saw_ac_service_failure):
            rescanned_total, rescanned_seen_value = self._read_rescanned_pv_services()
            total += rescanned_total
            seen_value = seen_value or rescanned_seen_value

        dc_value, dc_read_failed = self._read_dc_pv_value()
        numeric_dc_value = self._numeric_sum(dc_value)
        if numeric_dc_value is not None:
            total += numeric_dc_value
            seen_value = True

        if seen_value:
            svc._last_pv_missing_warning = None
            self._mark_source_recovery("pv", "PV readings recovered")
            return total
        return self._handle_missing_pv_power(
            service_names,
            no_auto_ac_services_found,
            dc_value,
            dc_read_failed,
            now,
        )
