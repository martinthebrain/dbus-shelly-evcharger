"""Unit tests for the one-shot generic Shelly disable helper."""

import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.modules["dbus"] = MagicMock()

import disable_generic_shelly_once  # noqa: E402
from disable_generic_shelly_once import disable_matching_device, load_settings, matches_device  # noqa: E402


class TestDisableGenericShellyOnce(unittest.TestCase):
    def test_matches_device_prefers_ip(self):
        self.assertTrue(matches_device("98A316712F3C", "192.168.178.76", "98A316712F3C", "192.168.178.76", ""))
        self.assertFalse(matches_device("98A316712F3C", "192.168.178.77", "98A316712F3C", "192.168.178.76", ""))

    def test_matches_device_uses_mac_without_ip(self):
        self.assertTrue(matches_device("98A316712F3C", "192.168.178.77", "98A316712F3C", "", "98A316712F3C"))
        self.assertFalse(matches_device("OTHER", "192.168.178.76", "OTHER", "", "98A316712F3C"))

    def test_load_settings_uses_host_as_default_ip(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".ini") as handle:
            handle.write("[DEFAULT]\nHost=192.168.178.76\n")
            handle.flush()
            settings = load_settings(handle.name)

        self.assertEqual(settings["target_ip"], "192.168.178.76")
        self.assertEqual(settings["channel"], 1)
        self.assertEqual(settings["delay_seconds"], 180.0)

    def test_disable_matching_device_writes_only_when_enabled(self):
        settings = {
            "enabled": True,
            "allow_persistent_disable": True,
            "service": "com.victronenergy.shelly",
            "target_ip": "192.168.178.76",
            "target_mac": "98A316712F3C",
            "channel": 1,
        }

        def fake_get_value(service_name, path):
            values = {
                "/Devices/98A316712F3C/Ip": "192.168.178.76",
                "/Devices/98A316712F3C/Mac": "98A316712F3C",
                "/Devices/98A316712F3C/1/Enabled": 1,
            }
            return values[path]

        set_value = MagicMock()
        result = disable_matching_device(
            settings,
            lambda service_name, path: ["98A316712F3C"],
            fake_get_value,
            set_value,
        )

        self.assertEqual(result, "disabled")
        set_value.assert_called_once_with(
            "com.victronenergy.shelly",
            "/Devices/98A316712F3C/1/Enabled",
            0,
        )

    def test_disable_matching_device_skips_when_already_disabled(self):
        settings = {
            "enabled": True,
            "allow_persistent_disable": True,
            "service": "com.victronenergy.shelly",
            "target_ip": "192.168.178.76",
            "target_mac": "98A316712F3C",
            "channel": 1,
        }

        def fake_get_value(service_name, path):
            values = {
                "/Devices/98A316712F3C/Ip": "192.168.178.76",
                "/Devices/98A316712F3C/Mac": "98A316712F3C",
                "/Devices/98A316712F3C/1/Enabled": 0,
            }
            return values[path]

        set_value = MagicMock()
        result = disable_matching_device(
            settings,
            lambda service_name, path: ["98A316712F3C"],
            fake_get_value,
            set_value,
        )

        self.assertEqual(result, "already-disabled")
        set_value.assert_not_called()

    def test_disable_matching_device_respects_config_flags(self):
        settings = {
            "enabled": True,
            "allow_persistent_disable": False,
            "service": "com.victronenergy.shelly",
            "target_ip": "192.168.178.76",
            "target_mac": "98A316712F3C",
            "channel": 1,
        }

        set_value = MagicMock()
        result = disable_matching_device(
            settings,
            lambda service_name, path: ["98A316712F3C"],
            lambda service_name, path: None,
            set_value,
        )

        self.assertEqual(result, "persistent-disable-blocked")
        set_value.assert_not_called()
