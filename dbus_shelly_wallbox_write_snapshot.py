# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers for capturing and restoring DBus write-path state."""

from __future__ import annotations

import copy
from collections import deque
from typing import Any

SNAPSHOT_DBUS_PATHS = (
    "/Mode",
    "/AutoStart",
    "/StartStop",
    "/Enable",
    "/SetCurrent",
    "/MinCurrent",
    "/MaxCurrent",
)
SNAPSHOT_ATTRS = (
    "virtual_mode",
    "virtual_autostart",
    "virtual_startstop",
    "virtual_enable",
    "virtual_set_current",
    "min_current",
    "max_current",
    "manual_override_until",
    "auto_start_condition_since",
    "auto_stop_condition_since",
    "auto_stop_condition_reason",
    "_auto_mode_cutover_pending",
    "_ignore_min_offtime_once",
    "_pending_relay_state",
    "_pending_relay_requested_at",
    "_relay_sync_expected_state",
    "_relay_sync_requested_at",
    "_relay_sync_deadline_at",
    "_relay_sync_failure_reported",
    "_last_pm_status_at",
    "_last_pm_status_confirmed",
    "_last_confirmed_pm_status_at",
)
SNAPSHOT_DEQUE_ATTRS = ("auto_samples",)
SNAPSHOT_VALUE_ATTRS = (
    "_stop_smoothed_surplus_power",
    "_stop_smoothed_grid_power",
)
SNAPSHOT_MAPPING_ATTRS = (
    "_dbusservice",
    "_dbus_publish_state",
    "_worker_snapshot",
    "_last_pm_status",
    "_last_confirmed_pm_status",
)


def _snapshot_attrs(svc: Any, attr_names: tuple[str, ...]) -> dict[str, Any]:
    """Capture one set of scalar-like attributes."""
    return {attr_name: getattr(svc, attr_name) for attr_name in attr_names if hasattr(svc, attr_name)}


def _snapshot_deques(svc: Any, attr_names: tuple[str, ...]) -> dict[str, deque[Any]]:
    """Capture one set of deque attributes."""
    return {
        attr_name: deque(getattr(svc, attr_name))
        for attr_name in attr_names
        if hasattr(svc, attr_name)
    }


def _snapshot_mappings(svc: Any, attr_names: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Capture deep-copied dict attributes used by the write path."""
    captured: dict[str, dict[str, Any]] = {}
    for attr_name in attr_names:
        if not hasattr(svc, attr_name):
            continue
        current = getattr(svc, attr_name)
        if isinstance(current, dict):
            captured[attr_name] = copy.deepcopy(current)
    return captured


def _snapshot_dbus_paths(svc: Any, dbus_paths: tuple[str, ...]) -> dict[str, Any]:
    """Capture writable DBus paths when the service exposes mapping access."""
    dbus_service = getattr(svc, "_dbusservice", None)
    if dbus_service is None:
        return {}
    captured: dict[str, Any] = {}
    for path in dbus_paths:
        try:
            captured[path] = dbus_service[path]
        except Exception:  # pylint: disable=broad-except
            continue
    return captured


def capture_write_state(
    svc: Any,
    *,
    attrs: tuple[str, ...] = SNAPSHOT_ATTRS,
    deque_attrs: tuple[str, ...] = SNAPSHOT_DEQUE_ATTRS,
    value_attrs: tuple[str, ...] = SNAPSHOT_VALUE_ATTRS,
    mapping_attrs: tuple[str, ...] = SNAPSHOT_MAPPING_ATTRS,
    dbus_paths: tuple[str, ...] = SNAPSHOT_DBUS_PATHS,
) -> dict[str, Any]:
    """Capture mutable write-path state so failed writes can be rolled back."""
    return {
        "attrs": _snapshot_attrs(svc, attrs),
        "deques": _snapshot_deques(svc, deque_attrs),
        "values": _snapshot_attrs(svc, value_attrs),
        "mappings": _snapshot_mappings(svc, mapping_attrs),
        "dbus_paths": _snapshot_dbus_paths(svc, dbus_paths),
    }


def _restore_deques(svc: Any, saved_deques: dict[str, deque[Any]]) -> None:
    """Restore previously captured deque attributes."""
    for attr_name, saved in saved_deques.items():
        current = getattr(svc, attr_name, None)
        if isinstance(current, deque):
            current.clear()
            current.extend(saved)
            continue
        setattr(svc, attr_name, deque(saved))


def _restore_mappings(svc: Any, saved_mappings: dict[str, dict[str, Any]]) -> None:
    """Restore previously captured dict-like attributes."""
    for attr_name, saved in saved_mappings.items():
        current = getattr(svc, attr_name, None)
        if isinstance(current, dict):
            current.clear()
            current.update(saved)
            continue
        setattr(svc, attr_name, copy.deepcopy(saved))


def _restore_dbus_paths(svc: Any, saved_paths: dict[str, Any]) -> None:
    """Restore writable DBus paths on best effort."""
    dbus_service = getattr(svc, "_dbusservice", None)
    if dbus_service is None:
        return
    for path, value in saved_paths.items():
        try:
            dbus_service[path] = value
        except Exception:  # pylint: disable=broad-except
            continue


def restore_write_state(svc: Any, snapshot: dict[str, Any]) -> None:
    """Restore one previously captured write-path snapshot."""
    for attr_name, value in snapshot["attrs"].items():
        setattr(svc, attr_name, value)
    for attr_name, value in snapshot["values"].items():
        setattr(svc, attr_name, value)
    _restore_deques(svc, snapshot["deques"])
    _restore_mappings(svc, snapshot["mappings"])
    _restore_dbus_paths(svc, snapshot.get("dbus_paths", {}))
