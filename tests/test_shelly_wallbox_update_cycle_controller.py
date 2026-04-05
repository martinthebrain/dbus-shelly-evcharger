# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from dbus_shelly_wallbox_update_cycle import UpdateCycleController


def _phase_values(total_power, voltage, _phase, _voltage_mode):
    current = (total_power / voltage) if voltage else 0.0
    return {
        "L1": {"power": total_power, "voltage": voltage, "current": current},
        "L2": {"power": 0.0, "voltage": voltage, "current": 0.0},
        "L3": {"power": 0.0, "voltage": voltage, "current": 0.0},
    }


class TestUpdateCycleController(unittest.TestCase):
    def test_apply_startup_manual_target_returns_unchanged_when_relay_already_matches(self):
        service = SimpleNamespace(
            _startup_manual_target=True,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        pm_status = {"output": True, "apower": 1200.0, "current": 5.2}

        updated = controller.apply_startup_manual_target(pm_status, 123.0)

        self.assertIs(updated, pm_status)
        service._queue_relay_command.assert_not_called()
        self.assertIsNone(service._startup_manual_target)

    def test_apply_startup_manual_target_queues_requested_state(self):
        service = SimpleNamespace(
            _startup_manual_target=False,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        updated = controller.apply_startup_manual_target(
            {"output": True, "apower": 1200.0, "current": 5.2},
            123.0,
        )

        service._queue_relay_command.assert_called_once_with(False, 123.0)
        self.assertIsNone(service._startup_manual_target)
        self.assertFalse(updated["output"])
        self.assertEqual(updated["apower"], 0.0)
        self.assertEqual(updated["current"], 0.0)

    def test_apply_startup_manual_target_marks_failure_when_queueing_raises(self):
        service = SimpleNamespace(
            _startup_manual_target=False,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(side_effect=RuntimeError("boom")),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        pm_status = {"output": True, "apower": 1200.0, "current": 5.2}

        updated = controller.apply_startup_manual_target(pm_status, 123.0)

        self.assertIs(updated, pm_status)
        service._mark_failure.assert_called_once_with("shelly")
        service._warning_throttled.assert_called_once()

    def test_resolve_auto_inputs_uses_recent_cache_and_counts_hit(self):
        service = SimpleNamespace(
            auto_input_cache_seconds=120.0,
            _auto_cached_inputs_used=False,
            _error_state={"cache_hits": 0},
            _last_pv_value=2300.0,
            _last_pv_at=80.0,
            _last_grid_value=-1700.0,
            _last_grid_at=85.0,
            _last_battery_soc_value=61.0,
            _last_battery_soc_at=90.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        pv_power, battery_soc, grid_power = controller.resolve_auto_inputs(
            {
                "captured_at": 100.0,
                "pv_power": None,
                "grid_power": None,
                "battery_soc": None,
            },
            100.0,
            True,
        )

        self.assertEqual(pv_power, 2300.0)
        self.assertEqual(battery_soc, 61.0)
        self.assertEqual(grid_power, -1700.0)
        self.assertTrue(service._auto_cached_inputs_used)
        self.assertEqual(service._error_state["cache_hits"], 1)

    def test_update_offline_path_publishes_disconnected_state(self):
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=200.0),
            _state_summary=lambda: "state",
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={"pm_status": None}),
            _last_pm_status=None,
            _last_pm_status_at=None,
            auto_shelly_soft_fail_seconds=10.0,
            _last_voltage=230.0,
            virtual_startstop=1,
            phase="L1",
            voltage_mode="phase",
            _set_health=MagicMock(),
            _publish_live_measurements=MagicMock(return_value=True),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _publish_dbus_path=MagicMock(return_value=False),
            _bump_update_index=MagicMock(),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _last_health_reason="init",
            _last_health_code=0,
            charging_started_at=None,
            energy_at_start=0.0,
            last_status=0,
            virtual_mode=0,
            virtual_enable=1,
            _dbusservice={"/Ac/Power": 0.0},
            service_name="com.victronenergy.evcharger.http_60",
            last_update=0.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        result = controller.update()

        self.assertTrue(result)
        service._watchdog_recover.assert_called_once_with(200.0)
        service._publish_live_measurements.assert_called_once()
        service._set_health.assert_called_once_with("shelly-offline", cached=False)
        service._bump_update_index.assert_called_once_with(200.0)
        self.assertEqual(service.last_update, 200.0)

    def test_update_cycle_helpers_cover_cached_pm_status_session_branches_and_logging(self):
        service = SimpleNamespace(
            charging_started_at=None,
            energy_at_start=1.5,
            virtual_mode=1,
            virtual_enable=1,
            phase="3P",
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _last_pm_status={"output": True},
            _last_pm_status_at=95.0,
            auto_shelly_soft_fail_seconds=10.0,
            _last_auto_metrics={"surplus": 2500.0, "grid": -2200.0, "soc": 63.0},
            _last_health_reason="running",
            auto_audit_log=True,
            _queue_relay_command=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            charging_threshold_watts=1500.0,
            idle_status=1,
            _time_now=MagicMock(return_value=123.0),
            _bump_update_index=MagicMock(),
            virtual_startstop=1,
            service_name="com.victronenergy.evcharger.http_60",
            _dbusservice={"/Ac/Power": 321.0},
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        charging_time, session_energy = controller.session_state_from_status(service, 1, 2.0, True, 100.0)
        self.assertEqual((charging_time, session_energy), (0, 0.5))

        self.assertEqual(
            controller.phase_energies_for_total(service, 6.0),
            {"L1": 2.0, "L2": 2.0, "L3": 2.0},
        )

        self.assertEqual(
            controller.resolve_pm_status_for_update(service, {"pm_status": None}, 100.0),
            {"output": True},
        )

        with patch("dbus_shelly_wallbox_update_cycle.logging.info") as info_mock:
            controller.log_auto_relay_change(service, True)
            controller.sign_of_life()

        self.assertEqual(info_mock.call_count, 2)

        relay_on, power, current = controller.apply_relay_decision(
            False,
            True,
            {"output": True, "apower": 1200.0, "current": 5.2},
            1200.0,
            5.2,
            123.0,
            True,
        )
        self.assertEqual((relay_on, power, current), (False, 0.0, 0.0))
        service._publish_local_pm_status.assert_called_once_with(False, 123.0)

    def test_apply_relay_decision_and_update_cover_failure_and_warning_paths(self):
        service = SimpleNamespace(
            _last_auto_metrics={"surplus": None, "grid": None, "soc": None},
            _last_health_reason="init",
            auto_audit_log=False,
            auto_shelly_soft_fail_seconds=10.0,
            _queue_relay_command=MagicMock(side_effect=RuntimeError("boom")),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _state_summary=lambda: "state",
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={"pm_status": {"output": False}}),
            _last_pm_status=None,
            _last_pm_status_at=None,
            _safe_float=lambda value, default=0.0: float(value) if value is not None else default,
            virtual_mode=1,
            phase="L1",
            voltage_mode="line",
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _auto_decide_relay=MagicMock(side_effect=RuntimeError("auto failed")),
            _publish_live_measurements=MagicMock(return_value=False),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _last_health_code=0,
            charging_started_at=None,
            energy_at_start=0.0,
            last_status=0,
            virtual_enable=1,
            _dbusservice={"/Ac/Power": 0.0},
            service_name="com.victronenergy.evcharger.http_60",
            last_update=0.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _last_voltage=230.0,
            virtual_startstop=0,
            charging_threshold_watts=1500.0,
            idle_status=1,
            _last_successful_update_at=None,
            _last_recovery_attempt_at=None,
            _error_state={"cache_hits": 0},
            _last_pv_value=None,
            _last_pv_at=None,
            _last_grid_value=None,
            _last_grid_at=None,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
            auto_input_cache_seconds=0.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current = controller.apply_relay_decision(
            False,
            True,
            {"output": True, "apower": 1200.0, "current": 5.2},
            1200.0,
            5.2,
            100.0,
            False,
        )
        self.assertEqual((relay_on, power, current), (True, 1200.0, 5.2))
        service._mark_failure.assert_called_once_with("shelly")
        service._warning_throttled.assert_called_once()

        with patch("dbus_shelly_wallbox_update_cycle.logging.warning") as warning_mock:
            self.assertTrue(controller.update())
        warning_mock.assert_called_once()
