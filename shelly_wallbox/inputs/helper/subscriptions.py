#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Collect PV, battery, and grid inputs for the Shelly wallbox in a helper process.

The helper exists so DBus discovery and polling cannot stall the main wallbox
service. It periodically writes a compact JSON snapshot that the main process
can consume safely, even if DBus becomes slow or temporarily inconsistent.
"""

from functools import partial
from typing import Any, cast

import dbus
from gi.repository import GLib



class _AutoInputHelperSubscriptionMixin:
    @staticmethod
    def _signal_spec_key(source_name: str, service_name: str, path: str) -> tuple[str, str, str]:
        """Return a stable key for one subscribed DBus path."""
        return (str(source_name), str(service_name), str(path))

    def _subscribe_busitem_path(self: Any, source_name: str, service_name: str, path: str) -> None:
        """Subscribe to BusItem changes for one source path."""
        self._ensure_poll_state()
        key = self._signal_spec_key(source_name, service_name, path)
        if key in self._signal_matches:
            return
        match = self._get_system_bus().add_signal_receiver(
            partial(self._on_source_signal, source_name),
            signal_name="PropertiesChanged",
            dbus_interface="com.victronenergy.BusItem",
            bus_name=service_name,
            path=path,
            sender_keyword="sender",
            path_keyword="path",
        )
        self._signal_matches[key] = match
        self._monitored_specs[key] = {
            "source": source_name,
            "service_name": service_name,
            "path": path,
        }

    def _clear_missing_subscriptions(self: Any, desired_keys: set[tuple[str, str, str]]) -> None:
        """Remove subscriptions that are no longer needed."""
        self._ensure_poll_state()
        for key in list(self._signal_matches):
            if key in desired_keys:
                continue
            match = self._signal_matches.pop(key, None)
            if match is not None:
                try:
                    match.remove()
                except Exception:  # pylint: disable=broad-except
                    pass
            self._monitored_specs.pop(key, None)

    def _desired_pv_subscription_specs(self: Any) -> list[tuple[str, str, str]]:
        """Return the current AC/DC PV paths that should be monitored."""
        pv_services = self._resolved_pv_subscription_services()
        desired = [("pv", service_name, self.auto_pv_path) for service_name in pv_services]
        dc_spec = self._dc_pv_subscription_spec()
        if dc_spec is not None:
            desired.append(dc_spec)
        return desired

    def _resolved_pv_subscription_services(self: Any) -> list[str]:
        """Return AC PV services that should currently be monitored."""
        try:
            return [self.auto_pv_service] if self.auto_pv_service else self._resolve_auto_pv_services()
        except Exception:  # pylint: disable=broad-except
            return []

    def _dc_pv_subscription_spec(self: Any) -> tuple[str, str, str] | None:
        """Return the optional DC PV subscription tuple when configured."""
        if self.auto_use_dc_pv and self.auto_dc_pv_service and self.auto_dc_pv_path:
            return ("pv", self.auto_dc_pv_service, self.auto_dc_pv_path)
        return None

    def _desired_battery_subscription_specs(self: Any) -> list[tuple[str, str, str]]:
        """Return the battery SOC path that should be monitored."""
        try:
            battery_service = self._resolve_auto_battery_service()
        except Exception:  # pylint: disable=broad-except
            battery_service = None
        if not battery_service:
            return []
        return [("battery", battery_service, self.auto_battery_soc_path)]

    def _desired_grid_subscription_specs(self: Any) -> list[tuple[str, str, str]]:
        """Return the configured grid power paths that should be monitored."""
        if not self.auto_grid_service:
            return []
        return [
            ("grid", self.auto_grid_service, path)
            for path in (self.auto_grid_l1_path, self.auto_grid_l2_path, self.auto_grid_l3_path)
            if path
        ]

    def _desired_subscription_specs(self: Any) -> list[tuple[str, str, str]]:
        """Return the currently desired DBus paths to monitor."""
        pv_specs: list[tuple[str, str, str]] = self._desired_pv_subscription_specs()
        battery_specs: list[tuple[str, str, str]] = self._desired_battery_subscription_specs()
        grid_specs: list[tuple[str, str, str]] = self._desired_grid_subscription_specs()
        return pv_specs + battery_specs + grid_specs

    def _refresh_subscriptions(self: Any) -> bool:
        """Rebuild path subscriptions after startup or a DBus service topology change."""
        self._ensure_poll_state()
        desired_specs = self._desired_subscription_specs()
        desired_keys = set()
        for source_name, service_name, path in desired_specs:
            key = self._signal_spec_key(source_name, service_name, path)
            desired_keys.add(key)
            self._subscribe_busitem_path(source_name, service_name, path)
        self._clear_missing_subscriptions(desired_keys)
        self._refresh_all_sources()
        return False

    def _schedule_refresh_subscriptions(self: Any) -> None:
        """Schedule one deferred subscription rebuild."""
        self._ensure_poll_state()
        if self._refresh_scheduled:
            return
        self._refresh_scheduled = True

        def _run() -> bool:
            self._refresh_scheduled = False
            refreshed: bool = self._refresh_subscriptions()
            return refreshed

        GLib.idle_add(_run)

    def _on_source_signal(self: Any, source_name: str, *args: object, **kwargs: object) -> None:
        """Refresh one source when its DBus path emits a change signal."""
        del args, kwargs
        try:
            self._refresh_source(source_name)
        except Exception as error:  # pylint: disable=broad-except
            self._warning_throttled(
                f"auto-helper-source-signal-{source_name}",
                max(5.0, self.auto_dbus_backoff_base_seconds or 5.0),
                "Auto input helper failed to refresh %s after signal: %s",
                source_name,
                error,
            )

    def _on_name_owner_changed(self: Any, name: str, _old_owner: str, _new_owner: str) -> None:
        """Rebuild subscriptions when relevant DBus services appear or disappear."""
        if self._is_relevant_name_owner_change(name):
            self._schedule_refresh_subscriptions()

    def _is_relevant_name_owner_change(self: Any, name: str) -> bool:
        """Return whether a DBus owner-change affects monitored Auto-input services."""
        return bool(
            name == self.auto_grid_service
            or name == self.auto_dc_pv_service
            or self._matches_explicit_service_name(name)
            or self._matches_discovery_prefix(name)
        )

    def _matches_explicit_service_name(self: Any, name: str) -> bool:
        """Return whether one owner change matches explicit AC PV or battery services."""
        return bool(
            (self.auto_pv_service and name == self.auto_pv_service)
            or (self.auto_battery_service and name == self.auto_battery_service)
        )

    def _matches_discovery_prefix(self: Any, name: str) -> bool:
        """Return whether one owner change matches auto-discovered PV/battery prefixes."""
        return bool(
            name.startswith(self.auto_pv_service_prefix)
            or name.startswith(self.auto_battery_service_prefix)
        )

    def _refresh_subscriptions_timer(self: Any) -> bool:
        """Slow periodic refresh in case a DBus topology change signal was missed."""
        self._schedule_refresh_subscriptions()
        return not self._stop_requested

    def _parent_watchdog(self: Any) -> bool:
        """Stop the helper once the parent process disappears."""
        if self._stop_requested or self._parent_alive():
            return not self._stop_requested
        if self._main_loop is not None:
            self._main_loop.quit()
        return False

    def _reset_system_bus(self: Any) -> None:
        """Drop the cached DBus connection."""
        self._system_bus = None

    def _get_system_bus(self: Any) -> Any:
        """Return the current DBus connection for this helper process."""
        if self._system_bus is None:
            dbus_module = cast(Any, dbus)
            self._system_bus = dbus_module.SystemBus(private=True)
        return self._system_bus
