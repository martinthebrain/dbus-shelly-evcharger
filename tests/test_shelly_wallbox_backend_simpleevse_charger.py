# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from shelly_wallbox.backend.modbus_transport import ModbusRequest
from shelly_wallbox.backend.simpleevse_charger import SimpleEvseChargerBackend


class _FakeSimpleEvseTransport:
    def __init__(self) -> None:
        self.requests: list[ModbusRequest] = []
        self.holding_registers: dict[int, int] = {
            1000: 16,
            1001: 13,
            1002: 3,
            1004: 0,
            1005: 18,
            1006: 2,
            1007: 1,
        }

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes:
        self.requests.append(request)
        if request.function_code == 0x03:
            address = int.from_bytes(request.payload[0:2], "big")
            count = int.from_bytes(request.payload[2:4], "big")
            payload = b"".join(
                int(self.holding_registers.get(address + index, 0)).to_bytes(2, "big")
                for index in range(count)
            )
            return bytes((0x03, len(payload))) + payload
        if request.function_code == 0x10:
            address = int.from_bytes(request.payload[0:2], "big")
            count = int.from_bytes(request.payload[2:4], "big")
            byte_count = request.payload[4]
            data = request.payload[5 : 5 + byte_count]
            for index in range(count):
                start = index * 2
                self.holding_registers[address + index] = int.from_bytes(data[start : start + 2], "big")
            return bytes((0x10,)) + request.payload[:4]
        raise AssertionError(f"Unexpected Modbus function code {request.function_code}")


class TestShellyWallboxBackendSimpleEvseCharger(unittest.TestCase):
    @staticmethod
    def _service() -> SimpleNamespace:
        return SimpleNamespace(
            shelly_request_timeout_seconds=2.0,
        )

    @staticmethod
    def _write_config(directory: str, content: str) -> str:
        path = Path(directory) / "simpleevse-charger.ini"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_read_charger_state_maps_simpleevse_registers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.50\nPort=502\nUnitId=1\n",
            )
            fake_transport = _FakeSimpleEvseTransport()
            with patch(
                "shelly_wallbox.backend.simpleevse_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SimpleEvseChargerBackend(self._service(), config_path=config_path)

                state = backend.read_charger_state()

            self.assertTrue(state.enabled)
            self.assertEqual(state.current_amps, 16.0)
            self.assertEqual(state.actual_current_amps, 13.0)
            self.assertEqual(state.phase_selection, "P1")
            self.assertEqual(state.status_text, "charging")
            self.assertIsNone(state.fault_text)

    def test_read_charger_state_maps_simpleevse_fault_bits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.50\nPort=502\nUnitId=1\n",
            )
            fake_transport = _FakeSimpleEvseTransport()
            fake_transport.holding_registers[1002] = 5
            fake_transport.holding_registers[1006] = 1
            fake_transport.holding_registers[1007] = 0x0012
            with patch(
                "shelly_wallbox.backend.simpleevse_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SimpleEvseChargerBackend(self._service(), config_path=config_path)

                state = backend.read_charger_state()

            self.assertEqual(state.status_text, "error")
            self.assertEqual(state.fault_text, "diode-check-fail,rcd-check-error")

    def test_simpleevse_charger_writes_enable_and_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.50\nPort=502\nUnitId=1\n",
            )
            fake_transport = _FakeSimpleEvseTransport()
            with patch(
                "shelly_wallbox.backend.simpleevse_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SimpleEvseChargerBackend(self._service(), config_path=config_path)

                backend.set_enabled(False)
                backend.set_current(13.6)
                backend.set_enabled(True)

            self.assertEqual(fake_transport.holding_registers[1000], 14)
            self.assertEqual(fake_transport.holding_registers[1004], 0)

    def test_simpleevse_charger_rejects_native_phase_switching(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.50\nPort=502\nUnitId=1\n",
            )
            backend = SimpleEvseChargerBackend(self._service(), config_path=config_path)

            with self.assertRaisesRegex(ValueError, "does not support native phase switching"):
                backend.set_phase_selection("P1_P2_P3")
