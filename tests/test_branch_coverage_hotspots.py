# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.venus_evcharger_update_cycle_controller_support import UpdateCycleController, _phase_values
from venus_evcharger.bootstrap.wizard_energy import (
    _config_auto_energy_sources_value,
    _structured_energy_source_line,
    _structured_energy_source_value,
    build_suggested_energy_merge,
    bundle_block_label,
    bundle_labels,
    bundle_target_names,
    bundle_source_id,
    energy_source_capacity_follow_up,
    energy_source_merge_lines,
    existing_auto_energy_assignments,
    existing_auto_energy_source_ids,
    existing_source_ids_from_assignments,
    huawei_bundle_files,
    manual_review_union,
    merge_energy_source_ids,
    merged_recommendation_prefixes,
    normalized_recommendation_prefixes,
    optional_capacity_wh,
    structured_energy_source_from_block,
    suggested_energy_assignments,
    suggested_energy_merge_lines,
    suggested_energy_sources_with_capacity,
    suggested_energy_sources_with_capacity_overrides,
    validate_unique_suggested_energy_sources,
)
from venus_evcharger.bootstrap.wizard_render import (
    _remaining_default_assignment_lines,
    live_check_rendered_setup,
    live_connectivity_payload,
    upsert_default_assignments,
)
from venus_evcharger.update.victron_ess_balance_learning_profiles import (
    _victron_ess_balance_profile_identity,
)
from venus_evcharger.update.victron_ess_balance_learning_telemetry import (
    _UpdateCycleVictronEssBalanceLearningTelemetryMixin,
)


def _controller() -> UpdateCycleController:
    return UpdateCycleController(SimpleNamespace(), _phase_values, lambda _reason: 0)


class _TelemetryHarness(_UpdateCycleVictronEssBalanceLearningTelemetryMixin):
    @staticmethod
    def _optional_float(value: object) -> float | None:
        if not isinstance(value, (int, float)):
            return None
        return float(value)

    _ewma_learned_value = staticmethod(_UpdateCycleVictronEssBalanceLearningTelemetryMixin._ewma_learned_value)

    def __init__(self) -> None:
        self.delay_updates: list[tuple[str, float]] = []
        self.gain_updates: list[tuple[str, float]] = []
        self.counter_updates: list[tuple[str, str]] = []
        self.cooldowns: list[tuple[float, str]] = []
        self.metrics_calls = 0
        self.refreshed_profiles: list[str] = []

    def _victron_ess_balance_update_profile_delay(self, svc: object, profile_key: str, sample: float) -> None:
        self.delay_updates.append((profile_key, sample))

    def _victron_ess_balance_update_profile_gain(self, svc: object, profile_key: str, sample: float) -> None:
        self.gain_updates.append((profile_key, sample))

    def _victron_ess_balance_increment_profile_counter(self, svc: object, profile_key: str, field: str) -> None:
        self.counter_updates.append((profile_key, field))

    def _enter_victron_ess_balance_overshoot_cooldown(self, svc: object, now: float, reason: str) -> None:
        self.cooldowns.append((now, reason))

    def _victron_ess_balance_profile_sample_count(self, profile: dict[str, object]) -> int:
        return max(0, int(profile.get("delay_samples", 0) or 0))

    def _victron_ess_balance_telemetry_is_clean(
        self,
        svc: object,
        cluster: dict[str, object],
        source_error_w: float,
    ) -> tuple[bool, str]:
        return True, "clean"

    def _victron_ess_balance_ev_power_w(self, svc: object) -> float:
        return 0.0

    def _victron_ess_balance_refresh_profile_stability(self, svc: object, profile_key: str) -> None:
        self.refreshed_profiles.append(profile_key)

    def _populate_victron_ess_balance_telemetry_metrics(self, svc: object, metrics: dict[str, object]) -> None:
        self.metrics_calls += 1
        metrics["telemetry_metrics_populated"] = True


