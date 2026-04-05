# SPDX-License-Identifier: GPL-3.0-or-later
"""Small shared helpers reused across Shelly wallbox modules."""

import json
import os


VALUE_CASTERS = (float, int)


def _iter_numeric_container_items(value):
    """Return list-like DBus container items, or None for scalars and mappings."""
    if isinstance(value, (str, bytes, bytearray, dict)):
        return None
    try:
        return list(value)
    except TypeError:
        return None


def _coerce_scalar_numeric(value):
    """Convert one scalar DBus value to a Python number where possible."""
    if value is None:
        return None
    for caster in VALUE_CASTERS:
        try:
            return caster(value)
        except (TypeError, ValueError):
            continue
    return None


def coerce_dbus_numeric(value):
    """Convert a raw DBus value to float or int where possible."""
    scalar = _coerce_scalar_numeric(value)
    if scalar is not None:
        return scalar
    items = _iter_numeric_container_items(value)
    if items is None:
        return value
    numeric_items = []
    for item in items:
        numeric_item = _coerce_scalar_numeric(item)
        if numeric_item is None:
            return value
        numeric_items.append(numeric_item)
    if len(numeric_items) == 1:
        return numeric_items[0]
    return value


def sum_dbus_numeric(value):
    """Return a numeric sum for scalar or sequence DBus values, or None if unusable."""
    scalar = _coerce_scalar_numeric(value)
    if scalar is not None:
        return float(scalar)
    items = _iter_numeric_container_items(value)
    if items is None:
        return None
    total = 0.0
    seen_numeric = False
    for item in items:
        numeric_item = sum_dbus_numeric(item)
        if numeric_item is None:
            continue
        total += float(numeric_item)
        seen_numeric = True
    return total if seen_numeric else None


def configured_grid_paths(*paths):
    """Return only configured non-empty per-phase grid paths."""
    return [path for path in paths if path]


def discovery_cache_valid(cached_value, last_scan, scan_interval, now):
    """Return whether a cached discovery result may still be reused."""
    return bool(cached_value) and (now - float(last_scan)) < float(scan_interval)


def prefixed_service_names(service_names, prefix, max_services=None, sort_names=False):
    """Return services with the desired prefix, optionally sorted and limited."""
    names = [str(name) for name in service_names if str(name).startswith(prefix)]
    if sort_names:
        names.sort()
    if max_services is not None:
        names = names[: int(max_services)]
    return names


def first_matching_prefixed_service(service_names, prefix, predicate):
    """Return the first prefixed service accepted by the supplied predicate."""
    for service_name in service_names:
        if not str(service_name).startswith(prefix):
            continue
        if predicate(service_name):
            return str(service_name)
    return None


def grid_values_complete_enough(seen_value, missing_paths, require_all_phases):
    """Return whether available grid readings are sufficient for control logic."""
    return bool(seen_value) and not (bool(require_all_phases) and bool(missing_paths))


def should_assume_zero_pv(explicit_service, service_names, no_auto_ac_services_found, auto_use_dc_pv, dc_value):
    """Return whether missing PV inputs should conservatively map to 0 W."""
    return (
        not explicit_service
        and (bool(no_auto_ac_services_found) or bool(service_names))
        and (not bool(auto_use_dc_pv) or dc_value is None)
    )


def compact_json(data):
    """Serialize JSON with stable compact formatting."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def write_text_atomically(path, payload, encoding="utf-8"):
    """Atomically replace a text file, cleaning up the temp file on failure."""
    tmp_path = f"{path}.tmp"
    target_dir = os.path.dirname(path)
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)
    try:
        with open(tmp_path, "w", encoding=encoding) as handle:
            handle.write(payload)
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        raise
