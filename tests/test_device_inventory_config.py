# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import unittest

from venus_evcharger.inventory import (
    DeviceCapability,
    DeviceInventory,
    DeviceInventoryConfigError,
    DeviceInstance,
    DeviceProfile,
    parse_device_inventory_config,
    RoleBinding,
    RoleBindingMember,
    render_device_inventory_config,
)
from venus_evcharger.inventory.config import (
    _binding_members,
    _bindings,
    _capabilities,
    _devices,
    _phase_labels,
    _render_switch_capability_fields,
    validate_device_inventory,
)


class DeviceInventoryConfigTests(unittest.TestCase):
    class _FakeConfig:
        def __init__(self, sections: list[str], mapping: dict[str, configparser.SectionProxy]) -> None:
            self._sections = sections
            self._mapping = mapping

        def sections(self) -> list[str]:
            return list(self._sections)

        def __getitem__(self, key: str) -> configparser.SectionProxy:
            return self._mapping[key]

    def test_parse_three_single_phase_meters_as_one_measurement_group(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:single_phase_meter]
Label=Single phase meter

[Capability:single_phase_meter:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L1
MeasuresPower=1
MeasuresEnergy=1

[Profile:single_phase_meter_l2]
Label=Single phase meter L2

[Capability:single_phase_meter_l2:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L2
MeasuresPower=1
MeasuresEnergy=1

[Profile:single_phase_meter_l3]
Label=Single phase meter L3

[Capability:single_phase_meter_l3:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L3
MeasuresPower=1
MeasuresEnergy=1

[Device:meter_l1]
Profile=single_phase_meter
Label=Meter L1
Endpoint=http://meter-l1.local

[Device:meter_l2]
Profile=single_phase_meter_l2
Label=Meter L2
Endpoint=http://meter-l2.local

[Device:meter_l3]
Profile=single_phase_meter_l3
Label=Meter L3
Endpoint=http://meter-l3.local

[Binding:evse_measurement]
Role=measurement
Label=EVSE measurement group
PhaseScope=L1,L2,L3

[BindingMember:evse_measurement:1]
Device=meter_l1
Capability=meter
Phases=L1

[BindingMember:evse_measurement:2]
Device=meter_l2
Capability=meter
Phases=L2

[BindingMember:evse_measurement:3]
Device=meter_l3
Capability=meter
Phases=L3
"""
        )

        inventory = parse_device_inventory_config(parser)

        self.assertEqual(len(inventory.profiles), 3)
        self.assertEqual(len(inventory.devices), 3)
        self.assertEqual(len(inventory.bindings), 1)
        binding = inventory.bindings[0]
        self.assertEqual(binding.role, "measurement")
        self.assertEqual(binding.phase_scope, ("L1", "L2", "L3"))
        self.assertEqual(len(binding.members), 3)

    def test_parse_three_phase_device_and_bind_only_one_phase(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:three_phase_meter]
Label=Three phase meter

[Capability:three_phase_meter:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L1,L2,L3
MeasuresPower=1
MeasuresEnergy=1

[Device:grid_meter]
Profile=three_phase_meter
Label=Grid meter
Endpoint=http://grid.local

[Binding:l2_measurement]
Role=measurement
Label=Only L2
PhaseScope=L2

[BindingMember:l2_measurement:1]
Device=grid_meter
Capability=meter
Phases=L2
"""
        )

        inventory = parse_device_inventory_config(parser)

        binding = inventory.bindings[0]
        self.assertEqual(binding.phase_scope, ("L2",))
        self.assertEqual(binding.members[0].phases, ("L2",))

    def test_switch_group_can_bind_three_single_phase_relays(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:relay_l1]
Label=Relay L1

[Capability:relay_l1:switch]
Kind=switch
AdapterType=template_switch
SupportedPhases=L1
SwitchingMode=contactor
SupportsFeedback=1

[Profile:relay_l2]
Label=Relay L2

[Capability:relay_l2:switch]
Kind=switch
AdapterType=template_switch
SupportedPhases=L2
SwitchingMode=contactor
SupportsFeedback=1

[Profile:relay_l3]
Label=Relay L3

[Capability:relay_l3:switch]
Kind=switch
AdapterType=template_switch
SupportedPhases=L3
SwitchingMode=contactor
SupportsFeedback=1

[Device:relay1]
Profile=relay_l1
Label=Relay 1

[Device:relay2]
Profile=relay_l2
Label=Relay 2

[Device:relay3]
Profile=relay_l3
Label=Relay 3

[Binding:evse_contactors]
Role=actuation
Label=EVSE contactors
PhaseScope=L1,L2,L3

[BindingMember:evse_contactors:1]
Device=relay1
Capability=switch
Phases=L1

[BindingMember:evse_contactors:2]
Device=relay2
Capability=switch
Phases=L2

[BindingMember:evse_contactors:3]
Device=relay3
Capability=switch
Phases=L3
"""
        )

        inventory = parse_device_inventory_config(parser)

        self.assertEqual(inventory.bindings[0].role, "actuation")
        self.assertEqual(len(inventory.bindings[0].members), 3)

    def test_render_switch_capability_fields_returns_empty_without_switching_mode(self) -> None:
        capability = DeviceCapability(
            id="switch",
            kind="switch",
            adapter_type="template_switch",
            supported_phases=("L1",),
            switching_mode=None,
            supports_feedback=True,
            supports_phase_selection=True,
        )

        self.assertEqual(_render_switch_capability_fields(capability), [])

    def test_duplicate_phase_assignment_in_binding_fails(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:meter]
Label=Meter

[Capability:meter:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L1,L2
MeasuresPower=1
MeasuresEnergy=1

[Device:m1]
Profile=meter
Label=Meter 1

[Binding:bad_measurement]
Role=measurement
Label=Bad
PhaseScope=L1,L2

[BindingMember:bad_measurement:1]
Device=m1
Capability=meter
Phases=L1

[BindingMember:bad_measurement:2]
Device=m1
Capability=meter
Phases=L1,L2
"""
        )

        with self.assertRaisesRegex(DeviceInventoryConfigError, "duplicate phases"):
            parse_device_inventory_config(parser)

    def test_binding_rejects_capability_kind_mismatch(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:combo]
Label=Combo

[Capability:combo:switch]
Kind=switch
AdapterType=template_switch
SupportedPhases=L1
SwitchingMode=direct

[Device:d1]
Profile=combo
Label=Device 1

[Binding:bad_measurement]
Role=measurement
Label=Bad
PhaseScope=L1

[BindingMember:bad_measurement:1]
Device=d1
Capability=switch
Phases=L1
"""
        )

        with self.assertRaisesRegex(DeviceInventoryConfigError, "requires capability kind 'meter'"):
            parse_device_inventory_config(parser)

    def test_render_round_trip_preserves_inventory_shape(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:three_phase_device]
Label=Three phase device
Vendor=Example
Model=X1

[Capability:three_phase_device:switch]
Kind=switch
AdapterType=tasmota_switch
SupportedPhases=L1,L2,L3
SwitchingMode=contactor
SupportsFeedback=1
SupportsPhaseSelection=1

[Capability:three_phase_device:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L1,L2,L3
MeasuresPower=1
MeasuresEnergy=1

[Device:garage_device]
Profile=three_phase_device
Label=Garage device
Endpoint=http://garage-device.local

[Binding:garage_switching]
Role=actuation
Label=Garage switching
PhaseScope=L1,L2,L3

[BindingMember:garage_switching:1]
Device=garage_device
Capability=switch
Phases=L1,L2,L3
"""
        )

        inventory = parse_device_inventory_config(parser)
        rendered = render_device_inventory_config(inventory)
        round_trip = configparser.ConfigParser()
        round_trip.read_string(rendered)
        reparsed = parse_device_inventory_config(round_trip)

        self.assertEqual(inventory, reparsed)
        self.assertIsInstance(reparsed, DeviceInventory)

    def test_validation_rejects_duplicate_and_unknown_references(self) -> None:
        duplicated_profiles = DeviceInventory(
            profiles=(
                DeviceProfile(id="p1", label="P1", capabilities=(
                    DeviceCapability(
                        id="meter",
                        kind="meter",
                        adapter_type="template_meter",
                        supported_phases=("L1",),
                        measures_power=True,
                    ),
                )),
                DeviceProfile(id="p1", label="P1 duplicate", capabilities=(
                    DeviceCapability(
                        id="meter",
                        kind="meter",
                        adapter_type="template_meter",
                        supported_phases=("L1",),
                        measures_power=True,
                    ),
                )),
            ),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "duplicate profile id"):
            render_device_inventory_config(duplicated_profiles)

        missing_profile = DeviceInventory(
            devices=(DeviceInstance(id="d1", profile_id="missing", label="Device"),),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "unknown profile"):
            render_device_inventory_config(missing_profile)

        empty_binding = DeviceInventory(
            profiles=(
                DeviceProfile(
                    id="p1",
                    label="P1",
                    capabilities=(
                        DeviceCapability(
                            id="meter",
                            kind="meter",
                            adapter_type="template_meter",
                            supported_phases=("L1",),
                            measures_power=True,
                        ),
                    ),
                ),
            ),
            devices=(DeviceInstance(id="d1", profile_id="p1", label="Device"),),
            bindings=(RoleBinding(id="b1", role="measurement", label="B1", phase_scope=("L1",), members=()),),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "requires at least one member"):
            render_device_inventory_config(empty_binding)

        duplicate_devices = DeviceInventory(
            profiles=(
                DeviceProfile(
                    id="p1",
                    label="P1",
                    capabilities=(
                        DeviceCapability(
                            id="meter",
                            kind="meter",
                            adapter_type="template_meter",
                            supported_phases=("L1",),
                            measures_power=True,
                        ),
                    ),
                ),
            ),
            devices=(
                DeviceInstance(id="d1", profile_id="p1", label="Device 1"),
                DeviceInstance(id="d1", profile_id="p1", label="Device 2"),
            ),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "duplicate device id"):
            render_device_inventory_config(duplicate_devices)

        duplicate_bindings = DeviceInventory(
            profiles=duplicate_devices.profiles,
            devices=(DeviceInstance(id="d1", profile_id="p1", label="Device 1"),),
            bindings=(
                RoleBinding(
                    id="b1",
                    role="measurement",
                    label="B1",
                    phase_scope=("L1",),
                    members=(RoleBindingMember(device_id="d1", capability_id="meter", phases=("L1",)),),
                ),
                RoleBinding(
                    id="b1",
                    role="measurement",
                    label="B1 duplicate",
                    phase_scope=("L1",),
                    members=(RoleBindingMember(device_id="d1", capability_id="meter", phases=("L1",)),),
                ),
            ),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "duplicate binding id"):
            render_device_inventory_config(duplicate_bindings)

    def test_validation_rejects_profile_and_binding_capability_errors(self) -> None:
        with self.assertRaisesRegex(DeviceInventoryConfigError, "requires at least one capability"):
            render_device_inventory_config(DeviceInventory(profiles=(DeviceProfile(id="p1", label="P1"),)))

        with self.assertRaisesRegex(DeviceInventoryConfigError, "must measure power or energy"):
            render_device_inventory_config(
                DeviceInventory(
                    profiles=(
                        DeviceProfile(
                            id="p1",
                            label="P1",
                            capabilities=(
                                DeviceCapability(
                                    id="meter",
                                    kind="meter",
                                    adapter_type="template_meter",
                                    supported_phases=("L1",),
                                ),
                            ),
                        ),
                    ),
                )
            )

        with self.assertRaisesRegex(DeviceInventoryConfigError, "may not declare measurement flags"):
            render_device_inventory_config(
                DeviceInventory(
                    profiles=(
                        DeviceProfile(
                            id="p1",
                            label="P1",
                            capabilities=(
                                DeviceCapability(
                                    id="switch",
                                    kind="switch",
                                    adapter_type="template_switch",
                                    supported_phases=("L1",),
                                    measures_power=True,
                                    switching_mode="contactor",
                                ),
                            ),
                        ),
                    ),
                )
            )

        with self.assertRaisesRegex(DeviceInventoryConfigError, "requires SwitchingMode"):
            render_device_inventory_config(
                DeviceInventory(
                    profiles=(
                        DeviceProfile(
                            id="p1",
                            label="P1",
                            capabilities=(
                                DeviceCapability(
                                    id="switch",
                                    kind="switch",
                                    adapter_type="template_switch",
                                    supported_phases=("L1",),
                                ),
                            ),
                        ),
                    ),
                )
            )

        with self.assertRaisesRegex(DeviceInventoryConfigError, "may not declare SwitchingMode"):
            render_device_inventory_config(
                DeviceInventory(
                    profiles=(
                        DeviceProfile(
                            id="p1",
                            label="P1",
                            capabilities=(
                                DeviceCapability(
                                    id="charger",
                                    kind="charger",
                                    adapter_type="template_charger",
                                    supported_phases=("L1",),
                                    switching_mode="direct",
                                ),
                            ),
                        ),
                    ),
                )
            )

        invalid_binding_inventory = DeviceInventory(
            profiles=(
                DeviceProfile(
                    id="p1",
                    label="P1",
                    capabilities=(
                        DeviceCapability(
                            id="meter",
                            kind="meter",
                            adapter_type="template_meter",
                            supported_phases=("L1",),
                            measures_power=True,
                        ),
                    ),
                ),
            ),
            devices=(DeviceInstance(id="d1", profile_id="p1", label="Device 1"),),
            bindings=(
                RoleBinding(
                    id="b1",
                    role="measurement",
                    label="B1",
                    phase_scope=("L1", "L2"),
                    members=(RoleBindingMember(device_id="d1", capability_id="missing", phases=("L1",)),),
                ),
            ),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "unknown capability"):
            render_device_inventory_config(invalid_binding_inventory)

        invalid_binding_inventory = DeviceInventory(
            profiles=invalid_binding_inventory.profiles,
            devices=(DeviceInstance(id="d1", profile_id="p1", label="Device 1"),),
            bindings=(
                RoleBinding(
                    id="b1",
                    role="measurement",
                    label="B1",
                    phase_scope=("L1",),
                    members=(RoleBindingMember(device_id="d1", capability_id="meter", phases=("L2",)),),
                ),
            ),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "outside capability support"):
            render_device_inventory_config(invalid_binding_inventory)

        invalid_binding_inventory = DeviceInventory(
            profiles=(
                DeviceProfile(
                    id="p1",
                    label="P1",
                    capabilities=(
                        DeviceCapability(
                            id="meter",
                            kind="meter",
                            adapter_type="template_meter",
                            supported_phases=("L1", "L2"),
                            measures_power=True,
                        ),
                    ),
                ),
            ),
            devices=(DeviceInstance(id="d1", profile_id="p1", label="Device 1"),),
            bindings=(
                RoleBinding(
                    id="b1",
                    role="measurement",
                    label="B1",
                    phase_scope=("L1",),
                    members=(RoleBindingMember(device_id="d1", capability_id="meter", phases=("L2",)),),
                ),
            ),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "outside binding scope"):
            render_device_inventory_config(invalid_binding_inventory)

        invalid_binding_inventory = DeviceInventory(
            profiles=(
                DeviceProfile(
                    id="p1",
                    label="P1",
                    capabilities=(
                        DeviceCapability(
                            id="meter",
                            kind="meter",
                            adapter_type="template_meter",
                            supported_phases=("L1", "L2"),
                            measures_power=True,
                        ),
                    ),
                ),
            ),
            devices=(DeviceInstance(id="d1", profile_id="p1", label="Device 1"),),
            bindings=(
                RoleBinding(
                    id="b1",
                    role="measurement",
                    label="B1",
                    phase_scope=("L1", "L2"),
                    members=(RoleBindingMember(device_id="d1", capability_id="meter", phases=("L1",)),),
                ),
            ),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "covers phases"):
            render_device_inventory_config(invalid_binding_inventory)

    def test_parse_inventory_config_rejects_invalid_sections_and_literals(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:]
