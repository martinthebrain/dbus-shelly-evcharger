# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from venus_evcharger.backend.modbus_transport import ModbusRequest
from venus_evcharger.energy.probe import detect_modbus_energy_source, main, validate_huawei_energy_source


class _ProbeTransport:
    def __init__(self, *, expected_port: int, expected_unit_id: int, value: int) -> None:
        self._expected_port = expected_port
        self._expected_unit_id = expected_unit_id
        self._value = value

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes:
        if request.unit_id != self._expected_unit_id:
            raise TimeoutError("unit timeout")
        address = int.from_bytes(request.payload[0:2], "big")
        count = int.from_bytes(request.payload[2:4], "big")
        if address != 10 or count != 1:
            raise AssertionError("unexpected probe read")
        payload = int(self._value).to_bytes(2, "big")
        return bytes((0x03, len(payload))) + payload


class _FieldProbeTransport:
    def __init__(self, values: dict[int, tuple[int, ...]]) -> None:
        self._values = values

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes:
        _ = timeout_seconds
        address = int.from_bytes(request.payload[0:2], "big")
        count = int.from_bytes(request.payload[2:4], "big")
        registers = self._values[address]
        if len(registers) != count:
            raise AssertionError("unexpected probe count")
        payload = b"".join(int(register).to_bytes(2, "big") for register in registers)
        return bytes((0x03, len(payload))) + payload


