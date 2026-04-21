# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from venus_evcharger.energy import (
    EnergySourceSnapshot,
    aggregate_energy_sources,
    load_energy_source_settings,
    summarize_energy_learning_profiles,
    update_energy_learning_profiles,
)


class TestVenusEvchargerEnergyAggregate(unittest.TestCase):
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
                    online=True,
                    confidence=1.0,
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
        self.assertEqual(cluster.source_count, 2)
        self.assertEqual(cluster.online_source_count, 2)
        self.assertEqual(cluster.valid_soc_source_count, 2)

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
                "AutoEnergySource.hybrid.Service": "com.victronenergy.hybrid.hybrid",
                "AutoEnergySource.hybrid.SocPath": "/Soc",
                "AutoEnergySource.hybrid.UsableCapacityWh": "10000",
                "AutoEnergySource.hybrid.BatteryPowerPath": "/Dc/0/Power",
                "AutoEnergySource.hybrid.AcPowerPath": "/Ac/Power",
            }
        )
        self.assertFalse(use_combined)
        self.assertEqual([source.source_id for source in configured_sources], ["victron", "hybrid"])
        self.assertEqual(configured_sources[1].role, "hybrid-inverter")
        self.assertEqual(configured_sources[1].battery_power_path, "/Dc/0/Power")

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
                    online=True,
                    confidence=1.0,
                    captured_at=100.0,
                ),
            ),
            100.0,
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
                    online=True,
                    confidence=1.0,
                    captured_at=110.0,
                ),
            ),
            110.0,
        )

        profile = profiles["hybrid"]
        self.assertEqual(profile.sample_count, 2)
        self.assertEqual(profile.observed_max_discharge_power_w, 1500.0)
        self.assertEqual(profile.observed_max_charge_power_w, 1800.0)
        self.assertEqual(profile.observed_max_ac_power_w, 3500.0)
        self.assertEqual(profile.last_change_at, 110.0)

    def test_summarize_energy_learning_profiles_sums_observed_maxima(self) -> None:
        summary = summarize_energy_learning_profiles(
            {
                "victron": {
                    "sample_count": 2,
                    "observed_max_charge_power_w": 700.0,
                    "observed_max_discharge_power_w": 900.0,
                    "observed_max_ac_power_w": 1100.0,
                },
                "hybrid": {
                    "sample_count": 3,
                    "observed_max_charge_power_w": 500.0,
                    "observed_max_discharge_power_w": 1300.0,
                    "observed_max_ac_power_w": 1500.0,
                },
            }
        )

        self.assertEqual(summary["profile_count"], 2)
        self.assertEqual(summary["sample_count"], 5)
        self.assertEqual(summary["observed_max_charge_power_w"], 1200.0)
        self.assertEqual(summary["observed_max_discharge_power_w"], 2200.0)
        self.assertEqual(summary["observed_max_ac_power_w"], 2600.0)
