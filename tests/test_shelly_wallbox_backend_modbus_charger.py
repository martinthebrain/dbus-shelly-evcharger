# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from shelly_wallbox.backend.modbus_charger import ModbusChargerBackend
from shelly_wallbox.backend.modbus_transport import ModbusRequest


class _FakeModbusTransport:
    def __init__(self) -> None:
        self.requests: list[ModbusRequest] = []
        self.coils: dict[int, bool] = {10: True}
        self.discrete_inputs: dict[int, bool] = {}
        self.holding_registers: dict[int, int] = {
            11: 160,
            30: 160,
        }
        self.input_registers: dict[int, int] = {
            12: 3,
            13: 128,
            14: 0,
            15: 2950,
            16: 0,
            17: 712,
            18: 1,
            19: 0,
        }

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes:
        self.requests.append(request)
        function_code = request.function_code
        if function_code in {0x01, 0x02}:
            address = int.from_bytes(request.payload[0:2], "big")
            count = int.from_bytes(request.payload[2:4], "big")
            source = self.coils if function_code == 0x01 else self.discrete_inputs
            values = [bool(source.get(address + index, False)) for index in range(count)]
            byte_value = 0
            for index, value in enumerate(values):
                if value:
                    byte_value |= 1 << index
            return bytes((function_code, 1, byte_value))
        if function_code in {0x03, 0x04}:
            address = int.from_bytes(request.payload[0:2], "big")
            count = int.from_bytes(request.payload[2:4], "big")
            source = self.holding_registers if function_code == 0x03 else self.input_registers
            registers = [int(source.get(address + index, 0)) for index in range(count)]
            payload = b"".join(register.to_bytes(2, "big") for register in registers)
            return bytes((function_code, len(payload))) + payload
        if function_code == 0x05:
            address = int.from_bytes(request.payload[0:2], "big")
            encoded = int.from_bytes(request.payload[2:4], "big")
            self.coils[address] = encoded == 0xFF00
            return bytes((function_code,)) + request.payload
        if function_code == 0x06:
            address = int.from_bytes(request.payload[0:2], "big")
            value = int.from_bytes(request.payload[2:4], "big")
            self.holding_registers[address] = value
            return bytes((function_code,)) + request.payload
        raise AssertionError(f"Unexpected Modbus function code {function_code}")


class TestShellyWallboxBackendModbusCharger(unittest.TestCase):
    @staticmethod
    def _service() -> SimpleNamespace:
        return SimpleNamespace(
            shelly_request_timeout_seconds=2.0,
        )

    @staticmethod
    def _write_config(directory: str, content: str) -> str:
        path = Path(directory) / "modbus-charger.ini"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_read_charger_state_maps_generic_modbus_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.40\nPort=502\nUnitId=7\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "[StateEnabled]\nRegisterType=coil\nAddress=10\n"
                "[StateCurrent]\nRegisterType=holding\nAddress=11\nDataType=uint16\nScale=10\n"
                "[StatePhase]\nRegisterType=input\nAddress=12\nDataType=uint16\nValueMap=1:P1,3:P1_P2_P3\n"
                "[StateActualCurrent]\nRegisterType=input\nAddress=13\nDataType=uint16\nScale=10\n"
                "[StatePower]\nRegisterType=input\nAddress=14\nDataType=uint32\n"
                "[StateEnergy]\nRegisterType=input\nAddress=16\nDataType=uint32\nScale=10\n"
                "[StateStatus]\nRegisterType=input\nAddress=18\nDataType=uint16\nValueMap=1:charging\n"
                "[StateFault]\nRegisterType=input\nAddress=19\nDataType=uint16\nValueMap=0:none\n"
                "[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n"
                "[PhaseWrite]\nRegisterType=holding\nAddress=31\nDataType=uint16\nMap=P1:1,P1_P2_P3:3\n",
            )
            fake_transport = _FakeModbusTransport()
            with patch(
                "shelly_wallbox.backend.modbus_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = ModbusChargerBackend(self._service(), config_path=config_path)

                state = backend.read_charger_state()

            self.assertTrue(state.enabled)
            self.assertEqual(state.current_amps, 16.0)
            self.assertEqual(state.phase_selection, "P1_P2_P3")
            self.assertEqual(state.actual_current_amps, 12.8)
            self.assertEqual(state.power_w, 2950.0)
            self.assertEqual(state.energy_kwh, 71.2)
            self.assertEqual(state.status_text, "charging")
            self.assertEqual(state.fault_text, "none")

    def test_modbus_charger_writes_enable_current_and_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.40\nPort=502\nUnitId=7\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n"
                "[PhaseWrite]\nRegisterType=holding\nAddress=31\nDataType=uint16\nMap=P1:1,P1_P2_P3:3\n",
            )
            fake_transport = _FakeModbusTransport()
            with patch(
                "shelly_wallbox.backend.modbus_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = ModbusChargerBackend(self._service(), config_path=config_path)

                backend.set_enabled(False)
                backend.set_current(13.5)
                backend.set_phase_selection("P1_P2_P3")

            self.assertEqual(fake_transport.coils[20], False)
            self.assertEqual(fake_transport.holding_registers[30], 135)
            self.assertEqual(fake_transport.holding_registers[31], 3)

    def test_multi_phase_modbus_profile_requires_phase_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.40\nPort=502\nUnitId=7\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n",
            )

            with self.assertRaises(ValueError):
                ModbusChargerBackend(self._service(), config_path=config_path)
