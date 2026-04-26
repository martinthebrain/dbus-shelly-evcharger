# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from venus_evcharger.core.contracts import (
    STATE_API_KINDS,
    STATE_API_VERSIONS,
    normalized_state_api_config_effective_fields,
    normalized_state_api_health_fields,
    normalized_state_api_kind,
    normalized_state_api_operational_fields,
    normalized_state_api_operational_state_fields,
    normalized_state_api_runtime_fields,
    normalized_state_api_summary_fields,
    normalized_state_api_topology_fields,
    normalized_state_api_update_fields,
    normalized_state_api_version,
)


class TestVenusEvchargerStateContracts(unittest.TestCase):
    def test_state_contract_constants_and_version_kind_normalizers_are_stable(self) -> None:
        self.assertEqual(STATE_API_VERSIONS, frozenset({"v1"}))
        self.assertEqual(
            STATE_API_KINDS,
            frozenset(
                {
                    "build",
                    "contracts",
                    "healthz",
                    "summary",
                    "runtime",
                    "victron-bias-recommendation",
                    "operational",
                    "dbus-diagnostics",
                    "topology",
                    "update",
                    "config-effective",
                    "health",
                    "version",
                }
            ),
        )
        self.assertEqual(normalized_state_api_version(" v1 "), "v1")
        self.assertEqual(normalized_state_api_version("v2"), "v1")
        self.assertEqual(normalized_state_api_kind(" SUMMARY "), "summary")
        self.assertEqual(normalized_state_api_kind("other", default="runtime"), "runtime")

    def test_state_api_summary_and_runtime_fields_normalize_outer_shape(self) -> None:
        summary = normalized_state_api_summary_fields(
            {
                "ok": 1,
                "api_version": " v1 ",
                "kind": "summary",
                "summary": "  mode=1  ",
            }
        )
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["api_version"], "v1")
        self.assertEqual(summary["kind"], "summary")
        self.assertEqual(summary["summary"], "mode=1")

        runtime = normalized_state_api_runtime_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "runtime",
                "state": {"mode": 1, "autostart": 1},
            }
        )
        self.assertTrue(runtime["ok"])
        self.assertEqual(runtime["kind"], "runtime")
        self.assertEqual(runtime["state"], {"mode": 1, "autostart": 1})
        self.assertEqual(normalized_state_api_runtime_fields({"state": []})["state"], {})

    def test_operational_state_contract_normalizes_known_fields(self) -> None:
        state = normalized_state_api_operational_state_fields(
            {
                "mode": "2",
                "enable": 1,
                "startstop": 0,
                "autostart": 1,
                "active_phase_selection": " P1_P2 ",
                "requested_phase_selection": "",
                "backend_mode": " split ",
                "meter_backend": " template_meter ",
                "switch_backend": "",
                "charger_backend": None,
                "auto_state": " charging ",
                "auto_state_code": 999,
                "fault_active": 0,
                "fault_reason": "",
                "software_update_state": "available",
                "software_update_available": 1,
                "software_update_no_update_active": 1,
                "runtime_overrides_active": 1,
                "runtime_overrides_path": " /run/x.ini ",
                "combined_battery_headroom_charge_w": "1200",
                "combined_battery_headroom_discharge_w": "1800",
                "expected_near_term_export_w": "425",
                "expected_near_term_import_w": "50",
                "battery_discharge_balance_mode": " capacity_reserve_weighted ",
                "battery_discharge_balance_target_distribution_mode": " capacity_reserve_weighted ",
                "battery_discharge_balance_error_w": "250",
                "battery_discharge_balance_max_abs_error_w": "400",
                "battery_discharge_balance_total_discharge_w": "1200",
                "battery_discharge_balance_eligible_source_count": "2",
                "battery_discharge_balance_active_source_count": "1",
                "battery_discharge_balance_control_candidate_count": "1",
                "battery_discharge_balance_control_ready_count": "1",
                "battery_discharge_balance_supported_control_source_count": "0",
                "battery_discharge_balance_experimental_control_source_count": "1",
                "battery_discharge_balance_policy_enabled": 1,
                "battery_discharge_balance_warning_active": 1,
                "battery_discharge_balance_warning_error_w": "250",
                "battery_discharge_balance_warn_threshold_w": "400",
                "battery_discharge_balance_bias_mode": " export_only ",
                "battery_discharge_balance_bias_gate_active": 1,
                "battery_discharge_balance_bias_start_error_w": "500",
                "battery_discharge_balance_bias_penalty_w": "125",
                "battery_discharge_balance_coordination_policy_enabled": 1,
                "battery_discharge_balance_coordination_support_mode": " supported_only ",
                "battery_discharge_balance_coordination_feasibility": " partial ",
                "battery_discharge_balance_coordination_gate_active": 0,
                "battery_discharge_balance_coordination_start_error_w": "900",
                "battery_discharge_balance_coordination_penalty_w": "0",
                "battery_discharge_balance_coordination_advisory_active": 1,
                "battery_discharge_balance_coordination_advisory_reason": " only_some_sources_offer_a_write_path ",
                "battery_discharge_balance_victron_bias_enabled": 1,
                "battery_discharge_balance_victron_bias_active": 1,
                "battery_discharge_balance_victron_bias_source_id": " victron ",
                "battery_discharge_balance_victron_bias_topology_key": " topo ",
                "battery_discharge_balance_victron_bias_activation_mode": " export_and_above_reserve_band ",
                "battery_discharge_balance_victron_bias_activation_gate_active": 1,
                "battery_discharge_balance_victron_bias_support_mode": " supported_only ",
                "battery_discharge_balance_victron_bias_learning_profile_key": " more_export:export:day:above_reserve_band ",
                "battery_discharge_balance_victron_bias_learning_profile_action_direction": " more_export ",
                "battery_discharge_balance_victron_bias_learning_profile_site_regime": " export ",
                "battery_discharge_balance_victron_bias_learning_profile_direction": " export ",
                "battery_discharge_balance_victron_bias_learning_profile_day_phase": " day ",
                "battery_discharge_balance_victron_bias_learning_profile_reserve_phase": " above_reserve_band ",
                "battery_discharge_balance_victron_bias_learning_profile_sample_count": "3",
                "battery_discharge_balance_victron_bias_learning_profile_response_delay_seconds": "4",
                "battery_discharge_balance_victron_bias_learning_profile_estimated_gain": "2.5",
                "battery_discharge_balance_victron_bias_learning_profile_overshoot_count": "0",
                "battery_discharge_balance_victron_bias_learning_profile_settled_count": "3",
                "battery_discharge_balance_victron_bias_learning_profile_stability_score": "0.9",
                "battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second": "60",
                "battery_discharge_balance_victron_bias_learning_profile_preferred_bias_limit_watts": "550",
                "battery_discharge_balance_victron_bias_source_error_w": "-320",
                "battery_discharge_balance_victron_bias_pid_output_w": "-64",
                "battery_discharge_balance_victron_bias_setpoint_w": "-14",
                "battery_discharge_balance_victron_bias_telemetry_clean": 1,
                "battery_discharge_balance_victron_bias_telemetry_clean_reason": " clean ",
                "battery_discharge_balance_victron_bias_response_delay_seconds": "4",
                "battery_discharge_balance_victron_bias_estimated_gain": "2.5",
                "battery_discharge_balance_victron_bias_overshoot_active": 1,
                "battery_discharge_balance_victron_bias_overshoot_count": "2",
                "battery_discharge_balance_victron_bias_settling_active": 0,
                "battery_discharge_balance_victron_bias_settled_count": "3",
                "battery_discharge_balance_victron_bias_stability_score": "0.82",
                "battery_discharge_balance_victron_bias_oscillation_lockout_enabled": 1,
                "battery_discharge_balance_victron_bias_oscillation_lockout_active": 0,
                "battery_discharge_balance_victron_bias_oscillation_lockout_reason": " stable ",
                "battery_discharge_balance_victron_bias_oscillation_lockout_until": "1234",
                "battery_discharge_balance_victron_bias_oscillation_direction_change_count": "2",
                "battery_discharge_balance_victron_bias_recommended_kp": "0.23",
                "battery_discharge_balance_victron_bias_recommended_ki": "0.022",
                "battery_discharge_balance_victron_bias_recommended_kd": "0.01",
                "battery_discharge_balance_victron_bias_recommended_deadband_watts": "90",
                "battery_discharge_balance_victron_bias_recommended_max_abs_watts": "550",
                "battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second": "55",
                "battery_discharge_balance_victron_bias_recommended_activation_mode": " export_and_above_reserve_band ",
                "battery_discharge_balance_victron_bias_recommendation_confidence": "0.81",
                "battery_discharge_balance_victron_bias_recommendation_reason": " can_relax_conservatism ",
                "battery_discharge_balance_victron_bias_recommendation_profile_key": " more_export:export:day:above_reserve_band ",
                "battery_discharge_balance_victron_bias_recommendation_ini_snippet": (
                    " AutoBatteryDischargeBalanceVictronBiasKp=0.23\n"
                    "AutoBatteryDischargeBalanceVictronBiasKi=0.022\n"
                    "AutoBatteryDischargeBalanceVictronBiasKd=0.01\n"
                    "AutoBatteryDischargeBalanceVictronBiasDeadbandWatts=90\n"
                    "AutoBatteryDischargeBalanceVictronBiasMaxAbsWatts=550\n"
                    "AutoBatteryDischargeBalanceVictronBiasRampRateWattsPerSecond=55\n"
                    "AutoBatteryDischargeBalanceVictronBiasActivationMode=export_and_above_reserve_band "
                ),
                "battery_discharge_balance_victron_bias_recommendation_hint": (
                    " Telemetry looks stable; you can cautiously relax the current "
                    "Victron bias tuning (confidence 0.81). "
                ),
                "battery_discharge_balance_victron_bias_auto_apply_enabled": 1,
                "battery_discharge_balance_victron_bias_auto_apply_active": 1,
                "battery_discharge_balance_victron_bias_auto_apply_reason": " applied ",
                "battery_discharge_balance_victron_bias_auto_apply_generation": "2",
                "battery_discharge_balance_victron_bias_auto_apply_observation_window_active": 1,
                "battery_discharge_balance_victron_bias_auto_apply_observation_window_until": "2345",
                "battery_discharge_balance_victron_bias_auto_apply_last_param": (
                    " auto_battery_discharge_balance_victron_bias_deadband_watts "
                ),
                "battery_discharge_balance_victron_bias_rollback_enabled": 1,
                "battery_discharge_balance_victron_bias_rollback_active": 0,
                "battery_discharge_balance_victron_bias_rollback_reason": " stable ",
                "battery_discharge_balance_victron_bias_rollback_stable_profile_key": (
                    " more_export:export:day:above_reserve_band "
                ),
                "battery_discharge_balance_victron_bias_reason": " applied ",
                "combined_battery_average_active_power_delta_w": "120",
                "combined_battery_power_smoothing_ratio": "0.8",
                "combined_battery_day_support_bias": -0.4,
                "combined_battery_night_support_bias": 0.6,
                "combined_battery_battery_first_export_bias": 0.2,
                "combined_battery_reserve_band_floor_soc": "35",
                "combined_battery_reserve_band_ceiling_soc": "85",
                "combined_battery_reserve_band_width_soc": "50",
            }
        )
        self.assertEqual(state["mode"], 2)
        self.assertEqual(state["enable"], 1)
        self.assertEqual(state["requested_phase_selection"], "P1")
        self.assertEqual(state["backend_mode"], "split")
        self.assertEqual(state["switch_backend"], "na")
        self.assertEqual(state["charger_backend"], "na")
        self.assertEqual(state["auto_state"], "charging")
        self.assertEqual(state["auto_state_code"], 3)
        self.assertEqual(state["fault_reason"], "na")
        self.assertEqual(state["software_update_state"], "available-blocked")
        self.assertEqual(state["software_update_state_code"], 4)
        self.assertEqual(state["runtime_overrides_path"], "/run/x.ini")
        self.assertEqual(state["combined_battery_headroom_charge_w"], 1200.0)
        self.assertEqual(state["combined_battery_headroom_discharge_w"], 1800.0)
        self.assertEqual(state["expected_near_term_export_w"], 425.0)
        self.assertEqual(state["expected_near_term_import_w"], 50.0)
        self.assertEqual(state["battery_discharge_balance_mode"], "capacity_reserve_weighted")
        self.assertEqual(state["battery_discharge_balance_target_distribution_mode"], "capacity_reserve_weighted")
        self.assertEqual(state["battery_discharge_balance_error_w"], 250.0)
        self.assertEqual(state["battery_discharge_balance_max_abs_error_w"], 400.0)
        self.assertEqual(state["battery_discharge_balance_total_discharge_w"], 1200.0)
        self.assertEqual(state["battery_discharge_balance_eligible_source_count"], 2)
        self.assertEqual(state["battery_discharge_balance_active_source_count"], 1)
        self.assertEqual(state["battery_discharge_balance_control_candidate_count"], 1)
        self.assertEqual(state["battery_discharge_balance_control_ready_count"], 1)
        self.assertEqual(state["battery_discharge_balance_supported_control_source_count"], 0)
        self.assertEqual(state["battery_discharge_balance_experimental_control_source_count"], 1)
        self.assertEqual(state["battery_discharge_balance_policy_enabled"], 1)
        self.assertEqual(state["battery_discharge_balance_warning_active"], 1)
        self.assertEqual(state["battery_discharge_balance_warning_error_w"], 250.0)
        self.assertEqual(state["battery_discharge_balance_warn_threshold_w"], 400.0)
        self.assertEqual(state["battery_discharge_balance_bias_mode"], "export_only")
        self.assertEqual(state["battery_discharge_balance_bias_gate_active"], 1)
        self.assertEqual(state["battery_discharge_balance_bias_start_error_w"], 500.0)
        self.assertEqual(state["battery_discharge_balance_bias_penalty_w"], 125.0)
        self.assertEqual(state["battery_discharge_balance_coordination_policy_enabled"], 1)
        self.assertEqual(state["battery_discharge_balance_coordination_support_mode"], "supported_only")
        self.assertEqual(state["battery_discharge_balance_coordination_feasibility"], "partial")
        self.assertEqual(state["battery_discharge_balance_coordination_gate_active"], 0)
        self.assertEqual(state["battery_discharge_balance_coordination_start_error_w"], 900.0)
        self.assertEqual(state["battery_discharge_balance_coordination_penalty_w"], 0.0)
        self.assertEqual(state["battery_discharge_balance_coordination_advisory_active"], 1)
        self.assertEqual(
            state["battery_discharge_balance_coordination_advisory_reason"],
            "only_some_sources_offer_a_write_path",
        )
        self.assertEqual(state["battery_discharge_balance_victron_bias_enabled"], 1)
        self.assertEqual(state["battery_discharge_balance_victron_bias_active"], 1)
        self.assertEqual(state["battery_discharge_balance_victron_bias_source_id"], "victron")
        self.assertEqual(state["battery_discharge_balance_victron_bias_topology_key"], "topo")
        self.assertEqual(state["battery_discharge_balance_victron_bias_activation_mode"], "export_and_above_reserve_band")
        self.assertEqual(state["battery_discharge_balance_victron_bias_activation_gate_active"], 1)
        self.assertEqual(state["battery_discharge_balance_victron_bias_support_mode"], "supported_only")
        self.assertEqual(
            state["battery_discharge_balance_victron_bias_learning_profile_key"],
            "more_export:export:day:above_reserve_band",
        )
        self.assertEqual(state["battery_discharge_balance_victron_bias_learning_profile_action_direction"], "more_export")
        self.assertEqual(state["battery_discharge_balance_victron_bias_learning_profile_site_regime"], "export")
        self.assertEqual(state["battery_discharge_balance_victron_bias_learning_profile_direction"], "export")
        self.assertEqual(state["battery_discharge_balance_victron_bias_learning_profile_day_phase"], "day")
        self.assertEqual(
            state["battery_discharge_balance_victron_bias_learning_profile_reserve_phase"],
            "above_reserve_band",
        )
        self.assertEqual(state["battery_discharge_balance_victron_bias_learning_profile_sample_count"], 3)
        self.assertEqual(state["battery_discharge_balance_victron_bias_learning_profile_response_delay_seconds"], 4.0)
        self.assertEqual(state["battery_discharge_balance_victron_bias_learning_profile_estimated_gain"], 2.5)
        self.assertEqual(state["battery_discharge_balance_victron_bias_learning_profile_overshoot_count"], 0)
        self.assertEqual(state["battery_discharge_balance_victron_bias_learning_profile_settled_count"], 3)
        self.assertEqual(state["battery_discharge_balance_victron_bias_learning_profile_stability_score"], 0.9)
        self.assertEqual(state["battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second"], 60.0)
        self.assertEqual(state["battery_discharge_balance_victron_bias_learning_profile_preferred_bias_limit_watts"], 550.0)
        self.assertEqual(state["battery_discharge_balance_victron_bias_source_error_w"], -320.0)
        self.assertEqual(state["battery_discharge_balance_victron_bias_pid_output_w"], -64.0)
        self.assertEqual(state["battery_discharge_balance_victron_bias_setpoint_w"], -14.0)
        self.assertEqual(state["battery_discharge_balance_victron_bias_telemetry_clean"], 1)
        self.assertEqual(state["battery_discharge_balance_victron_bias_telemetry_clean_reason"], "clean")
        self.assertEqual(state["battery_discharge_balance_victron_bias_response_delay_seconds"], 4.0)
        self.assertEqual(state["battery_discharge_balance_victron_bias_estimated_gain"], 2.5)
        self.assertEqual(state["battery_discharge_balance_victron_bias_overshoot_active"], 1)
        self.assertEqual(state["battery_discharge_balance_victron_bias_overshoot_count"], 2)
        self.assertEqual(state["battery_discharge_balance_victron_bias_settling_active"], 0)
        self.assertEqual(state["battery_discharge_balance_victron_bias_settled_count"], 3)
        self.assertEqual(state["battery_discharge_balance_victron_bias_stability_score"], 0.82)
        self.assertEqual(state["battery_discharge_balance_victron_bias_oscillation_lockout_enabled"], 1)
        self.assertEqual(state["battery_discharge_balance_victron_bias_oscillation_lockout_active"], 0)
        self.assertEqual(state["battery_discharge_balance_victron_bias_oscillation_lockout_reason"], "stable")
        self.assertEqual(state["battery_discharge_balance_victron_bias_oscillation_lockout_until"], 1234.0)
        self.assertEqual(state["battery_discharge_balance_victron_bias_oscillation_direction_change_count"], 2)
        self.assertEqual(state["battery_discharge_balance_victron_bias_recommended_kp"], 0.23)
        self.assertEqual(state["battery_discharge_balance_victron_bias_recommended_ki"], 0.022)
        self.assertEqual(state["battery_discharge_balance_victron_bias_recommended_kd"], 0.01)
        self.assertEqual(state["battery_discharge_balance_victron_bias_recommended_deadband_watts"], 90.0)
        self.assertEqual(state["battery_discharge_balance_victron_bias_recommended_max_abs_watts"], 550.0)
        self.assertEqual(state["battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second"], 55.0)
        self.assertEqual(state["battery_discharge_balance_victron_bias_recommended_activation_mode"], "export_and_above_reserve_band")
        self.assertEqual(state["battery_discharge_balance_victron_bias_recommendation_confidence"], 0.81)
        self.assertEqual(state["battery_discharge_balance_victron_bias_recommendation_reason"], "can_relax_conservatism")
        self.assertEqual(
            state["battery_discharge_balance_victron_bias_recommendation_profile_key"],
            "more_export:export:day:above_reserve_band",
        )
        self.assertEqual(
            state["battery_discharge_balance_victron_bias_recommendation_ini_snippet"],
            "AutoBatteryDischargeBalanceVictronBiasKp=0.23\n"
            "AutoBatteryDischargeBalanceVictronBiasKi=0.022\n"
            "AutoBatteryDischargeBalanceVictronBiasKd=0.01\n"
            "AutoBatteryDischargeBalanceVictronBiasDeadbandWatts=90\n"
            "AutoBatteryDischargeBalanceVictronBiasMaxAbsWatts=550\n"
            "AutoBatteryDischargeBalanceVictronBiasRampRateWattsPerSecond=55\n"
            "AutoBatteryDischargeBalanceVictronBiasActivationMode=export_and_above_reserve_band",
        )
        self.assertEqual(
            state["battery_discharge_balance_victron_bias_recommendation_hint"],
            "Telemetry looks stable; you can cautiously relax the current "
            "Victron bias tuning (confidence 0.81).",
        )
        self.assertEqual(state["battery_discharge_balance_victron_bias_auto_apply_enabled"], 1)
        self.assertEqual(state["battery_discharge_balance_victron_bias_auto_apply_active"], 1)
        self.assertEqual(state["battery_discharge_balance_victron_bias_auto_apply_reason"], "applied")
        self.assertEqual(state["battery_discharge_balance_victron_bias_auto_apply_generation"], 2)
        self.assertEqual(state["battery_discharge_balance_victron_bias_auto_apply_observation_window_active"], 1)
        self.assertEqual(state["battery_discharge_balance_victron_bias_auto_apply_observation_window_until"], 2345.0)
        self.assertEqual(
            state["battery_discharge_balance_victron_bias_auto_apply_last_param"],
            "auto_battery_discharge_balance_victron_bias_deadband_watts",
        )
        self.assertEqual(state["battery_discharge_balance_victron_bias_rollback_enabled"], 1)
        self.assertEqual(state["battery_discharge_balance_victron_bias_rollback_active"], 0)
        self.assertEqual(state["battery_discharge_balance_victron_bias_rollback_reason"], "stable")
        self.assertEqual(
            state["battery_discharge_balance_victron_bias_rollback_stable_profile_key"],
            "more_export:export:day:above_reserve_band",
        )
        self.assertEqual(state["battery_discharge_balance_victron_bias_reason"], "applied")
        self.assertEqual(state["combined_battery_average_active_power_delta_w"], 120.0)
        self.assertEqual(state["combined_battery_power_smoothing_ratio"], 0.8)
        self.assertEqual(state["combined_battery_day_support_bias"], -0.4)
        self.assertEqual(state["combined_battery_night_support_bias"], 0.6)
        self.assertEqual(state["combined_battery_battery_first_export_bias"], 0.2)
        self.assertEqual(state["combined_battery_reserve_band_floor_soc"], 35.0)
        self.assertEqual(state["combined_battery_reserve_band_ceiling_soc"], 85.0)
        self.assertEqual(state["combined_battery_reserve_band_width_soc"], 50.0)

    def test_operational_envelope_wraps_normalized_state(self) -> None:
        payload = normalized_state_api_operational_fields(
            {
                "ok": 1,
                "api_version": " v1 ",
                "kind": "operational",
                "state": {"mode": 1, "auto_state": "idle"},
            }
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["api_version"], "v1")
        self.assertEqual(payload["kind"], "operational")
        self.assertEqual(payload["state"]["mode"], 1)
        self.assertEqual(payload["state"]["auto_state"], "idle")

    def test_dbus_diagnostics_envelope_normalizes_mapping_keys(self) -> None:
        from venus_evcharger.core.contracts import normalized_state_api_dbus_diagnostics_fields

        payload = normalized_state_api_dbus_diagnostics_fields(
            {
                "ok": 1,
                "api_version": "v1",
                "kind": "dbus-diagnostics",
                "state": {
                    "/Auto/State": "charging",
                    123: 456,
                },
            }
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["kind"], "dbus-diagnostics")
        self.assertEqual(payload["state"]["/Auto/State"], "charging")
        self.assertEqual(payload["state"]["123"], 456)

    def test_dbus_diagnostics_envelope_falls_back_to_empty_state_for_non_mapping(self) -> None:
        from venus_evcharger.core.contracts import normalized_state_api_dbus_diagnostics_fields

        payload = normalized_state_api_dbus_diagnostics_fields(
            {
                "ok": 1,
                "api_version": "v1",
                "kind": "dbus-diagnostics",
                "state": "not-a-mapping",
            }
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["kind"], "dbus-diagnostics")
        self.assertEqual(payload["state"], {})

    def test_additional_state_envelopes_normalize_generic_mappings(self) -> None:
        topology = normalized_state_api_topology_fields(
            {
                "ok": 1,
                "api_version": "v1",
                "kind": "topology",
                "state": {"backend_mode": "split", "available_modes": [0, 1, 2]},
            }
        )
        update = normalized_state_api_update_fields(
            {
                "ok": 1,
                "api_version": "v1",
                "kind": "update",
                "state": {
                    "available": 1,
                    "no_update_active": 0,
                    "last_check_at": "12.5",
                },
            }
        )
        config_effective = normalized_state_api_config_effective_fields(
            {
                "ok": 1,
                "api_version": "v1",
                "kind": "config-effective",
                "state": {"host": "charger.local"},
            }
        )
        health = normalized_state_api_health_fields(
            {
                "ok": 1,
                "api_version": "v1",
                "kind": "health",
                "state": {
                    "health_code": "3",
                    "fault_active": 1,
                    "listen_port": "8765",
                    "update_stale": 0,
                },
            }
        )

        self.assertEqual(topology["kind"], "topology")
        self.assertEqual(topology["state"]["backend_mode"], "split")
        self.assertTrue(update["state"]["available"])
        self.assertAlmostEqual(update["state"]["last_check_at"], 12.5)
        self.assertEqual(config_effective["state"]["host"], "charger.local")
        self.assertEqual(health["state"]["health_code"], 3)
        self.assertTrue(health["state"]["fault_active"])
        self.assertEqual(health["state"]["listen_port"], 8765)
        self.assertFalse(health["state"]["update_stale"])

    def test_update_and_health_envelopes_handle_empty_state_without_extra_keys(self) -> None:
        update = normalized_state_api_update_fields(
            {
                "ok": 1,
                "api_version": "v1",
                "kind": "update",
                "state": {},
            }
        )
        health = normalized_state_api_health_fields(
            {
                "ok": 1,
                "api_version": "v1",
                "kind": "health",
                "state": {},
            }
        )

        self.assertEqual(update["state"], {})
        self.assertEqual(health["state"], {})