class TestVenusEvchargerEnergyProbe(unittest.TestCase):
    @staticmethod
    def _write_config(directory: str, filename: str, content: str) -> str:
        path = Path(directory) / filename
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_detect_modbus_energy_source_uses_huawei_candidates_until_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "energy.ini",
                "[Adapter]\nType=modbus\n"
                "[Transport]\nRequestTimeoutSeconds=2.0\n"
                "[SocRead]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=0.1\n",
            )

            def fake_transport(settings: object) -> object:
                port = getattr(settings, "port")
                if port != 502:
                    raise TimeoutError("tcp timeout")
                return _ProbeTransport(expected_port=502, expected_unit_id=1, value=523)

            with patch("venus_evcharger.energy.probe.create_modbus_transport", side_effect=fake_transport):
                payload = detect_modbus_energy_source(
                    config_path,
                    profile_name="huawei_ma_native_ap",
                )

        self.assertEqual(payload["profile_name"], "huawei_ma_native_ap")
        self.assertEqual(payload["probe_field"]["section"], "SocRead")
        self.assertEqual(payload["detected"]["host"], "192.168.200.1")
        self.assertEqual(payload["detected"]["port"], 502)
        self.assertEqual(payload["detected"]["unit_id"], 1)
        self.assertEqual(payload["detected"]["scaled_value"], 52.300000000000004)
        self.assertEqual(payload["attempts"][0]["port"], 6607)
        self.assertFalse(payload["attempts"][0]["ok"])
        self.assertEqual(payload["attempts"][0]["reason"], "timeout")

    def test_detect_modbus_energy_source_prefers_cli_override_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "energy.ini",
                "[Adapter]\nType=modbus\n"
                "[Transport]\nHost=10.0.0.8\nPort=1502\nUnitId=7\nRequestTimeoutSeconds=2.0\n"
                "[BatteryPowerRead]\nRegisterType=holding\nAddress=10\nDataType=int16\nScale=1\n",
            )

            def fake_transport(settings: object) -> object:
                self.assertEqual(getattr(settings, "host"), "10.0.0.15")
                self.assertEqual(getattr(settings, "port"), 6607)
                self.assertEqual(getattr(settings, "unit_id"), 3)
                return _ProbeTransport(expected_port=6607, expected_unit_id=3, value=42)

            with patch("venus_evcharger.energy.probe.create_modbus_transport", side_effect=fake_transport):
                payload = detect_modbus_energy_source(
                    config_path,
                    profile_name="huawei_ma_native_lan",
                    host="10.0.0.15",
                    port=6607,
                    unit_id=3,
                )

        self.assertEqual(payload["detected"]["host"], "10.0.0.15")
        self.assertEqual(payload["detected"]["port"], 6607)
        self.assertEqual(payload["detected"]["unit_id"], 3)
        self.assertEqual(payload["attempts"], [payload["detected"]])

    def test_main_detect_modbus_energy_prints_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "energy.ini",
                "[Adapter]\nType=modbus\n"
                "[Transport]\nRequestTimeoutSeconds=2.0\n"
                "[SocRead]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=0.1\n",
            )
            stdout = io.StringIO()

            def fake_transport(settings: object) -> object:
                return _ProbeTransport(expected_port=getattr(settings, "port"), expected_unit_id=0, value=481)

            with patch("venus_evcharger.energy.probe.create_modbus_transport", side_effect=fake_transport):
                with redirect_stdout(stdout):
                    rc = main(
                        [
                            "detect-modbus-energy",
                            config_path,
                            "--profile",
                            "huawei_smartlogger_modbus_tcp",
                            "--host",
                            "10.0.0.20",
                            "--unit-id",
                            "0",
                        ]
                    )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual(payload["profile_details"]["platform"], "smartlogger")
        self.assertEqual(payload["detected"]["unit_id"], 0)

    def test_validate_huawei_energy_source_reads_required_and_meter_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "huawei.ini",
                "[Adapter]\nType=modbus\n"
                "[Transport]\nRequestTimeoutSeconds=2.0\n"
                "[SocRead]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=0.1\n"
                "[BatteryPowerRead]\nRegisterType=holding\nAddress=30\nDataType=int16\nScale=-1\n"
                "[AcPowerRead]\nRegisterType=holding\nAddress=40\nDataType=uint16\n"
                "[PvInputPowerRead]\nRegisterType=holding\nAddress=50\nDataType=uint16\n"
                "[GridInteractionRead]\nRegisterType=holding\nAddress=60\nDataType=int32\nScale=-1\n"
                "[OperatingModeRead]\nRegisterType=holding\nAddress=70\nDataType=uint16\n",
            )

            def fake_transport(settings: object) -> object:
                port = getattr(settings, "port")
                if port != 502:
                    raise TimeoutError("tcp timeout")
                return _FieldProbeTransport(
                    {
                        10: (645,),
                        30: (0xF830,),
                        40: (3200,),
                        50: (2800,),
                        60: (0xFFFE, 0xD4F0,),
                        70: (4,),
                        37100: (1,),
                        37113: (0xFFFF, 0xFC18,),
                        37119: (0, 150,),
                        37121: (0, 75,),
                        37125: (2,),
                    }
                )

            with patch("venus_evcharger.energy.probe.create_modbus_transport", side_effect=fake_transport):
                payload = validate_huawei_energy_source(
                    config_path,
                    profile_name="huawei_mb_native_lan",
                    host="10.0.0.25",
                )

        self.assertTrue(payload["validation_ok"])
        self.assertTrue(payload["meter_block_detected"])
        self.assertEqual(payload["recommendation"]["suggested_profile"], "huawei_mb_native_lan")
        self.assertEqual(
            payload["recommendation"]["suggested_template"],
            "deploy/venus/template-energy-source-huawei-mb-modbus.ini",
        )
        self.assertEqual(
            payload["recommendation"]["suggested_config_path"],
            "/data/etc/huawei-mb-modbus.ini",
        )
        self.assertEqual(payload["recommendation"]["port"], 502)
        self.assertEqual(payload["recommendation"]["unit_id"], 0)
        self.assertIn(
            "AutoEnergySource.huawei.Profile=huawei_mb_native_lan",
            payload["recommendation"]["config_snippet"],
        )
        self.assertIn(
            "AutoEnergySource.huawei.ConfigPath=/data/etc/huawei-mb-modbus.ini",
            payload["recommendation"]["config_snippet"],
        )
        self.assertIn(
            "AutoEnergySource.huawei.Port=502",
            payload["recommendation"]["config_snippet"],
        )
        self.assertIn(
            "AutoEnergySource.huawei.UnitId=0",
            payload["recommendation"]["config_snippet"],
        )
        self.assertTrue(payload["recommendation"]["capacity_required_for_weighted_soc"])
        self.assertEqual(
            payload["recommendation"]["capacity_config_key"],
            "AutoEnergySource.huawei.UsableCapacityWh",
        )
        self.assertIn(
            "usable battery capacity in Wh",
            payload["recommendation"]["capacity_hint"],
        )
        self.assertIn(
            "- meter block: present",
            payload["recommendation"]["wizard_hint_block"],
        )
        self.assertIn(
            "- capacity follow-up: set AutoEnergySource.huawei.UsableCapacityWh for weighted combined SOC",
            payload["recommendation"]["wizard_hint_block"],
        )
        sections = {entry["section"]: entry for entry in payload["field_results"]}
        self.assertTrue(sections["GridInteractionRead"]["ok"])
        self.assertEqual(sections["GridInteractionRead"]["scaled_value"], 76560.0)
        self.assertTrue(sections["HuaweiMeterActivePowerRead"]["ok"])
        self.assertEqual(sections["HuaweiMeterActivePowerRead"]["scaled_value"], 1000.0)

    def test_main_validate_huawei_energy_prints_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "huawei.ini",
                "[Adapter]\nType=modbus\n"
                "[Transport]\nRequestTimeoutSeconds=2.0\n"
                "[SocRead]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=0.1\n",
            )
            stdout = io.StringIO()

            def fake_transport(settings: object) -> object:
                port = getattr(settings, "port")
                if port != 502:
                    raise TimeoutError("tcp timeout")
                return _FieldProbeTransport(
                    {
                        10: (500,),
                        37100: (1,),
                        37113: (0, 250,),
                        37119: (0, 10,),
                        37121: (0, 5,),
                        37125: (2,),
                    }
                )

            with patch("venus_evcharger.energy.probe.create_modbus_transport", side_effect=fake_transport):
                with redirect_stdout(stdout):
                    rc = main(
                        [
                            "validate-huawei-energy",
                            config_path,
                            "--profile",
                            "huawei_smartlogger_modbus_tcp",
                            "--host",
                            "10.0.0.30",
                        ]
                    )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(rc, 0)
        self.assertTrue(payload["validation_ok"])
        self.assertTrue(payload["meter_block_detected"])
        self.assertIn("suggested_template", payload["recommendation"])
        self.assertIn("config_snippet", payload["recommendation"])
        self.assertIn("wizard_hint_block", payload["recommendation"])
        self.assertEqual(payload["recommendation"]["suggested_profile"], "huawei_smartlogger_modbus_tcp")

    def test_main_validate_huawei_energy_can_emit_config_snippet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "huawei.ini",
                "[Adapter]\nType=modbus\n"
                "[Transport]\nRequestTimeoutSeconds=2.0\n"
                "[SocRead]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=0.1\n",
            )
            stdout = io.StringIO()

            def fake_transport(settings: object) -> object:
                port = getattr(settings, "port")
                if port != 502:
                    raise TimeoutError("tcp timeout")
                return _FieldProbeTransport(
                    {
                        10: (500,),
                        37100: (1,),
                        37113: (0, 250,),
                        37119: (0, 10,),
                        37121: (0, 5,),
                        37125: (2,),
                    }
                )

            with patch("venus_evcharger.energy.probe.create_modbus_transport", side_effect=fake_transport):
                with redirect_stdout(stdout):
                    rc = main(
                        [
                            "validate-huawei-energy",
                            config_path,
                            "--profile",
                            "huawei_smartlogger_modbus_tcp",
                            "--host",
                            "10.0.0.30",
                            "--emit",
                            "ini",
                        ]
                    )

        output = stdout.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("AutoEnergySource.huawei.Profile=huawei_smartlogger_modbus_tcp", output)
        self.assertIn("AutoEnergySource.huawei.ConfigPath=/data/etc/huawei-mb-modbus.ini", output)
        self.assertNotIn('"recommendation"', output)

    def test_main_validate_huawei_energy_can_emit_wizard_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "huawei.ini",
                "[Adapter]\nType=modbus\n"
                "[Transport]\nRequestTimeoutSeconds=2.0\n"
                "[SocRead]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=0.1\n",
            )
            stdout = io.StringIO()

            def fake_transport(settings: object) -> object:
                port = getattr(settings, "port")
                if port != 502:
                    raise TimeoutError("tcp timeout")
                return _FieldProbeTransport(
                    {
                        10: (500,),
                        37100: (1,),
                        37113: (0, 250,),
                        37119: (0, 10,),
                        37121: (0, 5,),
                        37125: (2,),
                    }
                )

            with patch("venus_evcharger.energy.probe.create_modbus_transport", side_effect=fake_transport):
                with redirect_stdout(stdout):
                    rc = main(
                        [
                            "validate-huawei-energy",
                            config_path,
                            "--profile",
                            "huawei_smartlogger_modbus_tcp",
                            "--host",
                            "10.0.0.30",
                            "--emit",
                            "wizard-hint",
                        ]
                    )

        output = stdout.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("Huawei recommendation", output)
        self.assertIn("- profile: huawei_smartlogger_modbus_tcp", output)
        self.assertIn("- meter block: present", output)

    def test_main_validate_huawei_energy_can_write_recommendation_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "huawei.ini",
                "[Adapter]\nType=modbus\n"
                "[Transport]\nRequestTimeoutSeconds=2.0\n"
                "[SocRead]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=0.1\n",
            )
            output_prefix = Path(temp_dir) / "exports" / "huawei-rec"
            stdout = io.StringIO()

            def fake_transport(settings: object) -> object:
                port = getattr(settings, "port")
                if port != 502:
                    raise TimeoutError("tcp timeout")
                return _FieldProbeTransport(
                    {
                        10: (500,),
                        37100: (1,),
                        37113: (0, 250,),
                        37119: (0, 10,),
                        37121: (0, 5,),
                        37125: (2,),
                    }
                )

            with patch("venus_evcharger.energy.probe.create_modbus_transport", side_effect=fake_transport):
                with redirect_stdout(stdout):
                    rc = main(
                        [
                            "validate-huawei-energy",
                            config_path,
                            "--profile",
                            "huawei_smartlogger_modbus_tcp",
                            "--host",
                            "10.0.0.30",
                            "--write-recommendation-prefix",
                            str(output_prefix),
                        ]
                    )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            written_files = payload["written_files"]
            self.assertEqual(written_files["config_snippet"], str(output_prefix) + ".ini")
            self.assertEqual(written_files["wizard_hint"], str(output_prefix) + ".wizard.txt")
            self.assertEqual(written_files["summary"], str(output_prefix) + ".summary.txt")
            self.assertIn(
                "AutoEnergySource.huawei.Profile=huawei_smartlogger_modbus_tcp",
                Path(written_files["config_snippet"]).read_text(encoding="utf-8"),
            )
            self.assertIn(
                "Huawei recommendation",
                Path(written_files["wizard_hint"]).read_text(encoding="utf-8"),
            )
            self.assertIn(
                "Use profile huawei_smartlogger_modbus_tcp",
                Path(written_files["summary"]).read_text(encoding="utf-8"),
            )

    def test_validate_huawei_energy_source_can_render_custom_source_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "huawei.ini",
                "[Adapter]\nType=modbus\n"
                "[Transport]\nRequestTimeoutSeconds=2.0\n"
                "[SocRead]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=0.1\n",
            )

            def fake_transport(settings: object) -> object:
                port = getattr(settings, "port")
                if port != 502:
                    raise TimeoutError("tcp timeout")
                return _FieldProbeTransport(
                    {
                        10: (500,),
                        37100: (1,),
                        37113: (0, 250,),
                        37119: (0, 10,),
                        37121: (0, 5,),
                        37125: (2,),
                    }
                )

            with patch("venus_evcharger.energy.probe.create_modbus_transport", side_effect=fake_transport):
                payload = validate_huawei_energy_source(
                    config_path,
                    profile_name="huawei_mb_sdongle",
                    host="10.0.0.30",
                    source_id="hybrid_ext",
                )

        self.assertEqual(
            payload["recommendation"]["capacity_config_key"],
            "AutoEnergySource.hybrid_ext.UsableCapacityWh",
        )
        self.assertIn(
            "AutoEnergySource.hybrid_ext.Profile=huawei_mb_sdongle",
            payload["recommendation"]["config_snippet"],
        )
        self.assertIn(
            "- source id: hybrid_ext",
            payload["recommendation"]["wizard_hint_block"],
        )
