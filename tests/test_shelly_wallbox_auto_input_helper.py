# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the wallbox Auto input helper process."""

import json
import os
import runpy
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import MagicMock, patch

sys.modules["dbus"] = MagicMock()

import shelly_wallbox_auto_input_helper  # noqa: E402
from shelly_wallbox_auto_input_helper import AutoInputHelper, _as_bool  # noqa: E402


class TestShellyWallboxAutoInputHelper(unittest.TestCase):
    def test_as_bool_parses_truthy_and_defaults(self):
        self.assertTrue(_as_bool("yes"))
        self.assertFalse(_as_bool("0"))
        self.assertTrue(_as_bool(None, default=True))

    def test_init_raises_for_missing_or_invalid_config(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: os.path.isdir(temp_dir) and os.rmdir(temp_dir))
        config_path = os.path.join(temp_dir, "missing.ini")
        with self.assertRaises(ValueError):
            AutoInputHelper(config_path)

    def test_init_uses_dedicated_auto_input_poll_interval_when_configured(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(
                "[DEFAULT]\n"
                "PollIntervalMs=1000\n"
                "AutoInputPollIntervalMs=2000\n"
                "AutoPvPollIntervalMs=2000\n"
                "AutoGridPollIntervalMs=2000\n"
                "AutoBatteryPollIntervalMs=10000\n"
                "AutoInputSnapshotPath=/tmp/helper.json\n"
            )
            config_path = handle.name

        self.addCleanup(lambda: os.path.exists(config_path) and os.unlink(config_path))
        helper = AutoInputHelper(config_path)
        self.assertEqual(helper.poll_interval_seconds, 2.0)
        self.assertEqual(helper.auto_pv_poll_interval_seconds, 2.0)
        self.assertEqual(helper.auto_grid_poll_interval_seconds, 2.0)
        self.assertEqual(helper.auto_battery_poll_interval_seconds, 10.0)

    def _make_helper(self):
        helper = AutoInputHelper.__new__(AutoInputHelper)
        helper.config = {}
        helper._warning_state = {}
        helper._source_retry_after = {}
        helper._system_bus = None
        helper._dbus_list_backoff_until = 0.0
        helper._dbus_list_failures = 0
        helper._resolved_auto_pv_services = []
        helper._auto_pv_last_scan = 0.0
        helper._resolved_auto_battery_service = None
        helper._auto_battery_last_scan = 0.0
        helper._last_payload = None
        helper._last_snapshot_state = AutoInputHelper._empty_snapshot()
        helper._next_source_poll_at = {"pv": 0.0, "battery": 0.0, "grid": 0.0}
        helper.poll_interval_seconds = 1.0
        helper.auto_pv_poll_interval_seconds = 2.0
        helper.auto_grid_poll_interval_seconds = 2.0
        helper.auto_battery_poll_interval_seconds = 10.0
        helper.auto_dbus_backoff_base_seconds = 5.0
        helper.auto_dbus_backoff_max_seconds = 60.0
        helper.auto_pv_service = ""
        helper.auto_pv_service_prefix = "com.victronenergy.pvinverter"
        helper.auto_pv_path = "/Ac/Power"
        helper.auto_pv_max_services = 10
        helper.auto_pv_scan_interval_seconds = 60.0
        helper.auto_use_dc_pv = True
        helper.auto_dc_pv_service = "com.victronenergy.system"
        helper.auto_dc_pv_path = "/Dc/Pv/Power"
        helper.auto_battery_service = "com.victronenergy.battery.socketcan_can1"
        helper.auto_battery_soc_path = "/Soc"
        helper.auto_battery_service_prefix = "com.victronenergy.battery"
        helper.auto_battery_scan_interval_seconds = 60.0
        helper.auto_grid_service = "com.victronenergy.system"
        helper.auto_grid_l1_path = "/Ac/Grid/L1/Power"
        helper.auto_grid_l2_path = "/Ac/Grid/L2/Power"
        helper.auto_grid_l3_path = "/Ac/Grid/L3/Power"
        helper.auto_grid_require_all_phases = True
        helper.dbus_method_timeout_seconds = 1.0
        return helper

    def test_derive_subscription_refresh_seconds_uses_smallest_positive_scan_interval(self):
        helper = AutoInputHelper.__new__(AutoInputHelper)
        helper.config = {
            "AutoPvScanIntervalSeconds": "45",
            "AutoBatteryScanIntervalSeconds": "15",
        }

        self.assertEqual(helper._derive_subscription_refresh_seconds(), 15.0)

    def test_handle_signal_sets_stop_flag_and_requests_idle_quit(self):
        helper = self._make_helper()
        helper._main_loop = MagicMock()

        with patch("shelly_wallbox_auto_input_helper.GLib.idle_add") as idle_add:
            helper._handle_signal(15, None)

        self.assertTrue(helper._stop_requested)
        idle_add.assert_called_once_with(helper._main_loop.quit)

    def test_warning_throttled_logs_once_per_interval(self):
        helper = self._make_helper()

        with patch("shelly_wallbox_auto_input_helper.time.time", side_effect=[100.0, 105.0, 131.0]):
            with patch("shelly_wallbox_auto_input_helper.logging.warning") as warning_mock:
                helper._warning_throttled("dbus", 30.0, "failed %s", "x")
                helper._warning_throttled("dbus", 30.0, "failed %s", "x")
                helper._warning_throttled("dbus", 30.0, "failed %s", "x")

        self.assertEqual(warning_mock.call_count, 2)

    def test_ensure_poll_state_populates_missing_defaults(self):
        helper = AutoInputHelper.__new__(AutoInputHelper)
        helper.poll_interval_seconds = 1.5

        helper._ensure_poll_state()

        self.assertEqual(helper.auto_pv_poll_interval_seconds, 1.5)
        self.assertEqual(helper.auto_grid_poll_interval_seconds, 1.5)
        self.assertEqual(helper.auto_battery_poll_interval_seconds, 1.5)
        self.assertIn("captured_at", helper._last_snapshot_state)
        self.assertEqual(helper._next_source_poll_at["pv"], 0.0)
        self.assertFalse(helper._refresh_scheduled)
        self.assertEqual(helper.subscription_refresh_seconds, 60.0)
        self.assertEqual(helper.validation_poll_seconds, 30.0)
        self.assertIsNone(helper._main_loop)
        self.assertFalse(helper._stop_requested)

    def test_ensure_poll_state_derives_poll_interval_from_source_intervals(self):
        helper = AutoInputHelper.__new__(AutoInputHelper)
        helper.auto_pv_poll_interval_seconds = 2.0
        helper.auto_grid_poll_interval_seconds = 3.0
        helper.auto_battery_poll_interval_seconds = 5.0

        helper._ensure_poll_state()

        self.assertEqual(helper.poll_interval_seconds, 2.0)

    def test_get_pv_power_returns_zero_when_no_ac_or_dc_pv_is_found(self):
        helper = self._make_helper()
        helper._resolve_auto_pv_services = MagicMock(return_value=[])
        helper._get_dbus_value = MagicMock(return_value=None)

        self.assertEqual(helper._get_pv_power(), 0.0)

    def test_get_pv_power_sums_dc_sequence_values(self):
        helper = self._make_helper()
        helper._resolve_auto_pv_services = MagicMock(return_value=[])
        helper._get_dbus_value = MagicMock(return_value=[400.0, 350.0])

        self.assertEqual(helper._get_pv_power(), 750.0)

    def test_get_grid_power_requires_all_phases(self):
        helper = self._make_helper()

        def fake_get_value(service_name, path):
            values = {
                "/Ac/Grid/L1/Power": -500.0,
                "/Ac/Grid/L2/Power": None,
                "/Ac/Grid/L3/Power": -400.0,
            }
            return values[path]

        helper._get_dbus_value = MagicMock(side_effect=fake_get_value)

        self.assertIsNone(helper._get_grid_power())

    def test_write_snapshot_uses_atomic_ram_file(self):
        helper = self._make_helper()
        with tempfile.TemporaryDirectory() as temp_dir:
            helper.snapshot_path = f"{temp_dir}/auto.json"
            helper._write_snapshot({"captured_at": 100.0, "pv_power": 0.0})

            with open(helper.snapshot_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)

        self.assertEqual(payload["captured_at"], 100.0)
        self.assertEqual(payload["pv_power"], 0.0)

    def test_collect_snapshot_polls_battery_less_often_than_pv_and_grid(self):
        helper = self._make_helper()
        helper._get_pv_power = MagicMock(side_effect=[2100.0, 2200.0])
        helper._get_battery_soc = MagicMock(side_effect=[55.0])
        helper._get_grid_power = MagicMock(side_effect=[-800.0, -750.0])

        first = helper._collect_snapshot(100.0)
        second = helper._collect_snapshot(102.0)

        self.assertEqual(first["pv_power"], 2100.0)
        self.assertEqual(first["battery_soc"], 55.0)
        self.assertEqual(first["grid_power"], -800.0)
        self.assertEqual(second["pv_power"], 2200.0)
        self.assertEqual(second["battery_soc"], 55.0)
        self.assertEqual(second["battery_captured_at"], 100.0)
        self.assertEqual(second["grid_power"], -750.0)
        self.assertEqual(second["captured_at"], 102.0)
        self.assertEqual(helper._get_pv_power.call_count, 2)
        self.assertEqual(helper._get_grid_power.call_count, 2)
        self.assertEqual(helper._get_battery_soc.call_count, 1)

    def test_collect_snapshot_clears_source_fields_when_getter_returns_none(self):
        helper = self._make_helper()
        helper._last_snapshot_state["pv_power"] = 2100.0
        helper._last_snapshot_state["pv_captured_at"] = 90.0
        helper._get_pv_power = MagicMock(return_value=None)
        helper._get_battery_soc = MagicMock(return_value=55.0)
        helper._get_grid_power = MagicMock(return_value=-800.0)

        snapshot = helper._collect_snapshot(100.0)

        self.assertIsNone(snapshot["pv_power"])
        self.assertIsNone(snapshot["pv_captured_at"])

    def test_parent_alive_uses_parent_pid_and_handles_errors(self):
        helper = self._make_helper()
        helper.parent_pid = 1234

        helper.parent_pid = None
        self.assertTrue(helper._parent_alive())
        helper.parent_pid = 1234

        with unittest.mock.patch("shelly_wallbox_auto_input_helper.os.getppid", return_value=1234):
            self.assertTrue(helper._parent_alive())

        with unittest.mock.patch("shelly_wallbox_auto_input_helper.os.getppid", return_value=9999):
            self.assertFalse(helper._parent_alive())

        with unittest.mock.patch("shelly_wallbox_auto_input_helper.os.getppid", side_effect=RuntimeError("boom")):
            self.assertFalse(helper._parent_alive())

    def test_set_source_value_updates_snapshot_and_ignores_unknown_source(self):
        helper = self._make_helper()
        helper._write_snapshot = MagicMock()

        helper._set_source_value("pv", 1200.0, 99.0)
        self.assertEqual(helper._last_snapshot_state["pv_power"], 1200.0)
        self.assertEqual(helper._last_snapshot_state["pv_captured_at"], 99.0)

        helper._set_source_value("battery", 55.0, 99.5)
        self.assertEqual(helper._last_snapshot_state["battery_soc"], 55.0)
        self.assertEqual(helper._last_snapshot_state["battery_captured_at"], 99.5)

        helper._set_source_value("grid", -750.0, 100.0)
        self.assertEqual(helper._last_snapshot_state["grid_power"], -750.0)
        self.assertEqual(helper._last_snapshot_state["grid_captured_at"], 100.0)
        self.assertEqual(helper._write_snapshot.call_count, 3)

        helper._write_snapshot.reset_mock()
        previous_state = dict(helper._last_snapshot_state)
        helper._set_source_value("unknown", 1.0, 101.0)
        self.assertEqual(helper._last_snapshot_state, previous_state)
        helper._write_snapshot.assert_not_called()

    def test_heartbeat_updates_only_helper_liveness_not_source_timestamps(self):
        helper = self._make_helper()
        helper._last_snapshot_state = {
            "captured_at": 100.0,
            "heartbeat_at": 100.0,
            "pv_captured_at": 100.0,
            "pv_power": 2100.0,
            "battery_captured_at": 100.0,
            "battery_soc": 55.0,
            "grid_captured_at": 100.0,
            "grid_power": -800.0,
        }
        helper._write_snapshot = MagicMock()

        with unittest.mock.patch("shelly_wallbox_auto_input_helper.time.time", return_value=130.0):
            helper._heartbeat_snapshot()

        self.assertEqual(helper._last_snapshot_state["heartbeat_at"], 130.0)
        self.assertEqual(helper._last_snapshot_state["captured_at"], 100.0)
        self.assertEqual(helper._last_snapshot_state["pv_captured_at"], 100.0)
        self.assertEqual(helper._last_snapshot_state["battery_captured_at"], 100.0)
        self.assertEqual(helper._last_snapshot_state["grid_captured_at"], 100.0)

    def test_refresh_source_and_validation_poll_use_expected_source_getters(self):
        helper = self._make_helper()
        helper._set_source_value = MagicMock()
        helper._get_pv_power = MagicMock(return_value=2100.0)
        helper._get_battery_soc = MagicMock(return_value=56.0)
        helper._get_grid_power = MagicMock(return_value=-800.0)

        helper._refresh_source("pv", 100.0)
        helper._refresh_source("battery", 101.0)
        helper._refresh_source("grid", 102.0)
        helper._refresh_source("unknown", 103.0)

        helper._set_source_value.assert_any_call("pv", 2100.0, 100.0)
        helper._set_source_value.assert_any_call("battery", 56.0, 101.0)
        helper._set_source_value.assert_any_call("grid", -800.0, 102.0)
        self.assertEqual(helper._set_source_value.call_count, 3)

        helper._refresh_all_sources = MagicMock()
        helper._stop_requested = False
        self.assertTrue(helper._validation_poll())
        helper._refresh_all_sources.assert_called_once_with()

        helper._stop_requested = True
        self.assertFalse(helper._validation_poll())

    def test_refresh_all_sources_uses_current_time_when_now_is_omitted(self):
        helper = self._make_helper()
        helper._refresh_source = MagicMock()

        with patch("shelly_wallbox_auto_input_helper.time.time", return_value=123.0):
            helper._refresh_all_sources()

        helper._refresh_source.assert_any_call("pv", 123.0)
        helper._refresh_source.assert_any_call("battery", 123.0)
        helper._refresh_source.assert_any_call("grid", 123.0)
        self.assertEqual(helper._refresh_source.call_count, 3)

    def test_get_pv_power_sums_ac_numeric_values_without_dc(self):
        helper = self._make_helper()
        helper.auto_use_dc_pv = False
        helper._resolve_auto_pv_services = MagicMock(return_value=["com.victronenergy.pvinverter.http_40"])
        helper._get_dbus_value = MagicMock(return_value=500.0)

        self.assertEqual(helper._get_pv_power(), 500.0)

    def test_helper_module_import_fallback_sets_dbus_glib_mainloop_to_none(self):
        helper_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "shelly_wallbox_auto_input_helper.py")
        fake_dbus = ModuleType("dbus")
        fake_glib = MagicMock()
        fake_gi = ModuleType("gi")
        fake_repository = ModuleType("gi.repository")
        fake_repository.GLib = fake_glib
        fake_gi.repository = fake_repository

        with patch.dict(
            sys.modules,
            {
                "dbus": fake_dbus,
                "gi": fake_gi,
                "gi.repository": fake_repository,
                "gi.repository.GLib": fake_glib,
            },
            clear=False,
        ):
            module_globals = runpy.run_path(helper_path, run_name="shelly_wallbox_auto_input_helper_import_test")

        self.assertIsNone(module_globals["dbus_glib_mainloop"])

    def test_refresh_subscriptions_rebuilds_desired_specs_and_refreshes_sources(self):
        helper = self._make_helper()
        helper._desired_subscription_specs = MagicMock(
            return_value=[
                ("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power"),
                ("grid", "com.victronenergy.system", "/Ac/Grid/L1/Power"),
            ]
        )
        helper._subscribe_busitem_path = MagicMock()
        helper._clear_missing_subscriptions = MagicMock()
        helper._refresh_all_sources = MagicMock()

        self.assertFalse(helper._refresh_subscriptions())

        helper._subscribe_busitem_path.assert_any_call("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power")
        helper._subscribe_busitem_path.assert_any_call("grid", "com.victronenergy.system", "/Ac/Grid/L1/Power")
        helper._clear_missing_subscriptions.assert_called_once()
        helper._refresh_all_sources.assert_called_once_with()

    def test_signal_spec_key_and_subscribe_busitem_path_deduplicate_subscriptions(self):
        helper = self._make_helper()
        bus = MagicMock()
        bus.add_signal_receiver.return_value = "match"
        helper._get_system_bus = MagicMock(return_value=bus)

        self.assertEqual(
            helper._signal_spec_key("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power"),
            ("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power"),
        )

        helper._subscribe_busitem_path("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power")
        helper._subscribe_busitem_path("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power")

        bus.add_signal_receiver.assert_called_once()
        self.assertIn(("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power"), helper._signal_matches)
        self.assertIn(("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power"), helper._monitored_specs)

    def test_clear_missing_subscriptions_removes_stale_entries_and_ignores_remove_errors(self):
        helper = self._make_helper()
        keep_key = ("pv", "svc", "/Ac/Power")
        drop_key = ("grid", "svc", "/Ac/Grid/L1/Power")
        helper._signal_matches = {
            keep_key: MagicMock(),
            drop_key: MagicMock(remove=MagicMock(side_effect=RuntimeError("boom"))),
        }
        helper._monitored_specs = {keep_key: {"x": 1}, drop_key: {"y": 2}}

        helper._clear_missing_subscriptions({keep_key})

        self.assertIn(keep_key, helper._signal_matches)
        self.assertNotIn(drop_key, helper._signal_matches)
        self.assertNotIn(drop_key, helper._monitored_specs)

    def test_desired_subscription_specs_combines_pv_battery_and_grid_sources(self):
        helper = self._make_helper()
        helper.auto_pv_service = "com.victronenergy.pvinverter.http_40"
        helper._resolve_auto_battery_service = MagicMock(return_value="com.victronenergy.battery.socketcan_can1")

        specs = helper._desired_subscription_specs()

        self.assertIn(("pv", "com.victronenergy.pvinverter.http_40", "/Ac/Power"), specs)
        self.assertIn(("pv", "com.victronenergy.system", "/Dc/Pv/Power"), specs)
        self.assertIn(("battery", "com.victronenergy.battery.socketcan_can1", "/Soc"), specs)
        self.assertIn(("grid", "com.victronenergy.system", "/Ac/Grid/L1/Power"), specs)

    def test_desired_subscription_specs_tolerates_resolution_errors(self):
        helper = self._make_helper()
        helper.auto_pv_service = ""
        helper._resolve_auto_pv_services = MagicMock(side_effect=RuntimeError("pv down"))
        helper._resolve_auto_battery_service = MagicMock(side_effect=RuntimeError("battery down"))

        specs = helper._desired_subscription_specs()

        self.assertIn(("pv", "com.victronenergy.system", "/Dc/Pv/Power"), specs)
        self.assertNotIn(("battery", "com.victronenergy.battery.socketcan_can1", "/Soc"), specs)

    def test_on_source_signal_logs_throttled_warning_on_refresh_error(self):
        helper = self._make_helper()
        helper._refresh_source = MagicMock(side_effect=RuntimeError("boom"))
        helper._warning_throttled = MagicMock()

        helper._on_source_signal("pv", sender="svc", path="/Ac/Power")

        helper._warning_throttled.assert_called_once()
        args = helper._warning_throttled.call_args[0]
        self.assertEqual(args[0], "auto-helper-source-signal-pv")

    def test_refresh_subscriptions_timer_requests_refresh_and_obeys_stop_flag(self):
        helper = self._make_helper()
        helper._schedule_refresh_subscriptions = MagicMock()
        helper._stop_requested = False
        self.assertTrue(helper._refresh_subscriptions_timer())
        helper._schedule_refresh_subscriptions.assert_called_once_with()

        helper._stop_requested = True
        self.assertFalse(helper._refresh_subscriptions_timer())

    def test_schedule_refresh_subscriptions_only_schedules_one_idle_callback(self):
        helper = self._make_helper()
        helper._refresh_subscriptions = MagicMock(return_value=False)
        callbacks = []

        with unittest.mock.patch(
            "shelly_wallbox_auto_input_helper.GLib.idle_add",
            side_effect=lambda callback: callbacks.append(callback),
        ):
            helper._schedule_refresh_subscriptions()
            helper._schedule_refresh_subscriptions()

        self.assertEqual(len(callbacks), 1)
        self.assertTrue(helper._refresh_scheduled)
        self.assertFalse(callbacks[0]())
        helper._refresh_subscriptions.assert_called_once_with()
        self.assertFalse(helper._refresh_scheduled)

    def test_on_name_owner_changed_schedules_refresh_only_for_relevant_services(self):
        helper = self._make_helper()
        helper._schedule_refresh_subscriptions = MagicMock()

        helper._on_name_owner_changed("com.victronenergy.system", "", ":1.5")
        helper._on_name_owner_changed("com.example.unrelated", "", ":1.6")

        helper._schedule_refresh_subscriptions.assert_called_once_with()

    def test_parent_watchdog_quits_mainloop_when_parent_disappears(self):
        helper = self._make_helper()
        helper._stop_requested = False
        helper._parent_alive = MagicMock(return_value=False)
        helper._main_loop = MagicMock()

        self.assertFalse(helper._parent_watchdog())
        helper._main_loop.quit.assert_called_once_with()

        helper = self._make_helper()
        helper._stop_requested = True
        helper._parent_alive = MagicMock(return_value=False)
        self.assertFalse(helper._parent_watchdog())

    def test_list_dbus_services_returns_empty_and_sets_backoff_on_failure(self):
        helper = self._make_helper()
        helper._reset_system_bus = MagicMock()
        helper._get_system_bus = MagicMock(return_value=MagicMock(get_object=MagicMock(return_value=object())))
        failing_interface = MagicMock()
        failing_interface.ListNames.side_effect = RuntimeError("dbus down")
        original_interface = shelly_wallbox_auto_input_helper.dbus.Interface
        shelly_wallbox_auto_input_helper.dbus.Interface = MagicMock(return_value=failing_interface)
        try:
            with unittest.mock.patch("shelly_wallbox_auto_input_helper.time.time", return_value=100.0):
                self.assertEqual(helper._list_dbus_services(), [])
        finally:
            shelly_wallbox_auto_input_helper.dbus.Interface = original_interface

        helper._reset_system_bus.assert_called_once_with()
        self.assertEqual(helper._dbus_list_failures, 1)
        self.assertEqual(helper._dbus_list_backoff_until, 105.0)

    def test_get_system_bus_always_caches_system_bus(self):
        helper = self._make_helper()
        original_session_bus = shelly_wallbox_auto_input_helper.dbus.SessionBus
        original_system_bus = shelly_wallbox_auto_input_helper.dbus.SystemBus
        try:
            shelly_wallbox_auto_input_helper.dbus.SessionBus = MagicMock(return_value="session-bus")
            shelly_wallbox_auto_input_helper.dbus.SystemBus = MagicMock(return_value="system-bus")
            with patch.dict("shelly_wallbox_auto_input_helper.os.environ", {"DBUS_SESSION_BUS_ADDRESS": "x"}, clear=True):
                self.assertEqual(helper._get_system_bus(), "system-bus")
                self.assertEqual(helper._get_system_bus(), "system-bus")
            helper._reset_system_bus()
            with patch.dict("shelly_wallbox_auto_input_helper.os.environ", {}, clear=True):
                self.assertEqual(helper._get_system_bus(), "system-bus")
            shelly_wallbox_auto_input_helper.dbus.SessionBus.assert_not_called()
        finally:
            shelly_wallbox_auto_input_helper.dbus.SessionBus = original_session_bus
            shelly_wallbox_auto_input_helper.dbus.SystemBus = original_system_bus

    def test_get_dbus_value_and_child_nodes_retry_after_reset(self):
        helper = self._make_helper()
        first_bus = MagicMock()
        second_bus = MagicMock()
        helper._get_system_bus = MagicMock(side_effect=[first_bus, second_bus, first_bus, second_bus])
        helper._reset_system_bus = MagicMock()
        first_bus.get_object.return_value = object()
        second_bus.get_object.return_value = object()
        read_interface = MagicMock()
        read_interface.GetValue.side_effect = [RuntimeError("boom"), 42.0]
        introspect_interface = MagicMock()
        introspect_interface.Introspect.side_effect = [
            RuntimeError("boom"),
            "<node><node name='L1'/><node name='L2'/></node>",
        ]
        original_interface = shelly_wallbox_auto_input_helper.dbus.Interface
        shelly_wallbox_auto_input_helper.dbus.Interface = MagicMock(
            side_effect=[read_interface, read_interface, introspect_interface, introspect_interface]
        )
        try:
            self.assertEqual(helper._get_dbus_value("svc", "/Path"), 42.0)
            self.assertEqual(helper._get_dbus_child_nodes("svc", "/Ac/Grid"), ["L1", "L2"])
        finally:
            shelly_wallbox_auto_input_helper.dbus.Interface = original_interface

        self.assertEqual(helper._reset_system_bus.call_count, 2)

    def test_get_dbus_value_and_child_nodes_raise_after_second_failure(self):
        helper = self._make_helper()
        helper._get_system_bus = MagicMock(return_value=MagicMock(get_object=MagicMock(return_value=object())))
        helper._reset_system_bus = MagicMock()
        failing_interface = MagicMock()
        failing_interface.GetValue.side_effect = RuntimeError("dbus read failed")
        failing_interface.Introspect.side_effect = RuntimeError("dbus introspect failed")
        original_interface = shelly_wallbox_auto_input_helper.dbus.Interface
        shelly_wallbox_auto_input_helper.dbus.Interface = MagicMock(return_value=failing_interface)
        try:
            with self.assertRaises(RuntimeError):
                helper._get_dbus_value("svc", "/Path")
            with self.assertRaises(RuntimeError):
                helper._get_dbus_child_nodes("svc", "/Path")
        finally:
            shelly_wallbox_auto_input_helper.dbus.Interface = original_interface

        self.assertEqual(helper._reset_system_bus.call_count, 4)

    def test_list_dbus_services_short_circuits_during_backoff_and_resets_on_success(self):
        helper = self._make_helper()
        with patch("shelly_wallbox_auto_input_helper.time.time", return_value=50.0):
            helper._dbus_list_backoff_until = 60.0
            self.assertEqual(helper._list_dbus_services(), [])

        helper._dbus_list_backoff_until = 0.0
        helper._dbus_list_failures = 2
        helper._get_system_bus = MagicMock(return_value=MagicMock(get_object=MagicMock(return_value=object())))
        dbus_iface = MagicMock()
        dbus_iface.ListNames.return_value = ["com.victronenergy.system"]
        original_interface = shelly_wallbox_auto_input_helper.dbus.Interface
        shelly_wallbox_auto_input_helper.dbus.Interface = MagicMock(return_value=dbus_iface)
        try:
            with patch("shelly_wallbox_auto_input_helper.time.time", return_value=100.0):
                self.assertEqual(helper._list_dbus_services(), ["com.victronenergy.system"])
        finally:
            shelly_wallbox_auto_input_helper.dbus.Interface = original_interface

        self.assertEqual(helper._dbus_list_failures, 0)
        self.assertEqual(helper._dbus_list_backoff_until, 0.0)

    def test_source_retry_helpers_and_invalidate_helpers(self):
        helper = self._make_helper()
        with patch("shelly_wallbox_auto_input_helper.time.time", side_effect=[100.0, 100.0]):
            self.assertTrue(helper._source_retry_ready("pv"))
            helper._delay_source_retry("pv")
        with patch("shelly_wallbox_auto_input_helper.time.time", return_value=100.0):
            self.assertFalse(helper._source_retry_ready("pv"))

        helper._resolved_auto_pv_services = ["svc"]
        helper._auto_pv_last_scan = 100.0
        helper._invalidate_auto_pv_services()
        self.assertEqual(helper._resolved_auto_pv_services, [])
        self.assertEqual(helper._auto_pv_last_scan, 0.0)

        helper._resolved_auto_battery_service = "battery"
        helper._auto_battery_last_scan = 100.0
        helper._invalidate_auto_battery_service()
        self.assertIsNone(helper._resolved_auto_battery_service)
        self.assertEqual(helper._auto_battery_last_scan, 0.0)

    def test_resolve_auto_pv_services_uses_discovery_cache(self):
        helper = self._make_helper()
        helper._list_dbus_services = MagicMock(
            return_value=[
                "com.victronenergy.system",
                "com.victronenergy.pvinverter.http_40",
                "com.victronenergy.pvinverter.http_41",
            ]
        )

        with unittest.mock.patch("shelly_wallbox_auto_input_helper.time.time", side_effect=[100.0, 120.0]):
            first = helper._resolve_auto_pv_services()
            second = helper._resolve_auto_pv_services()

        self.assertEqual(first, ["com.victronenergy.pvinverter.http_40", "com.victronenergy.pvinverter.http_41"])
        self.assertEqual(second, first)
        helper._list_dbus_services.assert_called_once_with()

    def test_resolve_auto_pv_services_uses_configured_service_directly(self):
        helper = self._make_helper()
        helper.auto_pv_service = "com.victronenergy.pvinverter.http_40"
        self.assertEqual(helper._resolve_auto_pv_services(), ["com.victronenergy.pvinverter.http_40"])

    def test_get_pv_power_covers_retry_guard_and_read_failures(self):
        helper = self._make_helper()
        helper._source_retry_after["pv"] = 200.0
        with patch("shelly_wallbox_auto_input_helper.time.time", return_value=100.0):
            self.assertIsNone(helper._get_pv_power())

        helper = self._make_helper()
        helper.auto_use_dc_pv = True
        helper.auto_pv_service = ""
        helper._resolve_auto_pv_services = MagicMock(side_effect=RuntimeError("scan failed"))
        helper._get_dbus_value = MagicMock(return_value=None)
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._get_pv_power())
        helper._delay_source_retry.assert_called_once_with("pv")

        helper = self._make_helper()
        helper.auto_use_dc_pv = True
        helper.auto_pv_service = "configured-pv"
        helper._resolve_auto_pv_services = MagicMock(return_value=["svc"])
        helper._get_dbus_value = MagicMock(side_effect=[RuntimeError("ac failed"), RuntimeError("dc failed")])
        helper._invalidate_auto_pv_services = MagicMock()
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._get_pv_power())
        helper._invalidate_auto_pv_services.assert_called_once_with()
        helper._delay_source_retry.assert_called_once_with("pv")

    def test_resolve_auto_battery_service_finds_prefixed_service_with_soc(self):
        helper = self._make_helper()
        helper.auto_battery_service = ""
        helper._list_dbus_services = MagicMock(
            return_value=[
                "com.victronenergy.system",
                "com.victronenergy.battery.socketcan_can0",
                "com.victronenergy.battery.socketcan_can1",
            ]
        )
        helper._battery_service_has_soc = MagicMock(side_effect=lambda service_name: service_name.endswith("can1"))

        with unittest.mock.patch("shelly_wallbox_auto_input_helper.time.time", return_value=100.0):
            self.assertEqual(
                helper._resolve_auto_battery_service(),
                "com.victronenergy.battery.socketcan_can1",
            )

    def test_resolve_auto_battery_service_covers_configured_service_cache_and_failure(self):
        helper = self._make_helper()
        helper._get_dbus_value = MagicMock(return_value=60.0)
        with patch("shelly_wallbox_auto_input_helper.time.time", return_value=100.0):
            self.assertEqual(
                helper._resolve_auto_battery_service(),
                "com.victronenergy.battery.socketcan_can1",
            )
        self.assertEqual(helper._resolved_auto_battery_service, "com.victronenergy.battery.socketcan_can1")

        helper = self._make_helper()
        helper.auto_battery_service = "configured-battery"
        helper._get_dbus_value = MagicMock(side_effect=RuntimeError("offline"))
        helper._resolved_auto_battery_service = "cached-battery"
        helper._auto_battery_last_scan = 100.0
        with patch("shelly_wallbox_auto_input_helper.time.time", return_value=120.0):
            self.assertEqual(helper._resolve_auto_battery_service(), "cached-battery")

        helper = self._make_helper()
        helper.auto_battery_service = ""
        helper._list_dbus_services = MagicMock(return_value=["com.victronenergy.system"])
        helper._battery_service_has_soc = MagicMock(return_value=False)
        with self.assertRaises(ValueError):
            helper._resolve_auto_battery_service()

    def test_battery_service_has_soc_and_get_battery_soc_cover_success_and_failure(self):
        helper = self._make_helper()
        helper._get_dbus_value = MagicMock(side_effect=[55.0, RuntimeError("boom")])
        self.assertTrue(helper._battery_service_has_soc("svc"))
        self.assertFalse(helper._battery_service_has_soc("svc"))

        helper = self._make_helper()
        helper._resolve_auto_battery_service = MagicMock(return_value="battery")
        helper._get_dbus_value = MagicMock(return_value=57.5)
        self.assertEqual(helper._get_battery_soc(), 57.5)

        helper._get_dbus_value = MagicMock(return_value="bad")
        self.assertIsNone(helper._get_battery_soc())

        helper._resolve_auto_battery_service = MagicMock(side_effect=RuntimeError("offline"))
        helper._invalidate_auto_battery_service = MagicMock()
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._get_battery_soc())
        helper._invalidate_auto_battery_service.assert_called_once_with()
        helper._delay_source_retry.assert_called_once_with("battery")

    def test_get_battery_soc_respects_source_retry_guard(self):
        helper = self._make_helper()
        helper._source_retry_after["battery"] = 200.0
        with patch("shelly_wallbox_auto_input_helper.time.time", return_value=100.0):
            self.assertIsNone(helper._get_battery_soc())

    def test_get_grid_power_returns_total_and_handles_no_paths(self):
        helper = self._make_helper()

        def fake_get_value(service_name, path):
            values = {
                "/Ac/Grid/L1/Power": -500.0,
                "/Ac/Grid/L2/Power": -400.0,
                "/Ac/Grid/L3/Power": 100.0,
            }
            return values[path]

        helper._get_dbus_value = MagicMock(side_effect=fake_get_value)
        self.assertEqual(helper._get_grid_power(), -800.0)

        helper.auto_grid_l1_path = ""
        helper.auto_grid_l2_path = ""
        helper.auto_grid_l3_path = ""
        self.assertIsNone(helper._get_grid_power())

    def test_get_grid_power_covers_retry_guard_and_partial_failures(self):
        helper = self._make_helper()
        helper._source_retry_after["grid"] = 200.0
        with patch("shelly_wallbox_auto_input_helper.time.time", return_value=100.0):
            self.assertIsNone(helper._get_grid_power())

        helper = self._make_helper()

        def fake_get_value(_service_name, path):
            if path == "/Ac/Grid/L1/Power":
                raise RuntimeError("offline")
            if path == "/Ac/Grid/L2/Power":
                return "not-numeric"
            return 200.0

        helper.auto_grid_require_all_phases = False
        helper._get_dbus_value = MagicMock(side_effect=fake_get_value)
        self.assertEqual(helper._get_grid_power(), 200.0)

        helper = self._make_helper()
        helper.auto_grid_require_all_phases = True
        helper._get_dbus_value = MagicMock(side_effect=fake_get_value)
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._get_grid_power())
        helper._delay_source_retry.assert_called_once_with("grid")

    def test_write_snapshot_deduplicates_identical_payload(self):
        helper = self._make_helper()
        helper.snapshot_path = "/tmp/auto.json"
        with patch("shelly_wallbox_auto_input_helper.write_text_atomically") as write_mock:
            helper._write_snapshot({"captured_at": 100.0})
            helper._write_snapshot({"captured_at": 100.0})

        write_mock.assert_called_once()

    def test_run_sets_up_mainloop_subscriptions_and_timers(self):
        helper = self._make_helper()
        helper.snapshot_path = "/tmp/auto.json"
        helper.parent_pid = 1234
        helper.validation_poll_seconds = 30.0
        helper.subscription_refresh_seconds = 60.0
        helper._get_system_bus = MagicMock(return_value=MagicMock(add_signal_receiver=MagicMock()))
        helper._refresh_subscriptions = MagicMock()
        mainloop = MagicMock()

        fake_glib_mainloop = MagicMock()
        with patch.object(shelly_wallbox_auto_input_helper, "dbus_glib_mainloop", fake_glib_mainloop):
            with patch("shelly_wallbox_auto_input_helper.GLib.MainLoop", return_value=mainloop):
                with patch("shelly_wallbox_auto_input_helper.GLib.timeout_add") as timeout_add:
                    with patch("shelly_wallbox_auto_input_helper.signal.signal") as signal_mock:
                        helper.run()

        fake_glib_mainloop.DBusGMainLoop.assert_called_once_with(set_as_default=True)
        self.assertTrue(signal_mock.called)
        helper._get_system_bus.return_value.add_signal_receiver.assert_called_once()
        helper._refresh_subscriptions.assert_called_once_with()
        self.assertEqual(timeout_add.call_count, 4)
        mainloop.run.assert_called_once_with()

    def test_run_ignores_signal_registration_failures_and_missing_signals(self):
        helper = self._make_helper()
        helper.snapshot_path = "/tmp/auto.json"
        helper.parent_pid = 1234
        helper.validation_poll_seconds = 30.0
        helper.subscription_refresh_seconds = 60.0
        helper._get_system_bus = MagicMock(return_value=MagicMock(add_signal_receiver=MagicMock()))
        helper._refresh_subscriptions = MagicMock()
        mainloop = MagicMock()

        fake_glib_mainloop = MagicMock()
        fake_signal = MagicMock(SIGTERM=15, SIGINT=None, SIGHUP=1)
        fake_signal.signal.side_effect = [RuntimeError("no handler"), RuntimeError("no handler")]
        with patch.object(shelly_wallbox_auto_input_helper, "dbus_glib_mainloop", fake_glib_mainloop):
            with patch("shelly_wallbox_auto_input_helper.GLib.MainLoop", return_value=mainloop):
                with patch("shelly_wallbox_auto_input_helper.GLib.timeout_add"):
                    with patch.object(shelly_wallbox_auto_input_helper, "signal", fake_signal):
                        helper.run()

        helper._refresh_subscriptions.assert_called_once_with()

    def test_run_requires_dbus_glib_mainloop_and_main_invokes_helper(self):
        helper = self._make_helper()
        with patch.object(shelly_wallbox_auto_input_helper, "dbus_glib_mainloop", None):
            with self.assertRaises(RuntimeError):
                helper.run()

        fake_helper = MagicMock()
        with patch("shelly_wallbox_auto_input_helper.AutoInputHelper", return_value=fake_helper) as helper_cls:
            self.assertEqual(
                shelly_wallbox_auto_input_helper.main(["/tmp/config.ini", "/tmp/snapshot.json", "1234"]),
                0,
            )
        helper_cls.assert_called_once_with("/tmp/config.ini", "/tmp/snapshot.json", "1234")
        fake_helper.run.assert_called_once_with()

    def test_main_uses_default_config_path_when_argv_missing(self):
        fake_helper = MagicMock()
        with patch("shelly_wallbox_auto_input_helper.AutoInputHelper", return_value=fake_helper) as helper_cls:
            with patch("shelly_wallbox_auto_input_helper.os.path.abspath", return_value="/repo/shelly_wallbox_auto_input_helper.py"):
                self.assertEqual(shelly_wallbox_auto_input_helper.main([]), 0)

        helper_cls.assert_called_once_with("/repo/config.shelly_wallbox.ini", None, None)
