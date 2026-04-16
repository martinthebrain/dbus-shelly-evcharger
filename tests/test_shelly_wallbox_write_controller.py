# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from collections import deque
from collections.abc import Callable
from functools import partial
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from shelly_wallbox.ports import WriteControllerPort
from shelly_wallbox.controllers.write_snapshot import _snapshot_dbus_paths
from shelly_wallbox.controllers.write import DbusWriteController


class TestDbusWriteController(unittest.TestCase):
    @staticmethod
    def _normalize_mode(value: object) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float, str)):
            return int(value)
        return 0

    @staticmethod
    def _normalize_mode_5_to_2(value: object) -> int:
        mode = TestDbusWriteController._normalize_mode(value)
        return 2 if mode == 5 else mode

    @staticmethod
    def _mode_uses_auto_logic(mode: object) -> bool:
        return TestDbusWriteController._normalize_mode(mode) in (1, 2)

    @staticmethod
    def _clear_auto_samples(service: Any) -> None:
        service.auto_samples.clear()

    @staticmethod
    def _state_summary() -> str:
        return "state"

    @staticmethod
    def _publish_side_effect(service: Any) -> Callable[..., bool]:
        def _publish(
            path: str,
            value: object,
            _now: float | None = None,
            force: bool = False,
            **_kwargs: object,
        ) -> bool:
            service._dbusservice[path] = value
            return force

        return _publish

    @staticmethod
    def _apply_phase_selection(service: Any, selection: str) -> str:
        service.requested_phase_selection = selection
        service.active_phase_selection = selection
        return selection

    def test_snapshot_dbus_paths_returns_empty_mapping_without_dbusservice(self) -> None:
        self.assertEqual(_snapshot_dbus_paths(SimpleNamespace(), ("/Mode",)), {})

    def test_handle_mode_write_manual_to_auto_queues_clean_cutover(self) -> None:
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
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _clear_auto_samples=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_pm_status={"output": True},
            _get_worker_snapshot=MagicMock(return_value={"pv_power": 10, "battery_soc": 50, "grid_power": -10}),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._clear_auto_samples = partial(self._clear_auto_samples, service)
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

    def test_handle_mode_write_manual_to_auto_uses_best_known_relay_state_for_cutover(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=0,
            virtual_enable=0,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            auto_samples=deque(),
            manual_override_until=10.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={"/Mode": 0, "/StartStop": 0, "/Enable": 0},
            _time_now=MagicMock(return_value=200.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _clear_auto_samples=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_pm_status={"output": True},
            _last_pm_status_confirmed=True,
            _get_worker_snapshot=MagicMock(return_value={"pm_status": {"output": True}, "pm_confirmed": True}),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._clear_auto_samples = partial(self._clear_auto_samples, service)
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)

        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/Mode", 1))

        self.assertTrue(service._auto_mode_cutover_pending)
        service._queue_relay_command.assert_called_once_with(False, 200.0)
        service._publish_local_pm_status.assert_called_once_with(False, 200.0)

    def test_handle_mode_write_manual_to_auto_honors_pending_relay_command_for_cutover(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=0,
            virtual_enable=0,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            auto_samples=deque(),
            manual_override_until=10.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={"/Mode": 0, "/StartStop": 0, "/Enable": 0},
            _time_now=MagicMock(return_value=200.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _clear_auto_samples=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _peek_pending_relay_command=MagicMock(return_value=(True, 150.0)),
            _last_pm_status={"output": False},
            _get_worker_snapshot=MagicMock(return_value={"pm_status": {"output": False}}),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._clear_auto_samples = partial(self._clear_auto_samples, service)
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)

        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/Mode", 1))

        self.assertTrue(service._auto_mode_cutover_pending)
        service._queue_relay_command.assert_called_once_with(False, 200.0)
        service._publish_local_pm_status.assert_called_once_with(False, 200.0)

    def test_handle_mode_write_manual_to_auto_queues_cutover_when_relay_state_is_unknown(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=0,
            virtual_enable=0,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            auto_samples=deque(),
            manual_override_until=10.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={"/Mode": 0, "/StartStop": 0, "/Enable": 0},
            _time_now=MagicMock(return_value=200.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _clear_auto_samples=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_pm_status=None,
            _last_pm_status_confirmed=False,
            _last_pm_status_at=None,
            _get_worker_snapshot=MagicMock(return_value={"pm_status": None, "pm_confirmed": False}),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._clear_auto_samples = partial(self._clear_auto_samples, service)
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)

        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/Mode", 1))

        self.assertTrue(service._auto_mode_cutover_pending)
        service._queue_relay_command.assert_called_once_with(False, 200.0)
        service._publish_local_pm_status.assert_called_once_with(False, 200.0)

    def test_handle_mode_write_manual_to_auto_skips_cutover_when_relay_is_confirmed_off(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=1,
            virtual_enable=0,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            auto_samples=deque(),
            manual_override_until=10.0,
            _auto_mode_cutover_pending=True,
            _ignore_min_offtime_once=True,
            _dbusservice={"/Mode": 0, "/StartStop": 1, "/Enable": 0},
            _time_now=MagicMock(return_value=200.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _clear_auto_samples=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_pm_status={"output": False},
            _last_pm_status_confirmed=True,
            _last_pm_status_at=199.5,
            _worker_poll_interval_seconds=1.0,
            relay_sync_timeout_seconds=2.0,
            _get_worker_snapshot=MagicMock(
                return_value={
                    "pm_status": {"output": False},
                    "pm_confirmed": True,
                    "pm_captured_at": 199.5,
                    "pv_power": 10,
                    "battery_soc": 50,
                    "grid_power": -10,
                }
            ),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._clear_auto_samples = partial(self._clear_auto_samples, service)
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)

        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/Mode", 1))

        self.assertFalse(service._auto_mode_cutover_pending)
        self.assertFalse(service._ignore_min_offtime_once)
        self.assertEqual(service.virtual_enable, 0)
        service._queue_relay_command.assert_not_called()
        service._publish_local_pm_status.assert_not_called()

    def test_snapshot_write_state_skips_non_mapping_dbusservice_objects(self) -> None:
        service = SimpleNamespace(
            _dbusservice=object(),
            _dbus_publish_state={"/Mode": {"value": 0}},
            _worker_snapshot={"captured_at": 1.0},
        )

        snapshot = DbusWriteController._snapshot_write_state(service)

        self.assertNotIn("_dbusservice", snapshot["mappings"])
        self.assertIn("_dbus_publish_state", snapshot["mappings"])

    def test_handle_enable_write_in_manual_mode_switches_relay(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_startstop=0,
            virtual_enable=0,
            auto_manual_override_seconds=300,
            manual_override_until=0.0,
            _dbusservice={"/StartStop": 0, "/Enable": 0},
            _time_now=MagicMock(return_value=100.0),
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
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

    def test_handle_enable_write_in_manual_mode_uses_charger_backend_when_available(self) -> None:
        charger_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_startstop=0,
            virtual_enable=0,
            auto_manual_override_seconds=300,
            manual_override_until=0.0,
            _charger_backend=charger_backend,
            _dbusservice={"/StartStop": 0, "/Enable": 0},
            _time_now=MagicMock(return_value=100.0),
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)

        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/Enable", 1))

        charger_backend.set_enabled.assert_called_once_with(True)
        service._queue_relay_command.assert_not_called()
        service._publish_local_pm_status.assert_not_called()
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.virtual_startstop, 1)
        self.assertEqual(service.manual_override_until, 400.0)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)

    def test_handle_mode_write_keeps_in_flight_cutover_state_after_relay_side_effects_start(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=1,
            virtual_enable=0,
            auto_start_condition_since=100.0,
            auto_stop_condition_since=200.0,
            auto_stop_condition_reason=None,
            auto_samples=deque([(205.0, 2200.0, -2200.0)]),
            _stop_smoothed_surplus_power=123.0,
            _stop_smoothed_grid_power=45.0,
            manual_override_until=500.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _pending_relay_state=None,
            _pending_relay_requested_at=None,
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=False,
            _last_pm_status={"output": True},
            _last_pm_status_at=150.0,
            _last_pm_status_confirmed=True,
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=150.0,
            _worker_snapshot={"captured_at": 150.0},
            _dbusservice={"/Mode": 0, "/StartStop": 1, "/Enable": 0},
            _dbus_publish_state={"/Mode": {"value": 0, "updated_at": 150.0}},
            _time_now=MagicMock(return_value=200.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _clear_auto_samples=MagicMock(),
            _publish_local_pm_status=MagicMock(side_effect=RuntimeError("publish failed")),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _get_worker_snapshot=MagicMock(return_value={"pv_power": 10, "battery_soc": 50, "grid_power": -10}),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )

        def _queue_side_effect(relay_on: bool, current_time: float) -> None:
            service._pending_relay_state = bool(relay_on)
            service._pending_relay_requested_at = current_time
            service._relay_sync_expected_state = bool(relay_on)
            service._relay_sync_requested_at = current_time
            service._relay_sync_deadline_at = current_time + 2.0
            service._relay_sync_failure_reported = False

        service._queue_relay_command = MagicMock(side_effect=_queue_side_effect)
        service._clear_auto_samples = partial(self._clear_auto_samples, service)
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)

        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/Mode", 1))
        self.assertEqual(service.virtual_mode, 1)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.virtual_startstop, 0)
        self.assertEqual(service.manual_override_until, 0.0)
        self.assertTrue(service._auto_mode_cutover_pending)
        self.assertFalse(service._ignore_min_offtime_once)
        self.assertEqual(list(service.auto_samples), [])
        self.assertEqual(service._stop_smoothed_surplus_power, 123.0)
        self.assertEqual(service._stop_smoothed_grid_power, 45.0)
        self.assertFalse(service._pending_relay_state)
        self.assertEqual(service._pending_relay_requested_at, 200.0)
        self.assertFalse(service._relay_sync_expected_state)
        self.assertEqual(service._relay_sync_requested_at, 200.0)
        self.assertEqual(service._relay_sync_deadline_at, 202.0)
        self.assertFalse(service._relay_sync_failure_reported)
        self.assertEqual(service._dbusservice["/Mode"], 1)
        self.assertEqual(service._dbusservice["/StartStop"], 1)
        self.assertEqual(service._dbusservice["/Enable"], 1)
        service._save_runtime_state.assert_called_once()

    def test_handle_write_rolls_back_when_failure_happens_before_relay_side_effects(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=0,
            virtual_startstop=0,
            virtual_enable=0,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            auto_samples=deque(),
            manual_override_until=0.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={"/AutoStart": 0},
            _time_now=MagicMock(return_value=100.0),
            _publish_dbus_path=MagicMock(side_effect=RuntimeError("fail")),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )

        controller = DbusWriteController(WriteControllerPort(service))

        self.assertFalse(controller.handle_write("/AutoStart", 1))
        self.assertEqual(service.virtual_autostart, 0)

    def test_restore_write_state_recovers_values_deques_and_mappings_from_non_container_targets(self) -> None:
        service = SimpleNamespace(
            scalar_value=0.0,
            sample_buffer=None,
            mapping_state=None,
        )
        snapshot = {
            "attrs": {},
            "deques": {"sample_buffer": deque([(1.0, 2.0, 3.0)])},
            "values": {"scalar_value": 12.5},
            "mappings": {"mapping_state": {"mode": 1}},
        }

        DbusWriteController._restore_write_state(service, snapshot)

        self.assertEqual(service.scalar_value, 12.5)
        self.assertIsInstance(service.sample_buffer, deque)
        self.assertEqual(list(service.sample_buffer), [(1.0, 2.0, 3.0)])
        self.assertEqual(service.mapping_state, {"mode": 1})

    def test_restore_write_state_ignores_dbus_restore_errors(self) -> None:
        class FailingDbusService(dict[str, object]):
            def __setitem__(self, key: str, value: object) -> None:
                raise RuntimeError("dbus write failed")

        service = SimpleNamespace(_dbusservice=FailingDbusService())
        snapshot = {
            "attrs": {},
            "deques": {},
            "values": {},
            "mappings": {},
            "dbus_paths": {"/Mode": 0},
        }

        DbusWriteController._restore_write_state(service, snapshot)

    def test_handle_mode_write_returns_true_when_save_fails_after_relay_side_effects_started(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=1,
            virtual_enable=0,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            auto_samples=deque(),
            manual_override_until=500.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _pending_relay_state=None,
            _pending_relay_requested_at=None,
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=False,
            _dbusservice={"/Mode": 0, "/StartStop": 1, "/Enable": 0},
            _time_now=MagicMock(return_value=200.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _clear_auto_samples=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(return_value={"output": False}),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _get_worker_snapshot=MagicMock(return_value={"pv_power": 10, "battery_soc": 50, "grid_power": -10}),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(side_effect=RuntimeError("save failed")),
        )
        service._clear_auto_samples = partial(self._clear_auto_samples, service)
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)
        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/Mode", 1))
        self.assertEqual(service.virtual_mode, 1)
        self.assertTrue(service._auto_mode_cutover_pending)
        service._queue_relay_command.assert_called_once_with(False, 200.0)
        service._publish_local_pm_status.assert_called_once_with(False, 200.0)

    def test_handle_autostart_write_restores_dbus_path_when_save_fails_before_relay_side_effects(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=0,
            virtual_startstop=0,
            virtual_enable=0,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            auto_samples=deque(),
            manual_override_until=0.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={"/AutoStart": 0},
            _dbus_publish_state={"/AutoStart": {"value": 0, "updated_at": 10.0}},
            _time_now=MagicMock(return_value=200.0),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(side_effect=RuntimeError("save failed")),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)

        controller = DbusWriteController(WriteControllerPort(service))

        self.assertFalse(controller.handle_write("/AutoStart", 1))
        self.assertEqual(service.virtual_autostart, 0)
        self.assertEqual(service._dbusservice["/AutoStart"], 0)
        self.assertEqual(service._dbus_publish_state["/AutoStart"]["value"], 0)

    def test_mode_autostart_current_and_error_paths(self) -> None:
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
            _normalize_mode=self._normalize_mode_5_to_2,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _clear_auto_samples=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={"pv_power": 10, "battery_soc": 50, "grid_power": -10}),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._clear_auto_samples = partial(self._clear_auto_samples, service)
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)
        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/Mode", 5))
        self.assertEqual(service.virtual_mode, 2)
        self.assertEqual(service.manual_override_until, 0.0)
        self.assertTrue(service._auto_mode_cutover_pending)
        service._queue_relay_command.assert_called_once_with(False, 42.0)

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
        self.assertEqual(service.virtual_autostart, 1)
        service._save_runtime_state.assert_not_called()

    def test_manual_startstop_and_setcurrent_use_charger_backend_when_available(self) -> None:
        charger_backend = SimpleNamespace(
            set_enabled=MagicMock(),
            set_current=MagicMock(),
        )
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=0,
            virtual_startstop=0,
            virtual_enable=0,
            auto_manual_override_seconds=300,
            manual_override_until=0.0,
            max_current=16.0,
            min_current=6.0,
            virtual_set_current=10.0,
            _charger_backend=charger_backend,
            _dbusservice={"/StartStop": 0, "/Enable": 0, "/SetCurrent": 10.0},
            _time_now=MagicMock(return_value=100.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)
        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/StartStop", 1))
        charger_backend.set_enabled.assert_called_once_with(True)
        service._queue_relay_command.assert_not_called()
        service._publish_local_pm_status.assert_not_called()
        self.assertEqual(service.virtual_startstop, 1)
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.manual_override_until, 400.0)

        self.assertTrue(controller.handle_write("/SetCurrent", 12.5))
        charger_backend.set_current.assert_called_once_with(12.5)
        self.assertEqual(service.virtual_set_current, 12.5)
        self.assertEqual(service._dbusservice["/SetCurrent"], 12.5)

    def test_handle_setcurrent_returns_true_when_save_fails_after_charger_side_effects_started(self) -> None:
        charger_backend = SimpleNamespace(set_current=MagicMock())
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=0,
            virtual_startstop=0,
            virtual_enable=0,
            max_current=16.0,
            min_current=6.0,
            virtual_set_current=10.0,
            _charger_backend=charger_backend,
            _dbusservice={"/SetCurrent": 10.0},
            _time_now=MagicMock(return_value=100.0),
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(side_effect=RuntimeError("save failed")),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)
        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/SetCurrent", 12.5))

        charger_backend.set_current.assert_called_once_with(12.5)
        self.assertEqual(service.virtual_set_current, 12.5)
        self.assertEqual(service._dbusservice["/SetCurrent"], 12.5)

    def test_startstop_and_enable_in_auto_mode_cover_off_and_on_paths(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_startstop=0,
            virtual_enable=0,
            auto_manual_override_seconds=300,
            manual_override_until=0.0,
            _dbusservice={"/StartStop": 0, "/Enable": 0},
            _time_now=MagicMock(return_value=100.0),
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
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

    def test_mode_transition_and_publish_helpers_cover_remaining_branches(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_startstop=0,
            virtual_enable=1,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _publish_dbus_path=MagicMock(),
        )
        controller = DbusWriteController(WriteControllerPort(service))

        controller._handle_mode_transition_to_auto(1, 100.0)
        self.assertFalse(hasattr(service, "manual_override_until"))

        controller._publish_startstop_enable(service, 100.0)
        service._publish_dbus_path.assert_any_call("/StartStop", 1, 100.0, force=True)
        service._publish_dbus_path.assert_any_call("/Enable", 1, 100.0, force=True)

    def test_handle_phase_selection_write_applies_supported_selection_when_relay_is_off(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=0,
            virtual_enable=0,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1", "P1_P2"),
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            manual_override_until=0.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={
                "/PhaseSelection": "P1",
                "/PhaseSelectionActive": "P1",
                "/SupportedPhaseSelections": "P1,P1_P2",
            },
            _time_now=MagicMock(return_value=100.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _phase_selection_requires_pause=MagicMock(return_value=True),
            _apply_phase_selection=MagicMock(),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_pm_status={"output": False},
            _last_pm_status_confirmed=True,
            _get_worker_snapshot=MagicMock(return_value={"pm_status": {"output": False}, "pm_confirmed": True}),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._apply_phase_selection.side_effect = partial(self._apply_phase_selection, service)
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)

        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/PhaseSelection", "P1_P2"))

        service._apply_phase_selection.assert_called_once_with("P1_P2")
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service.active_phase_selection, "P1_P2")
        self.assertEqual(service._dbusservice["/PhaseSelection"], "P1_P2")
        self.assertEqual(service._dbusservice["/PhaseSelectionActive"], "P1_P2")
        self.assertEqual(service._dbusservice["/SupportedPhaseSelections"], "P1,P1_P2")

    def test_handle_phase_selection_write_stages_live_change_when_backend_requires_pause(self) -> None:
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=1,
            virtual_enable=1,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1", "P1_P2"),
            _phase_switch_pending_selection=None,
            _phase_switch_state=None,
            _phase_switch_requested_at=None,
            _phase_switch_stable_until=None,
            _phase_switch_resume_relay=False,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            manual_override_until=0.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _dbusservice={
                "/PhaseSelection": "P1",
                "/PhaseSelectionActive": "P1",
                "/SupportedPhaseSelections": "P1,P1_P2",
            },
            _time_now=MagicMock(return_value=100.0),
            _normalize_mode=self._normalize_mode,
            _mode_uses_auto_logic=self._mode_uses_auto_logic,
            _phase_selection_requires_pause=MagicMock(return_value=True),
            _apply_phase_selection=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_pm_status={"output": True},
            _last_pm_status_confirmed=True,
            _get_worker_snapshot=MagicMock(return_value={"pm_status": {"output": True}, "pm_confirmed": True}),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)

        controller = DbusWriteController(WriteControllerPort(service))

        self.assertTrue(controller.handle_write("/PhaseSelection", "P1_P2"))
        service._queue_relay_command.assert_called_once_with(False, 100.0)
        service._publish_local_pm_status.assert_called_once_with(False, 100.0)
        service._apply_phase_selection.assert_not_called()
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertEqual(service._phase_switch_pending_selection, "P1_P2")
        self.assertEqual(service._phase_switch_state, "waiting-relay-off")
        self.assertEqual(service._phase_switch_requested_at, 100.0)
        self.assertIsNone(service._phase_switch_stable_until)
        self.assertTrue(service._phase_switch_resume_relay)
        self.assertEqual(service._dbusservice["/PhaseSelection"], "P1_P2")
        self.assertEqual(service._dbusservice["/PhaseSelectionActive"], "P1")
        self.assertEqual(service._dbusservice["/SupportedPhaseSelections"], "P1,P1_P2")
