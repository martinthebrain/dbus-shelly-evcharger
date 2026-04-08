# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from unittest.mock import MagicMock

from dbus_shelly_wallbox_publisher import DbusPublishController


class TestDbusPublishController(unittest.TestCase):
    def test_publish_path_handles_change_and_interval_throttling(self):
        service = SimpleNamespace(
            _dbusservice={},
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )
        controller = DbusPublishController(service, lambda *_args: 0)

        self.assertTrue(controller.publish_path("/Path", 1, now=100.0))
        self.assertFalse(controller.publish_path("/Path", 1, now=101.0))

        service._dbus_publish_state["/IntervalMissing"] = {"value": 5}
        self.assertTrue(controller.publish_path("/IntervalMissing", 5, now=100.0, interval_seconds=5.0))

        self.assertFalse(controller.publish_path("/IntervalMissing", 7, now=103.0, interval_seconds=5.0))
        self.assertTrue(controller.publish_path("/IntervalMissing", 7, now=106.0, interval_seconds=5.0))

    def test_publish_live_measurements_rolls_back_publish_state_and_marks_failure(self):
        class FlakyDbusService(dict):
            def __init__(self):
                super().__init__({"/Ac/Power": 10.0})
                self.writes = []

            def __setitem__(self, key, value):
                self.writes.append((key, value))
                if key == "/Ac/Voltage":
                    raise RuntimeError("dbus write failed")
                super().__setitem__(key, value)

        service = SimpleNamespace(
            _dbusservice=FlakyDbusService(),
            _dbus_publish_state={
                "/Ac/Power": {"value": 10.0, "updated_at": 90.0},
            },
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = DbusPublishController(service, lambda *_args: 0)

        changed = controller.publish_live_measurements(
            1000.0,
            230.0,
            4.3,
            {
                "L1": {"power": 1000.0, "current": 4.3, "voltage": 230.0},
                "L2": {"power": 0.0, "current": 0.0, "voltage": 0.0},
                "L3": {"power": 0.0, "current": 0.0, "voltage": 0.0},
            },
            100.0,
        )

        self.assertFalse(changed)
        self.assertEqual(service._dbusservice["/Ac/Power"], 10.0)
        self.assertEqual(service._dbus_publish_state["/Ac/Power"], {"value": 10.0, "updated_at": 90.0})
        service._mark_failure.assert_called_once_with("dbus")
        service._warning_throttled.assert_called_once()

    def test_publish_config_paths_is_all_or_nothing_for_publish_state(self):
        class FlakyDbusService(dict):
            def __setitem__(self, key, value):
                if key == "/Enable":
                    raise RuntimeError("dbus write failed")
                super().__setitem__(key, value)

        service = SimpleNamespace(
            _dbusservice=FlakyDbusService(),
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=16.0,
            min_current=6.0,
            max_current=16.0,
        )
        controller = DbusPublishController(service, lambda *_args: 0)

        changed = controller.publish_config_paths(1, 100.0)

        self.assertFalse(changed)
        self.assertNotIn("/Mode", service._dbus_publish_state)
        self.assertNotIn("/Enable", service._dbus_publish_state)
        service._mark_failure.assert_called_once_with("dbus")

    def test_publish_group_failure_falls_back_to_logging_without_warning_helper(self):
        service = SimpleNamespace(
            _dbusservice={},
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _mark_failure=MagicMock(),
        )
        controller = DbusPublishController(service, lambda *_args: 0)

        with patch("logging.warning") as warning:
            controller._publish_group_failure("diagnostic", ["/Path"], 123.0)

        service._mark_failure.assert_called_once_with("dbus")
        warning.assert_called_once()

    def test_publish_values_returns_false_when_group_is_fully_throttled(self):
        service = SimpleNamespace(
            _dbusservice={"/Path": 5},
            _dbus_publish_state={
                "/Path": {"value": 5, "updated_at": 95.0},
            },
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )
        controller = DbusPublishController(service, lambda *_args: 0)

        changed = controller._publish_values({"/Path": 5}, 100.0, interval_seconds=10.0)

        self.assertFalse(changed)
        self.assertEqual(service._dbusservice["/Path"], 5)
        self.assertEqual(service._dbus_publish_state["/Path"], {"value": 5, "updated_at": 95.0})

    def test_publish_values_ignores_restore_failure_after_group_write_error(self):
        class FlakyRestoreDbusService(dict):
            def __init__(self):
                super().__init__({"/A": 1})
                self.restore_attempts = 0

            def __setitem__(self, key, value):
                if key == "/A" and value == 1:
                    self.restore_attempts += 1
                    raise RuntimeError("restore failed")
                if key == "/B":
                    raise RuntimeError("group write failed")
                super().__setitem__(key, value)

        service = SimpleNamespace(
            _dbusservice=FlakyRestoreDbusService(),
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = DbusPublishController(service, lambda *_args: 0)

        changed = controller._publish_values_transactional(
            "generic",
            {"/A": 2, "/B": 3},
            100.0,
            force=True,
        )

        self.assertFalse(changed)
        self.assertEqual(service._dbusservice["/A"], 2)
        self.assertEqual(service._dbus_publish_state, {})
        self.assertEqual(service._dbusservice.restore_attempts, 1)
        service._mark_failure.assert_called_once_with("dbus")
        service._warning_throttled.assert_called_once()