class BranchCoverageWizardEnergyCases(unittest.TestCase):
    def test_wizard_energy_helper_branches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            missing_config = temp_path / "missing.ini"
            self.assertEqual(_config_auto_energy_sources_value(missing_config), "")
            config_path = temp_path / "existing.ini"
            config_path.write_text("[DEFAULT]\nAutoEnergySources=grid, alpha\n", encoding="utf-8")
            self.assertEqual(_config_auto_energy_sources_value(config_path), "")
            self.assertEqual(existing_auto_energy_source_ids(config_path), ())
            config_path.write_text("[DEFAULT]\nautoenergysources=grid,alpha\nOther=value\n", encoding="utf-8")
            self.assertEqual(existing_auto_energy_assignments(config_path), {})
            self.assertEqual(existing_auto_energy_assignments(temp_path / "missing-assignments.ini"), {})
            with patch("configparser.ConfigParser.defaults", return_value={"AutoEnergySources": "grid,alpha"}):
                self.assertEqual(_config_auto_energy_sources_value(config_path), "grid,alpha")
            with patch("configparser.ConfigParser.defaults", return_value={}), patch(
                "configparser.ConfigParser.has_section", return_value=True
            ), patch("configparser.ConfigParser.__getitem__", return_value={"AutoEnergySources": "fallback"}):
                self.assertEqual(_config_auto_energy_sources_value(config_path), "fallback")
            self.assertEqual(normalized_recommendation_prefixes(None), ())
            self.assertEqual(normalized_recommendation_prefixes(" pref "), ("pref",))
            self.assertEqual(normalized_recommendation_prefixes([" a ", " ", "b "]), ("a", "b"))
            self.assertEqual(
                merged_recommendation_prefixes(("a", "b"), ["b", "c"], None, " d "),
                ("a", "b", "c", "d"),
            )
            self.assertIsNone(optional_capacity_wh("bad"))

            source = {"source_id": "alpha", "capacityConfigKey": "AutoEnergySource.alpha.UsableCapacityWh"}
            unchanged = suggested_energy_sources_with_capacity((source,), None)
            self.assertEqual(unchanged, (source,))
            self.assertEqual(suggested_energy_sources_with_capacity(({"source_id": "plain"},), 2048.0), ({"source_id": "plain"},))

            with self.assertRaisesRegex(ValueError, "unknown source ids"):
                suggested_energy_sources_with_capacity_overrides((source,), {"beta": 1000.0})
            with self.assertRaisesRegex(ValueError, "multiple recommendation bundles resolved"):
                validate_unique_suggested_energy_sources(({"source_id": "dup"}, {"source_id": "dup"}))
            self.assertEqual(
                validate_unique_suggested_energy_sources(({"source_id": ""}, {"source_id": "ok"})),
                ({"source_id": ""}, {"source_id": "ok"}),
            )

            updated = suggested_energy_sources_with_capacity((source,), 2048.0)
            self.assertEqual(updated[0]["usableCapacityWh"], 2048.0)
            self.assertEqual(suggested_energy_sources_with_capacity_overrides((source,), {}), (source,))
            overridden = suggested_energy_sources_with_capacity_overrides(
                (
                    {"source_id": "plain"},
                    source,
                    {"source_id": "beta", "capacityConfigKey": "AutoEnergySource.beta.UsableCapacityWh"},
                ),
                {"alpha": 4096.0},
            )
            self.assertEqual(overridden[1]["usableCapacityWh"], 4096.0)

            self.assertEqual(merge_energy_source_ids(("alpha",), ({"source_id": "alpha"}, {"source_id": "beta"})), ("alpha", "beta"))
            self.assertEqual(
                existing_source_ids_from_assignments(
                    {
                        "AutoEnergySources": "one",
                        "AutoEnergySource.two.Profile": "x",
                    }
                ),
                ("one", "two"),
            )

            self.assertEqual(energy_source_merge_lines({}), [])
            self.assertEqual(
                energy_source_merge_lines({"source_id": "alpha", "Port": 502, "usableCapacityWh": 1536.0})[-1],
                "AutoEnergySource.alpha.UsableCapacityWh=1536",
            )
            self.assertNotIn(
                "# Optional but recommended for weighted combined SOC:",
                suggested_energy_merge_lines(("alpha",), (source,), ()),
            )
            self.assertIsNone(energy_source_capacity_follow_up({"source_id": "alpha"}))
            self.assertEqual(
                energy_source_capacity_follow_up(
                    {
                        "source_id": "alpha",
                        "capacityConfigKey": "AutoEnergySource.alpha.UsableCapacityWh",
                        "usableCapacityWh": 1024.0,
                    }
                )["placeholder"],
                "1024",
            )

            merge_payload, merge_files = build_suggested_energy_merge(temp_path / "config.ini", ())
            self.assertIsNone(merge_payload)
            self.assertEqual(merge_files, {})
            assignments = suggested_energy_assignments(
                {"AutoUseCombinedBatterySoc": "0", "AutoEnergySources": "grid", "Keep": "yes"},
                (source,),
            )
            self.assertEqual(assignments["Keep"], "yes")

            self.assertIsNone(_structured_energy_source_line("#comment", "AutoEnergySource.alpha."))
            self.assertEqual(_structured_energy_source_value("Port", "bad"), "bad")
            self.assertEqual(bundle_source_id("ignored=true\n", "fallback"), "fallback")
            self.assertEqual(
                bundle_source_id("AutoEnergySource..Port=1\nAutoEnergySource.beta.Port=1\n", "fallback"),
                "beta",
            )
            self.assertEqual(bundle_target_names("")["ini"], "wizard-huawei-energy.ini")
            self.assertEqual(bundle_labels("")[0], "External energy source integration")
            self.assertEqual(bundle_block_label(""), "External energy source")
            self.assertEqual(manual_review_union(("Auth",), ("Auth", "Meter")), ("Auth", "Meter"))

            structured = structured_energy_source_from_block(
                "alpha",
                "\n".join(
                    (
                        "# comment",
                        "AutoEnergySource.alpha.Port=bad",
                        "AutoEnergySource.alpha.UnitId=7",
                        "Other=ignored",
                    )
                ),
            )
            self.assertEqual(structured["port"], "bad")
            self.assertEqual(structured["unitId"], 7)
            self.assertEqual(bundle_target_names("alpha")["ini"], "wizard-energy-alpha.ini")
            self.assertEqual(bundle_labels("alpha")[0], "External energy source integration (alpha)")
            self.assertEqual(bundle_block_label("alpha"), "External energy source (alpha)")

            bundle_prefix = temp_path / "bundle"
            with self.assertRaisesRegex(ValueError, "incomplete"):
                huawei_bundle_files(str(bundle_prefix))

    def test_wizard_energy_merge_and_bundle_branches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "config.ini"
            config_path.write_text(
                "[DEFAULT]\nAutoEnergySources=grid\nAutoEnergySource.grid.Profile=template\n",
                encoding="utf-8",
            )
            suggested_sources = (
                {
                    "source_id": "alpha",
                    "profile": "huawei_mb_sdongle",
                    "configPath": "/data/etc/huawei.ini",
                    "usableCapacityWh": 2048.0,
                    "capacityConfigKey": "AutoEnergySource.alpha.UsableCapacityWh",
                },
            )
            assignments = suggested_energy_assignments({"AutoEnergySource.grid.Profile": "template"}, suggested_sources)
            self.assertIn("AutoEnergySource.alpha.Profile", assignments)
            merge_lines = suggested_energy_merge_lines(
                ("grid", "alpha"),
                suggested_sources,
                tuple(item for item in (energy_source_capacity_follow_up(source) for source in suggested_sources) if item),
            )
            self.assertIn("AutoEnergySources=grid,alpha", merge_lines)

            merge_payload, merge_files = build_suggested_energy_merge(config_path, suggested_sources)
            self.assertEqual(merge_payload["merged_source_ids"], ["grid", "alpha"])
            self.assertIn("wizard-auto-energy-merge.ini", merge_files)

            bundle_prefix = temp_path / "bundle"
            manifest_dir = bundle_prefix
            manifest_dir.mkdir()
            (manifest_dir / "config.ini").write_text(
                "AutoEnergySource.beta.Profile=huawei_mb_sdongle\n"
                "AutoEnergySource.beta.ConfigPath=/data/etc/beta.ini\n",
                encoding="utf-8",
            )
            (manifest_dir / "hint.txt").write_text("Hint\n", encoding="utf-8")
            (manifest_dir / "summary.txt").write_text("Summary\n", encoding="utf-8")
            (temp_path / "bundle.manifest.json").write_text(
                '{'
                '"schema_type":"energy-recommendation-bundle",'
                '"schema_version":1,'
                '"source_id":"beta",'
                '"profile":"huawei_mb_sdongle",'
                '"config_path":"/data/etc/beta.ini",'
                '"files":{"config_snippet":"'
                + str((manifest_dir / "config.ini").resolve())
                + '","wizard_hint":"'
                + str((manifest_dir / "hint.txt").resolve())
                + '","summary":"'
                + str((manifest_dir / "summary.txt").resolve())
                + '"}'
                '}',
                encoding="utf-8",
            )
            rendered_files, review_items, suggested_blocks, structured_sources = huawei_bundle_files(str(manifest_dir))
            self.assertIn("wizard-energy-beta.ini", rendered_files)
            self.assertIn("weighted combined SOC", review_items[1])
            self.assertIn("External energy source (beta)", suggested_blocks)
            self.assertEqual(structured_sources[0]["source_id"], "beta")


class BranchCoverageVictronLearningCases(unittest.TestCase):
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


