# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from shelly_wallbox.bootstrap.wizard import WizardAnswers, configure_wallbox, default_template_path, main
from shelly_wallbox.bootstrap.wizard_cli import build_answers, build_parser


class TestShellyWallboxSetupWizard(unittest.TestCase):
    def test_configure_wallbox_generates_simple_relay_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="simple-relay",
                    host_input="192.168.1.44",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input=None,
                    device_instance=61,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    split_preset=None,
                    charger_backend=None,
                    transport_kind="serial_rtu",
                    transport_host="192.168.1.44",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            parser = configparser.ConfigParser()
            parser.read(config_path, encoding="utf-8")
            topology_text = (config_path.parent / "config.ini.wizard-topology.txt").read_text(encoding="utf-8")
            self.assertEqual(parser["DEFAULT"]["Host"], "192.168.1.44")
            self.assertEqual(parser["DEFAULT"]["DeviceInstance"], "61")
            self.assertFalse(parser.has_section("Backends"))
            self.assertTrue((config_path.parent / "config.ini.wizard-result.json").exists())
            self.assertTrue((config_path.parent / "config.ini.wizard-audit.jsonl").exists())
            self.assertTrue((config_path.parent / "config.ini.wizard-topology.txt").exists())
            self.assertIn("profile: simple-relay\n", topology_text)
            self.assertIn("role_hosts:\n  - none\n", topology_text)
            self.assertEqual(result.role_hosts, {})
            self.assertEqual(result.validation["resolved_roles"], {"meter": True, "switch": True, "charger": False})

    def test_configure_wallbox_generates_native_goe_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="native-charger",
                    host_input="goe.local",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input="goe.local",
                    device_instance=62,
                    phase="3P",
                    policy_mode="auto",
                    digest_auth=False,
                    username="",
                    password="",
                    split_preset=None,
                    charger_backend="goe_charger",
                    transport_kind="serial_rtu",
                    transport_host="goe.local",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            config_text = config_path.read_text(encoding="utf-8")
            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            self.assertIn("[Backends]\nMode=split\nMeterType=none\nSwitchType=none\n", config_text)
            self.assertIn("ChargerType=goe_charger\n", config_text)
            self.assertIn("Type=goe_charger\nBaseUrl=http://goe.local\n", charger_text)
            self.assertEqual(result.role_hosts, {"charger": "goe.local"})
            self.assertEqual(result.validation["resolved_roles"], {"meter": False, "switch": False, "charger": True})

    def test_configure_wallbox_generates_phase_switch_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="native-charger-phase-switch",
                    host_input="192.168.1.80",
                    meter_host_input=None,
                    switch_host_input="switch.local",
                    charger_host_input="charger.local",
                    device_instance=63,
                    phase="3P",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    split_preset=None,
                    charger_backend="simpleevse_charger",
                    transport_kind="serial_rtu",
                    transport_host="192.168.1.80",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            group_text = (config_path.parent / "wizard-switch-group.ini").read_text(encoding="utf-8")
            phase1_text = (config_path.parent / "wizard-phase1-switch.ini").read_text(encoding="utf-8")
            self.assertIn("SwitchType=switch_group\n", config_path.read_text(encoding="utf-8"))
            self.assertIn("P1=wizard-phase1-switch.ini\n", group_text)
            self.assertIn("BaseUrl=http://switch.local\n", phase1_text)
            self.assertEqual(result.role_hosts, {"charger": "charger.local", "switch": "switch.local"})
            self.assertEqual(result.validation["resolved_roles"], {"meter": False, "switch": True, "charger": True})

    def test_configure_wallbox_generates_native_modbus_tcp_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="native-charger",
                    host_input="192.168.1.90",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input=None,
                    device_instance=64,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    split_preset=None,
                    charger_backend="modbus_charger",
                    transport_kind="tcp",
                    transport_host="192.168.1.91",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=7,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            self.assertIn("Type=modbus_charger\n", charger_text)
            self.assertIn("Transport=tcp\n", charger_text)
            self.assertIn("Host=192.168.1.91\n", charger_text)
            self.assertIn("UnitId=7\n", charger_text)
            self.assertEqual(result.transport_kind, "tcp")
            self.assertEqual(result.validation["resolved_roles"], {"meter": False, "switch": False, "charger": True})

    def test_configure_wallbox_generates_split_preset_with_shelly_io_and_modbus(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="split-topology",
                    host_input="192.168.1.92",
                    meter_host_input="192.168.1.20",
                    switch_host_input="192.168.1.21",
                    charger_host_input=None,
                    device_instance=65,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    split_preset="shelly-io-modbus-charger",
                    charger_backend="modbus_charger",
                    transport_kind="tcp",
                    transport_host="192.168.1.93",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=8,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            meter_text = (config_path.parent / "wizard-meter.ini").read_text(encoding="utf-8")
            switch_text = (config_path.parent / "wizard-switch.ini").read_text(encoding="utf-8")
            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            self.assertIn("Type=shelly_meter\n", meter_text)
            self.assertIn("Type=shelly_switch\n", switch_text)
            self.assertIn("Type=modbus_charger\n", charger_text)
            self.assertIn("Host=192.168.1.20\n", meter_text)
            self.assertIn("Host=192.168.1.21\n", switch_text)
            self.assertEqual(result.role_hosts, {"meter": "192.168.1.20", "switch": "192.168.1.21"})
            self.assertEqual(result.split_preset, "shelly-io-modbus-charger")
            self.assertEqual(result.validation["resolved_roles"], {"meter": True, "switch": True, "charger": True})

    def test_configure_wallbox_generates_split_preset_with_shelly_meter_and_goe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="split-topology",
                    host_input="goe.local",
                    meter_host_input="meter.local",
                    switch_host_input=None,
                    charger_host_input="charger.local",
                    device_instance=66,
                    phase="3P",
                    policy_mode="auto",
                    digest_auth=False,
                    username="",
                    password="",
                    split_preset="shelly-meter-goe",
                    charger_backend="goe_charger",
                    transport_kind="serial_rtu",
                    transport_host="goe.local",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            config_text = config_path.read_text(encoding="utf-8")
            meter_text = (config_path.parent / "wizard-meter.ini").read_text(encoding="utf-8")
            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            self.assertIn("MeterType=shelly_meter\n", config_text)
            self.assertIn("SwitchType=none\n", config_text)
            self.assertIn("ChargerType=goe_charger\n", config_text)
            self.assertIn("Type=shelly_meter\n", meter_text)
            self.assertIn("Host=meter.local\n", meter_text)
            self.assertIn("Type=goe_charger\nBaseUrl=http://charger.local\n", charger_text)
            self.assertEqual(result.role_hosts, {"charger": "charger.local", "meter": "meter.local"})
            self.assertEqual(result.split_preset, "shelly-meter-goe")
            self.assertEqual(result.validation["resolved_roles"], {"meter": True, "switch": False, "charger": True})

    def test_configure_wallbox_generates_split_preset_with_goe_and_switch_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="split-topology",
                    host_input="goe.local",
                    meter_host_input=None,
                    switch_host_input="switch.local",
                    charger_host_input="charger.local",
                    device_instance=67,
                    phase="3P",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    split_preset="goe-external-switch-group",
                    charger_backend="goe_charger",
                    transport_kind="serial_rtu",
                    transport_host="goe.local",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            config_text = config_path.read_text(encoding="utf-8")
            group_text = (config_path.parent / "wizard-switch-group.ini").read_text(encoding="utf-8")
            phase1_text = (config_path.parent / "wizard-phase1-switch.ini").read_text(encoding="utf-8")
            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            self.assertIn("MeterType=none\n", config_text)
            self.assertIn("SwitchType=switch_group\n", config_text)
            self.assertIn("ChargerType=goe_charger\n", config_text)
            self.assertIn("SupportedPhaseSelections=P1,P1_P2,P1_P2_P3\n", group_text)
            self.assertTrue((config_path.parent / "wizard-phase1-switch.ini").exists())
            self.assertIn("BaseUrl=http://switch.local\n", phase1_text)
            self.assertIn("BaseUrl=http://charger.local\n", charger_text)
            self.assertEqual(result.role_hosts, {"charger": "charger.local", "switch": "switch.local"})
            self.assertEqual(result.split_preset, "goe-external-switch-group")
            self.assertEqual(result.validation["resolved_roles"], {"meter": False, "switch": True, "charger": True})

    def test_configure_wallbox_generates_split_preset_with_template_meter_goe_and_switch_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="split-topology",
                    host_input="adapter.local",
                    meter_host_input="meter.local",
                    switch_host_input="switch.local",
                    charger_host_input="charger.local",
                    device_instance=68,
                    phase="3P",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    split_preset="template-meter-goe-switch-group",
                    charger_backend="goe_charger",
                    transport_kind="serial_rtu",
                    transport_host="adapter.local",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            meter_text = (config_path.parent / "wizard-meter.ini").read_text(encoding="utf-8")
            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            phase1_text = (config_path.parent / "wizard-phase1-switch.ini").read_text(encoding="utf-8")
            self.assertIn("Type=template_meter\n", meter_text)
            self.assertIn("BaseUrl=http://meter.local\n", meter_text)
            self.assertIn("Type=goe_charger\nBaseUrl=http://charger.local\n", charger_text)
            self.assertIn("BaseUrl=http://switch.local\n", phase1_text)
            self.assertEqual(result.role_hosts, {"charger": "charger.local", "meter": "meter.local", "switch": "switch.local"})
            self.assertEqual(result.split_preset, "template-meter-goe-switch-group")
            self.assertEqual(result.validation["resolved_roles"], {"meter": True, "switch": True, "charger": True})

    def test_configure_wallbox_generates_split_preset_with_shelly_meter_modbus_and_switch_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="split-topology",
                    host_input="192.168.1.94",
                    meter_host_input="192.168.1.24",
                    switch_host_input="switch.local",
                    charger_host_input=None,
                    device_instance=69,
                    phase="3P",
                    policy_mode="scheduled",
                    digest_auth=False,
                    username="",
                    password="",
                    split_preset="shelly-meter-modbus-switch-group",
                    charger_backend="modbus_charger",
                    transport_kind="tcp",
                    transport_host="192.168.1.95",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=9,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            group_text = (config_path.parent / "wizard-switch-group.ini").read_text(encoding="utf-8")
            meter_text = (config_path.parent / "wizard-meter.ini").read_text(encoding="utf-8")
            phase1_text = (config_path.parent / "wizard-phase1-switch.ini").read_text(encoding="utf-8")
            self.assertIn("Type=modbus_charger\n", charger_text)
            self.assertIn("Transport=tcp\n", charger_text)
            self.assertIn("Host=192.168.1.95\n", charger_text)
            self.assertIn("SupportedPhaseSelections=P1,P1_P2,P1_P2_P3\n", group_text)
            self.assertIn("Host=192.168.1.24\n", meter_text)
            self.assertIn("BaseUrl=http://switch.local\n", phase1_text)
            self.assertEqual(result.role_hosts, {"meter": "192.168.1.24", "switch": "switch.local"})
            self.assertEqual(result.split_preset, "shelly-meter-modbus-switch-group")
            self.assertEqual(result.transport_kind, "tcp")
            self.assertEqual(result.validation["resolved_roles"], {"meter": True, "switch": True, "charger": True})

    def test_main_dry_run_emits_json_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(
                    [
                        "--non-interactive",
                        "--dry-run",
                        "--json",
                        "--config-path",
                        str(config_path),
                        "--profile",
                        "split-topology",
                        "--host",
                        "adapter.local",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertFalse(config_path.exists())
            self.assertEqual(payload["profile"], "split-topology")
            self.assertEqual(payload["validation"]["resolved_roles"], {"meter": True, "switch": True, "charger": True})

    def test_main_non_interactive_refuses_existing_files_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("legacy\n", encoding="utf-8")

            rc = main(
                [
                    "--non-interactive",
                    "--config-path",
                    str(config_path),
                    "--profile",
                    "simple-relay",
                    "--host",
                    "192.168.1.44",
                ]
            )

            self.assertEqual(rc, 2)
            self.assertEqual(config_path.read_text(encoding="utf-8"), "legacy\n")

    def test_main_non_interactive_force_overwrites_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            config_path.write_text("legacy\n", encoding="utf-8")

            rc = main(
                [
                    "--non-interactive",
                    "--force",
                    "--config-path",
                    str(config_path),
                    "--profile",
                    "simple-relay",
                    "--host",
                    "192.168.1.55",
                ]
            )

            parser = configparser.ConfigParser()
            parser.read(config_path, encoding="utf-8")
            self.assertEqual(rc, 0)
            self.assertEqual(parser["DEFAULT"]["Host"], "192.168.1.55")
            self.assertTrue(any(path.name.startswith("config.ini.wizard-backup-") for path in config_path.parent.iterdir()))

    def test_main_import_config_seeds_non_interactive_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.ini"
            configure_wallbox(
                WizardAnswers(
                    profile="split-topology",
                    host_input="192.168.1.92",
                    meter_host_input="192.168.1.20",
                    switch_host_input="192.168.1.21",
                    charger_host_input=None,
                    device_instance=65,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    split_preset="shelly-io-modbus-charger",
                    charger_backend="modbus_charger",
                    transport_kind="tcp",
                    transport_host="192.168.1.93",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=8,
                ),
                config_path=source_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(
                    [
                        "--non-interactive",
                        "--dry-run",
                        "--json",
                        "--import-config",
                        str(source_path),
                        "--config-path",
                        str(Path(temp_dir) / "preview.ini"),
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["imported_from"], str(source_path))
            self.assertEqual(payload["profile"], "split-topology")
            self.assertEqual(payload["split_preset"], "shelly-io-modbus-charger")
            self.assertEqual(payload["charger_backend"], "modbus_charger")
            self.assertEqual(payload["transport_kind"], "tcp")
            self.assertEqual(payload["role_hosts"], {"meter": "192.168.1.20", "switch": "192.168.1.21"})

    def test_main_import_config_recognizes_goe_switch_group_split_preset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.ini"
            configure_wallbox(
                WizardAnswers(
                    profile="split-topology",
                    host_input="goe.local",
                    meter_host_input="meter.local",
                    switch_host_input="switch.local",
                    charger_host_input="charger.local",
                    device_instance=70,
                    phase="3P",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    split_preset="shelly-meter-goe-switch-group",
                    charger_backend="goe_charger",
                    transport_kind="serial_rtu",
                    transport_host="goe.local",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=source_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(
                    [
                        "--non-interactive",
                        "--dry-run",
                        "--json",
                        "--import-config",
                        str(source_path),
                        "--config-path",
                        str(Path(temp_dir) / "preview.ini"),
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["split_preset"], "shelly-meter-goe-switch-group")
            self.assertEqual(payload["charger_backend"], "goe_charger")
            self.assertEqual(
                payload["role_hosts"],
                {"charger": "http://charger.local", "meter": "meter.local", "switch": "http://switch.local"},
            )

    def test_main_dry_run_uses_role_specific_host_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(
                    [
                        "--non-interactive",
                        "--dry-run",
                        "--json",
                        "--config-path",
                        str(config_path),
                        "--profile",
                        "split-topology",
                        "--split-preset",
                        "template-meter-goe-switch-group",
                        "--meter-host",
                        "meter.local",
                        "--switch-host",
                        "http://switch.local",
                        "--charger-host",
                        "goe.local",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(
                payload["role_hosts"],
                {"charger": "goe.local", "meter": "meter.local", "switch": "http://switch.local"},
            )

    def test_main_clone_current_reuses_existing_config_as_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            configure_wallbox(
                WizardAnswers(
                    profile="simple-relay",
                    host_input="192.168.1.44",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input=None,
                    device_instance=61,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    split_preset=None,
                    charger_backend=None,
                    transport_kind="serial_rtu",
                    transport_host="192.168.1.44",
                    transport_port=502,
                    transport_device="/dev/ttyUSB0",
                    transport_unit_id=1,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(
                    [
                        "--non-interactive",
                        "--dry-run",
                        "--json",
                        "--clone-current",
                        "--config-path",
                        str(config_path),
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["imported_from"], str(config_path))
            self.assertEqual(payload["profile"], "simple-relay")
            self.assertEqual(payload["config_path"], str(config_path))

    def test_build_answers_interactive_uses_hidden_password_prompt(self) -> None:
        parser = build_parser("/tmp/config.ini", str(default_template_path()))
        namespace = parser.parse_args(
            ["--profile", "simple-relay", "--phase", "L1", "--policy-mode", "manual", "--device-instance", "60"]
        )
        with (
            patch("shelly_wallbox.bootstrap.wizard_cli._prompt_text", side_effect=["192.168.1.50", "admin"]),
            patch("shelly_wallbox.bootstrap.wizard_cli.prompt_yes_no", return_value=True),
            patch("shelly_wallbox.bootstrap.wizard_cli.getpass.getpass", return_value="very-secret") as password_prompt,
        ):
            answers, _ = build_answers(namespace)

        self.assertTrue(answers.digest_auth)
        self.assertEqual(answers.username, "admin")
        self.assertEqual(answers.password, "very-secret")
        password_prompt.assert_called_once_with("Password: ")

    def test_main_dry_run_reports_optional_live_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            stdout = io.StringIO()
            with (
                patch(
                    "shelly_wallbox.bootstrap.wizard._live_check_rendered_setup",
                    return_value={
                        "ok": True,
                        "checked_roles": ("charger",),
                        "roles": {"charger": {"status": "ok", "payload": {"type": "goe_charger"}}},
                    },
                ),
                redirect_stdout(stdout),
            ):
                rc = main(
                    [
                        "--non-interactive",
                        "--dry-run",
                        "--json",
                        "--live-check",
                        "--config-path",
                        str(config_path),
                        "--profile",
                        "native-charger",
                        "--charger-backend",
                        "goe_charger",
                        "--host",
                        "goe.local",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(
                payload["live_check"],
                {
                    "checked_roles": ["charger"],
                    "ok": True,
                    "roles": {"charger": {"payload": {"type": "goe_charger"}, "status": "ok"}},
                },
            )


if __name__ == "__main__":
    unittest.main()
