#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Disable a matching generic dbus-shelly channel once after boot.

This helper is optional. It is meant for installations where a generic
dbus-shelly service and the dedicated wallbox service would otherwise talk to
the same physical Shelly relay and fight each other.
"""

import configparser
import logging
import os
import sys
import time
import xml.etree.ElementTree as xml_et

import dbus


DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "deploy",
    "venus",
    "config.shelly_wallbox.ini",
)
DEFAULT_GENERIC_SHELLY_SERVICE = "com.victronenergy.shelly"


def _as_bool(value, default=False):
    """Convert a config value to bool."""
    if value is None:
        return bool(default)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_int(value, default):
    """Convert a config value to int, falling back for invalid input."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _as_float(value, default):
    """Convert a config value to float, falling back for invalid input."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _normalize_mac(value):
    """Normalize MAC-like strings for comparison."""
    return str(value or "").replace(":", "").replace("-", "").replace(" ", "").strip().upper()


def load_settings(config_path):
    """Load helper settings from the shared wallbox config."""
    parser = configparser.ConfigParser()
    loaded = parser.read(config_path)
    if not loaded or "DEFAULT" not in parser:
        raise ValueError(f"Unable to read config file: {config_path}")

    section = parser["DEFAULT"]
    host = section.get("Host", "").strip()
    if not host:
        raise ValueError("DEFAULT Host is required in the config")

    channel = _as_int(section.get("GenericShellyDisableChannel", 1), 1)
    if channel < 1:
        channel = 1

    delay_seconds = _as_float(section.get("GenericShellyDisableDelaySeconds", 180), 180.0)
    if delay_seconds < 0:
        delay_seconds = 0.0

    return {
        "enabled": _as_bool(section.get("DisableGenericShellyDevice", "1"), True),
        "allow_persistent_disable": _as_bool(
            section.get("GenericShellyAllowPersistentDisable", "1"),
            True,
        ),
        "service": section.get("GenericShellyService", DEFAULT_GENERIC_SHELLY_SERVICE).strip()
        or DEFAULT_GENERIC_SHELLY_SERVICE,
        "target_ip": section.get("GenericShellyDisableIp", host).strip(),
        "target_mac": _normalize_mac(section.get("GenericShellyDisableMac", "")),
        "channel": channel,
        "delay_seconds": delay_seconds,
    }


def matches_device(serial, ip_value, mac_value, target_ip, target_mac):
    """Return True if the generic Shelly entry matches the configured target."""
    if target_ip:
        return str(ip_value or "").strip() == target_ip
    if target_mac:
        return _normalize_mac(mac_value or serial) == _normalize_mac(target_mac)
    return False


def get_system_bus():
    """Return the appropriate DBus bus for the current environment."""
    if "DBUS_SESSION_BUS_ADDRESS" in os.environ:
        return dbus.SessionBus()
    return dbus.SystemBus(private=True)


def _bus_item_interface(bus, service_name, path):
    obj = bus.get_object(service_name, path)
    return dbus.Interface(obj, "com.victronenergy.BusItem")


def get_dbus_value(bus, service_name, path, timeout=1.0):
    """Read a DBus value from com.victronenergy.BusItem."""
    return _bus_item_interface(bus, service_name, path).GetValue(timeout=timeout)


def set_dbus_value(bus, service_name, path, value, timeout=1.0):
    """Write a DBus value to com.victronenergy.BusItem."""
    payload = dbus.Int32(value) if hasattr(dbus, "Int32") and isinstance(value, int) else value
    return _bus_item_interface(bus, service_name, path).SetValue(payload, timeout=timeout)


def get_dbus_child_nodes(bus, service_name, path, timeout=1.0):
    """Return child nodes under a DBus path using introspection."""
    obj = bus.get_object(service_name, path)
    interface = dbus.Interface(obj, "org.freedesktop.DBus.Introspectable")
    xml_data = interface.Introspect(timeout=timeout)
    root = xml_et.fromstring(str(xml_data))
    return [node.attrib.get("name") for node in root.findall("node") if node.attrib.get("name")]


def disable_matching_device(settings, list_nodes, get_value, set_value, logger=None):
    """Disable the first matching generic Shelly device and return a status label."""
    logger = logger or logging
    if not settings.get("enabled", False):
        logger.info("Generic Shelly one-shot helper disabled by config")
        return "disabled-by-config"

    if not settings.get("allow_persistent_disable", True):
        logger.info("Generic Shelly one-shot helper blocked by config")
        return "persistent-disable-blocked"

    target_ip = settings.get("target_ip", "")
    target_mac = settings.get("target_mac", "")
    if not target_ip and not target_mac:
        logger.warning("Generic Shelly one-shot helper has no target IP or MAC configured")
        return "no-target"

    service_name = settings["service"]
    serials = list_nodes(service_name, "/Devices")
    for serial in serials:
        ip_value = get_value(service_name, f"/Devices/{serial}/Ip")
        mac_value = get_value(service_name, f"/Devices/{serial}/Mac")
        if not matches_device(serial, ip_value, mac_value, target_ip, target_mac):
            continue

        enabled_path = f"/Devices/{serial}/{settings['channel']}/Enabled"
        enabled_value = get_value(service_name, enabled_path)
        if int(enabled_value or 0) == 0:
            logger.info("Generic Shelly device %s already disabled on %s", serial, enabled_path)
            return "already-disabled"

        set_value(service_name, enabled_path, 0)
        logger.info("Disabled generic Shelly device %s on %s", serial, enabled_path)
        return "disabled"

    logger.info("No matching generic Shelly device found for IP %s MAC %s", target_ip, target_mac)
    return "not-found"


def run_once(config_path=DEFAULT_CONFIG_PATH):
    """Execute the delayed one-shot disable check."""
    settings = load_settings(config_path)
    delay_seconds = settings.get("delay_seconds", 0.0)
    if delay_seconds > 0:
        logging.info("Waiting %.0f seconds before generic Shelly one-shot check", delay_seconds)
        time.sleep(delay_seconds)

    bus = get_system_bus()
    timeout = 1.0
    return disable_matching_device(
        settings,
        lambda service_name, path: get_dbus_child_nodes(bus, service_name, path, timeout=timeout),
        lambda service_name, path: get_dbus_value(bus, service_name, path, timeout=timeout),
        lambda service_name, path, value: set_dbus_value(bus, service_name, path, value, timeout=timeout),
    )


def main(argv=None):
    """CLI entry point."""
    argv = list(sys.argv[1:] if argv is None else argv)
    config_path = argv[0] if argv else DEFAULT_CONFIG_PATH
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        result = run_once(config_path)
    except Exception as error:  # pylint: disable=broad-except
        logging.exception("Generic Shelly one-shot helper failed: %s", error)
        return 1
    logging.info("Generic Shelly one-shot helper finished: %s", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