Label=Broken
"""
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "invalid section name"):
            parse_device_inventory_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:p1]
Label=P1

[Capability:p1:meter]
Kind=invalid
AdapterType=template_meter
SupportedPhases=L1
MeasuresPower=1
"""
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "Capability.Kind must be one of"):
            parse_device_inventory_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:p1]
Label=P1

[Capability:p1:switch]
Kind=switch
AdapterType=template_switch
SupportedPhases=L1
SwitchingMode=bad
"""
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "Capability.SwitchingMode must be one of"):
            parse_device_inventory_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:p1]
Label=P1

[Capability:p1:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L4
MeasuresPower=1
"""
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "phase list"):
            parse_device_inventory_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Capability:broken]
Kind=meter
AdapterType=template_meter
SupportedPhases=L1
MeasuresPower=1
"""
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "invalid section name"):
            parse_device_inventory_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Profile:p1]
Vendor=Only vendor
"""
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "missing required key Profile:p1.Label"):
            parse_device_inventory_config(parser)

    def test_render_round_trip_preserves_optional_fields(self) -> None:
        inventory = DeviceInventory(
            profiles=(
                DeviceProfile(
                    id="p1",
                    label="P1",
                    vendor="Vendor",
                    model="Model",
                    description="Description",
                    capabilities=(
                        DeviceCapability(
                            id="switch",
                            kind="switch",
                            adapter_type="template_switch",
                            supported_phases=("L1",),
                            channel="relay0",
                            switching_mode="direct",
                            supports_feedback=True,
                            supports_phase_selection=True,
                        ),
                    ),
                ),
            ),
            devices=(
                DeviceInstance(
                    id="d1",
                    profile_id="p1",
                    label="Device",
                    endpoint="http://device.local",
                    notes="Notes",
                ),
            ),
        )

        rendered = render_device_inventory_config(inventory)

        self.assertIn("Vendor=Vendor", rendered)
        self.assertIn("Description=Description", rendered)
        self.assertIn("Channel=relay0", rendered)
        self.assertIn("Notes=Notes", rendered)

    def test_inventory_private_helpers_cover_duplicate_parsing_and_render_edges(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Capability:p1:meter]
