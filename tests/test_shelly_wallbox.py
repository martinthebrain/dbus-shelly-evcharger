# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the Shelly wallbox helper logic."""

import json
import sys
import tempfile
import threading
import unittest
from collections import deque
from datetime import datetime
from unittest.mock import MagicMock

sys.modules["vedbus"] = MagicMock()
sys.modules["dbus"] = MagicMock()
sys.modules["dbus.mainloop.glib"] = MagicMock()
sys.modules["gi"] = MagicMock()
sys.modules["gi.repository"] = MagicMock()
sys.modules["gi.repository.GLib"] = MagicMock()

import dbus_shelly_wallbox  # noqa: E402
import shelly_wallbox.runtime.support as runtime_support_module  # noqa: E402
from dbus_shelly_wallbox import ShellyWallboxService, mode_uses_auto_logic, month_in_ranges, month_window, normalize_mode, normalize_phase, parse_hhmm, phase_values  # noqa: E402


class TestShellyWallboxHelpers(unittest.TestCase):
    @staticmethod
    def _make_update_service():
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.poll_interval_ms = 1000
        service.phase = "L1"
        service.voltage_mode = "phase"
        service.charging_threshold_watts = 100
        service.idle_status = 6
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 1
        service.virtual_set_current = 16.0
        service.min_current = 6.0
        service.max_current = 16.0
        service.charging_started_at = None
        service.energy_at_start = 0.0
        service.last_status = 0
        service._last_health_reason = "init"
        service._last_health_code = 0
        service._error_state = {
            "dbus": 0,
            "shelly": 0,
            "charger": 0,
            "pv": 0,
            "battery": 0,
            "grid": 0,
            "cache_hits": 0,
        }
        service._failure_active = {
            "dbus": False,
            "shelly": False,
            "charger": False,
            "pv": False,
            "battery": False,
            "grid": False,
        }
        service._warning_state = {}
        service._dbusservice = {"/UpdateIndex": 0}
        service.last_update = 0
        service.auto_input_cache_seconds = 120
        service.auto_input_snapshot_path = "/tmp/auto-helper.json"
        service.auto_input_helper_restart_seconds = 5
        service.auto_input_helper_stale_seconds = 15
        service.auto_shelly_soft_fail_seconds = 10
        service.auto_watchdog_stale_seconds = 180
        service.auto_watchdog_recovery_seconds = 60
        service.auto_grid_missing_stop_seconds = 60
        service.auto_audit_log = False
        service.auto_manual_override_seconds = 300
        service.manual_override_until = 0.0
        service._auto_mode_cutover_pending = False
        service._ignore_min_offtime_once = False
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service._last_pv_value = None
        service._last_pv_at = None
        service._last_grid_value = None
        service._last_grid_at = None
        service._last_battery_soc_value = None
        service._last_battery_soc_at = None
        service._last_pm_status = None
        service._last_pm_status_at = None
        service._last_shelly_warning = None
        service._last_battery_allow_warning = None
        service._last_dbus_ok_at = None
        service._last_voltage = None
        service.backend_mode = "combined"
        service.meter_backend_type = "shelly_combined"
        service.switch_backend_type = "shelly_combined"
        service.charger_backend_type = None
        service._charger_target_current_amps = None
        service._charger_target_current_applied_at = None
        service._last_auto_metrics = {"surplus": None, "grid": None, "soc": None}
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service._auto_cached_inputs_used = False
        service._worker_snapshot_lock = threading.Lock()
        service._worker_snapshot = ShellyWallboxService._empty_worker_snapshot()
        service._ensure_auto_input_helper_process = MagicMock()
        service._refresh_auto_input_snapshot = MagicMock()
        return service

    @staticmethod
    def _set_worker_snapshot(service, **overrides):
        snapshot = ShellyWallboxService._empty_worker_snapshot()
        snapshot.update(overrides)
        service._worker_snapshot = snapshot

    def test_normalize_phase_accepts_1p_alias(self):
        self.assertEqual(normalize_phase("1P"), "L1")

    def test_normalize_mode_preserves_supported_values(self):
        self.assertEqual(normalize_mode(2), 2)
        self.assertEqual(normalize_mode("2"), 2)
        self.assertEqual(normalize_mode(1), 1)
        self.assertEqual(normalize_mode(0), 0)
        self.assertEqual(normalize_mode("bad"), 0)

    def test_mode_uses_auto_logic_accepts_auto_and_scheduled(self):
        self.assertTrue(mode_uses_auto_logic(1))
        self.assertTrue(mode_uses_auto_logic(2))
        self.assertFalse(mode_uses_auto_logic(0))

    def test_parse_hhmm(self):
        self.assertEqual(parse_hhmm("07:30", (8, 0)), (7, 30))
        self.assertEqual(parse_hhmm("bad", (8, 0)), (8, 0))

    def test_month_in_ranges(self):
        self.assertTrue(month_in_ranges(1, ((12, 2),)))
        self.assertTrue(month_in_ranges(7, ((6, 8),)))
        self.assertFalse(month_in_ranges(11, ((3, 5),)))

    def test_month_window(self):
        config = {"DEFAULT": {"AutoAprStart": "07:45", "AutoAprEnd": "19:15"}}
        self.assertEqual(month_window(config, 4, "07:30", "19:30"), ((7, 45), (19, 15)))

    def test_validate_runtime_config_clamps_invalid_values(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.poll_interval_ms = 0
        service.sign_of_life_minutes = 0
        service.auto_pv_max_services = 0
        service.auto_pv_scan_interval_seconds = -1
        service.auto_battery_scan_interval_seconds = -1
        service.auto_dbus_backoff_base_seconds = -1
        service.auto_dbus_backoff_max_seconds = -1
        service.auto_average_window_seconds = -1
        service.auto_min_runtime_seconds = -1
        service.auto_min_offtime_seconds = -1
        service.auto_start_delay_seconds = -1
        service.auto_stop_delay_seconds = -1
        service.auto_input_cache_seconds = -1
        service.auto_input_helper_restart_seconds = -1
        service.auto_input_helper_stale_seconds = -1
        service.auto_shelly_soft_fail_seconds = -1
        service.auto_watchdog_stale_seconds = -1
        service.auto_watchdog_recovery_seconds = -1
        service.auto_startup_warmup_seconds = -1
        service.auto_manual_override_seconds = -1
        service.startup_device_info_retry_seconds = -1
        service.startup_device_info_retries = -1
        service.shelly_request_timeout_seconds = -1
        service.dbus_method_timeout_seconds = -1
        service.auto_min_soc = 120
        service.auto_resume_soc = -5
        service.auto_start_surplus_watts = 1500
        service.auto_stop_surplus_watts = 2400

        service._validate_runtime_config()

        self.assertEqual(service.poll_interval_ms, 100)
        self.assertEqual(service.sign_of_life_minutes, 1)
        self.assertEqual(service.auto_pv_max_services, 1)
        self.assertEqual(service.auto_pv_scan_interval_seconds, 0.0)
        self.assertEqual(service.auto_input_cache_seconds, 0.0)
        self.assertEqual(service.auto_input_helper_restart_seconds, 0.0)
        self.assertEqual(service.auto_input_helper_stale_seconds, 0.0)
        self.assertEqual(service.auto_shelly_soft_fail_seconds, 0.0)
        self.assertEqual(service.auto_watchdog_stale_seconds, 0.0)
        self.assertEqual(service.auto_watchdog_recovery_seconds, 0.0)
        self.assertEqual(service.auto_startup_warmup_seconds, 0.0)
        self.assertEqual(service.auto_manual_override_seconds, 0.0)
        self.assertEqual(service.startup_device_info_retry_seconds, 0.0)
        self.assertEqual(service.startup_device_info_retries, 0)
        self.assertEqual(service.shelly_request_timeout_seconds, 2.0)
        self.assertEqual(service.dbus_method_timeout_seconds, 1.0)
        self.assertEqual(service.auto_min_soc, 100.0)
        self.assertEqual(service.auto_resume_soc, 100.0)
        self.assertEqual(service.auto_stop_surplus_watts, 1500)

    def test_available_surplus_uses_only_pv_backed_export(self):
        self.assertEqual(ShellyWallboxService._get_available_surplus_watts(2500, -1800), 1800)
        self.assertEqual(ShellyWallboxService._get_available_surplus_watts(0, -1800), 0)

    def test_get_pv_power_skips_failed_services_and_dc(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_pv_path = "/Ac/Power"
        service.auto_use_dc_pv = True
        service.auto_dc_pv_service = "com.victronenergy.system"
        service.auto_dc_pv_path = "/Dc/Pv/Power"
        service._resolve_auto_pv_services = MagicMock(return_value=["pv1", "pv2"])

        def fake_get_value(service_name, path):
            if service_name == "pv1":
                return 1000
            if service_name == "pv2":
                raise ValueError("gone")
            if service_name == "com.victronenergy.system":
                raise ValueError("no dc pv")
            return None

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        self.assertEqual(service._get_pv_power(), 1000)

    def test_get_pv_power_uses_dc_only_when_ac_missing(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_pv_path = "/Ac/Power"
        service.auto_pv_service = ""
        service.auto_use_dc_pv = True
        service.auto_dc_pv_service = "com.victronenergy.system"
        service.auto_dc_pv_path = "/Dc/Pv/Power"
        service._resolve_auto_pv_services = MagicMock(side_effect=ValueError("no ac pv"))
        service.auto_pv_scan_interval_seconds = 60
        service._last_pv_missing_warning = None

        def fake_get_value(service_name, path):
            if service_name == "com.victronenergy.system":
                return 750
            return None

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        self.assertEqual(service._get_pv_power(), 750)

    def test_get_pv_power_uses_summed_dc_sequence_when_available(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_pv_path = "/Ac/Power"
        service.auto_pv_service = ""
        service.auto_use_dc_pv = True
        service.auto_dc_pv_service = "com.victronenergy.system"
        service.auto_dc_pv_path = "/Dc/Pv/Power"
        service._resolve_auto_pv_services = MagicMock(side_effect=ValueError("no ac pv"))
        service.auto_pv_scan_interval_seconds = 60
        service._last_pv_missing_warning = None

        def fake_get_value(service_name, path):
            if service_name == "com.victronenergy.system":
                return [500, 250]
            return None

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        self.assertEqual(service._get_pv_power(), 750)

    def test_get_pv_power_assumes_zero_when_no_ac_or_dc_pv_exists(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_pv_path = "/Ac/Power"
        service.auto_pv_service = ""
        service.auto_use_dc_pv = True
        service.auto_dc_pv_service = "com.victronenergy.system"
        service.auto_dc_pv_path = "/Dc/Pv/Power"
        service._resolve_auto_pv_services = MagicMock(side_effect=ValueError("no ac pv"))
        service.auto_pv_scan_interval_seconds = 60
        service._last_pv_missing_warning = None

        def fake_get_value(service_name, path):
            if service_name == "com.victronenergy.system":
                raise ValueError("no dc pv")
            return None

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        self.assertEqual(service._get_pv_power(), 0.0)

    def test_get_pv_power_assumes_zero_when_discovered_services_have_no_readable_values(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_pv_path = "/Ac/Power"
        service.auto_pv_service = ""
        service.auto_use_dc_pv = True
        service.auto_dc_pv_service = "com.victronenergy.system"
        service.auto_dc_pv_path = "/Dc/Pv/Power"
        service._resolve_auto_pv_services = MagicMock(return_value=["pv1", "pv2"])
        service.auto_pv_scan_interval_seconds = 60
        service._last_pv_missing_warning = None

        def fake_get_value(service_name, path):
            if service_name == "com.victronenergy.system":
                raise ValueError("no dc pv")
            raise ValueError("night mode")

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        self.assertEqual(service._get_pv_power(), 0.0)

    def test_get_pv_power_does_not_assume_zero_for_explicit_ac_service_failure(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_pv_path = "/Ac/Power"
        service.auto_pv_service = "com.victronenergy.pvinverter.http_48"
        service.auto_use_dc_pv = False
        service._resolve_auto_pv_services = MagicMock(side_effect=ValueError("explicit service missing"))
        service.auto_pv_scan_interval_seconds = 60
        service._last_pv_missing_warning = None

        self.assertIsNone(service._get_pv_power())

    def test_get_pv_power_rescans_when_cached_services_fail(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_pv_path = "/Ac/Power"
        service.auto_use_dc_pv = False
        service.auto_pv_service = ""
        service._resolved_auto_pv_services = ["pv-old"]
        service._auto_pv_last_scan = 1.0
        service.auto_pv_scan_interval_seconds = 60
        service._last_pv_missing_warning = None
        service._resolve_auto_pv_services = MagicMock(side_effect=[["pv-old"], ["pv-new"]])

        def fake_get_value(service_name, path):
            if service_name == "pv-old":
                raise ValueError("stale service")
            if service_name == "pv-new":
                return 900
            return None

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        self.assertEqual(service._get_pv_power(), 900)

    def test_get_pv_power_skips_reads_during_retry_cooldown(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service._source_retry_after = {"pv": 200.0}

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            self.assertIsNone(service._get_pv_power())

    def test_resolve_auto_pv_services_limits_results(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_pv_service = ""
        service.auto_pv_service_prefix = "com.victronenergy.pvinverter"
        service.auto_pv_max_services = 1
        service.auto_pv_scan_interval_seconds = 60
        service._resolved_auto_pv_services = []
        service._auto_pv_last_scan = 0.0
        service._get_system_bus = MagicMock()
        dbus_shelly_wallbox.dbus.Interface.return_value.ListNames.return_value = [
            "com.victronenergy.pvinverter.http_1",
            "com.victronenergy.pvinverter.http_2",
        ]

        services = service._resolve_auto_pv_services()
        self.assertEqual(services, ["com.victronenergy.pvinverter.http_1"])

    def test_auto_battery_service_auto_detect(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_battery_service = ""
        service.auto_battery_service_prefix = "com.victronenergy.battery"
        service.auto_battery_scan_interval_seconds = 60
        service.auto_battery_soc_path = "/Soc"
        service._resolved_auto_battery_service = None
        service._auto_battery_last_scan = 0.0
        service._get_system_bus = MagicMock()
        dbus_shelly_wallbox.dbus.Interface.return_value.ListNames.return_value = [
            "com.victronenergy.system",
            "com.victronenergy.battery.test",
        ]
        service._get_dbus_value = MagicMock(return_value=55.0)

        self.assertEqual(service._resolve_auto_battery_service(), "com.victronenergy.battery.test")

    def test_get_grid_power_skips_reads_during_retry_cooldown(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service._source_retry_after = {"grid": 200.0}

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            self.assertIsNone(service._get_grid_power())

    def test_get_grid_power_requires_all_phases_by_default(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_grid_service = "com.victronenergy.system"
        service.auto_grid_l1_path = "/Ac/Grid/L1/Power"
        service.auto_grid_l2_path = "/Ac/Grid/L2/Power"
        service.auto_grid_l3_path = "/Ac/Grid/L3/Power"
        service.auto_grid_require_all_phases = True
        service.auto_pv_scan_interval_seconds = 60
        service._source_retry_after = {}
        service._warning_state = {}
        service._error_state = {"dbus": 0, "shelly": 0, "pv": 0, "battery": 0, "grid": 0, "cache_hits": 0}
        service._failure_active = {"dbus": False, "shelly": False, "pv": False, "battery": False, "grid": False}

        def fake_get_value(service_name, path):
            values = {
                "/Ac/Grid/L1/Power": -1000.0,
                "/Ac/Grid/L2/Power": 500.0,
            }
            if path not in values:
                raise ValueError("missing phase")
            return values[path]

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            self.assertIsNone(service._get_grid_power())

    def test_get_grid_power_can_allow_partial_phases_when_configured(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_grid_service = "com.victronenergy.system"
        service.auto_grid_l1_path = "/Ac/Grid/L1/Power"
        service.auto_grid_l2_path = "/Ac/Grid/L2/Power"
        service.auto_grid_l3_path = "/Ac/Grid/L3/Power"
        service.auto_grid_require_all_phases = False
        service.auto_pv_scan_interval_seconds = 60
        service._source_retry_after = {}
        service._warning_state = {}
        service._error_state = {"dbus": 0, "shelly": 0, "pv": 0, "battery": 0, "grid": 0, "cache_hits": 0}
        service._failure_active = {"dbus": False, "shelly": False, "pv": False, "battery": False, "grid": False}

        def fake_get_value(service_name, path):
            values = {
                "/Ac/Grid/L1/Power": -1000.0,
                "/Ac/Grid/L2/Power": 500.0,
            }
            if path not in values:
                raise ValueError("missing phase")
            return values[path]

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            self.assertEqual(service._get_grid_power(), -500.0)

    def test_auto_battery_service_fallback_when_override_missing(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_battery_service = "com.victronenergy.battery.explicit"
        service.auto_battery_service_prefix = "com.victronenergy.battery"
        service.auto_battery_scan_interval_seconds = 60
        service.auto_battery_soc_path = "/Soc"
        service._resolved_auto_battery_service = None
        service._auto_battery_last_scan = 0.0
        service._get_system_bus = MagicMock()
        dbus_shelly_wallbox.dbus.Interface.return_value.ListNames.return_value = [
            "com.victronenergy.system",
            "com.victronenergy.battery.test",
        ]

        def fake_get_value(service_name, path):
            if service_name == "com.victronenergy.battery.explicit":
                return None
            return 55.0

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        self.assertEqual(service._resolve_auto_battery_service(), "com.victronenergy.battery.test")

    def test_get_battery_soc_retries_after_cached_service_failure(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_battery_service = ""
        service.auto_battery_soc_path = "/Soc"
        service.auto_battery_scan_interval_seconds = 60
        service._last_battery_missing_warning = None
        service._resolved_auto_battery_service = "battery-old"
        service._auto_battery_last_scan = 1.0
        service._resolve_auto_battery_service = MagicMock(side_effect=["battery-old", "battery-new"])

        def fake_get_value(service_name, path):
            if service_name == "battery-old":
                raise ValueError("stale battery service")
            if service_name == "battery-new":
                return 56.0
            return None

        service._get_dbus_value = MagicMock(side_effect=fake_get_value)

        self.assertEqual(service._get_battery_soc(), 56.0)

    def test_get_dbus_value_retries_once_after_error(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.dbus_method_timeout_seconds = 1.25
        first_interface = MagicMock()
        second_interface = MagicMock()
        first_interface.GetValue.side_effect = RuntimeError("temporary dbus error")
        second_interface.GetValue.return_value = 42.0
        first_bus = MagicMock()
        second_bus = MagicMock()
        first_bus.get_object.return_value = object()
        second_bus.get_object.return_value = object()
        service._get_system_bus = MagicMock(side_effect=[first_bus, second_bus])
        service._reset_system_bus = MagicMock()

        original_interface = dbus_shelly_wallbox.dbus.Interface
        dbus_shelly_wallbox.dbus.Interface = MagicMock(side_effect=[first_interface, second_interface])
        try:
            self.assertEqual(service._get_dbus_value("com.victronenergy.system", "/Dc/Pv/Power"), 42.0)
        finally:
            dbus_shelly_wallbox.dbus.Interface = original_interface

        self.assertEqual(service._get_system_bus.call_count, 2)
        second_interface.GetValue.assert_called_once_with(timeout=1.25)
        service._reset_system_bus.assert_called()

    def test_system_bus_reset_recreates_cached_connection(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        first_bus = object()
        second_bus = object()

        original_system_bus = runtime_support_module.dbus.SystemBus
        original_session_bus = runtime_support_module.dbus.SessionBus
        runtime_support_module.dbus.SystemBus = MagicMock(side_effect=[first_bus, second_bus])
        runtime_support_module.dbus.SessionBus = MagicMock(
            side_effect=AssertionError("session bus not expected")
        )
        try:
            with unittest.mock.patch.dict(dbus_shelly_wallbox.os.environ, {}, clear=True):
                self.assertIs(service._get_system_bus(), first_bus)
                self.assertIs(service._get_system_bus(), first_bus)
                service._reset_system_bus()
                self.assertIs(service._get_system_bus(), second_bus)
        finally:
            runtime_support_module.dbus.SystemBus = original_system_bus
            runtime_support_module.dbus.SessionBus = original_session_bus

    def test_request_uses_configured_shelly_timeout(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.shelly_request_timeout_seconds = 1.5
        service.use_digest_auth = False
        service.username = ""
        service.password = ""
        response = MagicMock()
        response.json.return_value = {"ok": True}
        service.session = MagicMock()
        service.session.get.return_value = response

        self.assertEqual(service._request("http://example.invalid"), {"ok": True})
        service.session.get.assert_called_once_with(url="http://example.invalid", timeout=1.5)

    def test_runtime_state_can_be_loaded_from_ram_file(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 0
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.virtual_startstop = 1
        service.manual_override_until = 0.0
        service._auto_mode_cutover_pending = False
        service._ignore_min_offtime_once = False
        service.relay_last_changed_at = None
        service.relay_last_off_at = None

        with tempfile.TemporaryDirectory() as temp_dir:
            service.runtime_state_path = f"{temp_dir}/state.json"
            with open(service.runtime_state_path, "w", encoding="utf-8") as handle:
                handle.write(
                    '{"autostart":0,"auto_mode_cutover_pending":1,"enable":1,'
                    '"ignore_min_offtime_once":1,"manual_override_until":123.5,'
                    '"mode":1,"relay_last_changed_at":111.0,"relay_last_off_at":112.0,"startstop":0}'
                )

            service._load_runtime_state()

        self.assertEqual(service.virtual_mode, 1)
        self.assertEqual(service.virtual_autostart, 0)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.virtual_startstop, 0)
        self.assertEqual(service.manual_override_until, 123.5)
        self.assertTrue(service._auto_mode_cutover_pending)
        self.assertFalse(service._ignore_min_offtime_once)
        self.assertEqual(service.relay_last_changed_at, 111.0)
        self.assertEqual(service.relay_last_off_at, 112.0)

    def test_runtime_state_is_written_atomically_to_ram_file(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.virtual_startstop = 0
        service.manual_override_until = 0.0
        service._auto_mode_cutover_pending = True
        service._ignore_min_offtime_once = False
        service.relay_last_changed_at = 100.0
        service.relay_last_off_at = 101.0
        service._runtime_state_serialized = None

        with tempfile.TemporaryDirectory() as temp_dir:
            service.runtime_state_path = f"{temp_dir}/state.json"
            service._save_runtime_state()

            with open(service.runtime_state_path, "r", encoding="utf-8") as handle:
                saved = handle.read()

        self.assertIn('"mode":1', saved)
        self.assertIn('"enable":1', saved)
        self.assertIn('"auto_mode_cutover_pending":1', saved)
        self.assertNotIn('"ignore_min_offtime_once"', saved)

    def test_io_worker_once_collects_snapshot_in_ram(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.poll_interval_ms = 1000
        service.virtual_mode = 1
        service.auto_shelly_soft_fail_seconds = 10
        service.auto_dbus_backoff_base_seconds = 5
        service._warning_state = {}
        service._error_state = {
            "dbus": 0,
            "shelly": 0,
            "pv": 0,
            "battery": 0,
            "grid": 0,
            "cache_hits": 0,
        }
        service._failure_active = {
            "dbus": False,
            "shelly": False,
            "pv": False,
            "battery": False,
            "grid": False,
        }
        service._source_retry_after = {}
        service._worker_fetch_pm_status = MagicMock(return_value={"output": True})

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            service._io_worker_once()

        snapshot = service._get_worker_snapshot()
        self.assertEqual(snapshot["captured_at"], 100.0)
        self.assertEqual(snapshot["pm_captured_at"], 100.0)
        self.assertEqual(snapshot["pm_status"], {"output": True})
        self.assertTrue(snapshot["auto_mode_active"])

    def test_io_auto_worker_once_collects_auto_inputs_in_ram(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.poll_interval_ms = 1000
        service.virtual_mode = 1
        service.auto_shelly_soft_fail_seconds = 10
        service.auto_dbus_backoff_base_seconds = 5
        service._warning_state = {}
        service._error_state = {
            "dbus": 0,
            "shelly": 0,
            "pv": 0,
            "battery": 0,
            "grid": 0,
            "cache_hits": 0,
        }
        service._failure_active = {
            "dbus": False,
            "shelly": False,
            "pv": False,
            "battery": False,
            "grid": False,
        }
        service._source_retry_after = {}
        service.auto_input_snapshot_path = "/tmp/auto-helper.json"
        service.auto_input_helper_stale_seconds = 15
        service.auto_input_helper_restart_seconds = 5
        helper_snapshot = {
            "snapshot_version": 1,
            "captured_at": 100.0,
            "heartbeat_at": 100.0,
            "pv_captured_at": 100.0,
            "pv_power": 2300.0,
            "battery_captured_at": 100.0,
            "battery_soc": 57.0,
            "grid_captured_at": 100.0,
            "grid_power": -2100.0,
        }
        stat_result = MagicMock()
        stat_result.st_mtime_ns = 1

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            with unittest.mock.patch("dbus_shelly_wallbox.os.stat", return_value=stat_result):
                with unittest.mock.patch("dbus_shelly_wallbox.open", unittest.mock.mock_open(read_data=json.dumps(helper_snapshot))):
                    service._refresh_auto_input_snapshot()

        snapshot = service._get_worker_snapshot()
        self.assertEqual(snapshot["pv_power"], 2300.0)
        self.assertEqual(snapshot["battery_soc"], 57.0)
        self.assertEqual(snapshot["grid_power"], -2100.0)
        self.assertEqual(snapshot["pv_captured_at"], 100.0)
        self.assertEqual(snapshot["battery_captured_at"], 100.0)
        self.assertEqual(snapshot["grid_captured_at"], 100.0)
        self.assertTrue(snapshot["auto_mode_active"])

    def test_refresh_auto_input_snapshot_uses_heartbeat_for_helper_staleness(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.poll_interval_ms = 1000
        service.virtual_mode = 1
        service.auto_shelly_soft_fail_seconds = 10
        service.auto_dbus_backoff_base_seconds = 5
        service._warning_state = {}
        service._error_state = {
            "dbus": 0,
            "shelly": 0,
            "pv": 0,
            "battery": 0,
            "grid": 0,
            "cache_hits": 0,
        }
        service._failure_active = {
            "dbus": False,
            "shelly": False,
            "pv": False,
            "battery": False,
            "grid": False,
        }
        service._source_retry_after = {}
        service.auto_input_snapshot_path = "/tmp/auto-helper.json"
        service.auto_input_helper_stale_seconds = 15
        service.auto_input_helper_restart_seconds = 5
        helper_snapshot = {
            "snapshot_version": 1,
            "captured_at": 100.0,
            "heartbeat_at": 130.0,
            "pv_captured_at": 100.0,
            "pv_power": 2300.0,
            "battery_captured_at": 100.0,
            "battery_soc": 57.0,
            "grid_captured_at": 100.0,
            "grid_power": -2100.0,
        }
        stat_result = MagicMock()
        stat_result.st_mtime_ns = 3

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=130.0):
            with unittest.mock.patch("dbus_shelly_wallbox.os.stat", return_value=stat_result):
                with unittest.mock.patch("dbus_shelly_wallbox.open", unittest.mock.mock_open(read_data=json.dumps(helper_snapshot))):
                    service._refresh_auto_input_snapshot()

        snapshot = service._get_worker_snapshot()
        self.assertEqual(snapshot["pv_power"], 2300.0)
        self.assertEqual(snapshot["pv_captured_at"], 100.0)
        self.assertEqual(service._auto_input_snapshot_last_seen, 130.0)

    def test_io_auto_worker_does_not_delay_published_pm_status(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.poll_interval_ms = 1000
        service.virtual_mode = 1
        service.auto_shelly_soft_fail_seconds = 10
        service.auto_dbus_backoff_base_seconds = 5
        service._warning_state = {}
        service._error_state = {
            "dbus": 0,
            "shelly": 0,
            "pv": 0,
            "battery": 0,
            "grid": 0,
            "cache_hits": 0,
        }
        service._failure_active = {
            "dbus": False,
            "shelly": False,
            "pv": False,
            "battery": False,
            "grid": False,
        }
        service._source_retry_after = {}
        self._set_worker_snapshot(
            service,
            captured_at=99.0,
            pm_captured_at=99.0,
            pm_status={"output": True, "apower": 1800.0},
        )

        service.auto_input_snapshot_path = "/tmp/auto-helper.json"
        service.auto_input_helper_stale_seconds = 15
        service.auto_input_helper_restart_seconds = 5
        helper_snapshot = {
            "snapshot_version": 1,
            "captured_at": 100.0,
            "heartbeat_at": 100.0,
            "pv_captured_at": 100.0,
            "pv_power": 2300.0,
            "battery_captured_at": 100.0,
            "battery_soc": 57.0,
            "grid_captured_at": 100.0,
            "grid_power": -2100.0,
        }
        stat_result = MagicMock()
        stat_result.st_mtime_ns = 2

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            with unittest.mock.patch("dbus_shelly_wallbox.os.stat", return_value=stat_result):
                with unittest.mock.patch("dbus_shelly_wallbox.open", unittest.mock.mock_open(read_data=json.dumps(helper_snapshot))):
                    service._refresh_auto_input_snapshot()

        snapshot = service._get_worker_snapshot()
        self.assertEqual(snapshot["pm_status"], {"output": True, "apower": 1800.0})
        self.assertEqual(snapshot["pv_power"], 2300.0)
        self.assertEqual(snapshot["pm_captured_at"], 99.0)

    def test_start_io_worker_restarts_helper_when_process_missing(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service._ensure_worker_state = MagicMock()
        alive_worker = MagicMock()
        alive_worker.is_alive.return_value = True
        service._worker_thread = alive_worker
        service._auto_input_helper_process = None
        service._auto_input_helper_last_start_at = 0.0
        service._auto_input_helper_restart_requested_at = None
        service.auto_input_helper_restart_seconds = 5
        service._spawn_auto_input_helper = MagicMock()

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            service._start_io_worker()

        service._spawn_auto_input_helper.assert_called_once()

    def test_ensure_auto_input_helper_process_restarts_stale_helper(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service._ensure_worker_state = MagicMock()
        process = MagicMock()
        process.poll.return_value = None
        process.pid = 4321
        service._auto_input_helper_process = process
        service._auto_input_helper_last_start_at = 10.0
        service._auto_input_helper_restart_requested_at = None
        service._auto_input_snapshot_last_seen = 70.0
        service.auto_input_helper_stale_seconds = 15
        service.auto_input_helper_restart_seconds = 5
        service._stop_auto_input_helper = MagicMock()

        service._ensure_auto_input_helper_process(100.0)

        service._stop_auto_input_helper.assert_called_once_with(force=False)
        self.assertEqual(service._auto_input_helper_restart_requested_at, 100.0)

    def test_worker_apply_pending_relay_command_runs_in_worker_thread(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.poll_interval_ms = 1000
        service.pm_id = 0
        service.auto_shelly_soft_fail_seconds = 10
        service._warning_state = {}
        service._error_state = {
            "dbus": 0,
            "shelly": 0,
            "pv": 0,
            "battery": 0,
            "grid": 0,
            "cache_hits": 0,
        }
        service._failure_active = {
            "dbus": False,
            "shelly": False,
            "pv": False,
            "battery": False,
            "grid": False,
        }
        service._source_retry_after = {}
        service._worker_session = MagicMock()
        service._rpc_call_with_session = MagicMock(return_value={"was_on": False})
        service._publish_local_pm_status = MagicMock()
        service._mark_relay_changed = MagicMock()

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            service._queue_relay_command(True, 100.0)
            service._worker_apply_pending_relay_command()

        service._rpc_call_with_session.assert_called_once_with(
            service._worker_session,
            "Switch.Set",
            id=0,
            on=True,
        )
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)
        service._mark_relay_changed.assert_called_once_with(True, 100.0)
        self.assertEqual(service._peek_pending_relay_command(), (None, None))

    def test_phase_values_single_phase(self):
        values = phase_values(3680, 230, "L1", "phase")
        self.assertAlmostEqual(values["L1"]["power"], 3680)
        self.assertAlmostEqual(values["L1"]["current"], 16)
        self.assertEqual(values["L2"]["power"], 0.0)
        self.assertEqual(values["L3"]["power"], 0.0)

    def test_phase_values_three_phase_with_line_voltage(self):
        values = phase_values(6900, 400, "3P", "line")
        self.assertAlmostEqual(values["L1"]["voltage"], 400 / (3 ** 0.5))
        self.assertAlmostEqual(values["L2"]["voltage"], 400 / (3 ** 0.5))
        self.assertAlmostEqual(values["L3"]["voltage"], 400 / (3 ** 0.5))

    def test_phase_values_three_phase_with_phase_voltage(self):
        values = phase_values(6900, 230, "3P", "phase")
        self.assertAlmostEqual(values["L1"]["power"], 2300)
        self.assertAlmostEqual(values["L2"]["power"], 2300)
        self.assertAlmostEqual(values["L3"]["power"], 2300)
        self.assertAlmostEqual(values["L1"]["current"], 10)
        self.assertAlmostEqual(values["L2"]["current"], 10)
        self.assertAlmostEqual(values["L3"]["current"], 10)

    def test_rpc_call_bool_is_lowercase_query_param(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.host = "192.168.178.76"
        service._request = MagicMock(return_value={"was_on": True})

        service.rpc_call("Switch.Set", id=0, on=False)

        service._request.assert_called_once_with(
            "http://192.168.178.76/rpc/Switch.Set?id=0&on=false"
        )

    def test_fetch_device_info_with_fallback_returns_empty_dict_after_retries(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.startup_device_info_retries = 2
        service.startup_device_info_retry_seconds = 0
        service.fetch_rpc = MagicMock(side_effect=RuntimeError("offline"))

        self.assertEqual(service._fetch_device_info_with_fallback(), {})
        self.assertEqual(service.fetch_rpc.call_count, 3)

    def test_handle_write_startstop_switches_relay_and_updates_dbus(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 0
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 0
        service.virtual_set_current = 16.0
        service.max_current = 16.0
        service.min_current = 6.0
        service.auto_manual_override_seconds = 300
        service.manual_override_until = 0
        service._dbusservice = {"/StartStop": 0, "/Enable": 0}
        service._queue_relay_command = MagicMock()
        service._publish_local_pm_status = MagicMock()

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            result = service._handle_write("/StartStop", 1)

        self.assertTrue(result)
        service._queue_relay_command.assert_called_once_with(True, 100.0)
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)
        self.assertEqual(service.virtual_startstop, 1)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)
        self.assertEqual(service.manual_override_until, 400.0)

    def test_handle_write_enable_in_manual_mode_switches_relay_and_updates_dbus(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 0
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 0
        service.virtual_set_current = 16.0
        service.max_current = 16.0
        service.min_current = 6.0
        service.auto_manual_override_seconds = 300
        service.manual_override_until = 0
        service._dbusservice = {"/StartStop": 0, "/Enable": 0}
        service._queue_relay_command = MagicMock()
        service._publish_local_pm_status = MagicMock()

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            result = service._handle_write("/Enable", 1)

        self.assertTrue(result)
        service._queue_relay_command.assert_called_once_with(True, 100.0)
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)
        self.assertEqual(service.virtual_startstop, 1)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)
        self.assertEqual(service.manual_override_until, 400.0)

    def test_handle_write_enable_in_auto_mode_does_not_force_relay_on(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 0
        service.virtual_set_current = 16.0
        service.max_current = 16.0
        service.min_current = 6.0
        service.auto_manual_override_seconds = 300
        service.manual_override_until = 0
        service._dbusservice = {"/StartStop": 0, "/Enable": 0}
        service._queue_relay_command = MagicMock()

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            result = service._handle_write("/Enable", 1)

        self.assertTrue(result)
        service._queue_relay_command.assert_not_called()
        self.assertEqual(service.virtual_startstop, 0)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)
        self.assertEqual(service.manual_override_until, 0)

    def test_handle_write_startstop_in_auto_mode_does_not_force_relay_on(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 0
        service.virtual_set_current = 16.0
        service.max_current = 16.0
        service.min_current = 6.0
        service.auto_manual_override_seconds = 300
        service.manual_override_until = 0
        service._dbusservice = {"/StartStop": 0, "/Enable": 0}
        service._queue_relay_command = MagicMock()

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            result = service._handle_write("/StartStop", 1)

        self.assertTrue(result)
        service._queue_relay_command.assert_not_called()
        self.assertEqual(service.virtual_startstop, 0)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)
        self.assertEqual(service.manual_override_until, 0)

    def test_handle_write_mode_updates_dbus_immediately(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 1
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = 200.0
        service.auto_samples = deque([(205.0, 2200.0, -2200.0)])
        service._dbusservice = {"/Mode": 1, "/StartStop": 1}

        result = service._handle_write("/Mode", 0)

        self.assertTrue(result)
        self.assertEqual(service.virtual_mode, 0)
        self.assertEqual(service._dbusservice["/Mode"], 0)
        self.assertEqual(service._dbusservice["/StartStop"], 0)
        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertEqual(len(service.auto_samples), 0)

    def test_handle_write_mode_updates_startstop_display_when_switching_to_auto(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 0
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 1
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service._dbusservice = {"/Mode": 0, "/StartStop": 0, "/Enable": 1}

        result = service._handle_write("/Mode", 1)

        self.assertTrue(result)
        self.assertEqual(service.virtual_mode, 1)
        self.assertEqual(service._dbusservice["/Mode"], 1)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)

    def test_handle_write_mode_preserves_scheduled_value_and_uses_auto_display(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 0
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 1
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service._dbusservice = {"/Mode": 0, "/StartStop": 0, "/Enable": 1}

        result = service._handle_write("/Mode", 2)

        self.assertTrue(result)
        self.assertEqual(service.virtual_mode, 2)
        self.assertEqual(service._dbusservice["/Mode"], 2)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)

    def test_handle_write_mode_switching_from_manual_to_auto_queues_clean_cutover(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 0
        service.virtual_autostart = 1
        service.virtual_startstop = 1
        service.virtual_enable = 0
        service.auto_manual_override_seconds = 300
        service.manual_override_until = 500.0
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = 100.0
        service._dbusservice = {"/Mode": 0, "/StartStop": 1, "/Enable": 0}
        service._queue_relay_command = MagicMock()
        service._publish_local_pm_status = MagicMock()

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=200.0):
            result = service._handle_write("/Mode", 1)

        self.assertTrue(result)
        self.assertEqual(service.virtual_mode, 1)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.virtual_startstop, 0)
        self.assertEqual(service._dbusservice["/Mode"], 1)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)
        self.assertEqual(service.manual_override_until, 0.0)
        self.assertTrue(service._auto_mode_cutover_pending)
        self.assertFalse(service._ignore_min_offtime_once)
        service._queue_relay_command.assert_called_once_with(False, 200.0)
        service._publish_local_pm_status.assert_called_once_with(False, 200.0)

    def test_auto_mode_waits_for_cutover_off_before_restarting(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2300
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_grid_missing_stop_seconds = 60
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(105.0, 2400.0, -2400.0)])
        service.relay_last_changed_at = None
        service.relay_last_off_at = 200.0
        service._auto_mode_cutover_pending = True
        service._ignore_min_offtime_once = False
        service._last_health_reason = "init"
        service._last_health_code = 0
        service._last_grid_at = 110.0
        service._peek_pending_relay_command = MagicMock(return_value=(False, 200.0))

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=231.0):
            self.assertFalse(service._auto_decide_relay(False, 2400.0, 45.0, -2400.0))

        self.assertEqual(service._last_health_reason, "mode-transition")
        self.assertTrue(service._auto_mode_cutover_pending)

    def test_auto_mode_can_restart_after_cutover_without_waiting_min_offtime(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2300
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_grid_missing_stop_seconds = 60
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(105.0, 2400.0, -2400.0)])
        service.relay_last_changed_at = None
        service.relay_last_off_at = 200.0
        service._auto_mode_cutover_pending = True
        service._ignore_min_offtime_once = False
        service._last_health_reason = "init"
        service._last_health_code = 0
        service._last_grid_at = 110.0
        service._last_pm_status_confirmed = True
        service._last_confirmed_pm_status = {"output": False}
        service._last_confirmed_pm_status_at = 110.5
        service._relay_sync_requested_at = 110.0
        service._peek_pending_relay_command = MagicMock(return_value=(None, None))

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=111.0):
            self.assertFalse(service._auto_decide_relay(False, 2400.0, 45.0, -2400.0))
        self.assertFalse(service._auto_mode_cutover_pending)
        self.assertTrue(service._ignore_min_offtime_once)

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=122.0):
            self.assertTrue(service._auto_decide_relay(False, 2400.0, 45.0, -2400.0))

        self.assertFalse(service._ignore_min_offtime_once)

    def test_auto_mode_can_stop_running_charge_when_pv_missing_but_soc_is_too_low(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 1
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2300
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 0
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.auto_manual_override_seconds = 300
        service.manual_override_until = 0.0
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service._last_health_reason = "init"
        service._last_health_code = 0
        service._auto_cached_inputs_used = False

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=200.0):
            self.assertTrue(service._auto_decide_relay(True, None, 10.0, 500.0))
        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=231.0):
            self.assertFalse(service._auto_decide_relay(True, None, 10.0, 500.0))

    def test_auto_mode_can_stop_running_charge_when_pv_missing_but_grid_import_is_high(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 1
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = True
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2300
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 0
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.auto_manual_override_seconds = 300
        service.manual_override_until = 0.0
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service._last_health_reason = "init"
        service._last_health_code = 0
        service._auto_cached_inputs_used = False
        service._last_battery_allow_warning = None
        service.auto_battery_scan_interval_seconds = 60

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=200.0):
            self.assertTrue(service._auto_decide_relay(True, None, 40.0, 500.0))
        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=231.0):
            self.assertFalse(service._auto_decide_relay(True, None, 40.0, 500.0))

    def test_update_virtual_state_keeps_enable_separate_from_relay_state(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.charging_started_at = None
        service.energy_at_start = 0.0
        service.last_status = 0
        service.virtual_mode = 0
        service.virtual_autostart = 1
        service.virtual_startstop = 1
        service.virtual_enable = 1
        service.virtual_set_current = 16.0
        service.min_current = 6.0
        service.max_current = 16.0
        service._dbusservice = {}

        service._update_virtual_state(6, 1.23, False)

        self.assertEqual(service._dbusservice["/Status"], 6)
        self.assertEqual(service._dbusservice["/StartStop"], 0)
        self.assertEqual(service._dbusservice["/Enable"], 1)

    def test_update_virtual_state_keeps_active_session_when_relay_stays_on(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.charging_started_at = 100.0
        service.energy_at_start = 5.0
        service.last_status = 2
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 1
        service.virtual_enable = 1
        service.virtual_set_current = 16.0
        service.min_current = 6.0
        service.max_current = 16.0
        service._dbusservice = {}

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=130.0):
            service._update_virtual_state(6, 6.25, True)

        self.assertEqual(service._dbusservice["/Status"], 6)
        self.assertEqual(service._dbusservice["/ChargingTime"], 30)
        self.assertEqual(service._dbusservice["/Session/Time"], 30)
        self.assertEqual(service._dbusservice["/Session/Energy"], 1.25)
        self.assertEqual(service.charging_started_at, 100.0)

    def test_update_virtual_state_keeps_startstop_enabled_in_auto_while_waiting(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.charging_started_at = None
        service.energy_at_start = 0.0
        service.last_status = 0
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 1
        service.virtual_set_current = 16.0
        service.min_current = 6.0
        service.max_current = 16.0
        service._dbusservice = {}

        service._update_virtual_state(4, 1.23, False)

        self.assertEqual(service._dbusservice["/Status"], 4)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)

    def test_update_virtual_state_keeps_startstop_visible_in_auto_when_relay_is_on(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.charging_started_at = None
        service.energy_at_start = 0.0
        service.last_status = 0
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 0
        service.virtual_set_current = 16.0
        service.min_current = 6.0
        service.max_current = 16.0
        service._dbusservice = {}

        service._update_virtual_state(2, 1.23, True)

        self.assertEqual(service._dbusservice["/Status"], 2)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 0)

    def test_auto_mode_starts_after_surplus_delay(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(105.0, 2200.0, -2200.0)])
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=111.0):
            self.assertTrue(service._auto_decide_relay(False, 2200, 45, -2200))

    def test_auto_mode_stops_after_import_delay(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = 200.0
        service.auto_samples = deque([(205.0, 1500.0, 400.0)])
        service.relay_last_changed_at = -100.0
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=231.0):
            self.assertFalse(service._auto_decide_relay(True, 1500, 45, 400))

    def test_auto_mode_stops_running_charge_when_grid_value_missing_for_too_long(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 0
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_grid_missing_stop_seconds = 60
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0
        service._last_grid_at = 100.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=161.0):
            self.assertTrue(service._auto_decide_relay(True, 0.0, 45.0, None))
        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=192.0):
            self.assertFalse(service._auto_decide_relay(True, 0.0, 45.0, None))
        self.assertEqual(service._last_health_reason, "grid-missing")

    def test_auto_mode_resumes_normal_start_logic_when_grid_value_is_fresh_again(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_grid_missing_stop_seconds = 60
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(105.0, 2200.0, -2200.0)])
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0
        service._last_grid_at = 110.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=111.0):
            self.assertTrue(service._auto_decide_relay(False, 2200, 45, -2200))

    def test_auto_mode_forces_relay_off_when_disabled(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_enable = 0
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = 200.0
        service.auto_samples = deque([(205.0, 2200.0, -2200.0)])

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=231.0):
            self.assertFalse(service._auto_decide_relay(True, 2200, 45, -2200))

        self.assertEqual(service._last_health_reason, "disabled")
        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertEqual(len(service.auto_samples), 0)

    def test_manual_mode_ignores_auto_enable_disable_state(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 0
        service.virtual_autostart = 1
        service.virtual_enable = 0
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = 200.0
        service.auto_samples = deque([(205.0, 2200.0, -2200.0)])

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=231.0):
            self.assertTrue(service._auto_decide_relay(True, 2200, 45, -2200))

        self.assertIsNone(service.auto_start_condition_since)
        self.assertIsNone(service.auto_stop_condition_since)
        self.assertEqual(len(service.auto_samples), 0)

    def test_auto_mode_does_not_start_below_min_soc(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(105.0, 2200.0, -2200.0)])
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=111.0):
            self.assertFalse(service._auto_decide_relay(False, 2200, 25, -2200))

    def test_auto_mode_allows_missing_battery_soc_when_enabled(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_allow_without_battery_soc = True
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(105.0, 2200.0, -2200.0)])
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service._last_battery_allow_warning = None
        service.auto_battery_scan_interval_seconds = 60
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=111.0):
            self.assertTrue(service._auto_decide_relay(False, 2200, None, -2200))

    def test_auto_mode_does_not_restart_until_resume_soc(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(105.0, 2200.0, -2200.0)])
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=111.0):
            self.assertFalse(service._auto_decide_relay(False, 2200, 31, -2200))

    def test_auto_mode_keeps_running_inside_hysteresis_band(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(205.0, 1800.0, -1800.0)])
        service.relay_last_changed_at = 0.0
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=211.0):
            self.assertTrue(service._auto_decide_relay(True, 1800, 31, -1800))

    def test_auto_mode_does_not_stop_before_min_runtime(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(110.0, 1200.0, 500.0)])
        service.relay_last_changed_at = 100.0
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=200.0):
            self.assertTrue(service._auto_decide_relay(True, 1200, 45, 500))

    def test_auto_mode_does_not_start_when_average_grid_import_is_too_high(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(105.0, 2200.0, 150.0)])
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=111.0):
            self.assertFalse(service._auto_decide_relay(False, 2200, 45, 150))

    def test_auto_mode_does_not_restart_before_min_offtime(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(105.0, 2200.0, -2200.0)])
        service.relay_last_changed_at = 100.0
        service.relay_last_off_at = 100.0
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=111.0):
            self.assertFalse(service._auto_decide_relay(False, 2200, 45, -2200))

    def test_auto_mode_waits_during_startup_warmup(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 100.0
        service.auto_startup_warmup_seconds = 30.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=110.0):
            self.assertFalse(service._auto_decide_relay(False, 2200, 45, -2200))
        self.assertEqual(service._last_health_reason, "warmup")

    def test_auto_mode_respects_manual_override_holdoff(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 200.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=150.0):
            self.assertFalse(service._auto_decide_relay(False, 2200, 45, -2200))
        self.assertEqual(service._last_health_reason, "manual-override")

    def test_auto_mode_sets_specific_waiting_reason_for_daytime(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0
        service._is_within_auto_daytime_window = MagicMock(return_value=False)

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=150.0):
            self.assertFalse(service._auto_decide_relay(False, 2200, 45, -2200))
        self.assertEqual(service._last_health_reason, "waiting-daytime")

    def test_update_uses_cached_inputs_and_updates_observability_paths(self):
        service = self._make_update_service()
        self._set_worker_snapshot(service, captured_at=100.0, pm_captured_at=100.0, pm_status={
            "output": False,
            "apower": 0.0,
            "voltage": 230.0,
            "current": 0.0,
            "aenergy": {"total": 1000.0},
        })
        service._auto_decide_relay = MagicMock(return_value=False)
        service._last_pv_value = 2400.0
        service._last_pv_at = 90.0
        service._last_grid_value = -2100.0
        service._last_grid_at = 91.0
        service._last_battery_soc_value = 56.0
        service._last_battery_soc_at = 92.0
        service._last_dbus_ok_at = 95.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            self.assertTrue(service._update())

        service._auto_decide_relay.assert_called_once_with(False, 2400.0, 56.0, -2100.0)
        self.assertEqual(service._error_state["cache_hits"], 1)
        self.assertEqual(service._dbusservice["/Auto/InputCacheHits"], 1)
        self.assertEqual(service._dbusservice["/Auto/LastPvReadAge"], 10)
        self.assertEqual(service._dbusservice["/Auto/LastBatteryReadAge"], 8)
        self.assertEqual(service._dbusservice["/Auto/LastGridReadAge"], 9)
        self.assertEqual(service._dbusservice["/Auto/LastDbusReadAge"], 5)

    def test_update_uses_source_specific_snapshot_timestamps_for_ages(self):
        service = self._make_update_service()
        self._set_worker_snapshot(
            service,
            captured_at=100.0,
            pm_captured_at=95.0,
            pm_confirmed=True,
            pm_status={
                "output": False,
                "apower": 0.0,
                "voltage": 230.0,
                "current": 0.0,
                "aenergy": {"total": 1000.0},
            },
            pv_power=2400.0,
            pv_captured_at=98.0,
            battery_soc=56.0,
            battery_captured_at=85.0,
            grid_power=-2100.0,
            grid_captured_at=96.0,
            auto_mode_active=True,
        )
        service._auto_decide_relay = MagicMock(return_value=False)

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            self.assertTrue(service._update())

        self.assertEqual(service._dbusservice["/Auto/LastShellyReadAge"], 5)
        self.assertEqual(service._dbusservice["/Auto/LastPvReadAge"], 2)
        self.assertEqual(service._dbusservice["/Auto/LastBatteryReadAge"], 15)
        self.assertEqual(service._dbusservice["/Auto/LastGridReadAge"], 4)

    def test_diagnostics_keep_last_confirmed_shelly_age_after_local_placeholder_publish(self):
        service = self._make_update_service()
        service._last_confirmed_pm_status = {"output": False}
        service._last_confirmed_pm_status_at = 95.0
        service._last_pm_status = {"output": False}
        service._last_pm_status_at = 95.0
        service._last_pm_status_confirmed = True

        service._publish_local_pm_status(True, 100.0)
        service._publish_diagnostic_paths(100.0)

        self.assertFalse(service._last_pm_status_confirmed)
        self.assertEqual(service._dbusservice["/Auto/LastShellyReadAge"], 5)

    def test_diagnostics_fall_back_to_last_confirmed_flagged_pm_age_when_no_confirmed_snapshot_is_stored(self):
        service = self._make_update_service()
        service._last_confirmed_pm_status = None
        service._last_confirmed_pm_status_at = None
        service._last_pm_status = {"output": False}
        service._last_pm_status_at = 95.0
        service._last_pm_status_confirmed = True

        service._publish_diagnostic_paths(100.0)

        self.assertEqual(service._dbusservice["/Auto/LastShellyReadAge"], 5)

    def test_diagnostics_normalize_invalid_auto_state_pair_before_publish(self):
        service = self._make_update_service()
        service._last_auto_state = "mystery"
        service._last_auto_state_code = 99

        service._publish_diagnostic_paths(100.0)

        self.assertEqual(service._dbusservice["/Auto/State"], "idle")
        self.assertEqual(service._dbusservice["/Auto/StateCode"], 0)

    def test_diagnostics_ignore_future_confirmed_shelly_timestamps(self):
        service = self._make_update_service()
        service._last_confirmed_pm_status = {"output": False}
        service._last_confirmed_pm_status_at = 105.0
        service._last_pm_status = {"output": False}
        service._last_pm_status_at = 106.0
        service._last_pm_status_confirmed = True

        service._publish_diagnostic_paths(100.0)

        self.assertEqual(service._dbusservice["/Auto/LastShellyReadAge"], -1)

    def test_diagnostics_publish_backend_mode_and_charger_target_visibility(self):
        service = self._make_update_service()
        service.backend_mode = "split"
        service.meter_backend_type = "template_meter"
        service.switch_backend_type = "template_switch"
        service.charger_backend_type = "template_charger"
        service._error_state["charger"] = 2
        service._charger_target_current_amps = 13.0
        service._charger_target_current_applied_at = 96.0

        service._publish_diagnostic_paths(100.0)

        self.assertEqual(service._dbusservice["/Auto/BackendMode"], "split")
        self.assertEqual(service._dbusservice["/Auto/MeterBackend"], "template_meter")
        self.assertEqual(service._dbusservice["/Auto/SwitchBackend"], "template_switch")
        self.assertEqual(service._dbusservice["/Auto/ChargerBackend"], "template_charger")
        self.assertEqual(service._dbusservice["/Auto/StatusSource"], "unknown")
        self.assertEqual(service._dbusservice["/Auto/ChargerFaultActive"], 0)
        self.assertEqual(service._dbusservice["/Auto/ChargerWriteErrors"], 2)
        self.assertEqual(service._dbusservice["/Auto/ChargerCurrentTarget"], 13.0)
        self.assertEqual(service._dbusservice["/Auto/ChargerCurrentTargetAge"], 4)
        self.assertEqual(service._dbusservice["/Auto/ErrorCount"], 2)

    def test_stale_helper_snapshot_prevents_auto_start_and_marks_grid_missing(self):
        service = self._make_update_service()
        service._refresh_auto_input_snapshot = ShellyWallboxService._refresh_auto_input_snapshot.__get__(
            service,
            ShellyWallboxService,
        )
        service.auto_allow_without_battery_soc = True
        service.auto_resume_soc = 33.0
        service.auto_min_soc = 30.0
        service.auto_start_surplus_watts = 1500.0
        service.auto_stop_surplus_watts = 1100.0
        service.auto_average_window_seconds = 30.0
        service.auto_min_runtime_seconds = 300.0
        service.auto_min_offtime_seconds = 120.0
        service.auto_start_max_grid_import_watts = 50.0
        service.auto_stop_grid_import_watts = 300.0
        service.auto_start_delay_seconds = 10.0
        service.auto_stop_delay_seconds = 30.0
        service.auto_allow_without_battery_soc = True
        service._queue_relay_command = MagicMock()
        self._set_worker_snapshot(
            service,
            captured_at=125.0,
            pm_captured_at=125.0,
            pm_confirmed=True,
            pm_status={
                "output": False,
                "apower": 0.0,
                "voltage": 230.0,
                "current": 0.0,
                "aenergy": {"total": 1000.0},
            },
        )
        helper_snapshot = {
            "snapshot_version": 1,
            "captured_at": 100.0,
            "heartbeat_at": 100.0,
            "pv_captured_at": 100.0,
            "pv_power": 2600.0,
            "battery_captured_at": 100.0,
            "battery_soc": 58.0,
            "grid_captured_at": 100.0,
            "grid_power": -2200.0,
        }
        stat_result = MagicMock()
        stat_result.st_mtime_ns = 7

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=130.0):
            with unittest.mock.patch("dbus_shelly_wallbox.os.stat", return_value=stat_result):
                with unittest.mock.patch(
                    "dbus_shelly_wallbox.open",
                    unittest.mock.mock_open(read_data=json.dumps(helper_snapshot)),
                ):
                    self.assertTrue(service._update())

        service._queue_relay_command.assert_not_called()
        self.assertEqual(service._last_health_reason, "grid-missing")
        self.assertEqual(service._dbusservice["/Auto/Health"], "grid-missing")
        self.assertEqual(service._dbusservice["/Status"], 4)

    def test_update_shelly_offline_increments_error_counter_and_sets_age(self):
        service = self._make_update_service()
        service.virtual_mode = 0
        service.virtual_startstop = 1
        service._last_pm_status = None
        service._last_pm_status_at = None
        service._error_state["shelly"] = 1

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            self.assertTrue(service._update())

        self.assertEqual(service._error_state["shelly"], 1)
        self.assertEqual(service._dbusservice["/Auto/ShellyReadErrors"], 1)
        self.assertEqual(service._dbusservice["/Auto/ErrorCount"], 1)
        self.assertEqual(service._dbusservice["/Auto/LastShellyReadAge"], -1)
        self.assertEqual(service._dbusservice["/Auto/Health"], "shelly-offline")

    def test_update_keeps_live_measurements_when_auto_switch_command_fails(self):
        service = self._make_update_service()
        service.virtual_mode = 1
        service.virtual_startstop = 1
        service.virtual_enable = 1
        self._set_worker_snapshot(service, captured_at=100.0, pm_status={
            "output": True,
            "apower": 1980.0,
            "voltage": 230.0,
            "current": 8.6,
            "aenergy": {"total": 1000.0},
        }, pv_power=0.0, battery_soc=50.0, grid_power=500.0, auto_mode_active=True)
        service._auto_decide_relay = MagicMock(return_value=False)
        service._queue_relay_command = MagicMock(side_effect=RuntimeError("switch failed"))

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            self.assertTrue(service._update())

        service._queue_relay_command.assert_called_once_with(False, 100.0)
        self.assertEqual(service._dbusservice["/Ac/Power"], 1980.0)
        self.assertEqual(service._dbusservice["/Ac/Voltage"], 230.0)
        self.assertEqual(service._dbusservice["/Status"], 2)
        self.assertEqual(service._dbusservice["/Auto/ShellyReadErrors"], 1)

    def test_update_applies_startup_manual_target_from_config(self):
        service = self._make_update_service()
        service.virtual_mode = 0
        service.virtual_startstop = 1
        service.virtual_enable = 1
        service._startup_manual_target = True
        self._set_worker_snapshot(service, captured_at=100.0, pm_status={
            "output": False,
            "apower": 0.0,
            "voltage": 230.0,
            "current": 0.0,
            "aenergy": {"total": 1000.0},
        })
        service._queue_relay_command = MagicMock()

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            self.assertTrue(service._update())

        service._queue_relay_command.assert_called_once_with(True, 100.0)
        self.assertEqual(service._dbusservice["/Mode"], 0)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)
        self.assertIsNone(service._startup_manual_target)

    def test_update_uses_worker_snapshot_instead_of_direct_blocking_reads(self):
        service = self._make_update_service()
        self._set_worker_snapshot(service, captured_at=100.0, pm_status={
            "output": True,
            "apower": 1800.0,
            "voltage": 230.0,
            "current": 7.8,
            "aenergy": {"total": 1000.0},
        }, pv_power=2200.0, battery_soc=55.0, grid_power=-2000.0, auto_mode_active=True)
        service.fetch_pm_status = MagicMock(side_effect=AssertionError("main loop must not poll Shelly directly"))
        service._get_pv_power = MagicMock(side_effect=AssertionError("main loop must not poll PV directly"))
        service._get_battery_soc = MagicMock(side_effect=AssertionError("main loop must not poll battery directly"))
        service._get_grid_power = MagicMock(side_effect=AssertionError("main loop must not poll grid directly"))
        service._auto_decide_relay = MagicMock(return_value=True)

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            self.assertTrue(service._update())

        service._auto_decide_relay.assert_called_once_with(True, 2200.0, 55.0, -2000.0)

    def test_watchdog_marks_update_as_stale(self):
        service = self._make_update_service()
        service._last_successful_update_at = 100.0
        service.started_at = 0.0
        service.auto_watchdog_stale_seconds = 30.0
        service._dbusservice = {}

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=150.0):
            service._update_virtual_state(6, 0.0, False)

        self.assertEqual(service._dbusservice["/Auto/Stale"], 1)
        self.assertEqual(service._dbusservice["/Auto/StaleSeconds"], 50)
        self.assertEqual(service._dbusservice["/Auto/LastSuccessfulUpdateAge"], 50)

    def test_watchdog_can_be_disabled_with_zero_stale_seconds(self):
        service = self._make_update_service()
        service._last_successful_update_at = None
        service.started_at = 0.0
        service.auto_watchdog_stale_seconds = 0.0
        service._reset_system_bus = MagicMock()
        service._invalidate_auto_pv_services = MagicMock()
        service._invalidate_auto_battery_service = MagicMock()

        self.assertFalse(service._is_update_stale(500.0))
        service._watchdog_recover(500.0)

        self.assertEqual(service._recovery_attempts, 0)
        service._reset_system_bus.assert_not_called()
        service._invalidate_auto_pv_services.assert_not_called()
        service._invalidate_auto_battery_service.assert_not_called()

    def test_watchdog_age_uses_zero_timestamp_as_valid_success_time(self):
        service = self._make_update_service()
        service._last_successful_update_at = 0.0
        service.started_at = 10.0
        service.auto_watchdog_stale_seconds = 30.0
        service._dbusservice = {}

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=50.0):
            service._update_virtual_state(6, 0.0, False)

        self.assertEqual(service._dbusservice["/Auto/LastSuccessfulUpdateAge"], 50)
        self.assertEqual(service._dbusservice["/Auto/StaleSeconds"], 50)

    def test_watchdog_recovery_resets_bus_and_invalidates_cached_services(self):
        service = self._make_update_service()
        service._last_successful_update_at = 100.0
        service.started_at = 0.0
        service.auto_watchdog_stale_seconds = 30.0
        service.auto_watchdog_recovery_seconds = 60.0
        service._reset_system_bus = MagicMock()
        service._invalidate_auto_pv_services = MagicMock()
        service._invalidate_auto_battery_service = MagicMock()
        service._dbus_list_backoff_until = 999.0

        service._watchdog_recover(200.0)

        self.assertEqual(service._recovery_attempts, 1)
        self.assertEqual(service._last_recovery_attempt_at, 200.0)
        self.assertEqual(service._dbus_list_backoff_until, 0.0)
        service._reset_system_bus.assert_called_once()
        service._invalidate_auto_pv_services.assert_called_once()
        service._invalidate_auto_battery_service.assert_called_once()

    def test_auto_daytime_window(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.auto_daytime_only = True
        service.auto_month_windows = {
            1: ((9, 0), (16, 30)),
            7: ((7, 0), (21, 0)),
        }

        self.assertFalse(service._is_within_auto_daytime_window(datetime(2026, 1, 3, 8, 30)))
        self.assertTrue(service._is_within_auto_daytime_window(datetime(2026, 1, 3, 10, 0)))
        self.assertFalse(service._is_within_auto_daytime_window(datetime(2026, 7, 3, 6, 30)))
        self.assertTrue(service._is_within_auto_daytime_window(datetime(2026, 7, 3, 20, 30)))

    def test_list_dbus_services(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service._get_system_bus = MagicMock()
        dbus_shelly_wallbox.dbus.Interface.return_value.ListNames.return_value = [
            "com.victronenergy.system",
            "com.victronenergy.pvinverter.http_40",
        ]

        self.assertEqual(
            service._list_dbus_services(),
            ["com.victronenergy.system", "com.victronenergy.pvinverter.http_40"],
        )


if __name__ == "__main__":
    unittest.main()
