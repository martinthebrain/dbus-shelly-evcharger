# SPDX-License-Identifier: GPL-3.0-or-later
from tests.wallbox_auto_input_helper_support import (
    AutoInputHelperTestCase,
    MagicMock,
    patch,
    shelly_wallbox_auto_input_helper,
    sys,
    unittest,
)


class TestShellyWallboxAutoInputHelperSources(AutoInputHelperTestCase):
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
            self.assertEqual(helper._resolve_auto_battery_service(), "com.victronenergy.battery.socketcan_can1")

    def test_resolve_auto_battery_service_covers_configured_service_cache_and_failure(self):
        helper = self._make_helper()
        helper._get_dbus_value = MagicMock(return_value=60.0)
        with patch("shelly_wallbox_auto_input_helper.time.time", return_value=100.0):
            self.assertEqual(helper._resolve_auto_battery_service(), "com.victronenergy.battery.socketcan_can1")
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

        helper._get_dbus_value = MagicMock(return_value=True)
        self.assertIsNone(helper._get_battery_soc())

        helper._get_dbus_value = MagicMock(return_value=150.0)
        helper._warning_throttled = MagicMock()
        helper._delay_source_retry = MagicMock()
        self.assertIsNone(helper._get_battery_soc())
        helper._warning_throttled.assert_called_once()
        helper._delay_source_retry.assert_called_once_with("battery")

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

        helper_cls.assert_called_once_with("/repo/deploy/venus/config.shelly_wallbox.ini", None, None)
