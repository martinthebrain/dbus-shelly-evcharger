# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import io
import json
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.bootstrap import wizard, wizard_cli
from venus_evcharger.bootstrap.wizard_guidance import prompt_split_preset, resolved_primary_host
from venus_evcharger.bootstrap.wizard_import import (
    ImportedWizardDefaults,
    _adapter_path,
    _as_bool,
    _as_float,
    _as_int,
    _load_from_result_json,
    _policy_mode,
    _profile_defaults_from_types,
    _switch_group_member_host,
    load_imported_defaults,
)
from venus_evcharger.bootstrap.wizard_models import WizardResult
from venus_evcharger.bootstrap.wizard_persistence import _topology_summary_text
from venus_evcharger.bootstrap.wizard_policy_guidance import prompt_policy_defaults
from venus_evcharger.bootstrap.wizard_transport_guidance import preset_specific_defaults
from venus_evcharger.core.common_schedule import scheduled_mode_snapshot


def _imported_defaults(**overrides: object) -> ImportedWizardDefaults:
    values = {
        "imported_from": "",
        "profile": None,
        "host_input": None,
        "meter_host_input": None,
        "switch_host_input": None,
        "charger_host_input": None,
        "device_instance": None,
        "phase": None,
        "policy_mode": None,
        "digest_auth": None,
        "username": None,
        "password": None,
        "split_preset": None,
        "charger_backend": None,
        "charger_preset": None,
        "request_timeout_seconds": None,
        "switch_group_phase_layout": None,
        "auto_start_surplus_watts": None,
        "auto_stop_surplus_watts": None,
        "auto_min_soc": None,
        "auto_resume_soc": None,
        "scheduled_enabled_days": None,
        "scheduled_latest_end_time": None,
        "scheduled_night_current_amps": None,
        "transport_kind": None,
        "transport_host": None,
        "transport_port": None,
        "transport_device": None,
        "transport_unit_id": None,
    }
    values.update(overrides)
    return ImportedWizardDefaults(**values)


