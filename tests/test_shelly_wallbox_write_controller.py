# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock

from dbus_shelly_wallbox_ports import WriteControllerPort
from dbus_shelly_wallbox_write_controller import DbusWriteController


class TestDbusWriteController(unittest.TestCase):
    @staticmethod
    def _publish_side_effect(service):
        def _publish(path, value, _now=None, force=False, **_kwargs):
            service._dbusservice[path] = value
            return force

        return _publish

    def test_handle_mode_write_manual_to_auto_queues_clean_cutover(self):
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=1,
            virtual_enable=0,
            auto_start_condition_since=100.0,
            auto_stop_condition_since=200.0,
            auto_samples=deque([(205.0, 2200.0, -2200.0)]),
            manual_override_until=500.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={"/Mode": 0, "/StartStop": 1, "/Enable": 0},
            _time_now=MagicMock(return_value=200.0),
            _normalize_mode=lambda value: int(value),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _clear_auto_samples=lambda: service.auto_samples.clear(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={"pv_power": 10, "battery_soc": 50, "grid_power": -10}),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_path=MagicMock(),
            _state_summary=lambda: "state",
            _save_runtime_state=MagicMock(),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)

        controller = DbusWriteController(WriteControllerPort(service))
        result = controller.handle_write("/Mode", 1)

        self.assertTrue(result)
        self.assertEqual(service.virtual_mode, 1)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.virtual_startstop, 0)
        self.assertEqual(service.manual_override_until, 0.0)
        self.assertTrue(service._auto_mode_cutover_pending)
        service._queue_relay_command.assert_called_once_with(False, 200.0)
        service._publish_local_pm_status.assert_called_once_with(False, 200.0)
        service._update_worker_snapshot.assert_called_once()
        self.assertEqual(service._dbusservice["/Mode"], 1)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)
        service._save_runtime_state.assert_called_once()

    def test_handle_enable_write_in_manual_mode_switches_relay(self):
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_startstop=0,
            virtual_enable=0,
            auto_manual_override_seconds=300,
            manual_override_until=0.0,
            _dbusservice={"/StartStop": 0, "/Enable": 0},
            _time_now=MagicMock(return_value=100.0),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _publish_dbus_path=MagicMock(),
            _state_summary=lambda: "state",
            _save_runtime_state=MagicMock(),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)

        controller = DbusWriteController(WriteControllerPort(service))
        result = controller.handle_write("/Enable", 1)

        self.assertTrue(result)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.virtual_startstop, 1)
        self.assertEqual(service.manual_override_until, 400.0)
        service._queue_relay_command.assert_called_once_with(True, 100.0)
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)
        service._save_runtime_state.assert_called_once()

    def test_mode_autostart_current_and_error_paths(self):
        service = SimpleNamespace(
            virtual_mode=5,
            virtual_autostart=0,
            virtual_startstop=0,
            virtual_enable=0,
            auto_start_condition_since=1.0,
            auto_stop_condition_since=2.0,
            auto_samples=deque([(1.0, 2.0, 3.0)]),
            manual_override_until=50.0,
            _auto_mode_cutover_pending=True,
            _ignore_min_offtime_once=True,
            max_current=16.0,
            min_current=6.0,
            virtual_set_current=10.0,
            _dbusservice={"/Mode": 5, "/StartStop": 0, "/Enable": 0, "/AutoStart": 0, "/SetCurrent": 10.0},
            _time_now=MagicMock(return_value=42.0),
            _normalize_mode=lambda value: 2 if int(value) == 5 else int(value),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _clear_auto_samples=lambda: service.auto_samples.clear(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={"pv_power": 10, "battery_soc": 50, "grid_power": -10}),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_path=MagicMock(),
            _state_summary=lambda: "state",
            _save_runtime_state=MagicMock(),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)
        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/Mode", 5))
        self.assertEqual(service.virtual_mode, 2)
        self.assertEqual(service.manual_override_until, 0.0)
        self.assertFalse(service._auto_mode_cutover_pending)
        service._queue_relay_command.assert_not_called()

        self.assertTrue(controller.handle_write("/AutoStart", 1))
        self.assertEqual(service.virtual_autostart, 1)
        self.assertEqual(service._dbusservice["/AutoStart"], 1)

        self.assertTrue(controller.handle_write("/SetCurrent", 12.5))
        self.assertTrue(controller.handle_write("/MaxCurrent", 20))
        self.assertTrue(controller.handle_write("/MinCurrent", 4))
        self.assertEqual(service.virtual_set_current, 12.5)
        self.assertEqual(service.max_current, 20.0)
        self.assertEqual(service.min_current, 4.0)

        service._save_runtime_state.reset_mock()
        service._publish_dbus_path.side_effect = RuntimeError("fail")
        self.assertFalse(controller.handle_write("/AutoStart", 1))
        service._save_runtime_state.assert_not_called()

    def test_startstop_and_enable_in_auto_mode_cover_off_and_on_paths(self):
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_startstop=0,
            virtual_enable=0,
            auto_manual_override_seconds=300,
            manual_override_until=0.0,
            _dbusservice={"/StartStop": 0, "/Enable": 0},
            _time_now=MagicMock(return_value=100.0),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _publish_dbus_path=MagicMock(),
            _state_summary=lambda: "state",
            _save_runtime_state=MagicMock(),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)
        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/StartStop", 1))
        self.assertEqual(service.virtual_enable, 1)
        service._queue_relay_command.assert_not_called()

        self.assertTrue(controller.handle_write("/StartStop", 0))
        service._queue_relay_command.assert_called_once_with(False, 100.0)
        service._publish_local_pm_status.assert_called_once_with(False, 100.0)

        service._queue_relay_command.reset_mock()
        service._publish_local_pm_status.reset_mock()
        self.assertTrue(controller.handle_write("/Enable", 1))
        service._queue_relay_command.assert_not_called()
        self.assertTrue(controller.handle_write("/Enable", 0))
        service._queue_relay_command.assert_called_once_with(False, 100.0)

    def test_mode_transition_and_publish_helpers_cover_remaining_branches(self):
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_startstop=0,
            virtual_enable=1,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _publish_dbus_path=MagicMock(),
        )
        controller = DbusWriteController(WriteControllerPort(service))

        controller._handle_mode_transition_to_auto(1, 100.0)
        self.assertFalse(hasattr(service, "manual_override_until"))

        controller._publish_startstop_enable(service, 100.0)
        service._publish_dbus_path.assert_any_call("/StartStop", 1, 100.0, force=True)
        service._publish_dbus_path.assert_any_call("/Enable", 1, 100.0, force=True)
