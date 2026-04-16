# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from shelly_wallbox.auto.policy import (
    AutoLearnChargePowerPolicy,
    AutoPolicy,
    AutoStopEwmaPolicy,
    AutoThresholdProfile,
)
from shelly_wallbox.controllers.state import ServiceStateController
from tests.wallbox_test_fixtures import make_runtime_state_service, make_state_validation_service


class TestServiceStateController(unittest.TestCase):
    @staticmethod
    def _normalize_mode(value: object) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, (int, float, str)):
            return int(value)
        return 0

    def test_config_path_and_coercion_helpers_and_state_summary(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_enable=0,
            virtual_startstop=1,
            virtual_autostart=1,
            _auto_mode_cutover_pending=True,
            _ignore_min_offtime_once=False,
            auto_month_windows={4: ((7, 30), (19, 30))},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            active_phase_selection="P1_P2",
            requested_phase_selection="P1_P2_P3",
            supported_phase_selections=("P1", "P1_P2", "P1_P2_P3"),
            backend_mode="split",
            meter_backend_type="template_meter",
            switch_backend_type="template_switch",
            charger_backend_type="template_charger",
            _charger_target_current_amps=13.0,
            _last_charger_transport_reason="offline",
            _last_charger_transport_source="read",
            _last_charger_transport_at=100.0,
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=105.0,
            _last_confirmed_pm_status={"_phase_selection": "P1", "output": True},
            _phase_switch_mismatch_active=True,
            _phase_switch_lockout_selection="P1_P2_P3",
            _phase_switch_lockout_until=130.0,
            _last_switch_feedback_closed=False,
            _last_switch_interlock_ok=True,
            _contactor_fault_counts={"contactor-suspected-open": 2},
            _contactor_lockout_reason="contactor-suspected-open",
            _contactor_lockout_source="count-threshold",
            _contactor_lockout_at=90.0,
            _last_charger_state_status="charging",
            _last_charger_state_fault="none",
            _last_status_source="charger-fault",
            _last_auto_state="waiting",
            _last_health_reason="running",
        )
        controller = ServiceStateController(service, self._normalize_mode)
        with patch("shelly_wallbox.controllers.state.time.time", return_value=100.0):
            summary = controller.state_summary()

        self.assertTrue(controller.config_path().endswith("config.shelly_wallbox.ini"))
        self.assertEqual(controller.coerce_runtime_int("7"), 7)
        self.assertEqual(controller.coerce_runtime_int(True, 3), 3)
        self.assertEqual(controller.coerce_runtime_int("bad", 3), 3)
        self.assertEqual(controller.coerce_runtime_float("7.5"), 7.5)
        self.assertEqual(controller.coerce_runtime_float(True, 3.5), 3.5)
        self.assertEqual(controller.coerce_runtime_float("bad", 3.5), 3.5)
        self.assertEqual(controller.coerce_runtime_float(float("nan"), 3.5), 3.5)
        self.assertEqual(controller.coerce_runtime_float(float("inf"), 3.5), 3.5)
        self.assertIsNone(controller._coerce_optional_runtime_float(None))
        self.assertEqual(controller._coerce_optional_runtime_float("7.5"), 7.5)
        self.assertIsNone(controller._coerce_optional_runtime_past_time(110.0, 100.0))
        self.assertEqual(controller._coerce_optional_runtime_past_time(100.5, 100.0), 100.5)
        self.assertIn("mode=1", summary)
        self.assertIn("phase=P1_P2", summary)
        self.assertIn("phase_req=P1_P2_P3", summary)
        self.assertIn("phase_obs=P1", summary)
        self.assertIn("phase_mismatch=1", summary)
        self.assertIn("phase_lockout=1", summary)
        self.assertIn("phase_lockout_target=P1_P2_P3", summary)
        self.assertIn("phase_effective=P1,P1_P2", summary)
        self.assertIn("phase_degraded=1", summary)
        self.assertIn("switch_feedback=0", summary)
        self.assertIn("switch_interlock=1", summary)
        self.assertIn("switch_feedback_mismatch=1", summary)
        self.assertIn("contactor_fault_count=2", summary)
        self.assertIn("contactor_suspected_open=0", summary)
        self.assertIn("contactor_suspected_welded=0", summary)
        self.assertIn("contactor_lockout=1", summary)
        self.assertIn("contactor_lockout_reason=contactor-suspected-open", summary)
        self.assertIn("backend=split", summary)
        self.assertIn("meter_backend=template_meter", summary)
        self.assertIn("switch_backend=template_switch", summary)
        self.assertIn("charger_backend=template_charger", summary)
        self.assertIn("charger_target=13.0", summary)
        self.assertIn("charger_status=charging", summary)
        self.assertIn("charger_fault=none", summary)
        self.assertIn("charger_transport=offline", summary)
        self.assertIn("charger_transport_source=read", summary)
        self.assertIn("charger_retry=offline", summary)
        self.assertIn("charger_retry_source=read", summary)
        self.assertIn("charger_retry_remaining=5", summary)
        self.assertIn("status_source=charger-fault", summary)
        self.assertIn("fault=0", summary)
        self.assertIn("fault_reason=na", summary)
        self.assertIn("auto_state=waiting", summary)
        self.assertIn("recovery=0", summary)
        self.assertIn("health=running", summary)

    def test_state_summary_marks_contactor_suspicions_from_health_reason(self) -> None:
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_autostart=1,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            active_phase_selection="P1",
            requested_phase_selection="P1",
            supported_phase_selections=("P1",),
            backend_mode="split",
            meter_backend_type="template_meter",
            switch_backend_type="template_switch",
            charger_backend_type=None,
            _charger_target_current_amps=None,
            _last_confirmed_pm_status={"_phase_selection": "P1", "output": False},
            _phase_switch_mismatch_active=False,
            _phase_switch_lockout_selection=None,
            _phase_switch_lockout_until=None,
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_charger_state_status="",
            _last_charger_state_fault="",
            _last_status_source="switch-feedback",
            _last_auto_state="waiting",
            _last_health_reason="contactor-suspected-open",
            _contactor_fault_counts={},
            _contactor_lockout_reason="",
        )
        controller = ServiceStateController(service, self._normalize_mode)
        with patch("shelly_wallbox.controllers.state.time.time", return_value=100.0):
            open_summary = controller.state_summary()

        self.assertIn("contactor_suspected_open=1", open_summary)
        self.assertIn("contactor_suspected_welded=0", open_summary)
        self.assertIn("contactor_lockout=0", open_summary)
        self.assertIn("fault=0", open_summary)
        self.assertIn("recovery=0", open_summary)

        service._last_health_reason = "contactor-suspected-welded"
        with patch("shelly_wallbox.controllers.state.time.time", return_value=100.0):
            welded_summary = controller.state_summary()

        self.assertIn("contactor_suspected_open=0", welded_summary)
        self.assertIn("contactor_suspected_welded=1", welded_summary)

        service._last_health_reason = "contactor-lockout-open"
        service._last_auto_state = "recovery"
        with patch("shelly_wallbox.controllers.state.time.time", return_value=100.0):
            lockout_summary = controller.state_summary()

        self.assertIn("fault=1", lockout_summary)
        self.assertIn("fault_reason=contactor-lockout-open", lockout_summary)
        self.assertIn("recovery=1", lockout_summary)

    def test_state_summary_includes_scheduled_v2_diagnostics(self) -> None:
        service = SimpleNamespace(
            virtual_mode=2,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_autostart=1,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            active_phase_selection="P1",
            requested_phase_selection="P1",
            supported_phase_selections=("P1",),
            auto_month_windows={4: ((7, 30), (19, 30))},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            backend_mode="split",
            meter_backend_type="template_meter",
            switch_backend_type="template_switch",
            charger_backend_type=None,
            _charger_target_current_amps=None,
            _last_confirmed_pm_status={"_phase_selection": "P1", "output": False},
            _phase_switch_mismatch_active=False,
            _phase_switch_lockout_selection=None,
            _phase_switch_lockout_until=None,
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_charger_state_status="",
            _last_charger_state_fault="",
            _last_status_source="scheduled",
            _last_auto_state="charging",
            _last_health_reason="scheduled-night-charge",
            _contactor_fault_counts={},
            _contactor_lockout_reason="",
        )
        controller = ServiceStateController(service, self._normalize_mode)
        now = datetime(2026, 4, 20, 21, 0).timestamp()
        with patch("shelly_wallbox.controllers.state.time.time", return_value=now):
            summary = controller.state_summary()

        self.assertIn("scheduled_state=night-boost", summary)
        self.assertIn("scheduled_target_day=Tue", summary)
        self.assertIn("scheduled_boost=1", summary)

    def test_load_runtime_state_missing_file_and_load_config_success(self) -> None:
        service = SimpleNamespace(runtime_state_path="/tmp/does-not-exist.json")
        controller = ServiceStateController(service, self._normalize_mode)
        controller.load_runtime_state()

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("[DEFAULT]\nHost=192.168.1.20\n")
            config_path = handle.name
        def _cleanup_config() -> None:
            if os.path.exists(config_path):
                os.unlink(config_path)

        self.addCleanup(_cleanup_config)

        with patch.object(ServiceStateController, "config_path", return_value=config_path):
            config = controller.load_config()

        self.assertEqual(config["DEFAULT"]["Host"], "192.168.1.20")

    def test_load_config_applies_runtime_overrides_and_records_override_state(self) -> None:
        service = SimpleNamespace(runtime_state_path="/tmp/does-not-exist.json", deviceinstance=60)
        controller = ServiceStateController(service, self._normalize_mode)

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.ini")
            overrides_path = os.path.join(temp_dir, "runtime-overrides.ini")
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "[DEFAULT]\n"
                    "Host=192.168.1.20\n"
                    f"RuntimeOverridesPath={overrides_path}\n"
                    "Mode=0\n"
                    "AutoStart=1\n"
                    "AutoStartSurplusWatts=1700\n"
                    "PhaseSelection=P1\n"
                    "AutoScheduledEnabledDays=Mon,Tue,Wed,Thu,Fri\n"
                )
            with open(overrides_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "[RuntimeOverrides]\n"
                    "Mode=1\n"
                    "AutoStart=0\n"
                    "AutoStartSurplusWatts=1850\n"
                    "PhaseSelection=P1_P2\n"
                    "AutoPhaseMismatchLockoutCount=4\n"
                    "AutoLearnChargePower=0\n"
                    "AutoReferenceChargePowerWatts=2050\n"
                    "AutoScheduledEnabledDays=Sat,Sun\n"
                    "AutoScheduledLatestEndTime=07:15\n"
                )

            with patch.object(ServiceStateController, "config_path", return_value=config_path):
                config = controller.load_config()

        self.assertEqual(config["DEFAULT"]["Mode"], "1")
        self.assertEqual(config["DEFAULT"]["AutoStart"], "0")
        self.assertEqual(config["DEFAULT"]["AutoStartSurplusWatts"], "1850")
        self.assertEqual(config["DEFAULT"]["PhaseSelection"], "P1_P2")
        self.assertEqual(config["DEFAULT"]["AutoPhaseMismatchLockoutCount"], "4")
        self.assertEqual(config["DEFAULT"]["AutoLearnChargePower"], "0")
        self.assertEqual(config["DEFAULT"]["AutoReferenceChargePowerWatts"], "2050")
        self.assertEqual(config["DEFAULT"]["AutoScheduledEnabledDays"], "Sat,Sun")
        self.assertEqual(config["DEFAULT"]["AutoScheduledLatestEndTime"], "07:15")
        self.assertTrue(service._runtime_overrides_active)
        self.assertEqual(service.runtime_overrides_path, overrides_path)
        self.assertEqual(
            service._runtime_overrides_values,
            {
                "Mode": "1",
                "AutoStart": "0",
                "AutoStartSurplusWatts": "1850",
                "PhaseSelection": "P1_P2",
                "AutoPhaseMismatchLockoutCount": "4",
                "AutoLearnChargePower": "0",
                "AutoReferenceChargePowerWatts": "2050",
                "AutoScheduledEnabledDays": "Sat,Sun",
                "AutoScheduledLatestEndTime": "07:15",
            },
        )

    def test_save_runtime_overrides_writes_small_ini_and_skips_unchanged_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            overrides_path = os.path.join(temp_dir, "runtime-overrides.ini")
            service = SimpleNamespace(
                runtime_overrides_path=overrides_path,
                virtual_mode=1,
                virtual_autostart=0,
                virtual_set_current=13.5,
                min_current=6.0,
                max_current=16.0,
                requested_phase_selection="P1_P2",
                auto_start_surplus_watts=1850.0,
                auto_stop_surplus_watts=1350.0,
                auto_min_soc=40.0,
                auto_resume_soc=50.0,
                auto_start_delay_seconds=10.0,
                auto_stop_delay_seconds=30.0,
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
                auto_learn_charge_power_enabled=False,
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
                _runtime_overrides_serialized=None,
                _runtime_overrides_active=False,
                _runtime_overrides_values={},
            )
            controller = ServiceStateController(service, self._normalize_mode)

            controller.save_runtime_overrides()

            with open(overrides_path, "r", encoding="utf-8") as handle:
                payload = handle.read()

            self.assertIn("[RuntimeOverrides]", payload)
            self.assertIn("Mode = 1", payload)
            self.assertIn("PhaseSelection = P1_P2", payload)
            self.assertIn("AutoStartSurplusWatts = 1850.0", payload)
            self.assertIn("AutoScheduledEnabledDays = Mon,Tue,Wed,Thu,Fri", payload)
            self.assertIn("AutoScheduledNightStartDelaySeconds = 3600.0", payload)
            self.assertIn("AutoScheduledLatestEndTime = 06:30", payload)
            self.assertIn("AutoScheduledNightCurrentAmps = 13.0", payload)
            self.assertIn("AutoDbusBackoffBaseSeconds = 5.0", payload)
            self.assertIn("AutoLearnChargePower = 0", payload)
            self.assertIn("AutoReferenceChargePowerWatts = 2100.0", payload)
            self.assertIn("AutoPhasePreferLowestWhenIdle = 0", payload)
            self.assertIn("AutoPhaseMismatchLockoutCount = 3", payload)
            self.assertTrue(service._runtime_overrides_active)
            self.assertEqual(service._runtime_overrides_values["AutoPhaseSwitching"], "1")

            with patch("shelly_wallbox.controllers.state.write_text_atomically") as write_mock:
                controller.save_runtime_overrides()

        write_mock.assert_not_called()

    def test_validate_runtime_config_clamps_invalid_values(self) -> None:
        service = make_state_validation_service(
            poll_interval_ms=0,
            sign_of_life_minutes=0,
            auto_pv_max_services=0,
            auto_pv_scan_interval_seconds=-1,
            auto_battery_scan_interval_seconds=-1,
            auto_dbus_backoff_base_seconds=-1,
            auto_dbus_backoff_max_seconds=-1,
            auto_grid_missing_stop_seconds=-1,
            auto_grid_recovery_start_seconds=-1,
            auto_average_window_seconds=-1,
            auto_min_runtime_seconds=-1,
            auto_min_offtime_seconds=-1,
            auto_reference_charge_power_watts=-1,
            auto_learn_charge_power_min_watts=-1,
            auto_learn_charge_power_alpha=-1,
            auto_learn_charge_power_start_delay_seconds=-1,
            auto_learn_charge_power_window_seconds=-1,
            auto_learn_charge_power_max_age_seconds=-1,
            auto_start_delay_seconds=-1,
            auto_stop_delay_seconds=-1,
            auto_stop_surplus_delay_seconds=-1,
            auto_stop_ewma_alpha=-1,
            auto_stop_ewma_alpha_stable=-1,
            auto_stop_ewma_alpha_volatile=-1,
            auto_stop_surplus_volatility_low_watts=-1,
            auto_stop_surplus_volatility_high_watts=-2,
            auto_input_cache_seconds=-1,
            auto_input_helper_restart_seconds=-1,
            auto_input_helper_stale_seconds=-1,
            auto_shelly_soft_fail_seconds=-1,
            auto_watchdog_stale_seconds=-1,
            auto_watchdog_recovery_seconds=-1,
            auto_startup_warmup_seconds=-1,
            auto_manual_override_seconds=-1,
            startup_device_info_retry_seconds=-1,
            startup_device_info_retries=-1,
            shelly_request_timeout_seconds=-1,
            dbus_method_timeout_seconds=-1,
            auto_min_soc=120,
            auto_resume_soc=-5,
            auto_start_surplus_watts=1500,
            auto_stop_surplus_watts=2400,
        )

        controller = ServiceStateController(service, self._normalize_mode)
        controller.validate_runtime_config()

        self.assertEqual(service.poll_interval_ms, 100)
        self.assertEqual(service.sign_of_life_minutes, 1)
        self.assertEqual(service.auto_pv_max_services, 1)
        self.assertEqual(service.auto_input_helper_restart_seconds, 0.0)
        self.assertEqual(service.auto_input_helper_stale_seconds, 0.0)
        self.assertEqual(service.auto_stop_ewma_alpha, 0.35)
        self.assertEqual(service.auto_stop_ewma_alpha_stable, 0.55)
        self.assertEqual(service.auto_stop_ewma_alpha_volatile, 0.15)
        self.assertEqual(service.auto_reference_charge_power_watts, 1900.0)
        self.assertEqual(service.auto_learn_charge_power_min_watts, 0.0)
        self.assertEqual(service.auto_learn_charge_power_alpha, 0.2)
        self.assertEqual(service.auto_learn_charge_power_start_delay_seconds, 0.0)
        self.assertEqual(service.auto_learn_charge_power_window_seconds, 0.0)
        self.assertEqual(service.auto_learn_charge_power_max_age_seconds, 0.0)
        self.assertEqual(service.auto_stop_surplus_volatility_low_watts, 0.0)
        self.assertEqual(service.auto_stop_surplus_volatility_high_watts, 0.0)
        self.assertEqual(service.shelly_request_timeout_seconds, 2.0)
        self.assertEqual(service.dbus_method_timeout_seconds, 1.0)
        self.assertEqual(service.auto_min_soc, 100.0)
        self.assertEqual(service.auto_resume_soc, 100.0)
        self.assertEqual(service.auto_stop_surplus_watts, 1500)

    def test_validate_runtime_config_keeps_valid_values_and_clamps_audit_settings(self) -> None:
        service = make_state_validation_service(
            auto_grid_recovery_start_seconds=1.0,
            auto_stop_surplus_delay_seconds=1.0,
            auto_stop_ewma_alpha=1.0,
            auto_stop_ewma_alpha_stable=0.6,
            auto_stop_ewma_alpha_volatile=0.2,
            auto_stop_surplus_volatility_low_watts=120.0,
            auto_stop_surplus_volatility_high_watts=300.0,
            auto_audit_log_max_age_hours=0.0,
            auto_audit_log_repeat_seconds=0.0,
            auto_min_soc=30,
            auto_resume_soc=40,
            auto_high_soc_threshold=120,
            auto_high_soc_release_threshold=140,
            auto_start_surplus_watts=1500,
            auto_stop_surplus_watts=1100,
            auto_high_soc_start_surplus_watts=1650,
            auto_high_soc_stop_surplus_watts=2400,
        )

        controller = ServiceStateController(service, self._normalize_mode)
        controller.validate_runtime_config()

        self.assertEqual(service.auto_audit_log_max_age_hours, 168.0)
        self.assertEqual(service.auto_audit_log_repeat_seconds, 30.0)
        self.assertEqual(service.auto_resume_soc, 40)
        self.assertEqual(service.auto_high_soc_threshold, 100.0)
        self.assertEqual(service.auto_high_soc_release_threshold, 100.0)
        self.assertEqual(service.auto_stop_surplus_watts, 1100)
        self.assertEqual(service.auto_high_soc_stop_surplus_watts, 1650)

    def test_validate_runtime_config_clamps_structured_auto_policy_and_syncs_attrs(self) -> None:
        service = make_state_validation_service(
            auto_policy=AutoPolicy(
                normal_profile=AutoThresholdProfile(1850.0, 2400.0),
                high_soc_profile=AutoThresholdProfile(1650.0, 1800.0),
                high_soc_threshold=55.0,
                high_soc_release_threshold=60.0,
                min_soc=35.0,
                resume_soc=30.0,
                start_max_grid_import_watts=50.0,
                stop_grid_import_watts=300.0,
                grid_recovery_start_seconds=-1.0,
                stop_surplus_delay_seconds=-1.0,
                ewma=AutoStopEwmaPolicy(
                    base_alpha=-1.0,
                    stable_alpha=-1.0,
                    volatile_alpha=-1.0,
                    volatility_low_watts=200.0,
                    volatility_high_watts=100.0,
                ),
                learn_charge_power=AutoLearnChargePowerPolicy(
                    enabled=True,
                    reference_power_watts=-1.0,
                    min_watts=-1.0,
                    alpha=-1.0,
                    start_delay_seconds=-1.0,
                    window_seconds=-1.0,
                    max_age_seconds=-1.0,
                ),
            ),
        )

        controller = ServiceStateController(service, self._normalize_mode)
        controller.validate_runtime_config()

        self.assertEqual(service.auto_policy.normal_profile.stop_surplus_watts, 1850.0)
        self.assertEqual(service.auto_policy.high_soc_profile.stop_surplus_watts, 1650.0)
        self.assertEqual(service.auto_policy.high_soc_release_threshold, 55.0)
        self.assertEqual(service.auto_policy.resume_soc, 35.0)
        self.assertEqual(service.auto_policy.grid_recovery_start_seconds, 0.0)
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
        self.assertEqual(service.auto_start_surplus_watts, 1850.0)
        self.assertEqual(service.auto_high_soc_stop_surplus_watts, 1650.0)
        self.assertEqual(service.auto_reference_charge_power_watts, 1900.0)
        self.assertEqual(service.auto_learn_charge_power_min_watts, 0.0)
        self.assertEqual(service.auto_learn_charge_power_alpha, 0.2)
        self.assertEqual(service.auto_learn_charge_power_start_delay_seconds, 0.0)
        self.assertEqual(service.auto_learn_charge_power_window_seconds, 0.0)
        self.assertEqual(service.auto_learn_charge_power_max_age_seconds, 0.0)

    def test_validate_runtime_config_legacy_auto_thresholds_clamp_release_and_volatility_order(self) -> None:
        service = make_state_validation_service(
            auto_min_soc=35.0,
            auto_resume_soc=40.0,
            auto_high_soc_threshold=55.0,
            auto_high_soc_release_threshold=60.0,
            auto_start_surplus_watts=1850.0,
            auto_stop_surplus_watts=1350.0,
            auto_high_soc_start_surplus_watts=1650.0,
            auto_high_soc_stop_surplus_watts=800.0,
            auto_stop_surplus_volatility_low_watts=200.0,
            auto_stop_surplus_volatility_high_watts=100.0,
        )

        controller = ServiceStateController(service, self._normalize_mode)
        controller.validate_runtime_config()

        self.assertEqual(service.auto_high_soc_release_threshold, 55.0)
        self.assertEqual(service.auto_stop_surplus_volatility_high_watts, 200.0)

    def test_runtime_state_roundtrip_restores_ram_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/state.json"
            service = SimpleNamespace(
                runtime_state_path=path,
                virtual_mode=1,
                virtual_autostart=0,
                virtual_enable=1,
                virtual_startstop=0,
                manual_override_until=123.5,
                _auto_mode_cutover_pending=True,
                _ignore_min_offtime_once=True,
                learned_charge_power_watts=1980.0,
                learned_charge_power_updated_at=113.0,
                learned_charge_power_state="stable",
                learned_charge_power_learning_since=None,
                learned_charge_power_sample_count=4,
                learned_charge_power_phase="L1",
                learned_charge_power_voltage=229.4,
                learned_charge_power_signature_mismatch_sessions=1,
                learned_charge_power_signature_checked_session_started_at=80.0,
                supported_phase_selections=("P1", "P1_P2", "P1_P2_P3"),
                requested_phase_selection="P1_P2",
                active_phase_selection="P1_P2",
                _phase_switch_pending_selection="P1_P2_P3",
                _phase_switch_state="stabilizing",
                _phase_switch_requested_at=118.0,
                _phase_switch_stable_until=130.0,
                _phase_switch_resume_relay=True,
                _phase_switch_mismatch_counts={"P1_P2_P3": 2},
                _phase_switch_last_mismatch_selection="P1_P2_P3",
                _phase_switch_last_mismatch_at=119.0,
                _phase_switch_lockout_selection="P1_P2_P3",
                _phase_switch_lockout_reason="mismatch-threshold",
                _phase_switch_lockout_at=120.0,
                _phase_switch_lockout_until=180.0,
                _contactor_fault_counts={"contactor-suspected-open": 3},
                _contactor_fault_active_reason="contactor-suspected-open",
                _contactor_fault_active_since=121.0,
                _contactor_lockout_reason="contactor-suspected-open",
                _contactor_lockout_source="count-threshold",
                _contactor_lockout_at=122.0,
                relay_last_changed_at=111.0,
                relay_last_off_at=112.0,
                _runtime_state_serialized=None,
                _last_health_reason="init",
            )
            controller = ServiceStateController(service, self._normalize_mode)

            controller.save_runtime_state()

            with open(path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)

            self.assertEqual(saved["mode"], 1)
            self.assertEqual(saved["autostart"], 0)
            self.assertEqual(saved["auto_mode_cutover_pending"], 1)
            self.assertNotIn("ignore_min_offtime_once", saved)
            self.assertEqual(saved["learned_charge_power_watts"], 1980.0)
            self.assertEqual(saved["learned_charge_power_updated_at"], 113.0)
            self.assertEqual(saved["learned_charge_power_state"], "stable")
            self.assertEqual(saved["learned_charge_power_sample_count"], 4)
            self.assertEqual(saved["learned_charge_power_phase"], "L1")
            self.assertEqual(saved["learned_charge_power_voltage"], 229.4)
            self.assertEqual(saved["learned_charge_power_signature_mismatch_sessions"], 1)
            self.assertEqual(saved["learned_charge_power_signature_checked_session_started_at"], 80.0)
            self.assertEqual(saved["supported_phase_selections"], ["P1", "P1_P2", "P1_P2_P3"])
            self.assertEqual(saved["requested_phase_selection"], "P1_P2")
            self.assertEqual(saved["active_phase_selection"], "P1_P2")
            self.assertEqual(saved["phase_switch_pending_selection"], "P1_P2_P3")
            self.assertEqual(saved["phase_switch_state"], "stabilizing")
            self.assertEqual(saved["phase_switch_requested_at"], 118.0)
            self.assertEqual(saved["phase_switch_stable_until"], 130.0)
            self.assertEqual(saved["phase_switch_resume_relay"], 1)
            self.assertEqual(saved["phase_switch_mismatch_counts"], {"P1_P2_P3": 2})
            self.assertEqual(saved["phase_switch_last_mismatch_selection"], "P1_P2_P3")
            self.assertEqual(saved["phase_switch_last_mismatch_at"], 119.0)
            self.assertEqual(saved["phase_switch_lockout_selection"], "P1_P2_P3")
            self.assertEqual(saved["phase_switch_lockout_reason"], "mismatch-threshold")
            self.assertEqual(saved["phase_switch_lockout_at"], 120.0)
            self.assertEqual(saved["phase_switch_lockout_until"], 180.0)
            self.assertEqual(saved["contactor_fault_counts"], {"contactor-suspected-open": 3})
            self.assertEqual(saved["contactor_fault_active_reason"], "contactor-suspected-open")
            self.assertEqual(saved["contactor_fault_active_since"], 121.0)
            self.assertEqual(saved["contactor_lockout_reason"], "contactor-suspected-open")
            self.assertEqual(saved["contactor_lockout_source"], "count-threshold")
            self.assertEqual(saved["contactor_lockout_at"], 122.0)

            service.virtual_mode = 0
            service.virtual_autostart = 1
            service.virtual_enable = 0
            service.virtual_startstop = 1
            service.manual_override_until = 0.0
            service._auto_mode_cutover_pending = False
            service._ignore_min_offtime_once = False
            service.learned_charge_power_watts = None
            service.learned_charge_power_updated_at = None
            service.learned_charge_power_state = "unknown"
            service.learned_charge_power_learning_since = None
            service.learned_charge_power_sample_count = 0
            service.learned_charge_power_phase = None
            service.learned_charge_power_voltage = None
            service.learned_charge_power_signature_mismatch_sessions = 0
            service.learned_charge_power_signature_checked_session_started_at = None
            service.supported_phase_selections = ("P1",)
            service.requested_phase_selection = "P1"
            service.active_phase_selection = "P1"
            service._phase_switch_pending_selection = None
            service._phase_switch_state = None
            service._phase_switch_requested_at = None
            service._phase_switch_stable_until = None
            service._phase_switch_resume_relay = False
            service._phase_switch_mismatch_counts = {}
            service._phase_switch_last_mismatch_selection = None
            service._phase_switch_last_mismatch_at = None
            service._phase_switch_lockout_selection = None
            service._phase_switch_lockout_reason = ""
            service._phase_switch_lockout_at = None
            service._phase_switch_lockout_until = None
            service._contactor_fault_counts = {}
            service._contactor_fault_active_reason = None
            service._contactor_fault_active_since = None
            service._contactor_lockout_reason = ""
            service._contactor_lockout_source = ""
            service._contactor_lockout_at = None
            service.relay_last_changed_at = None
            service.relay_last_off_at = None

            controller.load_runtime_state()

            self.assertEqual(service.virtual_mode, 1)
            self.assertEqual(service.virtual_autostart, 0)
            self.assertEqual(service.virtual_enable, 1)
            self.assertEqual(service.virtual_startstop, 0)
            self.assertEqual(service.manual_override_until, 123.5)
            self.assertTrue(service._auto_mode_cutover_pending)
            self.assertFalse(service._ignore_min_offtime_once)
            self.assertEqual(service.learned_charge_power_watts, 1980.0)
            self.assertEqual(service.learned_charge_power_updated_at, 113.0)
            self.assertEqual(service.learned_charge_power_state, "stable")
            self.assertIsNone(service.learned_charge_power_learning_since)
            self.assertEqual(service.learned_charge_power_sample_count, 4)
            self.assertEqual(service.learned_charge_power_phase, "L1")
            self.assertEqual(service.learned_charge_power_voltage, 229.4)
            self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 1)
            self.assertEqual(service.learned_charge_power_signature_checked_session_started_at, 80.0)
            self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2", "P1_P2_P3"))
            self.assertEqual(service.requested_phase_selection, "P1_P2")
            self.assertEqual(service.active_phase_selection, "P1_P2")
            self.assertEqual(service._phase_switch_pending_selection, "P1_P2_P3")
            self.assertEqual(service._phase_switch_state, "stabilizing")
            self.assertEqual(service._phase_switch_requested_at, 118.0)
            self.assertEqual(service._phase_switch_stable_until, 130.0)
            self.assertTrue(service._phase_switch_resume_relay)
            self.assertEqual(service._phase_switch_mismatch_counts, {"P1_P2_P3": 2})
            self.assertEqual(service._phase_switch_last_mismatch_selection, "P1_P2_P3")
            self.assertEqual(service._phase_switch_last_mismatch_at, 119.0)
            self.assertEqual(service._phase_switch_lockout_selection, "P1_P2_P3")
            self.assertEqual(service._phase_switch_lockout_reason, "mismatch-threshold")
            self.assertEqual(service._phase_switch_lockout_at, 120.0)
            self.assertEqual(service._phase_switch_lockout_until, 180.0)
            self.assertEqual(service._contactor_fault_counts, {"contactor-suspected-open": 3})
            self.assertEqual(service._contactor_fault_active_reason, "contactor-suspected-open")
            self.assertEqual(service._contactor_fault_active_since, 121.0)
            self.assertEqual(service._contactor_lockout_reason, "contactor-suspected-open")
            self.assertEqual(service._contactor_lockout_source, "count-threshold")
            self.assertEqual(service._contactor_lockout_at, 122.0)
            self.assertEqual(service.relay_last_changed_at, 111.0)
            self.assertEqual(service.relay_last_off_at, 112.0)

    def test_load_runtime_state_normalizes_unknown_learned_power_state_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/state.json"
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "mode": 1,
                        "autostart": 1,
                        "enable": 1,
                        "startstop": 0,
                        "manual_override_until": 0.0,
                        "auto_mode_cutover_pending": 0,
                        "ignore_min_offtime_once": 0,
                        "learned_charge_power_watts": 1980.0,
                        "learned_charge_power_updated_at": 113.0,
                        "learned_charge_power_state": "nonsense",
                        "learned_charge_power_sample_count": 2,
                        "learned_charge_power_phase": "weird",
                        "supported_phase_selections": ["L2", "3P"],
                        "requested_phase_selection": "3P",
                        "active_phase_selection": "L2",
                    },
                    handle,
                )

            service = make_runtime_state_service(runtime_state_path=path)
            controller = ServiceStateController(service, self._normalize_mode)
            controller.load_runtime_state()

            self.assertFalse(service._ignore_min_offtime_once)
            self.assertEqual(service.learned_charge_power_state, "unknown")
            self.assertEqual(service.learned_charge_power_sample_count, 2)
            self.assertIsNone(service.learned_charge_power_phase)
            self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2_P3"))
            self.assertEqual(service.requested_phase_selection, "P1_P2_P3")
            self.assertEqual(service.active_phase_selection, "P1")

    def test_load_runtime_state_discards_future_historical_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/state.json"
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "mode": 1,
                        "autostart": 1,
                        "enable": 1,
                        "startstop": 0,
                        "manual_override_until": 150.0,
                        "auto_mode_cutover_pending": 0,
                        "learned_charge_power_watts": 1980.0,
                        "learned_charge_power_updated_at": 120.0,
                        "learned_charge_power_learning_since": 121.0,
                        "learned_charge_power_signature_checked_session_started_at": 122.0,
                        "relay_last_changed_at": 123.0,
                        "relay_last_off_at": 124.0,
                    },
                    handle,
                )

            service = make_runtime_state_service(runtime_state_path=path)
            service._time_now = lambda: 100.0
            controller = ServiceStateController(service, self._normalize_mode)
            controller.load_runtime_state()

            self.assertEqual(service.manual_override_until, 150.0)
            self.assertIsNone(service.learned_charge_power_updated_at)
            self.assertIsNone(service.learned_charge_power_learning_since)
            self.assertIsNone(service.learned_charge_power_signature_checked_session_started_at)
            self.assertIsNone(service.relay_last_changed_at)
            self.assertIsNone(service.relay_last_off_at)

    def test_load_runtime_state_discards_negative_learned_power_and_voltage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/state.json"
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "mode": 1,
                        "autostart": 1,
                        "enable": 1,
                        "startstop": 0,
                        "learned_charge_power_watts": -1.0,
                        "learned_charge_power_updated_at": 100.0,
                        "learned_charge_power_state": "stable",
                        "learned_charge_power_voltage": -230.0,
                        "learned_charge_power_sample_count": -4,
                        "learned_charge_power_signature_mismatch_sessions": -2,
                    },
                    handle,
                )

            service = make_runtime_state_service(runtime_state_path=path)
            service._time_now = lambda: 100.0
            controller = ServiceStateController(service, self._normalize_mode)
            controller.load_runtime_state()

            self.assertIsNone(service.learned_charge_power_watts)
            self.assertIsNone(service.learned_charge_power_voltage)
            self.assertEqual(service.learned_charge_power_sample_count, 0)
            self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 0)

    def test_save_runtime_state_skips_empty_path_deduplicates_and_warns_on_failure(self) -> None:
        service = make_runtime_state_service()
        controller = ServiceStateController(service, self._normalize_mode)
        controller.save_runtime_state()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "state.json")
            service.runtime_state_path = path
            controller.save_runtime_state()
            first_serialized = service._runtime_state_serialized
            controller.save_runtime_state()
            self.assertEqual(service._runtime_state_serialized, first_serialized)

            with patch("shelly_wallbox.controllers.state.write_text_atomically", side_effect=RuntimeError("boom")):
                service._runtime_state_serialized = None
                with patch("shelly_wallbox.controllers.state.logging.warning") as warning_mock:
                    controller.save_runtime_state()
                warning_mock.assert_called_once()

    def test_load_runtime_state_handles_missing_path_invalid_json_and_load_config_errors(self) -> None:
        service = make_runtime_state_service(
            virtual_mode=0,
            virtual_enable=0,
            virtual_startstop=1,
        )
        controller = ServiceStateController(service, self._normalize_mode)
        controller.load_runtime_state()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "state.json")
            service.runtime_state_path = path
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{bad json")
            with patch("shelly_wallbox.controllers.state.logging.warning") as warning_mock:
                controller.load_runtime_state()
            warning_mock.assert_called_once()

        parser = tempfile.TemporaryDirectory()
        self.addCleanup(parser.cleanup)
        missing_config_controller = ServiceStateController(service, self._normalize_mode)
        with patch.object(ServiceStateController, "config_path", return_value=os.path.join(parser.name, "missing.ini")):
            with self.assertRaises(ValueError):
                missing_config_controller.load_config()
