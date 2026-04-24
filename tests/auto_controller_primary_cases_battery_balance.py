# SPDX-License-Identifier: GPL-3.0-or-later
from tests.auto_controller_primary_cases_common import *


class _AutoControllerPrimaryBatteryBalanceCases:
    def test_combined_battery_activity_penalizes_surplus_when_batteries_are_active(self):
        controller, service = self._make_controller()
        service._add_auto_sample = MagicMock()
        service._average_auto_metric = MagicMock(side_effect=[1200.0, 100.0])
        service._last_energy_cluster = {
            "battery_sources": [
                {"source_id": "hybrid", "charge_power_w": 0.0, "discharge_power_w": 400.0},
                {"source_id": "victron", "charge_power_w": 200.0, "discharge_power_w": 0.0},
            ]
        }
        service._last_energy_learning_profiles = {
            "hybrid": {"observed_max_discharge_power_w": 1000.0, "sample_count": 3},
            "victron": {"observed_max_charge_power_w": 500.0, "sample_count": 2},
        }

        surplus, grid = controller._update_average_metrics(100.0, 2200.0, 400.0, 60.0, False)

        self.assertEqual(surplus, 600.0)
        self.assertEqual(grid, 100.0)
        self.assertEqual(service._last_auto_metrics["battery_surplus_penalty_w"], 600.0)
        self.assertEqual(service._last_auto_metrics["battery_support_mode"], "mixed")
        self.assertEqual(service._last_auto_metrics["battery_learning_profile_count"], 2)
        self.assertEqual(service._last_auto_metrics["battery_observed_max_charge_power_w"], 500.0)
        self.assertEqual(service._last_auto_metrics["battery_observed_max_discharge_power_w"], 1000.0)

    def test_tiny_battery_activity_below_learned_ratio_threshold_is_ignored(self):
        controller, service = self._make_controller()
        service._add_auto_sample = MagicMock()
        service._average_auto_metric = MagicMock(side_effect=[1200.0, 100.0])
        service._last_energy_cluster = {
            "battery_sources": [
                {"source_id": "hybrid", "charge_power_w": 0.0, "discharge_power_w": 30.0},
            ]
        }
        service._last_energy_learning_profiles = {
            "hybrid": {"observed_max_discharge_power_w": 1000.0, "sample_count": 5},
        }

        surplus, _grid = controller._update_average_metrics(100.0, 2200.0, 400.0, 60.0, False)

        self.assertEqual(surplus, 1200.0)
        self.assertEqual(service._last_auto_metrics["battery_surplus_penalty_w"], 0.0)
        self.assertEqual(service._last_auto_metrics["battery_discharge_activity_ratio"], 0.03)

    def test_battery_activity_penalty_uses_response_delay_and_bias_metrics(self):
        controller, service = self._make_controller()
        service._add_auto_sample = MagicMock()
        service._average_auto_metric = MagicMock(side_effect=[1200.0, 100.0])
        service._time_now = lambda: datetime(2026, 4, 22, 13, 0).timestamp()
        service._last_energy_cluster = {
            "battery_sources": [
                {"source_id": "hybrid", "charge_power_w": 0.0, "discharge_power_w": 400.0},
            ]
        }
        service._last_energy_learning_profiles = {
            "hybrid": {
                "observed_max_discharge_power_w": 1000.0,
                "sample_count": 4,
                "charge_sample_count": 1,
                "discharge_sample_count": 3,
                "day_charge_sample_count": 0,
                "day_discharge_sample_count": 3,
                "night_charge_sample_count": 1,
                "night_discharge_sample_count": 0,
                "import_support_sample_count": 3,
                "import_charge_sample_count": 1,
                "response_sample_count": 1,
                "typical_response_delay_seconds": 30.0,
            },
        }

        surplus, _grid = controller._update_average_metrics(100.0, 2200.0, 400.0, 60.0, False)

        self.assertAlmostEqual(surplus, 637.5, places=6)
        self.assertAlmostEqual(service._last_auto_metrics["battery_surplus_penalty_w"], 562.5, places=6)
        self.assertEqual(service._last_auto_metrics["battery_support_mode"], "discharging")
        self.assertEqual(service._last_auto_metrics["battery_typical_response_delay_seconds"], 30.0)
        self.assertAlmostEqual(service._last_auto_metrics["battery_support_bias"], 1.0, places=6)
        self.assertEqual(service._last_auto_metrics["battery_day_support_bias"], 1.0)
        self.assertEqual(service._last_auto_metrics["battery_night_support_bias"], -1.0)
        self.assertAlmostEqual(service._last_auto_metrics["battery_import_support_bias"], 0.5, places=6)
        self.assertIsNone(service._last_auto_metrics["battery_export_bias"])

    def test_battery_activity_uses_near_term_grid_forecast_metrics(self):
        controller, service = self._make_controller()
        service._add_auto_sample = MagicMock()
        service._average_auto_metric = MagicMock(side_effect=[1200.0, 100.0])
        service._last_energy_cluster = {
            "battery_sources": [
                {"source_id": "hybrid", "charge_power_w": 800.0, "discharge_power_w": 0.0},
            ],
            "battery_combined_charge_power_w": 800.0,
            "battery_combined_discharge_power_w": 0.0,
            "battery_combined_grid_interaction_w": -200.0,
        }
        service._last_energy_learning_profiles = {
            "hybrid": {
                "observed_max_charge_power_w": 2000.0,
                "sample_count": 4,
                "charge_sample_count": 3,
                "discharge_sample_count": 1,
                "export_charge_sample_count": 3,
                "export_discharge_sample_count": 0,
                "response_sample_count": 1,
                "typical_response_delay_seconds": 30.0,
            },
        }

        surplus, _grid = controller._update_average_metrics(100.0, 2200.0, 400.0, 60.0, False)

        self.assertAlmostEqual(surplus, 7.0, places=6)
        self.assertEqual(service._last_auto_metrics["battery_headroom_charge_w"], 1200.0)
        self.assertIsNone(service._last_auto_metrics["battery_headroom_discharge_w"])
        self.assertAlmostEqual(service._last_auto_metrics["expected_near_term_export_w"], 380.0, places=6)
        self.assertAlmostEqual(service._last_auto_metrics["expected_near_term_import_w"], 0.0, places=6)
        self.assertAlmostEqual(service._last_auto_metrics["battery_near_term_adjustment_w"], 57.0, places=6)

    def test_combined_battery_activity_context_covers_empty_sources_and_helper_normalizers(self):
        controller, service = self._make_controller()
        service._last_energy_cluster = {
            "battery_sources": [],
            "battery_combined_charge_power_w": 250.0,
            "battery_combined_discharge_power_w": 300.0,
            "battery_headroom_charge_w": -5.0,
            "battery_headroom_discharge_w": 50.0,
            "expected_near_term_export_w": 40.0,
            "expected_near_term_import_w": 60.0,
        }
        service._last_energy_learning_profiles = {
            "hybrid": {
                "observed_max_charge_power_w": -1.0,
                "observed_max_discharge_power_w": 1000.0,
                "typical_response_delay_seconds": 500.0,
                "support_bias": 2.0,
                "import_support_bias": 3.0,
                "export_bias": -2.0,
            }
        }

        activity = controller._combined_battery_activity_context()

        self.assertEqual(activity["charge_power_w"], 375.0)
        self.assertEqual(activity["discharge_power_w"], 450.0)
        self.assertIsNone(activity["charge_activity_ratio"])
        self.assertEqual(activity["discharge_activity_ratio"], 0.3)
        self.assertIsNone(activity["battery_headroom_charge_w"])
        self.assertEqual(activity["battery_headroom_discharge_w"], 50.0)
        self.assertEqual(activity["expected_near_term_export_w"], 40.0)
        self.assertEqual(activity["expected_near_term_import_w"], 60.0)
        self.assertIsNone(activity["support_bias"])
        self.assertIsNone(activity["import_support_bias"])
        self.assertIsNone(activity["export_bias"])
        self.assertEqual(activity["typical_response_delay_seconds"], 500.0)
        self.assertEqual(activity["mode"], "mixed")

        service._last_energy_cluster = {"battery_sources": ["bad-source"]}
        idle_activity = controller._combined_battery_activity_context()
        self.assertEqual(idle_activity["mode"], "idle")
        self.assertEqual(controller._normalized_mapping_list("bad-source"), [])

        self.assertIsNone(controller._learning_observed_value("bad-profile", "observed_max_charge_power_w"))
        self.assertEqual(controller._active_battery_power(50.0, None), (50.0, None))
        self.assertEqual(controller._active_battery_power(50.0, 1000.0), (50.0, 0.05))
        self.assertIsNone(controller._non_negative_optional_float(-1.0))
        self.assertEqual(controller._max_optional_ratio(0.2, 0.4), 0.4)
        self.assertEqual(controller._bounded_optional_float(-5.0), -1.0)
        self.assertEqual(controller._bounded_optional_float(5.0), 1.0)
        self.assertEqual(
            controller._battery_penalty_multiplier(
                direction="discharge",
                response_delay_seconds=30.0,
                support_bias=0.5,
                import_support_bias=None,
                export_bias=None,
            ),
            1.40625,
        )
        self.assertEqual(
            controller._battery_penalty_multiplier(
                direction="idle",
                response_delay_seconds=30.0,
                support_bias=0.5,
                import_support_bias=0.5,
                export_bias=0.5,
            ),
            1.25,
        )

    def test_battery_discharge_balance_policy_warns_and_adds_soft_penalty(self):
        controller, service = self._make_controller()
        service.auto_battery_discharge_balance_policy_enabled = True
        service.auto_battery_discharge_balance_warn_error_watts = 400.0
        service.auto_battery_discharge_balance_bias_start_error_watts = 500.0
        service.auto_battery_discharge_balance_bias_max_penalty_watts = 300.0
        service.auto_battery_discharge_balance_bias_mode = "always"
        service._warning_throttled = MagicMock()
        service._add_auto_sample = MagicMock()
        service._average_auto_metric = MagicMock(side_effect=[1200.0, 100.0])
        service._last_energy_cluster = {
            "battery_sources": [
                {"source_id": "hybrid", "charge_power_w": 0.0, "discharge_power_w": 400.0},
            ],
            "battery_combined_soc": 60.0,
            "battery_discharge_balance_error_w": 750.0,
            "battery_discharge_balance_eligible_source_count": 2,
            "battery_discharge_balance_active_source_count": 1,
        }
        service._last_energy_learning_profiles = {
            "hybrid": {"observed_max_discharge_power_w": 1000.0, "sample_count": 3},
        }

        surplus, _grid = controller._update_average_metrics(100.0, 2200.0, 400.0, 60.0, False)

        self.assertEqual(surplus, 650.0)
        self.assertEqual(service._last_auto_metrics["battery_surplus_penalty_w"], 550.0)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_policy_enabled"], 1)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_warning_active"], 1)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_warning_error_w"], 750.0)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_warn_threshold_w"], 400.0)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_bias_mode"], "always")
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_bias_gate_active"], 1)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_bias_start_error_w"], 500.0)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_bias_penalty_w"], 150.0)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_coordination_feasibility"], "observe_only")
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_coordination_advisory_active"], 1)
        self.assertEqual(
            service._last_auto_metrics["battery_discharge_balance_coordination_advisory_reason"],
            "no_configured_source_offers_a_write_path",
        )
        self.assertEqual(service._warning_throttled.call_count, 2)

    def test_battery_discharge_balance_policy_can_gate_bias_to_export_only(self):
        controller, service = self._make_controller()
        service.auto_battery_discharge_balance_policy_enabled = True
        service.auto_battery_discharge_balance_warn_error_watts = 400.0
        service.auto_battery_discharge_balance_bias_start_error_watts = 500.0
        service.auto_battery_discharge_balance_bias_max_penalty_watts = 300.0
        service.auto_battery_discharge_balance_bias_mode = "export_only"
        service._warning_throttled = MagicMock()
        service._last_energy_cluster = {
            "battery_sources": [
                {"source_id": "hybrid", "charge_power_w": 0.0, "discharge_power_w": 400.0},
            ],
            "battery_combined_soc": 60.0,
            "battery_combined_grid_interaction_w": 200.0,
            "battery_discharge_balance_error_w": 750.0,
            "battery_discharge_balance_eligible_source_count": 2,
            "battery_discharge_balance_active_source_count": 1,
        }
        service._last_energy_learning_profiles = {
            "hybrid": {"observed_max_discharge_power_w": 1000.0, "sample_count": 3},
        }

        activity = controller._combined_battery_activity_context()

        self.assertEqual(activity["discharge_balance_bias_mode"], "export_only")
        self.assertEqual(activity["discharge_balance_bias_gate_active"], 0)
        self.assertEqual(activity["discharge_balance_bias_penalty_w"], 0.0)
        self.assertEqual(activity["discharge_balance_coordination_feasibility"], "observe_only")
        self.assertEqual(activity["discharge_balance_coordination_advisory_active"], 1)
        self.assertEqual(service._warning_throttled.call_count, 2)

    def test_battery_discharge_balance_policy_can_gate_bias_to_reserve_band(self):
        controller, service = self._make_controller()
        service.auto_battery_discharge_balance_policy_enabled = True
        service.auto_battery_discharge_balance_warn_error_watts = 400.0
        service.auto_battery_discharge_balance_bias_start_error_watts = 500.0
        service.auto_battery_discharge_balance_bias_max_penalty_watts = 300.0
        service.auto_battery_discharge_balance_bias_mode = "above_reserve_band"
        service.auto_battery_discharge_balance_bias_reserve_margin_soc = 5.0
        service._warning_throttled = MagicMock()
        service._last_energy_cluster = {
            "battery_sources": [
                {"source_id": "hybrid", "charge_power_w": 0.0, "discharge_power_w": 400.0},
            ],
            "battery_combined_soc": 42.0,
            "battery_discharge_balance_error_w": 750.0,
            "battery_discharge_balance_eligible_source_count": 2,
            "battery_discharge_balance_active_source_count": 1,
        }
        service._last_energy_learning_profiles = {
            "hybrid": {"observed_max_discharge_power_w": 1000.0, "sample_count": 3},
            "other": {"reserve_band_floor_soc": 40.0},
        }

        activity = controller._combined_battery_activity_context()

        self.assertEqual(activity["discharge_balance_bias_mode"], "above_reserve_band")
        self.assertEqual(activity["discharge_balance_bias_gate_active"], 0)
        self.assertEqual(activity["discharge_balance_bias_penalty_w"], 0.0)

    def test_battery_discharge_balance_coordination_penalty_only_activates_with_two_ready_sources(self):
        controller, service = self._make_controller()
        service.auto_battery_discharge_balance_coordination_enabled = True
        service.auto_battery_discharge_balance_coordination_support_mode = "supported_only"
        service.auto_battery_discharge_balance_coordination_start_error_watts = 500.0
        service.auto_battery_discharge_balance_coordination_max_penalty_watts = 200.0
        service._last_energy_cluster = {
            "battery_sources": [
                {"source_id": "hybrid", "charge_power_w": 0.0, "discharge_power_w": 400.0},
            ],
            "battery_discharge_balance_error_w": 750.0,
            "battery_discharge_balance_eligible_source_count": 2,
            "battery_discharge_balance_active_source_count": 1,
            "battery_discharge_balance_control_ready_count": 2,
            "battery_discharge_balance_supported_control_source_count": 2,
            "battery_discharge_balance_experimental_control_source_count": 0,
        }
        service._last_energy_learning_profiles = {
            "hybrid": {"observed_max_discharge_power_w": 1000.0, "sample_count": 3},
        }

        activity = controller._combined_battery_activity_context()

        self.assertEqual(activity["discharge_balance_coordination_policy_enabled"], 1)
        self.assertEqual(activity["discharge_balance_coordination_support_mode"], "supported_only")
        self.assertEqual(activity["discharge_balance_coordination_feasibility"], "supported")
        self.assertEqual(activity["discharge_balance_coordination_gate_active"], 1)
        self.assertEqual(activity["discharge_balance_coordination_start_error_w"], 500.0)
        self.assertEqual(activity["discharge_balance_coordination_penalty_w"], 100.0)
        self.assertEqual(activity["surplus_penalty_w"], 500.0)

    def test_battery_discharge_balance_coordination_penalty_stays_inactive_when_only_one_source_is_ready(self):
        controller, service = self._make_controller()
        service.auto_battery_discharge_balance_coordination_enabled = True
        service.auto_battery_discharge_balance_coordination_support_mode = "supported_only"
        service.auto_battery_discharge_balance_coordination_start_error_watts = 500.0
        service.auto_battery_discharge_balance_coordination_max_penalty_watts = 200.0
        service._last_energy_cluster = {
            "battery_sources": [
                {"source_id": "hybrid", "charge_power_w": 0.0, "discharge_power_w": 400.0},
            ],
            "battery_discharge_balance_error_w": 750.0,
            "battery_discharge_balance_eligible_source_count": 2,
            "battery_discharge_balance_active_source_count": 1,
            "battery_discharge_balance_control_candidate_count": 2,
            "battery_discharge_balance_control_ready_count": 1,
            "battery_discharge_balance_supported_control_source_count": 2,
        }
        service._last_energy_learning_profiles = {
            "hybrid": {"observed_max_discharge_power_w": 1000.0, "sample_count": 3},
        }

        activity = controller._combined_battery_activity_context()

        self.assertEqual(activity["discharge_balance_coordination_policy_enabled"], 1)
        self.assertEqual(activity["discharge_balance_coordination_feasibility"], "blocked_by_source_availability")
        self.assertEqual(activity["discharge_balance_coordination_gate_active"], 0)
        self.assertEqual(activity["discharge_balance_coordination_penalty_w"], 0.0)
        self.assertEqual(activity["surplus_penalty_w"], 400.0)

    def test_battery_discharge_balance_coordination_support_mode_can_keep_experimental_paths_inactive(self):
        controller, service = self._make_controller()
        service.auto_battery_discharge_balance_coordination_enabled = True
        service.auto_battery_discharge_balance_coordination_support_mode = "supported_only"
        service.auto_battery_discharge_balance_coordination_start_error_watts = 500.0
        service.auto_battery_discharge_balance_coordination_max_penalty_watts = 200.0
        service._last_energy_cluster = {
            "battery_sources": [
                {"source_id": "hybrid", "charge_power_w": 0.0, "discharge_power_w": 400.0},
            ],
            "battery_discharge_balance_error_w": 750.0,
            "battery_discharge_balance_eligible_source_count": 2,
            "battery_discharge_balance_active_source_count": 1,
            "battery_discharge_balance_control_ready_count": 2,
            "battery_discharge_balance_supported_control_source_count": 1,
            "battery_discharge_balance_experimental_control_source_count": 1,
        }
        service._last_energy_learning_profiles = {
            "hybrid": {"observed_max_discharge_power_w": 1000.0, "sample_count": 3},
        }

        activity = controller._combined_battery_activity_context()

        self.assertEqual(activity["discharge_balance_coordination_support_mode"], "supported_only")
        self.assertEqual(activity["discharge_balance_coordination_feasibility"], "experimental")
        self.assertEqual(activity["discharge_balance_coordination_gate_active"], 0)
        self.assertEqual(activity["discharge_balance_coordination_penalty_w"], 0.0)

        service.auto_battery_discharge_balance_coordination_support_mode = "allow_experimental"
        activity = controller._combined_battery_activity_context()

        self.assertEqual(activity["discharge_balance_coordination_support_mode"], "allow_experimental")
        self.assertEqual(activity["discharge_balance_coordination_feasibility"], "experimental")
        self.assertEqual(activity["discharge_balance_coordination_gate_active"], 1)
        self.assertEqual(activity["discharge_balance_coordination_penalty_w"], 100.0)
