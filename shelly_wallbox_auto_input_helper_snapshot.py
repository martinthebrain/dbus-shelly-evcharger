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



class _AutoInputHelperSnapshotMixin:
    @staticmethod
    def _default_source_poll_schedule():
        return {
            "pv": 0.0,
            "battery": 0.0,
            "grid": 0.0,
        }

    def _ensure_poll_state(self):
        """Initialize runtime state for tests or partially constructed instances."""
        base_poll_interval = max(0.2, getattr(self, "poll_interval_seconds", 1.0))
        for attr_name in (
            "auto_pv_poll_interval_seconds",
            "auto_grid_poll_interval_seconds",
            "auto_battery_poll_interval_seconds",
        ):
            if not hasattr(self, attr_name):
                setattr(self, attr_name, base_poll_interval)
        if not hasattr(self, "poll_interval_seconds"):
            self.poll_interval_seconds = min(
                self.auto_pv_poll_interval_seconds,
                self.auto_grid_poll_interval_seconds,
                self.auto_battery_poll_interval_seconds,
            )
        default_values = {
            "_last_snapshot_state": self._empty_snapshot,
            "_next_source_poll_at": self._default_source_poll_schedule,
            "_signal_matches": dict,
            "_monitored_specs": dict,
            "_refresh_scheduled": False,
            "subscription_refresh_seconds": 60.0,
            "validation_poll_seconds": 30.0,
            "_main_loop": None,
            "_stop_requested": False,
        }
        for attr_name, default in default_values.items():
            if hasattr(self, attr_name):
                continue
            value = default() if callable(default) else default
            setattr(self, attr_name, value)

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
        snapshot["snapshot_version"] = self.SNAPSHOT_SCHEMA_VERSION
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
        snapshot["snapshot_version"] = self.SNAPSHOT_SCHEMA_VERSION
        self._last_snapshot_state = snapshot
        self._write_snapshot(snapshot)

    def _heartbeat_snapshot(self):
        """Keep the RAM snapshot fresh without re-reading DBus values."""
        self._ensure_poll_state()
        current = time.time()
        snapshot = dict(self._last_snapshot_state)
        snapshot["heartbeat_at"] = current
        snapshot["snapshot_version"] = self.SNAPSHOT_SCHEMA_VERSION
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