Kind=meter
AdapterType=template_meter
SupportedPhases=L1
MeasuresPower=1

[Device:d1]
Profile=p1
Label=Device 1

[Binding:b1]
Role=measurement
Label=Binding
PhaseScope=L1

[BindingMember:b1:m1]
Device=d1
Capability=meter
Phases=L1
"""
        )
        capability_section = parser["Capability:p1:meter"]
        device_section = parser["Device:d1"]
        binding_section = parser["Binding:b1"]
        member_section = parser["BindingMember:b1:m1"]

        duplicate_capability = self._FakeConfig(
            ["Capability:p1:meter", "Capability:p1:meter"],
            {"Capability:p1:meter": capability_section},
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "duplicate capability id 'meter'"):
            _capabilities(duplicate_capability)  # type: ignore[arg-type]

        duplicate_device = self._FakeConfig(
            ["Device:d1", "Device:d1"],
            {"Device:d1": device_section},
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "duplicate device id 'd1'"):
            _devices(duplicate_device)  # type: ignore[arg-type]

        duplicate_binding = self._FakeConfig(
            ["Binding:b1", "Binding:b1"],
            {"Binding:b1": binding_section},
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "duplicate binding id 'b1'"):
            _bindings(duplicate_binding)  # type: ignore[arg-type]

        duplicate_member = self._FakeConfig(
            ["BindingMember:b1:m1", "BindingMember:b1:m1"],
            {"BindingMember:b1:m1": member_section},
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "duplicate binding member id 'm1'"):
            _binding_members(duplicate_member)  # type: ignore[arg-type]

        duplicate_capability_inventory = DeviceInventory(
            profiles=(
                DeviceProfile(
                    id="p1",
                    label="P1",
                    capabilities=(
                        DeviceCapability(
                            id="meter",
                            kind="meter",
                            adapter_type="template_meter",
                            supported_phases=("L1",),
                            measures_power=True,
                        ),
                        DeviceCapability(
                            id="meter",
                            kind="meter",
                            adapter_type="template_meter",
                            supported_phases=("L2",),
                            measures_power=True,
                        ),
                    ),
                ),
            ),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "duplicate capability id 'meter'"):
            validate_device_inventory(duplicate_capability_inventory)

        unknown_device_inventory = DeviceInventory(
            profiles=(
                DeviceProfile(
                    id="p1",
                    label="P1",
                    capabilities=(
                        DeviceCapability(
                            id="meter",
                            kind="meter",
                            adapter_type="template_meter",
                            supported_phases=("L1",),
                            measures_power=True,
                        ),
                    ),
                ),
            ),
            bindings=(
                RoleBinding(
                    id="measurement",
                    role="measurement",
                    label="Measurement",
                    phase_scope=("L1",),
                    members=(
                        RoleBindingMember(device_id="missing", capability_id="meter", phases=("L1",)),
                    ),
                ),
            ),
        )
        with self.assertRaisesRegex(DeviceInventoryConfigError, "references unknown device 'missing'"):
            validate_device_inventory(unknown_device_inventory)

        with self.assertRaisesRegex(DeviceInventoryConfigError, "phase list may not be empty"):
            _phase_labels(" , ")
        self.assertEqual(_phase_labels("L1,,L2"), ("L1", "L2"))
        self.assertEqual(_phase_labels("L1,L1,L2"), ("L1", "L2"))

        rendered = render_device_inventory_config(
            DeviceInventory(
                profiles=(
                    DeviceProfile(
                        id="p1",
                        label="P1",
                        capabilities=(
                            DeviceCapability(
                                id="meter",
                                kind="meter",
                                adapter_type="template_meter",
                                supported_phases=("L1",),
                                measures_power=True,
                            ),
                        ),
                    ),
                ),
                devices=(
                    DeviceInstance(
                        id="d1",
                        profile_id="p1",
                        label="Device 1",
                        endpoint=None,
                        notes="Only notes",
                    ),
                ),
            )
        )
        self.assertIn("Notes=Only notes", rendered)


if __name__ == "__main__":
    unittest.main()
