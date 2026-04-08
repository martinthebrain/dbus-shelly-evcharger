#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Collect PV, battery, and grid inputs for the Shelly wallbox in a helper process.

The helper exists so DBus discovery and polling cannot stall the main wallbox
service. It periodically writes a compact JSON snapshot that the main process
can consume safely, even if DBus becomes slow or temporarily inconsistent.
"""

import configparser
import json
import logging
import os
import signal
import sys
import time
import xml.etree.ElementTree as xml_et
from functools import partial

import dbus
from dbus_shelly_wallbox_shared import (
    AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION,
    compact_json,
    configured_grid_paths,
    coerce_dbus_numeric,
    discovery_cache_valid,
    first_matching_prefixed_service,
    grid_values_complete_enough,
    prefixed_service_names,
    should_assume_zero_pv,
    sum_dbus_numeric,
    write_text_atomically,
)
from gi.repository import GLib



class _AutoInputHelperSubscriptionMixin:
    @staticmethod
    def _signal_spec_key(source_name, service_name, path):
        """Return a stable key for one subscribed DBus path."""
        return (str(source_name), str(service_name), str(path))

    def _subscribe_busitem_path(self, source_name, service_name, path):
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

    def _clear_missing_subscriptions(self, desired_keys):
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

    def _desired_pv_subscription_specs(self):
        """Return the current AC/DC PV paths that should be monitored."""
        try:
            pv_services = [self.auto_pv_service] if self.auto_pv_service else self._resolve_auto_pv_services()
        except Exception:  # pylint: disable=broad-except
            pv_services = []
        desired = [("pv", service_name, self.auto_pv_path) for service_name in pv_services]
        if self.auto_use_dc_pv and self.auto_dc_pv_service and self.auto_dc_pv_path:
            desired.append(("pv", self.auto_dc_pv_service, self.auto_dc_pv_path))
        return desired

    def _desired_battery_subscription_specs(self):
        """Return the battery SOC path that should be monitored."""
        try:
            battery_service = self._resolve_auto_battery_service()
        except Exception:  # pylint: disable=broad-except
            battery_service = None
        if not battery_service:
            return []
        return [("battery", battery_service, self.auto_battery_soc_path)]

    def _desired_grid_subscription_specs(self):
        """Return the configured grid power paths that should be monitored."""
        if not self.auto_grid_service:
            return []
        return [
            ("grid", self.auto_grid_service, path)
            for path in (self.auto_grid_l1_path, self.auto_grid_l2_path, self.auto_grid_l3_path)
            if path
        ]

    def _desired_subscription_specs(self):
        """Return the currently desired DBus paths to monitor."""
        return (
            self._desired_pv_subscription_specs()
            + self._desired_battery_subscription_specs()
            + self._desired_grid_subscription_specs()
        )

    def _refresh_subscriptions(self):
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

    def _schedule_refresh_subscriptions(self):
        """Schedule one deferred subscription rebuild."""
        self._ensure_poll_state()
        if self._refresh_scheduled:
            return
        self._refresh_scheduled = True

        def _run():
            self._refresh_scheduled = False
            return self._refresh_subscriptions()

        GLib.idle_add(_run)

    def _on_source_signal(self, source_name, *args, **kwargs):
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

    def _on_name_owner_changed(self, name, _old_owner, _new_owner):
        """Rebuild subscriptions when relevant DBus services appear or disappear."""
        relevant = (
            name == self.auto_grid_service
            or name == self.auto_dc_pv_service
            or (self.auto_pv_service and name == self.auto_pv_service)
            or (self.auto_battery_service and name == self.auto_battery_service)
            or name.startswith(self.auto_pv_service_prefix)
            or name.startswith(self.auto_battery_service_prefix)
        )
        if relevant:
            self._schedule_refresh_subscriptions()

    def _refresh_subscriptions_timer(self):
        """Slow periodic refresh in case a DBus topology change signal was missed."""
        self._schedule_refresh_subscriptions()
        return not self._stop_requested

    def _parent_watchdog(self):
        """Stop the helper once the parent process disappears."""
        if self._stop_requested or self._parent_alive():
            return not self._stop_requested
        if self._main_loop is not None:
            self._main_loop.quit()
        return False

    def _reset_system_bus(self):
        """Drop the cached DBus connection."""
        self._system_bus = None

    def _get_system_bus(self):
        """Return the current DBus connection for this helper process."""
        if self._system_bus is None:
            self._system_bus = dbus.SystemBus(private=True)
        return self._system_bus
