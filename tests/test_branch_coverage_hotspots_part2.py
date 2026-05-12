# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_branch_coverage_hotspots_support import *  # noqa: F401,F403

class _BranchCoverageVictronLearningCasesPart1:
    def test_victron_learning_telemetry_helper_branches(self) -> None:
        controller = _controller()
        service = SimpleNamespace(
            _victron_ess_balance_telemetry_settled_count=0,
            _victron_ess_balance_telemetry_overshoot_count=0,
            _victron_ess_balance_pid_last_error_w=5.0,
            _victron_ess_balance_pid_last_output_w=7.0,
        )
        controller._victron_ess_balance_increment_profile_counter = MagicMock()
        controller._victron_ess_balance_update_response_delay = MagicMock()
        controller._victron_ess_balance_update_gain = MagicMock()
        controller._victron_ess_balance_mark_overshoot = MagicMock()
        controller._enter_victron_ess_balance_overshoot_cooldown = MagicMock()

        controller._victron_ess_balance_mark_settled(service, "profile")
        self.assertTrue(service._victron_ess_balance_telemetry_command_settled_recorded)
        self.assertEqual(service._victron_ess_balance_telemetry_settled_count, 1)

        command_state = {"command_response_recorded": False}
        controller._victron_ess_balance_maybe_record_response_delay(service, 12.0, command_state, "profile", 25.0, 10.0, 7.0)
        controller._victron_ess_balance_update_response_delay.assert_called_once()
        self.assertTrue(command_state["command_response_recorded"])

        controller._victron_ess_balance_maybe_record_gain(service, "profile", 20.0, 10.0)
        controller._victron_ess_balance_update_gain.assert_called_once()

        already_recorded = {"command_overshoot_recorded": True}
        controller._victron_ess_balance_maybe_mark_overshoot(service, 10.0, -10.0, already_recorded, "profile", 5.0, 10.0, 2.0)
        controller._victron_ess_balance_mark_overshoot.assert_not_called()

        settled_state = {"command_settled_recorded": False}
        controller._victron_ess_balance_mark_settled = MagicMock()
        controller._victron_ess_balance_maybe_mark_settled(service, settled_state, "profile", 5.0, 10.0)
        controller._victron_ess_balance_mark_settled.assert_called_once_with(service, "profile")
        self.assertTrue(settled_state["command_settled_recorded"])

        self.assertEqual(controller._ewma_learned_value(8.0, 4.0, 2), 7.0)
        self.assertLess(controller._victron_ess_balance_variance_ratio(10.0, 1.0, 1.0), 1.0)
        self.assertLess(controller._victron_ess_balance_variance_score(10.0, 1.0, 2.0, 0.2), 1.0)
        self.assertLess(
            controller._victron_ess_balance_regime_consistency_score(
                {"delay_samples": 4, "stability_score": 0.8, "response_variance_score": 0.6}
            ),
            1.0,
        )
        self.assertLess(
            controller._victron_ess_balance_reproducibility_score(
                {"settled_count": 2, "overshoot_count": 1, "response_variance_score": 0.5}
            ),
            1.0,
        )
        self.assertAlmostEqual(controller._victron_ess_balance_stability_score_values(0, 0, None, None), 0.85)

        controller._record_victron_ess_balance_command(service, 100.0, 60.0, -30.0, "profile")
        self.assertEqual(service._victron_ess_balance_telemetry_last_command_profile_key, "profile")
        controller._clear_victron_ess_balance_tracking_episode(service)
        self.assertIsNone(service._victron_ess_balance_telemetry_last_command_at)
        controller._reset_victron_ess_balance_pid(service)
        self.assertEqual(service._victron_ess_balance_pid_last_output_w, 0.0)
        controller._reset_victron_ess_balance_pid_integral(service, aggressive=True)
        self.assertEqual(service._victron_ess_balance_pid_last_error_w, 0.0)

    def test_victron_learning_profile_helper_branches(self) -> None:
        controller = _controller()
        service = SimpleNamespace(
            auto_energy_sources=(SimpleNamespace(source_id="alpha"), SimpleNamespace(source_id=""),),
            auto_battery_discharge_balance_victron_bias_source_id="victron",
            auto_battery_discharge_balance_victron_bias_service="com.victronenergy.settings",
            auto_battery_discharge_balance_victron_bias_path="/Settings/CGwacs/AcPowerSetPoint",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.02,
            auto_battery_discharge_balance_victron_bias_kd=0.01,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=500.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=50.0,
            _victron_ess_balance_last_stable_tuning={"kp": 0.1},
            _victron_ess_balance_conservative_tuning={"kp": 0.05},
            _victron_ess_balance_learning_profiles={
                "profile": {
                    "key": "profile",
                    "action_direction": "more_export",
                    "site_regime": "export",
                    "direction": "export",
                    "day_phase": "day",
                    "reserve_phase": "above_reserve_band",
                    "ev_phase": "ev_idle",
                    "pv_phase": "pv_weak",
                    "battery_limit_phase": "mid_band",
                    "response_delay_seconds": 6.0,
                    "response_delay_mad_seconds": 1.0,
                    "delay_samples": 1,
                    "estimated_gain": 0.8,
                    "gain_mad": 0.1,
                    "gain_samples": 1,
                    "overshoot_count": 1,
                    "settled_count": 2,
                    "stability_score": 0.7,
                    "regime_consistency_score": 0.6,
                    "response_variance_score": 0.5,
                    "reproducibility_score": 0.4,
                    "safe_ramp_rate_watts_per_second": 40.0,
                    "preferred_bias_limit_watts": 300.0,
                }
            },
        )
        controller._victron_ess_balance_ev_active = MagicMock(return_value=False)

        self.assertEqual(controller._victron_ess_balance_action_direction(-1.0, 0.0, 0.0), "more_export")
        self.assertEqual(controller._victron_ess_balance_action_direction(1.0, 0.0, 0.0), "less_export")
        self.assertEqual(controller._victron_ess_balance_profile_limit_band(0.7, 0), "nominal")
        self.assertEqual(controller._victron_ess_balance_action_direction(0.0, 50.0, 10.0), "more_export")
        self.assertEqual(controller._victron_ess_balance_site_regime(None, 40.0, 0.0, "more_export"), "export")
        self.assertEqual(controller._victron_ess_balance_site_regime(30.0, 0.0, 0.0, "more_export"), "import")
        self.assertEqual(controller._victron_ess_balance_site_regime(None, 0.0, 50.0, "more_export"), "import")
        self.assertEqual(
            controller._victron_ess_balance_site_regime(None, 0.0, 0.0, "less_export"),
            "import",
        )
        self.assertEqual(
            controller._victron_ess_balance_reserve_phase(
                {"soc": 40.0, "discharge_balance_reserve_floor_soc": 35.0}
            ),
            "reserve_band",
        )
        self.assertEqual(
            controller._victron_ess_balance_battery_limit_phase("export", None, 200.0),
            "near_discharge_limit",
        )
        self.assertEqual(
            controller._victron_ess_balance_battery_limit_phase("import", 200.0, None),
            "near_charge_limit",
        )
        self.assertEqual(controller._ensure_victron_ess_balance_learning_profile_state(service, ""), {})
        controller._victron_ess_balance_update_profile_delay(service, "", 5.0)
        controller._victron_ess_balance_update_profile_gain(service, "", 0.5)
        controller._victron_ess_balance_increment_profile_counter(service, "", "overshoot_count")
        controller._victron_ess_balance_update_profile_delay(service, "profile", 9.0)
        controller._victron_ess_balance_update_profile_gain(service, "profile", 0.4)
        self.assertEqual(service._victron_ess_balance_learning_profiles["profile"]["delay_samples"], 2)
        self.assertEqual(service._victron_ess_balance_learning_profiles["profile"]["gain_samples"], 2)

        payload = controller.victron_ess_balance_learning_state_payload(service)
        self.assertIn("profile", payload["profiles"])
        adaptive = controller.victron_ess_balance_adaptive_tuning_payload(service)
        self.assertEqual(adaptive["last_stable_tuning"], {"kp": 0.1})
        self.assertEqual(adaptive["conservative_tuning"], {"kp": 0.05})

        identity_short = _victron_ess_balance_profile_identity("export:day:reserve")
        identity_min = _victron_ess_balance_profile_identity("import")
        identity_full = _victron_ess_balance_profile_identity(
            "more_export:export:day:above_reserve_band:ev_active:pv_strong:near_charge_limit"
        )
        class _EmptySplitKey(str):
            def split(self, _sep: str | None = None, _maxsplit: int = -1) -> list[str]:
                return []

        identity_none = _victron_ess_balance_profile_identity(_EmptySplitKey("ignored"))
        identity_empty = _victron_ess_balance_profile_identity("")
        self.assertEqual(identity_short["reserve_phase"], "reserve")
        self.assertEqual(identity_min["site_regime"], "import")
        self.assertEqual(identity_full["ev_phase"], "ev_active")
        self.assertEqual(identity_none["site_regime"], "")
        self.assertEqual(identity_empty["site_regime"], "")
        controller._victron_ess_balance_refresh_profile_stability(service, "missing")

    def test_victron_learning_profile_runtime_branches(self) -> None:
        controller = _controller()
        service = SimpleNamespace(
            auto_energy_sources=(SimpleNamespace(source_id="alpha"),),
            auto_battery_discharge_balance_victron_bias_source_id="victron",
            auto_battery_discharge_balance_victron_bias_service="com.victronenergy.settings",
            auto_battery_discharge_balance_victron_bias_path="/Settings/CGwacs/AcPowerSetPoint",
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=25.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=350.0,
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.02,
            auto_battery_discharge_balance_victron_bias_kd=0.01,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            _victron_ess_balance_learning_profiles={},
        )
        controller._victron_ess_balance_ev_active = MagicMock(return_value=True)
        cluster = {
            "battery_combined_grid_interaction_w": None,
            "expected_near_term_export_w": 80.0,
            "expected_near_term_import_w": 0.0,
            "battery_combined_pv_input_power_w": 1600.0,
            "battery_headroom_charge_w": 150.0,
            "battery_headroom_discharge_w": 200.0,
        }
        source = {"soc": 39.0, "discharge_balance_reserve_floor_soc": 35.0}
        learning_profile = controller._victron_ess_balance_learning_profile(service, cluster, source, 0.0)
        self.assertEqual(learning_profile["key"], "more_export:export:day:reserve_band:ev_active:pv_strong:near_discharge_limit")

        empty_service = SimpleNamespace()
        self.assertEqual(controller._victron_ess_balance_learning_profiles(empty_service), {})
        self.assertEqual(controller._victron_ess_balance_learning_profile_state(service, ""), {})
        created = controller._ensure_victron_ess_balance_learning_profile_state(service, "p:export:day:reserve")
        self.assertEqual(created["action_direction"], "p")
        self.assertEqual(created["site_regime"], "export")
        snapshot = controller._victron_ess_balance_profile_snapshot(service, "p:export:day:reserve")
        self.assertEqual(snapshot["sample_count"], 0)

        metrics: dict[str, object] = {}
        controller._merge_victron_ess_balance_learning_profile_metrics(service, metrics, "p:export:day:reserve")
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_learning_profile_sample_count"], 0)
        controller._set_victron_ess_balance_active_profile(service, learning_profile)
        self.assertEqual(service._victron_ess_balance_active_learning_profile_ev_phase, "ev_active")
        controller._clear_victron_ess_balance_active_profile(service)
        self.assertEqual(service._victron_ess_balance_active_learning_profile_key, "")

        profile_key = "p:export:day:reserve"
        service._victron_ess_balance_learning_profiles[profile_key].update(
            {
                "estimated_gain": 0.7,
                "response_delay_seconds": 5.0,
                "gain_mad": 0.1,
                "response_delay_mad_seconds": 0.5,
                "overshoot_count": 1,
                "settled_count": 2,
            }
        )
        controller._victron_ess_balance_refresh_profile_stability(service, profile_key)
        refreshed = service._victron_ess_balance_learning_profiles[profile_key]
        self.assertIsNotNone(refreshed["stability_score"])
        self.assertIsNotNone(refreshed["preferred_bias_limit_watts"])
        self.assertEqual(controller._victron_ess_balance_adaptive_scalar_value(True, "bool"), True)
        self.assertEqual(controller._victron_ess_balance_current_tuning_snapshot(service)["activation_mode"], "always")
        self.assertEqual(controller._victron_ess_balance_reserve_phase({}), "above_reserve_band")
        self.assertEqual(controller._victron_ess_balance_battery_limit_phase("idle", None, None), "mid_band")
        self.assertEqual(controller._victron_ess_balance_action_direction(0.0, 0.0, 50.0), "less_export")
        self.assertEqual(controller._victron_ess_balance_site_regime(-30.0, 0.0, 0.0, "less_export"), "export")
        self.assertEqual(controller._victron_ess_balance_profile_sample_count({}), 0)
        self.assertEqual(controller._victron_ess_balance_profile_snapshot(service, "missing"), {})
        self.assertIsNone(controller._victron_ess_balance_learning_profile_state(service, "missing").get("key"))