class BranchCoverageVictronApplyCases(unittest.TestCase):
    def test_victron_apply_helper_branches(self) -> None:
        controller = _controller()
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_min_update_seconds=10.0,
            auto_battery_discharge_balance_victron_bias_support_mode="weird",
            auto_battery_discharge_balance_victron_bias_activation_mode="weird",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.1,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=50.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=200.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=10.0,
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_service="",
            auto_battery_discharge_balance_victron_bias_path="",
            dbus_method_timeout_seconds=1.0,
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _last_auto_metrics=None,
            _victron_ess_balance_last_write_at=100.0,
            _victron_ess_balance_last_setpoint_w=75.0,
            _victron_ess_balance_pid_last_output_w=0.0,
            _victron_ess_balance_pid_integral_output_w=10.0,
            _victron_ess_balance_pid_last_error_w=15.0,
            _victron_ess_balance_pid_last_at=100.0,
        )
        metrics: dict[str, object] = {}

        self.assertEqual(controller._victron_ess_balance_cluster_state(service, False), ({}, "auto-mode-inactive"))
        service._last_energy_cluster = {"battery_discharge_balance_eligible_source_count": 1}
        self.assertEqual(
            controller._victron_ess_balance_cluster_state(service, True),
            (service._last_energy_cluster, "insufficient-eligible-sources"),
        )

        with patch.object(controller, "_victron_ess_balance_source", return_value=(None, "missing")):
            self.assertEqual(
                controller._victron_ess_balance_source_state({}, service, {}),
                (None, None, "missing"),
            )

        with patch.object(controller, "_victron_ess_balance_source", return_value=({"source_id": "a", "online": False}, "")):
            self.assertEqual(
                controller._victron_ess_balance_source_state({}, service, {}),
                (None, None, "victron-source-offline"),
            )

        with patch.object(
            controller,
            "_victron_ess_balance_source",
            return_value=({"source_id": "a", "online": True, "discharge_balance_error_w": None}, ""),
        ):
            self.assertEqual(
                controller._victron_ess_balance_source_state({}, service, {}),
                (None, None, "victron-source-error-missing"),
            )

        with patch.object(
            controller,
            "_victron_ess_balance_source",
            return_value=({"source_id": "a", "online": True, "discharge_balance_error_w": 10.0}, ""),
        ), patch.object(controller, "_victron_ess_balance_source_support_allowed", return_value=False):
            self.assertEqual(
                controller._victron_ess_balance_source_state({}, service, {}),
                (None, None, "victron-source-support-blocked"),
            )

        self.assertEqual(controller._victron_ess_balance_support_mode(service), "allow_experimental")
        self.assertEqual(controller._victron_ess_balance_activation_mode(service), "always")
        self.assertFalse(
            controller._victron_ess_balance_activation_allowed(
                {"site_regime": "import", "reserve_phase": "reserve_band"},
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_activation_mode="export_and_above_reserve_band"),
            )
        )
        self.assertTrue(controller._victron_ess_balance_activation_site_regime_matches("above_reserve_band", "import"))
        self.assertTrue(controller._victron_ess_balance_activation_reserve_phase_matches("export_only", "reserve_band"))
        self.assertTrue(
            controller._victron_ess_balance_activation_allowed(
                {"site_regime": "import", "reserve_phase": "reserve_band"},
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_activation_mode="always"),
            )
        )

        self.assertFalse(controller._victron_ess_balance_should_write(service, 105.0, 80.0))
        self.assertFalse(controller._victron_ess_balance_write_setpoint(service, "", "", 10.0))
        self.assertEqual(controller._victron_ess_balance_write_target(None, None), ("", ""))
        self.assertEqual(controller._victron_ess_balance_write_payload(object(), 12.5), 12.5)

        with patch.object(controller, "_victron_ess_balance_write_error", return_value=RuntimeError("boom")):
            self.assertFalse(controller._victron_ess_balance_write_setpoint(service, "svc", "/path", 10.0))
            service._warning_throttled.assert_called()

        with patch.object(
            controller,
            "_victron_ess_balance_try_write_setpoint",
            side_effect=[RuntimeError("first"), None],
        ), patch.object(controller, "_victron_ess_balance_log_write_retry") as log_retry:
            self.assertIsNone(controller._victron_ess_balance_write_error(service, "svc", "/path", 10.0))
            log_retry.assert_called_once()
            service._reset_system_bus.assert_called()
        with patch.object(
            controller,
            "_victron_ess_balance_try_write_setpoint",
            side_effect=[RuntimeError("first"), RuntimeError("second")],
        ), patch.object(controller, "_victron_ess_balance_log_write_retry") as log_retry:
            last_error = controller._victron_ess_balance_write_error(service, "svc", "/path", 10.0)
            self.assertEqual(str(last_error), "second")
            log_retry.assert_called_once()

        class _FakeInterface:
            def __init__(self) -> None:
                self.calls: list[tuple[object, float]] = []

            def SetValue(self, value: object, timeout: float) -> None:
                self.calls.append((value, timeout))

        class _FakeBus:
            def __init__(self, interface: _FakeInterface) -> None:
                self.interface = interface

            def get_object(self, normalized_service: str, normalized_path: str) -> object:
                return (normalized_service, normalized_path)

        fake_interface = _FakeInterface()

        class _FakeDbus:
            @staticmethod
            def Double(value: float) -> tuple[str, float]:
                return ("double", value)

            @staticmethod
            def Interface(_obj: object, _name: str) -> _FakeInterface:
                return fake_interface

        service._get_system_bus = MagicMock(return_value=_FakeBus(fake_interface))
        with patch("venus_evcharger.update.victron_ess_balance_apply.dbus", _FakeDbus):
            controller._victron_ess_balance_try_write_setpoint(service, "svc", "/path", 12.0)
        self.assertEqual(fake_interface.calls[0], (("double", 12.0), 1.0))

        with patch("venus_evcharger.update.victron_ess_balance_apply.logging.debug") as debug_log:
            controller._victron_ess_balance_log_write_retry("svc", "/path", RuntimeError("boom"))
            debug_log.assert_called_once()

        self.assertEqual(controller._victron_ess_balance_pid_output(service, 150.0, 101.0), 10.0)
        self.assertEqual(controller._victron_ess_balance_pid_output(service, 0.0, 102.0), 0.0)

        with patch.object(controller, "_victron_ess_balance_should_write", return_value=False):
            controller._victron_ess_balance_tracking_write_state(service, 110.0, 75.0, 25.0, "profile", metrics)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_reason"], "holding")
        service._victron_ess_balance_last_setpoint_w = None
        metrics = {}
        with patch.object(controller, "_victron_ess_balance_should_write", return_value=False):
            controller._victron_ess_balance_tracking_write_state(service, 110.5, 75.0, 25.0, "profile", metrics)
        self.assertEqual(metrics, {})
        with patch.object(controller, "_victron_ess_balance_should_write", return_value=True), patch.object(
            controller, "_victron_ess_balance_apply_write_outcome"
        ) as apply_outcome:
            controller._victron_ess_balance_tracking_write_state(service, 111.0, 75.0, 25.0, "profile", {})
            apply_outcome.assert_called_once()

        with patch.object(controller, "_populate_victron_ess_balance_telemetry_metrics"), patch.object(
            controller, "_maybe_auto_apply_victron_ess_balance_recommendation"
        ), patch.object(controller, "_merge_victron_ess_balance_metrics"):
            service._victron_ess_balance_last_setpoint_w = None
            metrics = {}
            controller._restore_victron_ess_balance_base_setpoint(service, 120.0, metrics, "blocked")
            self.assertEqual(metrics["battery_discharge_balance_victron_bias_reason"], "blocked")

        with patch.object(controller, "_victron_ess_balance_should_write", return_value=False), patch.object(
            controller, "_populate_victron_ess_balance_telemetry_metrics"
        ), patch.object(controller, "_maybe_auto_apply_victron_ess_balance_recommendation"), patch.object(
            controller, "_merge_victron_ess_balance_metrics"
        ):
            service._victron_ess_balance_last_setpoint_w = 70.0
            metrics = {}
            controller._restore_victron_ess_balance_base_setpoint(service, 121.0, metrics, "blocked")
            self.assertEqual(metrics["battery_discharge_balance_victron_bias_reason"], "blocked-holding")

        with patch.object(controller, "_victron_ess_balance_should_write", return_value=True), patch.object(
            controller, "_victron_ess_balance_write_setpoint", return_value=False
        ), patch.object(controller, "_populate_victron_ess_balance_telemetry_metrics"), patch.object(
            controller, "_maybe_auto_apply_victron_ess_balance_recommendation"
        ), patch.object(controller, "_merge_victron_ess_balance_metrics"):
            metrics = {}
            controller._restore_victron_ess_balance_base_setpoint(service, 122.0, metrics, "blocked")
            self.assertEqual(metrics["battery_discharge_balance_victron_bias_reason"], "blocked-restore-failed")

        self.assertEqual(
            controller._victron_ess_balance_source(
                {"battery_sources": [{"source_id": "x", "discharge_balance_control_connector_type": "dbus"}]},
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_source_id="missing"),
            ),
            (None, "victron-source-not-found"),
        )
        self.assertEqual(
            controller._victron_ess_balance_source({"battery_sources": []}, SimpleNamespace(auto_battery_discharge_balance_victron_bias_source_id="")),
            (None, "victron-source-not-detected"),
        )
        self.assertEqual(
            controller._victron_ess_balance_source(
                {
                    "battery_sources": [
                        {"source_id": "a", "discharge_balance_control_connector_type": "dbus"},
                        {"source_id": "b", "discharge_balance_control_connector_type": "dbus"},
                    ]
                },
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_source_id=""),
            ),
            (None, "victron-source-ambiguous"),
        )

        self.assertFalse(
            controller._victron_ess_balance_source_support_allowed(
                {"discharge_balance_control_support": "experimental"},
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_support_mode="supported_only"),
            )
        )
        self.assertTrue(
            controller._victron_ess_balance_source_support_allowed(
                {"discharge_balance_control_support": "experimental"},
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_support_mode="allow_experimental"),
            )
        )
        self.assertEqual(controller._victron_ess_balance_cluster_state(SimpleNamespace(_last_energy_cluster={"battery_discharge_balance_eligible_source_count": 2}), True)[1], "")
        self.assertEqual(
            controller._victron_ess_balance_matching_source([{"source_id": "a"}], "a"),
            {"source_id": "a"},
        )
        self.assertEqual(
            controller._victron_ess_balance_source(
                {"battery_sources": [{"source_id": "a", "discharge_balance_control_connector_type": "dbus"}]},
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_source_id="a"),
            ),
            ({"source_id": "a", "discharge_balance_control_connector_type": "dbus"}, "configured-source"),
        )
        self.assertEqual(
            controller._victron_ess_balance_source(
                {"battery_sources": [{"source_id": "a", "discharge_balance_control_connector_type": "dbus"}]},
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_source_id=""),
            ),
            ({"source_id": "a", "discharge_balance_control_connector_type": "dbus"}, "auto-detected-dbus-source"),
        )
        controller._reset_victron_ess_balance_pid_integral(service)
        self.assertEqual(service._victron_ess_balance_pid_integral_output_w, 0.0)
        self.assertTrue(
            controller._victron_ess_balance_activation_allowed(
                {"site_regime": "export", "reserve_phase": "above_reserve_band"},
                SimpleNamespace(auto_battery_discharge_balance_victron_bias_activation_mode="export_and_above_reserve_band"),
            )
        )
        self.assertEqual(controller._victron_ess_balance_pid_clamped_output_w(12.0, 0.0), 12.0)
        self.assertEqual(controller._victron_ess_balance_pid_ramped_output_w(1.0, 5.0, 0.0, 10.0), 5.0)
        service._victron_ess_balance_last_setpoint_w = None
        self.assertTrue(controller._victron_ess_balance_should_write(service, 120.0, 80.0))
        service._victron_ess_balance_last_setpoint_w = 75.0
        service._victron_ess_balance_last_write_at = 100.0
        self.assertTrue(controller._victron_ess_balance_should_write(service, 120.0, 90.0))

        metrics = {}
        with patch.object(
            controller,
            "_victron_ess_balance_source",
            return_value=({"source_id": "a", "online": True, "discharge_balance_error_w": 10.0}, ""),
        ), patch.object(controller, "_victron_ess_balance_source_support_allowed", return_value=True):
            source_state = controller._victron_ess_balance_source_state({}, service, metrics)
            self.assertEqual(source_state[2], "")
            self.assertEqual(source_state[1], 10.0)
        with patch.object(controller, "_victron_ess_balance_cluster_state", return_value=({"cluster": 1}, "")), patch.object(
            controller, "_victron_ess_balance_source_state", return_value=(None, None, "blocked-source")
        ):
            self.assertEqual(
                controller._prepare_victron_ess_balance_tracking_source(service, True, {}),
                ({"cluster": 1}, None, None, "blocked-source"),
            )
        with patch.object(controller, "_victron_ess_balance_cluster_state", return_value=({"cluster": 1}, "blocked-cluster")):
            self.assertEqual(
                controller._prepare_victron_ess_balance_tracking_source(service, True, {}),
                ({"cluster": 1}, None, None, "blocked-cluster"),
            )
        with patch.object(controller, "_victron_ess_balance_cluster_state", return_value=({"cluster": 1}, "")), patch.object(
            controller, "_victron_ess_balance_source_state", return_value=({"source_id": "a"}, 12.0, "")
        ):
            self.assertEqual(
                controller._prepare_victron_ess_balance_tracking_source(service, True, {}),
                ({"cluster": 1}, {"source_id": "a"}, 12.0, ""),
            )

        with patch.object(
            controller,
            "_prepare_victron_ess_balance_tracking_source",
            return_value=({"cluster": 1}, None, None, "blocked"),
        ):
            self.assertEqual(
                controller._prepare_victron_ess_balance_tracking_state(service, 122.0, True, {}),
                ({"cluster": 1}, None, None, "blocked"),
            )

        with patch.object(
            controller,
            "_prepare_victron_ess_balance_tracking_source",
            return_value=({"cluster": 1}, {"source_id": "a"}, 12.0, ""),
        ), patch.object(controller, "_prepare_victron_ess_balance_tracking_profile", return_value=("profile", "")):
            self.assertEqual(
                controller._prepare_victron_ess_balance_tracking_state(service, 123.0, True, {}),
                ({"cluster": 1}, 12.0, "profile", ""),
            )
        with patch.object(
            controller,
            "_prepare_victron_ess_balance_tracking_source",
            return_value=({"cluster": 1}, {"source_id": "a"}, 12.0, ""),
        ), patch.object(controller, "_prepare_victron_ess_balance_tracking_profile", return_value=(None, "profile-blocked")):
            self.assertEqual(
                controller._prepare_victron_ess_balance_tracking_state(service, 124.0, True, {}),
                ({"cluster": 1}, None, None, "profile-blocked"),
            )

        with patch.object(controller, "_victron_ess_balance_learning_profile", return_value={"key": "profile", "site_regime": "export", "reserve_phase": "above_reserve_band"}), patch.object(
            controller, "_merge_victron_ess_balance_learning_profile_metrics"
        ), patch.object(controller, "_victron_ess_balance_refresh_stable_tuning"), patch.object(
            controller, "_victron_ess_balance_note_action_direction", return_value=0
        ), patch.object(controller, "_populate_victron_ess_balance_runtime_safety_metrics"), patch.object(
            controller, "_victron_ess_balance_safety_block_reason", return_value=""
        ), patch.object(controller, "_victron_ess_balance_activation_allowed", return_value=True):
            self.assertEqual(
                controller._prepare_victron_ess_balance_tracking_profile(service, 100.0, {}, {"source_id": "a"}, 10.0, {}),
                ("profile", ""),
            )

        with patch.object(controller, "_victron_ess_balance_overshoot_cooldown_active", return_value=True), patch.object(
            controller, "_maybe_restore_victron_ess_balance_stable_tuning"
        ) as restore_tuning:
            self.assertEqual(controller._victron_ess_balance_safety_block_reason(service, 130.0, {}), "overshoot-cooldown-active")
            restore_tuning.assert_called_once()
        with patch.object(controller, "_victron_ess_balance_overshoot_cooldown_active", return_value=False), patch.object(
            controller, "_victron_ess_balance_oscillation_lockout_active", return_value=True
        ), patch.object(controller, "_maybe_restore_victron_ess_balance_stable_tuning") as restore_tuning:
            self.assertEqual(controller._victron_ess_balance_safety_block_reason(service, 131.0, {}), "oscillation-lockout-active")
            restore_tuning.assert_called_once()
        with patch.object(controller, "_victron_ess_balance_overshoot_cooldown_active", return_value=False), patch.object(
            controller, "_victron_ess_balance_oscillation_lockout_active", return_value=False
        ):
            service._victron_ess_balance_safe_state_active = True
            service._victron_ess_balance_safe_state_reason = "old"
            self.assertEqual(controller._victron_ess_balance_safety_block_reason(service, 130.0, {}), "")
            self.assertFalse(service._victron_ess_balance_safe_state_active)
        with patch.object(controller, "_update_victron_ess_balance_telemetry") as update_telemetry:
            controller._victron_ess_balance_update_tracking_telemetry(service, 132.0, {"cluster": 1}, -10.0, "profile", {})
            update_telemetry.assert_called_once()
        with patch.object(controller, "_victron_ess_balance_write_setpoint", return_value=False):
            metrics = {}
            controller._victron_ess_balance_apply_write_outcome(service, 133.0, 70.0, -20.0, "profile", metrics)
            self.assertEqual(metrics["battery_discharge_balance_victron_bias_reason"], "write-failed")
        with patch.object(controller, "_victron_ess_balance_write_error", return_value=None):
            self.assertTrue(controller._victron_ess_balance_write_setpoint(service, "svc", "/path", 10.0))

        controller._merge_victron_ess_balance_metrics(service, {"x": 1})
        self.assertEqual(service._last_auto_metrics, {"x": 1})

    def test_victron_apply_prepare_and_telemetry_branches(self) -> None:
        controller = _controller()
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=True,
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=20.0,
            auto_battery_discharge_balance_victron_bias_support_mode="allow_experimental",
            auto_battery_discharge_balance_victron_bias_activation_mode="always",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.0,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=0.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=100.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=0.0,
            auto_battery_discharge_balance_victron_bias_source_id="victron",
            auto_battery_discharge_balance_victron_bias_service="svc",
            auto_battery_discharge_balance_victron_bias_path="/path",
            auto_energy_sources=(SimpleNamespace(source_id="alpha"),),
            _last_auto_metrics={},
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
            _victron_ess_balance_last_setpoint_w=None,
            _victron_ess_balance_last_write_at=None,
            _victron_ess_balance_telemetry_last_command_at=100.0,
            _victron_ess_balance_telemetry_last_command_error_w=-60.0,
            _victron_ess_balance_telemetry_last_command_setpoint_w=70.0,
            _victron_ess_balance_telemetry_last_command_profile_key="profile",
            _victron_ess_balance_learning_profiles={"profile": {"delay_samples": 0, "gain_samples": 0, "settled_count": 0, "overshoot_count": 0}},
        )
        metrics = controller._victron_ess_balance_default_metrics()
        cluster = {
            "battery_discharge_balance_eligible_source_count": 2,
            "battery_combined_grid_interaction_w": -150.0,
            "battery_combined_ac_power_w": 800.0,
            "expected_near_term_export_w": 120.0,
            "expected_near_term_import_w": 0.0,
            "battery_combined_pv_input_power_w": 300.0,
            "battery_sources": [
                {
                    "source_id": "victron",
                    "online": True,
                    "soc": 60.0,
                    "discharge_balance_reserve_floor_soc": 35.0,
                    "discharge_balance_error_w": -60.0,
                    "discharge_balance_control_connector_type": "dbus",
                    "discharge_balance_control_support": "supported",
                },
                {"source_id": "hybrid", "online": True},
            ],
        }
        controller._victron_ess_balance_ev_power_w = MagicMock(return_value=0.0)
        controller._victron_ess_balance_telemetry_is_clean = MagicMock(return_value=(True, "clean"))
        controller._populate_victron_ess_balance_telemetry_metrics = MagicMock()

        command_state = {
            "command_at": 100.0,
            "command_error_w": -60.0,
            "command_setpoint_w": 70.0,
            "command_profile_key": "profile",
            "command_response_recorded": False,
            "command_overshoot_recorded": False,
            "command_settled_recorded": False,
        }
        overshoot_active, settling_active = controller._victron_ess_balance_process_clean_episode(
            service,
            105.0,
            -10.0,
            command_state,
            10.0,
            50.0,
            20.0,
        )
        self.assertFalse(overshoot_active)
        self.assertFalse(settling_active)

        service._victron_ess_balance_telemetry_last_command_at = None
        controller._update_victron_ess_balance_telemetry(service, 105.5, cluster, -12.0, metrics, "profile")
        self.assertFalse(service._victron_ess_balance_telemetry_settling_active)
        service._victron_ess_balance_telemetry_last_command_at = 100.0
        controller._update_victron_ess_balance_telemetry(service, 106.0, cluster, -10.0, metrics, "profile")
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_telemetry_clean_reason"], "clean")
        self.assertEqual(service._victron_ess_balance_telemetry_last_observed_error_w, -10.0)

        with patch.object(controller, "_prepare_victron_ess_balance_learning_state", return_value={"key": "profile"}), patch.object(
            controller, "_victron_ess_balance_safety_block_reason", return_value="blocked"
        ):
            self.assertEqual(
                controller._prepare_victron_ess_balance_tracking_profile(service, 100.0, cluster, cluster["battery_sources"][0], -60.0, metrics),
                (None, "blocked"),
            )

        with patch.object(controller, "_prepare_victron_ess_balance_learning_state", return_value={"key": "profile"}), patch.object(
            controller, "_victron_ess_balance_safety_block_reason", return_value=""
        ), patch.object(controller, "_victron_ess_balance_activation_allowed", return_value=False):
            self.assertEqual(
                controller._prepare_victron_ess_balance_tracking_profile(service, 100.0, cluster, cluster["battery_sources"][0], -60.0, metrics),
                (None, "activation-mode-blocked"),
            )

    def test_victron_apply_composite_branches(self) -> None:
        controller = _controller()
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=False,
            auto_battery_discharge_balance_victron_bias_activation_mode="always",
            auto_battery_discharge_balance_victron_bias_auto_apply_enabled=False,
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.0,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=20.0,
            auto_battery_discharge_balance_victron_bias_integral_limit_watts=0.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=100.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=0.0,
            auto_battery_discharge_balance_victron_bias_service="svc",
            auto_battery_discharge_balance_victron_bias_path="/path",
            _last_auto_metrics={},
            _warning_throttled=MagicMock(),
            _reset_system_bus=MagicMock(),
        )
        controller.apply_victron_ess_balance_bias(service, 10.0, True)
        self.assertEqual(service._last_auto_metrics["battery_discharge_balance_victron_bias_enabled"], 0)

        service.auto_battery_discharge_balance_victron_bias_enabled = True
        with patch.object(
            controller,
            "_prepare_victron_ess_balance_tracking_state",
            return_value=({}, None, None, "blocked"),
        ), patch.object(controller, "_restore_victron_ess_balance_base_setpoint") as restore_base:
            controller.apply_victron_ess_balance_bias(service, 11.0, True)
            restore_base.assert_called_once()

        with patch.object(
            controller,
            "_prepare_victron_ess_balance_tracking_state",
            return_value=({"cluster": 1}, 25.0, "profile", ""),
        ), patch.object(controller, "_apply_victron_ess_balance_tracking") as apply_tracking:
            controller.apply_victron_ess_balance_bias(service, 12.0, True)
            apply_tracking.assert_called_once()

        metrics = {}
        learning_profile = {"key": "profile", "action_direction": "more_export"}
        with patch.object(controller, "_victron_ess_balance_learning_profile", return_value=learning_profile), patch.object(
            controller, "_merge_victron_ess_balance_learning_profile_metrics"
        ) as merge_metrics, patch.object(controller, "_victron_ess_balance_refresh_stable_tuning") as refresh_tuning, patch.object(
            controller, "_victron_ess_balance_note_action_direction", return_value=2
        ) as note_direction, patch.object(controller, "_populate_victron_ess_balance_runtime_safety_metrics") as populate_safety:
            returned = controller._prepare_victron_ess_balance_learning_state(service, 20.0, {"c": 1}, {"source_id": "victron"}, -20.0, metrics)
            self.assertEqual(returned, learning_profile)
            merge_metrics.assert_called_once()
            refresh_tuning.assert_called_once()
            note_direction.assert_called_once()
            populate_safety.assert_called_once()
            self.assertEqual(metrics["battery_discharge_balance_victron_bias_oscillation_direction_change_count"], 2)

        with patch.object(controller, "_maybe_auto_apply_victron_ess_balance_recommendation") as maybe_auto_apply, patch.object(
            controller, "_merge_victron_ess_balance_metrics"
        ) as merge_metrics:
            controller._finalize_victron_ess_balance_metrics(service, 13.0, {})
            maybe_auto_apply.assert_called_once()
            merge_metrics.assert_called_once()

        metrics = {}
        with patch.object(controller, "_victron_ess_balance_pid_output", return_value=12.0):
            self.assertEqual(controller._victron_ess_balance_tracking_setpoint(service, 14.0, -40.0, metrics), 62.0)
            self.assertEqual(metrics["battery_discharge_balance_victron_bias_reason"], "tracking")

        service._victron_ess_balance_last_setpoint_w = 60.0
        with patch.object(controller, "_victron_ess_balance_write_setpoint", return_value=True):
            metrics = {}
            controller._victron_ess_balance_apply_write_outcome(service, 15.0, 70.0, -20.0, "profile", metrics)
            self.assertEqual(metrics["battery_discharge_balance_victron_bias_reason"], "applied")

        with patch.object(controller, "_victron_ess_balance_update_tracking_telemetry") as update_telemetry, patch.object(
            controller, "_victron_ess_balance_tracking_write_state"
        ) as write_state, patch.object(controller, "_finalize_victron_ess_balance_metrics") as finalize_metrics:
            controller._apply_victron_ess_balance_tracking(service, 16.0, {"cluster": 1}, -20.0, "profile", {})
            update_telemetry.assert_called_once()
            write_state.assert_called_once()
            finalize_metrics.assert_called_once()

        with patch.object(controller, "_populate_victron_ess_balance_telemetry_metrics"), patch.object(
            controller, "_maybe_auto_apply_victron_ess_balance_recommendation"
        ), patch.object(controller, "_merge_victron_ess_balance_metrics"), patch.object(
            controller, "_victron_ess_balance_should_write", return_value=True
        ), patch.object(controller, "_victron_ess_balance_write_setpoint", return_value=True):
            metrics = {}
            service._victron_ess_balance_last_setpoint_w = 70.0
            controller._restore_victron_ess_balance_base_setpoint(service, 17.0, metrics, "blocked")
            self.assertEqual(metrics["battery_discharge_balance_victron_bias_reason"], "blocked-restored")


