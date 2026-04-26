# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from venus_evcharger.backend.config import (
    _adapter_type_from_config_path,
    _build_runtime_summary,
    _native_meter_type_for_actuator,
    _runtime_summary_from_topology,
    _topology_backend_label,
    backend_mode_for_service,
    backend_type_for_service,
    compat_legacy_backend_view_from_config,
    compat_legacy_backend_view_from_runtime,
    load_runtime_backend_summary,
    runtime_summary_from_service,
    runtime_summary_is_configured,
    runtime_summary_uses_legacy_primary_rpc,
)
from venus_evcharger.backend.models import BackendRuntimeSummary
from venus_evcharger.topology.config import (
    _legacy_hybrid_measurement_config,
    _legacy_measurement_config,
    _legacy_native_measurement_config,
    _optional_text,
    _validate_measurement,
    TopologyConfigError,
    legacy_topology_from_config,
    parse_topology_config,
)
from venus_evcharger.topology.schema import (
    EvChargerTopologyConfig,
    MeasurementConfig,
    PolicyConfig,
    TopologyConfig,
)


class TopologyConfigTests(unittest.TestCase):
    def test_parse_simple_relay_with_fixed_reference(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath=/data/etc/wallbox-actuator.ini

[Measurement]
Type=fixed_reference
ReferenceWatts=2300

[Policy]
Mode=manual
Phase=L1
"""
        )

        parsed = parse_topology_config(parser)

        self.assertEqual(parsed.topology.type, "simple_relay")
        self.assertEqual(parsed.actuator.type, "template_switch")
        self.assertEqual(parsed.measurement.type, "fixed_reference")
        self.assertEqual(parsed.measurement.reference_watts, 2300.0)
        self.assertEqual(parsed.policy.mode, "manual")

    def test_parse_auto_without_measurement_fails(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath=/data/etc/wallbox-actuator.ini

[Measurement]
Type=none

[Policy]
Mode=auto
"""
        )

        with self.assertRaisesRegex(TopologyConfigError, "measurement"):
            parse_topology_config(parser)

    def test_parse_topology_validation_and_literal_errors(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Measurement]
Type=actuator_native
"""
        )
        with self.assertRaisesRegex(TopologyConfigError, "simple_relay requires an actuator"):
            parse_topology_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=native_device
"""
        )
        with self.assertRaisesRegex(TopologyConfigError, "native_device requires a charger"):
            parse_topology_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=hybrid_topology

[Charger]
Type=goe_charger
"""
        )
        with self.assertRaisesRegex(TopologyConfigError, "hybrid_topology requires both charger and actuator"):
            parse_topology_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch

[Measurement]
Type=external_meter
"""
        )
        with self.assertRaisesRegex(TopologyConfigError, "Measurement.ConfigPath"):
            parse_topology_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch

[Measurement]
Type=fixed_reference
"""
        )
        with self.assertRaisesRegex(TopologyConfigError, "Measurement.ReferenceWatts"):
            parse_topology_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch

[Policy]
Mode=invalid
"""
        )
        with self.assertRaisesRegex(TopologyConfigError, "invalid Policy.Mode"):
            parse_topology_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch

