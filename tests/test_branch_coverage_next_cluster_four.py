import configparser
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.backend.modbus_transport_types import ModbusTransportSettings
from venus_evcharger.energy import connectors_modbus as connectors_modbus_mod
from venus_evcharger.energy import probe as probe_mod
from venus_evcharger.energy import profiles as profiles_mod
from venus_evcharger.energy.models import EnergySourceDefinition


def _tcp_transport() -> ModbusTransportSettings:
    return ModbusTransportSettings(
        transport_kind="tcp",
        unit_id=1,
        timeout_seconds=2.0,
        host="modbus.local",
        port=502,
        device=None,
        baudrate=9600,
        bytesize=8,
        parity="N",
        stopbits=1,
        serial_port_owner="none",
        serial_port_owner_stop_command=None,
        serial_port_owner_start_command=None,
        serial_retry_count=0,
        serial_retry_delay_seconds=0.0,
    )


class BranchCoverageProbeClusterFourTests(unittest.TestCase):
    def test_validate_huawei_returns_invalid_payload_when_detection_has_no_mapping(self) -> None:
        with patch.object(probe_mod, "_huawei_detection", return_value={"detected": None, "host": "invalid"}):
            payload = probe_mod.validate_huawei_energy_source(
                "config.ini",
                profile_name="huawei_mb_native_lan",
                source_id="hybrid",
            )

        self.assertFalse(payload["validation_ok"])
        self.assertEqual(payload["field_results"], [])
        self.assertFalse(payload["meter_block_detected"])
        self.assertIn("recommendation", payload)

    def test_apply_detected_probe_target_updates_only_present_values(self) -> None:
        transport = {"Host": "old-host", "Port": "6607", "UnitId": "9"}

        probe_mod._apply_detected_probe_target(transport, {"host": "new-host", "port": 502})
        self.assertEqual(transport, {"Host": "new-host", "Port": "502", "UnitId": "9"})

        probe_mod._apply_detected_probe_target(transport, {"host": "final-host"})
        self.assertEqual(transport, {"Host": "final-host", "Port": "502", "UnitId": "9"})

    def test_probe_attempts_returns_none_when_all_candidates_fail(self) -> None:
        attempts_payload = {"ok": False, "reason": "timeout"}
        with (
            patch.object(probe_mod, "_probe_candidates", return_value=[SimpleNamespace(), SimpleNamespace()]),
            patch.object(probe_mod, "_attempt_probe", return_value=attempts_payload),
        ):
            attempts, detected = probe_mod._probe_attempts(_tcp_transport(), {}, {"address": 1})

        self.assertEqual(attempts, [attempts_payload, attempts_payload])
        self.assertIsNone(detected)

    def test_command_payload_rejects_unsupported_command(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported energy probe command"):
            probe_mod._command_payload(SimpleNamespace(command="unsupported"))


class BranchCoverageProfilesClusterFourTests(unittest.TestCase):
    def test_profile_helpers_return_empty_mappings_for_unknown_profiles(self) -> None:
        self.assertEqual(profiles_mod.energy_source_profile_details("unknown-profile"), {})
        self.assertEqual(profiles_mod.energy_source_profile_probe_plan("unknown-profile"), {})

    def test_candidate_int_helpers_cover_bool_and_invalid_strings(self) -> None:
        self.assertIsNone(profiles_mod._candidate_int_value(True))
        self.assertIsNone(profiles_mod._candidate_int_from_string("not-a-number"))
        self.assertEqual(profiles_mod._candidate_values(True, (502, 6607)), [502, 6607])


class BranchCoverageConnectorsModbusClusterFourTests(unittest.TestCase):
    def test_modbus_text_map_skips_empty_values(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string("[OperatingModeMap]\nauto = charging\nidle = \n")

        self.assertEqual(
            connectors_modbus_mod._modbus_text_map(parser, "OperatingModeMap"),
            {"auto": "charging"},
        )

    def test_render_scope_key_covers_missing_placeholders_and_format_errors(self) -> None:
        source = EnergySourceDefinition(source_id="hybrid", connector_type="modbus")
        transport = _tcp_transport()

        self.assertEqual(
            connectors_modbus_mod._render_scope_key(source, transport, "{source_id}-{missing}"),
            "hybrid-{missing}",
        )
        self.assertEqual(connectors_modbus_mod._render_scope_key(source, transport, "{"), "{")


if __name__ == "__main__":
    unittest.main()
