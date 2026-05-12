# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_wizard_inventory_helpers_support import *  # noqa: F401,F403

class _WizardInventoryHelperTestsPart1:
    def test_inventory_field_requires_value_in_non_interactive_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "--inventory-profile-id is required"):
            inventory_field(_namespace(non_interactive=True), "inventory_profile_id", "Profile id")

    def test_inventory_optional_field_strips_and_clears_values(self) -> None:
        self.assertEqual(
            inventory_optional_field(_namespace(inventory_endpoint="  http://device.local  "), "inventory_endpoint", "Device endpoint"),
            "http://device.local",
        )
        self.assertIsNone(inventory_optional_field(_namespace(inventory_endpoint="   "), "inventory_endpoint", "Device endpoint"))

    def test_inventory_choice_field_retries_after_invalid_input(self) -> None:
        stdout = io.StringIO()
        with (
            patch("builtins.input", side_effect=["9", "2"]),
            redirect_stdout(stdout),
        ):
            choice = inventory_choice_field(
                _namespace(),
                "inventory_kind",
                "Choose kind:",
                ("switch", "meter", "charger"),
                "switch",
            )
        self.assertEqual(choice, "meter")
        self.assertIn("Invalid selection, please try again.", stdout.getvalue())

    def test_inventory_prompt_helpers_cover_default_and_validation_paths(self) -> None:
        self.assertEqual(
            inventory_field_with_default(_namespace(non_interactive=True), "inventory_label", "Label", "Default label"),
            "Default label",
        )
        self.assertTrue(inventory_bool_field(_namespace(inventory_measures_power=True), "inventory_measures_power", "Measures power"))
        self.assertFalse(inventory_bool_field(_namespace(non_interactive=True), "inventory_measures_power", "Measures power"))
        self.assertEqual(
            inventory_choice_field(
                _namespace(non_interactive=True),
                "inventory_kind",
                "Choose kind:",
                ("switch", "meter"),
                "switch",
            ),
            "switch",
        )
        self.assertEqual(
            inventory_choice_field(
                _namespace(inventory_kind="meter"),
                "inventory_kind",
                "Choose kind:",
                ("switch", "meter"),
                "switch",
            ),
            "meter",
        )
        with self.assertRaisesRegex(ValueError, "must be one of"):
            inventory_choice_field(
                _namespace(inventory_kind="bad"),
                "inventory_kind",
                "Choose kind:",
                ("switch", "meter"),
                "switch",
            )
        with patch("builtins.input", return_value="meter"):
            self.assertEqual(
                inventory_choice_field(
                    _namespace(),
                    "inventory_kind",
                    "Choose kind:",
                    ("switch", "meter"),
                    "switch",
                ),
                "meter",
            )
        with patch("builtins.input", return_value="   "):
            with self.assertRaisesRegex(ValueError, "Profile id must not be empty"):
                inventory_field(_namespace(), "inventory_profile_id", "Profile id")

    def test_parse_inventory_helpers_cover_error_paths(self) -> None:
        self.assertEqual(parse_inventory_phases(" L1 , L1 , l2 "), ("L1", "L2"))
        with self.assertRaisesRegex(ValueError, "Phase list must not be empty"):
            parse_inventory_phases(" , ")
        with self.assertRaisesRegex(ValueError, "Unknown phase label"):
            parse_inventory_phases("L4")
        with self.assertRaisesRegex(ValueError, "Capability kind"):
            parse_inventory_kind("bad")
        with self.assertRaisesRegex(ValueError, "Binding role"):
            parse_inventory_binding_role("bad")
        self.assertIsNone(parse_inventory_switching_mode(None))
        self.assertIsNone(parse_inventory_switching_mode("  "))
        self.assertEqual(parse_inventory_switching_mode("DIRECT"), "direct")
        with self.assertRaisesRegex(ValueError, "Switching mode"):
            parse_inventory_switching_mode("bad")

    def test_inventory_summary_text_handles_empty_inventory(self) -> None:
        summary = inventory_summary_text(Path("/tmp/inventory.ini"), DeviceInventory())
        self.assertIn("Profiles: 0", summary)
        self.assertIn("  - none", summary)

    def test_inventory_summary_text_renders_non_empty_inventory(self) -> None:
        summary = inventory_summary_text(Path("/tmp/inventory.ini"), _inventory_with_binding())
        self.assertIn("meter_profile: meter/meter@template_meter[L1,L2,L3]", summary)
        self.assertIn("meter_l1: profile=meter_profile, label=Meter L1, endpoint=n/a", summary)
        self.assertIn("measurement: role=measurement, phases=L1,L2", summary)

    def test_render_adapter_files_from_topology_covers_remaining_actuator_edges(self) -> None:
        hybrid_without_actuator = EvChargerTopologyConfig(
            topology=TopologyConfig(type="hybrid_topology"),
            actuator=None,
            charger=ChargerConfig(type="goe_charger", config_path="/data/etc/charger.ini"),
            measurement=MeasurementConfig(type="charger_native"),
            policy=PolicyConfig(mode="auto", phase="L1"),
        )
        answers = WizardAnswers(
            profile="hybrid_topology",
            host_input="http://charger.local",
            meter_host_input=None,
            switch_host_input="http://switch.local",
            charger_host_input="http://charger.local",
            device_instance=1,
            phase="L1",
            policy_mode="auto",
            digest_auth=False,
            username="",
            password="",
            charger_backend="goe_charger",
            topology_preset=None,
        )
        no_actuator_files = render_adapter_files_from_topology(hybrid_without_actuator, answers, {})
        self.assertEqual(set(no_actuator_files), {"wizard-charger.ini"})

        custom_actuator_topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="hybrid_topology"),
            actuator=ActuatorConfig(type="custom_switch", config_path="/data/etc/switch.ini"),
            charger=ChargerConfig(type="goe_charger", config_path="/data/etc/charger.ini"),
            measurement=MeasurementConfig(type="charger_native"),
            policy=PolicyConfig(mode="auto", phase="L1"),
        )
        custom_files = render_adapter_files_from_topology(
            custom_actuator_topology,
            answers,
            {"charger": "http://charger.local", "switch": "http://switch.local"},
        )
        self.assertIn("wizard-charger.ini", custom_files)
        self.assertNotIn("wizard-switch.ini", custom_files)

    def test_inventory_support_helpers_cover_paths(self) -> None:
        self.assertEqual(
            inventory_action_path(_namespace(config_path="/tmp/config.ini")),
            Path("/tmp/config.ini.wizard-inventory.ini"),
        )
        self.assertEqual(
            inventory_action_path(_namespace(config_path="/tmp/config.ini", inventory_path="/tmp/custom.ini")),
            Path("/tmp/custom.ini"),
        )
        with self.assertRaisesRegex(ValueError, "Inventory does not exist"):
            load_inventory(Path("/tmp/does-not-exist.ini"))

    def test_remove_inventory_device_drops_orphaned_binding_and_keeps_scope(self) -> None:
        inventory = _inventory_with_binding()
        updated = remove_inventory_device(inventory, device_id="meter_l2")
        self.assertEqual(len(updated.devices), 1)
        self.assertEqual(updated.bindings[0].phase_scope, ("L1",))
        self.assertEqual(updated.bindings[0].members[0].device_id, "meter_l1")
        with self.assertRaisesRegex(ValueError, "Unknown device id"):
            remove_inventory_device(inventory, device_id="missing")

    def test_inventory_editor_helpers_cover_duplicate_and_unknown_paths(self) -> None:
        inventory = _inventory_with_binding()
        with self.assertRaisesRegex(ValueError, "Unknown binding id"):
            remove_inventory_binding(inventory, binding_id="missing")
        removed = remove_inventory_binding(inventory, binding_id="measurement")
        self.assertEqual(removed.bindings, ())

        choices = inventory_role_capability_choices(inventory, role="measurement")
        self.assertEqual(len(choices), 2)
        self.assertEqual(inventory_role_capability_choices(inventory, role="charger"), ())
        self.assertEqual(_binding_capability_kind_for_role("actuation"), "switch")
        self.assertEqual(_binding_capability_kind_for_role("charger"), "charger")
        self.assertEqual(_binding_capability_kind_for_role("measurement"), "meter")

        with self.assertRaisesRegex(ValueError, "Unknown profile id"):
            add_inventory_device(inventory, profile_id="missing", device_id="new", label="New", endpoint=None)
        with self.assertRaisesRegex(ValueError, "Device id already exists"):
            add_inventory_device(inventory, profile_id="meter_profile", device_id="meter_l1", label="Dup", endpoint=None)

        with self.assertRaisesRegex(ValueError, "Unknown device id"):
            set_inventory_device_endpoint(inventory, device_id="missing", endpoint="http://x")

        with self.assertRaisesRegex(ValueError, "Profile id already exists"):
            add_inventory_profile(
                inventory,
                profile_id="meter_profile",
                label="Dup",
                capability_id="meter2",
                kind="meter",
                adapter_type="template_meter",
                supported_phases=("L1",),
                measures_power=True,
            )

        with self.assertRaisesRegex(ValueError, "Unknown profile id"):
            add_inventory_capability(
                inventory,
                profile_id="missing",
                capability_id="switch",
                kind="switch",
                adapter_type="template_switch",
                supported_phases=("L1",),
                switching_mode="direct",
            )
        with self.assertRaisesRegex(ValueError, "Capability id already exists"):
            add_inventory_capability(
                inventory,
                profile_id="meter_profile",
                capability_id="meter",
                kind="meter",
                adapter_type="template_meter",
                supported_phases=("L1",),
                measures_power=True,
            )

        with self.assertRaisesRegex(ValueError, "Unknown device id"):
            set_inventory_binding_member(
                inventory,
                binding_id="measurement",
                device_id="missing",
                capability_id="meter",
                member_phases=("L1",),
            )

        inventory_with_missing_profile = DeviceInventory(
            profiles=inventory.profiles,
            devices=inventory.devices + (DeviceInstance(id="orphan", profile_id="missing", label="Orphan"),),
            bindings=inventory.bindings,
        )
        self.assertEqual(len(inventory_role_capability_choices(inventory_with_missing_profile, role="measurement")), 2)

    def test_inventory_editor_helpers_cover_additional_binding_role_branches(self) -> None:
        charger_profile = DeviceProfile(
            id="charger_profile",
            label="Charger profile",
            capabilities=(
                DeviceCapability(
                    id="charger",
                    kind="charger",
                    adapter_type="template_charger",
                    supported_phases=("L1", "L2", "L3"),
                ),
                DeviceCapability(
                    id="switch",
                    kind="switch",
                    adapter_type="template_switch",
                    supported_phases=("L1",),
                    switching_mode="direct",
                ),
            ),
        )
        inventory = DeviceInventory(
            profiles=(charger_profile,),
            devices=(DeviceInstance(id="charger_device", profile_id="charger_profile", label="Charger"),),
        )
        created_switch = set_inventory_binding_member(
            inventory,
            binding_id="switch_binding",
            device_id="charger_device",
            capability_id="switch",
            member_phases=("L1",),
        )
        self.assertEqual(created_switch.bindings[0].role, "actuation")

        created_charger = set_inventory_binding_member(
            inventory,
            binding_id="charger_binding",
            device_id="charger_device",
            capability_id="charger",
            member_phases=("L1", "L2", "L3"),
        )
        self.assertEqual(created_charger.bindings[0].role, "charger")

    def test_guided_edit_binding_rejects_invalid_member_selection_and_scope_mismatch(self) -> None:
        inventory = _inventory_with_binding()
        namespace = _namespace(
            inventory_binding_role="measurement",
            inventory_binding_id="measurement_group",
            inventory_binding_label="Measurement group",
            inventory_binding_phase_scope="L1",
        )
        with patch("builtins.input", side_effect=["9"]):
            with self.assertRaisesRegex(ValueError, "out of range"):
                guided_inventory_edit_binding(namespace, Path("/tmp/inventory.ini"), inventory)


