# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from venus_evcharger.bootstrap.wizard import WizardAnswers, configure_wallbox, default_template_path, main


class TestShellyWallboxSetupWizardExtensions(unittest.TestCase):
    def test_configure_wallbox_applies_policy_tuning_and_goe_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="native_device",
                    host_input="goe.local",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input="goe.local",
                    device_instance=71,
                    phase="3P",
                    policy_mode="scheduled",
                    digest_auth=False,
                    username="",
                    password="",
                    charger_backend="goe_charger",
                    request_timeout_seconds=4.5,
                    auto_start_surplus_watts=2100.0,
                    auto_stop_surplus_watts=1650.0,
                    auto_min_soc=35.0,
                    auto_resume_soc=39.0,
                    scheduled_enabled_days="Mon,Wed,Fri",
                    scheduled_latest_end_time="07:15",
                    scheduled_night_current_amps=8.0,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            parser = configparser.ConfigParser()
            parser.read(config_path, encoding="utf-8")
            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            self.assertEqual(parser["DEFAULT"]["AutoStartSurplusWatts"], "2100")
            self.assertEqual(parser["DEFAULT"]["AutoStopSurplusWatts"], "1650")
            self.assertEqual(parser["DEFAULT"]["AutoMinSoc"], "35")
            self.assertEqual(parser["DEFAULT"]["AutoResumeSoc"], "39")
            self.assertEqual(parser["DEFAULT"]["AutoScheduledEnabledDays"], "Mon,Wed,Fri")
            self.assertEqual(parser["DEFAULT"]["AutoScheduledLatestEndTime"], "07:15")
            self.assertEqual(parser["DEFAULT"]["AutoScheduledNightCurrentAmps"], "8")
            self.assertIn("RequestTimeoutSeconds=4.5\n", charger_text)
            self.assertEqual(result.answer_defaults["request_timeout_seconds"], 4.5)

    def test_configure_wallbox_keeps_switch_group_phase_layout_and_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="multi_adapter_topology",
                    host_input="shared.local",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input="goe.local",
                    device_instance=72,
                    phase="3P",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    topology_preset="goe-external-switch-group",
                    charger_backend="goe_charger",
                    switch_group_supported_phase_selections="P1,P1_P2_P3",
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            switch_group = (config_path.parent / "wizard-switch-group.ini").read_text(encoding="utf-8")
            self.assertIn("SupportedPhaseSelections=P1,P1_P2_P3\n", switch_group)
            self.assertTrue(any("1P -> 3P" in warning for warning in result.warnings))
            self.assertTrue(any("shared primary endpoint" in warning for warning in result.warnings))

    def test_main_resume_last_uses_previous_wizard_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            configure_wallbox(
                WizardAnswers(
                    profile="native_device",
                    host_input="goe.local",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input="goe.local",
                    device_instance=73,
                    phase="3P",
                    policy_mode="auto",
                    digest_auth=False,
                    username="wizard",
                    password="secret-value",
                    charger_backend="goe_charger",
                    request_timeout_seconds=3.5,
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
                        "--config-path",
                        str(config_path),
                        "--resume-last",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertTrue(payload["imported_from"].endswith(".wizard-result.json"))
            self.assertEqual(payload["profile"], "native_device")
            self.assertEqual(payload["charger_backend"], "goe_charger")
            self.assertEqual(payload["answer_defaults"]["password_present"], False)
            self.assertIn("device_inventory", payload)
            self.assertTrue(payload["device_inventory"]["profiles"])

    def test_main_probe_role_limits_live_check_to_selected_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            stdout = io.StringIO()
            with (
                patch(
                    "venus_evcharger.bootstrap.wizard._live_check_rendered_setup",
                    return_value={
                        "ok": True,
                        "checked_roles": ("charger",),
                        "roles": {
                            "meter": {"status": "skipped", "reason": "not requested"},
                            "switch": {"status": "skipped", "reason": "not requested"},
                            "charger": {"status": "ok", "payload": {"type": "goe_charger"}},
                        },
                    },
                ),
                redirect_stdout(stdout),
            ):
                rc = main(
                    [
                        "--non-interactive",
                        "--dry-run",
                        "--json",
                        "--config-path",
                        str(config_path),
                        "--profile",
                        "native_device",
                        "--charger-backend",
                        "goe_charger",
                        "--host",
                        "goe.local",
                        "--probe-role",
                        "charger",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["live_check"]["checked_roles"], ["charger"])
            self.assertEqual(payload["live_check"]["roles"]["meter"]["status"], "skipped")
            self.assertEqual(payload["live_check"]["roles"]["charger"]["status"], "ok")

    def test_main_inventory_show_and_add_device(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            configure_wallbox(
                WizardAnswers(
                    profile="simple_relay",
                    host_input="192.168.1.44",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input=None,
                    device_instance=74,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    charger_backend=None,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            show_stdout = io.StringIO()
            with redirect_stdout(show_stdout):
                show_rc = main(
                    [
                        "--json",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "show",
                    ]
                )
            show_payload = json.loads(show_stdout.getvalue())
            self.assertEqual(show_rc, 0)
            self.assertEqual(len(show_payload["devices"]), 1)

            add_stdout = io.StringIO()
            with redirect_stdout(add_stdout):
                add_rc = main(
                    [
                        "--json",
                        "--non-interactive",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "add-device",
                        "--inventory-profile-id",
                        "switch_shelly_contactor_switch",
                        "--inventory-device-id",
                        "switch_aux",
                        "--inventory-label",
                        "Aux switch",
                        "--inventory-endpoint",
                        "http://192.168.1.45",
                    ]
                )
            add_payload = json.loads(add_stdout.getvalue())
            self.assertEqual(add_rc, 0)
            self.assertEqual(add_payload["device_id"], "switch_aux")
            self.assertEqual(len(add_payload["inventory"]["devices"]), 2)

    def test_main_inventory_set_endpoint_and_remove_device(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            configure_wallbox(
                WizardAnswers(
                    profile="native_device",
                    host_input="goe.local",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input="goe.local",
                    device_instance=75,
                    phase="3P",
                    policy_mode="auto",
                    digest_auth=False,
                    username="",
                    password="",
                    charger_backend="goe_charger",
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            endpoint_stdout = io.StringIO()
            with redirect_stdout(endpoint_stdout):
                endpoint_rc = main(
                    [
                        "--json",
                        "--non-interactive",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "set-endpoint",
                        "--inventory-device-id",
                        "charger_device",
                        "--inventory-endpoint",
                        "http://updated.local",
                    ]
                )
            endpoint_payload = json.loads(endpoint_stdout.getvalue())
            self.assertEqual(endpoint_rc, 0)
            self.assertEqual(endpoint_payload["endpoint"], "http://updated.local")

            remove_stdout = io.StringIO()
            with redirect_stdout(remove_stdout):
                remove_rc = main(
                    [
                        "--json",
                        "--non-interactive",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "remove-device",
                        "--inventory-device-id",
                        "charger_device",
                    ]
                )
            remove_payload = json.loads(remove_stdout.getvalue())
            self.assertEqual(remove_rc, 0)
            self.assertEqual(remove_payload["device_id"], "charger_device")
            self.assertEqual(len(remove_payload["inventory"]["devices"]), 0)

    def test_main_inventory_can_add_profile_capability_and_binding_member(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            configure_wallbox(
                WizardAnswers(
                    profile="simple_relay",
                    host_input="192.168.1.44",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input=None,
                    device_instance=76,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    charger_backend=None,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            add_profile_stdout = io.StringIO()
            with redirect_stdout(add_profile_stdout):
                add_profile_rc = main(
                    [
                        "--json",
                        "--non-interactive",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "add-profile",
                        "--inventory-profile-id",
                        "custom_node",
                        "--inventory-label",
                        "Custom node",
                        "--inventory-capability-id",
                        "switch",
                        "--inventory-kind",
                        "switch",
                        "--inventory-adapter-type",
                        "template_switch",
                        "--inventory-supported-phases",
                        "L2",
                        "--inventory-switching-mode",
                        "contactor",
                        "--inventory-supports-feedback",
                    ]
                )
            add_profile_payload = json.loads(add_profile_stdout.getvalue())
            self.assertEqual(add_profile_rc, 0)
            self.assertEqual(add_profile_payload["profile_id"], "custom_node")

            add_capability_stdout = io.StringIO()
            with redirect_stdout(add_capability_stdout):
                add_capability_rc = main(
                    [
                        "--json",
                        "--non-interactive",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "add-capability",
                        "--inventory-profile-id",
                        "custom_node",
                        "--inventory-capability-id",
                        "meter",
                        "--inventory-kind",
                        "meter",
                        "--inventory-adapter-type",
                        "template_meter",
                        "--inventory-supported-phases",
                        "L2",
                        "--inventory-measures-power",
                        "--inventory-measures-energy",
                    ]
                )
            add_capability_payload = json.loads(add_capability_stdout.getvalue())
            self.assertEqual(add_capability_rc, 0)
            custom_profile = next(
                profile
                for profile in add_capability_payload["inventory"]["profiles"]
                if profile["id"] == "custom_node"
            )
            self.assertEqual(len(custom_profile["capabilities"]), 2)

            add_device_stdout = io.StringIO()
            with redirect_stdout(add_device_stdout):
                add_device_rc = main(
                    [
                        "--json",
                        "--non-interactive",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "add-device",
                        "--inventory-profile-id",
                        "custom_node",
                        "--inventory-device-id",
                        "custom_node_l2",
                        "--inventory-label",
                        "Custom node L2",
                        "--inventory-endpoint",
                        "http://custom-node.local",
                    ]
                )
            add_device_payload = json.loads(add_device_stdout.getvalue())
            self.assertEqual(add_device_rc, 0)
            self.assertEqual(add_device_payload["device_id"], "custom_node_l2")

            bind_stdout = io.StringIO()
            with redirect_stdout(bind_stdout):
                bind_rc = main(
                    [
                        "--json",
                        "--non-interactive",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "set-binding-member",
                        "--inventory-binding-id",
                        "custom_measurement",
                        "--inventory-binding-role",
                        "measurement",
                        "--inventory-binding-label",
                        "Custom measurement",
                        "--inventory-device-id",
                        "custom_node_l2",
                        "--inventory-capability-id",
                        "meter",
                        "--inventory-member-phases",
                        "L2",
                    ]
                )
            bind_payload = json.loads(bind_stdout.getvalue())
            self.assertEqual(bind_rc, 0)
            custom_binding = next(
                binding
                for binding in bind_payload["inventory"]["bindings"]
                if binding["id"] == "custom_measurement"
            )
            self.assertEqual(custom_binding["phase_scope"], ["L2"])
            self.assertEqual(custom_binding["members"][0]["device_id"], "custom_node_l2")

            unbind_stdout = io.StringIO()
            with redirect_stdout(unbind_stdout):
                unbind_rc = main(
                    [
                        "--json",
                        "--non-interactive",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "remove-binding-member",
                        "--inventory-binding-id",
                        "custom_measurement",
                        "--inventory-device-id",
                        "custom_node_l2",
                    ]
                )
            unbind_payload = json.loads(unbind_stdout.getvalue())
            self.assertEqual(unbind_rc, 0)
            self.assertFalse(
                any(
                    binding["id"] == "custom_measurement"
                    for binding in unbind_payload["inventory"]["bindings"]
                )
            )

    def test_main_inventory_guided_add_profile_builds_profile_device_and_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            configure_wallbox(
                WizardAnswers(
                    profile="simple_relay",
                    host_input="192.168.1.44",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input=None,
                    device_instance=77,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    charger_backend=None,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            stdout = io.StringIO()
            inventory_path = config_path.with_name(f"{config_path.name}.wizard-inventory.ini")
            with (
                patch(
                    "builtins.input",
                    side_effect=[
                        "custom_meter",
                        "Custom meter",
                        "2",
                        "",
                        "",
                        "L2",
                        "y",
                        "y",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "meter_l2",
                        "",
                        "http://meter-l2.local",
                        "",
                        "",
                        "",
                        "",
                    ],
                ),
                redirect_stdout(stdout),
            ):
                rc = main(
                    [
                        "--json",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "guided-add-profile",
                    ]
                )

            self.assertEqual(rc, 0)
            parser = configparser.ConfigParser()
            parser.read(inventory_path, encoding="utf-8")
            self.assertEqual(parser["Profile:custom_meter"]["Label"], "Custom meter")
            self.assertEqual(parser["Capability:custom_meter:meter"]["Kind"], "meter")
            self.assertEqual(parser["Capability:custom_meter:meter"]["SupportedPhases"], "L2")
            self.assertEqual(parser["Device:meter_l2"]["Profile"], "custom_meter")
            self.assertEqual(parser["Binding:custom_meter_measurement"]["Role"], "measurement")
            self.assertEqual(parser["BindingMember:custom_meter_measurement:1"]["Device"], "meter_l2")

    def test_main_inventory_guided_edit_binding_builds_measurement_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            configure_wallbox(
                WizardAnswers(
                    profile="native_device",
                    host_input="goe.local",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input="goe.local",
                    device_instance=78,
                    phase="3P",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    charger_backend="goe_charger",
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )
            inventory_path = config_path.with_name(f"{config_path.name}.wizard-inventory.ini")

            for profile_id, label, phase, device_id in (
                ("meter_l1_profile", "Meter L1", "L1", "meter_l1"),
                ("meter_l2_profile", "Meter L2", "L2", "meter_l2"),
            ):
                rc_profile = main(
                    [
                        "--json",
                        "--non-interactive",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "add-profile",
                        "--inventory-profile-id",
                        profile_id,
                        "--inventory-label",
                        label,
                        "--inventory-capability-id",
                        "meter",
                        "--inventory-kind",
                        "meter",
                        "--inventory-adapter-type",
                        "template_meter",
                        "--inventory-supported-phases",
                        phase,
                        "--inventory-measures-power",
                        "--inventory-measures-energy",
                    ]
                )
                self.assertEqual(rc_profile, 0)
                rc_device = main(
                    [
                        "--json",
                        "--non-interactive",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "add-device",
                        "--inventory-profile-id",
                        profile_id,
                        "--inventory-device-id",
                        device_id,
                        "--inventory-label",
                        label,
                        "--inventory-endpoint",
                        f"http://{device_id}.local",
                    ]
                )
                self.assertEqual(rc_device, 0)

            stdout = io.StringIO()
            with (
                patch(
                    "builtins.input",
                    side_effect=[
                        "",
                        "garage_measurement",
                        "Garage measurement",
                        "L1,L2",
                        "2",
                        "",
                        "y",
                        "3",
                        "",
                        "n",
                    ],
                ),
                redirect_stdout(stdout),
            ):
                rc = main(
                    [
                        "--json",
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "guided-edit-binding",
                    ]
                )

            self.assertEqual(rc, 0)
            parser = configparser.ConfigParser()
            parser.read(inventory_path, encoding="utf-8")
            self.assertEqual(parser["Binding:garage_measurement"]["Role"], "measurement")
            self.assertEqual(parser["Binding:garage_measurement"]["PhaseScope"], "L1,L2")
            self.assertEqual(parser["BindingMember:garage_measurement:1"]["Device"], "meter_l1")
            self.assertEqual(parser["BindingMember:garage_measurement:1"]["Phases"], "L1")
            self.assertEqual(parser["BindingMember:garage_measurement:2"]["Device"], "meter_l2")
            self.assertEqual(parser["BindingMember:garage_measurement:2"]["Phases"], "L2")


if __name__ == "__main__":
    unittest.main()
