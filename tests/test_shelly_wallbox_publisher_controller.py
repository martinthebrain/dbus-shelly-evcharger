# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from shelly_wallbox.publish.dbus import DbusPublishController


class TestDbusPublishController(unittest.TestCase):
    @staticmethod
    def _age_seconds(_timestamp: Any, _now: float) -> float:
        return 0.0

    @staticmethod
    def _real_age_seconds(timestamp: Any, now: float) -> float:
        if timestamp is None:
            return -1.0
        return float(now) - float(timestamp)

    @staticmethod
    def _never_stale(_now: float) -> bool:
        return False

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
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1", "P1_P2"),
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
            requested_phase_selection="P1_P2",
            active_phase_selection="P1",
            supported_phase_selections=("P1", "P1_P2"),
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
        self.assertEqual(values["/PhaseSelection"], "P1_P2")
        self.assertEqual(values["/PhaseSelectionActive"], "P1")
        self.assertEqual(values["/SupportedPhaseSelections"], "P1,P1_P2")

    def test_config_values_can_disable_learned_current_display(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=16.0,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1",),
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

    def test_config_values_keep_actual_set_current_when_native_charger_backend_is_present(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=11.0,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1",),
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
            _charger_backend=SimpleNamespace(set_current=MagicMock()),
        )
        controller = DbusPublishController(service, self._age_seconds)

        values = controller._config_values(1, now=100.0)

        self.assertEqual(values["/SetCurrent"], 11.0)

    def test_config_values_degrade_supported_phase_selections_while_lockout_is_active(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=16.0,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1", "P1_P2", "P1_P2_P3"),
            min_current=6.0,
            max_current=16.0,
            _phase_switch_lockout_selection="P1_P2_P3",
            _phase_switch_lockout_until=140.0,
        )
        controller = DbusPublishController(service, self._age_seconds)

        values = controller._config_values(1, now=100.0)

        self.assertEqual(values["/SupportedPhaseSelections"], "P1,P1_P2")

    def test_config_values_prefer_fresh_native_charger_readback_for_gui_state(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=16.0,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1",),
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
            auto_shelly_soft_fail_seconds=10.0,
            _charger_backend=SimpleNamespace(set_current=MagicMock()),
            _last_charger_state_enabled=False,
            _last_charger_state_current_amps=12.5,
            _last_charger_state_status="paused",
            _last_charger_state_fault="vehicle-sleeping",
            _last_charger_state_at=99.5,
        )
        controller = DbusPublishController(service, self._age_seconds)

        values = controller._config_values(1, now=100.0)

        self.assertEqual(values["/Enable"], 0)
        self.assertEqual(values["/StartStop"], 0)
        self.assertEqual(values["/SetCurrent"], 12.5)

    def test_config_values_convert_stable_three_phase_line_voltage_to_display_current(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_autostart=1,
            virtual_enable=1,
            virtual_set_current=16.0,
            requested_phase_selection="P1_P2_P3",
            active_phase_selection="P1_P2_P3",
            supported_phase_selections=("P1", "P1_P2_P3"),
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

    def test_diagnostic_values_include_backend_and_charger_visibility(self) -> None:
        service = SimpleNamespace(
            _error_state={
                "dbus": 1,
                "shelly": 0,
                "charger": 2,
                "pv": 0,
                "battery": 0,
                "grid": 1,
                "cache_hits": 3,
            },
            last_status=2,
            _last_health_reason="running",
            _last_health_code=5,
            _last_auto_state="charging",
            _last_auto_state_code=2,
            _last_status_source="charger-fault",
            backend_mode="split",
            meter_backend_type="template_meter",
            switch_backend_type="template_switch",
            charger_backend_type="smartevse_charger",
            _charger_backend=SimpleNamespace(set_current=MagicMock()),
            _charger_target_current_amps=13.0,
            _charger_target_current_applied_at=96.0,
            _last_charger_state_status="charging",
            _last_charger_state_fault="",
            _last_charger_fault_active=1,
            _last_charger_state_at=97.0,
            _last_charger_transport_reason="offline",
            _last_charger_transport_source="read",
            _last_charger_transport_detail="Modbus slave 1 on /dev/ttyS7 did not respond",
            _last_charger_transport_at=98.0,
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=105.0,
            _last_confirmed_pm_status={"_phase_selection": "P1"},
            _last_switch_feedback_closed=False,
            _last_switch_interlock_ok=True,
            _last_switch_feedback_at=96.0,
            _contactor_fault_counts={},
            _contactor_lockout_reason="",
            _contactor_lockout_source="",
            _contactor_lockout_at=None,
            _contactor_fault_active_reason=None,
            _phase_switch_mismatch_active=True,
            supported_phase_selections=("P1", "P1_P2_P3"),
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_reason="mismatch-threshold",
            _phase_switch_lockout_at=91.0,
            _phase_switch_lockout_until=150.0,
            _last_auto_metrics={
                "phase_current": "P1",
                "phase_target": "P1_P2",
                "phase_reason": "phase-upshift-pending",
                "phase_threshold_watts": 3010.0,
                "phase_candidate": "P1_P2",
            },
            _auto_phase_target_since=92.0,
            _is_update_stale=self._never_stale,
            _recovery_attempts=4,
            _last_confirmed_pm_status_at=95.0,
            _last_pm_status_at=95.0,
            _last_pm_status_confirmed=True,
            _last_pv_at=98.0,
            _last_battery_soc_at=97.0,
            _last_grid_at=94.0,
            _last_dbus_ok_at=99.0,
            _last_successful_update_at=93.0,
            started_at=90.0,
        )
        controller = DbusPublishController(service, self._real_age_seconds)

        counter_values = controller._diagnostic_counter_values(100.0)
        age_values = controller._diagnostic_age_values(100.0)

        self.assertEqual(counter_values["/Auto/BackendMode"], "split")
        self.assertEqual(counter_values["/Auto/MeterBackend"], "template_meter")
        self.assertEqual(counter_values["/Auto/SwitchBackend"], "template_switch")
        self.assertEqual(counter_values["/Auto/ChargerBackend"], "smartevse_charger")
        self.assertEqual(counter_values["/Auto/RecoveryActive"], 0)
        self.assertEqual(counter_values["/Auto/StatusSource"], "charger-fault")
        self.assertEqual(counter_values["/Auto/FaultActive"], 0)
        self.assertEqual(counter_values["/Auto/FaultReason"], "")
        self.assertEqual(counter_values["/Auto/ChargerStatus"], "charging")
        self.assertEqual(counter_values["/Auto/ChargerFault"], "")
        self.assertEqual(counter_values["/Auto/ChargerFaultActive"], 1)
        self.assertEqual(counter_values["/Auto/ChargerTransportActive"], 1)
        self.assertEqual(counter_values["/Auto/ChargerTransportReason"], "offline")
        self.assertEqual(counter_values["/Auto/ChargerTransportSource"], "read")
        self.assertEqual(
            counter_values["/Auto/ChargerTransportDetail"],
            "Modbus slave 1 on /dev/ttyS7 did not respond",
        )
        self.assertEqual(counter_values["/Auto/ChargerRetryActive"], 1)
        self.assertEqual(counter_values["/Auto/ChargerRetryReason"], "offline")
        self.assertEqual(counter_values["/Auto/ChargerRetrySource"], "read")
        self.assertEqual(counter_values["/Auto/ChargerWriteErrors"], 2)
        self.assertEqual(counter_values["/Auto/ErrorCount"], 4)
        self.assertEqual(counter_values["/Auto/ChargerCurrentTarget"], 13.0)
        self.assertEqual(counter_values["/Auto/PhaseCurrent"], "P1")
        self.assertEqual(counter_values["/Auto/PhaseObserved"], "P1")
        self.assertEqual(counter_values["/Auto/PhaseTarget"], "P1_P2")
        self.assertEqual(counter_values["/Auto/PhaseReason"], "phase-upshift-pending")
        self.assertEqual(counter_values["/Auto/PhaseMismatchActive"], 1)
        self.assertEqual(counter_values["/Auto/PhaseLockoutActive"], 1)
        self.assertEqual(counter_values["/Auto/PhaseLockoutTarget"], "P1_P2")
        self.assertEqual(counter_values["/Auto/PhaseLockoutReason"], "mismatch-threshold")
        self.assertEqual(counter_values["/Auto/PhaseSupportedConfigured"], "P1,P1_P2_P3")
        self.assertEqual(counter_values["/Auto/PhaseSupportedEffective"], "P1")
        self.assertEqual(counter_values["/Auto/PhaseDegradedActive"], 1)
        self.assertEqual(counter_values["/Auto/SwitchFeedbackClosed"], 0)
        self.assertEqual(counter_values["/Auto/SwitchInterlockOk"], 1)
        self.assertEqual(counter_values["/Auto/SwitchFeedbackMismatch"], 0)
        self.assertEqual(counter_values["/Auto/ContactorSuspectedOpen"], 0)
        self.assertEqual(counter_values["/Auto/ContactorSuspectedWelded"], 0)
        self.assertEqual(counter_values["/Auto/ContactorFaultCount"], 0)
        self.assertEqual(counter_values["/Auto/ContactorLockoutActive"], 0)
        self.assertEqual(counter_values["/Auto/ContactorLockoutReason"], "")
        self.assertEqual(counter_values["/Auto/ContactorLockoutSource"], "")
        self.assertEqual(counter_values["/Auto/PhaseThresholdWatts"], 3010.0)
        self.assertEqual(counter_values["/Auto/PhaseCandidate"], "P1_P2")
        self.assertEqual(age_values["/Auto/ChargerCurrentTargetAge"], 4.0)
        self.assertEqual(age_values["/Auto/PhaseCandidateAge"], 8.0)
        self.assertEqual(age_values["/Auto/PhaseLockoutAge"], 9.0)
        self.assertEqual(age_values["/Auto/ContactorLockoutAge"], -1.0)
        self.assertEqual(age_values["/Auto/LastSwitchFeedbackAge"], 4.0)
        self.assertEqual(age_values["/Auto/LastChargerReadAge"], 3.0)
        self.assertEqual(age_values["/Auto/LastChargerTransportAge"], 2.0)
        self.assertEqual(age_values["/Auto/ChargerRetryRemaining"], 5.0)

        service._last_health_reason = "contactor-suspected-welded"
        welded_counter_values = controller._diagnostic_counter_values(100.0)
        self.assertEqual(welded_counter_values["/Auto/ContactorSuspectedOpen"], 0)
        self.assertEqual(welded_counter_values["/Auto/ContactorSuspectedWelded"], 1)

        service._last_health_reason = "contactor-suspected-open"
        open_counter_values = controller._diagnostic_counter_values(100.0)
        self.assertEqual(open_counter_values["/Auto/ContactorSuspectedOpen"], 1)
        self.assertEqual(open_counter_values["/Auto/ContactorSuspectedWelded"], 0)

        service._last_health_reason = "contactor-lockout-open"
        service._last_auto_state = "recovery"
        service._last_auto_state_code = 5
        service._contactor_fault_counts = {"contactor-suspected-open": 3}
        service._contactor_lockout_reason = "contactor-suspected-open"
        service._contactor_lockout_source = "count-threshold"
        service._contactor_lockout_at = 94.0
        lockout_counter_values = controller._diagnostic_counter_values(100.0)
        lockout_age_values = controller._diagnostic_age_values(100.0)
        self.assertEqual(lockout_counter_values["/Auto/RecoveryActive"], 1)
        self.assertEqual(lockout_counter_values["/Auto/FaultActive"], 1)
        self.assertEqual(lockout_counter_values["/Auto/FaultReason"], "contactor-lockout-open")
        self.assertEqual(lockout_counter_values["/Auto/ContactorFaultCount"], 3)
        self.assertEqual(lockout_counter_values["/Auto/ContactorLockoutActive"], 1)
        self.assertEqual(lockout_counter_values["/Auto/ContactorLockoutReason"], "contactor-suspected-open")
        self.assertEqual(lockout_counter_values["/Auto/ContactorLockoutSource"], "count-threshold")
        self.assertEqual(lockout_age_values["/Auto/ContactorLockoutAge"], 6.0)
