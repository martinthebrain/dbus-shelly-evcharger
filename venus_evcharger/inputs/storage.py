# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared DBus input-reading helpers for the Venus EV charger service."""

from __future__ import annotations

import logging
import time
from typing import cast

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
    derive_discharge_balance_metrics,
    derive_discharge_control_metrics,
    derive_energy_forecast,
    read_energy_source_snapshot,
    summarize_energy_learning_profiles,
    update_energy_learning_profiles,
)
from venus_evcharger.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin


class _DbusInputStorageMixin(_ComposableControllerMixin):
    def _configured_primary_energy_sources(self) -> tuple[EnergySourceDefinition, ...]:
        return tuple(getattr(self.service, "auto_energy_sources", ()) or ())

    @staticmethod
    def _primary_energy_source_id() -> str:
        return "primary_battery"

    @staticmethod
    def _primary_energy_source_role() -> str:
        return "battery"

    def _primary_energy_service_name(self) -> str:
        return str(getattr(self.service, "auto_battery_service", "") or "")

    def _primary_energy_service_prefix(self) -> str:
        return str(getattr(self.service, "auto_battery_service_prefix", "") or "")

    def _primary_energy_soc_path(self) -> str:
        return str(getattr(self.service, "auto_battery_soc_path", "/Soc") or "/Soc")

    def _primary_energy_capacity_wh(self) -> object:
        return getattr(self.service, "auto_battery_capacity_wh", None)

    def _primary_energy_battery_power_path(self) -> str:
        return str(getattr(self.service, "auto_battery_power_path", "") or "")

    def _primary_energy_ac_power_path(self) -> str:
        return str(getattr(self.service, "auto_battery_ac_power_path", "") or "")

    def _primary_energy_pv_power_path(self) -> str:
        return str(getattr(self.service, "auto_battery_pv_power_path", "") or "")

    def _primary_energy_grid_interaction_path(self) -> str:
        return str(getattr(self.service, "auto_battery_grid_interaction_path", "") or "")

    def _primary_energy_operating_mode_path(self) -> str:
        return str(getattr(self.service, "auto_battery_operating_mode_path", "") or "")

    def _default_primary_energy_source(self) -> EnergySourceDefinition:
        return EnergySourceDefinition(
            source_id=self._primary_energy_source_id(),
            role=self._primary_energy_source_role(),
            service_name=self._primary_energy_service_name(),
            service_prefix=self._primary_energy_service_prefix(),
            soc_path=self._primary_energy_soc_path(),
            usable_capacity_wh=self._primary_energy_capacity_wh(),
            battery_power_path=self._primary_energy_battery_power_path(),
            ac_power_path=self._primary_energy_ac_power_path(),
            pv_power_path=self._primary_energy_pv_power_path(),
            grid_interaction_path=self._primary_energy_grid_interaction_path(),
            operating_mode_path=self._primary_energy_operating_mode_path(),
        )

    def _primary_energy_source(self) -> EnergySourceDefinition:
        sources = self._configured_primary_energy_sources()
        if sources:
            return cast(EnergySourceDefinition, sources[0])
        return self._default_primary_energy_source()

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
                self._energy_service_has_readable_field(service_name, source.pv_power_path),
                self._energy_service_has_readable_field(service_name, source.grid_interaction_path),
                self._energy_service_has_readable_field(service_name, source.operating_mode_path),
            )
        )

    def _resolve_battery_service_override(self) -> str | None:
        """Try the configured battery override service before scanning by prefix."""
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

    def _primary_energy_source_service(self) -> str:
        return str(self.service._resolve_auto_battery_service())

    def _configured_energy_source_service(self, source: EnergySourceDefinition, now: float) -> str | None:
        if not source.service_name or not self._energy_source_has_readable_data(source, source.service_name):
            return None
        return self._remember_energy_service(source.source_id, source.service_name, now)

    def _discovered_energy_source_service(self, source: EnergySourceDefinition, now: float) -> str:
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

    def _resolve_energy_source_service(self, source: EnergySourceDefinition) -> str:
        now = time.time()
        if source.source_id == self._primary_energy_source().source_id:
            return self._primary_energy_source_service()
        configured_service = self._configured_energy_source_service(source, now)
        if configured_service is not None:
            return configured_service
        cached_service = self._energy_cache_valid(source.source_id, now)
        if cached_service is not None:
            return cached_service
        return self._discovered_energy_source_service(source, now)

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

    def _read_optional_energy_text(self, service_name: str, path: str) -> str:
        if not path:
            return ""
        value = self.service._get_dbus_value(service_name, path)
        return "" if value is None else str(value).strip()

    def _dbus_energy_source_snapshot(self, source: EnergySourceDefinition, now: float) -> EnergySourceSnapshot:
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
            self.service._invalidate_auto_battery_service()
            service_name = self._resolve_energy_source_service(source)
            soc_value = self._read_optional_energy_value(service_name, source.soc_path)
            net_battery_power = self._read_optional_energy_value(service_name, source.battery_power_path)
            ac_power = self._read_optional_energy_value(service_name, source.ac_power_path)
            pv_input_power = self._read_optional_energy_value(service_name, source.pv_power_path)
            grid_interaction = self._read_optional_energy_value(service_name, source.grid_interaction_path)
            operating_mode = self._read_optional_energy_text(service_name, source.operating_mode_path)
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
            pv_input_power_w=pv_input_power,
            grid_interaction_w=grid_interaction,
            operating_mode=operating_mode,
            online=True,
            confidence=1.0,
            captured_at=now,
        )

    def _battery_snapshot_sources(self) -> tuple[EnergySourceDefinition, ...]:
        return tuple(getattr(self.service, "auto_energy_sources", ()) or (self._primary_energy_source(),))

    def _battery_snapshot_cluster(
        self,
        now: float,
    ) -> tuple[object, tuple[EnergySourceDefinition, ...], list[EnergySourceSnapshot]]:
        sources = self._battery_snapshot_sources()
        source_snapshots = [read_energy_source_snapshot(self, source, now) for source in sources]
        return aggregate_energy_sources(source_snapshots), sources, source_snapshots

    def _battery_snapshot_effective_soc(self, cluster: object) -> float | None:
        primary_soc = cluster.sources[0].soc if cluster.sources else None
        return cluster.effective_soc if bool(getattr(self.service, "auto_use_combined_battery_soc", True)) else primary_soc

    @staticmethod
    def _battery_snapshot_validate_soc(effective_soc: float | None, cluster: object) -> None:
        if effective_soc is None and not any(source.soc is not None for source in cluster.sources):
            raise TypeError("Battery SOC is not numeric")

    def _battery_snapshot_cache_owner(self) -> object:
        svc = self.service
        return getattr(svc, "_service", svc)

    def _battery_snapshot_learning_bundle(
        self,
        cache_owner: object,
        cluster: object,
        now: float,
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        cache_owner._last_energy_learning_profiles = update_energy_learning_profiles(
            getattr(cache_owner, "_last_energy_learning_profiles", {}),
            cluster.sources,
            now,
        )
        learning_summary = summarize_energy_learning_profiles(
            getattr(cache_owner, "_last_energy_learning_profiles", {})
        )
        discharge_balance = derive_discharge_balance_metrics(
            cluster.sources,
            getattr(cache_owner, "_last_energy_learning_profiles", {}),
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
        return (
            cast(dict[str, object], learning_summary),
            cast(dict[str, object], discharge_balance),
            cast(dict[str, object], forecast),
        )

    @staticmethod
    def _battery_snapshot_discharge_control(
        cluster: object,
        sources: tuple[EnergySourceDefinition, ...],
    ) -> dict[str, object]:
        return cast(
            dict[str, object],
            derive_discharge_control_metrics(cluster.sources, {source.source_id: source for source in sources}),
        )

    @staticmethod
    def _battery_snapshot_source_payloads(
        cluster: object,
        discharge_balance: dict[str, object],
        discharge_control: dict[str, object],
    ) -> list[dict[str, object]]:
        source_payloads = [source.as_dict() for source in cluster.sources]
        source_balance = cast(dict[str, dict[str, object]], discharge_balance.get("sources", {}))
        source_control = cast(dict[str, dict[str, object]], discharge_control.get("sources", {}))
        for source_payload in source_payloads:
            source_id = str(source_payload.get("source_id", ""))
            source_payload.update(source_balance.get(source_id, {}))
            source_payload.update(source_control.get(source_id, {}))
        return source_payloads

    def _battery_snapshot_payload(
        self,
        cache_owner: object,
        effective_soc: float | None,
        cluster: object,
        forecast: dict[str, object],
        discharge_balance: dict[str, object],
        discharge_control: dict[str, object],
        source_payloads: list[dict[str, object]],
    ) -> dict[str, object]:
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
                for source_id, profile in getattr(cache_owner, "_last_energy_learning_profiles", {}).items()
            },
        }

    @staticmethod
    def _empty_battery_snapshot_payload(failure: float | None) -> dict[str, object]:
        return {
            "battery_soc": cast(float | None, failure),
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

    def _successful_battery_snapshot_payload(self, now: float) -> dict[str, object]:
        cluster, sources, _ = self._battery_snapshot_cluster(now)
        effective_soc = self._battery_snapshot_effective_soc(cluster)
        self._battery_snapshot_validate_soc(effective_soc, cluster)
        cache_owner = self._battery_snapshot_cache_owner()
        learning_summary, discharge_balance, forecast = self._battery_snapshot_learning_bundle(cache_owner, cluster, now)
        discharge_control = self._battery_snapshot_discharge_control(cluster, sources)
        source_payloads = self._battery_snapshot_source_payloads(cluster, discharge_balance, discharge_control)
        self._mark_source_recovery("battery", "Battery SOC readings recovered")
        battery_payload = self._battery_snapshot_payload(
            cache_owner,
            effective_soc,
            cluster,
            forecast,
            discharge_balance,
            discharge_control,
            source_payloads,
        )
        setattr(cache_owner, "_last_energy_cluster", dict(battery_payload))
        return battery_payload

    def _failed_battery_snapshot_payload(self, now: float, error: Exception) -> dict[str, object]:
        svc = self.service
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
        return self._empty_battery_snapshot_payload(cast(float | None, failure))

    def get_battery_snapshot(self) -> dict[str, object]:
        """Return aggregated battery and inverter source data for Auto mode."""
        now = time.time()
        if not self._source_retry_ready("battery", now):
            return {"battery_soc": None}
        try:
            return self._successful_battery_snapshot_payload(now)
        except Exception as error:  # pylint: disable=broad-except
            return self._failed_battery_snapshot_payload(now, error)

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
