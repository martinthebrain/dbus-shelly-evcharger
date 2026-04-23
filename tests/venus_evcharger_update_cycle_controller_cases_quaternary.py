# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class TestUpdateCycleControllerQuaternary(UpdateCycleControllerTestBase):
    def test_victron_ess_balance_bias_applies_pid_output_and_tracks_setpoint(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
            auto_battery_discharge_balance_victron_bias_service="com.victronenergy.settings",
            auto_battery_discharge_balance_victron_bias_path="/Settings/CGwacs/AcPowerSetPoint",
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_support_mode="allow_experimental",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.0,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=250.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=0.0,
            auto_battery_discharge_balance_victron_bias_min_update_seconds=0.0,
            auto_battery_discharge_balance_victron_bias_require_clean_phases=False,
            dbus_method_timeout_seconds=1.0,
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _last_auto_metrics={},
            _auto_cached_inputs_used=False,
            _last_energy_cluster={
                "battery_combined_grid_interaction_w": -300.0,
                "battery_discharge_balance_eligible_source_count": 2,
                "battery_sources": [
                    {
                        "source_id": "victron",
                        "online": True,
                        "discharge_balance_error_w": -500.0,
                        "discharge_balance_control_connector_type": "dbus",
                        "discharge_balance_control_support": "supported",
                    },
                    {
                        "source_id": "hybrid",
                        "online": True,
                    },
                ],
            },
            _victron_ess_balance_last_write_at=None,
            _victron_ess_balance_last_setpoint_w=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller, "_victron_ess_balance_write_setpoint", return_value=True) as write_mock:
            controller.apply_victron_ess_balance_bias(service, 100.0, True)

        write_mock.assert_called_once_with(
            service,
            "com.victronenergy.settings",
            "/Settings/CGwacs/AcPowerSetPoint",
            -50.0,
        )
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_active"], 1)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_source_id"], "victron")
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_source_error_w"], -500.0)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_pid_output_w"], -100.0)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_setpoint_w"], -50.0)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_reason"], "applied")

    def test_victron_ess_balance_bias_restores_base_setpoint_when_auto_mode_stops(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
            auto_battery_discharge_balance_victron_bias_service="com.victronenergy.settings",
            auto_battery_discharge_balance_victron_bias_path="/Settings/CGwacs/AcPowerSetPoint",
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_support_mode="allow_experimental",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.0,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=250.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=0.0,
            auto_battery_discharge_balance_victron_bias_min_update_seconds=0.0,
            auto_battery_discharge_balance_victron_bias_require_clean_phases=False,
            dbus_method_timeout_seconds=1.0,
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _last_auto_metrics={},
            _last_energy_cluster={},
            _victron_ess_balance_last_write_at=None,
            _victron_ess_balance_last_setpoint_w=-50.0,
            _victron_ess_balance_pid_last_error_w=-500.0,
            _victron_ess_balance_pid_last_at=90.0,
            _victron_ess_balance_pid_integral_output_w=-25.0,
            _victron_ess_balance_pid_last_output_w=-100.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller, "_victron_ess_balance_write_setpoint", return_value=True) as write_mock:
            controller.apply_victron_ess_balance_bias(service, 100.0, False)

        write_mock.assert_called_once_with(
            service,
            "com.victronenergy.settings",
            "/Settings/CGwacs/AcPowerSetPoint",
            50.0,
        )
        self.assertIsNone(service._victron_ess_balance_last_setpoint_w)
        self.assertEqual(service._victron_ess_balance_pid_last_output_w, 0.0)
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_reason"],
            "auto-mode-inactive-restored",
        )

    def test_victron_ess_balance_bias_support_mode_blocks_experimental_when_requested(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
            auto_battery_discharge_balance_victron_bias_service="com.victronenergy.settings",
            auto_battery_discharge_balance_victron_bias_path="/Settings/CGwacs/AcPowerSetPoint",
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_support_mode="supported_only",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.0,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=250.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=0.0,
            auto_battery_discharge_balance_victron_bias_min_update_seconds=0.0,
            dbus_method_timeout_seconds=1.0,
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _last_auto_metrics={},
            _auto_cached_inputs_used=False,
            _last_energy_cluster={
                "battery_combined_grid_interaction_w": -300.0,
                "battery_discharge_balance_eligible_source_count": 2,
                "battery_sources": [
                    {
                        "source_id": "victron",
                        "online": True,
                        "discharge_balance_error_w": -400.0,
                        "discharge_balance_control_connector_type": "dbus",
                        "discharge_balance_control_support": "experimental",
                    },
                    {"source_id": "hybrid", "online": True},
                ],
            },
            _victron_ess_balance_last_write_at=None,
            _victron_ess_balance_last_setpoint_w=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller, "_victron_ess_balance_write_setpoint", return_value=True) as write_mock:
            controller.apply_victron_ess_balance_bias(service, 100.0, True)
            self.assertEqual(
                service._last_auto_metrics["battery_discharge_balance_victron_bias_reason"],
                "victron-source-support-blocked",
            )
            write_mock.assert_not_called()
            service.auto_battery_discharge_balance_victron_bias_support_mode = "allow_experimental"
            controller.apply_victron_ess_balance_bias(service, 101.0, True)

        write_mock.assert_called_once_with(
            service,
            "com.victronenergy.settings",
            "/Settings/CGwacs/AcPowerSetPoint",
            -30.0,
        )
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_reason"], "applied")

    def test_victron_ess_balance_bias_learns_response_delay_and_gain(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
            auto_battery_discharge_balance_victron_bias_service="com.victronenergy.settings",
            auto_battery_discharge_balance_victron_bias_path="/Settings/CGwacs/AcPowerSetPoint",
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_support_mode="allow_experimental",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.0,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=250.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=0.0,
            auto_battery_discharge_balance_victron_bias_min_update_seconds=0.0,
            auto_battery_discharge_balance_victron_bias_require_clean_phases=False,
            dbus_method_timeout_seconds=1.0,
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _last_auto_metrics={},
            _last_energy_cluster={
                "battery_combined_grid_interaction_w": -300.0,
                "battery_discharge_balance_eligible_source_count": 2,
                "battery_sources": [
                    {
                        "source_id": "victron",
                        "online": True,
                        "discharge_balance_error_w": -500.0,
                        "discharge_balance_control_connector_type": "dbus",
                        "discharge_balance_control_support": "supported",
                    },
                    {"source_id": "hybrid", "online": True},
                ],
            },
            _victron_ess_balance_last_write_at=None,
            _victron_ess_balance_last_setpoint_w=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller, "_victron_ess_balance_write_setpoint", return_value=True):
            controller.apply_victron_ess_balance_bias(service, 100.0, True)
            service._last_energy_cluster["battery_sources"][0]["discharge_balance_error_w"] = -200.0
            controller.apply_victron_ess_balance_bias(service, 104.0, True)

        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_response_delay_seconds"],
            4.0,
        )
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_estimated_gain"], 3.0)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_overshoot_active"], 0)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_overshoot_count"], 0)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_settling_active"], 1)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_settled_count"], 0)
        self.assertIsNotNone(service._last_auto_metrics["battery_discharge_balance_victron_bias_stability_score"])

    def test_victron_ess_balance_bias_detects_overshoot(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
            auto_battery_discharge_balance_victron_bias_service="com.victronenergy.settings",
            auto_battery_discharge_balance_victron_bias_path="/Settings/CGwacs/AcPowerSetPoint",
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_support_mode="allow_experimental",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.0,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=250.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=0.0,
            auto_battery_discharge_balance_victron_bias_min_update_seconds=0.0,
            auto_battery_discharge_balance_victron_bias_require_clean_phases=False,
            dbus_method_timeout_seconds=1.0,
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _last_auto_metrics={},
            _last_energy_cluster={
                "battery_combined_grid_interaction_w": -300.0,
                "battery_discharge_balance_eligible_source_count": 2,
                "battery_sources": [
                    {
                        "source_id": "victron",
                        "online": True,
                        "discharge_balance_error_w": -500.0,
                        "discharge_balance_control_connector_type": "dbus",
                        "discharge_balance_control_support": "supported",
                    },
                    {"source_id": "hybrid", "online": True},
                ],
            },
            _victron_ess_balance_last_write_at=None,
            _victron_ess_balance_last_setpoint_w=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller, "_victron_ess_balance_write_setpoint", return_value=True):
            controller.apply_victron_ess_balance_bias(service, 100.0, True)
            service._last_energy_cluster["battery_sources"][0]["discharge_balance_error_w"] = 150.0
            controller.apply_victron_ess_balance_bias(service, 103.0, True)

        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_overshoot_active"], 1)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_overshoot_count"], 1)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_settling_active"], 0)
        self.assertLess(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_stability_score"],
            1.0,
        )

    def test_victron_ess_balance_bias_recommends_more_relaxed_tuning_when_stable(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.02,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=50.0,
            _victron_ess_balance_telemetry_response_delay_seconds=4.0,
            _victron_ess_balance_telemetry_estimated_gain=2.5,
            _victron_ess_balance_telemetry_stability_score=0.9,
            _victron_ess_balance_telemetry_overshoot_count=0,
            _victron_ess_balance_telemetry_settled_count=3,
            _victron_ess_balance_telemetry_delay_samples=2,
            _victron_ess_balance_telemetry_gain_samples=2,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        metrics = controller._victron_ess_balance_default_metrics()
        controller._populate_victron_ess_balance_telemetry_metrics(service, metrics)

        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_kp"], 0.23)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_ki"], 0.022)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_kd"], 0.0)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_deadband_watts"], 80.0)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_max_abs_watts"], 550.0)
        self.assertEqual(
            metrics["battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second"],
            55.0,
        )
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_activation_mode"], "always")
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommendation_reason"], "can_relax_conservatism")
        self.assertEqual(
            metrics["battery_discharge_balance_victron_bias_recommendation_ini_snippet"],
            "AutoBatteryDischargeBalanceVictronBiasKp=0.23\n"
            "AutoBatteryDischargeBalanceVictronBiasKi=0.022\n"
            "AutoBatteryDischargeBalanceVictronBiasKd=0\n"
            "AutoBatteryDischargeBalanceVictronBiasDeadbandWatts=80\n"
            "AutoBatteryDischargeBalanceVictronBiasMaxAbsWatts=550\n"
            "AutoBatteryDischargeBalanceVictronBiasRampRateWattsPerSecond=55\n"
            "AutoBatteryDischargeBalanceVictronBiasActivationMode=always",
        )
        self.assertEqual(
            metrics["battery_discharge_balance_victron_bias_recommendation_hint"],
            "Telemetry looks stable; you can cautiously relax the current "
            "Victron bias tuning (confidence 0.90).",
        )
        self.assertGreater(metrics["battery_discharge_balance_victron_bias_recommendation_confidence"], 0.6)

    def test_victron_ess_balance_bias_recommends_more_conservative_tuning_on_overshoot(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.02,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=50.0,
            _victron_ess_balance_telemetry_response_delay_seconds=3.0,
            _victron_ess_balance_telemetry_estimated_gain=2.0,
            _victron_ess_balance_telemetry_stability_score=0.4,
            _victron_ess_balance_telemetry_overshoot_count=1,
            _victron_ess_balance_telemetry_settled_count=0,
            _victron_ess_balance_telemetry_delay_samples=2,
            _victron_ess_balance_telemetry_gain_samples=2,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        metrics = controller._victron_ess_balance_default_metrics()
        controller._populate_victron_ess_balance_telemetry_metrics(service, metrics)

        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_kp"], 0.16)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_ki"], 0.01)
        self.assertEqual(
            metrics["battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second"],
            35.0,
        )
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommendation_reason"], "overshoot_risk")
        self.assertIn(
            "AutoBatteryDischargeBalanceVictronBiasKp=0.16",
            metrics["battery_discharge_balance_victron_bias_recommendation_ini_snippet"],
        )
        self.assertIn("overshoot risk", metrics["battery_discharge_balance_victron_bias_recommendation_hint"].lower())

    def test_victron_ess_balance_bias_skips_learning_when_telemetry_is_not_clean(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
            auto_battery_discharge_balance_victron_bias_service="com.victronenergy.settings",
            auto_battery_discharge_balance_victron_bias_path="/Settings/CGwacs/AcPowerSetPoint",
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_support_mode="allow_experimental",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.0,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=250.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=0.0,
            auto_battery_discharge_balance_victron_bias_min_update_seconds=0.0,
            auto_battery_discharge_balance_victron_bias_require_clean_phases=True,
            dbus_method_timeout_seconds=1.0,
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _last_auto_metrics={},
            _auto_cached_inputs_used=False,
            _last_energy_cluster={
                "battery_discharge_balance_eligible_source_count": 2,
                "battery_combined_grid_interaction_w": -300.0,
                "battery_sources": [
                    {
                        "source_id": "victron",
                        "online": True,
                        "discharge_balance_error_w": -500.0,
                        "discharge_balance_control_connector_type": "dbus",
                        "discharge_balance_control_support": "supported",
                    },
                    {"source_id": "hybrid", "online": True},
                ],
            },
            _victron_ess_balance_last_write_at=None,
            _victron_ess_balance_last_setpoint_w=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller, "_victron_ess_balance_write_setpoint", return_value=True):
            controller.apply_victron_ess_balance_bias(service, 100.0, True)
            service._auto_cached_inputs_used = True
            service._last_energy_cluster["battery_sources"][0]["discharge_balance_error_w"] = -250.0
            controller.apply_victron_ess_balance_bias(service, 104.0, True)

        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_telemetry_clean"], 0)
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_telemetry_clean_reason"],
            "cached_inputs",
        )
        self.assertIsNone(service._last_auto_metrics["battery_discharge_balance_victron_bias_response_delay_seconds"])

    def test_victron_ess_balance_bias_enters_oscillation_lockout_after_repeated_direction_changes(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
            auto_battery_discharge_balance_victron_bias_service="com.victronenergy.settings",
            auto_battery_discharge_balance_victron_bias_path="/Settings/CGwacs/AcPowerSetPoint",
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_support_mode="allow_experimental",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.0,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=250.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=0.0,
            auto_battery_discharge_balance_victron_bias_min_update_seconds=0.0,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled=True,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds=120.0,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes=2,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds=90.0,
            dbus_method_timeout_seconds=1.0,
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _last_auto_metrics={},
            _last_energy_cluster={
                "battery_discharge_balance_eligible_source_count": 2,
                "battery_combined_grid_interaction_w": -300.0,
                "battery_sources": [
                    {
                        "source_id": "victron",
                        "online": True,
                        "discharge_balance_error_w": -500.0,
                        "discharge_balance_control_connector_type": "dbus",
                        "discharge_balance_control_support": "supported",
                    },
                    {"source_id": "hybrid", "online": True},
                ],
            },
            _victron_ess_balance_last_write_at=None,
            _victron_ess_balance_last_setpoint_w=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller, "_victron_ess_balance_write_setpoint", return_value=True):
            controller.apply_victron_ess_balance_bias(service, 100.0, True)
            service._last_energy_cluster["battery_sources"][0]["discharge_balance_error_w"] = 400.0
            controller.apply_victron_ess_balance_bias(service, 101.0, True)
            service._last_energy_cluster["battery_sources"][0]["discharge_balance_error_w"] = -350.0
            controller.apply_victron_ess_balance_bias(service, 102.0, True)

        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_oscillation_lockout_active"],
            1,
        )
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_oscillation_lockout_reason"],
            "direction_change_oscillation",
        )
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_reason"],
            "overshoot-cooldown-active-restored",
        )

    def test_victron_ess_balance_bias_auto_apply_is_stepwise_and_holds_observation_window(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_auto_apply_enabled=True,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence=0.8,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples=2,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score=0.75,
            auto_battery_discharge_balance_victron_bias_auto_apply_blend=0.5,
            auto_battery_discharge_balance_victron_bias_observation_window_seconds=30.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=50.0,
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.02,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_activation_mode="always",
            auto_battery_discharge_balance_victron_bias_rollback_enabled=True,
            _victron_ess_balance_auto_apply_generation=0,
            _save_runtime_state=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        metrics = controller._victron_ess_balance_default_metrics()
        metrics.update(
            {
                "battery_discharge_balance_victron_bias_recommendation_confidence": 0.9,
                "battery_discharge_balance_victron_bias_learning_profile_stability_score": 0.9,
                "battery_discharge_balance_victron_bias_learning_profile_sample_count": 3,
                "battery_discharge_balance_victron_bias_recommendation_profile_key": "more_export:export:day:above_reserve_band",
                "battery_discharge_balance_victron_bias_learning_profile_key": "more_export:export:day:above_reserve_band",
                "battery_discharge_balance_victron_bias_recommended_deadband_watts": 80.0,
                "battery_discharge_balance_victron_bias_recommended_max_abs_watts": 600.0,
                "battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second": 60.0,
                "battery_discharge_balance_victron_bias_recommended_kp": 0.25,
                "battery_discharge_balance_victron_bias_recommended_ki": 0.03,
                "battery_discharge_balance_victron_bias_recommended_kd": 0.01,
                "battery_discharge_balance_victron_bias_recommended_activation_mode": "export_only",
            }
        )

        controller._maybe_auto_apply_victron_ess_balance_recommendation(service, metrics, 100.0)
        first_deadband = service.auto_battery_discharge_balance_victron_bias_deadband_watts

        self.assertEqual(first_deadband, 90.0)
        self.assertEqual(
            metrics["battery_discharge_balance_victron_bias_auto_apply_last_param"],
            "auto_battery_discharge_balance_victron_bias_deadband_watts",
        )
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_auto_apply_reason"], "applied_step")

        metrics["battery_discharge_balance_victron_bias_auto_apply_reason"] = ""
        controller._maybe_auto_apply_victron_ess_balance_recommendation(service, metrics, 110.0)

        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_deadband_watts, first_deadband)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_auto_apply_reason"], "observation_window_active")

    def test_victron_ess_balance_bias_rolls_back_to_last_stable_tuning_when_observation_turns_unstable(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_auto_apply_enabled=True,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence=0.8,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples=2,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score=0.75,
            auto_battery_discharge_balance_victron_bias_auto_apply_blend=0.5,
            auto_battery_discharge_balance_victron_bias_deadband_watts=90.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=550.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=55.0,
            auto_battery_discharge_balance_victron_bias_kp=0.23,
            auto_battery_discharge_balance_victron_bias_ki=0.022,
            auto_battery_discharge_balance_victron_bias_kd=0.01,
            auto_battery_discharge_balance_victron_bias_activation_mode="export_only",
            auto_battery_discharge_balance_victron_bias_rollback_enabled=True,
            auto_battery_discharge_balance_victron_bias_rollback_min_stability_score=0.45,
            _victron_ess_balance_auto_apply_observe_until=120.0,
            _victron_ess_balance_last_stable_tuning={
                "kp": 0.2,
                "ki": 0.02,
                "kd": 0.0,
                "deadband_watts": 100.0,
                "max_abs_watts": 500.0,
                "ramp_rate_watts_per_second": 50.0,
                "activation_mode": "always",
            },
            _victron_ess_balance_last_stable_profile_key="more_export:export:day:above_reserve_band",
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        metrics = controller._victron_ess_balance_default_metrics()
        metrics.update(
            {
                "battery_discharge_balance_victron_bias_overshoot_active": 1,
                "battery_discharge_balance_victron_bias_stability_score": 0.3,
            }
        )

        controller._maybe_auto_apply_victron_ess_balance_recommendation(service, metrics, 110.0)

        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_kp, 0.2)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_deadband_watts, 100.0)
        self.assertEqual(service.auto_battery_discharge_balance_victron_bias_activation_mode, "always")
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_rollback_active"], 1)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_rollback_reason"], "unstable_observation_window")

    def test_victron_ess_balance_bias_learns_profiled_telemetry_for_export_day_above_reserve(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
            auto_battery_discharge_balance_victron_bias_service="com.victronenergy.settings",
            auto_battery_discharge_balance_victron_bias_path="/Settings/CGwacs/AcPowerSetPoint",
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_support_mode="allow_experimental",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.0,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=250.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=0.0,
            auto_battery_discharge_balance_victron_bias_min_update_seconds=0.0,
            _last_auto_metrics={},
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            dbus_method_timeout_seconds=1.0,
            _get_system_bus=MagicMock(),
            _last_energy_cluster={
                "battery_combined_grid_interaction_w": -450.0,
                "battery_combined_pv_input_power_w": 1800.0,
                "expected_near_term_export_w": 600.0,
                "expected_near_term_import_w": 0.0,
                "battery_discharge_balance_eligible_source_count": 2,
                "battery_sources": [
                    {
                        "source_id": "victron",
                        "online": True,
                        "soc": 62.0,
                        "discharge_balance_reserve_floor_soc": 35.0,
                        "discharge_balance_error_w": -500.0,
                        "discharge_balance_control_connector_type": "dbus",
                        "discharge_balance_control_support": "supported",
                    },
                    {"source_id": "hybrid", "online": True},
                ],
            },
            _victron_ess_balance_last_write_at=None,
            _victron_ess_balance_last_setpoint_w=None,
            _victron_ess_balance_learning_profiles={},
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller, "_victron_ess_balance_write_setpoint", return_value=True):
            controller.apply_victron_ess_balance_bias(service, 100.0, True)
            service._last_energy_cluster["battery_sources"][0]["discharge_balance_error_w"] = -320.0
            controller.apply_victron_ess_balance_bias(service, 104.0, True)

        profile = service._victron_ess_balance_learning_profiles[
            "more_export:export:day:above_reserve_band:ev_idle:pv_strong:mid_band"
        ]
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_learning_profile_key"],
            "more_export:export:day:above_reserve_band:ev_idle:pv_strong:mid_band",
        )
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_learning_profile_action_direction"],
            "more_export",
        )
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_learning_profile_site_regime"],
            "export",
        )
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_learning_profile_direction"], "export")
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_learning_profile_day_phase"], "day")
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_learning_profile_reserve_phase"],
            "above_reserve_band",
        )
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_learning_profile_ev_phase"],
            "ev_idle",
        )
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_learning_profile_pv_phase"],
            "pv_strong",
        )
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_learning_profile_battery_limit_phase"],
            "mid_band",
        )
        self.assertEqual(profile["delay_samples"], 1)
        self.assertEqual(profile["gain_samples"], 1)
        self.assertGreater(profile["response_delay_seconds"], 0.0)
        self.assertGreater(profile["estimated_gain"], 0.0)

    def test_victron_ess_balance_bias_enters_overshoot_cooldown_and_suspends_auto_apply(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
            auto_battery_discharge_balance_victron_bias_service="com.victronenergy.settings",
            auto_battery_discharge_balance_victron_bias_path="/Settings/CGwacs/AcPowerSetPoint",
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_support_mode="allow_experimental",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.05,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=250.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=0.0,
            auto_battery_discharge_balance_victron_bias_min_update_seconds=0.0,
            auto_battery_discharge_balance_victron_bias_require_clean_phases=False,
            auto_battery_discharge_balance_victron_bias_observation_window_seconds=30.0,
            dbus_method_timeout_seconds=1.0,
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _last_auto_metrics={},
            _last_energy_cluster={
                "battery_combined_grid_interaction_w": -300.0,
                "battery_discharge_balance_eligible_source_count": 2,
                "battery_sources": [
                    {
                        "source_id": "victron",
                        "online": True,
                        "discharge_balance_error_w": -500.0,
                        "discharge_balance_control_connector_type": "dbus",
                        "discharge_balance_control_support": "supported",
                    },
                    {"source_id": "hybrid", "online": True},
                ],
            },
            _victron_ess_balance_last_write_at=None,
            _victron_ess_balance_last_setpoint_w=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller, "_victron_ess_balance_write_setpoint", return_value=True):
            controller.apply_victron_ess_balance_bias(service, 100.0, True)
            service._last_energy_cluster["battery_sources"][0]["discharge_balance_error_w"] = 250.0
            controller.apply_victron_ess_balance_bias(service, 103.0, True)

        self.assertTrue(service._last_auto_metrics["battery_discharge_balance_victron_bias_overshoot_cooldown_active"])
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_overshoot_cooldown_reason"],
            "overshoot_detected",
        )
        self.assertEqual(service._victron_ess_balance_pid_integral_output_w, 0.0)
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_victron_bias_auto_apply_suspend_reason"],
            "overshoot_cooldown",
        )

    def test_victron_ess_balance_bias_rejects_learning_when_grid_or_ev_jumps(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
            auto_battery_discharge_balance_victron_bias_service="com.victronenergy.settings",
            auto_battery_discharge_balance_victron_bias_path="/Settings/CGwacs/AcPowerSetPoint",
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_support_mode="allow_experimental",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.0,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=250.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=0.0,
            auto_battery_discharge_balance_victron_bias_min_update_seconds=0.0,
            auto_battery_discharge_balance_victron_bias_require_clean_phases=True,
            dbus_method_timeout_seconds=1.0,
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _last_auto_metrics={},
            _charger_estimated_power_w=200.0,
            _victron_ess_balance_telemetry_last_grid_interaction_w=-200.0,
            _victron_ess_balance_telemetry_last_ac_power_w=400.0,
            _victron_ess_balance_telemetry_last_ev_power_w=200.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        clean, reason = controller._victron_ess_balance_telemetry_is_clean(
            service,
            {
                "battery_combined_grid_interaction_w": -900.0,
                "battery_combined_ac_power_w": 400.0,
            },
            -500.0,
        )
        self.assertFalse(clean)
        self.assertEqual(reason, "grid_unstable")

        service._charger_estimated_power_w = 800.0
        clean, reason = controller._victron_ess_balance_telemetry_is_clean(
            service,
            {
                "battery_combined_grid_interaction_w": -220.0,
                "battery_combined_ac_power_w": 400.0,
            },
            -500.0,
        )
        self.assertFalse(clean)
        self.assertEqual(reason, "ev_load_jump")

    def test_victron_ess_balance_bias_prefers_active_profile_for_recommendation(self) -> None:
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.02,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_activation_mode="always",
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=50.0,
            _victron_ess_balance_active_learning_profile_key="more_export:export:day:above_reserve_band",
            _victron_ess_balance_learning_profiles={
                "more_export:export:day:above_reserve_band": {
                    "key": "more_export:export:day:above_reserve_band",
                    "action_direction": "more_export",
                    "site_regime": "export",
                    "direction": "export",
                    "day_phase": "day",
                    "reserve_phase": "above_reserve_band",
                    "response_delay_seconds": 4.0,
                    "delay_samples": 2,
                    "estimated_gain": 2.5,
                    "gain_samples": 2,
                    "overshoot_count": 0,
                    "settled_count": 3,
                    "stability_score": 0.9,
                }
            },
            _victron_ess_balance_telemetry_response_delay_seconds=12.0,
            _victron_ess_balance_telemetry_estimated_gain=0.5,
            _victron_ess_balance_telemetry_stability_score=0.2,
            _victron_ess_balance_telemetry_overshoot_count=3,
            _victron_ess_balance_telemetry_settled_count=0,
            _victron_ess_balance_telemetry_delay_samples=4,
            _victron_ess_balance_telemetry_gain_samples=4,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        metrics = controller._victron_ess_balance_default_metrics()
        controller._populate_victron_ess_balance_telemetry_metrics(service, metrics)

        self.assertEqual(
            metrics["battery_discharge_balance_victron_bias_recommendation_profile_key"],
            "more_export:export:day:above_reserve_band",
        )
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommendation_reason"], "can_relax_conservatism")
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_kp"], 0.23)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_kd"], 0.0)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_deadband_watts"], 90.0)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_recommended_max_abs_watts"], 550.0)
        self.assertEqual(
            metrics["battery_discharge_balance_victron_bias_recommended_activation_mode"],
            "export_and_above_reserve_band",
        )

    def test_update_cycle_helpers_cover_offline_inputs_and_relay_resolution_edges(self) -> None:
        service = SimpleNamespace(
            _last_confirmed_pm_status="bad",
            _last_confirmed_pm_status_at=100.0,
            relay_sync_timeout_seconds=3.0,
            virtual_mode=1,
            _auto_cached_inputs_used=True,
            _auto_decide_relay=MagicMock(return_value=True),
            _bump_update_index=MagicMock(),
            _time_now=MagicMock(return_value=123.0),
            _last_successful_update_at=None,
            _last_recovery_attempt_at=1.0,
            last_update=0.0,
            _warning_throttled=MagicMock(),
            auto_shelly_soft_fail_seconds=10.0,
            _last_charger_transport_source="source",
            _last_charger_transport_detail="detail",
            _last_charger_state_status="charging",
            _last_charger_state_fault=None,
            _last_switch_feedback_closed=True,
            _contactor_fault_counts={},
            _contactor_lockout_source="",
            _publish_companion_dbus_bridge=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertIsNone(controller._fresh_offline_pm_status(service, 101.0))
        self.assertEqual(controller._offline_power_state(), (0.0, 0.0, 0))
        self.assertEqual(controller.resolve_auto_inputs({}, 100.0, False), (None, None, None))
        self.assertFalse(service._auto_cached_inputs_used)

        controller.complete_update_cycle(service, False, 200.0, False, 0.0, 0.0, 0, None, None, None)
        service._bump_update_index.assert_not_called()
        self.assertEqual(service._last_successful_update_at, 123.0)

        controller.complete_update_cycle(service, True, 201.0, False, 0.0, 0.0, 0, None, None, None)
        service._bump_update_index.assert_called_once_with(201.0)
        self.assertEqual(service._publish_companion_dbus_bridge.call_count, 2)
        service._publish_companion_dbus_bridge.assert_called_with(123.0)

        with patch.object(controller, "orchestrate_pending_phase_switch", return_value=(True, 2300.0, 10.0, True, None)), patch.object(
            controller,
            "_blocking_switch_feedback_health",
            return_value="switch-feedback-mismatch",
        ), patch.object(controller, "_blocking_charger_health", return_value=None), patch.object(
            controller,
            "maybe_apply_auto_phase_selection",
            return_value=True,
        ), patch.object(controller, "apply_charger_current_target") as apply_target:
            result = controller._resolved_relay_decision({}, True, 2300.0, 230.0, 10.0, True, 100.0, True, 5000.0, 50.0, -1000.0)

        self.assertEqual(result, (True, 2300.0, 10.0, True, True, "switch-feedback-mismatch"))
        apply_target.assert_called_once_with(service, True, 100.0, True)

    def test_software_update_run_is_blocked_by_no_update_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "noUpdate").write_text("", encoding="utf-8")
            service = self._software_update_service(
                temp_dir,
                _software_update_run_requested_at=50.0,
                _software_update_available=True,
                _software_update_last_check_at=100.0,
            )
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

            started = controller._start_software_update_run(service, 120.0, "manual")

            self.assertFalse(started)
            self.assertEqual(service._software_update_state, "available-blocked")
            self.assertEqual(service._software_update_detail, "noUpdate marker present")
            self.assertIsNone(service._software_update_run_requested_at)
            self.assertIsNone(service._software_update_process)

    def test_software_update_run_requires_restart_script(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "install.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            service = self._software_update_service(temp_dir, _software_update_run_requested_at=50.0)
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

            started = controller._start_software_update_run(service, 120.0, "manual")

            self.assertFalse(started)
            self.assertEqual(service._software_update_state, "update-unavailable")
            self.assertEqual(service._software_update_detail, "restart script missing")
            self.assertIsNone(service._software_update_run_requested_at)
            self.assertIsNone(service._software_update_process)

    def test_software_update_housekeeping_starts_boot_delayed_run_when_due(self):
        service = self._software_update_service(
            "",
            _software_update_next_check_at=10_000.0,
            _software_update_boot_auto_due_at=100.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(UpdateCycleController, "_start_software_update_run", return_value=True) as start_run:
            controller._software_update_housekeeping(service, 120.0)

        self.assertIsNone(service._software_update_boot_auto_due_at)
        start_run.assert_called_once_with(service, 120.0, "boot-auto")

    def test_software_update_housekeeping_starts_manual_run_when_requested(self):
        service = self._software_update_service(
            "",
            _software_update_next_check_at=10_000.0,
            _software_update_run_requested_at=110.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(UpdateCycleController, "_start_software_update_run", return_value=True) as start_run:
            controller._software_update_housekeeping(service, 120.0)

        start_run.assert_called_once_with(service, 120.0, "manual")

    def test_software_update_housekeeping_discards_manual_request_while_run_is_already_active(self):
        process = MagicMock()
        process.poll.return_value = None
        service = self._software_update_service(
            "",
            _software_update_process=process,
            _software_update_run_requested_at=110.0,
            _software_update_boot_auto_due_at=100.0,
            _software_update_next_check_at=10_000.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(UpdateCycleController, "_start_software_update_run", return_value=True) as start_run:
            controller._software_update_housekeeping(service, 120.0)

        self.assertIsNone(service._software_update_run_requested_at)
        self.assertIsNone(service._software_update_boot_auto_due_at)
        start_run.assert_not_called()

    def test_update_flushes_debounced_runtime_overrides_from_main_loop(self):
        service = self._software_update_service("")
        service._time_now = MagicMock(return_value=42.0)
        service._flush_runtime_overrides = MagicMock()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller, "_run_update_cycle", return_value=True), patch.object(
            controller,
            "_software_update_housekeeping",
        ) as housekeeping_mock:
            result = controller.update()

        self.assertTrue(result)
        service._flush_runtime_overrides.assert_called_once_with(42.0)
        housekeeping_mock.assert_called_once_with(service, 42.0)

    def test_current_learning_voltage_signature_uses_last_voltage_fallback_and_none_without_cache(self):
        service = SimpleNamespace(_last_voltage=228.5)
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller._current_learning_voltage_signature(0.0), 228.5)

        service._last_voltage = None
        self.assertIsNone(controller._current_learning_voltage_signature(0.0))

    def test_update_learned_charge_power_requires_stable_active_charge(self):
        service = SimpleNamespace(
            charging_started_at=None,
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_state="unknown",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(controller.update_learned_charge_power(False, 2, 1900.0, 230.0, 100.0))
        self.assertFalse(controller.update_learned_charge_power(True, 1, 1900.0, 230.0, 100.0))

        service.charging_started_at = 90.0
        self.assertFalse(controller.update_learned_charge_power(True, 2, 1900.0, 230.0, 100.0))
        self.assertFalse(controller.update_learned_charge_power(True, 2, 400.0, 230.0, 130.0))
        self.assertIsNone(service.learned_charge_power_watts)

    def test_learning_window_status_waits_without_session_start(self):
        service = SimpleNamespace(
            charging_started_at=None,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller._learning_window_status(100.0), ("waiting", None))

    def test_update_learned_charge_power_learns_and_smooths_stable_power(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_state="unknown",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.update_learned_charge_power(True, 2, 1900.0, 230.0, 100.0))
        self.assertEqual(service.learned_charge_power_watts, 1900.0)
        self.assertEqual(service.learned_charge_power_updated_at, 100.0)
        self.assertEqual(service.learned_charge_power_state, "learning")
        self.assertEqual(service.learned_charge_power_learning_since, 100.0)
        self.assertEqual(service.learned_charge_power_sample_count, 1)

        self.assertTrue(controller.update_learned_charge_power(True, 2, 1940.0, 230.0, 110.0))
        self.assertEqual(service.learned_charge_power_watts, 1908.0)
        self.assertEqual(service.learned_charge_power_updated_at, 110.0)
        self.assertEqual(service.learned_charge_power_state, "learning")
        self.assertEqual(service.learned_charge_power_sample_count, 2)

        self.assertTrue(controller.update_learned_charge_power(True, 2, 1920.0, 230.0, 116.0))
        self.assertEqual(service.learned_charge_power_watts, 1910.4)
        self.assertEqual(service.learned_charge_power_updated_at, 116.0)
        self.assertEqual(service.learned_charge_power_state, "stable")
        self.assertIsNone(service.learned_charge_power_learning_since)
        self.assertEqual(service.learned_charge_power_sample_count, 3)

    def test_update_learned_charge_power_respects_disable_and_configurable_learning_parameters(self):
        disabled_service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_state="unknown",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            auto_learn_charge_power_enabled=False,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
        )
        disabled_controller = UpdateCycleController(
            disabled_service,
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )
        self.assertFalse(disabled_controller.update_learned_charge_power(True, 2, 1900.0, 230.0, 100.0))
        self.assertIsNone(disabled_service.learned_charge_power_watts)

        tuned_service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=1800.0,
            learned_charge_power_updated_at=80.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=40.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=700.0,
            auto_learn_charge_power_alpha=0.5,
            phase="L1",
            max_current=16.0,
        )
        tuned_controller = UpdateCycleController(
            tuned_service,
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )
        self.assertFalse(tuned_controller.update_learned_charge_power(True, 2, 650.0, 230.0, 95.0))
        self.assertTrue(tuned_controller.update_learned_charge_power(True, 2, 2000.0, 230.0, 100.0))
        self.assertEqual(tuned_service.learned_charge_power_watts, 1900.0)
        self.assertEqual(tuned_service.learned_charge_power_updated_at, 100.0)
        self.assertEqual(tuned_service.learned_charge_power_state, "stable")

    def test_update_learned_charge_power_uses_early_session_window_and_restarts_from_stale_value(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=2400.0,
            learned_charge_power_updated_at=-30.0,
            learned_charge_power_state="stale",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=60.0,
            auto_learn_charge_power_max_age_seconds=120.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.update_learned_charge_power(True, 2, 1900.0, 230.0, 100.0))
        self.assertEqual(service.learned_charge_power_watts, 1900.0)
        self.assertEqual(service.learned_charge_power_state, "learning")
        self.assertTrue(controller.update_learned_charge_power(True, 2, 2000.0, 230.0, 150.5))
        self.assertIsNone(service.learned_charge_power_watts)
        self.assertEqual(service.learned_charge_power_state, "unknown")

    def test_stored_positive_learned_charge_power_rejects_non_positive_values(self):
        service = SimpleNamespace(learned_charge_power_watts=0.0)
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertIsNone(controller._stored_positive_learned_charge_power())

    def test_update_learned_charge_power_rejects_implausible_spike(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=1900.0,
            learned_charge_power_updated_at=90.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(controller.update_learned_charge_power(True, 2, 5000.0, 230.0, 100.0))
        self.assertEqual(service.learned_charge_power_watts, 1900.0)

    def test_orchestrate_pending_phase_switch_enters_stabilization_after_confirmed_relay_off(self):
        service = SimpleNamespace(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="waiting-relay-off",
            _phase_switch_requested_at=98.0,
            _phase_switch_stable_until=None,
            _phase_switch_resume_relay=True,
            requested_phase_selection="P1_P2",
            active_phase_selection="P1",
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _apply_phase_selection=MagicMock(return_value="P1_P2"),
            _save_runtime_state=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed, desired_override = controller.orchestrate_pending_phase_switch(
            {"output": False, "_phase_selection": "P1"},
            False,
            0.0,
            0.0,
            True,
            100.0,
            False,
        )

        self.assertFalse(relay_on)
        self.assertEqual(power, 0.0)
        self.assertEqual(current, 0.0)
        self.assertFalse(confirmed)
        self.assertFalse(desired_override)
        service._apply_phase_selection.assert_called_once_with("P1_P2")
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertEqual(service._phase_switch_state, "stabilizing")
        self.assertEqual(service._phase_switch_stable_until, 102.0)
