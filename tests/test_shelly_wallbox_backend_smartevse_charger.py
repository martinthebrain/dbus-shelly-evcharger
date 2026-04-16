# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from shelly_wallbox.backend.modbus_transport import ModbusRequest
from shelly_wallbox.backend.smartevse_charger import SmartEvseChargerBackend


class _FakeSmartEvseTransport:
    def __init__(self) -> None:
        self.requests: list[ModbusRequest] = []
        self.holding_registers: dict[int, int] = {
            0x0000: 2,
            0x0001: 0,
            0x0002: 16,
            0x0003: 0,
            0x0005: 1,
            0x0007: 32,
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
        if request.function_code == 0x06:
            address = int.from_bytes(request.payload[0:2], "big")
            value = int.from_bytes(request.payload[2:4], "big")
            self.holding_registers[address] = value
            return bytes((0x06,)) + request.payload
        raise AssertionError(f"Unexpected Modbus function code {request.function_code}")


class TestShellyWallboxBackendSmartEvseCharger(unittest.TestCase):
    @staticmethod
    def _service() -> SimpleNamespace:
        return SimpleNamespace(
            shelly_request_timeout_seconds=2.0,
        )

    @staticmethod
    def _write_config(directory: str, content: str) -> str:
        path = Path(directory) / "smartevse-charger.ini"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_read_charger_state_maps_smartevse_registers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )
            fake_transport = _FakeSmartEvseTransport()
            with patch(
                "shelly_wallbox.backend.smartevse_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SmartEvseChargerBackend(self._service(), config_path=config_path)

                state = backend.read_charger_state()

            self.assertTrue(state.enabled)
            self.assertEqual(state.current_amps, 16.0)
            self.assertEqual(state.phase_selection, "P1")
            self.assertIsNone(state.actual_current_amps)
            self.assertEqual(state.status_text, "charging")
            self.assertIsNone(state.fault_text)

    def test_smartevse_charger_uses_configured_fixed_phase_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n"
                "[Capabilities]\nSupportedPhaseSelections=P1_P2\n",
            )
            fake_transport = _FakeSmartEvseTransport()
            with patch(
                "shelly_wallbox.backend.smartevse_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SmartEvseChargerBackend(self._service(), config_path=config_path)
                state = backend.read_charger_state()
                backend.set_phase_selection("P1_P2")

            self.assertEqual(backend.settings.supported_phase_selections, ("P1_P2",))
            self.assertEqual(state.phase_selection, "P1_P2")

    def test_read_charger_state_handles_scaled_current_and_faults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )
            fake_transport = _FakeSmartEvseTransport()
            fake_transport.holding_registers[0x0000] = 1
            fake_transport.holding_registers[0x0001] = 0x0020
            fake_transport.holding_registers[0x0003] = 2
            fake_transport.holding_registers[0x0002] = 160
            with patch(
                "shelly_wallbox.backend.smartevse_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SmartEvseChargerBackend(self._service(), config_path=config_path)

                state = backend.read_charger_state()

            self.assertEqual(state.current_amps, 16.0)
            self.assertEqual(state.status_text, "waiting-solar")
            self.assertEqual(state.fault_text, "no-sun")

    def test_read_charger_state_maps_documented_load_balance_and_activation_states(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )
            fake_transport = _FakeSmartEvseTransport()
            fake_transport.holding_registers[0x0000] = 4
            with patch(
                "shelly_wallbox.backend.smartevse_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SmartEvseChargerBackend(self._service(), config_path=config_path)
                state = backend.read_charger_state()
                self.assertEqual(state.status_text, "connected-load-balance")

            fake_transport = _FakeSmartEvseTransport()
            fake_transport.holding_registers[0x0000] = 8
            with patch(
                "shelly_wallbox.backend.smartevse_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SmartEvseChargerBackend(self._service(), config_path=config_path)
                state = backend.read_charger_state()
                self.assertEqual(state.status_text, "activation-required")

    def test_smartevse_charger_writes_enable_and_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )
            fake_transport = _FakeSmartEvseTransport()
            with patch(
                "shelly_wallbox.backend.smartevse_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SmartEvseChargerBackend(self._service(), config_path=config_path)

                backend.set_enabled(False)
                backend.set_current(13.6)
                backend.set_enabled(True)

            self.assertEqual(fake_transport.holding_registers[0x0002], 14)
            self.assertEqual(fake_transport.holding_registers[0x0005], 1)

    def test_smartevse_charger_rejects_current_above_documented_max_current_register(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )
            fake_transport = _FakeSmartEvseTransport()
            fake_transport.holding_registers[0x0007] = 13
            with patch(
                "shelly_wallbox.backend.smartevse_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SmartEvseChargerBackend(self._service(), config_path=config_path)

                with self.assertRaisesRegex(ValueError, "maximum charging current 13 A"):
                    backend.set_current(16.0)

    def test_smartevse_charger_rejects_native_phase_switching(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )
            backend = SmartEvseChargerBackend(self._service(), config_path=config_path)

            with self.assertRaisesRegex(ValueError, "configured fixed phase selection: P1"):
                backend.set_phase_selection("P1_P2_P3")

    def test_smartevse_charger_rejects_multiple_supported_phase_selections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n",
            )

            with self.assertRaisesRegex(ValueError, "requires exactly one fixed"):
                SmartEvseChargerBackend(self._service(), config_path=config_path)
