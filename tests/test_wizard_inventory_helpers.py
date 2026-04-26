# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import configparser
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import cast
from unittest.mock import patch

from venus_evcharger.bootstrap.wizard_inventory_cli import run_inventory_editor
from venus_evcharger.bootstrap.wizard_inventory_editor import (
    _binding_capability_kind_for_role,
    add_inventory_capability,
    add_inventory_device,
    add_inventory_profile,
    inventory_role_capability_choices,
    remove_inventory_binding,
    remove_inventory_binding_member,
    remove_inventory_device,
    set_inventory_device_endpoint,
    set_inventory_binding_member,
)
from venus_evcharger.bootstrap.wizard_inventory import build_wizard_inventory, inventory_payload, inventory_text
from venus_evcharger.bootstrap.wizard_inventory import (
    _endpoint,
    _measurement_profile_label,
    _phase_scope,
    _switch_group_phase_scope,
)
from venus_evcharger.bootstrap.wizard_inventory_prompts import (
    inventory_bool_field,
    inventory_choice_field,
    inventory_field,
    inventory_field_with_default,
    inventory_optional_field,
)
from venus_evcharger.bootstrap.wizard_inventory_support import (
    inventory_action_path,
    load_inventory,
    inventory_summary_text,
    parse_inventory_binding_role,
    parse_inventory_kind,
    parse_inventory_phases,
    parse_inventory_switching_mode,
)
from venus_evcharger.bootstrap import wizard
from venus_evcharger.bootstrap.wizard_inventory_cli_actions import run_simple_inventory_action
from venus_evcharger.bootstrap.wizard_inventory_cli_support import (
    _binding_label_default,
    _binding_scope_default,
    _guided_capability_flags,
    _maybe_add_guided_device_and_binding,
    _maybe_replace_binding,
    _prompt_binding_choice,
    _validated_guided_binding,
    guided_inventory_add_profile,
    guided_inventory_edit_binding,
)
from venus_evcharger.bootstrap.wizard_topology import build_wizard_topology_config
from venus_evcharger.bootstrap.wizard_topology_render import render_adapter_files_from_topology
from venus_evcharger.bootstrap.wizard_runtime_results import json_ready
from venus_evcharger.bootstrap.wizard_models import WizardAnswers
from venus_evcharger.inventory import (
    DeviceCapability,
    DeviceInstance,
    DeviceInventory,
    DeviceProfile,
    RoleBinding,
    RoleBindingMember,
)
from venus_evcharger.topology.schema import (
    ActuatorConfig,
    ChargerConfig,
    EvChargerTopologyConfig,
    MeasurementConfig,
    PolicyConfig,
    TopologyConfig,
)


