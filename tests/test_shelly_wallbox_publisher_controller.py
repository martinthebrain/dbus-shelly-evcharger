# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from dbus_shelly_wallbox_publisher import DbusPublishController


class TestDbusPublishController(unittest.TestCase):
    @staticmethod
    def _age_seconds(_timestamp: Any, _now: float) -> float:
        return 0.0

    def test_publish_path_handles_change_and_interval_throttling(self) -> None:
        service = SimpleNamespace(
            _dbusservice={},
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )
        controller = DbusPublishController(service, self._age_seconds)

        self.assertTrue(controller.publish_path("/Path", 1, now=100.0))
        self.assertFalse(controller.publish_path("/Path", 1, now=101.0))

        service._dbus_publish_state["/IntervalMissing"] = {"value": 5}
        self.assertTrue(controller.publish_path("/IntervalMissing", 5, now=100.0, interval_seconds=5.0))

        self.assertFalse(controller.publish_path("/IntervalMissing", 7, now=103.0, interval_seconds=5.0))
        self.assertTrue(controller.publish_path("/IntervalMissing", 7, now=106.0, interval_seconds=5.0))

    def test_publish_live_measurements_rolls_back_publish_state_and_marks_failure(self) -> None:
        class FlakyDbusService(dict[str, float]):
            def __init__(self) -> None:
                super().__init__({"/Ac/Power": 10.0})
                self.writes: list[tuple[str, float]] = []

            def __setitem__(self, key: str, value: float) -> None:
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
        controller = DbusPublishController(service, self._age_seconds)

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

    def test_publish_config_paths_is_all_or_nothing_for_publish_state(self) -> None:
        class FlakyDbusService(dict[str, Any]):
            def __setitem__(self, key: str, value: Any) -> None:
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
        controller = DbusPublishController(service, self._age_seconds)

        changed = controller.publish_config_paths(1, 100.0)

        self.assertFalse(changed)
        self.assertNotIn("/Mode", service._dbus_publish_state)
        self.assertNotIn("/Enable", service._dbus_publish_state)
        service._mark_failure.assert_called_once_with("dbus")

    def test_config_values_use_stable_learned_current_by_default(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=16.0,
            min_current=6.0,
            max_current=16.0,
            learned_charge_power_state="stable",
            learned_charge_power_watts=2990.0,
            learned_charge_power_updated_at=95.0,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            phase="L1",
            voltage_mode="phase",
            auto_learn_charge_power_max_age_seconds=21600.0,
        )
        controller = DbusPublishController(service, self._age_seconds)

        values = controller._config_values(1, now=100.0)

        self.assertEqual(values["/SetCurrent"], 13.0)

    def test_config_values_can_disable_learned_current_display(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=16.0,
            min_current=6.0,
            max_current=16.0,
            display_learned_set_current=0,
            learned_charge_power_state="stable",
            learned_charge_power_watts=2990.0,
            learned_charge_power_updated_at=95.0,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            phase="L1",
            voltage_mode="phase",
            auto_learn_charge_power_max_age_seconds=21600.0,
        )
        controller = DbusPublishController(service, self._age_seconds)

        values = controller._config_values(1, now=100.0)

        self.assertEqual(values["/SetCurrent"], 16.0)

    def test_config_values_convert_stable_three_phase_line_voltage_to_display_current(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=16.0,
            min_current=6.0,
            max_current=16.0,
            learned_charge_power_state="stable",
            learned_charge_power_watts=10400.0,
            learned_charge_power_updated_at=95.0,
            learned_charge_power_phase="3P",
            learned_charge_power_voltage=400.0,
            phase="3P",
            voltage_mode="line_to_line",
            auto_learn_charge_power_max_age_seconds=21600.0,
        )
        controller = DbusPublishController(service, self._age_seconds)

        values = controller._config_values(1, now=100.0)

        self.assertEqual(values["/SetCurrent"], 15.0)

    def test_publish_transactional_removes_new_paths_when_group_write_fails(self) -> None:
        class FlakyDbusService(dict[str, int]):
            def __setitem__(self, key: str, value: int) -> None:
                if key == "/B":
                    raise RuntimeError("group write failed")
                super().__setitem__(key, value)

        service = SimpleNamespace(
            _dbusservice=FlakyDbusService(),
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = DbusPublishController(service, self._age_seconds)

        changed = controller._publish_values_transactional(
            "generic",
            {"/A": 1, "/B": 2},
            100.0,
            force=True,
        )

        self.assertFalse(changed)
        self.assertNotIn("/A", service._dbusservice)
        self.assertNotIn("/B", service._dbusservice)
        self.assertEqual(service._dbus_publish_state, {})
        service._mark_failure.assert_called_once_with("dbus")

    def test_publish_group_failure_falls_back_to_logging_without_warning_helper(self) -> None:
        service = SimpleNamespace(
            _dbusservice={},
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _mark_failure=MagicMock(),
        )
        controller = DbusPublishController(service, self._age_seconds)

        with patch("logging.warning") as warning:
            controller._publish_group_failure("diagnostic", ["/Path"], 123.0)

        service._mark_failure.assert_called_once_with("dbus")
        warning.assert_called_once()

    def test_publish_values_returns_false_when_group_is_fully_throttled(self) -> None:
        service = SimpleNamespace(
            _dbusservice={"/Path": 5},
            _dbus_publish_state={
                "/Path": {"value": 5, "updated_at": 95.0},
            },
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )
        controller = DbusPublishController(service, self._age_seconds)

        changed = controller._publish_values({"/Path": 5}, 100.0, interval_seconds=10.0)

        self.assertFalse(changed)
        self.assertEqual(service._dbusservice["/Path"], 5)
        self.assertEqual(service._dbus_publish_state["/Path"], {"value": 5, "updated_at": 95.0})

    def test_publish_values_ignores_restore_failure_after_group_write_error(self) -> None:
        class FlakyRestoreDbusService(dict[str, int]):
            def __init__(self) -> None:
                super().__init__({"/A": 1})
                self.restore_attempts = 0

            def __setitem__(self, key: str, value: int) -> None:
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
        controller = DbusPublishController(service, self._age_seconds)

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
