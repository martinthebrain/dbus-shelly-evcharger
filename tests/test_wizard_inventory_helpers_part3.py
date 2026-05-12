# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_wizard_inventory_helpers_support import *  # noqa: F401,F403

class _WizardInventoryHelperTestsPart3:
    def test_wizard_main_inventory_action_non_json_prints_summary(self) -> None:
        inventory = _inventory_with_binding()
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            inventory_path = Path(f"{config_path}.wizard-inventory.ini")
            inventory_path.write_text(inventory_text(
                WizardAnswers(
                    profile="simple_relay",
                    host_input="switch.local",
                    meter_host_input=None,
                    switch_host_input="switch.local",
                    charger_host_input=None,
                    device_instance=1,
                    phase="L1",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    charger_backend=None,
                    topology_preset=None,
                ),
                {"switch": "switch.local"},
                EvChargerTopologyConfig(
                    topology=TopologyConfig(type="simple_relay"),
                    actuator=ActuatorConfig(type="template_switch"),
                    measurement=MeasurementConfig(type="none"),
                    policy=PolicyConfig(mode="manual", phase="L1"),
                ),
            ), encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = wizard.main(
                    [
                        "--config-path",
                        str(config_path),
                        "--inventory-action",
                        "show",
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertIn("Inventory path:", stdout.getvalue())

    def test_inventory_generation_helpers_cover_labels_phases_and_endpoints(self) -> None:
        self.assertEqual(_measurement_profile_label("learned_reference"), "Learned reference meter")
        self.assertEqual(_measurement_profile_label("external_meter"), "Meter device")
        self.assertEqual(_phase_scope("L3"), ("L3",))
        self.assertEqual(_phase_scope("weird"), ("L1",))
        self.assertEqual(_switch_group_phase_scope("P1,P1_P2"), ("L1", "L2"))
        self.assertEqual(_switch_group_phase_scope("P1"), ("L1",))
        self.assertIsNone(_endpoint(None))
        self.assertIsNone(_endpoint("   "))
        self.assertEqual(_endpoint(" meter.local "), "meter.local")

    def test_inventory_and_topology_helpers_cover_remaining_fallback_paths(self) -> None:
        answers = WizardAnswers(
            profile="simple_relay",
            host_input="switch.local",
            meter_host_input=None,
            switch_host_input="switch.local",
            charger_host_input=None,
            device_instance=1,
            phase="L1",
            policy_mode="manual",
            digest_auth=False,
            username="",
            password="",
            charger_backend=None,
            topology_preset=None,
        )
        simple_topology = build_wizard_topology_config(answers)
        simple_inventory = build_wizard_inventory(answers, {"switch": "switch.local"}, simple_topology)
        self.assertEqual(len(simple_inventory.bindings), 2)

        no_measurement_topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            actuator=ActuatorConfig(type="template_switch"),
            measurement=MeasurementConfig(type="none"),
            policy=PolicyConfig(mode="manual", phase="L1"),
        )
        no_measurement_inventory = build_wizard_inventory(answers, {"switch": "switch.local"}, no_measurement_topology)
        self.assertEqual(len(no_measurement_inventory.bindings), 1)

        unsupported_profile_answers = WizardAnswers(
            profile="unsupported_profile",
            host_input="host.local",
            meter_host_input=None,
            switch_host_input=None,
            charger_host_input=None,
            device_instance=1,
            phase="L1",
            policy_mode="manual",
            digest_auth=False,
            username="",
            password="",
            charger_backend=None,
            topology_preset=None,
        )
        with self.assertRaisesRegex(ValueError, "unsupported wizard profile"):
            build_wizard_topology_config(unsupported_profile_answers)

        bad_preset_answers = WizardAnswers(
            profile="multi_adapter_topology",
            host_input="host.local",
            meter_host_input=None,
            switch_host_input="switch.local",
            charger_host_input="charger.local",
            device_instance=1,
            phase="L1",
            policy_mode="manual",
            digest_auth=False,
            username="",
            password="",
            charger_backend="goe_charger",
            topology_preset="unsupported-preset",
        )
        with self.assertRaisesRegex(ValueError, "unsupported topology preset"):
            build_wizard_topology_config(bad_preset_answers)

        shelly_template_answers = WizardAnswers(
            profile="multi_adapter_topology",
            host_input="host.local",
            meter_host_input=None,
            switch_host_input="switch.local",
            charger_host_input="charger.local",
            device_instance=1,
            phase="L1",
            policy_mode="manual",
            digest_auth=False,
            username="",
            password="",
            charger_backend="template_charger",
            topology_preset="shelly-io-template-charger",
        )
        shelly_template_topology = build_wizard_topology_config(shelly_template_answers)
        self.assertEqual(shelly_template_topology.actuator.type, "shelly_switch")

        self.assertEqual(render_adapter_files_from_topology(
            EvChargerTopologyConfig(topology=TopologyConfig(type="unknown")),
            answers,
            {},
        ), {})
        self.assertEqual(render_adapter_files_from_topology(
            EvChargerTopologyConfig(
                topology=TopologyConfig(type="hybrid_topology"),
                actuator=ActuatorConfig(type="unsupported_switch"),
                charger=ChargerConfig(type="goe_charger"),
                policy=PolicyConfig(mode="manual", phase="L1"),
            ),
            answers,
            {"charger": "charger.local", "switch": "switch.local"},
        )["wizard-charger.ini"].splitlines()[0], "[Adapter]")



