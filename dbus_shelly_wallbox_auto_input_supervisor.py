# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-input helper process supervision and snapshot refresh helpers."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Any


class AutoInputSupervisor:
    """Supervise the external auto-input helper and ingest its RAM snapshot."""

    SNAPSHOT_SOURCE_KEYS = ("pv", "battery", "grid")

    def __init__(self, service):
        self.service = service

    @staticmethod
    def _coerce_snapshot_timestamp(value):
        """Convert optional snapshot timestamps to floats."""
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _snapshot_mtime_ns(self, path):
        """Return the current snapshot mtime or None when the file is absent."""
        svc = self.service
        try:
            stat_result = svc._stat_path(path)
        except OSError:
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

        if isinstance(snapshot, dict):
            return snapshot

        svc._warning_throttled(
            "auto-input-helper-invalid",
            max(1.0, svc.auto_input_helper_restart_seconds),
            "Auto input helper snapshot %s is not a JSON object",
            path,
        )
        return None

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
        if not path:
            return
        mtime_ns = self._snapshot_mtime_ns(path)
        if mtime_ns is None or svc._auto_input_snapshot_mtime_ns == mtime_ns:
            return

        snapshot = self._load_snapshot_dict(path)
        if snapshot is None:
            return

        captured_at, freshness_timestamp, stale = self._snapshot_freshness(snapshot, current)
        fields = self._build_snapshot_fields(snapshot, current, captured_at, stale)
        self._apply_snapshot(mtime_ns, freshness_timestamp, current, fields)
