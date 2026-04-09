# SPDX-License-Identifier: GPL-3.0-or-later
import math
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from dbus_shelly_wallbox_auto_input_supervisor import AutoInputSupervisor


class TestAutoInputSupervisor(unittest.TestCase):
    def test_coerce_snapshot_timestamp_and_snapshot_mtime_cover_invalid_inputs(self):
        self.assertIsNone(AutoInputSupervisor._coerce_snapshot_timestamp(None))
        self.assertIsNone(AutoInputSupervisor._coerce_snapshot_timestamp(True))
        self.assertIsNone(AutoInputSupervisor._coerce_snapshot_timestamp(False))
        self.assertIsNone(AutoInputSupervisor._coerce_snapshot_timestamp("nope"))
        self.assertIsNone(AutoInputSupervisor._coerce_snapshot_timestamp(float("nan")))
        self.assertIsNone(AutoInputSupervisor._coerce_snapshot_timestamp(float("inf")))
        self.assertEqual(AutoInputSupervisor._coerce_snapshot_timestamp("12.5"), 12.5)

        service = SimpleNamespace(_stat_path=MagicMock(side_effect=OSError("missing")))
        controller = AutoInputSupervisor(service)
        self.assertIsNone(controller._snapshot_mtime_ns("/tmp/missing.json"))

    def test_helper_snapshot_age_falls_back_to_helper_start_time(self):
        service = SimpleNamespace(
            _auto_input_snapshot_last_seen=None,
            _auto_input_helper_last_start_at=90.0,
        )
        controller = AutoInputSupervisor(service)
        self.assertEqual(controller._helper_snapshot_age(100.0), 10.0)

    def test_snapshot_freshness_not_future_accepts_missing_timestamp(self):
        service = SimpleNamespace()
        controller = AutoInputSupervisor(service)

        self.assertTrue(controller._snapshot_freshness_not_future("/tmp/auto.json", None, 100.0))

    def test_ensure_helper_process_restarts_stale_helper(self):
        process = MagicMock()
        process.poll.return_value = None
        process.pid = 4321
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _auto_input_helper_process=process,
            _auto_input_helper_last_start_at=10.0,
            _auto_input_helper_restart_requested_at=None,
            _auto_input_snapshot_last_seen=70.0,
            auto_input_helper_stale_seconds=15.0,
            auto_input_helper_restart_seconds=5.0,
            _time_now=MagicMock(return_value=100.0),
            _stop_auto_input_helper=MagicMock(),
            _spawn_auto_input_helper=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = AutoInputSupervisor(service)
        controller.ensure_helper_process()

        service._stop_auto_input_helper.assert_called_once_with(force=False)
        self.assertEqual(service._auto_input_helper_restart_requested_at, 100.0)
        service._spawn_auto_input_helper.assert_not_called()

    def test_refresh_snapshot_uses_heartbeat_for_staleness_and_updates_fields(self):
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            auto_input_snapshot_path="/tmp/auto-helper.json",
            auto_input_helper_stale_seconds=15.0,
            auto_input_helper_restart_seconds=5.0,
            _time_now=MagicMock(return_value=130.0),
            _stat_path=MagicMock(return_value=SimpleNamespace(st_mtime_ns=3, st_mtime=0.0)),
            _load_json_file=MagicMock(
                return_value={
                    "snapshot_version": AutoInputSupervisor.SNAPSHOT_SCHEMA_VERSION,
                    "captured_at": 100.0,
                    "heartbeat_at": 130.0,
                    "pv_captured_at": 100.0,
                    "pv_power": 2300.0,
                    "battery_captured_at": 100.0,
                    "battery_soc": 57.0,
                    "grid_captured_at": 100.0,
                    "grid_power": -2100.0,
                }
            ),
            _warning_throttled=MagicMock(),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            virtual_mode=1,
            _auto_input_snapshot_mtime_ns=None,
            _auto_input_snapshot_last_seen=None,
            _update_worker_snapshot=MagicMock(),
        )

        controller = AutoInputSupervisor(service)
        controller.refresh_snapshot()

        self.assertEqual(service._auto_input_snapshot_last_seen, 130.0)
        self.assertEqual(service._auto_input_snapshot_last_captured_at, 100.0)
        self.assertEqual(service._auto_input_snapshot_version, AutoInputSupervisor.SNAPSHOT_SCHEMA_VERSION)
        service._update_worker_snapshot.assert_called_once_with(
            captured_at=100.0,
            auto_mode_active=True,
            snapshot_version=AutoInputSupervisor.SNAPSHOT_SCHEMA_VERSION,
            pv_captured_at=100.0,
            pv_power=2300.0,
            battery_captured_at=100.0,
            battery_soc=57.0,
            grid_captured_at=100.0,
            grid_power=-2100.0,
        )

    def test_refresh_snapshot_warns_for_read_failure_and_invalid_payload(self):
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            auto_input_snapshot_path="/tmp/auto-helper.json",
            auto_input_helper_stale_seconds=15.0,
            auto_input_helper_restart_seconds=5.0,
            _time_now=MagicMock(return_value=130.0),
            _stat_path=MagicMock(return_value=SimpleNamespace(st_mtime_ns=3, st_mtime=0.0)),
            _warning_throttled=MagicMock(),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            virtual_mode=1,
            _auto_input_snapshot_mtime_ns=None,
            _auto_input_snapshot_last_seen=None,
            _update_worker_snapshot=MagicMock(),
        )

        controller = AutoInputSupervisor(service)

        service._load_json_file = MagicMock(side_effect=RuntimeError("boom"))
        controller.refresh_snapshot()
        service._warning_throttled.assert_called_once()
        service._update_worker_snapshot.assert_not_called()

        service._warning_throttled.reset_mock()
        service._load_json_file = MagicMock(return_value=["not", "a", "dict"])
        controller.refresh_snapshot()
        service._warning_throttled.assert_called_once()
        service._update_worker_snapshot.assert_not_called()

    def test_refresh_snapshot_rejects_missing_captured_at_and_invalid_version(self):
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            auto_input_snapshot_path="/tmp/auto-helper.json",
            auto_input_helper_stale_seconds=15.0,
            auto_input_helper_restart_seconds=5.0,
            _time_now=MagicMock(return_value=130.0),
            _stat_path=MagicMock(return_value=SimpleNamespace(st_mtime_ns=4, st_mtime=0.0)),
            _load_json_file=MagicMock(
                return_value={
                    "snapshot_version": AutoInputSupervisor.SNAPSHOT_SCHEMA_VERSION,
                    "heartbeat_at": 100.0,
                    "pv_captured_at": "98.0",
                    "pv_power": 2300.0,
                    "battery_captured_at": "97.0",
                    "battery_soc": 57.0,
                    "grid_captured_at": "96.0",
                    "grid_power": -2100.0,
                }
            ),
            _warning_throttled=MagicMock(),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            virtual_mode=0,
            _auto_input_snapshot_mtime_ns=None,
            _auto_input_snapshot_last_seen=None,
            _auto_input_snapshot_last_captured_at=None,
            _auto_input_snapshot_version=None,
            _update_worker_snapshot=MagicMock(),
        )

        controller = AutoInputSupervisor(service)
        controller.refresh_snapshot()

        service._warning_throttled.assert_called_once()
        service._update_worker_snapshot.assert_not_called()
        self.assertIsNone(service._auto_input_snapshot_last_seen)

        service._warning_throttled.reset_mock()
        service._stat_path = MagicMock(return_value=SimpleNamespace(st_mtime_ns=5, st_mtime=0.0))
        service._load_json_file = MagicMock(
            return_value={
                "snapshot_version": 999,
                "captured_at": 100.0,
                "heartbeat_at": 100.0,
                "pv_captured_at": 98.0,
                "pv_power": 2300.0,
                "battery_captured_at": 97.0,
                "battery_soc": 57.0,
                "grid_captured_at": 96.0,
                "grid_power": -2100.0,
            }
        )
        controller.refresh_snapshot()
        service._warning_throttled.assert_called_once()
        service._update_worker_snapshot.assert_not_called()

    def test_refresh_snapshot_rejects_non_monotonic_captured_at(self):
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            auto_input_snapshot_path="/tmp/auto-helper.json",
            auto_input_helper_stale_seconds=15.0,
            auto_input_helper_restart_seconds=5.0,
            _time_now=MagicMock(return_value=130.0),
            _stat_path=MagicMock(return_value=SimpleNamespace(st_mtime_ns=6, st_mtime=0.0)),
            _load_json_file=MagicMock(
                return_value={
                    "snapshot_version": AutoInputSupervisor.SNAPSHOT_SCHEMA_VERSION,
                    "captured_at": 95.0,
                    "heartbeat_at": 130.0,
                    "pv_captured_at": 95.0,
                    "pv_power": 2300.0,
                    "battery_captured_at": 95.0,
                    "battery_soc": 57.0,
                    "grid_captured_at": 95.0,
                    "grid_power": -2100.0,
                }
            ),
            _warning_throttled=MagicMock(),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            virtual_mode=1,
            _auto_input_snapshot_mtime_ns=None,
            _auto_input_snapshot_last_seen=120.0,
            _auto_input_snapshot_last_captured_at=100.0,
            _auto_input_snapshot_version=AutoInputSupervisor.SNAPSHOT_SCHEMA_VERSION,
            _update_worker_snapshot=MagicMock(),
        )

        controller = AutoInputSupervisor(service)
        controller.refresh_snapshot()

        service._warning_throttled.assert_called_once()
        service._update_worker_snapshot.assert_not_called()

    def test_validate_snapshot_dict_rejects_invalid_version_timestamps_and_numeric_fields(self):
        service = SimpleNamespace(
            auto_input_helper_restart_seconds=5.0,
            _warning_throttled=MagicMock(),
        )
        controller = AutoInputSupervisor(service)
        base_snapshot = {
            "snapshot_version": AutoInputSupervisor.SNAPSHOT_SCHEMA_VERSION,
            "captured_at": 100.0,
            "heartbeat_at": 100.0,
            "pv_captured_at": 100.0,
            "pv_power": 2300.0,
            "battery_captured_at": 100.0,
            "battery_soc": 57.0,
            "grid_captured_at": 100.0,
            "grid_power": -2100.0,
        }

        self.assertIsNone(controller._validate_snapshot_version("nope"))
        self.assertIsNone(controller._validate_snapshot_version(True))

        invalid_timestamp = dict(base_snapshot, pv_captured_at="bad")
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", invalid_timestamp))

        service._warning_throttled.reset_mock()
        invalid_numeric = dict(base_snapshot, pv_power="bad")
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", invalid_numeric))

        service._warning_throttled.reset_mock()
        invalid_timestamp_nan = dict(base_snapshot, captured_at=math.nan)
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", invalid_timestamp_nan))

        service._warning_throttled.reset_mock()
        invalid_timestamp_inf = dict(base_snapshot, heartbeat_at=math.inf)
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", invalid_timestamp_inf))

        service._warning_throttled.reset_mock()
        invalid_numeric_nan = dict(base_snapshot, pv_power=math.nan)
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", invalid_numeric_nan))

        service._warning_throttled.reset_mock()
        invalid_numeric_inf = dict(base_snapshot, battery_soc=math.inf)
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", invalid_numeric_inf))

        service._warning_throttled.reset_mock()
        invalid_numeric_bool = dict(base_snapshot, pv_power=True)
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", invalid_numeric_bool))

        service._warning_throttled.reset_mock()
        invalid_battery_soc = dict(base_snapshot, battery_soc=150.0)
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", invalid_battery_soc))

        service._warning_throttled.reset_mock()
        missing_timestamps = dict(base_snapshot, captured_at=None)
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", missing_timestamps))

        service._warning_throttled.reset_mock()
        regressed_heartbeat = dict(base_snapshot, heartbeat_at=99.0)
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", regressed_heartbeat))

        service._warning_throttled.reset_mock()
        source_after_snapshot = dict(base_snapshot, pv_captured_at=101.0)
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", source_after_snapshot))

    def test_validate_snapshot_dict_rejects_source_values_without_matching_timestamps(self):
        service = SimpleNamespace(
            auto_input_helper_restart_seconds=5.0,
            _warning_throttled=MagicMock(),
        )
        controller = AutoInputSupervisor(service)
        snapshot = {
            "snapshot_version": AutoInputSupervisor.SNAPSHOT_SCHEMA_VERSION,
            "captured_at": 100.0,
            "heartbeat_at": 100.0,
            "pv_captured_at": None,
            "pv_power": 2300.0,
            "battery_captured_at": 100.0,
            "battery_soc": 57.0,
            "grid_captured_at": None,
            "grid_power": -2100.0,
        }

        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", snapshot))

        service._warning_throttled.reset_mock()
        timestamp_without_value = dict(snapshot, pv_captured_at=100.0, pv_power=None)
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", timestamp_without_value))

    def test_validate_snapshot_dict_allows_source_absence_only_when_value_and_timestamp_are_both_none(self):
        service = SimpleNamespace(
            auto_input_helper_restart_seconds=5.0,
            _warning_throttled=MagicMock(),
        )
        controller = AutoInputSupervisor(service)
        snapshot = {
            "snapshot_version": AutoInputSupervisor.SNAPSHOT_SCHEMA_VERSION,
            "captured_at": 100.0,
            "heartbeat_at": 100.0,
            "pv_captured_at": None,
            "pv_power": None,
            "battery_captured_at": 100.0,
            "battery_soc": 57.0,
            "grid_captured_at": 100.0,
            "grid_power": -2100.0,
        }

        normalized = controller._validate_snapshot_dict("/tmp/auto.json", snapshot)

        self.assertIsNotNone(normalized)
        self.assertIsNone(normalized["pv_captured_at"])
        self.assertIsNone(normalized["pv_power"])
        service._warning_throttled.assert_not_called()

    def test_refresh_snapshot_rejects_future_snapshot_and_source_timestamps(self):
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            auto_input_snapshot_path="/tmp/auto-helper.json",
            auto_input_helper_stale_seconds=15.0,
            auto_input_helper_restart_seconds=5.0,
            _time_now=MagicMock(return_value=130.0),
            _stat_path=MagicMock(return_value=SimpleNamespace(st_mtime_ns=7, st_mtime=0.0)),
            _load_json_file=MagicMock(
                return_value={
                    "snapshot_version": AutoInputSupervisor.SNAPSHOT_SCHEMA_VERSION,
                    "captured_at": 131.5,
                    "heartbeat_at": 131.5,
                    "pv_captured_at": 131.5,
                    "pv_power": 2300.0,
                    "battery_captured_at": 131.5,
                    "battery_soc": 57.0,
                    "grid_captured_at": 131.5,
                    "grid_power": -2100.0,
                }
            ),
            _warning_throttled=MagicMock(),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            virtual_mode=1,
            _auto_input_snapshot_mtime_ns=None,
            _auto_input_snapshot_last_seen=None,
            _auto_input_snapshot_last_captured_at=None,
            _auto_input_snapshot_version=None,
            _update_worker_snapshot=MagicMock(),
        )

        controller = AutoInputSupervisor(service)
        controller.refresh_snapshot()

        service._warning_throttled.assert_called_once()
        service._update_worker_snapshot.assert_not_called()

        service._warning_throttled.reset_mock()
        service._stat_path = MagicMock(return_value=SimpleNamespace(st_mtime_ns=8, st_mtime=0.0))
        service._load_json_file = MagicMock(
            return_value={
                "snapshot_version": AutoInputSupervisor.SNAPSHOT_SCHEMA_VERSION,
                "captured_at": 130.0,
                "heartbeat_at": 130.0,
                "pv_captured_at": 132.0,
                "pv_power": 2300.0,
                "battery_captured_at": 130.0,
                "battery_soc": 57.0,
                "grid_captured_at": 130.0,
                "grid_power": -2100.0,
            }
        )
        controller.refresh_snapshot()

        service._warning_throttled.assert_called_once()
        service._update_worker_snapshot.assert_not_called()

    def test_stop_helper_covers_none_exited_running_and_force_paths(self):
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _auto_input_helper_process=None,
            _auto_input_helper_restart_requested_at="pending",
        )
        controller = AutoInputSupervisor(service)
        controller.stop_helper()
        service._ensure_worker_state.assert_called_once_with()

        exited_process = MagicMock()
        exited_process.poll.return_value = 0
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _auto_input_helper_process=exited_process,
            _auto_input_helper_restart_requested_at="pending",
        )
        controller = AutoInputSupervisor(service)
        controller.stop_helper()
        self.assertIsNone(service._auto_input_helper_process)
        self.assertIsNone(service._auto_input_helper_restart_requested_at)

        running_process = MagicMock()
        running_process.poll.return_value = None
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _auto_input_helper_process=running_process,
            _auto_input_helper_restart_requested_at=None,
        )
        controller = AutoInputSupervisor(service)
        controller.stop_helper()
        running_process.terminate.assert_called_once_with()

        running_process = MagicMock()
        running_process.poll.return_value = None
        service._auto_input_helper_process = running_process
        controller = AutoInputSupervisor(service)
        controller.stop_helper(force=True)
        running_process.kill.assert_called_once_with()

        broken_process = MagicMock()
        broken_process.poll.return_value = None
        broken_process.terminate.side_effect = RuntimeError("stuck")
        service._auto_input_helper_process = broken_process
        controller = AutoInputSupervisor(service)
        with patch("dbus_shelly_wallbox_auto_input_supervisor.logging.debug") as debug_mock:
            controller.stop_helper()
        debug_mock.assert_called_once()

    def test_ensure_helper_process_handles_exited_process_cooldown_and_spawn_failure(self):
        exited_process = MagicMock()
        exited_process.poll.return_value = 1
        exited_process.pid = 4444
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _auto_input_helper_process=exited_process,
            _auto_input_helper_last_start_at=98.0,
            _auto_input_helper_restart_requested_at="pending",
            _auto_input_snapshot_last_seen=None,
            auto_input_helper_stale_seconds=15.0,
            auto_input_helper_restart_seconds=5.0,
            _time_now=MagicMock(return_value=100.0),
            _stop_auto_input_helper=MagicMock(),
            _spawn_auto_input_helper=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = AutoInputSupervisor(service)
        with patch("dbus_shelly_wallbox_auto_input_supervisor.logging.warning") as warning_mock:
            controller.ensure_helper_process()

        warning_mock.assert_called_once()
        self.assertIsNone(service._auto_input_helper_process)
        self.assertIsNone(service._auto_input_helper_restart_requested_at)
        service._spawn_auto_input_helper.assert_not_called()

        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _auto_input_helper_process=None,
            _auto_input_helper_last_start_at=0.0,
            _auto_input_helper_restart_requested_at=None,
            auto_input_helper_restart_seconds=5.0,
            _time_now=MagicMock(return_value=100.0),
            _spawn_auto_input_helper=MagicMock(side_effect=RuntimeError("spawn failed")),
            _warning_throttled=MagicMock(),
        )

        controller = AutoInputSupervisor(service)
        controller.ensure_helper_process()
        service._warning_throttled.assert_called_once()

    def test_spawn_helper_and_stale_helper_edge_paths(self):
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _auto_input_helper_path=MagicMock(return_value="/tmp/helper.py"),
            _config_path=MagicMock(return_value="/tmp/config.ini"),
            auto_input_snapshot_path="/tmp/snapshot.json",
            _auto_input_helper_process=None,
            _auto_input_helper_last_start_at=0.0,
            _auto_input_helper_restart_requested_at="pending",
            _stop_auto_input_helper=MagicMock(),
        )
        process = MagicMock(pid=5555)
        controller = AutoInputSupervisor(service)
        with patch("dbus_shelly_wallbox_auto_input_supervisor.os.getpid", return_value=1234):
            with patch("dbus_shelly_wallbox_auto_input_supervisor.subprocess.Popen", return_value=process) as popen:
                controller.spawn_helper()

        popen.assert_called_once()
        self.assertEqual(service._auto_input_helper_process, process)
        self.assertEqual(service._auto_input_helper_last_start_at, 100.0)
        self.assertIsNone(service._auto_input_helper_restart_requested_at)

        process = MagicMock(pid=4444)
        service = SimpleNamespace(
            _auto_input_snapshot_last_seen=None,
            _auto_input_helper_last_start_at=0.0,
            auto_input_helper_stale_seconds=15.0,
            _auto_input_helper_restart_requested_at=None,
            auto_input_helper_restart_seconds=5.0,
            _stop_auto_input_helper=MagicMock(),
        )
        controller = AutoInputSupervisor(service)
        self.assertIsNone(controller._helper_snapshot_age(100.0))
        self.assertFalse(controller._handle_stale_running_helper(process, 100.0, 10.0))
        service._auto_input_helper_restart_requested_at = 90.0
        self.assertTrue(controller._handle_stale_running_helper(process, 100.0, 20.0))
        service._stop_auto_input_helper.assert_called_once_with(force=True)

    def test_handle_existing_helper_and_refresh_snapshot_early_return_paths(self):
        process = MagicMock()
        process.poll.return_value = None
        service = SimpleNamespace(
            _helper_snapshot_age=MagicMock(),
            _handle_stale_running_helper=MagicMock(return_value=False),
        )
        controller = AutoInputSupervisor(service)
        with patch.object(controller, "_helper_snapshot_age", return_value=1.0):
            with patch.object(controller, "_handle_stale_running_helper", return_value=False):
                self.assertTrue(controller._handle_existing_helper_process(process, 100.0))

        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            auto_input_snapshot_path="",
            _time_now=MagicMock(return_value=100.0),
            _auto_input_snapshot_mtime_ns=None,
        )
        controller = AutoInputSupervisor(service)
        controller.refresh_snapshot()
        service._ensure_worker_state.assert_called_once_with()

        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            auto_input_snapshot_path="/tmp/snapshot.json",
            _time_now=MagicMock(return_value=100.0),
            _snapshot_mtime_ns=MagicMock(return_value=None),
            _auto_input_snapshot_mtime_ns=None,
        )
        controller = AutoInputSupervisor(service)
        with patch.object(controller, "_snapshot_mtime_ns", return_value=None):
            controller.refresh_snapshot()

        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            auto_input_snapshot_path="/tmp/snapshot.json",
            _time_now=MagicMock(return_value=100.0),
            _stat_path=MagicMock(return_value=SimpleNamespace(st_mtime_ns=3, st_mtime=0.0)),
            _auto_input_snapshot_mtime_ns=3,
        )
        controller = AutoInputSupervisor(service)
        controller.refresh_snapshot()
