# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_auto_input_helper_support import (
    AutoInputHelper,
    AutoInputHelperTestCase,
    MagicMock,
    ModuleType,
    _as_bool,
    json,
    os,
    patch,
    runpy,
    venus_evcharger_auto_input_helper,
    sys,
    tempfile,
    unittest,
)


class TestShellyWallboxAutoInputHelperBasic(AutoInputHelperTestCase):
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

    def test_derive_subscription_refresh_seconds_uses_smallest_positive_scan_interval(self):
        helper = AutoInputHelper.__new__(AutoInputHelper)
        helper.config = {
            "AutoPvScanIntervalSeconds": "45",
            "AutoBatteryScanIntervalSeconds": "15",
        }

        self.assertEqual(helper._derive_subscription_refresh_seconds(), 15.0)

    def test_derive_subscription_refresh_seconds_ignores_non_positive_candidates(self):
        helper = AutoInputHelper.__new__(AutoInputHelper)
        helper.config = {
            "AutoPvScanIntervalSeconds": "0",
            "AutoBatteryScanIntervalSeconds": "-5",
        }

        self.assertEqual(helper._derive_subscription_refresh_seconds(), 60.0)

    def test_handle_signal_sets_stop_flag_and_requests_idle_quit(self):
        helper = self._make_helper()
        helper._main_loop = MagicMock()

        with patch("venus_evcharger_auto_input_helper.GLib.idle_add") as idle_add:
            helper._handle_signal(15, None)

        self.assertTrue(helper._stop_requested)
        idle_add.assert_called_once_with(helper._main_loop.quit)

    def test_handle_signal_sets_stop_flag_without_idle_quit_when_main_loop_is_missing(self):
        helper = self._make_helper()
        helper._main_loop = None

        with patch("venus_evcharger_auto_input_helper.GLib.idle_add") as idle_add:
            helper._handle_signal(15, None)

        self.assertTrue(helper._stop_requested)
        idle_add.assert_not_called()

    def test_warning_throttled_logs_once_per_interval(self):
        helper = self._make_helper()

        with patch("venus_evcharger_auto_input_helper.time.time", side_effect=[100.0, 105.0, 131.0]):
            with patch("venus_evcharger_auto_input_helper.logging.warning") as warning_mock:
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
        self.assertEqual(helper._last_snapshot_state["snapshot_version"], AutoInputHelper.SNAPSHOT_SCHEMA_VERSION)
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
        self.assertEqual(payload["snapshot_version"], AutoInputHelper.SNAPSHOT_SCHEMA_VERSION)

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
        self.assertEqual(second["snapshot_version"], AutoInputHelper.SNAPSHOT_SCHEMA_VERSION)
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

    def test_configured_auto_battery_service_returns_none_when_soc_is_missing(self):
        helper = self._make_helper()
        helper._get_dbus_value = MagicMock(return_value=None)

        self.assertIsNone(helper._configured_auto_battery_service(100.0))
        self.assertIsNone(helper._resolved_auto_battery_service)
        self.assertEqual(helper._auto_battery_last_scan, 0.0)

    def test_configured_auto_battery_service_returns_none_when_read_raises(self):
        helper = self._make_helper()
        helper.auto_battery_service = "configured-battery"
        helper._get_dbus_value = MagicMock(side_effect=RuntimeError("offline"))

        self.assertIsNone(helper._configured_auto_battery_service(100.0))

    def test_parent_alive_uses_parent_pid_and_handles_errors(self):
        helper = self._make_helper()
        helper.parent_pid = 1234

        helper.parent_pid = None
        self.assertTrue(helper._parent_alive())
        helper.parent_pid = 1234

        with unittest.mock.patch("venus_evcharger_auto_input_helper.os.getppid", return_value=1234):
            self.assertTrue(helper._parent_alive())

        with unittest.mock.patch("venus_evcharger_auto_input_helper.os.getppid", return_value=9999):
            self.assertFalse(helper._parent_alive())

        with unittest.mock.patch("venus_evcharger_auto_input_helper.os.getppid", side_effect=RuntimeError("boom")):
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

    def test_set_source_value_applies_structured_battery_snapshot_payload(self):
        helper = self._make_helper()
        helper._write_snapshot = MagicMock()

        helper._set_source_value(
            "battery",
            {
                "battery_soc": 56.0,
                "battery_combined_soc": 58.0,
                "battery_combined_usable_capacity_wh": 9000.0,
                "battery_combined_charge_power_w": 700.0,
                "battery_combined_discharge_power_w": 0.0,
                "battery_combined_net_power_w": -700.0,
                "battery_combined_ac_power_w": 1500.0,
                "battery_headroom_charge_w": 300.0,
                "battery_headroom_discharge_w": 1100.0,
                "expected_near_term_export_w": 120.0,
                "expected_near_term_import_w": 0.0,
                "battery_source_count": 2,
                "battery_online_source_count": 2,
                "battery_valid_soc_source_count": 2,
                "battery_sources": [{"source_id": "victron"}],
                "battery_learning_profiles": {"victron": {"sample_count": 1}},
            },
            99.5,
        )

        self.assertEqual(helper._last_snapshot_state["battery_soc"], 56.0)
        self.assertEqual(helper._last_snapshot_state["battery_combined_soc"], 58.0)
        self.assertEqual(helper._last_snapshot_state["battery_headroom_charge_w"], 300.0)
        self.assertEqual(helper._last_snapshot_state["expected_near_term_export_w"], 120.0)
        self.assertEqual(helper._last_snapshot_state["battery_sources"], [{"source_id": "victron"}])
        self.assertEqual(helper._last_snapshot_state["battery_learning_profiles"], {"victron": {"sample_count": 1}})
        self.assertEqual(helper._last_snapshot_state["battery_captured_at"], 99.5)

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

        with unittest.mock.patch("venus_evcharger_auto_input_helper.time.time", return_value=130.0):
            helper._heartbeat_snapshot()

        self.assertEqual(helper._last_snapshot_state["heartbeat_at"], 130.0)
        self.assertEqual(helper._last_snapshot_state["captured_at"], 100.0)
        self.assertEqual(helper._last_snapshot_state["snapshot_version"], AutoInputHelper.SNAPSHOT_SCHEMA_VERSION)
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

        with patch("venus_evcharger_auto_input_helper.time.time", return_value=123.0):
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

    def test_read_ac_pv_total_ignores_non_numeric_values(self):
        helper = self._make_helper()
        helper._get_dbus_value = MagicMock(return_value="invalid")

        self.assertEqual(helper._read_ac_pv_total(["com.victronenergy.pvinverter.http_40"]), (0.0, False))

    def test_helper_module_import_fallback_sets_dbus_glib_mainloop_to_none(self):
        helper_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "venus_evcharger_auto_input_helper.py")
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
            module_globals = runpy.run_path(helper_path, run_name="venus_evcharger_auto_input_helper_import_test")

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

    def test_clear_missing_subscriptions_also_drops_specs_without_live_match(self):
        helper = self._make_helper()
        drop_key = ("grid", "svc", "/Ac/Grid/L1/Power")
        helper._signal_matches = {drop_key: None}
        helper._monitored_specs = {drop_key: {"y": 2}}

        helper._clear_missing_subscriptions(set())

        self.assertEqual(helper._signal_matches, {})
        self.assertEqual(helper._monitored_specs, {})

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

    def test_desired_grid_subscription_specs_returns_empty_without_grid_service(self):
        helper = self._make_helper()
        helper.auto_grid_service = ""

        self.assertEqual(helper._desired_grid_subscription_specs(), [])

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

    def test_parent_watchdog_stops_without_quit_call_when_mainloop_is_missing(self):
        helper = self._make_helper()
        helper._stop_requested = False
        helper._main_loop = None
        helper._parent_alive = MagicMock(return_value=False)

        self.assertFalse(helper._parent_watchdog())

    def test_schedule_refresh_subscriptions_only_schedules_one_idle_callback(self):
        helper = self._make_helper()
        helper._refresh_subscriptions = MagicMock(return_value=False)
        callbacks = []

        with unittest.mock.patch(
            "venus_evcharger_auto_input_helper.GLib.idle_add",
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
