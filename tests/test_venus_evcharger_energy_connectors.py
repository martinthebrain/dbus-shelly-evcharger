# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from configparser import ConfigParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.energy import EnergySourceDefinition, EnergySourceSnapshot, read_energy_source_snapshot
from venus_evcharger.energy import connectors as energy_connectors


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _FakeModbusTransport:
    def exchange(self, request: object, *, timeout_seconds: float) -> bytes:
        _ = timeout_seconds
        function_code = getattr(request, "function_code")
        payload = getattr(request, "payload")
        address = int.from_bytes(payload[:2], "big")
        count = int.from_bytes(payload[2:4], "big")
        if function_code != 0x03:
            raise AssertionError("unexpected Modbus function")
        values = {
            10: (645,),
            20: (12000,),
            30: (0xF830,),
            40: (3200,),
            50: (4,),
            60: (900,),
            70: (1400,),
            80: (0xFFFE, 0xD4F0,),
        }
        registers = values[address]
        if len(registers) != count:
            raise AssertionError("unexpected register count")
        register_bytes = b"".join(int(register).to_bytes(2, "big") for register in registers)
        return bytes((0x03, len(register_bytes))) + register_bytes


class TestVenusEvchargerEnergyConnectors(unittest.TestCase):
    @staticmethod
    def _write_config(directory: str, content: str) -> str:
        path = Path(directory) / "external-energy.ini"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_read_energy_source_snapshot_dispatches_to_template_http_connector(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nBaseUrl=http://hybrid.local\n"
                "[EnergyRequest]\nMethod=GET\nUrl=/state\n"
                "[EnergyResponse]\nSocPath=data.soc\nUsableCapacityWhPath=data.capacity_wh\n"
                "BatteryPowerPath=data.battery_power_w\nAcPowerPath=data.ac_power_w\n"
                "PvInputPowerPath=data.pv_input_power_w\nGridInteractionPath=data.grid_power_w\n"
                "OperatingModePath=data.mode\n"
                "OnlinePath=data.online\nConfidencePath=data.confidence\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "data": {
                        "soc": 74.5,
                        "capacity_wh": 12000.0,
                        "battery_power_w": -1800.0,
                        "ac_power_w": 3200.0,
                        "pv_input_power_w": 2500.0,
                        "grid_power_w": -600.0,
                        "mode": "self-consumption",
                        "online": True,
                        "confidence": 0.8,
                    }
                }
            )
            runtime = SimpleNamespace(session=session, shelly_request_timeout_seconds=2.0)
            owner = SimpleNamespace(service=runtime)
            source = EnergySourceDefinition(
                source_id="hybrid",
                role="hybrid-inverter",
                connector_type="template_http",
                config_path=config_path,
                service_name="external-hybrid",
            )

            snapshot = read_energy_source_snapshot(owner, source, 100.0)

            self.assertEqual(snapshot.source_id, "hybrid")
            self.assertEqual(snapshot.role, "hybrid-inverter")
            self.assertEqual(snapshot.service_name, "external-hybrid")
            self.assertEqual(snapshot.soc, 74.5)
            self.assertEqual(snapshot.usable_capacity_wh, 12000.0)
            self.assertEqual(snapshot.net_battery_power_w, -1800.0)
            self.assertEqual(snapshot.charge_power_w, 1800.0)
            self.assertEqual(snapshot.discharge_power_w, 0.0)
            self.assertEqual(snapshot.ac_power_w, 3200.0)
            self.assertEqual(snapshot.pv_input_power_w, 2500.0)
            self.assertEqual(snapshot.grid_interaction_w, -600.0)
            self.assertEqual(snapshot.operating_mode, "self-consumption")
            self.assertTrue(snapshot.online)
            self.assertEqual(snapshot.confidence, 0.8)
            self.assertEqual(snapshot.captured_at, 100.0)
            session.get.assert_called_once_with(url="http://hybrid.local/state", timeout=2.0)

    def test_template_http_connector_normalizes_out_of_range_values_and_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nBaseUrl=http://battery.local\n"
                "[EnergyRequest]\nUrl=/snapshot\n"
                "[EnergyResponse]\nSocPath=soc\nUsableCapacityWhPath=capacity_wh\nConfidencePath=confidence\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "soc": 150.0,
                    "capacity_wh": -1.0,
                    "confidence": 5.0,
                }
            )
            runtime = SimpleNamespace(session=session, shelly_request_timeout_seconds=2.0)
            owner = SimpleNamespace(service=runtime)
            source = EnergySourceDefinition(
                source_id="external-battery",
                role="battery",
                connector_type="template_http",
                config_path=config_path,
            )

            snapshot = read_energy_source_snapshot(owner, source, 123.0)

            self.assertIsNone(snapshot.soc)
            self.assertIsNone(snapshot.usable_capacity_wh)
            self.assertTrue(snapshot.online)
            self.assertEqual(snapshot.confidence, 1.0)

    def test_read_energy_source_snapshot_uses_source_capacity_fallback_and_dbus_connector(self) -> None:
        forwarded: list[tuple[str, float]] = []

        def _dbus_snapshot(source: EnergySourceDefinition, now: float) -> EnergySourceSnapshot:
            forwarded.append((source.source_id, now))
            return EnergySourceSnapshot(
                source_id=source.source_id,
                role=source.role,
                service_name="com.victronenergy.battery.demo",
                soc=55.0,
                usable_capacity_wh=source.usable_capacity_wh,
                online=True,
                confidence=1.0,
                captured_at=now,
            )

        owner = SimpleNamespace(_dbus_energy_source_snapshot=_dbus_snapshot)
        source = EnergySourceDefinition(
            source_id="victron",
            role="battery",
            connector_type="dbus",
            usable_capacity_wh=5120.0,
        )

        snapshot = read_energy_source_snapshot(owner, source, 50.0)

        self.assertEqual(forwarded, [("victron", 50.0)])
        self.assertEqual(snapshot.usable_capacity_wh, 5120.0)

    def test_template_http_connector_uses_source_capacity_when_response_omits_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nBaseUrl=http://battery.local\n"
                "[EnergyRequest]\nUrl=/snapshot\n"
                "[EnergyResponse]\nSocPath=soc\nBatteryPowerPath=battery_power_w\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"soc": 61.0, "battery_power_w": 900.0})
            runtime = SimpleNamespace(session=session, shelly_request_timeout_seconds=2.0)
            owner = SimpleNamespace(service=runtime)
            source = EnergySourceDefinition(
                source_id="external-battery",
                role="battery",
                connector_type="template_http",
                config_path=config_path,
                usable_capacity_wh=7000.0,
            )

            snapshot = read_energy_source_snapshot(owner, source, 200.0)

            self.assertEqual(snapshot.usable_capacity_wh, 7000.0)
            self.assertEqual(snapshot.discharge_power_w, 900.0)

    def test_read_energy_source_snapshot_dispatches_to_modbus_connector(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nTransport=tcp\n"
                "[Transport]\nHost=192.0.2.10\nPort=502\nUnitId=7\nRequestTimeoutSeconds=2.0\n"
                "[SocRead]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=0.1\n"
                "[UsableCapacityRead]\nRegisterType=holding\nAddress=20\nDataType=uint16\n"
                "[BatteryPowerRead]\nRegisterType=holding\nAddress=30\nDataType=int16\n"
                "[ChargeLimitPowerRead]\nRegisterType=holding\nAddress=60\nDataType=uint16\n"
                "[DischargeLimitPowerRead]\nRegisterType=holding\nAddress=70\nDataType=uint16\n"
                "[AcPowerRead]\nRegisterType=holding\nAddress=40\nDataType=uint16\n"
                "[PvInputPowerRead]\nRegisterType=holding\nAddress=40\nDataType=uint16\n"
                "[GridInteractionRead]\nRegisterType=holding\nAddress=80\nDataType=int32\nScale=-1\n"
                "[OperatingModeRead]\nRegisterType=holding\nAddress=50\nDataType=uint16\n"
                "[OperatingModeMap]\n4=maximise_self_consumption\n"
                "[Aggregation]\nAcPowerScopeKey={host}:{port}:ac\nPvInputPowerScopeKey={host}:{port}:pv\n"
                "GridInteractionScopeKey={host}:{port}:meter\n",
            )
            runtime = SimpleNamespace(shelly_request_timeout_seconds=2.0)
            owner = SimpleNamespace(service=runtime)
            source = EnergySourceDefinition(
                source_id="modbus-battery",
                role="battery",
                connector_type="modbus",
                config_path=config_path,
            )

            with patch("venus_evcharger.energy.connectors.create_modbus_transport", return_value=_FakeModbusTransport()):
                snapshot = read_energy_source_snapshot(owner, source, 300.0)

            self.assertEqual(snapshot.service_name, "192.0.2.10")
            self.assertEqual(snapshot.soc, 64.5)
            self.assertEqual(snapshot.usable_capacity_wh, 12000.0)
            self.assertEqual(snapshot.net_battery_power_w, -2000.0)
            self.assertEqual(snapshot.charge_power_w, 2000.0)
            self.assertEqual(snapshot.charge_limit_power_w, 900.0)
            self.assertEqual(snapshot.discharge_limit_power_w, 1400.0)
            self.assertEqual(snapshot.ac_power_w, 3200.0)
            self.assertEqual(snapshot.pv_input_power_w, 3200.0)
            self.assertEqual(snapshot.grid_interaction_w, 76560.0)
            self.assertEqual(snapshot.ac_power_scope_key, "192.0.2.10:502:ac")
            self.assertEqual(snapshot.pv_input_power_scope_key, "192.0.2.10:502:pv")
            self.assertEqual(snapshot.grid_interaction_scope_key, "192.0.2.10:502:meter")
            self.assertEqual(snapshot.operating_mode, "maximise_self_consumption")
            self.assertTrue(snapshot.online)

    def test_modbus_connector_supports_negative_scale_for_vendor_sign_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nTransport=tcp\n"
                "[Transport]\nHost=192.0.2.11\nPort=502\nUnitId=1\nRequestTimeoutSeconds=2.0\n"
                "[BatteryPowerRead]\nRegisterType=holding\nAddress=30\nDataType=int16\nScale=-1\n",
            )
            runtime = SimpleNamespace(shelly_request_timeout_seconds=2.0)
            owner = SimpleNamespace(service=runtime)
            source = EnergySourceDefinition(
                source_id="vendor-battery",
                role="battery",
                connector_type="modbus",
                config_path=config_path,
            )

            with patch("venus_evcharger.energy.connectors.create_modbus_transport", return_value=_FakeModbusTransport()):
                snapshot = read_energy_source_snapshot(owner, source, 301.0)

            self.assertEqual(snapshot.net_battery_power_w, 2000.0)
            self.assertEqual(snapshot.charge_power_w, 0.0)
            self.assertEqual(snapshot.discharge_power_w, 2000.0)

    def test_read_energy_source_snapshot_dispatches_to_command_json_connector(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Command]\nArgs=python3 /tmp/fake-energy-helper.py --once\nTimeoutSeconds=1.5\n"
                "[Response]\nSocPath=data.soc\nBatteryPowerPath=data.battery_power_w\n"
                "AcPowerPath=data.ac_power_w\nPvInputPowerPath=data.pv_input_power_w\n"
                "GridInteractionPath=data.grid_power_w\nOperatingModePath=data.mode\n"
                "OnlinePath=data.online\nConfidencePath=data.confidence\n",
            )
            runtime = SimpleNamespace(shelly_request_timeout_seconds=2.0)
            owner = SimpleNamespace(service=runtime)
            source = EnergySourceDefinition(
                source_id="helper-energy",
                role="hybrid-inverter",
                connector_type="command_json",
                config_path=config_path,
                usable_capacity_wh=9000.0,
            )
            completed = SimpleNamespace(
                stdout='{"data":{"soc":57.0,"battery_power_w":1100.0,"ac_power_w":2500.0,"pv_input_power_w":1800.0,"grid_power_w":200.0,"mode":"support","online":true,"confidence":0.6}}'
            )

            with patch("venus_evcharger.energy.connectors.subprocess.run", return_value=completed) as run_mock:
                snapshot = read_energy_source_snapshot(owner, source, 400.0)

            self.assertEqual(snapshot.service_name, "python3")
            self.assertEqual(snapshot.soc, 57.0)
            self.assertEqual(snapshot.usable_capacity_wh, 9000.0)
            self.assertEqual(snapshot.net_battery_power_w, 1100.0)
            self.assertEqual(snapshot.discharge_power_w, 1100.0)
            self.assertEqual(snapshot.ac_power_w, 2500.0)
            self.assertEqual(snapshot.pv_input_power_w, 1800.0)
            self.assertEqual(snapshot.grid_interaction_w, 200.0)
            self.assertEqual(snapshot.operating_mode, "support")
            self.assertTrue(snapshot.online)
            self.assertEqual(snapshot.confidence, 0.6)
            run_mock.assert_called_once()

    def test_template_http_settings_and_validation_helpers_cover_cache_and_errors(self) -> None:
        runtime = SimpleNamespace(shelly_request_timeout_seconds=2.0)
        source = EnergySourceDefinition(source_id="battery", role="battery", connector_type="template_http", config_path="")

        with self.assertRaisesRegex(ValueError, "requires ConfigPath"):
            energy_connectors._template_http_energy_source_settings(runtime, source)

        settings = energy_connectors.TemplateHttpEnergySourceSettings(
            base_url="http://cached.local",
            auth_settings=energy_connectors.TemplateAuthSettings("", "", False, None, None),
            timeout_seconds=2.0,
            request_method="GET",
            request_url="http://cached.local/state",
            soc_path="soc",
            usable_capacity_wh_path=None,
            battery_power_path=None,
            ac_power_path=None,
            pv_input_power_path=None,
            grid_interaction_path=None,
            operating_mode_path=None,
            online_path=None,
            confidence_path=None,
        )
        runtime._energy_template_settings_cache = {"cached.ini": settings}
        cached_source = EnergySourceDefinition(
            source_id="cached",
            role="battery",
            connector_type="template_http",
            config_path="cached.ini",
        )
        self.assertIs(energy_connectors._template_http_energy_source_settings(runtime, cached_source), settings)

        with self.assertRaisesRegex(ValueError, "requires \\[EnergyRequest\\] Url"):
            energy_connectors._validate_template_http_energy_source_settings(
                cached_source,
                settings.__class__(**{**settings.__dict__, "request_url": ""}),
            )
        with self.assertRaisesRegex(ValueError, "requires at least one readable EnergyResponse path"):
            energy_connectors._validate_template_http_energy_source_settings(
                cached_source,
                settings.__class__(**{**settings.__dict__, "request_url": "http://cached.local/state", "soc_path": None}),
            )
        self.assertEqual(
            energy_connectors._template_source_name(
                EnergySourceDefinition(source_id="cached", role="battery", connector_type="template_http", config_path="cfg.ini"),
                settings.__class__(**{**settings.__dict__, "base_url": ""}),
            ),
            "cfg.ini",
        )

    def test_modbus_and_command_connector_helpers_cover_validation_and_fallbacks(self) -> None:
        runtime = SimpleNamespace(shelly_request_timeout_seconds=2.0)

        with self.assertRaisesRegex(ValueError, "requires ConfigPath"):
            energy_connectors._modbus_energy_source_settings(
                runtime,
                EnergySourceDefinition(source_id="modbus", role="battery", connector_type="modbus", config_path=""),
            )

        with self.assertRaisesRegex(ValueError, "requires ConfigPath"):
            energy_connectors._command_json_energy_source_settings(
                runtime,
                EnergySourceDefinition(source_id="helper", role="battery", connector_type="command_json", config_path=""),
            )

        parser = ConfigParser()
        parser.add_section("SocRead")
        parser.set("SocRead", "RegisterType", "holding")
        parser.set("SocRead", "DataType", "uint16")
        parser.set("SocRead", "Scale", "1")
        self.assertIsNone(energy_connectors._modbus_field_settings(parser, "Missing"))
        self.assertIsNone(energy_connectors._modbus_field_settings(parser, "SocRead"))

        transport_settings = energy_connectors.ModbusTransportSettings(
            transport_kind="tcp",
            unit_id=1,
            timeout_seconds=1.0,
            host="",
            port=502,
            device="/dev/ttyUSB0",
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
        self.assertEqual(
            energy_connectors._modbus_source_name(
                EnergySourceDefinition(
                    source_id="modbus",
                    role="battery",
                    connector_type="modbus",
                    config_path="cfg.ini",
                    service_name="modbus-service",
                ),
                transport_settings,
            ),
            "modbus-service",
        )
        self.assertEqual(
            energy_connectors._modbus_source_name(
                EnergySourceDefinition(source_id="modbus", role="battery", connector_type="modbus", config_path="cfg.ini"),
                transport_settings,
            ),
            "/dev/ttyUSB0",
        )
        self.assertEqual(
            energy_connectors._command_source_name(
                EnergySourceDefinition(source_id="helper", role="battery", connector_type="command_json", config_path="cfg.ini"),
                energy_connectors.CommandJsonEnergySourceSettings(
                    command=(),
                    timeout_seconds=1.0,
                    soc_path=None,
                    usable_capacity_wh_path=None,
                    battery_power_path=None,
                    ac_power_path=None,
                    pv_input_power_path=None,
                    grid_interaction_path=None,
                    operating_mode_path=None,
                    online_path=None,
                    confidence_path=None,
                ),
            ),
            "cfg.ini",
        )

        with self.assertRaisesRegex(ValueError, "requires at least one Modbus read section"):
            energy_connectors._validate_modbus_energy_source_settings(
                EnergySourceDefinition(source_id="modbus", role="battery", connector_type="modbus"),
                energy_connectors.ModbusEnergySourceSettings(
                    transport_settings=transport_settings,
                    soc_field=None,
                    usable_capacity_field=None,
                    battery_power_field=None,
                    charge_limit_power_field=None,
                    discharge_limit_power_field=None,
                    ac_power_field=None,
                    pv_input_power_field=None,
                    grid_interaction_field=None,
                    operating_mode_field=None,
                    operating_mode_map={},
                    ac_power_scope_key="",
                    pv_input_power_scope_key="",
                    grid_interaction_scope_key="",
                ),
            )
        with self.assertRaisesRegex(ValueError, "requires \\[Command\\] Args"):
            energy_connectors._validate_command_json_energy_source_settings(
                EnergySourceDefinition(source_id="helper", role="battery", connector_type="command_json"),
                energy_connectors.CommandJsonEnergySourceSettings(
                    command=(),
                    timeout_seconds=1.0,
                    soc_path="soc",
                    usable_capacity_wh_path=None,
                    battery_power_path=None,
                    ac_power_path=None,
                    pv_input_power_path=None,
                    grid_interaction_path=None,
                    operating_mode_path=None,
                    online_path=None,
                    confidence_path=None,
                ),
            )
        with self.assertRaisesRegex(ValueError, "requires at least one Response path or UsableCapacityWh"):
            energy_connectors._validate_command_json_energy_source_settings(
                EnergySourceDefinition(source_id="helper", role="battery", connector_type="command_json"),
                energy_connectors.CommandJsonEnergySourceSettings(
                    command=("helper",),
                    timeout_seconds=1.0,
                    soc_path=None,
                    usable_capacity_wh_path=None,
                    battery_power_path=None,
                    ac_power_path=None,
                    pv_input_power_path=None,
                    grid_interaction_path=None,
                    operating_mode_path=None,
                    online_path=None,
                    confidence_path=None,
                ),
            )

        runtime._energy_modbus_settings_cache = {}
        runtime._energy_modbus_client_cache = {}
        source = EnergySourceDefinition(source_id="modbus", role="battery", connector_type="modbus", config_path="cfg.ini")
        cached_settings = energy_connectors.ModbusEnergySourceSettings(
            transport_settings=transport_settings,
            soc_field=energy_connectors.ModbusEnergyFieldSettings("holding", 1, "uint16", 1.0, "big"),
            usable_capacity_field=None,
            battery_power_field=None,
            charge_limit_power_field=None,
            discharge_limit_power_field=None,
            ac_power_field=None,
            pv_input_power_field=None,
            grid_interaction_field=None,
            operating_mode_field=None,
            operating_mode_map={},
            ac_power_scope_key="",
            pv_input_power_scope_key="",
            grid_interaction_scope_key="",
        )
        runtime._energy_modbus_settings_cache["cfg.ini"] = cached_settings
        self.assertIs(energy_connectors._modbus_energy_source_settings(runtime, source), cached_settings)
        with patch("venus_evcharger.energy.connectors.create_modbus_transport", return_value=_FakeModbusTransport()):
            first_client = energy_connectors._modbus_energy_source_client(runtime, source, cached_settings)
            second_client = energy_connectors._modbus_energy_source_client(runtime, source, cached_settings)
        self.assertIs(first_client, second_client)
        self.assertEqual(energy_connectors._modbus_source_name(source, cached_settings.transport_settings), "/dev/ttyUSB0")
        self.assertEqual(
            energy_connectors._modbus_source_name(
                source,
                cached_settings.transport_settings.__class__(
                    **{**cached_settings.transport_settings.__dict__, "device": "", "host": ""}
                ),
            ),
            "cfg.ini",
        )

        runtime._energy_command_settings_cache = {}
        command_source = EnergySourceDefinition(
            source_id="helper",
            role="battery",
            connector_type="command_json",
            config_path="helper.ini",
            service_name="helper-service",
        )
        cached_command_settings = energy_connectors.CommandJsonEnergySourceSettings(
            command=("helper",),
            timeout_seconds=1.0,
            soc_path="soc",
            usable_capacity_wh_path="capacity",
            battery_power_path=None,
            ac_power_path=None,
            pv_input_power_path=None,
            grid_interaction_path=None,
            operating_mode_path=None,
            online_path=None,
            confidence_path=None,
        )
        runtime._energy_command_settings_cache["helper.ini"] = cached_command_settings
        self.assertIs(
            energy_connectors._command_json_energy_source_settings(runtime, command_source),
            cached_command_settings,
        )
        self.assertEqual(
            energy_connectors._command_source_name(command_source, cached_command_settings),
            "helper-service",
        )

        command_owner = SimpleNamespace(service=runtime)
        completed = SimpleNamespace(stdout='{"soc":150.0,"capacity":-5.0}')
        with patch("venus_evcharger.energy.connectors.subprocess.run", return_value=completed):
            snapshot = energy_connectors._command_json_energy_source_snapshot(command_owner, command_source, 1.0)
        self.assertIsNone(snapshot.soc)
        self.assertIsNone(snapshot.usable_capacity_wh)

        modbus_owner = SimpleNamespace(service=runtime)
        negative_capacity_source = EnergySourceDefinition(
            source_id="modbus-negative",
            role="battery",
            connector_type="modbus",
            config_path="neg.ini",
            usable_capacity_wh=7000.0,
        )
        negative_capacity_settings = energy_connectors.ModbusEnergySourceSettings(
            transport_settings=transport_settings,
            soc_field=energy_connectors.ModbusEnergyFieldSettings("holding", 1, "uint16", 1.0, "big"),
            usable_capacity_field=energy_connectors.ModbusEnergyFieldSettings("holding", 2, "uint16", 1.0, "big"),
            battery_power_field=None,
            charge_limit_power_field=None,
            discharge_limit_power_field=None,
            ac_power_field=None,
            pv_input_power_field=None,
            grid_interaction_field=None,
            operating_mode_field=None,
            operating_mode_map={},
            ac_power_scope_key="",
            pv_input_power_scope_key="",
            grid_interaction_scope_key="",
        )
        modbus_client = SimpleNamespace(read_scalar=MagicMock(side_effect=[150.0, -2.0]))
        with (
            patch("venus_evcharger.energy.connectors._modbus_energy_source_settings", return_value=negative_capacity_settings),
            patch("venus_evcharger.energy.connectors._modbus_energy_source_client", return_value=modbus_client),
        ):
            modbus_snapshot = energy_connectors._modbus_energy_source_snapshot(modbus_owner, negative_capacity_source, 2.0)
        self.assertIsNone(modbus_snapshot.soc)
        self.assertIsNone(modbus_snapshot.usable_capacity_wh)

        fallback_capacity_source = EnergySourceDefinition(
            source_id="modbus-fallback",
            role="battery",
            connector_type="modbus",
            config_path="fallback.ini",
            usable_capacity_wh=6400.0,
        )
        fallback_capacity_settings = energy_connectors.ModbusEnergySourceSettings(
            transport_settings=transport_settings,
            soc_field=None,
            usable_capacity_field=energy_connectors.ModbusEnergyFieldSettings("holding", 2, "uint16", 1.0, "big"),
            battery_power_field=None,
            charge_limit_power_field=None,
            discharge_limit_power_field=None,
            ac_power_field=None,
            pv_input_power_field=None,
            grid_interaction_field=None,
            operating_mode_field=None,
            operating_mode_map={},
            ac_power_scope_key="",
            pv_input_power_scope_key="",
            grid_interaction_scope_key="",
        )
        with (
            patch("venus_evcharger.energy.connectors._modbus_energy_source_settings", return_value=fallback_capacity_settings),
            patch("venus_evcharger.energy.connectors._modbus_energy_source_client", return_value=SimpleNamespace()),
            patch(
                "venus_evcharger.energy.connectors._modbus_field_value",
                side_effect=[None, None, None, None, None, None, None, None, None],
            ),
        ):
            fallback_snapshot = energy_connectors._modbus_energy_source_snapshot(modbus_owner, fallback_capacity_source, 2.5)
        self.assertEqual(fallback_snapshot.usable_capacity_wh, 6400.0)

        positive_capacity_settings = cached_command_settings.__class__(**{**cached_command_settings.__dict__, "usable_capacity_wh_path": "capacity"})
        positive_capacity_source = EnergySourceDefinition(
            source_id="helper-positive",
            role="battery",
            connector_type="command_json",
            config_path="helper-positive.ini",
            usable_capacity_wh=4000.0,
        )
        with (
            patch("venus_evcharger.energy.connectors._command_json_energy_source_settings", return_value=positive_capacity_settings),
            patch(
                "venus_evcharger.energy.connectors.subprocess.run",
                return_value=SimpleNamespace(stdout='{"soc":50.0,"capacity":6000.0}'),
            ),
        ):
            positive_snapshot = energy_connectors._command_json_energy_source_snapshot(command_owner, positive_capacity_source, 3.0)
        self.assertEqual(positive_snapshot.usable_capacity_wh, 6000.0)

    def test_connector_scalar_helpers_cover_bool_and_path_parsing(self) -> None:
        class _BoolClient:
            @staticmethod
            def read_scalar(*_args: object) -> bool:
                return True

        field = energy_connectors.ModbusEnergyFieldSettings(
            register_type="holding",
            address=1,
            data_type="uint16",
            scale=2.0,
            word_order="big",
        )
        self.assertEqual(energy_connectors._modbus_field_value(_BoolClient(), field), 2.0)
        self.assertEqual(energy_connectors._normalized_connector_type("template_http_energy"), "template_http")
        self.assertEqual(energy_connectors._normalized_connector_type(""), "dbus")
        self.assertEqual(energy_connectors._command_args({"Args": ""}), ())
        self.assertEqual(energy_connectors._command_args({"Args": "python3 helper.py --once"}), ("python3", "helper.py", "--once"))
        self.assertFalse(energy_connectors._optional_bool_path({"value": "enabled"}, "value"))
        self.assertFalse(energy_connectors._optional_bool_path({"value": "disabled"}, "value"))
        self.assertTrue(energy_connectors._optional_bool_path({"value": 1}, "value"))
        self.assertTrue(energy_connectors._optional_bool_path({"value": "1"}, "value"))
        self.assertFalse(energy_connectors._optional_bool_path({"value": None}, "value"))
        self.assertEqual(energy_connectors._optional_confidence_path({"value": 5.0}, "value"), 1.0)
        self.assertEqual(energy_connectors._optional_confidence_path({"value": -1.0}, "value"), 0.0)

    def test_command_json_connector_rejects_non_object_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Command]\nArgs=python3 /tmp/fake-helper.py\n"
                "[Response]\nSocPath=data.soc\n",
            )
            runtime = SimpleNamespace(shelly_request_timeout_seconds=2.0)
            owner = SimpleNamespace(service=runtime)
            source = EnergySourceDefinition(
                source_id="helper-energy",
                role="hybrid-inverter",
                connector_type="command_json",
                config_path=config_path,
            )
            completed = SimpleNamespace(stdout='["not-an-object"]')

            with patch("venus_evcharger.energy.connectors.subprocess.run", return_value=completed):
                with self.assertRaisesRegex(ValueError, "did not return a JSON object"):
                    read_energy_source_snapshot(owner, source, 1.0)


if __name__ == "__main__":
    unittest.main()