class BranchCoverageVictronTelemetryMixinCases(unittest.TestCase):
    def test_telemetry_mixin_runtime_branches(self) -> None:
        harness = _TelemetryHarness()
        service = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_deadband_watts=20.0,
            auto_battery_discharge_balance_victron_bias_base_setpoint_watts=50.0,
            _victron_ess_balance_telemetry_last_command_at=100.0,
            _victron_ess_balance_telemetry_last_command_error_w=-60.0,
            _victron_ess_balance_telemetry_last_command_setpoint_w=70.0,
            _victron_ess_balance_telemetry_last_command_profile_key="profile",
            _victron_ess_balance_telemetry_command_response_recorded=False,
            _victron_ess_balance_telemetry_command_overshoot_recorded=False,
            _victron_ess_balance_telemetry_command_settled_recorded=False,
            _victron_ess_balance_telemetry_settled_count=0,
            _victron_ess_balance_telemetry_overshoot_count=0,
            _victron_ess_balance_pid_last_error_w=3.0,
            _victron_ess_balance_pid_last_output_w=4.0,
            _victron_ess_balance_pid_integral_output_w=5.0,
            _victron_ess_balance_learning_profiles={
                "profile": {
                    "delay_samples": 0,
                    "gain_samples": 0,
                    "settled_count": 0,
                    "overshoot_count": 0,
                    "stability_score": 0.0,
                    "response_variance_score": 0.0,
                }
            },
        )
        cluster = {
            "battery_combined_grid_interaction_w": -100.0,
            "battery_combined_ac_power_w": 800.0,
        }
        metrics: dict[str, object] = {}

        harness._victron_ess_balance_mark_overshoot(service, 101.0, "profile")
        self.assertEqual(service._victron_ess_balance_telemetry_overshoot_count, 1)
        self.assertEqual(harness.cooldowns[-1][1], "overshoot_detected")

        harness._update_victron_ess_balance_telemetry(service, 105.0, cluster, -10.0, metrics, "profile")
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_telemetry_clean"], 1)
        self.assertTrue(metrics["telemetry_metrics_populated"])
        self.assertEqual(harness.refreshed_profiles[-1], "profile")
        self.assertTrue(harness.delay_updates)
        self.assertTrue(harness.gain_updates)

        service._victron_ess_balance_telemetry_last_command_profile_key = ""
        harness._record_victron_ess_balance_command(service, 111.0, 62.0, -14.0, "fallback")
        self.assertEqual(service._victron_ess_balance_telemetry_last_command_setpoint_w, 62.0)
        harness._clear_victron_ess_balance_tracking_episode(service)
        self.assertIsNone(service._victron_ess_balance_telemetry_last_command_at)
        harness._reset_victron_ess_balance_pid(service)
        self.assertEqual(service._victron_ess_balance_pid_last_output_w, 0.0)
        harness._reset_victron_ess_balance_pid_integral(service)
        self.assertEqual(service._victron_ess_balance_pid_integral_output_w, 0.0)
        self.assertAlmostEqual(harness._victron_ess_balance_stability_score_values(0, 0, None, None), 0.85)

        no_gain_updates = len(harness.gain_updates)
        harness._victron_ess_balance_maybe_record_gain(service, "profile", 0.0, 0.5)
        self.assertEqual(len(harness.gain_updates), no_gain_updates)

        overshoot_state = {"command_overshoot_recorded": False}
        harness._victron_ess_balance_maybe_mark_overshoot(service, 112.0, 12.0, overshoot_state, "profile", -8.0, 12.0, 2.0)
        self.assertTrue(overshoot_state["command_overshoot_recorded"])

        command_state = harness._victron_ess_balance_telemetry_command_state(service, "fallback")
        self.assertEqual(command_state["command_profile_key"], "fallback")


