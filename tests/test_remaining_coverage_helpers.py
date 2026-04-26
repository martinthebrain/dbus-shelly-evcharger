# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from types import SimpleNamespace

from venus_evcharger.bootstrap.wizard_render import (
    _actuator_backend_lines,
    _adapter_type_from_file,
    _charger_backend_lines,
    _measurement_backend_lines,
)
from venus_evcharger.controllers.state_restore_support import _StateRuntimeRestoreVictronEssMixin
from venus_evcharger.controllers import state_runtime_snapshot as runtime_snapshot_mod
from venus_evcharger.energy.aggregate import _effective_soc
from venus_evcharger.energy.probe import _optional_detected_int
from venus_evcharger.energy import EnergySourceSnapshot
from venus_evcharger.publish.dbus_config import _DbusPublishConfigMixin
from venus_evcharger.runtime.audit_fields import _RuntimeSupportAuditFieldsMixin
from venus_evcharger.topology.schema import (
    ActuatorConfig,
    ChargerConfig,
    EvChargerTopologyConfig,
    MeasurementConfig,
    PolicyConfig,
    TopologyConfig,
)
from venus_evcharger_auto_input_helper import AutoInputHelper


class RemainingCoverageHelperTests(unittest.TestCase):
    def test_runtime_snapshot_helper_accepts_bool_as_non_negative_int(self) -> None:
        self.assertEqual(runtime_snapshot_mod._victron_ess_balance_runtime_non_negative_int(True), 1)

    def test_energy_effective_soc_returns_none_for_single_online_source_without_soc(self) -> None:
        source = EnergySourceSnapshot(
            source_id="hybrid",
            role="battery",
            service_name="com.victronenergy.battery.ttyO1",
            online=True,
            soc=None,
            ac_power_w=1000.0,
            captured_at=1.0,
        )
        self.assertIsNone(_effective_soc(None, (source,)))

    def test_optional_detected_int_returns_none_for_invalid_values(self) -> None:
        self.assertIsNone(_optional_detected_int("not-an-int"))
        self.assertIsNone(_optional_detected_int(True))

    def test_dbus_and_audit_backend_fallback_helpers_cover_unknown_attributes(self) -> None:
        service = SimpleNamespace(custom_backend="  custom  ", empty_backend=None)
        self.assertEqual(_DbusPublishConfigMixin._backend_type_value(service, "custom_backend", "fallback"), "custom")
        self.assertEqual(_DbusPublishConfigMixin._backend_type_value(service, "empty_backend", "fallback"), "fallback")
        self.assertEqual(_RuntimeSupportAuditFieldsMixin._backend_value(service, "custom_backend", "fallback"), "custom")
        self.assertEqual(_RuntimeSupportAuditFieldsMixin._backend_value(service, "empty_backend", "fallback"), "fallback")

    def test_auto_input_helper_parent_pid_parser_accepts_bool_like_int(self) -> None:
        self.assertEqual(AutoInputHelper._parsed_parent_pid(True), 1)

    def test_wizard_render_helper_errors_and_empty_config_paths(self) -> None:
        self.assertEqual(_measurement_backend_lines(None, {}), ["MeterType=none"])
        with self.assertRaisesRegex(ValueError, "unsupported legacy meter mapping"):
            _measurement_backend_lines(MeasurementConfig(type="external_meter", config_path=None), {})

        no_switch_path = EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            actuator=ActuatorConfig(type="template_switch", config_path=""),
            policy=PolicyConfig(mode="manual", phase="L1"),
        )
        self.assertEqual(_actuator_backend_lines(no_switch_path), ["SwitchType=template_switch"])

        no_charger = EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            policy=PolicyConfig(mode="manual", phase="L1"),
        )
        self.assertEqual(_charger_backend_lines(no_charger), ["ChargerType="])

        empty_charger_path = EvChargerTopologyConfig(
            topology=TopologyConfig(type="native_device"),
            charger=ChargerConfig(type="goe_charger", config_path=""),
            policy=PolicyConfig(mode="manual", phase="L1"),
        )
        self.assertEqual(_charger_backend_lines(empty_charger_path), ["ChargerType=goe_charger"])

        with self.assertRaisesRegex(ValueError, "missing adapter file"):
            _adapter_type_from_file({}, "wizard-meter.ini")
        with self.assertRaisesRegex(ValueError, "missing required \\[Adapter\\] section"):
            _adapter_type_from_file({"wizard-meter.ini": "[DEFAULT]\nType=template_meter\n"}, "wizard-meter.ini")
        with self.assertRaisesRegex(ValueError, "missing Adapter.Type"):
            _adapter_type_from_file({"wizard-meter.ini": "[Adapter]\nType=\n"}, "wizard-meter.ini")

    def test_state_restore_support_accepts_valid_activation_mode_and_service_fallback(self) -> None:
        service = SimpleNamespace(auto_battery_discharge_balance_victron_bias_activation_mode="export_only")
        self.assertEqual(
            _StateRuntimeRestoreVictronEssMixin._victron_ess_balance_activation_mode({}, service),
            "export_only",
        )
        self.assertEqual(
            _StateRuntimeRestoreVictronEssMixin._victron_ess_balance_activation_mode(
                {"activation_mode": "export_and_above_reserve_band"},
                service,
            ),
            "export_and_above_reserve_band",
        )
        self.assertIsNone(
            _StateRuntimeRestoreVictronEssMixin._victron_ess_balance_activation_mode(
                {"activation_mode": "invalid"},
                service,
            )
        )


if __name__ == "__main__":
    unittest.main()
