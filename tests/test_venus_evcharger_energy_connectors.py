# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.energy import EnergySourceDefinition, EnergySourceSnapshot, read_energy_source_snapshot


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
            self.assertTrue(snapshot.online)
            self.assertEqual(snapshot.confidence, 0.8)
            self.assertEqual(snapshot.captured_at, 100.0)
            session.get.assert_called_once_with(url="http://hybrid.local/state", timeout=2.0)

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
                "[AcPowerRead]\nRegisterType=holding\nAddress=40\nDataType=uint16\n",
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
            self.assertEqual(snapshot.ac_power_w, 3200.0)
            self.assertTrue(snapshot.online)

    def test_read_energy_source_snapshot_dispatches_to_command_json_connector(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Command]\nArgs=python3 /tmp/fake-energy-helper.py --once\nTimeoutSeconds=1.5\n"
                "[Response]\nSocPath=data.soc\nBatteryPowerPath=data.battery_power_w\n"
                "AcPowerPath=data.ac_power_w\nOnlinePath=data.online\nConfidencePath=data.confidence\n",
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
                stdout='{"data":{"soc":57.0,"battery_power_w":1100.0,"ac_power_w":2500.0,"online":true,"confidence":0.6}}'
            )

            with patch("venus_evcharger.energy.connectors.subprocess.run", return_value=completed) as run_mock:
                snapshot = read_energy_source_snapshot(owner, source, 400.0)

            self.assertEqual(snapshot.service_name, "python3")
            self.assertEqual(snapshot.soc, 57.0)
            self.assertEqual(snapshot.usable_capacity_wh, 9000.0)
            self.assertEqual(snapshot.net_battery_power_w, 1100.0)
            self.assertEqual(snapshot.discharge_power_w, 1100.0)
            self.assertEqual(snapshot.ac_power_w, 2500.0)
            self.assertTrue(snapshot.online)
            self.assertEqual(snapshot.confidence, 0.6)
            run_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