class BranchCoverageWizardRenderCases(unittest.TestCase):
    def test_wizard_render_helper_branches(self) -> None:
        self.assertEqual(upsert_default_assignments("[DEFAULT]\nHost=a\n", {}), "[DEFAULT]\nHost=a\n")
        self.assertEqual(
            upsert_default_assignments("[DEFAULT]\n[Other]\n", {"AutoEnergySources": "alpha"}),
            "[DEFAULT]\nAutoEnergySources=alpha\n[Other]\n",
        )
        self.assertEqual(_remaining_default_assignment_lines(["Host=a"], {}), [])
        self.assertEqual(
            _remaining_default_assignment_lines(["Host=a"], {"AutoEnergySources": "alpha"}),
            ["", "AutoEnergySources=alpha"],
        )
        self.assertEqual(
            _remaining_default_assignment_lines([""], {"AutoEnergySources": "alpha"}),
            ["AutoEnergySources=alpha"],
        )

    def test_wizard_render_live_connectivity_branches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            main_path = temp_path / "config.ini"
            main_path.write_text("[DEFAULT]\n", encoding="utf-8")

            runtime = SimpleNamespace(
                meter_config_path=Path("meter.ini"),
                switch_config_path=None,
                charger_config_path=Path("charger.ini"),
            )
            resolved = SimpleNamespace(runtime=runtime)
            with patch("venus_evcharger.bootstrap.wizard_render.build_service_backends", return_value=resolved), patch(
                "venus_evcharger.bootstrap.wizard_render.probe_meter_backend",
                return_value={"meter": "ok"},
            ), patch(
                "venus_evcharger.bootstrap.wizard_render.read_charger_backend",
                side_effect=RuntimeError("boom"),
            ):
                payload = live_connectivity_payload(main_path, ("meter", "charger"))
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["roles"]["switch"]["reason"], "not requested")
            self.assertEqual(payload["roles"]["meter"]["status"], "ok")
            self.assertEqual(payload["roles"]["charger"]["status"], "error")

            runtime = SimpleNamespace(
                meter_config_path=None,
                switch_config_path=None,
                charger_config_path=None,
            )
            with patch(
                "venus_evcharger.bootstrap.wizard_render.build_service_backends",
                return_value=SimpleNamespace(runtime=runtime),
            ):
                payload = live_connectivity_payload(main_path, None)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["roles"]["meter"]["reason"], "not configured")

            with patch(
                "venus_evcharger.bootstrap.wizard_render.live_connectivity_payload",
                return_value={"ok": True, "checked_roles": (), "roles": {}},
            ):
                live_payload = live_check_rendered_setup(
                    "Host=example\nAdapter=adapter.ini\n",
                    {"adapter.ini": "backend=true\n"},
                    "config.ini",
                    ("meter",),
                )
            self.assertTrue(live_payload["ok"])


