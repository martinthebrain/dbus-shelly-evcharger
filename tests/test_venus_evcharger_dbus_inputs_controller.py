# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import venus_evcharger.inputs.dbus as wallbox_dbus_inputs
from venus_evcharger.inputs.dbus import DbusInputController


class TestDbusInputController(unittest.TestCase):
    @staticmethod
    def _make_service() -> SimpleNamespace:
        service = SimpleNamespace(
            auto_pv_service="",
            auto_pv_service_prefix="com.victronenergy.pvinverter",
            auto_pv_path="/Ac/Power",
            auto_pv_max_services=2,
            auto_pv_scan_interval_seconds=60.0,
            auto_use_dc_pv=True,
            auto_dc_pv_service="com.victronenergy.system",
            auto_dc_pv_path="/Dc/Pv/Power",
            auto_battery_service="com.victronenergy.battery.socketcan_can1",
            auto_battery_soc_path="/Soc",
            auto_battery_service_prefix="com.victronenergy.battery",
            auto_battery_scan_interval_seconds=60.0,
            auto_grid_service="com.victronenergy.system",
            auto_grid_l1_path="/Ac/Grid/L1/Power",
            auto_grid_l2_path="/Ac/Grid/L2/Power",
            auto_grid_l3_path="/Ac/Grid/L3/Power",
            auto_grid_require_all_phases=True,
            auto_dbus_backoff_base_seconds=5.0,
            auto_dbus_backoff_max_seconds=60.0,
            dbus_method_timeout_seconds=1.0,
            _resolved_auto_pv_services=[],
            _auto_pv_last_scan=0.0,
            _resolved_auto_battery_service=None,
            _auto_battery_last_scan=0.0,
            _dbus_list_backoff_until=0.0,
            _dbus_list_failures=0,
            _last_dbus_ok_at=None,
            _source_retry_after={},
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _delay_source_retry=MagicMock(),
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _get_system_bus=MagicMock(),
        )
        return service

    def test_get_dbus_value_and_list_services_cover_retry_and_failure_paths(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        module: Any = wallbox_dbus_inputs

        service._get_system_bus.return_value = MagicMock(get_object=MagicMock(return_value=object()))
        failing_interface = MagicMock()
        failing_interface.GetValue.side_effect = [RuntimeError("boom"), RuntimeError("boom")]
        original_interface = module.dbus.Interface
        module.dbus.Interface = MagicMock(return_value=failing_interface)
        try:
            with self.assertRaises(RuntimeError):
                controller.get_dbus_value("svc", "/Path")
        finally:
            module.dbus.Interface = original_interface

        self.assertEqual(service._reset_system_bus.call_count, 2)

        with patch("venus_evcharger.inputs.dbus.time.time", return_value=10.0):
            service._dbus_list_backoff_until = 20.0
            with self.assertRaises(RuntimeError):
                controller.list_dbus_services()

        service._dbus_list_backoff_until = 0.0
        service._get_system_bus.return_value = MagicMock(get_object=MagicMock(return_value=object()))
        failing_interface = MagicMock()
        failing_interface.ListNames.side_effect = RuntimeError("dbus down")
        module.dbus.Interface = MagicMock(return_value=failing_interface)
        try:
            with patch("venus_evcharger.inputs.dbus.time.time", return_value=100.0):
                with self.assertRaises(RuntimeError):
                    controller.list_dbus_services()
        finally:
            module.dbus.Interface = original_interface

        self.assertEqual(service._dbus_list_failures, 1)
        self.assertEqual(service._dbus_list_backoff_until, 105.0)

    def test_pv_resolution_and_missing_pv_paths_cover_explicit_cached_and_rescan_failures(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)

        service.auto_pv_service = "explicit-pv"
        self.assertEqual(controller.resolve_auto_pv_services(), ["explicit-pv"])

        service.auto_pv_service = ""
        service._resolved_auto_pv_services = ["cached-pv"]
        service._auto_pv_last_scan = 100.0
        with patch("venus_evcharger.inputs.dbus.time.time", return_value=120.0):
            self.assertEqual(controller.resolve_auto_pv_services(), ["cached-pv"])

        service._resolved_auto_pv_services = []
        service._list_dbus_services = MagicMock(return_value=["com.victronenergy.system"])
        with patch("venus_evcharger.inputs.dbus.time.time", return_value=200.0):
            with self.assertRaises(ValueError):
                controller.resolve_auto_pv_services()

        service.auto_pv_service = ""
        service._resolve_auto_pv_services = MagicMock(side_effect=RuntimeError("scan failed"))
        self.assertEqual(controller._resolve_pv_service_names(), ([], False))

        service._invalidate_auto_pv_services = MagicMock()
        service._resolve_auto_pv_services = MagicMock(side_effect=RuntimeError("rescan failed"))
        self.assertEqual(controller._read_rescanned_pv_services(), (0.0, False))

        service._mark_recovery.reset_mock()
        self.assertEqual(
            controller._handle_missing_pv_power([], True, None, False, 100.0),
            0.0,
        )
        service._mark_recovery.assert_called_once_with("pv", "No readable PV values discovered, assuming 0 W")

    def test_battery_and_grid_paths_cover_override_cache_failures_and_missing_values(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)

        service._get_dbus_value = MagicMock(side_effect=RuntimeError("offline"))
        self.assertFalse(controller._battery_service_has_soc("battery"))

        service.auto_battery_service = "configured-battery"
        with patch.object(controller, "_battery_service_has_soc", return_value=False):
            self.assertIsNone(controller._resolve_battery_service_override())

        service._resolved_auto_battery_service = "cached-battery"
        service._auto_battery_last_scan = 100.0
        with patch("venus_evcharger.inputs.dbus.time.time", return_value=120.0):
            self.assertEqual(controller._cached_auto_battery_service(120.0), "cached-battery")

        service._list_dbus_services = MagicMock(return_value=["com.victronenergy.system"])
        with patch.object(controller, "_battery_service_has_soc", return_value=False):
            with self.assertRaises(ValueError):
                controller._scan_auto_battery_service(200.0)

        service._source_retry_ready = MagicMock(return_value=False)
        self.assertIsNone(controller.get_battery_soc())

        service._source_retry_ready = MagicMock(return_value=True)
        service._resolve_auto_battery_service = MagicMock(return_value="battery")
        service._get_dbus_value = MagicMock(return_value="bad")
        with patch.object(controller, "_handle_source_failure", return_value=None) as handle_failure:
            self.assertIsNone(controller.get_battery_soc())
        handle_failure.assert_called_once()

        service._source_retry_ready = MagicMock(return_value=True)
        service.auto_grid_l1_path = ""
        service.auto_grid_l2_path = ""
        service.auto_grid_l3_path = ""
        self.assertIsNone(controller.get_grid_power())

        service.auto_grid_l1_path = "/Ac/Grid/L1/Power"
        service.auto_grid_l2_path = "/Ac/Grid/L2/Power"
        service.auto_grid_l3_path = ""
        service._get_dbus_value = MagicMock(side_effect=[100.0, None])
        total, seen_value, missing_paths = controller._read_grid_phase_values(
            ["/Ac/Grid/L1/Power", "/Ac/Grid/L2/Power"]
        )
        self.assertEqual(total, 100.0)
        self.assertTrue(seen_value)
        self.assertEqual(missing_paths, ["/Ac/Grid/L2/Power"])

        with patch.object(controller, "_handle_source_failure", return_value=None) as handle_failure:
            self.assertIsNone(controller._handle_missing_grid_values(False, [], 100.0))
        handle_failure.assert_called_once()

    def test_battery_override_cached_resolution_and_nonnumeric_grid_values(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)

        with patch.object(controller, "_battery_service_has_soc", return_value=True):
            self.assertEqual(controller._resolve_battery_service_override(), service.auto_battery_service)

        with patch.object(controller, "_resolve_battery_service_override", return_value="override-battery"):
            self.assertEqual(controller.resolve_auto_battery_service(), "override-battery")

        with (
            patch.object(controller, "_resolve_battery_service_override", return_value=None),
            patch.object(controller, "_cached_auto_battery_service", return_value="cached-battery"),
        ):
            self.assertEqual(controller.resolve_auto_battery_service(), "cached-battery")

        service._get_dbus_value = MagicMock(return_value=["bad"])
        total, seen_value, missing_paths = controller._read_grid_phase_values(["/Ac/Grid/L1/Power"])
        self.assertEqual(total, 0.0)
        self.assertFalse(seen_value)
        self.assertEqual(missing_paths, ["/Ac/Grid/L1/Power"])

    def test_scan_auto_battery_service_propagates_dbus_listing_failures(self) -> None:
        service = self._make_service()
        controller = DbusInputController(service)
        service._list_dbus_services = MagicMock(side_effect=RuntimeError("dbus down"))

        with self.assertRaisesRegex(RuntimeError, "dbus down"):
            controller._scan_auto_battery_service(100.0)
