# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional companion DBus services for aggregated external energy visibility."""

from __future__ import annotations

import platform
import re
from typing import Any, Mapping

from vedbus import VeDbusService


class EnergyCompanionDbusBridge:
    """Publish optional aggregated battery and PV companion services on DBus."""

    def __init__(self, service: Any, script_path: str) -> None:
        self.service = service
        self._script_path = script_path
        self._battery_service: Any = None
        self._pvinverter_service: Any = None
        self._source_battery_services: dict[str, Any] = {}
        self._source_pvinverter_services: dict[str, Any] = {}
        self._published_values: dict[str, dict[str, Any]] = {}

    def start(self) -> None:
        """Create and register companion services when enabled."""
        svc = self.service
        base_device_instance = int(getattr(svc, "deviceinstance", 0))
        if not bool(getattr(svc, "companion_dbus_bridge_enabled", False)):
            return
        self._ensure_battery_service(base_device_instance)
        self._ensure_pvinverter_service(base_device_instance)

    def stop(self) -> None:
        """Release companion service references."""
        self._battery_service = None
        self._pvinverter_service = None
        self._source_battery_services = {}
        self._source_pvinverter_services = {}
        self._published_values = {}

    def publish(self, now: float | None = None) -> bool:
        """Publish the latest worker snapshot to any active companion services."""
        _ = now
        svc = self.service
        if not bool(getattr(svc, "companion_dbus_bridge_enabled", False)):
            return False
        get_snapshot = getattr(svc, "_get_worker_snapshot", None)
        snapshot = get_snapshot() if callable(get_snapshot) else {}
        normalized_snapshot = dict(snapshot) if isinstance(snapshot, Mapping) else {}
        publish_results = (
            self._publish_battery_snapshot(normalized_snapshot),
            self._publish_pvinverter_snapshot(normalized_snapshot),
            self._publish_source_snapshots(normalized_snapshot),
        )
        return any(publish_results)

    def _ensure_battery_service(self, base_device_instance: int) -> None:
        svc = self.service
        if not bool(getattr(svc, "companion_battery_service_enabled", True)) or self._battery_service is not None:
            return
        self._battery_service = self._register_service(
            getattr(
                svc,
                "companion_battery_service_name",
                f"com.victronenergy.battery.external_{base_device_instance}",
            ),
            int(getattr(svc, "companion_battery_deviceinstance", base_device_instance + 40)),
            "External Energy Battery",
            {
                "/Soc": None,
                "/Dc/0/Power": 0.0,
                "/Capacity": None,
            },
        )

    def _ensure_pvinverter_service(self, base_device_instance: int) -> None:
        svc = self.service
        if not bool(getattr(svc, "companion_pvinverter_service_enabled", True)) or self._pvinverter_service is not None:
            return
        self._pvinverter_service = self._register_service(
            getattr(
                svc,
                "companion_pvinverter_service_name",
                f"com.victronenergy.pvinverter.external_{base_device_instance}",
            ),
            int(getattr(svc, "companion_pvinverter_deviceinstance", base_device_instance + 41)),
            "External Energy PV",
            {
                "/Ac/Power": 0.0,
                "/Ac/L1/Power": 0.0,
                "/Ac/L2/Power": 0.0,
                "/Ac/L3/Power": 0.0,
            },
        )

    def _register_service(
        self,
        service_name: str,
        device_instance: int,
        product_label: str,
        specific_paths: Mapping[str, Any],
    ) -> Any:
        service = VeDbusService(service_name, register=False)
        self._register_common_paths(service, device_instance, product_label)
        for path, initial in specific_paths.items():
            service.add_path(path, initial)
        service.register()
        return service

    def _register_common_paths(self, dbus_service: Any, device_instance: int, product_label: str) -> None:
        svc = self.service
        dbus_service.add_path("/Mgmt/ProcessName", self._script_path)
        dbus_service.add_path(
            "/Mgmt/ProcessVersion",
            "Unknown version, and running on Python " + platform.python_version(),
        )
        dbus_service.add_path("/Mgmt/Connection", getattr(svc, "connection_name", "External energy companion"))
        dbus_service.add_path("/DeviceInstance", int(device_instance))
        dbus_service.add_path("/ProductId", 0xFFFF)
        dbus_service.add_path("/ProductName", product_label)
        dbus_service.add_path("/CustomName", f"{getattr(svc, 'custom_name', 'Venus EV Charger')} {product_label}")
        dbus_service.add_path("/FirmwareVersion", getattr(svc, "firmware_version", ""))
        dbus_service.add_path("/HardwareVersion", getattr(svc, "hardware_version", ""))
        dbus_service.add_path("/Serial", getattr(svc, "serial", ""))
        dbus_service.add_path("/Connected", 0)
        dbus_service.add_path("/UpdateIndex", 0)

    def _publish_service_values(
        self,
        service_key: str,
        dbus_service: Any,
        values: Mapping[str, Any],
    ) -> bool:
        previous_values = self._published_values.setdefault(service_key, {})
        changed = False
        for path, value in values.items():
            if previous_values.get(path) == value:
                continue
            dbus_service[path] = value
            previous_values[path] = value
            changed = True
        if changed:
            dbus_service["/UpdateIndex"] = int(dbus_service["/UpdateIndex"]) + 1
        return changed

    def _publish_source_snapshots(self, snapshot: Mapping[str, Any]) -> bool:
        if not bool(getattr(self.service, "companion_source_services_enabled", True)):
            return False
        source_snapshots = self._normalized_source_snapshots(snapshot)
        changed = False
        for index, source in enumerate(source_snapshots):
            changed = self._publish_one_source_snapshot(source, index) or changed
        return changed

    def _publish_one_source_snapshot(self, source: Mapping[str, Any], index: int) -> bool:
        publish_results = (
            self._publish_battery_source_service(source, index),
            self._publish_pvinverter_source_service(source, index),
        )
        return any(publish_results)

    def _publish_battery_source_service(self, source: Mapping[str, Any], index: int) -> bool:
        if not self._source_supports_battery_service(source):
            return False
        battery_service = self._ensure_source_battery_service(source, index)
        return self._publish_service_values(
            f"source-battery:{source['source_id']}",
            battery_service,
            self._battery_source_values(source),
        )

    def _publish_pvinverter_source_service(self, source: Mapping[str, Any], index: int) -> bool:
        if not self._source_supports_pvinverter_service(source):
            return False
        pvinverter_service = self._ensure_source_pvinverter_service(source, index)
        return self._publish_service_values(
            f"source-pvinverter:{source['source_id']}",
            pvinverter_service,
            self._pvinverter_source_values(source),
        )

    def _publish_battery_snapshot(self, snapshot: Mapping[str, Any]) -> bool:
        if self._battery_service is None:
            return False
        return self._publish_service_values(
            "battery",
            self._battery_service,
            {
                "/Connected": self._battery_connected(snapshot),
                "/Soc": snapshot.get("battery_combined_soc"),
                "/Dc/0/Power": snapshot.get("battery_combined_net_power_w", 0.0),
                "/Capacity": snapshot.get("battery_combined_usable_capacity_wh"),
            },
        )

    def _publish_pvinverter_snapshot(self, snapshot: Mapping[str, Any]) -> bool:
        if self._pvinverter_service is None:
            return False
        pv_power = self._pvinverter_power_w(snapshot)
        return self._publish_service_values(
            "pvinverter",
            self._pvinverter_service,
            {
                "/Connected": self._pvinverter_connected(snapshot),
                "/Ac/Power": pv_power,
                "/Ac/L1/Power": pv_power,
                "/Ac/L2/Power": 0.0,
                "/Ac/L3/Power": 0.0,
            },
        )

    @staticmethod
    def _battery_connected(snapshot: Mapping[str, Any]) -> int:
        source_count = int(snapshot.get("battery_source_count", 0) or 0)
        online_count = int(snapshot.get("battery_online_source_count", 0) or 0)
        return 1 if source_count > 0 and online_count > 0 else 0

    @staticmethod
    def _pvinverter_connected(snapshot: Mapping[str, Any]) -> int:
        pv_power = snapshot.get("battery_combined_pv_input_power_w")
        ac_power = snapshot.get("battery_combined_ac_power_w")
        if isinstance(pv_power, (int, float)) and float(pv_power) > 0.0:
            return 1
        if isinstance(ac_power, (int, float)) and float(ac_power) > 0.0:
            return 1
        return EnergyCompanionDbusBridge._battery_connected(snapshot)

    @staticmethod
    def _pvinverter_power_w(snapshot: Mapping[str, Any]) -> float:
        pv_power = snapshot.get("battery_combined_pv_input_power_w")
        if isinstance(pv_power, (int, float)):
            return max(0.0, float(pv_power))
        ac_power = snapshot.get("battery_combined_ac_power_w")
        if isinstance(ac_power, (int, float)):
            return max(0.0, float(ac_power))
        return 0.0

    def _ensure_source_battery_service(self, source: Mapping[str, Any], index: int) -> Any:
        source_id = str(source.get("source_id", "")).strip()
        existing = self._source_battery_services.get(source_id)
        if existing is not None:
            return existing
        device_instance = self._source_device_instance("battery", index)
        service = self._register_service(
            self._source_service_name("battery", source_id, device_instance),
            device_instance,
            self._source_product_label(source, "Battery"),
            {
                "/Soc": None,
                "/Dc/0/Power": 0.0,
                "/Capacity": None,
            },
        )
        self._source_battery_services[source_id] = service
        return service

    def _ensure_source_pvinverter_service(self, source: Mapping[str, Any], index: int) -> Any:
        source_id = str(source.get("source_id", "")).strip()
        existing = self._source_pvinverter_services.get(source_id)
        if existing is not None:
            return existing
        device_instance = self._source_device_instance("pvinverter", index)
        service = self._register_service(
            self._source_service_name("pvinverter", source_id, device_instance),
            device_instance,
            self._source_product_label(source, "PV"),
            {
                "/Ac/Power": 0.0,
                "/Ac/L1/Power": 0.0,
                "/Ac/L2/Power": 0.0,
                "/Ac/L3/Power": 0.0,
            },
        )
        self._source_pvinverter_services[source_id] = service
        return service

    def _source_device_instance(self, service_kind: str, index: int) -> int:
        svc = self.service
        battery_base = int(getattr(svc, "companion_source_battery_deviceinstance_base", 0))
        pvinverter_base = int(getattr(svc, "companion_source_pvinverter_deviceinstance_base", 0))
        if service_kind == "battery":
            return battery_base + int(index)
        return pvinverter_base + int(index)

    def _source_service_name(self, service_kind: str, source_id: str, device_instance: int) -> str:
        svc = self.service
        sanitized_source_id = self._sanitize_source_id(source_id or str(device_instance))
        if service_kind == "battery":
            configured_prefix = str(
                getattr(svc, "companion_source_battery_service_prefix", "com.victronenergy.battery.external")
            ).strip()
        else:
            configured_prefix = str(
                getattr(
                    svc,
                    "companion_source_pvinverter_service_prefix",
                    "com.victronenergy.pvinverter.external",
                )
            ).strip()
        prefix = configured_prefix.rstrip(".") or (
            "com.victronenergy.battery.external"
            if service_kind == "battery"
            else "com.victronenergy.pvinverter.external"
        )
        return f"{prefix}.{sanitized_source_id}"

    @staticmethod
    def _source_product_label(source: Mapping[str, Any], suffix: str) -> str:
        source_id = str(source.get("source_id", "")).strip() or "source"
        return f"External Energy {source_id} {suffix}"

    @staticmethod
    def _sanitize_source_id(source_id: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9]+", "_", str(source_id).strip()).strip("_").lower()
        return normalized or "source"

    @staticmethod
    def _normalized_source_snapshots(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
        sources = snapshot.get("battery_sources")
        if not isinstance(sources, list):
            return ()
        normalized_sources: list[dict[str, Any]] = []
        for item in sources:
            if not isinstance(item, Mapping):
                continue
            normalized = dict(item)
            if not str(normalized.get("source_id", "")).strip():
                continue
            normalized_sources.append(normalized)
        return tuple(normalized_sources)

    @staticmethod
    def _source_supports_battery_service(source: Mapping[str, Any]) -> bool:
        return str(source.get("role", "")).strip() in {"battery", "hybrid-inverter"}

    @staticmethod
    def _source_supports_pvinverter_service(source: Mapping[str, Any]) -> bool:
        return str(source.get("role", "")).strip() in {"hybrid-inverter", "inverter"}

    @staticmethod
    def _battery_source_values(source: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "/Connected": 1 if bool(source.get("online", False)) else 0,
            "/Soc": source.get("soc"),
            "/Dc/0/Power": source.get("net_battery_power_w", 0.0),
            "/Capacity": source.get("usable_capacity_wh"),
        }

    @staticmethod
    def _pvinverter_source_values(source: Mapping[str, Any]) -> dict[str, Any]:
        pv_power = EnergyCompanionDbusBridge._source_pvinverter_power_w(source)
        return {
            "/Connected": 1 if bool(source.get("online", False)) else 0,
            "/Ac/Power": pv_power,
            "/Ac/L1/Power": pv_power,
            "/Ac/L2/Power": 0.0,
            "/Ac/L3/Power": 0.0,
        }

    @staticmethod
    def _source_pvinverter_power_w(source: Mapping[str, Any]) -> float:
        pv_power = source.get("pv_input_power_w")
        if isinstance(pv_power, (int, float)):
            return max(0.0, float(pv_power))
        ac_power = source.get("ac_power_w")
        if isinstance(ac_power, (int, float)):
            return max(0.0, float(ac_power))
        return 0.0