class BranchCoverageVictronAdaptiveCases(unittest.TestCase):
    def test_victron_adaptive_helper_branches(self) -> None:
        controller = _controller()
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_auto_apply_enabled=False,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence=0.85,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score=0.75,
            auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples=3,
            auto_battery_discharge_balance_victron_bias_auto_apply_blend=0.25,
            auto_battery_discharge_balance_victron_bias_observation_window_seconds=0.0,
            auto_battery_discharge_balance_victron_bias_activation_mode="always",
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.02,
            auto_battery_discharge_balance_victron_bias_kd=0.01,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=300.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=40.0,
            _victron_ess_balance_auto_apply_observe_until=50.0,
            _victron_ess_balance_auto_apply_suspend_until=60.0,
            _victron_ess_balance_auto_apply_suspend_reason="cooldown",
            _victron_ess_balance_last_stable_profile_key="stable",
        )
        metrics = {
            "battery_discharge_balance_victron_bias_recommendation_confidence": 0.1,
            "battery_discharge_balance_victron_bias_learning_profile_stability_score": 0.1,
            "battery_discharge_balance_victron_bias_learning_profile_sample_count": 1,
            "battery_discharge_balance_victron_bias_recommendation_profile_key": "recommended",
            "battery_discharge_balance_victron_bias_learning_profile_key": "active",
            "battery_discharge_balance_victron_bias_recommended_activation_mode": "always",
        }

        self.assertEqual(controller._victron_ess_balance_auto_apply_confidence_reason(svc, metrics), "confidence_too_low")
        self.assertEqual(controller._victron_ess_balance_auto_apply_stability_reason(svc, metrics), "stability_too_low")
        self.assertEqual(controller._victron_ess_balance_auto_apply_sample_reason(svc, metrics), "insufficient_profile_samples")
        self.assertEqual(controller._victron_ess_balance_auto_apply_profile_reason(metrics), "profile_mismatch")
        self.assertEqual(controller._victron_ess_balance_auto_apply_observation_until(svc, 10.0), 40.0)
        svc.auto_battery_discharge_balance_victron_bias_observation_window_seconds = -1.0
        self.assertIsNone(controller._victron_ess_balance_auto_apply_observation_until(svc, 10.0))
        self.assertEqual(controller._victron_ess_balance_auto_apply_suspend_reason(svc, 10.0), "auto_apply_suspended")
        self.assertEqual(controller._victron_ess_balance_auto_apply_observation_reason(svc, {}, 10.0), "observation_window_active")

        saver = MagicMock()
        svc._save_runtime_state = saver
        controller._victron_ess_balance_save_runtime_state(svc)
        saver.assert_called_once()
        controller._victron_ess_balance_save_runtime_state(SimpleNamespace(_save_runtime_state=None))

        self.assertFalse(controller._blend_recommended_setting(svc, "missing_attr", 1.0, 0.5))
        self.assertFalse(
            controller._blend_recommended_setting(
                svc,
                "auto_battery_discharge_balance_victron_bias_kp",
                0.2,
                0.5,
            )
        )
        self.assertEqual(
            controller._victron_ess_balance_recommended_activation_step(svc, metrics),
            "",
        )
        self.assertEqual(
            controller._victron_ess_balance_auto_apply_readiness(
                svc,
                {
                    "battery_discharge_balance_victron_bias_recommendation_confidence": 0.9,
                    "battery_discharge_balance_victron_bias_learning_profile_stability_score": 0.9,
                    "battery_discharge_balance_victron_bias_learning_profile_sample_count": 5,
                    "battery_discharge_balance_victron_bias_recommendation_profile_key": "r",
                    "battery_discharge_balance_victron_bias_learning_profile_key": "a",
                },
            ),
            "profile_mismatch",
        )
        self.assertEqual(
            controller._victron_ess_balance_auto_apply_readiness(
                svc,
                {
                    "battery_discharge_balance_victron_bias_recommendation_confidence": 0.9,
                    "battery_discharge_balance_victron_bias_learning_profile_stability_score": 0.9,
                    "battery_discharge_balance_victron_bias_learning_profile_sample_count": 1,
                    "battery_discharge_balance_victron_bias_recommendation_profile_key": "same",
                    "battery_discharge_balance_victron_bias_learning_profile_key": "same",
                },
            ),
            "insufficient_profile_samples",
        )
        self.assertEqual(
            controller._victron_ess_balance_auto_apply_readiness(
                svc,
                {
                    "battery_discharge_balance_victron_bias_recommendation_confidence": 0.9,
                    "battery_discharge_balance_victron_bias_learning_profile_stability_score": 0.9,
                    "battery_discharge_balance_victron_bias_learning_profile_sample_count": 5,
                    "battery_discharge_balance_victron_bias_recommendation_profile_key": "same",
                    "battery_discharge_balance_victron_bias_learning_profile_key": "same",
                },
            ),
            "",
        )

        metrics = {}
        controller._maybe_auto_apply_victron_ess_balance_recommendation(svc, metrics, 10.0)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_auto_apply_reason"], "disabled")

        svc.auto_battery_discharge_balance_victron_bias_auto_apply_enabled = True
        metrics = {}
        with patch.object(controller, "_victron_ess_balance_auto_apply_blocker_reason", return_value="blocked"):
            controller._maybe_auto_apply_victron_ess_balance_recommendation(svc, metrics, 11.0)
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_auto_apply_reason"], "blocked")

        metrics = {
            "battery_discharge_balance_victron_bias_recommended_activation_mode": "export_only",
        }
        self.assertEqual(
            controller._apply_victron_ess_balance_recommended_tuning_step(svc, metrics, 0.25),
            "auto_battery_discharge_balance_victron_bias_activation_mode",
        )

        with patch.object(controller, "_victron_ess_balance_should_rollback_stable_tuning", return_value=True), patch.object(
            controller, "_maybe_restore_victron_ess_balance_stable_tuning", return_value=False
        ):
            self.assertEqual(controller._victron_ess_balance_auto_apply_rollback_reason(svc, {}, 12.0), "")

        metrics = {}
        with patch.object(controller, "_apply_victron_ess_balance_recommended_tuning_step", return_value=""):
            self.assertFalse(controller._apply_victron_ess_balance_auto_apply_step(svc, metrics, 13.0))
        self.assertEqual(metrics["battery_discharge_balance_victron_bias_auto_apply_reason"], "already_at_recommendation")


