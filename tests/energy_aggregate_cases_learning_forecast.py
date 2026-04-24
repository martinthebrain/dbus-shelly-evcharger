# SPDX-License-Identifier: GPL-3.0-or-later
from tests.energy_aggregate_cases_common import *


class _EnergyAggregateLearningForecastCases:
    def test_update_energy_learning_profiles_tracks_observed_maxima(self) -> None:
        with patch("venus_evcharger.energy.learning._sample_period", side_effect=("day", "night")):
            profiles = update_energy_learning_profiles(
                {},
                (
                    EnergySourceSnapshot(
                        source_id="hybrid",
                        role="hybrid-inverter",
                        service_name="svc",
                        soc=70.0,
                        usable_capacity_wh=10000.0,
                        net_battery_power_w=1500.0,
                        ac_power_w=3200.0,
                        grid_interaction_w=600.0,
                        online=True,
                        confidence=1.0,
                        captured_at=75590.0,
                    ),
                ),
                75590.0,
            )
            profiles = update_energy_learning_profiles(
                profiles,
                (
                    EnergySourceSnapshot(
                        source_id="hybrid",
                        role="hybrid-inverter",
                        service_name="svc",
                        soc=68.0,
                        usable_capacity_wh=10000.0,
                        net_battery_power_w=-1800.0,
                        ac_power_w=3500.0,
                        pv_input_power_w=2200.0,
                        grid_interaction_w=-900.0,
                        online=True,
                        confidence=1.0,
                        captured_at=75600.0,
                    ),
                ),
                75600.0,
            )

        profile = profiles["hybrid"]
        self.assertEqual(profile.sample_count, 2)
        self.assertEqual(profile.active_sample_count, 2)
        self.assertEqual(profile.observed_max_discharge_power_w, 1500.0)
        self.assertEqual(profile.observed_max_charge_power_w, 1800.0)
        self.assertEqual(profile.observed_max_ac_power_w, 3500.0)
        self.assertEqual(profile.observed_max_pv_input_power_w, 2200.0)
        self.assertEqual(profile.observed_max_grid_import_w, 600.0)
        self.assertEqual(profile.observed_max_grid_export_w, 900.0)
        self.assertEqual(profile.average_active_charge_power_w, 1800.0)
        self.assertEqual(profile.average_active_discharge_power_w, 1500.0)
        self.assertEqual(profile.import_support_sample_count, 1)
        self.assertEqual(profile.export_charge_sample_count, 1)
        self.assertEqual(profile.typical_response_delay_seconds, 10.0)
        self.assertEqual(profile.observed_min_discharge_soc, 70.0)
        self.assertEqual(profile.observed_max_charge_soc, 68.0)
        self.assertEqual(profile.support_bias, 0.0)
        self.assertEqual(profile.export_bias, 1.0)
        self.assertEqual(profile.import_support_bias, 1.0)
        self.assertEqual(profile.export_idle_sample_count, 0)
        self.assertEqual(profile.day_active_sample_count, 1)
        self.assertEqual(profile.night_active_sample_count, 1)
        self.assertEqual(profile.day_discharge_sample_count, 1)
        self.assertEqual(profile.night_charge_sample_count, 1)
        self.assertEqual(profile.battery_first_export_bias, 1.0)
        self.assertEqual(profile.day_support_bias, 1.0)
        self.assertEqual(profile.night_support_bias, -1.0)
        self.assertEqual(profile.reserve_band_floor_soc, 70.0)
        self.assertEqual(profile.reserve_band_ceiling_soc, 68.0)
        self.assertIsNone(profile.reserve_band_width_soc)
        self.assertEqual(profile.last_change_at, 75600.0)

    def test_summarize_energy_learning_profiles_sums_observed_maxima(self) -> None:
        summary = summarize_energy_learning_profiles(self._summary_profiles_fixture())
        self._assert_summary_profile_counts(summary)
        self._assert_summary_power_totals(summary)
        self._assert_summary_learning_biases(summary)
        self._assert_summary_reserve_band(summary)

    @staticmethod
    def _summary_profiles_fixture() -> dict[str, dict[str, float | int]]:
        return {
            "victron": {
                "sample_count": 2,
                "active_sample_count": 2,
                "charge_sample_count": 1,
                "discharge_sample_count": 1,
                "response_sample_count": 1,
                "observed_max_charge_power_w": 700.0,
                "observed_max_discharge_power_w": 900.0,
                "observed_max_ac_power_w": 1100.0,
                "observed_max_pv_input_power_w": 1500.0,
                "observed_max_grid_import_w": 400.0,
                "average_active_charge_power_w": 700.0,
                "average_active_discharge_power_w": 900.0,
                "typical_response_delay_seconds": 4.0,
                "import_support_sample_count": 1,
                "import_charge_sample_count": 0,
                "export_charge_sample_count": 0,
                "export_discharge_sample_count": 1,
                "export_idle_sample_count": 0,
                "day_charge_sample_count": 1,
                "day_discharge_sample_count": 0,
                "night_charge_sample_count": 0,
                "night_discharge_sample_count": 1,
                "average_active_power_delta_w": 100.0,
                "smoothing_sample_count": 1,
                "observed_min_discharge_soc": 35.0,
                "observed_max_charge_soc": 85.0,
                "direction_change_count": 1,
            },
            "hybrid": {
                "sample_count": 3,
                "active_sample_count": 3,
                "charge_sample_count": 2,
                "discharge_sample_count": 1,
                "response_sample_count": 1,
                "observed_max_charge_power_w": 500.0,
                "observed_max_discharge_power_w": 1300.0,
                "observed_max_ac_power_w": 1500.0,
                "observed_max_pv_input_power_w": 2000.0,
                "observed_max_grid_export_w": 900.0,
                "average_active_charge_power_w": 450.0,
                "average_active_discharge_power_w": 1300.0,
                "typical_response_delay_seconds": 8.0,
                "import_support_sample_count": 0,
                "import_charge_sample_count": 1,
                "export_charge_sample_count": 2,
                "export_discharge_sample_count": 0,
                "export_idle_sample_count": 1,
                "day_charge_sample_count": 1,
                "day_discharge_sample_count": 1,
                "night_charge_sample_count": 1,
                "night_discharge_sample_count": 0,
                "average_active_power_delta_w": 200.0,
                "smoothing_sample_count": 2,
                "observed_min_discharge_soc": 45.0,
                "observed_max_charge_soc": 90.0,
                "direction_change_count": 2,
            },
        }

    def _assert_summary_profile_counts(self, summary: dict[str, float | int | None]) -> None:
        self.assertEqual(summary["profile_count"], 2)
        self.assertEqual(summary["sample_count"], 5)
        self.assertEqual(summary["active_sample_count"], 5)
        self.assertEqual(summary["direction_change_count"], 3)

    def _assert_summary_power_totals(self, summary: dict[str, float | int | None]) -> None:
        self.assertEqual(summary["observed_max_charge_power_w"], 1200.0)
        self.assertEqual(summary["observed_max_discharge_power_w"], 2200.0)
        self.assertEqual(summary["observed_max_ac_power_w"], 2600.0)
        self.assertEqual(summary["observed_max_pv_input_power_w"], 3500.0)
        self.assertEqual(summary["observed_max_grid_import_w"], 400.0)
        self.assertEqual(summary["observed_max_grid_export_w"], 900.0)
        self.assertAlmostEqual(summary["average_active_charge_power_w"] or 0.0, 533.3333333333, places=6)
        self.assertEqual(summary["average_active_discharge_power_w"], 1100.0)
        self.assertEqual(summary["typical_response_delay_seconds"], 6.0)
        self.assertAlmostEqual(summary["average_active_power_delta_w"] or 0.0, 166.6666666667, places=6)
        self.assertAlmostEqual(summary["power_smoothing_ratio"] or 0.0, 0.8059523810, places=6)

    def _assert_summary_learning_biases(self, summary: dict[str, float | int | None]) -> None:
        self.assertAlmostEqual(summary["support_bias"] or 0.0, -0.2, places=6)
        self.assertEqual(summary["import_support_bias"], 0.0)
        self.assertAlmostEqual(summary["export_bias"] or 0.0, 1.0 / 3.0, places=6)
        self.assertEqual(summary["battery_first_export_bias"], 0.0)
        self.assertAlmostEqual(summary["day_support_bias"] or 0.0, -1.0 / 3.0, places=6)
        self.assertEqual(summary["night_support_bias"], 0.0)

    def _assert_summary_reserve_band(self, summary: dict[str, float | int | None]) -> None:
        self.assertEqual(summary["reserve_band_floor_soc"], 45.0)
        self.assertEqual(summary["reserve_band_ceiling_soc"], 85.0)
        self.assertEqual(summary["reserve_band_width_soc"], 40.0)

    def test_update_energy_learning_profiles_uses_reactivation_delay_and_rolling_averages(self) -> None:
        profiles = update_energy_learning_profiles(
            {
                "hybrid": {
                    "source_id": "hybrid",
                    "sample_count": 1,
                    "active_sample_count": 0,
                    "charge_sample_count": 0,
                    "discharge_sample_count": 1,
                    "response_sample_count": 0,
                    "observed_min_discharge_soc": 80.0,
                    "average_active_discharge_power_w": 1000.0,
                    "last_direction": "idle",
                    "last_activity_state": "idle",
                    "last_inactive_at": 90.0,
                    "last_change_at": 90.0,
                },
            },
            (
                EnergySourceSnapshot(
                    source_id="hybrid",
                    role="hybrid-inverter",
                    service_name="svc",
                    soc=60.0,
                    net_battery_power_w=1500.0,
                    online=True,
                    confidence=1.0,
                    captured_at=100.0,
                ),
            ),
            100.0,
        )

        profile = profiles["hybrid"]
        self.assertEqual(profile.response_sample_count, 1)
        self.assertEqual(profile.typical_response_delay_seconds, 10.0)
        self.assertEqual(profile.observed_min_discharge_soc, 60.0)
        self.assertEqual(profile.average_active_discharge_power_w, 1250.0)

    def test_update_energy_learning_profiles_tracks_smoothing_and_export_first_behavior(self) -> None:
        profiles = update_energy_learning_profiles(
            {
                "hybrid": {
                    "source_id": "hybrid",
                    "sample_count": 1,
                    "active_sample_count": 1,
                    "charge_sample_count": 1,
                    "average_active_charge_power_w": 1000.0,
                    "last_direction": "charge",
                    "last_activity_state": "active",
                    "last_change_at": 43200.0,
                },
            },
            (
                EnergySourceSnapshot(
                    source_id="hybrid",
                    role="hybrid-inverter",
                    service_name="svc",
                    soc=75.0,
                    net_battery_power_w=0.0,
                    grid_interaction_w=-500.0,
                    online=True,
                    confidence=1.0,
                    captured_at=84600.0,
                ),
            ),
            84600.0,
        )

        profile = profiles["hybrid"]
        self.assertEqual(profile.export_idle_sample_count, 1)
        self.assertEqual(profile.smoothing_sample_count, 0)
        self.assertIsNone(profile.average_active_power_delta_w)
        self.assertEqual(profile.battery_first_export_bias, -1.0)

    def test_energy_learning_profile_reports_reserve_band_width_when_floor_and_ceiling_are_valid(self) -> None:
        profile = EnergyLearningProfile(
            source_id="hybrid",
            observed_min_discharge_soc=35.0,
            observed_max_charge_soc=85.0,
        )

        self.assertEqual(profile.reserve_band_width_soc, 50.0)

    def test_derive_energy_forecast_uses_reserve_band_capture_bias_and_smoothing(self) -> None:
        forecast = derive_energy_forecast(
            {
                "battery_combined_soc": 79.0,
                "battery_combined_charge_power_w": 800.0,
                "battery_combined_discharge_power_w": 1200.0,
                "battery_combined_grid_interaction_w": -400.0,
            },
            {
                "observed_max_charge_power_w": 2000.0,
                "observed_max_discharge_power_w": 3000.0,
                "average_active_charge_power_w": 1000.0,
                "average_active_discharge_power_w": 1600.0,
                "typical_response_delay_seconds": 30.0,
                "export_bias": 0.75,
                "battery_first_export_bias": 0.5,
                "power_smoothing_ratio": 0.8,
                "reserve_band_floor_soc": 35.0,
                "reserve_band_ceiling_soc": 80.0,
                "import_support_bias": 0.5,
            },
        )

        self.assertEqual(forecast["battery_headroom_charge_w"], 120.0)
        self.assertEqual(forecast["battery_headroom_discharge_w"], 1800.0)
        self.assertAlmostEqual(forecast["expected_near_term_export_w"] or 0.0, 404.6, places=6)
        self.assertEqual(forecast["expected_near_term_import_w"], 0.0)

    def test_derive_energy_forecast_returns_headroom_and_near_term_grid_estimates(self) -> None:
        forecast = derive_energy_forecast(
            {
                "battery_combined_charge_power_w": 800.0,
                "battery_combined_discharge_power_w": 1200.0,
                "battery_combined_grid_interaction_w": 400.0,
            },
            {
                "observed_max_charge_power_w": 2000.0,
                "observed_max_discharge_power_w": 3000.0,
                "average_active_charge_power_w": 1000.0,
                "average_active_discharge_power_w": 1600.0,
                "typical_response_delay_seconds": 30.0,
                "support_bias": 0.5,
                "import_support_bias": 0.5,
                "export_bias": 0.75,
            },
        )

        self.assertEqual(forecast["battery_headroom_charge_w"], 1200.0)
        self.assertEqual(forecast["battery_headroom_discharge_w"], 1800.0)
        self.assertAlmostEqual(forecast["expected_near_term_export_w"] or 0.0, 210.0, places=6)
        self.assertAlmostEqual(forecast["expected_near_term_import_w"] or 0.0, 100.0, places=6)

    def test_derive_energy_forecast_prefers_documented_charge_discharge_limits(self) -> None:
        forecast = derive_energy_forecast(
            {
                "battery_combined_charge_power_w": 800.0,
                "battery_combined_discharge_power_w": 1200.0,
                "battery_combined_charge_limit_power_w": 900.0,
                "battery_combined_discharge_limit_power_w": 1400.0,
                "battery_combined_grid_interaction_w": 400.0,
            },
            {
                "observed_max_charge_power_w": 2000.0,
                "observed_max_discharge_power_w": 3000.0,
                "average_active_charge_power_w": 1000.0,
                "average_active_discharge_power_w": 1600.0,
                "support_bias": 0.5,
                "import_support_bias": 0.5,
                "export_bias": 0.75,
            },
        )

        self.assertEqual(forecast["battery_headroom_charge_w"], 100.0)
        self.assertEqual(forecast["battery_headroom_discharge_w"], 200.0)

    def test_derive_energy_forecast_covers_average_limit_and_saturation_fallbacks(self) -> None:
        forecast = derive_energy_forecast(
            {
                "battery_combined_charge_power_w": 600.0,
                "battery_combined_discharge_power_w": 0.0,
                "battery_combined_grid_interaction_w": -50.0,
            },
            {
                "average_active_charge_power_w": 400.0,
                "average_active_discharge_power_w": 250.0,
                "export_bias": 0.5,
            },
        )

        self.assertEqual(forecast["battery_headroom_charge_w"], 0.0)
        self.assertEqual(forecast["battery_headroom_discharge_w"], 312.5)
        self.assertAlmostEqual(forecast["expected_near_term_export_w"] or 0.0, 350.0, places=6)
        self.assertEqual(forecast["expected_near_term_import_w"], 0.0)

        no_grid_forecast = derive_energy_forecast(
            {
                "battery_combined_charge_power_w": 0.0,
                "battery_combined_discharge_power_w": 0.0,
                "battery_combined_grid_interaction_w": None,
            },
            {},
        )
        self.assertIsNone(no_grid_forecast["expected_near_term_export_w"])
        self.assertIsNone(no_grid_forecast["expected_near_term_import_w"])

    def test_derive_energy_forecast_saturates_when_only_current_charge_power_is_known(self) -> None:
        forecast = derive_energy_forecast(
            {
                "battery_combined_charge_power_w": 400.0,
                "battery_combined_grid_interaction_w": -100.0,
            },
            {
                "export_bias": 1.0,
            },
        )

        self.assertEqual(forecast["battery_headroom_charge_w"], 0.0)
        self.assertIsNone(forecast["battery_headroom_discharge_w"])
        self.assertEqual(forecast["expected_near_term_export_w"], 500.0)

    def test_derive_energy_forecast_zeroes_headroom_at_reserve_band_edges(self) -> None:
        forecast = derive_energy_forecast(
            {
                "battery_combined_soc": 80.0,
                "battery_combined_charge_power_w": 200.0,
                "battery_combined_discharge_power_w": 300.0,
                "battery_combined_grid_interaction_w": 150.0,
            },
            {
                "observed_max_charge_power_w": 1200.0,
                "observed_max_discharge_power_w": 1400.0,
                "average_active_charge_power_w": 600.0,
                "average_active_discharge_power_w": 900.0,
                "reserve_band_ceiling_soc": 80.0,
                "reserve_band_floor_soc": 80.0,
                "import_support_bias": 1.0,
            },
        )

        self.assertEqual(forecast["battery_headroom_charge_w"], 0.0)
        self.assertEqual(forecast["battery_headroom_discharge_w"], 0.0)
        self.assertEqual(forecast["expected_near_term_import_w"], 150.0)

    def test_derive_energy_forecast_returns_zero_saturation_without_observed_limit(self) -> None:
        forecast = derive_energy_forecast(
            {
                "battery_combined_charge_power_w": 0.0,
                "battery_combined_grid_interaction_w": -25.0,
            },
            {
                "export_bias": 1.0,
            },
        )

        self.assertEqual(forecast["expected_near_term_export_w"], 25.0)

    def test_charge_saturation_helper_returns_one_when_charge_power_exists_without_limit(self) -> None:
        charge_saturation = derive_energy_forecast.__globals__["_charge_saturation"]
        self.assertEqual(charge_saturation(None, None, 250.0), 1.0)

    def test_energy_cluster_as_dict_includes_ac_output_alias(self) -> None:
        cluster = aggregate_energy_sources(
            (
                EnergySourceSnapshot(
                    source_id="hybrid",
                    role="hybrid-inverter",
                    service_name="svc",
                    soc=50.0,
                    usable_capacity_wh=5000.0,
                    ac_power_w=1800.0,
                    online=True,
                    confidence=1.0,
                    captured_at=1.0,
                ),
            )
        )

        payload = cluster.as_dict()

        self.assertEqual(payload["combined_ac_power_w"], 1800.0)
        self.assertEqual(payload["combined_ac_output_power_w"], 1800.0)