def _namespace(**overrides: object) -> argparse.Namespace:
    values = {
        "profile": None,
        "split_preset": None,
        "charger_backend": None,
        "charger_preset": None,
        "host": None,
        "meter_host": None,
        "switch_host": None,
        "charger_host": None,
        "device_instance": None,
        "phase": None,
        "policy_mode": None,
        "transport": None,
        "transport_host": None,
        "transport_port": None,
        "transport_device": None,
        "transport_unit_id": None,
        "digest_auth": False,
        "username": None,
        "password": None,
        "import_config": None,
        "resume_last": False,
        "clone_current": False,
        "yes": False,
        "force": False,
        "dry_run": False,
        "json": False,
        "live_check": False,
        "probe_roles": None,
        "request_timeout_seconds": None,
        "switch_group_phase_layout": None,
        "auto_start_surplus_watts": None,
        "auto_stop_surplus_watts": None,
        "auto_min_soc": None,
        "auto_resume_soc": None,
        "scheduled_enabled_days": None,
        "scheduled_latest_end_time": None,
        "scheduled_night_current_amps": None,
        "non_interactive": False,
        "config_path": "/tmp/config.ini",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _result() -> WizardResult:
    return WizardResult(
        created_at="2026-04-20T02:53:57",
        config_path="/tmp/config.ini",
        imported_from=None,
        profile="simple-relay",
        policy_mode="manual",
        split_preset=None,
        charger_backend=None,
        charger_preset=None,
        transport_kind=None,
        role_hosts={},
        validation={"resolved_roles": {"meter": False}},
        live_check=None,
        generated_files=("config.ini",),
        backup_files=tuple(),
        result_path=None,
        audit_path=None,
        topology_summary_path=None,
        manual_review=("Auth",),
        dry_run=False,
        warnings=tuple(),
        answer_defaults={},
    )


class TestShellyWallboxWizardBranchRuntime(unittest.TestCase):
    def test_wizard_core_helpers_cover_live_checks_write_paths_and_main_guard(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing required key"):
            wizard._replace_assignment("Host=foo\n", "DeviceInstance", "60")
        self.assertEqual(wizard._append_backends("[Backends]\nX=1\n\n[Other]\nA=1\n", []), "[Other]\nA=1\n")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            main_path = temp_path / "config.ini"
            main_path.write_text("[DEFAULT]\nHost=demo\n", encoding="utf-8")

            selection = SimpleNamespace(
                meter_config_path=Path("meter.ini"),
                switch_config_path=None,
                charger_config_path=Path("charger.ini"),
            )
            (temp_path / "meter.ini").write_text("meter", encoding="utf-8")
            (temp_path / "charger.ini").write_text("charger", encoding="utf-8")
            with (
                patch("venus_evcharger.bootstrap.wizard.load_backend_selection", return_value=selection),
                patch("venus_evcharger.bootstrap.wizard.probe_meter_backend", return_value={"type": "meter"}) as meter_probe,
                patch("venus_evcharger.bootstrap.wizard.read_charger_backend", side_effect=RuntimeError("boom")),
            ):
                payload = wizard._live_connectivity_payload(main_path, ("meter", "charger"))

            self.assertFalse(payload["ok"])
            self.assertEqual(payload["roles"]["switch"]["status"], "skipped")
            self.assertEqual(payload["roles"]["charger"]["status"], "error")
            meter_probe.assert_called_once_with(str(temp_path / "meter.ini"))

            with (
                patch("venus_evcharger.bootstrap.wizard.load_backend_selection", return_value=selection),
                patch("venus_evcharger.bootstrap.wizard.probe_meter_backend", return_value={"type": "meter"}),
                patch("venus_evcharger.bootstrap.wizard.probe_switch_backend", return_value={"type": "switch"}),
                patch("venus_evcharger.bootstrap.wizard.read_charger_backend", return_value={"type": "charger"}),
            ):
                skipped_payload = wizard._live_connectivity_payload(main_path, None)
            self.assertEqual(skipped_payload["roles"]["switch"]["status"], "skipped")
            self.assertEqual(skipped_payload["roles"]["switch"]["reason"], "not configured")

            with patch(
                "venus_evcharger.bootstrap.wizard._live_connectivity_payload",
                side_effect=lambda path, roles: {"ok": path.exists() and (path.parent / "adapter.ini").exists(), "checked_roles": roles or (), "roles": {}},
            ):
                live_payload = wizard._live_check_rendered_setup(
                    "[DEFAULT]\nAdapter=adapter.ini\n",
                    {"adapter.ini": "[Adapter]\n"},
                    "config.ini",
                    ("meter",),
                )
            self.assertTrue(live_payload["ok"])

            config_path = temp_path / "written.ini"
            adapter_path = temp_path / "adapter.ini"
            config_path.write_text("old\n", encoding="utf-8")
            adapter_path.write_text("old\n", encoding="utf-8")
            backups = wizard._write_generated_files(config_path, "new\n", {"adapter.ini": "new\n"})
            self.assertEqual(len(backups), 2)

            self.assertFalse(wizard._non_interactive_write_allowed(_namespace(), tuple()))
            with self.assertRaisesRegex(ValueError, "Refusing to overwrite existing files"):
                wizard._non_interactive_write_allowed(_namespace(non_interactive=True), ("existing.ini",))
            self.assertTrue(wizard._non_interactive_write_allowed(_namespace(non_interactive=True, force=True), ("existing.ini",)))

            preview = _result()
            with patch("venus_evcharger.bootstrap.wizard._interactive_write_confirmed", return_value=False):
                with self.assertRaisesRegex(ValueError, "cancelled"):
                    wizard._confirm_write(_namespace(), preview, ("existing.ini",))
            with patch("venus_evcharger.bootstrap.wizard._interactive_write_confirmed", return_value=True):
                wizard._confirm_write(_namespace(), preview, ("existing.ini",))
            with (
                patch("venus_evcharger.bootstrap.wizard.prompt_yes_no", return_value=True),
                redirect_stdout(io.StringIO()),
            ):
                self.assertTrue(wizard._interactive_write_confirmed(preview, ("existing.ini",)))

            with patch("venus_evcharger.bootstrap.wizard.prompt_yes_no", return_value=True):
                self.assertTrue(wizard._resolve_live_check(_namespace()))
            self.assertFalse(wizard._resolve_live_check(_namespace(non_interactive=True)))

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as raised:
                with patch("sys.argv", ["wizard.py", "--non-interactive", "--dry-run", "--json", "--profile", "simple-relay", "--host", "192.168.1.44"]):
                    runpy.run_module("venus_evcharger.bootstrap.wizard", run_name="__main__")
        self.assertEqual(raised.exception.code, 0)
        self.assertIn('"profile": "simple-relay"', stdout.getvalue())

        error_stdout = io.StringIO()
        with redirect_stdout(error_stdout):
            rc = wizard.main(["--non-interactive", "--json"])
        self.assertEqual(rc, 2)
        self.assertIn('"error"', error_stdout.getvalue())

    def test_wizard_cli_helpers_cover_prompt_loops_imports_and_noninteractive_errors(self) -> None:
        with patch("builtins.input", return_value="typed"):
            self.assertEqual(wizard_cli._prompt_text("Prompt", "default"), "typed")
        with patch("builtins.input", return_value=""):
            self.assertEqual(wizard_cli._prompt_text("Prompt", "default"), "default")
        with patch("builtins.input", return_value=""):
            self.assertTrue(wizard_cli.prompt_yes_no("Question", True))
        with patch("builtins.input", return_value="yes"):
            self.assertTrue(wizard_cli.prompt_yes_no("Question", False))

        self.assertEqual(wizard_cli._choice_from_raw("2", ("a", "b")), "b")
        self.assertIsNone(wizard_cli._choice_from_raw("9", ("a", "b")))
        self.assertIsNone(wizard_cli._choice_from_raw("x", ("a", "b")))
        self.assertEqual(wizard_cli._resolved_choice_input("", ("a", "b"), "a"), "a")

        parser = wizard_cli.build_parser("/tmp/config.ini", "/tmp/template.ini")
        namespace = parser.parse_args(
            [
                "--energy-recommendation-prefix",
                "/tmp/energy-rec",
                "--huawei-recommendation-prefix",
                "/tmp/huawei-rec",
                "--huawei-recommendation-prefix",
                "/tmp/huawei-rec-2",
                "--apply-energy-merge",
                "--energy-default-usable-capacity-wh",
                "12288",
                "--huawei-usable-capacity-wh",
                "15360",
                "--energy-usable-capacity-wh",
                "hybrid_a=10240",
            ]
        )
        self.assertEqual(namespace.energy_recommendation_prefix, ["/tmp/energy-rec"])
        self.assertEqual(namespace.huawei_recommendation_prefix, ["/tmp/huawei-rec", "/tmp/huawei-rec-2"])
        self.assertTrue(namespace.apply_energy_merge)
        self.assertEqual(namespace.energy_default_usable_capacity_wh, 12288.0)
        self.assertEqual(namespace.huawei_usable_capacity_wh, 15360.0)
        self.assertEqual(namespace.energy_usable_capacity_wh, ["hybrid_a=10240"])
        namespace = parser.parse_args(["--resume-last", "--config-path", "/tmp/missing.ini"])
        with self.assertRaisesRegex(ValueError, "no prior wizard result exists"):
            wizard_cli.resolve_imported_defaults(namespace)

        namespace = parser.parse_args(["--clone-current", "--config-path", "/tmp/missing.ini"])
        with self.assertRaisesRegex(ValueError, "config does not exist"):
            wizard_cli.resolve_imported_defaults(namespace)

        with patch("builtins.input", side_effect=["9", "2"]):
            self.assertEqual(wizard_cli._prompt_choice("Pick", ("a", "b")), "b")

        with patch("venus_evcharger.bootstrap.wizard_cli.prompt_yes_no", return_value=True):
            self.assertEqual(wizard_cli._prompt_password("imported-secret"), "imported-secret")
        with patch("venus_evcharger.bootstrap.wizard_cli.getpass.getpass", return_value="typed-secret"):
            self.assertEqual(wizard_cli._prompt_password(""), "typed-secret")

        imported = _imported_defaults()
        with patch("venus_evcharger.bootstrap.wizard_cli.prompt_yes_no", return_value=True):
            self.assertTrue(wizard_cli._interactive_digest_auth(_namespace(), imported))
        self.assertTrue(wizard_cli._interactive_digest_auth(_namespace(digest_auth=True), imported))
        self.assertEqual(wizard_cli._interactive_backend_choice("advanced-manual", "template_charger"), "template_charger")
        with patch("venus_evcharger.bootstrap.wizard_cli._prompt_choice", return_value="goe_charger"):
            self.assertEqual(wizard_cli._interactive_backend_choice("native-charger", None), "goe_charger")
        with patch("venus_evcharger.bootstrap.wizard_cli._prompt_choice", return_value="simpleevse_charger"):
            self.assertEqual(wizard_cli._interactive_backend_choice("native-charger-phase-switch", None), "simpleevse_charger")
        self.assertEqual(
            wizard_cli._interactive_backend(_namespace(charger_backend="modbus_charger"), "native-charger", imported, None),
            "modbus_charger",
        )
        self.assertEqual(
            wizard_cli._interactive_charger_preset(_namespace(charger_preset="abb-terra-ac-modbus"), imported, "modbus_charger"),
            "abb-terra-ac-modbus",
        )
        with self.assertRaisesRegex(ValueError, "is not supported for backend goe_charger"):
            wizard_cli._interactive_charger_preset(_namespace(charger_preset="abb-terra-ac-modbus"), imported, "goe_charger")
        with patch("venus_evcharger.bootstrap.wizard_cli._prompt_optional_choice", return_value=None):
            self.assertIsNone(
                wizard_cli._interactive_charger_preset(
                    _namespace(),
                    _imported_defaults(charger_preset="unsupported"),
                    "modbus_charger",
                )
            )
        self.assertEqual(
            wizard_cli._non_interactive_charger_preset(
                _namespace(charger_preset="abb-terra-ac-modbus"),
                _imported_defaults(),
                "goe_charger",
            ),
            "abb-terra-ac-modbus",
        )
        with self.assertRaisesRegex(ValueError, "is not supported for backend goe_charger"):
            wizard_cli._non_interactive_charger_preset(
                _namespace(charger_preset="not-a-real-preset"),
                _imported_defaults(),
                "goe_charger",
            )
        with patch("venus_evcharger.bootstrap.wizard_cli._prompt_choice", return_value="none"):
            self.assertIsNone(
                wizard_cli._prompt_optional_choice(
                    "Choose",
                    ("none", "abb-terra-ac-modbus"),
                    {"none": "None", "abb-terra-ac-modbus": "ABB"},
                    None,
                )
            )
        self.assertEqual(
            wizard_cli._interactive_transport_inputs("goe_charger", None, "goe.local", imported),
            ("serial_rtu", "goe.local", 502, "/dev/ttyUSB0", 1),
        )
        with patch("venus_evcharger.bootstrap.wizard_cli.prompt_transport_inputs", return_value=("tcp", "modbus.local", 1502, "/dev/ttyUSB0", 3)):
            self.assertEqual(
                wizard_cli._interactive_transport_inputs("modbus_charger", "abb-terra-ac-modbus", "modbus.local", imported),
                ("tcp", "modbus.local", 1502, "/dev/ttyUSB0", 3),
            )
        self.assertEqual(wizard_cli._interactive_split_preset(_namespace(), _imported_defaults(), "native-charger"), None)
        with patch("venus_evcharger.bootstrap.wizard_cli.prompt_split_preset", return_value="template-stack"):
            self.assertEqual(
                wizard_cli._interactive_split_preset(_namespace(), _imported_defaults(), "split-topology"),
                "template-stack",
            )
        self.assertEqual(wizard_cli._interactive_username(_namespace(username="user"), imported, True), "user")
        self.assertEqual(wizard_cli._interactive_password(_namespace(password="secret"), imported, True), "secret")
        with patch("venus_evcharger.bootstrap.wizard_cli._prompt_text", return_value="77"):
            self.assertEqual(wizard_cli._interactive_device_instance(_namespace(), imported), 77)

        stdout = io.StringIO()
        with (
            patch("venus_evcharger.bootstrap.wizard_cli._interactive_profile", return_value="split-topology"),
            patch("venus_evcharger.bootstrap.wizard_cli._prompt_text", side_effect=["shared.local", "81"]),
            patch("venus_evcharger.bootstrap.wizard_cli._interactive_split_preset", return_value="template-stack"),
            patch("venus_evcharger.bootstrap.wizard_cli._interactive_backend", return_value="template_charger"),
            patch("venus_evcharger.bootstrap.wizard_cli.prompt_role_hosts", return_value=("meter.local", "switch.local", "charger.local")),
            patch("venus_evcharger.bootstrap.wizard_cli._interactive_transport_inputs", return_value=("serial_rtu", "shared.local", 502, "/dev/ttyUSB0", 1)),
            patch("venus_evcharger.bootstrap.wizard_cli._interactive_auth_inputs", return_value=(False, "", "")),
            patch("venus_evcharger.bootstrap.wizard_cli._interactive_policy_mode", return_value="manual"),
            patch("venus_evcharger.bootstrap.wizard_cli.prompt_preset_specific_defaults", return_value=(None, "P1,P1_P2,P1_P2_P3")),
            patch("venus_evcharger.bootstrap.wizard_cli.prompt_policy_defaults", return_value=(None, None, None, None, None, None, None)),
            patch("venus_evcharger.bootstrap.wizard_cli.role_prompt_intro", return_value="Intro text"),
            redirect_stdout(stdout),
        ):
            answers = wizard_cli._interactive_answers(_namespace(phase="L1"), imported)
        self.assertEqual(answers.device_instance, 81)
        self.assertIn("Intro text", stdout.getvalue())

        with self.assertRaisesRegex(ValueError, "--profile is required"):
            wizard_cli._non_interactive_profile(_namespace(), _imported_defaults())
        self.assertTrue(wizard_cli._non_interactive_digest_auth(_namespace(digest_auth=True), _imported_defaults()))

    def test_branch_helpers_cover_remaining_prompt_and_import_edges(self) -> None:
        self.assertEqual(prompt_split_preset(lambda *_args: "template-stack", "template-stack"), "template-stack")
        self.assertEqual(resolved_primary_host(_namespace(), _imported_defaults(), None, None, None), "192.168.1.50")

        result_text = wizard_cli.result_text(
            _result()._replace(
                live_check={"ok": True, "roles": {}}
            ) if False else WizardResult(
                created_at="2026-04-20T02:53:57",
                config_path="/tmp/config.ini",
                imported_from=None,
                profile="simple-relay",
                policy_mode="manual",
                split_preset=None,
                charger_backend=None,
                charger_preset=None,
                transport_kind=None,
                role_hosts={},
                validation={"resolved_roles": {"meter": False}},
                live_check={"ok": True, "roles": {}},
                generated_files=("config.ini",),
                backup_files=tuple(),
                result_path=None,
                audit_path=None,
                topology_summary_path=None,
                manual_review=("Auth",),
                dry_run=False,
                warnings=tuple(),
                answer_defaults={},
            )
        )
        self.assertNotIn("  - meter:", result_text)

        hinted_result_text = wizard_cli.result_text(
            WizardResult(
                created_at="2026-04-20T02:53:57",
                config_path="/tmp/config.ini",
                imported_from=None,
                profile="simple-relay",
                policy_mode="manual",
                split_preset=None,
                charger_backend=None,
                charger_preset=None,
                transport_kind=None,
                role_hosts={},
                validation={"resolved_roles": {"meter": False}},
                live_check=None,
                generated_files=("config.ini", "wizard-huawei-energy.ini"),
                backup_files=tuple(),
                result_path=None,
                audit_path=None,
                topology_summary_path=None,
                manual_review=("Auth", "External energy source integration"),
                dry_run=False,
                warnings=tuple(),
                answer_defaults={},
                suggested_blocks={
                    "External energy source": "AutoEnergySource.huawei.Profile=huawei_mb_sdongle\n"
                },
                suggested_energy_sources=(
                    {
                        "source_id": "huawei",
                        "profile": "huawei_mb_sdongle",
                        "configPath": "/data/etc/huawei-mb-modbus.ini",
                        "host": "192.168.8.1",
                        "port": 502,
                        "unitId": 1,
                        "usableCapacityWh": 15360.0,
                        "capacityConfigKey": "AutoEnergySource.huawei.UsableCapacityWh",
                    },
                ),
                suggested_energy_merge={
                    "merged_source_ids": ["victron", "huawei"],
                    "helper_file": "wizard-auto-energy-merge.ini",
                    "applied_to_config": True,
                    "capacity_follow_up": [
                        {
                            "source_id": "huawei",
                            "config_key": "AutoEnergySource.huawei.UsableCapacityWh",
                            "placeholder": "15360",
                            "configured": True,
                        }
                    ],
                    "merge_block": (
                        "AutoEnergySources=victron,huawei\n"
                        "AutoEnergySource.huawei.Profile=huawei_mb_sdongle\n"
                        "AutoEnergySource.huawei.UsableCapacityWh=15360\n"
                    ),
                },
            )
        )
        self.assertIn("Suggested energy sources:", hinted_result_text)
        self.assertIn("profile=huawei_mb_sdongle", hinted_result_text)
        self.assertIn("capacity follow-up: AutoEnergySource.huawei.UsableCapacityWh=<set-me>", hinted_result_text)
        self.assertIn("Suggested AutoEnergy merge:", hinted_result_text)
        self.assertIn("merged source ids: victron,huawei", hinted_result_text)
        self.assertIn("wizard-auto-energy-merge.ini", hinted_result_text)
        self.assertIn("applied to main config: yes", hinted_result_text)
        self.assertIn("AutoEnergySource.huawei.UsableCapacityWh=15360", hinted_result_text)
        self.assertIn("Suggested config blocks:", hinted_result_text)
        self.assertIn("AutoEnergySource.huawei.Profile=huawei_mb_sdongle", hinted_result_text)

    def test_resolved_energy_capacity_wh_prompts_only_for_single_energy_recommendation(self) -> None:
        self.assertIsNone(wizard._resolved_energy_capacity_wh(_namespace(non_interactive=True), tuple()))
        self.assertEqual(
            wizard._resolved_energy_capacity_wh(
                _namespace(non_interactive=True, energy_default_usable_capacity_wh=12000.0),
                ("/tmp/huawei-rec",),
            ),
            12000.0,
        )
        with (
            patch("venus_evcharger.bootstrap.wizard.prompt_yes_no", return_value=True),
            patch("builtins.input", return_value="15360"),
        ):
            self.assertEqual(
                wizard._resolved_energy_capacity_wh(
                    _namespace(energy_recommendation_prefix=["/tmp/huawei-rec"]),
                    ("/tmp/huawei-rec",),
                ),
                15360.0,
            )
        self.assertEqual(
            wizard._resolved_energy_capacity_overrides(
                _namespace(energy_usable_capacity_wh=["hybrid_a=15360", "hybrid_b=7680"])
            ),
            {"hybrid_a": 15360.0, "hybrid_b": 7680.0},
        )
        with self.assertRaisesRegex(ValueError, "source_id=Wh"):
            wizard._resolved_energy_capacity_overrides(_namespace(energy_usable_capacity_wh=["broken"]))
        with self.assertRaisesRegex(ValueError, "source_id=Wh"):
            wizard._resolved_energy_capacity_overrides(_namespace(energy_usable_capacity_wh=["hybrid_a=0"]))

        self.assertIsNone(_as_bool(None))
        self.assertIsNone(_as_int(" "))
        self.assertIsNone(_as_float(" "))
        self.assertEqual(_policy_mode("1"), "auto")
        self.assertEqual(_policy_mode("2"), "scheduled")
        self.assertEqual(_policy_mode("0"), "manual")
        self.assertIsNone(_policy_mode("other"))
        self.assertEqual(_profile_defaults_from_types("none", "none", "goe_charger"), ("native-charger", None, "goe_charger"))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            group_path = temp_path / "switch-group.ini"
            group_path.write_text("[Members]\nP1=missing.ini\n", encoding="utf-8")
            config_path = temp_path / "config.ini"
            config_path.write_text("[Backends]\n", encoding="utf-8")
            self.assertIsNone(_adapter_path(config_path, {}, "SwitchConfigPath"))
            self.assertIsNone(_switch_group_member_host(group_path, None))
            self.assertIsNone(_switch_group_member_host(group_path, "missing.ini"))
            phase_path = temp_path / "phase1.ini"
            phase_path.write_text("[Adapter]\nHost=phase1.local\n", encoding="utf-8")
            self.assertEqual(_switch_group_member_host(group_path, str(phase_path)), "phase1.local")

            bad_result_path = temp_path / "bad.wizard-result.json"
            bad_result_path.write_text("[]\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "JSON object"):
                _load_from_result_json(bad_result_path)

            bad_defaults_path = temp_path / "bad-defaults.wizard-result.json"
            bad_defaults_path.write_text(json.dumps({"answer_defaults": []}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing answer_defaults"):
                _load_from_result_json(bad_defaults_path)

            with self.assertRaisesRegex(ValueError, "Import config does not exist"):
                load_imported_defaults(temp_path / "missing.ini")

        summary = _topology_summary_text({"validation": "invalid"})
        self.assertNotIn("resolved_roles:", summary)

        prompted = prompt_policy_defaults(
            "scheduled",
            _imported_defaults(
                scheduled_enabled_days="Mon",
                scheduled_latest_end_time="06:30",
                scheduled_night_current_amps=5.0,
            ),
            _namespace(
                auto_start_surplus_watts=1800.0,
                scheduled_enabled_days="Mon",
                scheduled_latest_end_time="06:30",
                scheduled_night_current_amps=5.0,
            ),
            prompt_text=lambda _label, default: default,
        )
        self.assertEqual(prompted[0], 1800.0)
        self.assertEqual(prompted[4:], ("Mon", "06:30", 5.0))

        timeout, _ = preset_specific_defaults(
            _namespace(request_timeout_seconds=7.5),
            _imported_defaults(),
            backend="goe_charger",
            split_preset=None,
            charger_preset=None,
        )
        self.assertEqual(timeout, 7.5)

    def test_scheduled_mode_snapshot_covers_daytime_window(self) -> None:
        snapshot = scheduled_mode_snapshot(
            datetime(2026, 4, 20, 10, 30),
            {4: ((7, 30), (19, 30))},
            "Mon,Tue,Wed,Thu,Fri",
            delay_seconds=3600.0,
            latest_end_time="06:30",
        )
        self.assertEqual(snapshot.state, "auto-window")
        self.assertEqual(snapshot.reason, "daytime-auto")


if __name__ == "__main__":
    unittest.main()
