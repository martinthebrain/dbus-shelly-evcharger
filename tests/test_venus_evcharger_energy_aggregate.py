# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from venus_evcharger.energy import (
    EnergySourceSnapshot,
    EnergyLearningProfile,
    aggregate_energy_sources,
    derive_energy_forecast,
    load_energy_source_settings,
    summarize_energy_learning_profiles,
    update_energy_learning_profiles,
)


class TestVenusEvchargerEnergyAggregate(unittest.TestCase):
    def test_aggregate_energy_sources_returns_none_average_confidence_without_valid_values(self) -> None:
        cluster = aggregate_energy_sources(
            (
                EnergySourceSnapshot(
                    source_id="external",
                    role="battery",
                    service_name="svc",
                    soc=None,
                    usable_capacity_wh=None,
                    online=False,
                    confidence=-1.0,
                    captured_at=1.0,
                ),
            )
        )

        self.assertIsNone(cluster.average_confidence)

    def test_aggregate_energy_sources_uses_capacity_weighted_soc_and_power_sums(self) -> None:
        cluster = aggregate_energy_sources(
            (
                EnergySourceSnapshot(
                    source_id="victron",
                    role="battery",
                    service_name="com.victronenergy.battery.victron",
                    soc=40.0,
                    usable_capacity_wh=5000.0,
                    net_battery_power_w=1200.0,
                    grid_interaction_w=300.0,
                    online=True,
                    confidence=1.0,
                    captured_at=100.0,
                ),
                EnergySourceSnapshot(
                    source_id="hybrid",
                    role="hybrid-inverter",
                    service_name="com.victronenergy.hybrid.hybrid",
                    soc=80.0,
                    usable_capacity_wh=10000.0,
                    net_battery_power_w=-800.0,
                    ac_power_w=3200.0,
                    pv_input_power_w=2500.0,
                    grid_interaction_w=-600.0,
                    online=True,
                    confidence=0.6,
                    captured_at=100.0,
                ),
            )
        )

        self.assertAlmostEqual(cluster.combined_soc or 0.0, 66.6666666667, places=6)
        self.assertAlmostEqual(cluster.effective_soc or 0.0, 66.6666666667, places=6)
        self.assertEqual(cluster.combined_usable_capacity_wh, 15000.0)
        self.assertEqual(cluster.combined_discharge_power_w, 1200.0)
        self.assertEqual(cluster.combined_charge_power_w, 800.0)
        self.assertEqual(cluster.combined_net_battery_power_w, 400.0)
        self.assertEqual(cluster.combined_ac_power_w, 3200.0)
        self.assertEqual(cluster.combined_pv_input_power_w, 2500.0)
        self.assertEqual(cluster.combined_grid_interaction_w, -300.0)
        self.assertEqual(cluster.average_confidence, 0.8)
        self.assertEqual(cluster.source_count, 2)
        self.assertEqual(cluster.online_source_count, 2)
        self.assertEqual(cluster.valid_soc_source_count, 2)
        self.assertEqual(cluster.battery_source_count, 1)
        self.assertEqual(cluster.hybrid_inverter_source_count, 1)
        self.assertEqual(cluster.inverter_source_count, 0)

    def test_aggregate_energy_sources_falls_back_to_single_online_soc_without_capacity(self) -> None:
        cluster = aggregate_energy_sources(
            (
                EnergySourceSnapshot(
                    source_id="primary",
                    role="battery",
                    service_name="com.victronenergy.battery.primary",
                    soc=55.0,
                    usable_capacity_wh=None,
                    online=True,
                    confidence=1.0,
                    captured_at=100.0,
                ),
            )
        )

        self.assertIsNone(cluster.combined_soc)
        self.assertEqual(cluster.effective_soc, 55.0)

    def test_load_energy_source_settings_supports_dynamic_sources_and_legacy_defaults(self) -> None:
        legacy_sources, use_combined = load_energy_source_settings(
            {
                "AutoBatteryService": "com.victronenergy.battery.legacy",
                "AutoBatteryServicePrefix": "com.victronenergy.battery",
                "AutoBatterySocPath": "/Soc",
                "AutoBatteryCapacityWh": "5120",
            }
        )
        self.assertTrue(use_combined)
        self.assertEqual(len(legacy_sources), 1)
        self.assertEqual(legacy_sources[0].source_id, "primary_battery")
        self.assertEqual(legacy_sources[0].connector_type, "dbus")
        self.assertEqual(legacy_sources[0].usable_capacity_wh, 5120.0)

        configured_sources, use_combined = load_energy_source_settings(
            {
                "AutoEnergySources": "victron,hybrid",
                "AutoUseCombinedBatterySoc": "0",
                "AutoEnergySource.victron.Role": "battery",
                "AutoEnergySource.victron.Service": "com.victronenergy.battery.victron",
                "AutoEnergySource.victron.SocPath": "/Soc",
                "AutoEnergySource.victron.UsableCapacityWh": "5000",
                "AutoEnergySource.hybrid.Role": "hybrid-inverter",
                "AutoEnergySource.hybrid.Type": "template_http_energy",
                "AutoEnergySource.hybrid.ConfigPath": "/data/etc/external-hybrid.ini",
                "AutoEnergySource.hybrid.Service": "com.victronenergy.hybrid.hybrid",
                "AutoEnergySource.hybrid.SocPath": "/Soc",
                "AutoEnergySource.hybrid.UsableCapacityWh": "10000",
                "AutoEnergySource.hybrid.BatteryPowerPath": "/Dc/0/Power",
                "AutoEnergySource.hybrid.AcPowerPath": "/Ac/Power",
                "AutoEnergySource.hybrid.PvPowerPath": "/Pv/Power",
                "AutoEnergySource.hybrid.GridInteractionPath": "/Grid/Power",
                "AutoEnergySource.hybrid.OperatingModePath": "/Mode",
            }
        )
        self.assertFalse(use_combined)
        self.assertEqual([source.source_id for source in configured_sources], ["victron", "hybrid"])
        self.assertEqual(configured_sources[1].role, "hybrid-inverter")
        self.assertEqual(configured_sources[1].connector_type, "template_http")
        self.assertEqual(configured_sources[1].config_path, "/data/etc/external-hybrid.ini")
        self.assertEqual(configured_sources[1].battery_power_path, "/Dc/0/Power")
        self.assertEqual(configured_sources[1].pv_power_path, "/Pv/Power")
        self.assertEqual(configured_sources[1].grid_interaction_path, "/Grid/Power")
        self.assertEqual(configured_sources[1].operating_mode_path, "/Mode")

        connector_sources, _ = load_energy_source_settings(
            {
                "AutoEnergySources": "modbus,helper",
                "AutoEnergySource.modbus.Role": "battery",
                "AutoEnergySource.modbus.Type": "modbus",
                "AutoEnergySource.modbus.ConfigPath": "/data/etc/external-modbus.ini",
                "AutoEnergySource.helper.Role": "hybrid-inverter",
                "AutoEnergySource.helper.Type": "command_json",
                "AutoEnergySource.helper.ConfigPath": "/data/etc/external-helper.ini",
            }
        )
        self.assertEqual(connector_sources[0].connector_type, "modbus")
        self.assertEqual(connector_sources[1].connector_type, "command_json")

    def test_load_energy_source_settings_normalizes_invalid_role_connector_and_capacity(self) -> None:
        configured_sources, use_combined = load_energy_source_settings(
            {
                "AutoEnergySources": "external",
                "AutoUseCombinedBatterySoc": "yes",
                "AutoEnergySource.external.Role": "unknown",
                "AutoEnergySource.external.Type": "unknown",
                "AutoEnergySource.external.UsableCapacityWh": "bad",
            }
        )

        self.assertTrue(use_combined)
        self.assertEqual(len(configured_sources), 1)
        self.assertEqual(configured_sources[0].role, "battery")
        self.assertEqual(configured_sources[0].connector_type, "dbus")
        self.assertIsNone(configured_sources[0].usable_capacity_wh)

    def test_update_energy_learning_profiles_tracks_observed_maxima(self) -> None:
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
        summary = summarize_energy_learning_profiles(
            {
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
        )

        self.assertEqual(summary["profile_count"], 2)
        self.assertEqual(summary["sample_count"], 5)
        self.assertEqual(summary["active_sample_count"], 5)
        self.assertEqual(summary["direction_change_count"], 3)
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
        self.assertAlmostEqual(summary["support_bias"] or 0.0, -0.2, places=6)
        self.assertEqual(summary["import_support_bias"], 0.0)
        self.assertAlmostEqual(summary["export_bias"] or 0.0, 1.0 / 3.0, places=6)
        self.assertEqual(summary["battery_first_export_bias"], 0.0)
        self.assertAlmostEqual(summary["day_support_bias"] or 0.0, -1.0 / 3.0, places=6)
        self.assertEqual(summary["night_support_bias"], 0.0)
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