class BranchCoverageVictronRecommendationCases(unittest.TestCase):
    def test_victron_recommendation_helper_branches(self) -> None:
        controller = _controller()
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_enabled=False,
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.02,
            auto_battery_discharge_balance_victron_bias_kd=0.04,
            auto_battery_discharge_balance_victron_bias_deadband_watts=100.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=300.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=40.0,
            auto_battery_discharge_balance_victron_bias_activation_mode="always",
            _victron_ess_balance_active_learning_profile_key="",
        )
        self.assertFalse(controller._victron_ess_balance_can_relax_conservatism({"stability_score": None}))
        self.assertEqual(
            controller._victron_ess_balance_recommendation_reason(
                {
                    "response_delay_seconds": 9.0,
                    "estimated_gain": 1.0,
                    "stability_score": 0.9,
                    "overshoot_count": 0,
                    "settled_count": 2,
                },
                0.9,
            ),
            "slow_response",
        )
        self.assertEqual(
            controller._victron_ess_balance_adjusted_kd(0.04, {"kd_factor": 0.5}),
            0.02,
        )
        self.assertIn("slow site response", controller._victron_ess_balance_recommendation_hint("slow_response", 0.6))
        self.assertEqual(
            controller._victron_ess_balance_recommended_activation_mode(
                {"site_regime": "import", "reserve_phase": "above_reserve_band"},
                svc,
            ),
            "above_reserve_band",
        )
        self.assertEqual(controller._victron_ess_balance_export_activation_mode("reserve_band"), "export_only")
        disabled = controller._victron_ess_balance_recommendation_metrics(svc)
        self.assertEqual(disabled["battery_discharge_balance_victron_bias_recommendation_reason"], "disabled")


