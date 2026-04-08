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


class _DbusInputStorageMixin(_ComposableControllerMixin):
    def _battery_service_has_soc(self, service_name: str) -> bool:
        """Return whether the given battery service currently provides a readable SOC."""
        try:
            soc_value = self.service._get_dbus_value(service_name, self.service.auto_battery_soc_path)
        except Exception:
            return False
        return soc_value is not None

    def _resolve_battery_service_override(self) -> str | None:
        """Try the configured battery override service before scanning by prefix."""
        svc = self.service
        if not svc.auto_battery_service:
            return None
        if self._battery_service_has_soc(svc.auto_battery_service):
            return svc.auto_battery_service
        logging.debug(
            "Auto battery service override %s missing SOC, falling back to prefix scan.",
            svc.auto_battery_service,
        )
        return None

    def _cached_auto_battery_service(self, now: float) -> str | None:
        """Return the cached battery service if its discovery result is still fresh."""
        svc = self.service
        if discovery_cache_valid(
            svc._resolved_auto_battery_service,
            svc._auto_battery_last_scan,
            svc.auto_battery_scan_interval_seconds,
            now,
        ):
            return svc._resolved_auto_battery_service
        return None

    def _scan_auto_battery_service(self, now: float) -> str:
        """Scan DBus services by prefix until a readable battery SOC appears."""
        svc = self.service
        service_name = first_matching_prefixed_service(
            svc._list_dbus_services(),
            svc.auto_battery_service_prefix,
            self._battery_service_has_soc,
        )
        if service_name is not None:
            svc._resolved_auto_battery_service = service_name
            svc._auto_battery_last_scan = now
            logging.debug("Auto battery service resolved: %s", svc._resolved_auto_battery_service)
            return svc._resolved_auto_battery_service
        raise ValueError(f"No DBus service found with prefix '{svc.auto_battery_service_prefix}'")

    def resolve_auto_battery_service(self) -> str:
        """Resolve a battery service, falling back to prefix scan if needed."""
        override_service = self._resolve_battery_service_override()
        if override_service is not None:
            return override_service
        now = time.time()
        cached_service = self._cached_auto_battery_service(now)
        if cached_service is not None:
            return cached_service
        return self._scan_auto_battery_service(now)

    def get_battery_soc(self) -> float | None:
        """Read battery SOC from the resolved battery service."""
        svc = self.service
        now = time.time()
        if not self._source_retry_ready("battery", now):
            return None
        try:
            service_name = svc._resolve_auto_battery_service()
            try:
                value = svc._get_dbus_value(service_name, svc.auto_battery_soc_path)
            except Exception:
                svc._invalidate_auto_battery_service()
                service_name = svc._resolve_auto_battery_service()
                value = svc._get_dbus_value(service_name, svc.auto_battery_soc_path)
            value = self._coerce_dbus_value(value)
            if not isinstance(value, (int, float)):
                raise TypeError(f"Battery SOC is not numeric: {type(value).__name__}")
            self._mark_source_recovery("battery", "Battery SOC readings recovered")
            return float(value)
        except Exception as error:  # pylint: disable=broad-except
            return self._handle_source_failure(
                "battery",
                now,
                "battery-missing",
                svc.auto_battery_scan_interval_seconds,
                "Auto mode could not read battery SOC from %s %s: %s",
                svc.auto_battery_service or svc.auto_battery_service_prefix,
                svc.auto_battery_soc_path,
                error,
            )

    def get_grid_power(self) -> float | None:
        """Read and sum grid power from per-phase paths."""
        svc = self.service
        now = time.time()
        if not self._source_retry_ready("grid", now):
            return None
        configured_paths = self._configured_grid_paths()
        if not configured_paths:
            return None
        total, seen_value, missing_paths = self._read_grid_phase_values(configured_paths)
        return self._finalize_grid_power(total, seen_value, missing_paths, now)

    def _configured_grid_paths(self) -> list[str]:
        """Return the configured per-phase grid DBus paths."""
        svc = self.service
        return configured_grid_paths(svc.auto_grid_l1_path, svc.auto_grid_l2_path, svc.auto_grid_l3_path)

    def _read_grid_phase_values(self, configured_paths: list[str]) -> tuple[float, bool, list[str]]:
        """Read all configured grid phase paths and track missing values."""
        svc = self.service
        total = 0.0
        seen_value = False
        missing_paths = []
        for path in configured_paths:
            try:
                value = svc._get_dbus_value(svc.auto_grid_service, path)
            except Exception as error:  # pylint: disable=broad-except
                logging.debug("Auto grid read failed for %s %s: %s", svc.auto_grid_service, path, error)
                missing_paths.append(path)
                continue
            if value is not None:
                numeric_value = self._numeric_sum(value)
                if numeric_value is not None:
                    total += numeric_value
                    seen_value = True
                else:
                    missing_paths.append(path)
            else:
                missing_paths.append(path)
        return total, seen_value, missing_paths

    def _grid_values_complete_enough(self, seen_value: bool, missing_paths: list[str]) -> bool:
        """Return whether the available grid phase values are sufficient for control."""
        return grid_values_complete_enough(
            seen_value,
            missing_paths,
            self.service.auto_grid_require_all_phases,
        )

    def _handle_missing_grid_values(self, seen_value: bool, missing_paths: list[str], now: float) -> float | None:
        """Handle incomplete or unreadable grid data."""
        svc = self.service
        if seen_value and missing_paths:
            logging.debug(
                "Auto grid readings incomplete for %s, missing paths: %s",
                svc.auto_grid_service,
                ", ".join(missing_paths),
            )
        return self._handle_source_failure(
            "grid",
            now,
            "grid-missing",
            svc.auto_pv_scan_interval_seconds,
            "Auto mode could not read grid power from %s.",
            svc.auto_grid_service,
        )

    def _finalize_grid_power(
        self,
        total: float,
        seen_value: bool,
        missing_paths: list[str],
        now: float,
    ) -> float | None:
        """Return the effective grid power or trigger the missing-data fallback."""
        if self._grid_values_complete_enough(seen_value, missing_paths):
            self._mark_source_recovery("grid", "Grid readings recovered")
            return total
        return self._handle_missing_grid_values(seen_value, missing_paths, now)
