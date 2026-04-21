# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared DBus input-reading helpers for the Venus EV charger service."""

from __future__ import annotations

import logging
import time
from typing import Any, cast

from venus_evcharger.core.shared import (
    configured_grid_paths,
    discovery_cache_valid,
    first_matching_prefixed_service,
    grid_values_complete_enough,
)
from venus_evcharger.energy import (
    EnergySourceDefinition,
    EnergySourceSnapshot,
    aggregate_energy_sources,
    update_energy_learning_profiles,
)
from venus_evcharger.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin


class _DbusInputStorageMixin(_ComposableControllerMixin):
    def _primary_energy_source(self) -> EnergySourceDefinition:
        sources = tuple(getattr(self.service, "auto_energy_sources", ()) or ())
        if sources:
            return cast(EnergySourceDefinition, sources[0])
        return EnergySourceDefinition(
            source_id="primary_battery",
            role="battery",
            service_name=str(getattr(self.service, "auto_battery_service", "") or ""),
            service_prefix=str(getattr(self.service, "auto_battery_service_prefix", "") or ""),
            soc_path=str(getattr(self.service, "auto_battery_soc_path", "/Soc") or "/Soc"),
            usable_capacity_wh=getattr(self.service, "auto_battery_capacity_wh", None),
            battery_power_path=str(getattr(self.service, "auto_battery_power_path", "") or ""),
            ac_power_path=str(getattr(self.service, "auto_battery_ac_power_path", "") or ""),
        )

    def _battery_service_has_soc(self, service_name: str) -> bool:
        """Return whether the given battery service currently provides a readable SOC."""
        try:
            soc_value = self.service._get_dbus_value(service_name, self.service.auto_battery_soc_path)
        except Exception:
            return False
        return soc_value is not None

    def _energy_service_has_readable_field(self, service_name: str, path: str) -> bool:
        if not path:
            return False
        try:
            return self.service._get_dbus_value(service_name, path) is not None
        except Exception:
            return False

    def _energy_source_has_readable_data(self, source: EnergySourceDefinition, service_name: str) -> bool:
        return any(
            (
                self._energy_service_has_readable_field(service_name, source.soc_path),
                self._energy_service_has_readable_field(service_name, source.battery_power_path),
                self._energy_service_has_readable_field(service_name, source.ac_power_path),
            )
        )

    def _resolve_battery_service_override(self) -> str | None:
        """Try the configured battery override service before scanning by prefix."""
        svc = self.service
        source = self._primary_energy_source()
        if not source.service_name:
            return None
        if self._battery_service_has_soc(source.service_name) or self._energy_source_has_readable_data(source, source.service_name):
            battery_service: str = source.service_name
            return battery_service
        logging.debug(
            "Auto battery service override %s missing SOC, falling back to prefix scan.",
            source.service_name,
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
            return cast(str | None, svc._resolved_auto_battery_service)
        return None

    def _energy_cache_valid(self, source_id: str, now: float) -> str | None:
        svc = self.service
        resolved = getattr(svc, "_resolved_auto_energy_services", {})
        last_scan = getattr(svc, "_auto_energy_last_scan", {})
        cached_service = resolved.get(source_id) if isinstance(resolved, dict) else None
        cached_scan_at = last_scan.get(source_id, 0.0) if isinstance(last_scan, dict) else 0.0
        if discovery_cache_valid(
            cached_service,
            cached_scan_at,
            svc.auto_battery_scan_interval_seconds,
            now,
        ):
            return cast(str | None, cached_service)
        return None

    def _remember_energy_service(self, source_id: str, service_name: str, now: float) -> str:
        svc = self.service
        if not isinstance(getattr(svc, "_resolved_auto_energy_services", None), dict):
            svc._resolved_auto_energy_services = {}
        if not isinstance(getattr(svc, "_auto_energy_last_scan", None), dict):
            svc._auto_energy_last_scan = {}
        svc._resolved_auto_energy_services[source_id] = service_name
        svc._auto_energy_last_scan[source_id] = now
        return service_name

    def _resolve_energy_source_service(self, source: EnergySourceDefinition) -> str:
        now = time.time()
        if source.source_id == self._primary_energy_source().source_id:
            primary_service = self.service._resolve_auto_battery_service()
            resolved_primary_service: str = str(primary_service)
            return resolved_primary_service
        if source.service_name and self._energy_source_has_readable_data(source, source.service_name):
            return self._remember_energy_service(source.source_id, source.service_name, now)
        cached_service = self._energy_cache_valid(source.source_id, now)
        if cached_service is not None:
            return cached_service
        if not source.service_prefix:
            raise ValueError(f"No readable DBus service configured for energy source '{source.source_id}'")
        service_name = first_matching_prefixed_service(
            self.service._list_dbus_services(),
            source.service_prefix,
            lambda candidate: self._energy_source_has_readable_data(source, candidate),
        )
        if service_name is None:
            raise ValueError(f"No DBus service found for energy source '{source.source_id}'")
        return self._remember_energy_service(source.source_id, service_name, now)

    def _scan_auto_battery_service(self, now: float) -> str:
        """Scan DBus services by prefix until a readable battery SOC appears."""
        svc = self.service
        source = self._primary_energy_source()
        battery_service_prefix = str(getattr(svc, "auto_battery_service_prefix", "") or "")
        service_name = first_matching_prefixed_service(
            svc._list_dbus_services(),
            source.service_prefix or battery_service_prefix,
            lambda candidate: self._energy_source_has_readable_data(source, candidate),
        )
        if service_name is not None:
            svc._resolved_auto_battery_service = service_name
            svc._auto_battery_last_scan = now
            self._remember_energy_service(source.source_id, service_name, now)
            logging.debug("Auto battery service resolved: %s", svc._resolved_auto_battery_service)
            resolved_service: str = svc._resolved_auto_battery_service
            return resolved_service
        raise ValueError(f"No DBus service found with prefix '{source.service_prefix or battery_service_prefix}'")

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

    def _read_optional_energy_value(self, service_name: str, path: str) -> float | None:
        if not path:
            return None
        value = self.service._get_dbus_value(service_name, path)
        return self._battery_soc_numeric(value)

    def _read_energy_source_snapshot(self, source: EnergySourceDefinition, now: float) -> EnergySourceSnapshot:
        service_name = self._resolve_energy_source_service(source)
        try:
            soc_value = self._read_optional_energy_value(service_name, source.soc_path)
            net_battery_power = self._read_optional_energy_value(service_name, source.battery_power_path)
            ac_power = self._read_optional_energy_value(service_name, source.ac_power_path)
        except Exception:
            if source.source_id != self._primary_energy_source().source_id:
                raise
            self.service._invalidate_auto_battery_service()
            service_name = self._resolve_energy_source_service(source)
            soc_value = self._read_optional_energy_value(service_name, source.soc_path)
            net_battery_power = self._read_optional_energy_value(service_name, source.battery_power_path)
            ac_power = self._read_optional_energy_value(service_name, source.ac_power_path)
        if soc_value is not None and not 0.0 <= soc_value <= 100.0:
            soc_value = None
        return EnergySourceSnapshot(
            source_id=source.source_id,
            role=source.role,
            service_name=service_name,
            soc=soc_value,
            usable_capacity_wh=source.usable_capacity_wh,
            net_battery_power_w=net_battery_power,
            ac_power_w=ac_power,
            online=True,
            confidence=1.0,
            captured_at=now,
        )

    def get_battery_snapshot(self) -> dict[str, object]:
        """Return aggregated battery and inverter source data for Auto mode."""
        svc = self.service
        now = time.time()
        if not self._source_retry_ready("battery", now):
            return {"battery_soc": None}
        try:
            source_snapshots: list[EnergySourceSnapshot] = []
            for source in tuple(getattr(svc, "auto_energy_sources", ()) or (self._primary_energy_source(),)):
                source_snapshots.append(self._read_energy_source_snapshot(cast(EnergySourceDefinition, source), now))
            cluster = aggregate_energy_sources(source_snapshots)
            primary_soc = cluster.sources[0].soc if cluster.sources else None
            effective_soc = cluster.effective_soc if bool(getattr(svc, "auto_use_combined_battery_soc", True)) else primary_soc
            if effective_soc is None and not any(source.soc is not None for source in cluster.sources):
                raise TypeError("Battery SOC is not numeric")
            cache_owner = getattr(svc, "_service", svc)
            cache_owner._last_energy_learning_profiles = update_energy_learning_profiles(
                getattr(cache_owner, "_last_energy_learning_profiles", {}),
                cluster.sources,
                now,
            )
            self._mark_source_recovery("battery", "Battery SOC readings recovered")
            battery_payload: dict[str, object] = {
                "battery_soc": effective_soc,
                "battery_combined_soc": cluster.combined_soc,
                "battery_combined_usable_capacity_wh": cluster.combined_usable_capacity_wh,
                "battery_combined_charge_power_w": cluster.combined_charge_power_w,
                "battery_combined_discharge_power_w": cluster.combined_discharge_power_w,
                "battery_combined_net_power_w": cluster.combined_net_battery_power_w,
                "battery_combined_ac_power_w": cluster.combined_ac_power_w,
                "battery_source_count": cluster.source_count,
                "battery_online_source_count": cluster.online_source_count,
                "battery_valid_soc_source_count": cluster.valid_soc_source_count,
                "battery_sources": [source.as_dict() for source in cluster.sources],
                "battery_learning_profiles": {
                    source_id: profile.as_dict()
                    for source_id, profile in getattr(cache_owner, "_last_energy_learning_profiles", {}).items()
                },
            }
            setattr(cache_owner, "_last_energy_cluster", dict(battery_payload))
            return battery_payload
        except Exception as error:  # pylint: disable=broad-except
            battery_service_name = str(getattr(svc, "auto_battery_service", "") or "")
            battery_service_prefix = str(getattr(svc, "auto_battery_service_prefix", "") or "")
            failure = self._handle_source_failure(
                "battery",
                now,
                "battery-missing",
                svc.auto_battery_scan_interval_seconds,
                "Auto mode could not read battery SOC from %s %s: %s",
                battery_service_name or battery_service_prefix,
                svc.auto_battery_soc_path,
                error,
            )
            return {
                "battery_soc": cast(float | None, failure),
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
            }

    def get_battery_soc(self) -> float | None:
        """Read battery SOC from the resolved battery service."""
        snapshot = self.get_battery_snapshot()
        battery_soc = snapshot.get("battery_soc")
        if isinstance(battery_soc, (int, float)):
            return float(battery_soc)
        return None

    def _read_battery_soc_value(self) -> object:
        """Read one raw battery SOC value, retrying once after invalidating cached service discovery."""
        svc = self.service
        service_name = svc._resolve_auto_battery_service()
        try:
            return svc._get_dbus_value(service_name, svc.auto_battery_soc_path)
        except Exception:
            svc._invalidate_auto_battery_service()
            service_name = svc._resolve_auto_battery_service()
            return svc._get_dbus_value(service_name, svc.auto_battery_soc_path)

    def _battery_soc_numeric(self, value: object) -> float | None:
        """Return one numeric battery SOC value after DBus coercion."""
        coerced_value = self._coerce_dbus_value(value)
        if not isinstance(coerced_value, (int, float)):
            return None
        return float(coerced_value)

    def get_grid_power(self) -> float | None:
        """Read and sum grid power from per-phase paths."""
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
        grid_paths = configured_grid_paths(svc.auto_grid_l1_path, svc.auto_grid_l2_path, svc.auto_grid_l3_path)
        return grid_paths

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
        complete_enough = grid_values_complete_enough(
            seen_value,
            missing_paths,
            self.service.auto_grid_require_all_phases,
        )
        return complete_enough

    def _handle_missing_grid_values(self, seen_value: bool, missing_paths: list[str], now: float) -> float | None:
        """Handle incomplete or unreadable grid data."""
        svc = self.service
        if seen_value and missing_paths:
            logging.debug(
                "Auto grid readings incomplete for %s, missing paths: %s",
                svc.auto_grid_service,
                ", ".join(missing_paths),
            )
        return cast(float | None, self._handle_source_failure(
            "grid",
            now,
            "grid-missing",
            svc.auto_pv_scan_interval_seconds,
            "Auto mode could not read grid power from %s.",
            svc.auto_grid_service,
        ))

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
