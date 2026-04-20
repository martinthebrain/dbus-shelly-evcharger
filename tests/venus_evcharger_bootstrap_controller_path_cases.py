# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_bootstrap_controller_support import (
    AutoDecisionPort,
    MagicMock,
    Path,
    ServiceBootstrapController,
    ServiceBootstrapControllerTestCase,
    SimpleNamespace,
    UpdateCyclePort,
    WriteControllerPort,
    _FakeDbusService,
    datetime,
    patch,
    tempfile,
)


class TestServiceBootstrapControllerPaths(ServiceBootstrapControllerTestCase):
    def test_register_paths_uses_main_script_path_and_registers_service(self):
        service = SimpleNamespace(
            _dbusservice=_FakeDbusService(),
            connection_name="Shelly RPC",
            deviceinstance=60,
            product_name="Venus EV Charger Service",
            custom_name="Wallbox",
            firmware_version="1.0",
            hardware_version="Shelly 1PM Gen4",
            serial="ABC123",
            position=1,
            min_current=6.0,
            max_current=16.0,
            virtual_set_current=16.0,
            virtual_autostart=1,
            virtual_mode=0,
            virtual_startstop=1,
            virtual_enable=1,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1",),
            auto_start_surplus_watts=1850.0,
            auto_stop_surplus_watts=1350.0,
            auto_min_soc=40.0,
            auto_resume_soc=50.0,
            auto_start_delay_seconds=10.0,
            auto_stop_delay_seconds=30.0,
            auto_month_windows={4: ((7, 30), (19, 30))},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            auto_scheduled_night_current_amps=13.0,
            auto_dbus_backoff_base_seconds=5.0,
            auto_dbus_backoff_max_seconds=60.0,
            auto_grid_recovery_start_seconds=14.0,
            auto_stop_surplus_delay_seconds=45.0,
            auto_stop_surplus_volatility_low_watts=80.0,
            auto_stop_surplus_volatility_high_watts=240.0,
            auto_reference_charge_power_watts=2100.0,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_min_watts=1400.0,
            auto_learn_charge_power_alpha=0.25,
            auto_learn_charge_power_start_delay_seconds=12.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_phase_switching_enabled=True,
            auto_phase_prefer_lowest_when_idle=False,
            auto_phase_upshift_delay_seconds=120.0,
            auto_phase_downshift_delay_seconds=30.0,
            auto_phase_upshift_headroom_watts=250.0,
            auto_phase_downshift_margin_watts=150.0,
            auto_phase_mismatch_retry_seconds=300.0,
            auto_phase_mismatch_lockout_count=3,
            auto_phase_mismatch_lockout_seconds=1800.0,
            runtime_overrides_path="/run/wallbox-overrides.ini",
            _runtime_overrides_active=True,
            backend_mode="split",
            meter_backend_type="shelly_meter",
            switch_backend_type="template_switch",
            charger_backend_type="template_charger",
            _last_health_reason="init",
            _last_health_code=0,
            _last_auto_state="idle",
            _last_auto_state_code=0,
            _handle_write=MagicMock(),
        )

        controller = self._controller(service)
        controller.register_paths()

        self.assertEqual(service._dbusservice.paths["/Mgmt/ProcessName"]["value"], "/tmp/venus_evcharger_service.py")
        self.assertEqual(service._dbusservice.paths["/Mode"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/PhaseSelection"]["value"], "P1")
        self.assertEqual(service._dbusservice.paths["/Auto/StartSurplusWatts"]["value"], 1850.0)
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledEnabledDays"]["value"], "Mon,Tue,Wed,Thu,Fri")
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledFallbackDelaySeconds"]["value"], 3600.0)
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledLatestEndTime"]["value"], "06:30")
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledNightCurrent"]["value"], 13.0)
        self.assertEqual(service._dbusservice.paths["/Auto/DbusBackoffBaseSeconds"]["value"], 5.0)
        self.assertEqual(service._dbusservice.paths["/Auto/GridRecoveryStartSeconds"]["value"], 14.0)
        self.assertEqual(service._dbusservice.paths["/Auto/StopSurplusVolatilityLowWatts"]["value"], 80.0)
        self.assertEqual(service._dbusservice.paths["/Auto/ReferenceChargePowerWatts"]["value"], 2100.0)
        self.assertEqual(service._dbusservice.paths["/Auto/LearnChargePowerEnabled"]["value"], 1)
        self.assertEqual(service._dbusservice.paths["/Auto/LearnChargePowerWindowSeconds"]["value"], 180.0)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseSwitching"]["value"], 1)
        self.assertEqual(service._dbusservice.paths["/Auto/PhasePreferLowestWhenIdle"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseUpshiftDelaySeconds"]["value"], 120.0)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseMismatchLockoutCount"]["value"], 3)
        self.assertEqual(service._dbusservice.paths["/Auto/State"]["value"], "idle")
        self.assertEqual(service._dbusservice.paths["/Auto/StateCode"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledState"]["value"], "disabled")
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledStateCode"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledReason"]["value"], "disabled")
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledReasonCode"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledNightBoostActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledTargetDayEnabled"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledTargetDay"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledTargetDate"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledFallbackStart"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledBoostUntil"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/RecoveryActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/StatusSource"]["value"], "unknown")
        self.assertEqual(service._dbusservice.paths["/Auto/FaultActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/FaultReason"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/BackendMode"]["value"], "split")
        self.assertEqual(service._dbusservice.paths["/Auto/MeterBackend"]["value"], "shelly_meter")
        self.assertEqual(service._dbusservice.paths["/Auto/SwitchBackend"]["value"], "template_switch")
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerBackend"]["value"], "template_charger")
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerFaultActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerTransportActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerTransportReason"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerTransportSource"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerTransportDetail"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/RuntimeOverridesActive"]["value"], 1)
        self.assertEqual(service._dbusservice.paths["/Auto/RuntimeOverridesPath"]["value"], "/run/wallbox-overrides.ini")
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerRetryActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerRetryReason"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerRetrySource"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerCurrentTarget"]["value"], -1.0)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseCurrent"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseObserved"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseTarget"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseReason"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseMismatchActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseLockoutActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseLockoutTarget"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseLockoutReason"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseSupportedConfigured"]["value"], "P1")
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseSupportedEffective"]["value"], "P1")
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseDegradedActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/SwitchFeedbackClosed"]["value"], -1)
        self.assertEqual(service._dbusservice.paths["/Auto/SwitchInterlockOk"]["value"], -1)
        self.assertEqual(service._dbusservice.paths["/Auto/SwitchFeedbackMismatch"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ContactorSuspectedOpen"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ContactorSuspectedWelded"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ContactorFaultCount"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ContactorLockoutActive"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/ContactorLockoutReason"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/ContactorLockoutSource"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/ContactorLockoutReset"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseLockoutReset"]["value"], 0)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseThresholdWatts"]["value"], -1.0)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseCandidate"]["value"], "")
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseCandidateAge"]["value"], -1)
        self.assertEqual(service._dbusservice.paths["/Auto/PhaseLockoutAge"]["value"], -1)

    def test_register_paths_initializes_scheduled_snapshot_when_mode_is_scheduled(self):
        service = SimpleNamespace(
            _dbusservice=_FakeDbusService(),
            connection_name="Shelly RPC",
            deviceinstance=60,
            product_name="Venus EV Charger Service",
            custom_name="Wallbox",
            firmware_version="1.0",
            hardware_version="Shelly 1PM Gen4",
            serial="ABC123",
            position=1,
            min_current=6.0,
            max_current=16.0,
            virtual_set_current=16.0,
            virtual_autostart=1,
            virtual_mode=2,
            virtual_startstop=1,
            virtual_enable=1,
            requested_phase_selection="P1",
            active_phase_selection="P1",
            supported_phase_selections=("P1",),
            auto_start_surplus_watts=1850.0,
            auto_stop_surplus_watts=1350.0,
            auto_min_soc=40.0,
            auto_resume_soc=50.0,
            auto_start_delay_seconds=10.0,
            auto_stop_delay_seconds=30.0,
            auto_month_windows={4: ((7, 30), (19, 30))},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            auto_scheduled_night_current_amps=13.0,
            auto_dbus_backoff_base_seconds=5.0,
            auto_dbus_backoff_max_seconds=60.0,
            auto_grid_recovery_start_seconds=14.0,
            auto_stop_surplus_delay_seconds=45.0,
            auto_stop_surplus_volatility_low_watts=80.0,
            auto_stop_surplus_volatility_high_watts=240.0,
            auto_reference_charge_power_watts=2100.0,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_min_watts=1400.0,
            auto_learn_charge_power_alpha=0.25,
            auto_learn_charge_power_start_delay_seconds=12.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_phase_switching_enabled=True,
            auto_phase_prefer_lowest_when_idle=False,
            auto_phase_upshift_delay_seconds=120.0,
            auto_phase_downshift_delay_seconds=30.0,
            auto_phase_upshift_headroom_watts=250.0,
            auto_phase_downshift_margin_watts=150.0,
            auto_phase_mismatch_retry_seconds=300.0,
            auto_phase_mismatch_lockout_count=3,
            auto_phase_mismatch_lockout_seconds=1800.0,
            runtime_overrides_path="/run/wallbox-overrides.ini",
            _runtime_overrides_active=True,
            backend_mode="combined",
            meter_backend_type="shelly_combined",
            switch_backend_type="shelly_combined",
            charger_backend_type=None,
            _last_health_reason="init",
            _last_health_code=0,
            _last_auto_state="idle",
            _last_auto_state_code=0,
            _handle_write=MagicMock(),
        )
        controller = self._controller(service)

        with patch("venus_evcharger.bootstrap.paths.time.time", return_value=datetime(2026, 4, 20, 21, 0).timestamp()):
            controller.register_paths()

        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledState"]["value"], "night-boost")
        self.assertEqual(service._dbusservice.paths["/Auto/ScheduledReason"]["value"], "night-boost-window")
        self.assertEqual(service._dbusservice.paths["/Auto/ContactorLockoutAge"]["value"], -1)
        self.assertEqual(service._dbusservice.paths["/Auto/LastSwitchFeedbackAge"]["value"], -1)
        self.assertEqual(service._dbusservice.paths["/Auto/LastChargerTransportAge"]["value"], -1)
        self.assertEqual(service._dbusservice.paths["/Auto/ChargerRetryRemaining"]["value"], -1)
        self.assertTrue(service._dbusservice.paths["/Mode"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/PhaseSelection"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/StartSurplusWatts"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/ScheduledEnabledDays"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/ScheduledFallbackDelaySeconds"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/ScheduledLatestEndTime"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/ScheduledNightCurrent"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/DbusBackoffBaseSeconds"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/LearnChargePowerEnabled"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/PhasePreferLowestWhenIdle"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/PhaseSwitching"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/PhaseMismatchLockoutCount"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/PhaseLockoutReset"]["writeable"])
        self.assertTrue(service._dbusservice.paths["/Auto/ContactorLockoutReset"]["writeable"])
        self.assertTrue(service._dbusservice.register_called)

    def test_initialize_controllers_uses_port_wrappers_for_bound_controllers(self):
        service = SimpleNamespace(
            backend_mode="combined",
            meter_backend_type="shelly_combined",
            switch_backend_type="shelly_combined",
            charger_backend_type=None,
            meter_backend_config_path="",
            switch_backend_config_path="",
            charger_backend_config_path="",
            phase="L1",
            pm_component="Switch",
            pm_id=0,
            max_current=16.0,
        )
        controller = self._controller(service)

        controller.initialize_controllers()

        self.assertIsInstance(service._auto_controller.service, AutoDecisionPort)
        self.assertIsInstance(service._write_controller.port, WriteControllerPort)
        self.assertIsInstance(service._update_controller.service, UpdateCyclePort)
        self.assertEqual(service._backend_selection.mode, "combined")
        self.assertEqual(service._backend_selection.meter_type, "shelly_combined")
        self.assertEqual(service._backend_selection.switch_type, "shelly_combined")
        self.assertIsNotNone(service._meter_backend)
        self.assertIsNotNone(service._switch_backend)
        self.assertIsNone(service._charger_backend)

    def test_initialize_controllers_supports_meterless_split_charger_setup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = Path(temp_dir) / "charger.ini"
            charger_path.write_text(
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[EnableRequest]\nUrl=/charger/enable\n"
                "[CurrentRequest]\nUrl=/charger/current\n",
                encoding="utf-8",
            )
            service = SimpleNamespace(
                backend_mode="split",
                meter_backend_type="none",
                switch_backend_type="shelly_combined",
                charger_backend_type="template_charger",
                meter_backend_config_path="",
                switch_backend_config_path="",
                charger_backend_config_path=str(charger_path),
                phase="L1",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                host="192.168.1.20",
                session=MagicMock(),
            )
            controller = self._controller(service)

            controller.initialize_controllers()

            self.assertEqual(service._backend_selection.mode, "split")
            self.assertEqual(service._backend_selection.meter_type, "none")
            self.assertIsNone(service._meter_backend)
            self.assertIsNotNone(service._switch_backend)
            self.assertIsNotNone(service._charger_backend)

    def test_initialize_controllers_supports_switchless_split_charger_setup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = Path(temp_dir) / "charger.ini"
            charger_path.write_text(
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[EnableRequest]\nUrl=/charger/enable\n"
                "[CurrentRequest]\nUrl=/charger/current\n"
                "[PhaseRequest]\nUrl=/charger/phase\n",
                encoding="utf-8",
            )
            service = SimpleNamespace(
                backend_mode="split",
                meter_backend_type="none",
                switch_backend_type="none",
                charger_backend_type="template_charger",
                meter_backend_config_path="",
                switch_backend_config_path="",
                charger_backend_config_path=str(charger_path),
                phase="L1",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                host="192.168.1.20",
                session=MagicMock(),
                config={"DEFAULT": {}},
            )
            controller = self._controller(service)

            controller.initialize_controllers()
            controller.initialize_virtual_state()

            self.assertEqual(service._backend_selection.switch_type, "none")
            self.assertIsNone(service._switch_backend)
            self.assertIsNotNone(service._charger_backend)
            self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2"))