class BranchCoverageVictronSafetyCases(unittest.TestCase):
    def test_victron_safety_helper_branches(self) -> None:
        controller = _controller()
        svc = SimpleNamespace(
            auto_battery_discharge_balance_victron_bias_deadband_watts=50.0,
            auto_battery_discharge_balance_victron_bias_require_clean_phases=True,
            auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled=False,
            auto_battery_discharge_balance_victron_bias_rollback_enabled=False,
            auto_battery_discharge_balance_victron_bias_activation_mode="always",
            auto_battery_discharge_balance_victron_bias_observation_window_seconds=15.0,
            _auto_cached_inputs_used=True,
            _phase_switch_state="switching",
            _contactor_fault_active_reason="fault",
            _contactor_lockout_reason="lockout",
            _victron_ess_balance_recent_action_changes=[None, {"at": 5.0}, {"at": 20.0, "action_direction": "more_export"}],
            _victron_ess_balance_telemetry_last_observed_at=10.0,
            _victron_ess_balance_auto_apply_last_applied_at=None,
            _victron_ess_balance_last_stable_tuning=None,
            _victron_ess_balance_conservative_tuning={"kp": 0.1},
            charging_started_at=1.0,
            virtual_startstop=1,
            learned_charge_power_watts=2300.0,
        )
        self.assertEqual(controller._victron_ess_balance_grid_window_reason(svc, {}), "grid_interaction_missing")
        self.assertEqual(
            controller._victron_ess_balance_power_window_reason(
                SimpleNamespace(_victron_ess_balance_telemetry_last_ac_power_w=0.0),
                {"battery_combined_ac_power_w": 1000.0},
            ),
            "foreign_power_event",
        )
        self.assertEqual(controller._victron_ess_balance_phase_switch_reason(svc), "phase_switch_active")
        self.assertEqual(controller._victron_ess_balance_contactor_block_reason(svc), "contactor_fault_active")
        svc._contactor_fault_active_reason = ""
        self.assertEqual(controller._victron_ess_balance_contactor_block_reason(svc), "contactor_lockout_active")
        self.assertEqual(controller._victron_ess_balance_telemetry_precheck_reason(svc), (False, "cached_inputs"))
        self.assertEqual(
            controller._victron_ess_balance_telemetry_precheck_reason(
                SimpleNamespace(
                    auto_battery_discharge_balance_victron_bias_require_clean_phases=False,
                    _auto_cached_inputs_used=False,
                    _phase_switch_state="",
                    _contactor_fault_active_reason="",
                    _contactor_lockout_reason="",
                )
            ),
            (True, "clean_not_required"),
        )
        self.assertEqual(
            controller._victron_ess_balance_telemetry_precheck_reason(
                SimpleNamespace(
                    auto_battery_discharge_balance_victron_bias_require_clean_phases=True,
                    _auto_cached_inputs_used=False,
                    _phase_switch_state="switching",
                    _contactor_fault_active_reason="",
                    _contactor_lockout_reason="",
                )
            ),
            (False, "phase_switch_active"),
        )
        self.assertEqual(
            controller._victron_ess_balance_telemetry_precheck_reason(
                SimpleNamespace(
                    auto_battery_discharge_balance_victron_bias_require_clean_phases=True,
                    _auto_cached_inputs_used=False,
                    _phase_switch_state="",
                    _contactor_fault_active_reason="fault",
                    _contactor_lockout_reason="",
                )
            ),
            (False, "contactor_fault_active"),
        )
        self.assertIsNone(
            controller._victron_ess_balance_power_window_reason(
                SimpleNamespace(_victron_ess_balance_telemetry_last_ac_power_w=100.0),
                {"battery_combined_ac_power_w": 120.0},
            )
        )
        self.assertIsNone(
            controller._victron_ess_balance_telemetry_window_reason(
                SimpleNamespace(
                    _victron_ess_balance_telemetry_last_grid_interaction_w=10.0,
                    _victron_ess_balance_telemetry_last_ac_power_w=100.0,
                    _victron_ess_balance_telemetry_last_ev_power_w=0.0,
                    charging_started_at=None,
                    virtual_startstop=0,
                ),
                {"battery_combined_grid_interaction_w": 15.0, "battery_combined_ac_power_w": 120.0},
            )
        )
        self.assertEqual(
            controller._victron_ess_balance_telemetry_window_reason(
                SimpleNamespace(
                    _victron_ess_balance_telemetry_last_grid_interaction_w=10.0,
                    _victron_ess_balance_telemetry_last_ac_power_w=0.0,
                    _victron_ess_balance_telemetry_last_ev_power_w=0.0,
                    charging_started_at=None,
                    virtual_startstop=0,
                ),
                {"battery_combined_grid_interaction_w": 15.0, "battery_combined_ac_power_w": 1500.0},
            ),
            "foreign_power_event",
        )

        with patch.object(controller, "_victron_ess_balance_telemetry_precheck_reason", return_value=None), patch.object(
            controller, "_victron_ess_balance_telemetry_window_reason", return_value=None
        ):
            self.assertEqual(controller._victron_ess_balance_telemetry_is_clean(svc, {}, 5.0), (False, "error_inside_deadband"))

        kept = controller._victron_ess_balance_kept_action_changes([None, {"at": None}, {"at": 1.0}, {"at": 20.0}], 10.0)
        self.assertEqual(kept, [{"at": 20.0}])
        self.assertEqual(controller._victron_ess_balance_note_action_direction(svc, "idle", 30.0), 1)
        self.assertFalse(controller._victron_ess_balance_should_enter_oscillation_lockout(svc, 5))

        metrics = {
            "battery_discharge_balance_victron_bias_recommendation_confidence": 0.9,
            "battery_discharge_balance_victron_bias_learning_profile_stability_score": 0.9,
            "battery_discharge_balance_victron_bias_learning_profile_sample_count": 3,
            "battery_discharge_balance_victron_bias_learning_profile_overshoot_count": 0,
            "battery_discharge_balance_victron_bias_learning_profile_key": "profile",
        }
        controller._victron_ess_balance_refresh_stable_tuning(svc, metrics, 40.0)
        self.assertEqual(svc._victron_ess_balance_last_stable_profile_key, "profile")
        self.assertIsNotNone(svc._victron_ess_balance_conservative_tuning)
        self.assertFalse(controller._victron_ess_balance_has_minimum_stability(0.7))
        self.assertFalse(controller._victron_ess_balance_should_rollback_stable_tuning(svc, {}, 41.0))
        no_cons = SimpleNamespace(
            _victron_ess_balance_conservative_tuning=None,
            auto_battery_discharge_balance_victron_bias_kp=0.2,
            auto_battery_discharge_balance_victron_bias_ki=0.02,
            auto_battery_discharge_balance_victron_bias_kd=0.0,
            auto_battery_discharge_balance_victron_bias_deadband_watts=50.0,
            auto_battery_discharge_balance_victron_bias_max_abs_watts=200.0,
            auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second=20.0,
            auto_battery_discharge_balance_victron_bias_activation_mode="always",
        )
        controller._victron_ess_balance_ensure_conservative_tuning(no_cons)
        self.assertEqual(no_cons._victron_ess_balance_conservative_tuning["kp"], 0.2)
        svc._victron_ess_balance_last_stable_tuning = None
        self.assertEqual(controller._victron_ess_balance_restore_target(svc, "reason")[1], "conservative_fallback")

        with patch.object(controller, "_victron_ess_balance_restored_activation_mode", return_value=""):
            controller._apply_victron_ess_balance_restored_tuning(
                svc,
                {
                    "kp": 0.1,
                    "ki": 0.01,
                    "kd": 0.0,
                    "deadband_watts": 50.0,
                    "max_abs_watts": 200.0,
                    "ramp_rate_watts_per_second": 20.0,
                },
            )
        self.assertEqual(svc.auto_battery_discharge_balance_victron_bias_kp, 0.1)
        self.assertEqual(controller._victron_ess_balance_ev_power_w(svc), 2300.0)
        self.assertTrue(controller._victron_ess_balance_ev_active(svc))
        svc.charging_started_at = None
        self.assertTrue(controller._victron_ess_balance_ev_active(svc))
        svc.learned_charge_power_watts = None
        self.assertTrue(controller._victron_ess_balance_ev_active(svc))
        svc.virtual_startstop = 0
        svc.charging_started_at = 2.0
        self.assertTrue(controller._victron_ess_balance_ev_active(svc))



if __name__ == "__main__":
    unittest.main()
