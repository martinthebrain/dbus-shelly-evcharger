# SPDX-License-Identifier: GPL-3.0-or-later
import math
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from venus_evcharger.inputs.supervisor import AutoInputSupervisor


class TestAutoInputSupervisorValidation(unittest.TestCase):
    def test_refresh_snapshot_uses_heartbeat_for_staleness_and_updates_fields(self):
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            auto_input_snapshot_path="/tmp/auto-helper.json",
            auto_input_helper_stale_seconds=15.0,
            auto_input_helper_restart_seconds=5.0,
            _time_now=MagicMock(return_value=130.0),
            _stat_path=MagicMock(return_value=SimpleNamespace(st_mtime_ns=3, st_mtime=0.0)),
            _load_json_file=MagicMock(return_value={"snapshot_version": AutoInputSupervisor.SNAPSHOT_SCHEMA_VERSION, "captured_at": 100.0, "heartbeat_at": 130.0, "pv_captured_at": 100.0, "pv_power": 2300.0, "battery_captured_at": 100.0, "battery_soc": 57.0, "grid_captured_at": 100.0, "grid_power": -2100.0}),
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
        service._update_worker_snapshot.assert_called_once()

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

    def test_refresh_snapshot_rejects_missing_captured_at_and_invalid_version(self):
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            auto_input_snapshot_path="/tmp/auto-helper.json",
            auto_input_helper_stale_seconds=15.0,
            auto_input_helper_restart_seconds=5.0,
            _time_now=MagicMock(return_value=130.0),
            _stat_path=MagicMock(return_value=SimpleNamespace(st_mtime_ns=4, st_mtime=0.0)),
            _load_json_file=MagicMock(return_value={"snapshot_version": AutoInputSupervisor.SNAPSHOT_SCHEMA_VERSION, "heartbeat_at": 100.0, "pv_captured_at": "98.0", "pv_power": 2300.0, "battery_captured_at": "97.0", "battery_soc": 57.0, "grid_captured_at": "96.0", "grid_power": -2100.0}),
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

        service._warning_throttled.reset_mock()
        service._stat_path = MagicMock(return_value=SimpleNamespace(st_mtime_ns=5, st_mtime=0.0))
        service._load_json_file = MagicMock(return_value={"snapshot_version": 999, "captured_at": 100.0, "heartbeat_at": 100.0, "pv_captured_at": 98.0, "pv_power": 2300.0, "battery_captured_at": 97.0, "battery_soc": 57.0, "grid_captured_at": 96.0, "grid_power": -2100.0})
        controller.refresh_snapshot()
        service._warning_throttled.assert_called_once()

    def test_refresh_snapshot_rejects_non_monotonic_captured_at(self):
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            auto_input_snapshot_path="/tmp/auto-helper.json",
            auto_input_helper_stale_seconds=15.0,
            auto_input_helper_restart_seconds=5.0,
            _time_now=MagicMock(return_value=130.0),
            _stat_path=MagicMock(return_value=SimpleNamespace(st_mtime_ns=6, st_mtime=0.0)),
            _load_json_file=MagicMock(return_value={"snapshot_version": AutoInputSupervisor.SNAPSHOT_SCHEMA_VERSION, "captured_at": 95.0, "heartbeat_at": 130.0, "pv_captured_at": 95.0, "pv_power": 2300.0, "battery_captured_at": 95.0, "battery_soc": 57.0, "grid_captured_at": 95.0, "grid_power": -2100.0}),
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
        service = SimpleNamespace(auto_input_helper_restart_seconds=5.0, _warning_throttled=MagicMock())
        controller = AutoInputSupervisor(service)
        base_snapshot = {"snapshot_version": AutoInputSupervisor.SNAPSHOT_SCHEMA_VERSION, "captured_at": 100.0, "heartbeat_at": 100.0, "pv_captured_at": 100.0, "pv_power": 2300.0, "battery_captured_at": 100.0, "battery_soc": 57.0, "grid_captured_at": 100.0, "grid_power": -2100.0}
        self.assertIsNone(controller._validate_snapshot_version("nope"))
        self.assertIsNone(controller._validate_snapshot_version(True))
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", dict(base_snapshot, pv_captured_at="bad")))
        service._warning_throttled.reset_mock()
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", dict(base_snapshot, pv_power="bad")))
        service._warning_throttled.reset_mock()
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", dict(base_snapshot, captured_at=math.nan)))
        service._warning_throttled.reset_mock()
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", dict(base_snapshot, heartbeat_at=math.inf)))
        service._warning_throttled.reset_mock()
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", dict(base_snapshot, pv_power=math.nan)))
        service._warning_throttled.reset_mock()
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", dict(base_snapshot, battery_soc=math.inf)))
        service._warning_throttled.reset_mock()
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", dict(base_snapshot, pv_power=True)))
        service._warning_throttled.reset_mock()
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", dict(base_snapshot, battery_soc=150.0)))
        service._warning_throttled.reset_mock()
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", dict(base_snapshot, captured_at=None)))
        service._warning_throttled.reset_mock()
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", dict(base_snapshot, heartbeat_at=99.0)))
        service._warning_throttled.reset_mock()
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", dict(base_snapshot, pv_captured_at=101.0)))

    def test_validate_snapshot_dict_rejects_source_values_without_matching_timestamps(self):
        service = SimpleNamespace(auto_input_helper_restart_seconds=5.0, _warning_throttled=MagicMock())
        controller = AutoInputSupervisor(service)
        snapshot = {"snapshot_version": AutoInputSupervisor.SNAPSHOT_SCHEMA_VERSION, "captured_at": 100.0, "heartbeat_at": 100.0, "pv_captured_at": None, "pv_power": 2300.0, "battery_captured_at": 100.0, "battery_soc": 57.0, "grid_captured_at": None, "grid_power": -2100.0}
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", snapshot))
        service._warning_throttled.reset_mock()
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", dict(snapshot, pv_captured_at=100.0, pv_power=None)))

    def test_validate_snapshot_dict_allows_source_absence_only_when_value_and_timestamp_are_both_none(self):
        service = SimpleNamespace(auto_input_helper_restart_seconds=5.0, _warning_throttled=MagicMock())
        controller = AutoInputSupervisor(service)
        snapshot = {"snapshot_version": AutoInputSupervisor.SNAPSHOT_SCHEMA_VERSION, "captured_at": 100.0, "heartbeat_at": 100.0, "pv_captured_at": None, "pv_power": None, "battery_captured_at": 100.0, "battery_soc": 57.0, "grid_captured_at": 100.0, "grid_power": -2100.0}
        normalized = controller._validate_snapshot_dict("/tmp/auto.json", snapshot)
        self.assertIsNotNone(normalized)
        self.assertIsNone(normalized["pv_captured_at"])
        self.assertIsNone(normalized["pv_power"])
        service._warning_throttled.assert_not_called()

    def test_validate_snapshot_dict_normalizes_structured_energy_fields_and_rejects_invalid_payloads(self):
        service = SimpleNamespace(auto_input_helper_restart_seconds=5.0, _warning_throttled=MagicMock())
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
            "battery_source_count": 2,
            "battery_online_source_count": 2,
            "battery_valid_soc_source_count": 1,
            "battery_sources": [{"source_id": "victron"}],
            "battery_learning_profiles": {"victron": {"sample_count": 1}},
            "battery_combined_soc": 58.0,
            "battery_headroom_charge_w": 300.0,
            "expected_near_term_export_w": 120.0,
        }

        normalized = controller._validate_snapshot_dict("/tmp/auto.json", base_snapshot)
        self.assertEqual(normalized["battery_sources"], [{"source_id": "victron"}])
        self.assertEqual(normalized["battery_learning_profiles"], {"victron": {"sample_count": 1}})

        service._warning_throttled.reset_mock()
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", dict(base_snapshot, battery_source_count=True)))
        service._warning_throttled.reset_mock()
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", dict(base_snapshot, battery_sources="bad")))
        service._warning_throttled.reset_mock()
        self.assertIsNone(
            controller._validate_snapshot_dict("/tmp/auto.json", dict(base_snapshot, battery_learning_profiles="bad"))
        )

    def test_validate_snapshot_dict_rejects_invalid_combined_soc_optional_numeric_and_count_types(self):
        service = SimpleNamespace(auto_input_helper_restart_seconds=5.0, _warning_throttled=MagicMock())
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
            "battery_combined_soc": 58.0,
            "battery_headroom_charge_w": 300.0,
            "battery_source_count": 2,
        }

        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", dict(base_snapshot, battery_combined_soc=150.0)))
        service._warning_throttled.reset_mock()
        self.assertIsNone(
            controller._validate_snapshot_dict("/tmp/auto.json", dict(base_snapshot, battery_headroom_charge_w="bad"))
        )
        service._warning_throttled.reset_mock()
        self.assertIsNone(controller._validate_snapshot_dict("/tmp/auto.json", dict(base_snapshot, battery_source_count="bad")))

    def test_refresh_snapshot_rejects_future_snapshot_and_source_timestamps(self):
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            auto_input_snapshot_path="/tmp/auto-helper.json",
            auto_input_helper_stale_seconds=15.0,
            auto_input_helper_restart_seconds=5.0,
            _time_now=MagicMock(return_value=130.0),
            _stat_path=MagicMock(return_value=SimpleNamespace(st_mtime_ns=7, st_mtime=0.0)),
            _load_json_file=MagicMock(return_value={"snapshot_version": AutoInputSupervisor.SNAPSHOT_SCHEMA_VERSION, "captured_at": 131.5, "heartbeat_at": 131.5, "pv_captured_at": 131.5, "pv_power": 2300.0, "battery_captured_at": 131.5, "battery_soc": 57.0, "grid_captured_at": 131.5, "grid_power": -2100.0}),
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
        service._load_json_file = MagicMock(return_value={"snapshot_version": AutoInputSupervisor.SNAPSHOT_SCHEMA_VERSION, "captured_at": 130.0, "heartbeat_at": 130.0, "pv_captured_at": 132.0, "pv_power": 2300.0, "battery_captured_at": 130.0, "battery_soc": 57.0, "grid_captured_at": 130.0, "grid_power": -2100.0})
        controller.refresh_snapshot()
        service._warning_throttled.assert_called_once()
        service._update_worker_snapshot.assert_not_called()
