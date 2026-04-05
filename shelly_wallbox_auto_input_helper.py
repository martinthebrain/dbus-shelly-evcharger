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

try:
    import dbus.mainloop.glib as dbus_glib_mainloop
except Exception:  # pylint: disable=broad-except
    dbus_glib_mainloop = None


def _as_bool(value, default=False):
    """Parse a config-style truthy value."""
    if value is None:
        return bool(default)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


class AutoInputHelper:
    """Standalone DBus reader process for Auto mode inputs."""

    def __init__(self, config_path, snapshot_path=None, parent_pid=None):
        parser = configparser.ConfigParser()
        loaded = parser.read(config_path)
        if not loaded or "DEFAULT" not in parser:
            raise ValueError(f"Unable to read config file: {config_path}")

        self.config_path = config_path
        self.config = parser["DEFAULT"]
        self.parent_pid = int(parent_pid) if parent_pid is not None else None
        # The helper uses a shared base poll interval but allows slower battery
        # polling to reduce unnecessary DBus traffic on systems where SOC values
        # change much less frequently than PV or grid power.
        auto_input_poll_interval_ms = float(
            self.config.get(
                "AutoInputPollIntervalMs",
                self.config.get("PollIntervalMs", 1000),
            )
        )
        self.auto_pv_poll_interval_seconds = max(
            0.2,
            float(self.config.get("AutoPvPollIntervalMs", auto_input_poll_interval_ms)) / 1000.0,
        )
        self.auto_grid_poll_interval_seconds = max(
            0.2,
            float(self.config.get("AutoGridPollIntervalMs", auto_input_poll_interval_ms)) / 1000.0,
        )
        self.auto_battery_poll_interval_seconds = max(
            0.2,
            float(self.config.get("AutoBatteryPollIntervalMs", auto_input_poll_interval_ms)) / 1000.0,
        )
        self.poll_interval_seconds = min(
            max(0.2, auto_input_poll_interval_ms / 1000.0),
            self.auto_pv_poll_interval_seconds,
            self.auto_grid_poll_interval_seconds,
            self.auto_battery_poll_interval_seconds,
        )
        # The main service watches this snapshot file and can restart the helper
        # if the file stops being refreshed.
        self.snapshot_path = (
            snapshot_path
            or self.config.get(
                "AutoInputSnapshotPath",
                "/run/dbus-shelly-wallbox-auto.json",
            ).strip()
        )
        self.dbus_method_timeout_seconds = float(self.config.get("DbusMethodTimeoutSeconds", 1.0))
        self.auto_pv_service = self.config.get("AutoPvService", "").strip()
        self.auto_pv_service_prefix = self.config.get("AutoPvServicePrefix", "com.victronenergy.pvinverter").strip()
        self.auto_pv_path = self.config.get("AutoPvPath", "/Ac/Power").strip()
        self.auto_pv_max_services = max(1, int(self.config.get("AutoPvMaxServices", 10)))
        self.auto_pv_scan_interval_seconds = max(0.0, float(self.config.get("AutoPvScanIntervalSeconds", 60)))
        self.auto_use_dc_pv = _as_bool(self.config.get("AutoUseDcPv", "1"), True)
        self.auto_dc_pv_service = self.config.get("AutoDcPvService", "com.victronenergy.system").strip()
        self.auto_dc_pv_path = self.config.get("AutoDcPvPath", "/Dc/Pv/Power").strip()
        self.auto_battery_service = self.config.get(
            "AutoBatteryService",
            "com.victronenergy.battery.socketcan_can1",
        ).strip()
        self.auto_battery_soc_path = self.config.get("AutoBatterySocPath", "/Soc").strip()
        self.auto_battery_service_prefix = self.config.get(
            "AutoBatteryServicePrefix",
            "com.victronenergy.battery",
        ).strip()
        self.auto_battery_scan_interval_seconds = max(
            0.0,
            float(self.config.get("AutoBatteryScanIntervalSeconds", 60)),
        )
        self.auto_grid_service = self.config.get("AutoGridService", "com.victronenergy.system").strip()
        self.auto_grid_l1_path = self.config.get("AutoGridL1Path", "/Ac/Grid/L1/Power").strip()
        self.auto_grid_l2_path = self.config.get("AutoGridL2Path", "/Ac/Grid/L2/Power").strip()
        self.auto_grid_l3_path = self.config.get("AutoGridL3Path", "/Ac/Grid/L3/Power").strip()
        self.auto_grid_require_all_phases = _as_bool(
            self.config.get("AutoGridRequireAllPhases", "1"),
            True,
        )
        self.auto_dbus_backoff_base_seconds = max(
            0.0,
            float(self.config.get("AutoDbusBackoffBaseSeconds", 5)),
        )
        self.auto_dbus_backoff_max_seconds = max(
            0.0,
            float(self.config.get("AutoDbusBackoffMaxSeconds", 60)),
        )
        self.validation_poll_seconds = max(
            5.0,
            float(self.config.get("AutoInputValidationPollSeconds", 30)),
        )
        self.subscription_refresh_seconds = self._derive_subscription_refresh_seconds()
        self._system_bus = None
        self._dbus_list_backoff_until = 0.0
        self._dbus_list_failures = 0
        self._resolved_auto_pv_services = []
        self._auto_pv_last_scan = 0.0
        self._resolved_auto_battery_service = None
        self._auto_battery_last_scan = 0.0
        self._source_retry_after = {}
        self._warning_state = {}
        self._last_payload = None
        self._last_snapshot_state = self._empty_snapshot()
        self._next_source_poll_at = {
            "pv": 0.0,
            "battery": 0.0,
            "grid": 0.0,
        }
        self._signal_matches = {}
        self._monitored_specs = {}
        self._refresh_scheduled = False
        self._main_loop = None
        self._stop_requested = False

    def _handle_signal(self, signum, _frame):
        """Stop the helper cleanly when asked."""
        logging.info("Auto input helper received signal %s", signum)
        self._stop_requested = True
        if self._main_loop is not None:
            GLib.idle_add(self._main_loop.quit)

    def _derive_subscription_refresh_seconds(self):
        """Return a slow service refresh interval for DBus subscription bookkeeping."""
        candidates = [60.0]
        for value in (
            float(self.config.get("AutoPvScanIntervalSeconds", 60)),
            float(self.config.get("AutoBatteryScanIntervalSeconds", 60)),
        ):
            if value > 0:
                candidates.append(value)
        return max(5.0, min(candidates))

    def _parent_alive(self):
        """Return False when the parent process is gone."""
        if self.parent_pid is None:
            return True
        try:
            return os.getppid() == self.parent_pid
        except Exception:  # pylint: disable=broad-except
            return False

    def _warning_throttled(self, key, interval_seconds, message, *args):
        """Log a warning only once per interval for a given issue."""
        now = time.time()
        last_logged = self._warning_state.get(key)
        if last_logged is None or (now - last_logged) > interval_seconds:
            logging.warning(message, *args)
            self._warning_state[key] = now

    @staticmethod
    def _empty_snapshot(captured_at=None):
        """Return an empty helper snapshot payload."""
        return {
            "captured_at": captured_at,
            "heartbeat_at": captured_at,
            "pv_captured_at": None,
            "pv_power": None,
            "battery_captured_at": None,
            "battery_soc": None,
            "grid_captured_at": None,
            "grid_power": None,
        }

    def _ensure_poll_state(self):
        """Initialize runtime state for tests or partially constructed instances."""
        if not hasattr(self, "auto_pv_poll_interval_seconds"):
            self.auto_pv_poll_interval_seconds = max(0.2, getattr(self, "poll_interval_seconds", 1.0))
        if not hasattr(self, "auto_grid_poll_interval_seconds"):
            self.auto_grid_poll_interval_seconds = max(0.2, getattr(self, "poll_interval_seconds", 1.0))
        if not hasattr(self, "auto_battery_poll_interval_seconds"):
            self.auto_battery_poll_interval_seconds = max(0.2, getattr(self, "poll_interval_seconds", 1.0))
        if not hasattr(self, "poll_interval_seconds"):
            self.poll_interval_seconds = min(
                self.auto_pv_poll_interval_seconds,
                self.auto_grid_poll_interval_seconds,
                self.auto_battery_poll_interval_seconds,
            )
        if not hasattr(self, "_last_snapshot_state"):
            self._last_snapshot_state = self._empty_snapshot()
        if not hasattr(self, "_next_source_poll_at"):
            self._next_source_poll_at = {
                "pv": 0.0,
                "battery": 0.0,
                "grid": 0.0,
            }
        if not hasattr(self, "_signal_matches"):
            self._signal_matches = {}
        if not hasattr(self, "_monitored_specs"):
            self._monitored_specs = {}
        if not hasattr(self, "_refresh_scheduled"):
            self._refresh_scheduled = False
        if not hasattr(self, "subscription_refresh_seconds"):
            self.subscription_refresh_seconds = 60.0
        if not hasattr(self, "validation_poll_seconds"):
            self.validation_poll_seconds = 30.0
        if not hasattr(self, "_main_loop"):
            self._main_loop = None
        if not hasattr(self, "_stop_requested"):
            self._stop_requested = False

    def _collect_snapshot(self, now=None):
        """Collect only the due Auto inputs and keep the last snapshot state for the others."""
        self._ensure_poll_state()
        current = time.time() if now is None else float(now)
        snapshot = dict(self._last_snapshot_state)

        source_specs = (
            ("pv", self.auto_pv_poll_interval_seconds, self._get_pv_power, "pv_power", "pv_captured_at"),
            ("battery", self.auto_battery_poll_interval_seconds, self._get_battery_soc, "battery_soc", "battery_captured_at"),
            ("grid", self.auto_grid_poll_interval_seconds, self._get_grid_power, "grid_power", "grid_captured_at"),
        )

        for source_name, interval_seconds, getter, value_key, captured_key in source_specs:
            if current < float(self._next_source_poll_at.get(source_name, 0.0)):
                continue
            value = getter()
            if value is None:
                snapshot[value_key] = None
                snapshot[captured_key] = None
            else:
                snapshot[value_key] = value
                snapshot[captured_key] = current
            self._next_source_poll_at[source_name] = current + float(interval_seconds)

        snapshot["captured_at"] = current
        snapshot["heartbeat_at"] = current
        self._last_snapshot_state = dict(snapshot)
        return snapshot

    def _set_source_value(self, source_name, value, now=None):
        """Update one source in the snapshot and write it to RAM."""
        self._ensure_poll_state()
        current = time.time() if now is None else float(now)
        snapshot = dict(self._last_snapshot_state)
        if source_name == "pv":
            snapshot["pv_power"] = value
            snapshot["pv_captured_at"] = None if value is None else current
        elif source_name == "battery":
            snapshot["battery_soc"] = value
            snapshot["battery_captured_at"] = None if value is None else current
        elif source_name == "grid":
            snapshot["grid_power"] = value
            snapshot["grid_captured_at"] = None if value is None else current
        else:
            return
        snapshot["captured_at"] = current
        snapshot["heartbeat_at"] = current
        self._last_snapshot_state = snapshot
        self._write_snapshot(snapshot)

    def _heartbeat_snapshot(self):
        """Keep the RAM snapshot fresh without re-reading DBus values."""
        self._ensure_poll_state()
        current = time.time()
        snapshot = dict(self._last_snapshot_state)
        snapshot["heartbeat_at"] = current
        self._last_snapshot_state = snapshot
        self._write_snapshot(snapshot)
        return not self._stop_requested

    def _refresh_source(self, source_name, now=None):
        """Refresh exactly one source on startup or when its DBus signal fires."""
        current = time.time() if now is None else float(now)
        if source_name == "pv":
            value = self._get_pv_power()
        elif source_name == "battery":
            value = self._get_battery_soc()
        elif source_name == "grid":
            value = self._get_grid_power()
        else:
            return
        self._set_source_value(source_name, value, current)

    def _refresh_all_sources(self, now=None):
        """Refresh all Auto inputs once, for startup or service topology changes."""
        current = time.time() if now is None else float(now)
        for source_name in ("pv", "battery", "grid"):
            self._refresh_source(source_name, current)

    def _validation_poll(self):
        """Fallback poll to recover from silent subscription failures."""
        self._refresh_all_sources()
        return not self._stop_requested

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

    def _desired_subscription_specs(self):
        """Return the currently desired DBus paths to monitor."""
        desired = []
        try:
            if self.auto_pv_service:
                pv_services = [self.auto_pv_service]
            else:
                pv_services = self._resolve_auto_pv_services()
        except Exception:  # pylint: disable=broad-except
            pv_services = []
        for service_name in pv_services:
            desired.append(("pv", service_name, self.auto_pv_path))
        if self.auto_use_dc_pv and self.auto_dc_pv_service and self.auto_dc_pv_path:
            desired.append(("pv", self.auto_dc_pv_service, self.auto_dc_pv_path))

        try:
            battery_service = self._resolve_auto_battery_service()
        except Exception:  # pylint: disable=broad-except
            battery_service = None
        if battery_service:
            desired.append(("battery", battery_service, self.auto_battery_soc_path))

        if self.auto_grid_service:
            for path in (self.auto_grid_l1_path, self.auto_grid_l2_path, self.auto_grid_l3_path):
                if path:
                    desired.append(("grid", self.auto_grid_service, path))
        return desired

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

    def _get_dbus_value(self, service_name, path):
        """Read one DBus value with a small retry on reconnect."""
        last_error = None
        for attempt in range(2):
            try:
                obj = self._get_system_bus().get_object(service_name, path)
                interface = dbus.Interface(obj, "com.victronenergy.BusItem")
                value = interface.GetValue(timeout=self.dbus_method_timeout_seconds)
                return coerce_dbus_numeric(value)
            except Exception as error:  # pylint: disable=broad-except
                last_error = error
                self._reset_system_bus()
                if attempt == 0:
                    logging.debug("DBus read retry for %s %s after error: %s", service_name, path, error)
        raise last_error

    def _get_dbus_child_nodes(self, service_name, path):
        """Return child nodes below a DBus path via introspection."""
        last_error = None
        for attempt in range(2):
            try:
                obj = self._get_system_bus().get_object(service_name, path)
                interface = dbus.Interface(obj, "org.freedesktop.DBus.Introspectable")
                xml_data = interface.Introspect(timeout=self.dbus_method_timeout_seconds)
                root = xml_et.fromstring(str(xml_data))
                return [node.attrib.get("name") for node in root.findall("node") if node.attrib.get("name")]
            except Exception as error:  # pylint: disable=broad-except
                last_error = error
                self._reset_system_bus()
                if attempt == 0:
                    logging.debug("DBus introspection retry for %s %s after error: %s", service_name, path, error)
        raise last_error

    def _list_dbus_services(self):
        """Return all DBus service names with a small backoff on failure."""
        now = time.time()
        if now < self._dbus_list_backoff_until:
            return []
        try:
            dbus_proxy = self._get_system_bus().get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
            dbus_iface = dbus.Interface(dbus_proxy, "org.freedesktop.DBus")
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

    def _source_retry_ready(self, key):
        """Return True when a source may be queried again."""
        return time.time() >= float(self._source_retry_after.get(key, 0.0))

    def _delay_source_retry(self, key):
        """Delay retries briefly after a failing source read."""
        delay = max(1.0, self.auto_dbus_backoff_base_seconds or 5.0)
        self._source_retry_after[key] = time.time() + delay

    def _invalidate_auto_pv_services(self):
        """Force the next PV lookup to re-scan DBus services."""
        self._resolved_auto_pv_services = []
        self._auto_pv_last_scan = 0.0

    def _resolve_auto_pv_services(self):
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

    def _get_pv_power(self):
        """Read total PV power from auto-discovered AC PV plus optional DC PV."""
        if not self._source_retry_ready("pv"):
            return None
        total = 0.0
        seen_value = False
        no_auto_ac_services_found = False
        try:
            service_names = self._resolve_auto_pv_services()
            if not self.auto_pv_service and not service_names:
                no_auto_ac_services_found = True
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Auto helper AC PV service resolution failed: %s", error)
            service_names = []

        for service_name in service_names:
            try:
                value = self._get_dbus_value(service_name, self.auto_pv_path)
            except Exception as error:  # pylint: disable=broad-except
                logging.debug("Auto helper PV read failed for %s %s: %s", service_name, self.auto_pv_path, error)
                self._invalidate_auto_pv_services()
                continue
            if value is not None:
                numeric_value = sum_dbus_numeric(value)
                if numeric_value is not None:
                    total += numeric_value
                    seen_value = True

        if self.auto_use_dc_pv:
            try:
                dc_value = self._get_dbus_value(self.auto_dc_pv_service, self.auto_dc_pv_path)
            except Exception as error:  # pylint: disable=broad-except
                logging.debug(
                    "Auto helper DC PV read failed for %s %s: %s",
                    self.auto_dc_pv_service,
                    self.auto_dc_pv_path,
                    error,
                )
                dc_value = None
            numeric_dc_value = sum_dbus_numeric(dc_value)
            if numeric_dc_value is not None:
                total += numeric_dc_value
                seen_value = True

        if seen_value:
            return total
        if should_assume_zero_pv(
            self.auto_pv_service,
            service_names,
            no_auto_ac_services_found,
            self.auto_use_dc_pv,
            dc_value if self.auto_use_dc_pv else None,
        ):
            return 0.0
        self._delay_source_retry("pv")
        return None

    def _invalidate_auto_battery_service(self):
        """Force the next battery lookup to re-scan DBus services."""
        self._resolved_auto_battery_service = None
        self._auto_battery_last_scan = 0.0

    def _battery_service_has_soc(self, service_name):
        """Return whether the candidate battery service currently exposes SOC."""
        try:
            return self._get_dbus_value(service_name, self.auto_battery_soc_path) is not None
        except Exception:
            return False

    def _resolve_auto_battery_service(self):
        """Resolve battery service from config or DBus discovery."""
        if self.auto_battery_service:
            try:
                if self._get_dbus_value(self.auto_battery_service, self.auto_battery_soc_path) is not None:
                    self._resolved_auto_battery_service = self.auto_battery_service
                    self._auto_battery_last_scan = time.time()
                    return self._resolved_auto_battery_service
            except Exception:
                pass
        now = time.time()
        if discovery_cache_valid(
            self._resolved_auto_battery_service,
            self._auto_battery_last_scan,
            self.auto_battery_scan_interval_seconds,
            now,
        ):
            return self._resolved_auto_battery_service
        service_name = first_matching_prefixed_service(
            self._list_dbus_services(),
            self.auto_battery_service_prefix,
            self._battery_service_has_soc,
        )
        if service_name is not None:
            self._resolved_auto_battery_service = service_name
            self._auto_battery_last_scan = now
            return self._resolved_auto_battery_service
        raise ValueError(f"No DBus service found with prefix '{self.auto_battery_service_prefix}'")

    def _get_battery_soc(self):
        """Read battery SOC from the resolved battery service."""
        if not self._source_retry_ready("battery"):
            return None
        try:
            service_name = self._resolve_auto_battery_service()
            value = self._get_dbus_value(service_name, self.auto_battery_soc_path)
            value = coerce_dbus_numeric(value)
            return float(value) if isinstance(value, (int, float)) else None
        except Exception:
            self._invalidate_auto_battery_service()
            self._delay_source_retry("battery")
            return None

    def _get_grid_power(self):
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

    def _write_snapshot(self, payload):
        """Persist the helper snapshot atomically in RAM."""
        serialized = compact_json(payload)
        if serialized == self._last_payload:
            return
        write_text_atomically(self.snapshot_path, serialized)
        self._last_payload = serialized

    def run(self):
        """Main helper loop using DBus subscriptions plus a small RAM heartbeat."""
        if dbus_glib_mainloop is None:
            raise RuntimeError("dbus.mainloop.glib is required for the auto input helper")
        dbus_glib_mainloop.DBusGMainLoop(set_as_default=True)
        for signum in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None), getattr(signal, "SIGHUP", None)):
            if signum is None:
                continue
            try:
                signal.signal(signum, self._handle_signal)
            except Exception:  # pylint: disable=broad-except
                pass

        logging.info(
            "Start auto input helper pid=%s parent=%s snapshot=%s",
            os.getpid(),
            self.parent_pid,
            self.snapshot_path,
        )
        self._main_loop = GLib.MainLoop()
        self._get_system_bus().add_signal_receiver(
            self._on_name_owner_changed,
            signal_name="NameOwnerChanged",
            dbus_interface="org.freedesktop.DBus",
            bus_name="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
        )
        self._refresh_subscriptions()
        GLib.timeout_add(max(500, int(self.poll_interval_seconds * 1000)), self._heartbeat_snapshot)
        GLib.timeout_add(max(5000, int(self.validation_poll_seconds * 1000)), self._validation_poll)
        GLib.timeout_add(max(1000, int(self.subscription_refresh_seconds * 1000)), self._refresh_subscriptions_timer)
        GLib.timeout_add(1000, self._parent_watchdog)
        self._main_loop.run()
        logging.info("Auto input helper stopping pid=%s", os.getpid())


def main(argv=None):
    """CLI entry point."""
    argv = list(sys.argv[1:] if argv is None else argv)
    config_path = argv[0] if argv else os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "config.shelly_wallbox.ini",
    )
    snapshot_path = argv[1] if len(argv) > 1 else None
    parent_pid = argv[2] if len(argv) > 2 else None
    logging.basicConfig(
        format="%(levelname)s [pid=%(process)d %(threadName)s] %(message)s",
        level=logging.INFO,
    )
    helper = AutoInputHelper(config_path, snapshot_path, parent_pid)
    helper.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
