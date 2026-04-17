# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
import sys
import configparser
import tempfile
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.modules["vedbus"] = MagicMock()

from shelly_wallbox.bootstrap.controller import (
    MONTH_WINDOW_DEFAULTS,
    ServiceBootstrapController,
    _enable_fault_diagnostics,
    _install_signal_logging,
    _logging_level_from_config,
    _request_mainloop_quit,
    _run_service_loop,
    _seasonal_month_windows,
    run_service_main,
)
from shelly_wallbox.ports import AutoDecisionPort, UpdateCyclePort, WriteControllerPort


class _FakeDbusService:
    def __init__(self):
        self.paths = {}
        self.register_called = False

    def add_path(self, path, value, **kwargs):
        self.paths[path] = {"value": value, **kwargs}

    def register(self):
        self.register_called = True


class TestServiceBootstrapController(unittest.TestCase):
    @staticmethod
    def _controller(service):
        return ServiceBootstrapController(
            service,
            normalize_phase_func=lambda value: value,
            normalize_mode_func=lambda value: int(value),
            mode_uses_auto_logic_func=lambda mode: int(mode) in (1, 2),
            month_window_func=lambda *_args, **_kwargs: ((8, 0), (18, 0)),
            age_seconds_func=lambda *_args, **_kwargs: 0,
            health_code_func=lambda reason: {"init": 0}.get(reason, 99),
            phase_values_func=lambda *_args, **_kwargs: {},
            read_version_func=lambda _name: "1.0",
            gobject_module=MagicMock(),
            script_path="/tmp/dbus_shelly_wallbox.py",
            formatters={
                "kwh": None,
                "a": None,
                "w": None,
                "v": None,
                "status": None,
            },
        )

    def test_fetch_device_info_with_fallback_returns_empty_dict_after_retries(self):
        service = SimpleNamespace(
            startup_device_info_retries=2,
            startup_device_info_retry_seconds=0,
            fetch_rpc=MagicMock(side_effect=RuntimeError("offline")),
        )

        controller = self._controller(service)
        self.assertEqual(controller.fetch_device_info_with_fallback(), {})
        self.assertEqual(service.fetch_rpc.call_count, 3)

    def test_logging_level_and_signal_install_cover_default_and_error_paths(self):
        empty_config = configparser.ConfigParser(default_section="NOT_DEFAULT")
        self.assertEqual(_logging_level_from_config(empty_config, "WARNING"), "WARNING")

        handlers = {}

        def fake_signal(signum, handler):
            handlers[signum] = handler

        with patch("shelly_wallbox.bootstrap.controller.signal.SIGTERM", 15), patch(
            "shelly_wallbox.bootstrap.controller.signal.SIGINT", 2
        ), patch("shelly_wallbox.bootstrap.controller.signal.SIGHUP", None), patch(
            "shelly_wallbox.bootstrap.controller.signal.signal",
            side_effect=fake_signal,
        ):
            _install_signal_logging(lambda: (_ for _ in ()).throw(RuntimeError("boom")))

        self.assertEqual(sorted(handlers), [2, 15])
        with patch("shelly_wallbox.bootstrap.controller.logging.debug") as debug_mock:
            handlers[15](15, None)
        debug_mock.assert_called_once()

    def test_register_paths_uses_main_script_path_and_registers_service(self):
        service = SimpleNamespace(
            _dbusservice=_FakeDbusService(),
            connection_name="Shelly RPC",
            deviceinstance=60,
            product_name="Shelly Wallbox Meter",
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
            runtime_overrides_path="/data/etc/wallbox-overrides.ini",
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

        self.assertEqual(service._dbusservice.paths["/Mgmt/ProcessName"]["value"], "/tmp/dbus_shelly_wallbox.py")
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
        self.assertEqual(service._dbusservice.paths["/Auto/RuntimeOverridesPath"]["value"], "/data/etc/wallbox-overrides.ini")
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

    def test_initialize_virtual_state_uses_config_defaults(self):
        service = SimpleNamespace(
            config={
                "DEFAULT": {
                    "Mode": "1",
                    "AutoStart": "0",
                    "StartStop": "1",
                    "Enable": "0",
                    "SetCurrent": "12.5",
                    "PhaseSelection": "P1_P2",
                }
            },
            max_current=16.0,
            _switch_backend=SimpleNamespace(
                capabilities=MagicMock(return_value=SimpleNamespace(supported_phase_selections=("P1", "P1_P2")))
            ),
        )
        controller = self._controller(service)

        controller.initialize_virtual_state()

        self.assertEqual(service.virtual_mode, 1)
        self.assertEqual(service.virtual_autostart, 0)
        self.assertEqual(service.virtual_startstop, 1)
        self.assertEqual(service.virtual_enable, 0)
        self.assertEqual(service.virtual_set_current, 12.5)
        self.assertEqual(list(service.auto_samples), [])
        self.assertIsNone(service.learned_charge_power_watts)
        self.assertIsNone(service.learned_charge_power_updated_at)
        self.assertEqual(service.learned_charge_power_state, "unknown")
        self.assertIsNone(service.learned_charge_power_learning_since)
        self.assertEqual(service.learned_charge_power_sample_count, 0)
        self.assertIsNone(service.learned_charge_power_phase)
        self.assertIsNone(service.learned_charge_power_voltage)
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 0)
        self.assertIsNone(service.learned_charge_power_signature_checked_session_started_at)
        self.assertIsNone(service.relay_last_changed_at)
        self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2"))
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service.active_phase_selection, "P1_P2")
        self.assertFalse(service._auto_mode_cutover_pending)

    def test_restore_runtime_state_sets_manual_startup_target_only_outside_auto_mode(self):
        manual_service = SimpleNamespace(
            virtual_mode=0,
            virtual_enable=0,
            virtual_startstop=1,
            _load_runtime_state=MagicMock(),
            _init_worker_state=MagicMock(),
        )
        auto_service = SimpleNamespace(
            virtual_mode=1,
            virtual_enable=1,
            virtual_startstop=1,
            _load_runtime_state=MagicMock(),
            _init_worker_state=MagicMock(),
        )

        self._controller(manual_service).restore_runtime_state()
        self._controller(auto_service).restore_runtime_state()

        self.assertTrue(manual_service._startup_manual_target)
        self.assertIsNone(auto_service._startup_manual_target)
        manual_service._load_runtime_state.assert_called_once_with()
        manual_service._init_worker_state.assert_called_once_with()

    def test_apply_device_metadata_prefers_custom_override_and_defaults(self):
        service = SimpleNamespace(
            config={"DEFAULT": {"ProductName": "Configured Product"}},
            custom_name_override="My Wallbox",
            host="192.168.1.20",
        )
        controller = self._controller(service)
        controller.fetch_device_info_with_fallback = MagicMock(return_value={})

        controller.apply_device_metadata()

        self.assertEqual(service.product_name, "Configured Product")
        self.assertEqual(service.custom_name, "My Wallbox")
        self.assertEqual(service.serial, "192168120")
        self.assertEqual(service.firmware_version, "1.0")
        self.assertEqual(service.hardware_version, "Shelly 1PM Gen4")

    def test_apply_device_metadata_uses_device_info_when_available(self):
        service = SimpleNamespace(
            config={"DEFAULT": {}},
            custom_name_override="",
            host="192.168.1.20",
        )
        controller = self._controller(service)
        controller.fetch_device_info_with_fallback = MagicMock(
            return_value={
                "name": "Shelly Garage",
                "mac": "ABCDEF",
                "fw_id": "fw-123",
                "model": "Shelly Plus",
            }
        )

        controller.apply_device_metadata()

        self.assertEqual(service.product_name, "Shelly Wallbox Meter")
        self.assertEqual(service.custom_name, "Shelly Garage")
        self.assertEqual(service.serial, "ABCDEF")
        self.assertEqual(service.firmware_version, "fw-123")
        self.assertEqual(service.hardware_version, "Shelly Plus")

    def test_start_runtime_loops_starts_worker_and_schedules_timers(self):
        gobject_module = MagicMock()
        service = SimpleNamespace(
            _start_io_worker=MagicMock(),
            runtime_state_path="/run/state.json",
            _state_summary=MagicMock(return_value="mode=1"),
            poll_interval_ms=1000,
            sign_of_life_minutes=10,
            _update=MagicMock(),
            _sign_of_life=MagicMock(),
        )
        controller = ServiceBootstrapController(
            service,
            normalize_phase_func=lambda value: value,
            normalize_mode_func=lambda value: int(value),
            mode_uses_auto_logic_func=lambda mode: int(mode) in (1, 2),
            month_window_func=lambda *_args, **_kwargs: ((8, 0), (18, 0)),
            age_seconds_func=lambda *_args, **_kwargs: 0,
            health_code_func=lambda reason: {"init": 0}.get(reason, 99),
            phase_values_func=lambda *_args, **_kwargs: {},
            read_version_func=lambda _name: "1.0",
            gobject_module=gobject_module,
            script_path="/tmp/dbus_shelly_wallbox.py",
            formatters={"kwh": None, "a": None, "w": None, "v": None, "status": None},
        )

        controller.start_runtime_loops()

        service._start_io_worker.assert_called_once_with()
        gobject_module.timeout_add.assert_any_call(1000, service._update)
        gobject_module.timeout_add.assert_any_call(600000, service._sign_of_life)

    def test_initialize_dbus_service_uses_device_instance_in_name(self):
        service = SimpleNamespace(service_name="com.victronenergy.evcharger", deviceinstance=60)
        controller = self._controller(service)

        with patch("shelly_wallbox.bootstrap.controller.VeDbusService", return_value="dbus-service") as factory:
            controller.initialize_dbus_service()

        factory.assert_called_once_with("com.victronenergy.evcharger.http_60", register=False)
        self.assertEqual(service._dbusservice, "dbus-service")

    def test_load_auto_policy_config_reads_thresholds_timing_and_daytime(self):
        parser = configparser.ConfigParser()
        parser.read_dict(
            {
                "DEFAULT": {
                    "AutoStartSurplusWatts": "1700",
                    "AutoStopSurplusWatts": "1200",
                    "AutoMinSoc": "40",
                    "AutoResumeSoc": "44",
                    "AutoStartMaxGridImportWatts": "70",
                    "AutoStopGridImportWatts": "350",
                    "AutoHighSocThreshold": "50",
                    "AutoHighSocReleaseThreshold": "45",
                    "AutoHighSocStartSurplusWatts": "1650",
                    "AutoHighSocStopSurplusWatts": "800",
                    "AutoAverageWindowSeconds": "45",
                    "AutoMinRuntimeSeconds": "360",
                    "AutoMinOfftimeSeconds": "90",
                    "AutoStartDelaySeconds": "12",
                    "AutoStopDelaySeconds": "18",
                    "AutoStopSurplusDelaySeconds": "54",
                    "AutoStopEwmaAlpha": "0.4",
                    "AutoStopEwmaAlphaStable": "0.6",
                    "AutoStopEwmaAlphaVolatile": "0.2",
                    "AutoStopSurplusVolatilityLowWatts": "120",
                    "AutoStopSurplusVolatilityHighWatts": "380",
                    "AutoLearnChargePower": "1",
                    "AutoReferenceChargePowerWatts": "2050",
                    "AutoLearnChargePowerMinWatts": "650",
                    "AutoLearnChargePowerAlpha": "0.3",
                    "AutoLearnChargePowerStartDelaySeconds": "40",
                    "AutoLearnChargePowerWindowSeconds": "120",
                    "AutoLearnChargePowerMaxAgeSeconds": "1800",
                    "AutoInputCacheSeconds": "150",
                    "AutoAuditLog": "1",
                    "AutoAuditLogPath": "/tmp/auto.log",
                    "AutoAuditLogMaxAgeHours": "24",
                    "AutoAuditLogRepeatSeconds": "60",
                    "AutoDaytimeOnly": "0",
                    "AutoNightLockStop": "1",
                }
            }
        )
        service = SimpleNamespace(config=parser)
        controller = self._controller(service)

        controller._load_auto_policy_config(parser["DEFAULT"])

        self.assertEqual(service.auto_start_surplus_watts, 1700.0)
        self.assertEqual(service.auto_stop_surplus_watts, 1200.0)
        self.assertEqual(service.auto_policy.normal_profile.start_surplus_watts, 1700.0)
        self.assertEqual(service.auto_policy.normal_profile.stop_surplus_watts, 1200.0)
        self.assertEqual(service.auto_high_soc_threshold, 50.0)
        self.assertEqual(service.auto_high_soc_release_threshold, 45.0)
        self.assertEqual(service.auto_high_soc_start_surplus_watts, 1650.0)
        self.assertEqual(service.auto_high_soc_stop_surplus_watts, 800.0)
        self.assertEqual(service.auto_policy.high_soc_profile.start_surplus_watts, 1650.0)
        self.assertEqual(service.auto_policy.high_soc_profile.stop_surplus_watts, 800.0)
        self.assertEqual(service.auto_min_soc, 40.0)
        self.assertEqual(service.auto_resume_soc, 44.0)
        self.assertEqual(service.auto_start_max_grid_import_watts, 70.0)
        self.assertEqual(service.auto_stop_grid_import_watts, 350.0)
        self.assertEqual(service.auto_average_window_seconds, 45.0)
        self.assertEqual(service.auto_min_runtime_seconds, 360.0)
        self.assertEqual(service.auto_min_offtime_seconds, 90.0)
        self.assertEqual(service.auto_start_delay_seconds, 12.0)
        self.assertEqual(service.auto_stop_delay_seconds, 18.0)
        self.assertEqual(service.auto_stop_surplus_delay_seconds, 54.0)
        self.assertEqual(service.auto_stop_ewma_alpha, 0.4)
        self.assertEqual(service.auto_policy.ewma.base_alpha, 0.4)
        self.assertEqual(service.auto_stop_ewma_alpha_stable, 0.6)
        self.assertEqual(service.auto_stop_ewma_alpha_volatile, 0.2)
        self.assertEqual(service.auto_stop_surplus_volatility_low_watts, 120.0)
        self.assertEqual(service.auto_stop_surplus_volatility_high_watts, 380.0)
        self.assertTrue(service.auto_learn_charge_power_enabled)
        self.assertEqual(service.auto_reference_charge_power_watts, 2050.0)
        self.assertEqual(service.auto_learn_charge_power_min_watts, 650.0)
        self.assertEqual(service.auto_learn_charge_power_alpha, 0.3)
        self.assertEqual(service.auto_learn_charge_power_start_delay_seconds, 40.0)
        self.assertEqual(service.auto_learn_charge_power_window_seconds, 120.0)
        self.assertEqual(service.auto_learn_charge_power_max_age_seconds, 1800.0)
        self.assertEqual(service.auto_input_cache_seconds, 150.0)
        self.assertTrue(service.auto_audit_log)
        self.assertEqual(service.auto_audit_log_path, "/tmp/auto.log")
        self.assertEqual(service.auto_audit_log_max_age_hours, 24.0)
        self.assertEqual(service.auto_audit_log_repeat_seconds, 60.0)
        self.assertFalse(service.auto_daytime_only)
        self.assertTrue(service.auto_night_lock_stop)

    def test_load_auto_policy_config_validates_policy_while_loading(self):
        parser = configparser.ConfigParser()
        parser.read_dict(
            {
                "DEFAULT": {
                    "AutoStartSurplusWatts": "1850",
                    "AutoStopSurplusWatts": "2400",
                    "AutoMinSoc": "35",
                    "AutoResumeSoc": "30",
                    "AutoHighSocThreshold": "55",
                    "AutoHighSocReleaseThreshold": "60",
                    "AutoHighSocStartSurplusWatts": "1650",
                    "AutoHighSocStopSurplusWatts": "1800",
                    "AutoStopSurplusDelaySeconds": "-1",
                    "AutoStopEwmaAlpha": "-1",
                    "AutoStopEwmaAlphaStable": "-1",
                    "AutoStopEwmaAlphaVolatile": "-1",
                    "AutoStopSurplusVolatilityLowWatts": "200",
                    "AutoStopSurplusVolatilityHighWatts": "100",
                    "AutoReferenceChargePowerWatts": "-1",
                    "AutoLearnChargePowerMinWatts": "-1",
                    "AutoLearnChargePowerAlpha": "-1",
                    "AutoLearnChargePowerStartDelaySeconds": "-1",
                    "AutoLearnChargePowerWindowSeconds": "-1",
                    "AutoLearnChargePowerMaxAgeSeconds": "-1",
                }
            }
        )
        service = SimpleNamespace(config=parser)
        controller = self._controller(service)

        controller._load_auto_policy_config(parser["DEFAULT"])

        self.assertEqual(service.auto_policy.normal_profile.stop_surplus_watts, 1850.0)
        self.assertEqual(service.auto_policy.high_soc_profile.stop_surplus_watts, 1650.0)
        self.assertEqual(service.auto_policy.high_soc_release_threshold, 55.0)
        self.assertEqual(service.auto_policy.resume_soc, 35.0)
        self.assertEqual(service.auto_policy.stop_surplus_delay_seconds, 0.0)
        self.assertEqual(service.auto_policy.ewma.base_alpha, 0.35)
        self.assertEqual(service.auto_policy.ewma.stable_alpha, 0.55)
        self.assertEqual(service.auto_policy.ewma.volatile_alpha, 0.15)
        self.assertEqual(service.auto_policy.ewma.volatility_high_watts, 200.0)
        self.assertEqual(service.auto_policy.learn_charge_power.reference_power_watts, 1900.0)
        self.assertEqual(service.auto_policy.learn_charge_power.min_watts, 0.0)
        self.assertEqual(service.auto_policy.learn_charge_power.alpha, 0.2)
        self.assertEqual(service.auto_policy.learn_charge_power.start_delay_seconds, 0.0)
        self.assertEqual(service.auto_policy.learn_charge_power.window_seconds, 0.0)
        self.assertEqual(service.auto_policy.learn_charge_power.max_age_seconds, 0.0)
        self.assertEqual(len(service.auto_month_windows), len(MONTH_WINDOW_DEFAULTS))

    def test_load_runtime_configuration_populates_identity_sources_and_helper_settings(self):
        parser = configparser.ConfigParser()
        parser.read_dict(
            {
                "DEFAULT": {
                    "DeviceInstance": "77",
                    "Host": "192.168.1.44",
                    "Phase": "L2",
                    "Position": "3",
                    "PollIntervalMs": "2500",
                    "SignOfLifeLog": "7",
                    "MaxCurrent": "20",
                    "MinCurrent": "5",
                    "ChargingThresholdWatts": "250",
                    "IdleStatus": "9",
                    "ThreePhaseVoltageMode": "line",
                    "Username": "user",
                    "Password": "secret",
                    "DigestAuth": "yes",
                    "ShellyComponent": "Relay",
                    "ShellyId": "4",
                    "Name": "Garage Wallbox",
                    "ServiceName": "com.example.ev",
                    "Connection": "Custom RPC",
                    "RuntimeStatePath": "/tmp/runtime.json",
                    "AutoPvService": "com.example.pv",
                    "AutoPvServicePrefix": "com.example.pvprefix",
                    "AutoPvPath": "/Pv/Power",
                    "AutoPvMaxServices": "2",
                    "AutoPvScanIntervalSeconds": "12",
                    "AutoUseDcPv": "0",
                    "AutoDcPvService": "com.example.system",
                    "AutoDcPvPath": "/Dc/Pv",
                    "AutoBatteryService": "com.example.battery",
                    "AutoBatterySocPath": "/Battery/Soc",
                    "AutoBatteryServicePrefix": "com.example.battprefix",
                    "AutoBatteryScanIntervalSeconds": "25",
                    "AutoAllowWithoutBatterySoc": "false",
                    "AutoDbusBackoffBaseSeconds": "3",
                    "AutoDbusBackoffMaxSeconds": "9",
                    "AutoGridService": "com.example.grid",
                    "AutoGridL1Path": "/Grid/L1",
                    "AutoGridL2Path": "/Grid/L2",
                    "AutoGridL3Path": "/Grid/L3",
                    "AutoGridRequireAllPhases": "0",
                    "AutoGridMissingStopSeconds": "33",
                    "AutoGridRecoveryStartSeconds": "14",
                    "AutoInputSnapshotPath": "/tmp/auto.json",
                    "AutoPvPollIntervalMs": "2200",
                    "AutoGridPollIntervalMs": "3300",
                    "AutoBatteryPollIntervalMs": "4400",
                    "AutoInputValidationPollSeconds": "45",
                    "AutoInputHelperRestartSeconds": "8",
                    "AutoInputHelperStaleSeconds": "19",
                    "AutoShellySoftFailSeconds": "17",
                    "AutoWatchdogStaleSeconds": "111",
                    "AutoWatchdogRecoverySeconds": "22",
                    "AutoStartupWarmupSeconds": "18",
                    "AutoManualOverrideSeconds": "333",
                    "StartupDeviceInfoRetries": "4",
                    "StartupDeviceInfoRetrySeconds": "1.5",
                    "ShellyRequestTimeoutSeconds": "4.5",
                    "DbusMethodTimeoutSeconds": "2.5",
                }
            }
        )
        service = SimpleNamespace(_load_config=MagicMock(return_value=parser), _validate_runtime_config=MagicMock())
        controller = self._controller(service)

        controller.load_runtime_configuration()

        self.assertEqual(service.deviceinstance, 77)
        self.assertEqual(service.host, "192.168.1.44")
        self.assertEqual(service.phase, "L2")
        self.assertEqual(service.position, 3)
        self.assertEqual(service.poll_interval_ms, 2500)
        self.assertEqual(service.sign_of_life_minutes, 7)
        self.assertEqual(service.max_current, 20.0)
        self.assertEqual(service.min_current, 5.0)
        self.assertEqual(service.charging_threshold_watts, 250.0)
        self.assertEqual(service.idle_status, 9)
        self.assertEqual(service.voltage_mode, "line")
        self.assertEqual(service.username, "user")
        self.assertEqual(service.password, "secret")
        self.assertTrue(service.use_digest_auth)
        self.assertEqual(service.pm_component, "Relay")
        self.assertEqual(service.pm_id, 4)
        self.assertEqual(service.custom_name_override, "Garage Wallbox")
        self.assertEqual(service.service_name, "com.example.ev")
        self.assertEqual(service.connection_name, "Custom RPC")
        self.assertEqual(service.runtime_state_path, "/tmp/runtime.json")
        self.assertEqual(service.backend_mode, "combined")
        self.assertEqual(service.meter_backend_type, "shelly_combined")
        self.assertEqual(service.switch_backend_type, "shelly_combined")
        self.assertIsNone(service.charger_backend_type)
        self.assertIsNone(service.meter_backend_config_path)
        self.assertIsNone(service.switch_backend_config_path)
        self.assertIsNone(service.charger_backend_config_path)
        self.assertEqual(service.auto_pv_service, "com.example.pv")
        self.assertEqual(service.auto_pv_service_prefix, "com.example.pvprefix")
        self.assertEqual(service.auto_pv_path, "/Pv/Power")
        self.assertEqual(service.auto_pv_max_services, 2)
        self.assertEqual(service.auto_pv_scan_interval_seconds, 12.0)
        self.assertFalse(service.auto_use_dc_pv)
        self.assertEqual(service.auto_dc_pv_service, "com.example.system")
        self.assertEqual(service.auto_dc_pv_path, "/Dc/Pv")
        self.assertEqual(service.auto_battery_service, "com.example.battery")
        self.assertEqual(service.auto_battery_soc_path, "/Battery/Soc")
        self.assertEqual(service.auto_battery_service_prefix, "com.example.battprefix")
        self.assertEqual(service.auto_battery_scan_interval_seconds, 25.0)
        self.assertFalse(service.auto_allow_without_battery_soc)
        self.assertEqual(service.auto_dbus_backoff_base_seconds, 3.0)
        self.assertEqual(service.auto_dbus_backoff_max_seconds, 9.0)
        self.assertEqual(service.auto_grid_service, "com.example.grid")
        self.assertEqual(service.auto_grid_l1_path, "/Grid/L1")
        self.assertEqual(service.auto_grid_l2_path, "/Grid/L2")
        self.assertEqual(service.auto_grid_l3_path, "/Grid/L3")
        self.assertFalse(service.auto_grid_require_all_phases)
        self.assertEqual(service.auto_grid_missing_stop_seconds, 33.0)
        self.assertEqual(service.auto_grid_recovery_start_seconds, 14.0)
        self.assertEqual(service.auto_input_snapshot_path, "/tmp/auto.json")
        self.assertEqual(service.auto_pv_poll_interval_seconds, 2.2)
        self.assertEqual(service.auto_grid_poll_interval_seconds, 3.3)
        self.assertEqual(service.auto_battery_poll_interval_seconds, 4.4)
        self.assertEqual(service.auto_input_validation_poll_seconds, 45.0)
        self.assertEqual(service.auto_input_helper_restart_seconds, 8.0)
        self.assertEqual(service.auto_input_helper_stale_seconds, 19.0)
        self.assertEqual(service.auto_shelly_soft_fail_seconds, 17.0)
        self.assertEqual(service.auto_watchdog_stale_seconds, 111.0)
        self.assertEqual(service.auto_watchdog_recovery_seconds, 22.0)
        self.assertEqual(service.auto_startup_warmup_seconds, 18.0)
        self.assertEqual(service.auto_manual_override_seconds, 333.0)
        self.assertEqual(service.startup_device_info_retries, 4)
        self.assertEqual(service.startup_device_info_retry_seconds, 1.5)
        self.assertEqual(service.shelly_request_timeout_seconds, 4.5)
        self.assertEqual(service.dbus_method_timeout_seconds, 2.5)
        service._validate_runtime_config.assert_called_once_with()

    def test_load_runtime_configuration_reads_backend_section_when_present(self):
        parser = configparser.ConfigParser()
        parser.read_dict(
            {
                "DEFAULT": {"Host": "192.168.1.20"},
                "Backends": {
                    "Mode": "split",
                    "MeterType": "shelly_combined",
                    "SwitchType": "shelly_combined",
                    "ChargerType": "",
                    "MeterConfigPath": "/data/meter.ini",
                    "SwitchConfigPath": "/data/switch.ini",
                    "ChargerConfigPath": "",
                },
            }
        )
        service = SimpleNamespace(_load_config=MagicMock(return_value=parser), _validate_runtime_config=MagicMock())
        controller = self._controller(service)

        controller.load_runtime_configuration()

        self.assertEqual(service.backend_mode, "split")
        self.assertEqual(service.meter_backend_type, "shelly_combined")
        self.assertEqual(service.switch_backend_type, "shelly_combined")
        self.assertIsNone(service.charger_backend_type)
        self.assertEqual(service.meter_backend_config_path, Path("/data/meter.ini"))
        self.assertEqual(service.switch_backend_config_path, Path("/data/switch.ini"))
        self.assertIsNone(service.charger_backend_config_path)

    def test_load_runtime_configuration_rejects_invalid_meterless_backend_combo_early(self):
        parser = configparser.ConfigParser()
        parser.read_dict(
            {
                "DEFAULT": {"Host": "192.168.1.20"},
                "Backends": {
                    "Mode": "split",
                    "MeterType": "none",
                    "SwitchType": "shelly_combined",
                    "ChargerType": "",
                },
            }
        )
        service = SimpleNamespace(_load_config=MagicMock(return_value=parser), _validate_runtime_config=MagicMock())
        controller = self._controller(service)

        with self.assertRaisesRegex(ValueError, "MeterType=none requires a configured charger backend"):
            controller.load_runtime_configuration()

        service._validate_runtime_config.assert_not_called()

    def test_logging_level_and_seasonal_windows_helpers(self):
        parser = configparser.ConfigParser()
        parser.read_dict({"DEFAULT": {"Logging": "debug"}})

        windows = _seasonal_month_windows(parser, lambda *_args, **_kwargs: ((7, 0), (19, 0)))

        self.assertEqual(_logging_level_from_config(parser), "DEBUG")
        self.assertEqual(len(windows), len(MONTH_WINDOW_DEFAULTS))
        self.assertEqual(windows[1], ((7, 0), (19, 0)))

    def test_initialize_service_runs_full_startup_sequence(self):
        service = SimpleNamespace()
        controller = self._controller(service)
        calls = []
        controller.load_runtime_configuration = MagicMock(side_effect=lambda: calls.append("config"))
        controller.initialize_controllers = MagicMock(side_effect=lambda: calls.append("controllers"))
        controller.initialize_virtual_state = MagicMock(side_effect=lambda: calls.append("virtual"))
        controller.restore_runtime_state = MagicMock(side_effect=lambda: calls.append("restore"))
        controller.initialize_dbus_service = MagicMock(side_effect=lambda: calls.append("dbus"))
        controller.apply_device_metadata = MagicMock(side_effect=lambda: calls.append("metadata"))
        controller.register_paths = MagicMock(side_effect=lambda: calls.append("paths"))
        controller.start_runtime_loops = MagicMock(side_effect=lambda: calls.append("loops"))

        controller.initialize_service()

        self.assertEqual(
            calls,
            ["config", "controllers", "virtual", "restore", "dbus", "metadata", "paths", "loops"],
        )

    def test_request_mainloop_quit_uses_idle_add_when_available_and_falls_back(self):
        mainloop = MagicMock()
        gobject_module = MagicMock()

        _request_mainloop_quit(gobject_module, mainloop)
        gobject_module.idle_add.assert_called_once_with(mainloop.quit)

        gobject_module = SimpleNamespace(idle_add=MagicMock(side_effect=RuntimeError("nope")))
        _request_mainloop_quit(gobject_module, mainloop)
        mainloop.quit.assert_called()

    def test_run_service_loop_instantiates_service_and_runs_mainloop(self):
        mainloop = MagicMock()
        gobject_module = MagicMock()
        gobject_module.MainLoop.return_value = mainloop
        service_factory = MagicMock()

        with patch("shelly_wallbox.bootstrap.controller._install_signal_logging") as install_signal_logging:
            _run_service_loop(service_factory, gobject_module)

        service_factory.assert_called_once_with()
        install_signal_logging.assert_called_once()
        mainloop.run.assert_called_once_with()

    def test_enable_fault_diagnostics_swallows_failures(self):
        with patch("shelly_wallbox.bootstrap.controller.faulthandler.enable", side_effect=RuntimeError("nope")):
            _enable_fault_diagnostics()

    def test_setup_dbus_mainloop_initializes_threads_and_tolerates_missing_threads_init(self):
        dbus_module = ModuleType("dbus")
        mainloop_module = ModuleType("dbus.mainloop")
        glib_module = ModuleType("dbus.mainloop.glib")
        glib_module.DBusGMainLoop = MagicMock()
        glib_module.threads_init = MagicMock()

        with patch.dict(
            sys.modules,
            {
                "dbus": dbus_module,
                "dbus.mainloop": mainloop_module,
                "dbus.mainloop.glib": glib_module,
            },
            clear=False,
        ):
            import dbus as imported_dbus

            imported_dbus.mainloop = mainloop_module
            mainloop_module.glib = glib_module
            from shelly_wallbox.bootstrap.controller import _setup_dbus_mainloop

            _setup_dbus_mainloop()

        glib_module.threads_init.assert_called_once_with()
        glib_module.DBusGMainLoop.assert_called_once_with(set_as_default=True)

        glib_module = ModuleType("dbus.mainloop.glib")
        glib_module.DBusGMainLoop = MagicMock()
        glib_module.threads_init = MagicMock(side_effect=AttributeError("missing"))
        with patch.dict(
            sys.modules,
            {
                "dbus": dbus_module,
                "dbus.mainloop": mainloop_module,
                "dbus.mainloop.glib": glib_module,
            },
            clear=False,
        ):
            import dbus as imported_dbus

            imported_dbus.mainloop = mainloop_module
            mainloop_module.glib = glib_module
            _setup_dbus_mainloop()

        glib_module.DBusGMainLoop.assert_called_once_with(set_as_default=True)

    def test_run_service_main_runs_loop_and_logs_critical_on_failure(self):
        gobject_module = MagicMock()
        with patch("shelly_wallbox.bootstrap.controller._enable_fault_diagnostics") as enable_faults:
            with patch("shelly_wallbox.bootstrap.controller._setup_dbus_mainloop") as setup_loop:
                with patch("shelly_wallbox.bootstrap.controller._run_service_loop") as run_loop:
                    run_service_main(lambda: None, "/tmp/does-not-matter.ini", gobject_module)

        enable_faults.assert_called_once_with()
        setup_loop.assert_called_once_with()
        run_loop.assert_called_once()

        with patch("shelly_wallbox.bootstrap.controller._setup_dbus_mainloop", side_effect=RuntimeError("boom")):
            with patch("shelly_wallbox.bootstrap.controller.logging.critical") as critical_mock:
                run_service_main(lambda: None, "/tmp/does-not-matter.ini", gobject_module)
        critical_mock.assert_called_once()

    def test_install_signal_logging_requests_clean_shutdown(self):
        handlers = {}
        quit_calls = []

        def _capture_handler(signum, handler):
            handlers[signum] = handler

        with patch("shelly_wallbox.bootstrap.controller.signal.signal", side_effect=_capture_handler):
            _install_signal_logging(lambda: quit_calls.append("quit"))

        self.assertTrue(handlers)
        handlers[next(iter(handlers))](15, None)
        self.assertEqual(quit_calls, ["quit"])

    def test_install_signal_logging_handles_missing_callback_and_registration_failures(self):
        handlers = {}

        def _capture_handler(signum, handler):
            if not handlers:
                raise RuntimeError("nope")
            handlers[signum] = handler

        with patch("shelly_wallbox.bootstrap.controller.signal.signal", side_effect=_capture_handler):
            with patch("shelly_wallbox.bootstrap.controller.logging.debug") as debug_mock:
                _install_signal_logging()

        debug_mock.assert_called()

        handlers = {}
        with patch("shelly_wallbox.bootstrap.controller.signal.signal", side_effect=lambda signum, handler: handlers.setdefault(signum, handler)):
            _install_signal_logging(None)

        self.assertTrue(handlers)
        handlers[next(iter(handlers))](15, None)

    def test_fetch_device_info_with_fallback_logs_retry_and_sleeps(self):
        service = SimpleNamespace(
            startup_device_info_retries=1,
            startup_device_info_retry_seconds=2.5,
            fetch_rpc=MagicMock(side_effect=[RuntimeError("offline"), {"mac": "ABC"}]),
        )
        controller = self._controller(service)

        with patch("shelly_wallbox.bootstrap.controller.time.sleep") as sleep_mock:
            with patch("shelly_wallbox.bootstrap.controller.logging.warning") as warning_mock:
                result = controller.fetch_device_info_with_fallback()

        self.assertEqual(result, {"mac": "ABC"})
        sleep_mock.assert_called_once_with(2.5)
        self.assertGreaterEqual(warning_mock.call_count, 1)

    def test_register_paths_logs_and_reraises_add_path_failures(self):
        class _BrokenDbusService(_FakeDbusService):
            def add_path(self, path, value, **kwargs):
                if path == "/Mode":
                    raise RuntimeError("boom")
                return super().add_path(path, value, **kwargs)

        service = SimpleNamespace(
            _dbusservice=_BrokenDbusService(),
            connection_name="Shelly RPC",
            deviceinstance=60,
            product_name="Shelly Wallbox Meter",
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
            _last_health_reason="init",
            _last_health_code=0,
            _handle_write=MagicMock(),
        )
        controller = self._controller(service)

        with patch("shelly_wallbox.bootstrap.controller.logging.error") as error_mock:
            with self.assertRaises(RuntimeError):
                controller.register_paths()

        error_mock.assert_called_once()
