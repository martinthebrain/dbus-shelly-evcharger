# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_publisher_support import (
    DbusPublishController,
    DbusPublishControllerTestCase,
    MagicMock,
    SimpleNamespace,
)


class TestDbusPublishControllerDiagnostics(DbusPublishControllerTestCase):
    def test_diagnostic_values_include_backend_and_charger_visibility(self) -> None:
        current_time = 1776718800.0
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
            virtual_mode=2,
            _last_health_reason="running",
            _last_health_code=5,
            _last_auto_state="charging",
            _last_auto_state_code=2,
            _last_status_source="charger-fault",
            auto_month_windows={4: ((7, 30), (19, 30))},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            backend_mode="split",
            meter_backend_type="template_meter",
            switch_backend_type="template_switch",
            charger_backend_type="smartevse_charger",
            _charger_backend=SimpleNamespace(set_current=MagicMock()),
            _charger_target_current_amps=13.0,
            _charger_target_current_applied_at=current_time - 4.0,
            _last_charger_state_status="charging",
            _last_charger_state_fault="",
            _last_charger_fault_active=1,
            _last_charger_state_at=current_time - 3.0,
            _last_charger_estimate_source="current-voltage-phase",
            _last_charger_estimate_at=current_time - 1.0,
            _runtime_overrides_active=True,
            runtime_overrides_path="/run/wallbox-overrides.ini",
            _last_charger_transport_reason="offline",
            _last_charger_transport_source="read",
            _last_charger_transport_detail="Modbus slave 1 on /dev/ttyS7 did not respond",
            _last_charger_transport_at=current_time - 2.0,
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=current_time + 5.0,
            _last_confirmed_pm_status={"_phase_selection": "P1"},
            _last_switch_feedback_closed=False,
            _last_switch_interlock_ok=True,
            _last_switch_feedback_at=current_time - 4.0,
            _contactor_fault_counts={},
            _contactor_lockout_reason="",
            _contactor_lockout_source="",
            _contactor_lockout_at=None,
            _contactor_fault_active_reason=None,
            _phase_switch_mismatch_active=True,
            supported_phase_selections=("P1", "P1_P2_P3"),
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_reason="mismatch-threshold",
            _phase_switch_lockout_at=current_time - 9.0,
            _phase_switch_lockout_until=current_time + 50.0,
            _last_auto_metrics={
                "phase_current": "P1",
                "phase_target": "P1_P2",
                "phase_reason": "phase-upshift-pending",
                "phase_threshold_watts": 3010.0,
                "phase_candidate": "P1_P2",
            },
            _auto_phase_target_since=current_time - 8.0,
            _is_update_stale=self._never_stale,
            _recovery_attempts=4,
            _last_confirmed_pm_status_at=current_time - 5.0,
            _last_pm_status_at=current_time - 5.0,
            _last_pm_status_confirmed=True,
            _last_pv_at=current_time - 2.0,
            _last_battery_soc_at=current_time - 3.0,
            _last_grid_at=current_time - 6.0,
            _last_dbus_ok_at=current_time - 1.0,
            _last_successful_update_at=current_time - 7.0,
            _software_update_available=True,
            _software_update_state="available",
            _software_update_detail="manifest",
            _software_update_current_version="1.2.3",
            _software_update_available_version="1.2.4",
            _software_update_no_update_active=True,
            _software_update_last_check_at=current_time - 60.0,
            _software_update_last_run_at=current_time - 3600.0,
            started_at=current_time - 10.0,
        )
        controller = DbusPublishController(service, self._real_age_seconds)

        counter_values = controller._diagnostic_counter_values(current_time)
        age_values = controller._diagnostic_age_values(current_time)

        self.assertEqual(counter_values["/Auto/ScheduledState"], "night-boost")
        self.assertEqual(counter_values["/Auto/ScheduledStateCode"], 4)
        self.assertEqual(counter_values["/Auto/ScheduledReason"], "night-boost-window")
        self.assertEqual(counter_values["/Auto/ScheduledReasonCode"], 4)
        self.assertEqual(counter_values["/Auto/ScheduledNightBoostActive"], 1)
        self.assertEqual(counter_values["/Auto/ScheduledTargetDayEnabled"], 1)
        self.assertEqual(counter_values["/Auto/ScheduledTargetDay"], "Tue")
        self.assertEqual(counter_values["/Auto/ScheduledTargetDate"], "2026-04-21")
        self.assertEqual(counter_values["/Auto/ScheduledFallbackStart"], "2026-04-20 20:30")
        self.assertEqual(counter_values["/Auto/ScheduledBoostUntil"], "2026-04-21 06:30")
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
        self.assertEqual(counter_values["/Auto/ChargerEstimateActive"], 1)
        self.assertEqual(counter_values["/Auto/ChargerEstimateSource"], "current-voltage-phase")
        self.assertEqual(counter_values["/Auto/RuntimeOverridesActive"], 1)
        self.assertEqual(counter_values["/Auto/RuntimeOverridesPath"], "/run/wallbox-overrides.ini")
        self.assertEqual(counter_values["/Auto/SoftwareUpdateAvailable"], 1)
        self.assertEqual(counter_values["/Auto/SoftwareUpdateState"], "available-blocked")
        self.assertEqual(counter_values["/Auto/SoftwareUpdateStateCode"], 4)
        self.assertEqual(counter_values["/Auto/SoftwareUpdateDetail"], "manifest")
        self.assertEqual(counter_values["/Auto/SoftwareUpdateCurrentVersion"], "1.2.3")
        self.assertEqual(counter_values["/Auto/SoftwareUpdateAvailableVersion"], "1.2.4")
        self.assertEqual(counter_values["/Auto/SoftwareUpdateNoUpdateActive"], 1)
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
        self.assertEqual(age_values["/Auto/LastChargerEstimateAge"], 1.0)
        self.assertEqual(age_values["/Auto/LastChargerTransportAge"], 2.0)
        self.assertEqual(age_values["/Auto/ChargerRetryRemaining"], 5.0)
        self.assertEqual(age_values["/Auto/SoftwareUpdateLastCheckAge"], 60.0)
        self.assertEqual(age_values["/Auto/SoftwareUpdateLastRunAge"], 3600.0)

        service._last_health_reason = "contactor-suspected-welded"
        welded_counter_values = controller._diagnostic_counter_values(current_time)
        self.assertEqual(welded_counter_values["/Auto/ContactorSuspectedOpen"], 0)
        self.assertEqual(welded_counter_values["/Auto/ContactorSuspectedWelded"], 1)

        service._last_health_reason = "contactor-suspected-open"
        open_counter_values = controller._diagnostic_counter_values(current_time)
        self.assertEqual(open_counter_values["/Auto/ContactorSuspectedOpen"], 1)
        self.assertEqual(open_counter_values["/Auto/ContactorSuspectedWelded"], 0)

        service._last_health_reason = "contactor-lockout-open"
        service._last_auto_state = "recovery"
        service._last_auto_state_code = 5
        service._contactor_fault_counts = {"contactor-suspected-open": 3}
        service._contactor_lockout_reason = "contactor-suspected-open"
        service._contactor_lockout_source = "count-threshold"
        service._contactor_lockout_at = current_time - 6.0
        lockout_counter_values = controller._diagnostic_counter_values(current_time)
        lockout_age_values = controller._diagnostic_age_values(current_time)
        self.assertEqual(lockout_counter_values["/Auto/RecoveryActive"], 1)
        self.assertEqual(lockout_counter_values["/Auto/FaultActive"], 1)
        self.assertEqual(lockout_counter_values["/Auto/FaultReason"], "contactor-lockout-open")
        self.assertEqual(lockout_counter_values["/Auto/ContactorFaultCount"], 3)
        self.assertEqual(lockout_counter_values["/Auto/ContactorLockoutActive"], 1)
        self.assertEqual(lockout_counter_values["/Auto/ContactorLockoutReason"], "contactor-suspected-open")
        self.assertEqual(lockout_counter_values["/Auto/ContactorLockoutSource"], "count-threshold")
        self.assertEqual(lockout_age_values["/Auto/ContactorLockoutAge"], 6.0)

    def test_software_update_age_values_are_negative_one_before_any_check_or_run(self) -> None:
        service = SimpleNamespace(
            _dbusservice={},
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _error_state={"dbus": 0, "shelly": 0, "charger": 0, "pv": 0, "battery": 0, "grid": 0, "cache_hits": 0},
            last_status=0,
            virtual_mode=1,
            _last_health_reason="init",
            _last_health_code=0,
            _last_auto_state="idle",
            _last_auto_state_code=0,
            _last_status_source="unknown",
            auto_month_windows={},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            _last_confirmed_pm_status_at=None,
            _last_pm_status_at=None,
            _last_pm_status_confirmed=False,
            _last_pv_at=None,
            _last_battery_soc_at=None,
            _last_grid_at=None,
            _last_dbus_ok_at=None,
            _software_update_last_check_at=None,
            _software_update_last_run_at=None,
            _is_update_stale=self._never_stale,
            _last_successful_update_at=90.0,
            started_at=90.0,
        )
        controller = DbusPublishController(service, self._real_age_seconds)

        age_values = controller._diagnostic_age_values(100.0)

        self.assertEqual(age_values["/Auto/SoftwareUpdateLastCheckAge"], -1.0)
        self.assertEqual(age_values["/Auto/SoftwareUpdateLastRunAge"], -1.0)

    def test_diagnostic_values_keep_fault_and_recovery_visible_while_scheduled_and_retry_are_also_active(self) -> None:
        current_time = 1776718800.0
        service = SimpleNamespace(
            _error_state={"dbus": 0, "shelly": 0, "charger": 1, "pv": 0, "battery": 0, "grid": 0, "cache_hits": 0},
            last_status=0,
            virtual_mode=2,
            _last_health_reason="contactor-lockout-open",
            _last_health_code=32,
            _last_auto_state="recovery",
            _last_auto_state_code=5,
            _last_status_source="contactor-lockout-open",
            auto_month_windows={4: ((7, 30), (19, 30))},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            backend_mode="split",
            meter_backend_type="template_meter",
            switch_backend_type="switch_group",
            charger_backend_type="simpleevse_charger",
            _charger_backend=SimpleNamespace(set_current=MagicMock()),
            _last_charger_state_status="charging",
            _last_charger_state_fault="overcurrent error",
            _last_charger_fault_active=1,
            _last_charger_state_at=current_time - 1.0,
            _last_charger_transport_reason="offline",
            _last_charger_transport_source="read",
            _last_charger_transport_detail="timeout",
            _last_charger_transport_at=current_time - 1.0,
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=current_time + 12.0,
            _last_confirmed_pm_status={"_phase_selection": "P1", "output": False},
            _phase_switch_mismatch_active=False,
            supported_phase_selections=("P1", "P1_P2"),
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_reason="mismatch-threshold",
            _phase_switch_lockout_at=current_time - 5.0,
            _phase_switch_lockout_until=current_time + 50.0,
            _last_switch_feedback_closed=False,
            _last_switch_interlock_ok=True,
            _last_switch_feedback_at=current_time - 1.0,
            _contactor_fault_counts={"contactor-suspected-open": 3},
            _contactor_lockout_reason="contactor-suspected-open",
            _contactor_lockout_source="count-threshold",
            _contactor_lockout_at=current_time - 4.0,
            _is_update_stale=self._never_stale,
            _recovery_attempts=1,
            _last_confirmed_pm_status_at=current_time - 2.0,
            _last_pm_status_at=current_time - 2.0,
            _last_pm_status_confirmed=True,
            _last_pv_at=current_time - 2.0,
            _last_battery_soc_at=current_time - 2.0,
            _last_grid_at=current_time - 2.0,
            _last_dbus_ok_at=current_time - 1.0,
            _last_successful_update_at=current_time - 3.0,
            started_at=current_time - 10.0,
        )
        controller = DbusPublishController(service, self._real_age_seconds)

        counter_values = controller._diagnostic_counter_values(current_time)

        self.assertEqual(counter_values["/Status"], 0)
        self.assertEqual(counter_values["/Auto/RecoveryActive"], 1)
        self.assertEqual(counter_values["/Auto/FaultActive"], 1)
        self.assertEqual(counter_values["/Auto/FaultReason"], "contactor-lockout-open")
        self.assertEqual(counter_values["/Auto/StatusSource"], "contactor-lockout-open")
        self.assertEqual(counter_values["/Auto/ScheduledState"], "night-boost")
        self.assertEqual(counter_values["/Auto/ScheduledReason"], "night-boost-window")
        self.assertEqual(counter_values["/Auto/ChargerTransportActive"], 1)
        self.assertEqual(counter_values["/Auto/ChargerRetryActive"], 1)
        self.assertEqual(counter_values["/Auto/ContactorLockoutActive"], 1)
        self.assertEqual(counter_values["/Auto/ContactorLockoutReason"], "contactor-suspected-open")

    def test_diagnostic_values_keep_retry_visible_after_transport_detail_has_gone_stale(self) -> None:
        current_time = 200.0
        service = SimpleNamespace(
            _error_state={"dbus": 0, "shelly": 0, "charger": 1, "pv": 0, "battery": 0, "grid": 0, "cache_hits": 0},
            last_status=6,
            virtual_mode=1,
            _last_health_reason="charger-transport-offline",
            _last_health_code=37,
            _last_auto_state="blocked",
            _last_auto_state_code=4,
            _last_status_source="charger-status-ready",
            auto_month_windows={},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            backend_mode="split",
            meter_backend_type="template_meter",
            switch_backend_type="switch_group",
            charger_backend_type="smartevse_charger",
            _last_charger_state_status="ready",
            _last_charger_state_fault="",
            _last_charger_fault_active=0,
            _last_charger_state_at=199.0,
            auto_shelly_soft_fail_seconds=10.0,
            _last_charger_transport_reason="offline",
            _last_charger_transport_source="read",
            _last_charger_transport_detail="timeout",
            _last_charger_transport_at=150.0,
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=210.0,
            _last_confirmed_pm_status={"_phase_selection": "P1", "output": False},
            _phase_switch_mismatch_active=False,
            supported_phase_selections=("P1",),
            _phase_switch_lockout_selection=None,
            _phase_switch_lockout_reason="",
            _phase_switch_lockout_at=None,
            _phase_switch_lockout_until=None,
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_switch_feedback_at=None,
            _contactor_fault_counts={},
            _contactor_lockout_reason="",
            _contactor_lockout_source="",
            _contactor_lockout_at=None,
            _is_update_stale=self._never_stale,
            _recovery_attempts=0,
            _last_confirmed_pm_status_at=199.0,
            _last_pm_status_at=199.0,
            _last_pm_status_confirmed=True,
            _last_pv_at=199.0,
            _last_battery_soc_at=199.0,
            _last_grid_at=199.0,
            _last_dbus_ok_at=199.0,
            _last_successful_update_at=199.0,
            started_at=100.0,
        )
        controller = DbusPublishController(service, self._real_age_seconds)

        counter_values = controller._diagnostic_counter_values(current_time)
        age_values = controller._diagnostic_age_values(current_time)

        self.assertEqual(counter_values["/Status"], 6)
        self.assertEqual(counter_values["/Auto/FaultActive"], 0)
        self.assertEqual(counter_values["/Auto/FaultReason"], "")
        self.assertEqual(counter_values["/Auto/ChargerTransportActive"], 0)
        self.assertEqual(counter_values["/Auto/ChargerTransportReason"], "")
        self.assertEqual(counter_values["/Auto/ChargerRetryActive"], 1)
        self.assertEqual(counter_values["/Auto/ChargerRetryReason"], "offline")
        self.assertEqual(counter_values["/Auto/StatusSource"], "charger-status-ready")
        self.assertEqual(age_values["/Auto/LastChargerTransportAge"], -1.0)
        self.assertEqual(age_values["/Auto/ChargerRetryRemaining"], 10.0)

    def test_diagnostic_values_prefer_confirmed_switch_group_phase_over_native_charger_phase(self) -> None:
        current_time = 200.0
        service = SimpleNamespace(
            _error_state={"dbus": 0, "shelly": 0, "charger": 0, "pv": 0, "battery": 0, "grid": 0, "cache_hits": 0},
            last_status=2,
            virtual_mode=1,
            _last_health_reason="",
            _last_health_code=0,
            _last_auto_state="running",
            _last_auto_state_code=2,
            _last_status_source="charger-status-charging",
            auto_month_windows={},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            backend_mode="split",
            meter_backend_type="template_meter",
            switch_backend_type="switch_group",
            charger_backend_type="smartevse_charger",
            _last_charger_state_status="charging",
            _last_charger_state_fault="",
            _last_charger_fault_active=0,
            _last_charger_state_phase_selection="P1",
            _last_charger_state_at=199.0,
            _last_confirmed_pm_status={"_phase_selection": "P1_P2", "output": True},
            _last_confirmed_pm_status_at=199.0,
            _last_pm_status_at=199.0,
            _last_pm_status_confirmed=True,
            _phase_switch_mismatch_active=True,
            supported_phase_selections=("P1", "P1_P2"),
            _phase_switch_lockout_selection=None,
            _phase_switch_lockout_reason="",
            _phase_switch_lockout_at=None,
            _phase_switch_lockout_until=None,
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_switch_feedback_at=None,
            _contactor_fault_counts={},
            _contactor_lockout_reason="",
            _contactor_lockout_source="",
            _contactor_lockout_at=None,
            _is_update_stale=self._never_stale,
            _recovery_attempts=0,
            _last_pv_at=199.0,
            _last_battery_soc_at=199.0,
            _last_grid_at=199.0,
            _last_dbus_ok_at=199.0,
            _last_successful_update_at=199.0,
            started_at=100.0,
        )
        controller = DbusPublishController(service, self._real_age_seconds)

        counter_values = controller._diagnostic_counter_values(current_time)

        self.assertEqual(counter_values["/Status"], 2)
        self.assertEqual(counter_values["/Auto/StatusSource"], "charger-status-charging")
        self.assertEqual(counter_values["/Auto/PhaseObserved"], "P1_P2")
        self.assertEqual(counter_values["/Auto/PhaseMismatchActive"], 1)
