# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code=attr-defined
# pyright: reportAttributeAccessIssue=false
"""Snapshot ingestion helpers for the Auto input supervisor."""

from __future__ import annotations

import math
import os
from typing import Any, Callable

from venus_evcharger.core.contracts import paired_optional_values, timestamp_not_future, valid_battery_soc


class _AutoInputSupervisorSnapshotMixin:
    @staticmethod
    def _coerce_snapshot_timestamp(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            normalized = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(normalized):
            return None
        return normalized

    def _snapshot_mtime_ns(self, path: str) -> int | None:
        svc = self.service
        try:
            stat_path = getattr(svc, "_stat_path", os.stat)
            stat_result = stat_path(path)
        except (AttributeError, OSError):
            return None
        return getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000))

    def _load_snapshot_dict(self, path: str) -> dict[str, Any] | None:
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
    def _coerce_snapshot_number(cls, value: Any) -> float | None:
        return cls._coerce_snapshot_timestamp(value)

    @classmethod
    def _validate_snapshot_version(cls, value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            version = int(value)
        except (TypeError, ValueError):
            return None
        return version

    def _invalid_snapshot(
        self,
        warning_key: str,
        path: str,
        message: str,
        *args: object,
    ) -> dict[str, Any] | None:
        svc = self.service
        svc._warning_throttled(
            warning_key,
            max(1.0, svc.auto_input_helper_restart_seconds),
            message,
            path,
            *args,
        )
        return None

    def _normalize_snapshot_fields(
        self,
        path: str,
        snapshot: dict[str, Any],
        normalized: dict[str, Any],
        keys: tuple[str, ...],
        coercer: Callable[[Any], float | None],
        field_type: str,
    ) -> bool:
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

    def _validate_snapshot_temporal_order(self, path: str, normalized: dict[str, Any]) -> dict[str, Any] | None:
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

    def _validate_source_timestamps(self, path: str, normalized: dict[str, Any]) -> dict[str, Any] | None:
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

    def _validate_source_value_timestamp_pairs(self, path: str, normalized: dict[str, Any]) -> dict[str, Any] | None:
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

    def _validate_snapshot_battery_soc(
        self,
        path: str,
        snapshot: dict[str, Any],
        normalized: dict[str, Any],
    ) -> dict[str, Any] | None:
        battery_soc = normalized.get("battery_soc")
        if valid_battery_soc(battery_soc):
            return normalized
        return self._invalid_snapshot(
            "auto-input-helper-schema-invalid",
            path,
            "Auto input helper snapshot %s has out-of-range battery_soc=%r",
            snapshot.get("battery_soc"),
        )

    def _validate_snapshot_shape(self, path: str, snapshot: Any) -> int | None:
        if not isinstance(snapshot, dict):
            self._invalid_snapshot(
                "auto-input-helper-invalid",
                path,
                "Auto input helper snapshot %s is not a JSON object",
            )
            return None
        missing_keys = sorted(self.SNAPSHOT_REQUIRED_KEYS.difference(snapshot))
        if missing_keys:
            self._invalid_snapshot(
                "auto-input-helper-schema-invalid",
                path,
                "Auto input helper snapshot %s is missing required keys: %s",
                ", ".join(missing_keys),
            )
            return None
        version = self._validate_snapshot_version(snapshot.get("snapshot_version"))
        if version != self.SNAPSHOT_SCHEMA_VERSION:
            self._invalid_snapshot(
                "auto-input-helper-version-invalid",
                path,
                "Auto input helper snapshot %s has unsupported snapshot_version=%s",
                snapshot.get("snapshot_version"),
            )
            return None
        return version

    def _normalize_snapshot_payload(
        self,
        path: str,
        snapshot: dict[str, Any],
        version: int,
    ) -> dict[str, Any] | None:
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

    def _validate_snapshot_semantics(
        self,
        path: str,
        snapshot: dict[str, Any],
        normalized: dict[str, Any],
    ) -> dict[str, Any] | None:
        normalized_temporal = self._validate_snapshot_temporal_order(path, normalized)
        if normalized_temporal is None:
            return None
        normalized_pairs = self._validate_source_value_timestamp_pairs(path, normalized_temporal)
        if normalized_pairs is None:
            return None
        normalized_timestamps = self._validate_source_timestamps(path, normalized_pairs)
        if normalized_timestamps is None:
            return None
        return self._validate_snapshot_battery_soc(path, snapshot, normalized_timestamps)

    def _validate_snapshot_dict(self, path: str, snapshot: Any) -> dict[str, Any] | None:
        version = self._validate_snapshot_shape(path, snapshot)
        if version is None:
            return None
        normalized = self._normalize_snapshot_payload(path, snapshot, version)
        if normalized is None:
            return None
        return self._validate_snapshot_semantics(path, snapshot, normalized)

    def _snapshot_freshness(self, snapshot: dict[str, Any], current: float) -> tuple[float | None, float | None, bool]:
        svc = self.service
        captured_at = self._coerce_snapshot_timestamp(snapshot.get("captured_at"))
        heartbeat_at = self._coerce_snapshot_timestamp(snapshot.get("heartbeat_at"))
        freshness_timestamp = heartbeat_at if heartbeat_at is not None else captured_at
        snapshot_age = None if freshness_timestamp is None else max(0.0, current - freshness_timestamp)
        stale = snapshot_age is not None and snapshot_age > svc.auto_input_helper_stale_seconds
        return captured_at, freshness_timestamp, stale

    @classmethod
    def _empty_snapshot_fields(cls) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        for source_key in cls.SNAPSHOT_SOURCE_KEYS:
            fields[f"{source_key}_captured_at"] = None
            value_key = "battery_soc" if source_key == "battery" else f"{source_key}_power"
            fields[value_key] = None
        return fields

    @classmethod
    def _snapshot_value_fields(cls, snapshot: dict[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        for source_key in cls.SNAPSHOT_SOURCE_KEYS:
            fields[f"{source_key}_captured_at"] = snapshot.get(f"{source_key}_captured_at")
            value_key = "battery_soc" if source_key == "battery" else f"{source_key}_power"
            fields[value_key] = snapshot.get(value_key)
        return fields

    @classmethod
    def _normalize_source_timestamps(cls, fields: dict[str, Any]) -> dict[str, Any]:
        for source_key in cls.SNAPSHOT_SOURCE_KEYS:
            timestamp_key = f"{source_key}_captured_at"
            fields[timestamp_key] = cls._coerce_snapshot_timestamp(fields[timestamp_key])
        return fields

    def _build_snapshot_fields(
        self,
        snapshot: dict[str, Any],
        current: float,
        captured_at: float | None,
        stale: bool,
    ) -> dict[str, Any]:
        svc = self.service
        fields = {
            "captured_at": captured_at if captured_at is not None else current,
            "auto_mode_active": svc._mode_uses_auto_logic(getattr(svc, "virtual_mode", 0)),
        }
        source_fields = self._empty_snapshot_fields() if stale else self._snapshot_value_fields(snapshot)
        fields.update(self._normalize_source_timestamps(source_fields))
        return fields

    def _apply_snapshot(
        self,
        mtime_ns: int | None,
        freshness_timestamp: float | None,
        current: float,
        fields: dict[str, Any],
    ) -> None:
        svc = self.service
        svc._auto_input_snapshot_mtime_ns = mtime_ns
        svc._auto_input_snapshot_last_seen = freshness_timestamp if freshness_timestamp is not None else current
        svc._auto_input_snapshot_last_captured_at = fields.get("captured_at")
        svc._auto_input_snapshot_version = fields.get("snapshot_version")
        svc._update_worker_snapshot(**fields)

    def _snapshot_timestamps_valid(
        self,
        path: str,
        captured_at: float | None,
        freshness_timestamp: float | None,
        current: float,
    ) -> bool:
        return self._snapshot_captured_at_monotonic(path, captured_at) and self._snapshot_freshness_not_future(
            path,
            freshness_timestamp,
            current,
        )

    def _snapshot_captured_at_monotonic(self, path: str, captured_at: float | None) -> bool:
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

    def _snapshot_freshness_not_future(self, path: str, freshness_timestamp: float | None, current: float) -> bool:
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
    def _snapshot_path_changed(path: str, mtime_ns: int | None, previous_mtime_ns: int | None) -> bool:
        return bool(path) and mtime_ns is not None and previous_mtime_ns != mtime_ns

    def _refresh_snapshot_payload(
        self,
        path: str,
        current: float,
    ) -> tuple[int | None, float | None, dict[str, Any]] | None:
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

    def refresh_snapshot(self, now: float | None = None) -> None:
        svc = self.service
        svc._ensure_worker_state()
        current = svc._time_now() if now is None else float(now)
        path = getattr(svc, "auto_input_snapshot_path", "").strip()
        payload = self._refresh_snapshot_payload(path, current)
        if payload is None:
            return
        mtime_ns, freshness_timestamp, fields = payload
        self._apply_snapshot(mtime_ns, freshness_timestamp, current, fields)