[Measurement]
Type=charger_native
"""
        )
        with self.assertRaisesRegex(TopologyConfigError, "requires a charger"):
            parse_topology_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=native_device

[Charger]
Type=goe_charger

[Measurement]
Type=actuator_native
"""
        )
        with self.assertRaisesRegex(TopologyConfigError, "requires an actuator"):
            parse_topology_config(parser)

    def test_parse_topology_requires_sections_and_keys(self) -> None:
        with self.assertRaisesRegex(TopologyConfigError, r"missing required section \[Topology\]"):
            parse_topology_config(configparser.ConfigParser())

        parser = configparser.ConfigParser()
        parser.read_string("[Topology]\n")
        with self.assertRaisesRegex(TopologyConfigError, "missing required key Topology.Type"):
            parse_topology_config(parser)

    def test_parse_topology_allows_empty_measurement_and_default_policy(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=custom_topology
"""
        )

        parsed = parse_topology_config(parser)

        self.assertIsNone(parsed.measurement)
        self.assertEqual(parsed.policy.mode, "manual")
        self.assertEqual(parsed.policy.phase, "L1")

    def test_legacy_host_only_imports_simple_relay(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[DEFAULT]
Host=192.168.1.44
Mode=0
Phase=L1
"""
        )

        parsed = legacy_topology_from_config(parser)

        self.assertEqual(parsed.topology.type, "simple_relay")
        self.assertIsNotNone(parsed.actuator)
        self.assertEqual(parsed.actuator.type, "shelly_contactor_switch")
        self.assertEqual(parsed.measurement.type, "actuator_native")

    def test_legacy_split_goe_imports_native_device(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[DEFAULT]
Mode=1
Phase=3P

[Backends]
Mode=split
MeterType=none
SwitchType=none
ChargerType=goe_charger
ChargerConfigPath=/data/etc/wizard-charger.ini
"""
        )

        parsed = legacy_topology_from_config(parser)

        self.assertEqual(parsed.topology.type, "native_device")
        self.assertIsNone(parsed.actuator)
        self.assertEqual(parsed.charger.type, "goe_charger")
        self.assertEqual(parsed.measurement.type, "charger_native")
        self.assertEqual(parsed.policy.mode, "auto")

    def test_legacy_topology_import_covers_remaining_modes(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[DEFAULT]
Mode=2
Phase=L2

[Backends]
MeterType=none
SwitchType=none
"""
        )
        with self.assertRaisesRegex(TopologyConfigError, "simple_relay requires an actuator"):
            legacy_topology_from_config(parser)

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[DEFAULT]
Mode=2
Phase=L2
Host=192.168.1.44
"""
        )
        parsed = legacy_topology_from_config(parser)
        self.assertEqual(parsed.policy.mode, "scheduled")
        self.assertEqual(parsed.actuator.type, "shelly_contactor_switch")
        self.assertEqual(parsed.measurement.type, "actuator_native")

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[DEFAULT]
Host=192.168.1.44

[Backends]
SwitchType=shelly_combined
"""
        )
        parsed = legacy_topology_from_config(parser)
        self.assertEqual(parsed.actuator.type, "shelly_contactor_switch")

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Backends]
SwitchType=template_switch
MeterType=template_meter
MeterConfigPath=/data/etc/meter.ini
ChargerType=goe_charger
ChargerConfigPath=/data/etc/charger.ini
"""
        )
        parsed = legacy_topology_from_config(parser)
        self.assertEqual(parsed.topology.type, "hybrid_topology")
        self.assertEqual(parsed.measurement.type, "external_meter")
        self.assertEqual(str(parsed.measurement.config_path), "/data/etc/meter.ini")

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Backends]
SwitchType=template_switch
MeterType=none
ChargerType=goe_charger
ChargerConfigPath=/data/etc/charger.ini
"""
        )
        parsed = legacy_topology_from_config(parser)
        self.assertEqual(parsed.measurement.type, "charger_native")

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[DEFAULT]
Mode=0