def _namespace(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "config_path": "/tmp/config.ini",
        "inventory_action": "show",
        "inventory_path": None,
        "inventory_kind": None,
        "inventory_profile_id": None,
        "inventory_label": None,
        "inventory_capability_id": None,
        "inventory_adapter_type": None,
        "inventory_supported_phases": None,
        "inventory_channel": None,
        "inventory_binding_role": None,
        "inventory_binding_id": None,
        "inventory_binding_label": None,
        "inventory_binding_phase_scope": None,
        "inventory_device_id": None,
        "inventory_member_phases": None,
        "inventory_endpoint": None,
        "inventory_switching_mode": None,
        "inventory_measures_power": False,
        "inventory_measures_energy": False,
        "inventory_supports_feedback": False,
        "inventory_supports_phase_selection": False,
        "inventory_vendor": None,
        "inventory_model": None,
        "inventory_description": None,
        "non_interactive": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _inventory_with_binding() -> DeviceInventory:
    profile = DeviceProfile(
        id="meter_profile",
        label="Meter profile",
        capabilities=(
            DeviceCapability(
                id="meter",
                kind="meter",
                adapter_type="template_meter",
                supported_phases=("L1", "L2", "L3"),
                measures_power=True,
                measures_energy=True,
            ),
        ),
    )
    return DeviceInventory(
        profiles=(profile,),
        devices=(
            DeviceInstance(id="meter_l1", profile_id="meter_profile", label="Meter L1"),
            DeviceInstance(id="meter_l2", profile_id="meter_profile", label="Meter L2"),
        ),
        bindings=(
            RoleBinding(
                id="measurement",
                role="measurement",
                label="Measurement",
                phase_scope=("L1", "L2"),
                members=(
                    RoleBindingMember(device_id="meter_l1", capability_id="meter", phases=("L1",)),
                    RoleBindingMember(device_id="meter_l2", capability_id="meter", phases=("L2",)),
                ),
            ),
        ),
    )


class WizardInventoryHelperTests(unittest.TestCase):
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

    def test_guided_cli_support_helpers_cover_charger_and_skip_paths(self) -> None:
        charger_flags = _guided_capability_flags(_namespace(non_interactive=True), "charger", ("L1", "L2", "L3"))
        self.assertEqual(
            charger_flags,
            {
                "measures_power": False,
                "measures_energy": False,
                "switching_mode": None,
                "supports_feedback": False,
                "supports_phase_selection": False,
            },
        )

        inventory_with_group_default = DeviceInventory(
            profiles=_inventory_with_binding().profiles,
            devices=_inventory_with_binding().devices,
            bindings=_inventory_with_binding().bindings
            + (
                RoleBinding(
                    id="measurement_measurement",
                    role="measurement",
                    label="Measurement group",
                    phase_scope=("L1",),
                    members=(),
                ),
            ),
        )
        binding_id, existing_binding, binding_label, binding_scope = _prompt_binding_choice(
            _namespace(non_interactive=True),
            inventory_with_group_default,
            "measurement",
        )
        self.assertEqual(binding_id, "measurement_group")
        self.assertIsNone(existing_binding)
        self.assertEqual(binding_label, "Measurement")
        self.assertEqual(binding_scope, ("L1",))

        updated, device_id, binding_ref = _maybe_add_guided_device_and_binding(
            _namespace(non_interactive=True),
            _inventory_with_binding(),
            profile_id="meter_profile",
            label="Meter profile",
            capability_id="meter",
            supported_phases=("L1",),
            inferred_role="measurement",
        )
        self.assertEqual(updated, _inventory_with_binding())
        self.assertIsNone(device_id)
        self.assertIsNone(binding_ref)

        namespace = _namespace(
            non_interactive=True,
            _inventory_prompt_device=True,
            inventory_device_id="meter_l3",
            inventory_label="Meter L3",
        )
        updated, device_id, binding_ref = _maybe_add_guided_device_and_binding(
            namespace,
            _inventory_with_binding(),
            profile_id="meter_profile",
            label="Meter profile",
            capability_id="meter",
            supported_phases=("L1",),
            inferred_role="measurement",
        )
        self.assertEqual(device_id, "meter_l3")
        self.assertIsNone(binding_ref)

        inventory = _inventory_with_binding()
        inventory = DeviceInventory(
            profiles=inventory.profiles,
            devices=(inventory.devices[0],),
        )
        namespace = _namespace(
            inventory_binding_role="measurement",
            inventory_binding_id="measurement_group",
            inventory_binding_label="Measurement group",
            inventory_binding_phase_scope="L1,L2",
        )
        with (
            patch("builtins.input", side_effect=["1", "L1"]),
            patch("venus_evcharger.bootstrap.wizard_inventory_prompts.prompt_yes_no", return_value=False),
        ):
            with self.assertRaisesRegex(ValueError, "do not match the requested binding phase scope"):
                guided_inventory_edit_binding(namespace, Path("/tmp/inventory.ini"), inventory)

    def test_set_inventory_binding_member_replaces_existing_member_and_infers_new_binding(self) -> None:
        inventory = _inventory_with_binding()
        replaced = set_inventory_binding_member(
            inventory,
            binding_id="measurement",
            device_id="meter_l1",
            capability_id="meter",
            member_phases=("L3",),
        )
        self.assertEqual(replaced.bindings[0].members[0].phases, ("L3",))
        created = set_inventory_binding_member(
            inventory,
            binding_id="new_measurement",
            device_id="meter_l1",
            capability_id="meter",
            member_phases=("L1",),
        )
        self.assertEqual(created.bindings[-1].role, "measurement")
        self.assertEqual(created.bindings[-1].label, "New Measurement")

    def test_remove_inventory_binding_member_covers_missing_and_empty_binding_paths(self) -> None:
        inventory = _inventory_with_binding()
        updated = remove_inventory_binding_member(inventory, binding_id="measurement", device_id="meter_l2")
        self.assertEqual(len(updated.bindings[0].members), 1)
        removed_all = remove_inventory_binding_member(updated, binding_id="measurement", device_id="meter_l1")
        self.assertEqual(removed_all.bindings, ())
        with self.assertRaisesRegex(ValueError, "Unknown binding id"):
            remove_inventory_binding_member(inventory, binding_id="missing", device_id="meter_l1")
        with self.assertRaisesRegex(ValueError, "has no member"):
            remove_inventory_binding_member(inventory, binding_id="measurement", device_id="missing")

    def test_run_inventory_editor_guided_actions_reject_non_interactive_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inventory_path = Path(temp_dir) / "inventory.ini"
            parser = configparser.ConfigParser()
            parser.read_string(
                """
[Profile:meter_profile]
Label=Meter profile

[Capability:meter_profile:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L1
MeasuresPower=1
MeasuresEnergy=1
"""
            )
            inventory_path.write_text(parser_to_text(parser), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "guided-add-profile requires interactive input"):
                run_inventory_editor(
                    _namespace(
                        inventory_action="guided-add-profile",
                        inventory_path=str(inventory_path),
                        non_interactive=True,
                    )
                )
            with self.assertRaisesRegex(ValueError, "guided-edit-binding requires interactive input"):
                run_inventory_editor(
                    _namespace(
                        inventory_action="guided-edit-binding",
                        inventory_path=str(inventory_path),
                        non_interactive=True,
                    )
                )

    def test_run_inventory_editor_guided_edit_binding_requires_eligible_choices(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            inventory_path = Path(temp_dir) / "inventory.ini"
            inventory_path.write_text(
                """
[Profile:meter_profile]
Label=Meter profile

[Capability:meter_profile:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L1
MeasuresPower=1
MeasuresEnergy=1
""".strip()
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "No eligible devices"):
                run_inventory_editor(
                    _namespace(
                        inventory_action="guided-edit-binding",
                        inventory_path=str(inventory_path),
                        inventory_binding_role="measurement",
                    )
                )

    def test_build_wizard_inventory_covers_switch_group_and_native_measurement(self) -> None:
        answers = WizardAnswers(
            profile="multi_adapter_topology",
            host_input="switch.local",
            meter_host_input=None,
            switch_host_input="switch.local",
            charger_host_input=None,
            device_instance=1,
            phase="3P",
            policy_mode="manual",
            digest_auth=False,
            username="",
            password="",
            topology_preset="goe-external-switch-group",
            charger_backend=None,
            switch_group_supported_phase_selections="P1,P1_P2_P3",
        )
        topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            actuator=ActuatorConfig(type="switch_group"),
            measurement=MeasurementConfig(type="actuator_native"),
            policy=PolicyConfig(mode="manual", phase="3P"),
        )
        inventory = build_wizard_inventory(answers, {"switch": "switch.local"}, topology)
        self.assertEqual(len(inventory.devices), 3)
        self.assertEqual(inventory.bindings[0].role, "actuation")
        self.assertEqual(inventory.bindings[1].role, "measurement")
        self.assertEqual(inventory.bindings[1].phase_scope, ("L1", "L2", "L3"))

    def test_build_wizard_inventory_covers_fixed_reference_and_charger_native(self) -> None:
        answers = WizardAnswers(
            profile="native_device",
            host_input="charger.local",
            meter_host_input=None,
            switch_host_input=None,
            charger_host_input="charger.local",
            device_instance=2,
            phase="L2",
            policy_mode="auto",
            digest_auth=False,
            username="",
            password="",
            charger_backend="goe_charger",
            topology_preset=None,
        )
        fixed_topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            measurement=MeasurementConfig(type="fixed_reference"),
            policy=PolicyConfig(mode="manual", phase="L2"),
        )
        fixed_inventory = build_wizard_inventory(answers, {"meter": "meter.local"}, fixed_topology)
        self.assertEqual(fixed_inventory.devices[0].endpoint, "meter.local")
        self.assertFalse(fixed_inventory.profiles[0].capabilities[0].measures_energy)

        charger_topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="native_device"),
            measurement=MeasurementConfig(type="charger_native"),
            charger=ChargerConfig(type="goe_charger"),
            policy=PolicyConfig(mode="auto", phase="L2"),
        )
        charger_inventory = build_wizard_inventory(answers, {"charger": "charger.local"}, charger_topology)
        payload = inventory_payload(charger_inventory)
        rendered = inventory_text(answers, {"charger": "charger.local"}, charger_topology)
        self.assertEqual(payload["bindings"][0]["role"], "measurement")
        self.assertIn("[Binding:charger]", rendered)

    def test_build_wizard_inventory_ignores_unknown_measurement_type(self) -> None:
        answers = WizardAnswers(
            profile="native_device",
            host_input="charger.local",
            meter_host_input=None,
            switch_host_input=None,
            charger_host_input="charger.local",
            device_instance=1,
            phase="L1",
            policy_mode="manual",
            digest_auth=False,
            username="",
            password="",
            charger_backend="goe_charger",
            topology_preset=None,
        )
        topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="native_device"),
            measurement=cast(MeasurementConfig, argparse.Namespace(type="mystery")),
            charger=ChargerConfig(type="goe_charger"),
            policy=PolicyConfig(mode="manual", phase="L1"),
        )

        inventory = build_wizard_inventory(answers, {"charger": "charger.local"}, topology)

        self.assertEqual(len(inventory.bindings), 1)
        self.assertEqual(inventory.bindings[0].role, "charger")

    def test_inventory_cli_and_runtime_result_helpers_cover_remaining_edge_branches(self) -> None:
        binding = _inventory_with_binding().bindings[0]
        self.assertEqual(_binding_label_default(None, "measurement"), "Measurement")
        self.assertEqual(_binding_label_default(binding, "measurement"), "Measurement")
        self.assertEqual(_binding_scope_default(None), "L1")
        self.assertEqual(_binding_scope_default(binding), "L1,L2")

        switch_flags = _guided_capability_flags(
            _namespace(
                non_interactive=True,
                inventory_supports_feedback=True,
                inventory_supports_phase_selection=True,
            ),
            "switch",
            ("L1", "L2"),
        )
        self.assertEqual(switch_flags["switching_mode"], "contactor")
        self.assertTrue(switch_flags["supports_feedback"])
        self.assertTrue(switch_flags["supports_phase_selection"])

        inventory = _inventory_with_binding()
        untouched, existing_binding = _maybe_replace_binding(
            _namespace(non_interactive=True, _inventory_replace_binding=False),
            inventory,
            inventory.bindings[0],
            "measurement",
        )
        self.assertEqual(untouched.bindings[0].id, "measurement")
        self.assertIsNotNone(existing_binding)

        replaced, replaced_binding = _maybe_replace_binding(
            _namespace(non_interactive=True, _inventory_replace_binding=True),
            inventory,
            inventory.bindings[0],
            "measurement",
        )
        self.assertEqual(replaced.bindings, ())
        self.assertIsNone(replaced_binding)

        with self.assertRaisesRegex(ValueError, "was not created"):
            _validated_guided_binding(DeviceInventory(), "measurement", ("L1",))
        with self.assertRaisesRegex(ValueError, "do not match"):
            _validated_guided_binding(inventory, "measurement", ("L1",))

        with self.assertRaisesRegex(ValueError, "requires interactive input"):
            guided_inventory_add_profile(_namespace(non_interactive=True), Path("/tmp/inventory.ini"), DeviceInventory())

        with self.assertRaisesRegex(ValueError, "Unsupported inventory action"):
            run_simple_inventory_action("unsupported", _namespace(), Path("/tmp/inventory.ini"), DeviceInventory())

        ready = json_ready({"path": Path("/tmp/device.ini"), "items": (Path("/tmp/child.ini"), {"nested": [Path("/tmp/x.ini")]})})
        self.assertEqual(
            ready,
            {"path": "/tmp/device.ini", "items": ["/tmp/child.ini", {"nested": ["/tmp/x.ini"]}]},
        )

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


def parser_to_text(parser: configparser.ConfigParser) -> str:
    buffer = io.StringIO()
    parser.write(buffer)
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()
