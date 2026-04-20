# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_state_controller_support import *


class TestServiceStateControllerTertiary(ServiceStateControllerTestBase):
    def test_load_config_runtime_overrides_do_not_change_backend_selection_space(self) -> None:
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
                    "[Backends]\n"
                    "Mode=split\n"
                    "MeterType=shelly_meter\n"
                    "SwitchType=shelly_switch\n"
                    "ChargerType=simpleevse_charger\n"
                    "MeterConfigPath=/data/meter.ini\n"
                    "SwitchConfigPath=/data/switch.ini\n"
                    "ChargerConfigPath=/data/charger.ini\n"
                )
            with open(overrides_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "[RuntimeOverrides]\n"
                    "MeterType = none\n"
                    "SwitchType = none\n"
                    "ChargerType = goe_charger\n"
                    "Mode = 2\n"
                    "AutoScheduledEnabledDays = Sat,Sun\n"
                )

            with patch.object(ServiceStateController, "config_path", return_value=config_path):
                config = controller.load_config()

        self.assertEqual(config["Backends"]["Mode"], "split")
        self.assertEqual(config["Backends"]["MeterType"], "shelly_meter")
        self.assertEqual(config["Backends"]["SwitchType"], "shelly_switch")
        self.assertEqual(config["Backends"]["ChargerType"], "simpleevse_charger")
        self.assertEqual(config["DEFAULT"]["Mode"], "2")
        self.assertEqual(config["DEFAULT"]["AutoScheduledEnabledDays"], "Sat,Sun")
        self.assertNotIn("MeterType", service._runtime_overrides_values)
        self.assertNotIn("SwitchType", service._runtime_overrides_values)
        self.assertNotIn("ChargerType", service._runtime_overrides_values)

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