[Backends]
SwitchType=unknown_switch
"""
        )
        parsed = legacy_topology_from_config(parser)
        self.assertEqual(parsed.actuator.type, "custom")

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[DEFAULT]
Host=192.168.1.44
Mode=0

[Backends]
MeterType=none
"""
        )
        parsed = legacy_topology_from_config(parser)
        self.assertEqual(parsed.measurement.type, "none")

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Backends]
MeterType=template_meter
"""
        )
        parsed = legacy_topology_from_config(parser)
        self.assertEqual(parsed.measurement.type, "none")

        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Backends]
MeterType=none
SwitchType=template_switch
ChargerType=
"""
        )
        parsed = legacy_topology_from_config(parser)
        self.assertEqual(parsed.actuator.type, "template_switch")
        self.assertEqual(parsed.measurement.type, "none")

    def test_runtime_summary_is_configured_requires_host_for_legacy_combined(self) -> None:
        summary = BackendRuntimeSummary(
            backend_mode="combined",
            meter_type="shelly_meter",
            meter_config_path=None,
            switch_type="shelly_contactor_switch",
            switch_config_path=None,
            charger_type=None,
            charger_config_path=None,
            topology_configured=False,
            primary_rpc_configured=False,
        )

        self.assertFalse(runtime_summary_is_configured(summary, legacy_host=""))
        self.assertTrue(runtime_summary_is_configured(summary, legacy_host="192.168.1.44"))

    def test_runtime_summary_is_configured_accepts_split_topology_without_legacy_host(self) -> None:
        summary = BackendRuntimeSummary(
            backend_mode="split",
            meter_type="template_meter",
            meter_config_path=None,
            switch_type="template_switch",
            switch_config_path=Path("/data/etc/wizard-switch.ini"),
            charger_type=None,
            charger_config_path=None,
            topology_configured=True,
            primary_rpc_configured=False,
        )

        self.assertTrue(runtime_summary_is_configured(summary, legacy_host=""))

    def test_runtime_summary_from_service_prefers_config_over_legacy_backend_attrs(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=native_device

[Charger]
Type=goe_charger
ConfigPath=/data/etc/wizard-charger.ini

[Measurement]
Type=charger_native
"""
        )
        service = SimpleNamespace(
            config=parser,
            backend_mode="combined",
            meter_backend_type="shelly_combined",
            switch_backend_type="shelly_combined",
            charger_backend_type=None,
        )

        selection = runtime_summary_from_service(service)

        self.assertEqual(selection.backend_mode, "split")
        self.assertIsNone(selection.meter_type)
        self.assertIsNone(selection.switch_type)
        self.assertEqual(selection.charger_type, "goe_charger")

    def test_runtime_summary_from_service_prefers_backend_bundle_selection_over_legacy_backend_attrs(self) -> None:
        runtime = SimpleNamespace(
            backend_mode="split",
            meter_type="template_meter",
            switch_type="template_switch",
            charger_type="goe_charger",
            meter_config_path=None,
            switch_config_path=None,
            charger_config_path=None,
        )
        service = SimpleNamespace(
            _backend_bundle=SimpleNamespace(runtime=runtime),
            backend_mode="combined",
            meter_backend_type="shelly_combined",
            switch_backend_type="shelly_combined",
            charger_backend_type=None,
        )

        resolved = runtime_summary_from_service(service)

        self.assertEqual(resolved.backend_mode, "split")
        self.assertEqual(resolved.meter_type, "template_meter")
        self.assertEqual(resolved.switch_type, "template_switch")
        self.assertEqual(resolved.charger_type, "goe_charger")

    def test_runtime_summary_from_service_prefers_backend_bundle_selection_over_runtime_topology(self) -> None:
        runtime = SimpleNamespace(
            backend_mode="split",
            meter_type="template_meter",
            switch_type="template_switch",
            charger_type="goe_charger",
            meter_config_path=None,
            switch_config_path=None,
            charger_config_path=None,
        )
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath=/data/etc/wallbox-actuator.ini

[Measurement]
Type=fixed_reference
ReferenceWatts=2300
"""
        )
        topology = parse_topology_config(parser)
        service = SimpleNamespace(
            _topology_config=topology,
            _backend_bundle=SimpleNamespace(runtime=runtime),
        )

        resolved = runtime_summary_from_service(service)

        self.assertEqual(resolved.backend_mode, "split")
        self.assertEqual(resolved.meter_type, "template_meter")
        self.assertEqual(resolved.switch_type, "template_switch")
        self.assertEqual(resolved.charger_type, "goe_charger")

    def test_runtime_summary_from_service_prefers_runtime_topology_over_conflicting_legacy_attrs(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=native_device

[Charger]
Type=goe_charger
ConfigPath=/data/etc/wizard-charger.ini

[Measurement]
Type=charger_native
"""
        )
        topology = parse_topology_config(parser)
        service = SimpleNamespace(
            _topology_config=topology,
            backend_mode="combined",
            meter_backend_type="shelly_combined",
            switch_backend_type="shelly_combined",
            charger_backend_type=None,
        )

        resolved = runtime_summary_from_service(service)

        self.assertEqual(resolved.backend_mode, "split")
        self.assertIsNone(resolved.meter_type)
        self.assertIsNone(resolved.switch_type)
        self.assertEqual(resolved.charger_type, "goe_charger")

    def test_runtime_summary_from_service_supports_explicit_legacy_backend_attr_fallback(self) -> None:
        service = SimpleNamespace(
            backend_mode="split",
            meter_backend_type="template_meter",
            switch_backend_type="template_switch",
            charger_backend_type="goe_charger",
            meter_backend_config_path="/data/etc/wizard-meter.ini",
            switch_backend_config_path="/data/etc/wizard-switch.ini",
            charger_backend_config_path="/data/etc/wizard-charger.ini",
        )

        resolved = runtime_summary_from_service(service)

        self.assertEqual(resolved.backend_mode, "split")
        self.assertEqual(resolved.meter_type, "template_meter")
        self.assertEqual(resolved.switch_type, "template_switch")
        self.assertEqual(resolved.charger_type, "goe_charger")
        self.assertEqual(str(resolved.meter_config_path), "/data/etc/wizard-meter.ini")
        self.assertEqual(str(resolved.switch_config_path), "/data/etc/wizard-switch.ini")
        self.assertEqual(str(resolved.charger_config_path), "/data/etc/wizard-charger.ini")

    def test_runtime_summary_from_service_uses_default_selection_without_runtime_config_or_legacy_attrs(self) -> None:
        resolved = runtime_summary_from_service(SimpleNamespace())

        self.assertEqual(resolved.backend_mode, "combined")
        self.assertEqual(resolved.meter_type, "shelly_meter")
        self.assertEqual(resolved.switch_type, "shelly_contactor_switch")
        self.assertIsNone(resolved.charger_type)

    def test_load_runtime_backend_summary_supports_fixed_reference_topology(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath=/data/etc/wallbox-actuator.ini

[Measurement]
Type=fixed_reference
ReferenceWatts=2300
"""
        )

        summary = load_runtime_backend_summary(parser)

        self.assertEqual(summary.backend_mode, "split")
        self.assertIsNone(summary.meter_type)
        self.assertEqual(summary.switch_type, "template_switch")
        self.assertIsNone(summary.charger_type)

    def test_backend_helpers_prefer_runtime_selection_over_legacy_attrs(self) -> None:
        runtime = SimpleNamespace(
            backend_mode="split",
            meter_type="template_meter",
            switch_type="template_switch",
            charger_type="goe_charger",
            meter_config_path=None,
            switch_config_path=None,
            charger_config_path=None,
        )
        service = SimpleNamespace(
            _backend_bundle=SimpleNamespace(runtime=runtime),
            backend_mode="combined",
            meter_backend_type="shelly_combined",
            switch_backend_type="shelly_combined",
            charger_backend_type=None,
        )

        self.assertEqual(backend_mode_for_service(service, "combined"), "split")
        self.assertEqual(backend_type_for_service(service, "meter", "shelly_combined"), "template_meter")
        self.assertEqual(backend_type_for_service(service, "switch", "shelly_combined"), "template_switch")
        self.assertEqual(backend_type_for_service(service, "charger", ""), "goe_charger")

    def test_backend_helpers_use_direct_topology_for_fixed_reference_simple_relay(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath=/data/etc/wallbox-actuator.ini

[Measurement]
Type=fixed_reference
ReferenceWatts=2300
"""
        )
        service = SimpleNamespace(config=parser)

        self.assertEqual(backend_mode_for_service(service, "combined"), "split")
        self.assertEqual(backend_type_for_service(service, "meter", "shelly_combined"), "fixed_reference")
        self.assertEqual(backend_type_for_service(service, "switch", "shelly_combined"), "template_switch")
        self.assertEqual(backend_type_for_service(service, "charger", ""), "")

    def test_backend_helpers_use_direct_topology_for_external_meter_role(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = Path(temp_dir) / "meter.ini"
            meter_path.write_text(
                "[Adapter]\nType=template_meter\nBaseUrl=http://meter.local\n",
                encoding="utf-8",
            )
            parser = configparser.ConfigParser()
            parser.read_string(
                f"""
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath=/data/etc/wallbox-actuator.ini

[Measurement]
Type=external_meter
ConfigPath={meter_path}
"""
            )
            service = SimpleNamespace(config=parser)

            self.assertEqual(backend_mode_for_service(service, "combined"), "split")
            self.assertEqual(backend_type_for_service(service, "meter", "shelly_combined"), "template_meter")
            self.assertEqual(backend_type_for_service(service, "switch", "shelly_combined"), "template_switch")

    def test_backend_helpers_prefer_runtime_topology_over_conflicting_legacy_attrs(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath=/data/etc/wallbox-actuator.ini

[Measurement]
Type=fixed_reference
ReferenceWatts=2300
"""
        )
        topology = parse_topology_config(parser)
        service = SimpleNamespace(
            _topology_config=topology,
            backend_mode="combined",
            meter_backend_type="shelly_combined",
            switch_backend_type="shelly_combined",
            charger_backend_type=None,
        )

        self.assertEqual(backend_mode_for_service(service, "combined"), "split")
        self.assertEqual(backend_type_for_service(service, "meter", "shelly_combined"), "fixed_reference")
        self.assertEqual(backend_type_for_service(service, "switch", "shelly_combined"), "template_switch")
        self.assertEqual(backend_type_for_service(service, "charger", ""), "")

    def test_backend_helpers_still_prefer_backend_bundle_selection_over_runtime_topology(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath=/data/etc/wallbox-actuator.ini

[Measurement]
Type=fixed_reference
ReferenceWatts=2300
"""
        )
        topology = parse_topology_config(parser)
        runtime = SimpleNamespace(
            backend_mode="split",
            meter_type="template_meter",
            switch_type="template_switch",
            charger_type="goe_charger",
            meter_config_path=None,
            switch_config_path=None,
            charger_config_path=None,
        )
        service = SimpleNamespace(
            _topology_config=topology,
            _backend_bundle=SimpleNamespace(runtime=runtime),
        )

        self.assertEqual(backend_mode_for_service(service, "combined"), "split")
        self.assertEqual(backend_type_for_service(service, "meter", "shelly_combined"), "template_meter")
        self.assertEqual(backend_type_for_service(service, "switch", "shelly_combined"), "template_switch")
        self.assertEqual(backend_type_for_service(service, "charger", ""), "goe_charger")

    def test_backend_helpers_prefer_runtime_bundle_over_conflicting_topology(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath=/data/etc/wallbox-actuator.ini

[Measurement]
Type=fixed_reference
ReferenceWatts=2300
"""
        )
        topology = parse_topology_config(parser)
        runtime = SimpleNamespace(
            backend_mode="split",
            meter_type="template_meter",
            switch_type="template_switch",
            charger_type="goe_charger",
        )
        service = SimpleNamespace(
            _topology_config=topology,
            _backend_bundle=SimpleNamespace(runtime=runtime),
        )

        self.assertEqual(backend_mode_for_service(service, "combined"), "split")
        self.assertEqual(backend_type_for_service(service, "meter", "shelly_combined"), "template_meter")
        self.assertEqual(backend_type_for_service(service, "switch", "shelly_combined"), "template_switch")
        self.assertEqual(backend_type_for_service(service, "charger", ""), "goe_charger")

    def test_backend_config_helper_edges_cover_fallbacks_and_compat_views(self) -> None:
        self.assertEqual(_adapter_type_from_config_path(None), None)
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.ini"
            self.assertIsNone(_adapter_type_from_config_path(str(missing)))

            default_only = Path(temp_dir) / "default-only.ini"
            default_only.write_text("[DEFAULT]\nType=template_meter\n", encoding="utf-8")
            self.assertEqual(_adapter_type_from_config_path(str(default_only)), "template_meter")

        self.assertIsNone(_native_meter_type_for_actuator("template_switch"))

        native_parser = configparser.ConfigParser()
        native_parser.read_string(
            """
[Topology]
Type=native_device

[Charger]
Type=goe_charger
ConfigPath=/data/etc/charger.ini

[Measurement]
Type=charger_native
"""
        )
        native_topology = parse_topology_config(native_parser)
        self.assertEqual(_topology_backend_label(native_topology, "meter"), "goe_charger")
        self.assertIsNone(_topology_backend_label(native_topology, "switch"))
        self.assertIsNone(_topology_backend_label(native_topology, "unknown"))

        fixed_reference_parser = configparser.ConfigParser()
        fixed_reference_parser.read_string(
            """
[Topology]
Type=simple_relay

[Actuator]
Type=template_switch
ConfigPath=/data/etc/switch.ini

[Measurement]
Type=fixed_reference
ReferenceWatts=2300
"""
        )
        fixed_reference_topology = parse_topology_config(fixed_reference_parser)
        self.assertEqual(_topology_backend_label(fixed_reference_topology, "meter"), "fixed_reference")
        no_measurement_topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            measurement=None,
            policy=PolicyConfig(mode="manual", phase="L1"),
        )
        self.assertIsNone(_topology_backend_label(no_measurement_topology, "meter"))
        actuator_native_without_actuator = EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            measurement=MeasurementConfig(type="actuator_native"),
            policy=PolicyConfig(mode="manual", phase="L1"),
        )
        self.assertIsNone(_topology_backend_label(actuator_native_without_actuator, "meter"))

        runtime = _build_runtime_summary(
            backend_mode="split",
            meter_type=None,
            meter_config_path=None,
            switch_type=None,
            switch_config_path=None,
            charger_type=None,
            charger_config_path=None,
        )
        service = SimpleNamespace(_backend_bundle=SimpleNamespace(runtime=runtime))
        self.assertEqual(backend_type_for_service(service, "meter", "fallback"), "fallback")

        service_with_none_attr = SimpleNamespace(meter_backend_type=None)
        self.assertEqual(backend_type_for_service(service_with_none_attr, "meter", "fallback"), "fallback")

        legacy_parser = configparser.ConfigParser()
        legacy_parser.read_string(
            """
[Backends]
Mode=split
MeterType=none
SwitchType=none
ChargerType=goe_charger
"""
        )
        self.assertEqual(backend_type_for_service(SimpleNamespace(config=legacy_parser), "meter", "fallback"), "fallback")

        combined_summary = _build_runtime_summary(
            backend_mode="combined",
            meter_type="shelly_meter",
            meter_config_path=None,
            switch_type="shelly_contactor_switch",
            switch_config_path=None,
            charger_type=None,
            charger_config_path=None,
            primary_rpc_configured=False,
        )
        self.assertTrue(runtime_summary_uses_legacy_primary_rpc(combined_summary, legacy_host="192.168.1.20"))
        self.assertIsNone(compat_legacy_backend_view_from_runtime(None))
        self.assertEqual(compat_legacy_backend_view_from_config(legacy_parser)["mode"], "split")

    def test_topology_private_helpers_cover_remaining_measurement_and_legacy_edges(self) -> None:
        self.assertIsNone(_optional_text(None))
        self.assertEqual(_optional_text("  demo  "), "demo")

        no_measurement = EvChargerTopologyConfig(
            topology=TopologyConfig(type="custom_topology"),
            measurement=None,
            policy=PolicyConfig(mode="manual", phase="L1"),
        )
        _validate_measurement(no_measurement)
        no_measurement_runtime = _runtime_summary_from_topology(no_measurement)
        self.assertIsNone(no_measurement_runtime.meter_type)

        self.assertEqual(_legacy_measurement_config("template_meter", "/data/etc/meter.ini", "").type, "external_meter")
        self.assertEqual(_legacy_native_measurement_config("template_meter", None).type, "charger_native")
        self.assertEqual(_legacy_native_measurement_config("template_meter", "/data/etc/meter.ini").type, "external_meter")
        self.assertEqual(_legacy_hybrid_measurement_config("template_meter", None, "").type, "actuator_native")
        self.assertEqual(_legacy_hybrid_measurement_config("template_meter", "/data/etc/meter.ini", "").type, "external_meter")

        unknown_measurement_topology = EvChargerTopologyConfig(
            topology=TopologyConfig(type="custom_topology"),
            measurement=cast(MeasurementConfig, SimpleNamespace(type="mystery")),
            policy=PolicyConfig(mode="manual", phase="L1"),
        )
        runtime = _runtime_summary_from_topology(unknown_measurement_topology)
        self.assertIsNone(runtime.meter_type)
        self.assertIsNone(_topology_backend_label(unknown_measurement_topology, "meter"))


if __name__ == "__main__":
    unittest.main()
