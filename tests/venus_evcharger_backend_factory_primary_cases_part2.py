# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_backend_factory_primary_cases_support import *  # noqa: F401,F403

class _TestShellyWallboxBackendFactoryPrimaryPart2:
    def test_topology_backend_roles_cover_measurement_none_and_unsupported_paths(self) -> None:
        native_parser = configparser.ConfigParser()
        native_parser.read_string(
            """
[Topology]
Type=native_device

[Charger]
Type=goe_charger
ConfigPath=/tmp/charger.ini
"""
        )
        native_topology = parse_topology_config(native_parser)
        native_roles = _topology_backend_roles(native_topology)
        self.assertIsNotNone(native_roles)
        self.assertEqual(native_roles.charger_type, "goe_charger")
        self.assertIsNone(native_roles.switch_type)

        hybrid_parser = configparser.ConfigParser()
        hybrid_parser.read_string(
            """
[Topology]
Type=hybrid_topology

[Actuator]
Type=template_switch
ConfigPath=/tmp/switch.ini

[Charger]
Type=goe_charger
ConfigPath=/tmp/charger.ini

[Measurement]
Type=charger_native
"""
        )
        hybrid_topology = parse_topology_config(hybrid_parser)
        hybrid_roles = _topology_backend_roles(hybrid_topology)
        self.assertIsNotNone(hybrid_roles)
        self.assertEqual(hybrid_roles.switch_type, "template_switch")
        self.assertEqual(hybrid_roles.charger_type, "goe_charger")

        hybrid_no_measurement_parser = configparser.ConfigParser()
        hybrid_no_measurement_parser.read_string(
            """
[Topology]
Type=hybrid_topology

[Actuator]
Type=template_switch
ConfigPath=/tmp/switch.ini

[Charger]
Type=goe_charger
ConfigPath=/tmp/charger.ini
"""
        )
        hybrid_no_measurement_topology = parse_topology_config(hybrid_no_measurement_parser)
        hybrid_no_measurement_roles = _topology_backend_roles(hybrid_no_measurement_topology)
        self.assertIsNotNone(hybrid_no_measurement_roles)
        self.assertIsNone(hybrid_no_measurement_roles.meter_type)
        self.assertEqual(hybrid_no_measurement_roles.switch_type, "template_switch")
        self.assertEqual(hybrid_no_measurement_roles.charger_type, "goe_charger")

        unsupported_parser = configparser.ConfigParser()
        unsupported_parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath=/tmp/switch.ini

[Measurement]
Type=fixed_reference
ReferenceWatts=2300
"""
        )
        unsupported_topology = parse_topology_config(unsupported_parser)
        self.assertIsNone(_topology_backend_roles(unsupported_topology))

    def test_build_service_backends_falls_back_when_topology_roles_are_not_directly_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            switch_path = Path(temp_dir) / "switch.ini"
            switch_path.write_text(
                "[Adapter]\nType=template_switch\nBaseUrl=http://switch.local\n[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n[StateRequest]\nUrl=/switch/state\n[StateResponse]\nEnabledPath=enabled\nPhaseSelectionPath=phase_selection\n[CommandRequest]\nUrl=/switch/control\n[PhaseRequest]\nUrl=/switch/phase\n",
                encoding="utf-8",
            )
            parser = configparser.ConfigParser()
            parser.read_string(
                f"""
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath={switch_path}

[Measurement]
Type=fixed_reference
ReferenceWatts=2300
"""
            )
            service = SimpleNamespace(
                config=parser,
                phase="L1",
                host="",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=MagicMock(),
            )

            resolved = build_service_backends(service)

            self.assertEqual(resolved.runtime.backend_mode, "split")
            self.assertIsNone(resolved.meter)
            self.assertIsInstance(resolved.switch, TemplateSwitchBackend)

    def test_topology_backend_roles_returns_none_for_simple_relay_without_measurement(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath=/data/etc/actuator.ini
"""
        )

        topology = parse_topology_config(parser)

        self.assertIsNone(_topology_backend_roles(topology))

