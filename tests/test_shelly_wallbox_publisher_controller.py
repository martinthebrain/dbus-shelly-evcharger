# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from types import SimpleNamespace

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
