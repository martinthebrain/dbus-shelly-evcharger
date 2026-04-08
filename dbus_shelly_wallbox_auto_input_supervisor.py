# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-input helper process supervision and snapshot refresh helpers."""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import sys
from typing import Any

from dbus_shelly_wallbox_contracts import paired_optional_values, timestamp_not_future, valid_battery_soc
from dbus_shelly_wallbox_shared import AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION


class AutoInputSupervisor:
    """Supervise the external auto-input helper and ingest its RAM snapshot."""

    SNAPSHOT_SCHEMA_VERSION = AUTO_INPUT_SNAPSHOT_SCHEMA_VERSION
    SNAPSHOT_SOURCE_KEYS = ("pv", "battery", "grid")
    FUTURE_TIMESTAMP_TOLERANCE_SECONDS = 1.0
    SNAPSHOT_REQUIRED_KEYS = frozenset(
        {
            "snapshot_version",
            "captured_at",
            "heartbeat_at",
            "pv_captured_at",
            "pv_power",
            "battery_captured_at",
            "battery_soc",
            "grid_captured_at",
            "grid_power",
        }
    )

    def __init__(self, service):
        self.service = service

    @staticmethod
    def _coerce_snapshot_timestamp(value):
        """Convert optional snapshot timestamps to floats."""
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        try:
            normalized = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(normalized):
            return None
        return normalized

    def _snapshot_mtime_ns(self, path):
        """Return the current snapshot mtime or None when the file is absent."""
        svc = self.service
        try:
            stat_path = getattr(svc, "_stat_path", os.stat)
            stat_result = stat_path(path)
        except (AttributeError, OSError):
            return None
        return getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000))

    def _load_snapshot_dict(self, path):
        """Load and validate the helper JSON snapshot."""
        svc = self.service
        try:
            snapshot = svc._load_json_file(path)
        except Exception as error:  # pylint: disable=broad-except
            svc._warning_throttled(
                "auto-input-helper-read-failed",
                max(1.0, svc.auto_input_helper_restart_seconds),
                "Unable to read auto input helper snapshot %s: %s",
                path,
                error,
                exc_info=error,
            )
            return None

        return self._validate_snapshot_dict(path, snapshot)

    @classmethod
    def _coerce_snapshot_number(cls, value):
        """Convert optional snapshot numeric values to floats."""
        return cls._coerce_snapshot_timestamp(value)

    @classmethod
    def _validate_snapshot_version(cls, value):
        """Return the normalized snapshot schema version, or None when invalid."""
        if isinstance(value, bool):
            return None
        try:
            version = int(value)
        except (TypeError, ValueError):
            return None
        return version

    def _invalid_snapshot(self, warning_key, path, message, *args):
        """Emit one throttled schema warning and abort snapshot ingestion."""
        svc = self.service
        svc._warning_throttled(
            warning_key,
            max(1.0, svc.auto_input_helper_restart_seconds),
            message,
            path,
            *args,
        )
        return None

    def _normalize_snapshot_fields(self, path, snapshot, normalized, keys, coercer, field_type):
        """Normalize a set of snapshot fields and reject invalid values."""
        for key in keys:
            normalized_value = coercer(snapshot.get(key))
            if snapshot.get(key) is not None and normalized_value is None:
                self._invalid_snapshot(
                    "auto-input-helper-schema-invalid",
                    path,
                    "Auto input helper snapshot %s has invalid %s field %s=%r",
                    field_type,
                    key,
                    snapshot.get(key),
                )
                return False
            normalized[key] = normalized_value
        return True

    def _validate_snapshot_temporal_order(self, path, normalized):
        """Validate required snapshot timestamps and their ordering."""
        if normalized["captured_at"] is None or normalized["heartbeat_at"] is None:
            return self._invalid_snapshot(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s requires numeric captured_at and heartbeat_at fields",
            )
        if normalized["heartbeat_at"] < normalized["captured_at"]:
            return self._invalid_snapshot(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s has heartbeat_at older than captured_at",
            )
        return normalized

    def _validate_source_timestamps(self, path, normalized):
        """Validate per-source timestamps against the enclosing snapshot times."""
        for key in ("pv_captured_at", "battery_captured_at", "grid_captured_at"):
            timestamp = normalized.get(key)
            if timestamp is None:
                continue
            if timestamp > normalized["captured_at"] or timestamp > normalized["heartbeat_at"]:
                return self._invalid_snapshot(
                    "auto-input-helper-schema-invalid",
                    path,
                    "Auto input helper snapshot %s has %s newer than captured_at/heartbeat_at",
                    key,
                )
        return normalized

    def _validate_source_value_timestamp_pairs(self, path, normalized):
        """Require per-source values and their timestamps to appear together."""
        for source_key in self.SNAPSHOT_SOURCE_KEYS:
            timestamp_key = f"{source_key}_captured_at"
            value_key = "battery_soc" if source_key == "battery" else f"{source_key}_power"
            timestamp = normalized.get(timestamp_key)
            value = normalized.get(value_key)
            if paired_optional_values(value, timestamp):
                continue
            return self._invalid_snapshot(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s must provide %s and %s together",
                value_key,
                timestamp_key,
            )
        return normalized

    def _validate_snapshot_battery_soc(self, path, snapshot, normalized):
        """Validate runtime SOC values after numeric coercion."""
        battery_soc = normalized.get("battery_soc")
        if valid_battery_soc(battery_soc):
            return normalized
        return self._invalid_snapshot(
            "auto-input-helper-schema-invalid",
            path,
            "Auto input helper snapshot %s has out-of-range battery_soc=%r",
            snapshot.get("battery_soc"),
        )

    def _validate_snapshot_shape(self, path, snapshot):
        """Validate object shape, required keys, and schema version."""
        if not isinstance(snapshot, dict):
            return self._invalid_snapshot(
                "auto-input-helper-invalid",
                path,
                "Auto input helper snapshot %s is not a JSON object",
            )

        missing_keys = sorted(self.SNAPSHOT_REQUIRED_KEYS.difference(snapshot))
        if missing_keys:
            return self._invalid_snapshot(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s is missing required keys: %s",
                ", ".join(missing_keys),
            )

        version = self._validate_snapshot_version(snapshot.get("snapshot_version"))
        if version != self.SNAPSHOT_SCHEMA_VERSION:
            return self._invalid_snapshot(
                "auto-input-helper-version-invalid",
                path,
                "Auto input helper snapshot %s has unsupported snapshot_version=%s",
                snapshot.get("snapshot_version"),
            )
        return version

    def _normalize_snapshot_payload(self, path, snapshot, version):
        """Normalize timestamp and numeric fields inside one snapshot."""
        normalized = dict(snapshot)
        normalized["snapshot_version"] = version
        if not self._normalize_snapshot_fields(
            path,
            snapshot,
            normalized,
            ("captured_at", "heartbeat_at", "pv_captured_at", "battery_captured_at", "grid_captured_at"),
            self._coerce_snapshot_timestamp,
            "timestamp",
        ):
            return None
        if not self._normalize_snapshot_fields(
            path,
            snapshot,
            normalized,
            ("pv_power", "battery_soc", "grid_power"),
            self._coerce_snapshot_number,
            "numeric",
        ):
            return None
        return normalized

    def _validate_snapshot_semantics(self, path, snapshot, normalized):
        """Validate relationships between already-normalized snapshot fields."""
        normalized = self._validate_snapshot_temporal_order(path, normalized)
        if normalized is None:
            return None
        normalized = self._validate_source_value_timestamp_pairs(path, normalized)
        if normalized is None:
            return None
        normalized = self._validate_source_timestamps(path, normalized)
        if normalized is None:
            return None
        return self._validate_snapshot_battery_soc(path, snapshot, normalized)

    def _validate_snapshot_dict(self, path, snapshot):
        """Validate schema/version and normalize the helper JSON snapshot."""
        version = self._validate_snapshot_shape(path, snapshot)
        if version is None:
            return None
        normalized = self._normalize_snapshot_payload(path, snapshot, version)
        if normalized is None:
            return None
        return self._validate_snapshot_semantics(path, snapshot, normalized)

    def _snapshot_freshness(self, snapshot, current):
        """Extract normalized snapshot timestamps and stale state."""
        svc = self.service
        captured_at = self._coerce_snapshot_timestamp(snapshot.get("captured_at"))
        heartbeat_at = self._coerce_snapshot_timestamp(snapshot.get("heartbeat_at"))
        freshness_timestamp = heartbeat_at if heartbeat_at is not None else captured_at
        snapshot_age = None if freshness_timestamp is None else max(0.0, current - freshness_timestamp)
        stale = snapshot_age is not None and snapshot_age > svc.auto_input_helper_stale_seconds
        return captured_at, freshness_timestamp, stale

    @classmethod
    def _empty_snapshot_fields(cls):
        """Return the per-source snapshot fields cleared for stale snapshots."""
        fields: dict[str, Any] = {}
        for source_key in cls.SNAPSHOT_SOURCE_KEYS:
            fields[f"{source_key}_captured_at"] = None
            value_key = "battery_soc" if source_key == "battery" else f"{source_key}_power"
            fields[value_key] = None
        return fields

    @classmethod
    def _snapshot_value_fields(cls, snapshot):
        """Return live per-source fields copied from the helper snapshot."""
        fields: dict[str, Any] = {}
        for source_key in cls.SNAPSHOT_SOURCE_KEYS:
            fields[f"{source_key}_captured_at"] = snapshot.get(f"{source_key}_captured_at")
            value_key = "battery_soc" if source_key == "battery" else f"{source_key}_power"
            fields[value_key] = snapshot.get(value_key)
        return fields

    @classmethod
    def _normalize_source_timestamps(cls, fields):
        """Normalize optional per-source timestamps to float-or-None."""
        for source_key in cls.SNAPSHOT_SOURCE_KEYS:
            timestamp_key = f"{source_key}_captured_at"
            fields[timestamp_key] = cls._coerce_snapshot_timestamp(fields[timestamp_key])
        return fields

    def _build_snapshot_fields(self, snapshot, current, captured_at, stale):
        """Build the worker snapshot payload applied to the main service."""
        svc = self.service
        fields = {
            "captured_at": captured_at if captured_at is not None else current,
            "auto_mode_active": svc._mode_uses_auto_logic(getattr(svc, "virtual_mode", 0)),
        }
        source_fields = self._empty_snapshot_fields() if stale else self._snapshot_value_fields(snapshot)
        fields.update(self._normalize_source_timestamps(source_fields))
        return fields

    def _apply_snapshot(self, mtime_ns, freshness_timestamp, current, fields):
        """Update service-side snapshot bookkeeping and publish fresh fields."""
        svc = self.service
        svc._auto_input_snapshot_mtime_ns = mtime_ns
        svc._auto_input_snapshot_last_seen = freshness_timestamp if freshness_timestamp is not None else current
        svc._auto_input_snapshot_last_captured_at = fields.get("captured_at")
        svc._auto_input_snapshot_version = fields.get("snapshot_version")
        svc._update_worker_snapshot(**fields)

    def stop_helper(self, force=False):
        """Ask the helper process to stop, or kill it if it is already hung."""
        svc = self.service
        svc._ensure_worker_state()
        process = svc._auto_input_helper_process
        if process is None:
            return
        if process.poll() is not None:
            svc._auto_input_helper_process = None
            svc._auto_input_helper_restart_requested_at = None
            return
        try:
            if force:
                process.kill()
            else:
                process.terminate()
        except Exception as error:  # pylint: disable=broad-except
            logging.debug("Unable to stop auto input helper pid=%s: %s", getattr(process, "pid", "na"), error)

    def spawn_helper(self, now=None):
        """Start the separate helper process that reads PV/grid/battery from DBus."""
        svc = self.service
        svc._ensure_worker_state()
        current = svc._time_now() if now is None else float(now)
        command = [
            sys.executable,
            "-u",
            svc._auto_input_helper_path(),
            svc._config_path(),
            svc.auto_input_snapshot_path,
            str(os.getpid()),
        ]
        process = subprocess.Popen(command)  # pylint: disable=consider-using-with
        svc._auto_input_helper_process = process
        svc._auto_input_helper_last_start_at = current
        svc._auto_input_helper_restart_requested_at = None
        logging.info(
            "Started auto input helper pid=%s snapshot=%s",
            getattr(process, "pid", "na"),
            svc.auto_input_snapshot_path,
        )

    def _helper_snapshot_age(self, current):
        """Return helper freshness age based on the last seen snapshot or startup time."""
        svc = self.service
        if svc._auto_input_snapshot_last_seen is not None:
            return current - float(svc._auto_input_snapshot_last_seen)
        if svc._auto_input_helper_last_start_at > 0:
            return current - float(svc._auto_input_helper_last_start_at)
        return None

    def _handle_stale_running_helper(self, process, current, snapshot_age):
        """Schedule graceful or forced restarts for stale helper processes."""
        svc = self.service
        if snapshot_age is None or snapshot_age <= svc.auto_input_helper_stale_seconds:
            return False
        if svc._auto_input_helper_restart_requested_at is None:
            svc._auto_input_helper_restart_requested_at = current
            logging.warning(
                "Auto input helper pid=%s stale for %.0fs, restarting",
                getattr(process, "pid", "na"),
                snapshot_age,
            )
            svc._stop_auto_input_helper(force=False)
            return True
        if (current - svc._auto_input_helper_restart_requested_at) > max(2.0, svc.auto_input_helper_restart_seconds):
            svc._stop_auto_input_helper(force=True)
        return True

    def _handle_existing_helper_process(self, process, current):
        """Handle the running or exited helper process and report whether we are done."""
        svc = self.service
        return_code = process.poll()
        if return_code is None:
            snapshot_age = self._helper_snapshot_age(current)
            if self._handle_stale_running_helper(process, current, snapshot_age):
                return True
            return True

        logging.warning(
            "Auto input helper exited with rc=%s pid=%s",
            return_code,
            getattr(process, "pid", "na"),
        )
        svc._auto_input_helper_process = None
        svc._auto_input_helper_restart_requested_at = None
        return False

    def _helper_restart_cooldown_active(self, current):
        """Return whether helper restart backoff is still active."""
        svc = self.service
        return (
            svc._auto_input_helper_last_start_at > 0
            and (current - svc._auto_input_helper_last_start_at) < svc.auto_input_helper_restart_seconds
        )

    def _snapshot_timestamps_valid(self, path, captured_at, freshness_timestamp, current):
        """Return whether the new snapshot timestamps can replace the last accepted one."""
        return self._snapshot_captured_at_monotonic(path, captured_at) and self._snapshot_freshness_not_future(
            path,
            freshness_timestamp,
            current,
        )

    def _snapshot_captured_at_monotonic(self, path, captured_at):
        """Return whether captured_at still moves monotonically forward."""
        svc = self.service
        last_captured_at = getattr(svc, "_auto_input_snapshot_last_captured_at", None)
        if captured_at is None or last_captured_at is None or float(captured_at) >= float(last_captured_at):
            return True
        svc._warning_throttled(
            "auto-input-helper-captured-at-regressed",
            max(1.0, svc.auto_input_helper_restart_seconds),
            "Auto input helper snapshot %s moved captured_at backwards from %.3f to %.3f",
            path,
            float(last_captured_at),
            float(captured_at),
        )
        return False

    def _snapshot_freshness_not_future(self, path, freshness_timestamp, current):
        """Return whether the freshness timestamp still looks plausible."""
        if freshness_timestamp is None:
            return True
        svc = self.service
        if timestamp_not_future(freshness_timestamp, current, self.FUTURE_TIMESTAMP_TOLERANCE_SECONDS):
            return True
        svc._warning_throttled(
            "auto-input-helper-future-timestamp",
            max(1.0, svc.auto_input_helper_restart_seconds),
            "Auto input helper snapshot %s moved freshness timestamp into the future: %.3f > %.3f",
            path,
            float(freshness_timestamp),
            float(current),
        )
        return False

    @staticmethod
    def _snapshot_path_changed(path, mtime_ns, previous_mtime_ns):
        """Return whether one helper snapshot path has changed on disk."""
        return bool(path) and mtime_ns is not None and previous_mtime_ns != mtime_ns

    def _refresh_snapshot_payload(self, path, current):
        """Load, validate, and normalize one changed helper snapshot."""
        svc = self.service
        mtime_ns = self._snapshot_mtime_ns(path)
        if not self._snapshot_path_changed(path, mtime_ns, svc._auto_input_snapshot_mtime_ns):
            return None
        snapshot = self._load_snapshot_dict(path)
        if snapshot is None:
            return None
        captured_at, freshness_timestamp, stale = self._snapshot_freshness(snapshot, current)
        if not self._snapshot_timestamps_valid(path, captured_at, freshness_timestamp, current):
            return None
        fields = self._build_snapshot_fields(snapshot, current, captured_at, stale)
        fields["snapshot_version"] = snapshot["snapshot_version"]
        return mtime_ns, freshness_timestamp, fields

    def _spawn_helper_with_warning(self, current):
        """Start the helper and convert spawn failures into throttled warnings."""
        svc = self.service
        try:
            svc._spawn_auto_input_helper(current)
        except Exception as error:  # pylint: disable=broad-except
            svc._warning_throttled(
                "auto-input-helper-start-failed",
                max(1.0, svc.auto_input_helper_restart_seconds),
                "Unable to start auto input helper: %s",
                error,
                exc_info=error,
            )

    def ensure_helper_process(self, now=None):
        """Keep the external Auto input helper process running and restart it if stale."""
        svc = self.service
        svc._ensure_worker_state()
        current = svc._time_now() if now is None else float(now)
        process = svc._auto_input_helper_process
        if process is not None and self._handle_existing_helper_process(process, current):
            return
        if self._helper_restart_cooldown_active(current):
            return
        self._spawn_helper_with_warning(current)

    def refresh_snapshot(self, now=None):
        """Load the latest Auto input snapshot from the helper's RAM-backed JSON file."""
        svc = self.service
        svc._ensure_worker_state()
        current = svc._time_now() if now is None else float(now)
        path = getattr(svc, "auto_input_snapshot_path", "").strip()
        payload = self._refresh_snapshot_payload(path, current)
        if payload is None:
            return
        mtime_ns, freshness_timestamp, fields = payload
        self._apply_snapshot(mtime_ns, freshness_timestamp, current, fields)
