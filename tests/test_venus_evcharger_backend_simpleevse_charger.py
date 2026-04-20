# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.backend.modbus_transport import ModbusRequest
from venus_evcharger.backend.simpleevse_charger import (
    SimpleEvseChargerBackend,
    _enabled,
    _evse_status_text,
    _fault_text,
    _rounded_current_setting,
    _status_text,
)


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
                "venus_evcharger.backend.simpleevse_charger.create_modbus_transport",
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

    def test_simpleevse_charger_uses_configured_fixed_phase_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.50\nPort=502\nUnitId=1\n"
                "[Capabilities]\nSupportedPhaseSelections=P1_P2_P3\n",
            )
            fake_transport = _FakeSimpleEvseTransport()
            with patch(
                "venus_evcharger.backend.simpleevse_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                backend = SimpleEvseChargerBackend(self._service(), config_path=config_path)
                state = backend.read_charger_state()
                backend.set_phase_selection("P1_P2_P3")

            self.assertEqual(backend.settings.supported_phase_selections, ("P1_P2_P3",))
            self.assertEqual(state.phase_selection, "P1_P2_P3")

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
                "venus_evcharger.backend.simpleevse_charger.create_modbus_transport",
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
                "venus_evcharger.backend.simpleevse_charger.create_modbus_transport",
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

            with self.assertRaisesRegex(ValueError, "configured fixed phase selection: P1"):
                backend.set_phase_selection("P1_P2_P3")

    def test_simpleevse_charger_rejects_multiple_supported_phase_selections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.50\nPort=502\nUnitId=1\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n",
            )

            with self.assertRaisesRegex(ValueError, "requires exactly one fixed"):
                SimpleEvseChargerBackend(self._service(), config_path=config_path)

    def test_simpleevse_helper_edges_cover_fault_status_and_current_validation(self) -> None:
        self.assertEqual(_evse_status_text(1), "idle")
        self.assertEqual(_fault_text(0, 0x0004), "vent-required-fail")
        self.assertEqual(_fault_text(0, 0x0008), "pilot-release-wait")
        self.assertEqual(_fault_text(5, 0), "vehicle-failure")
        self.assertFalse(_enabled(0x0001, 3))
        self.assertEqual(_status_text(99, 2, None), "ready")
        with self.assertRaisesRegex(ValueError, "Unsupported charger current"):
            _rounded_current_setting(81.0)
        with self.assertRaises(FileNotFoundError):
            SimpleEvseChargerBackend(self._service(), config_path="/definitely/missing.ini")

    def test_simpleevse_reuses_preseeded_transport_when_client_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.50\nPort=502\nUnitId=1\n",
            )
            backend = SimpleEvseChargerBackend(self._service(), config_path=config_path)
            backend._transport = _FakeSimpleEvseTransport()

            with patch("venus_evcharger.backend.simpleevse_charger.create_modbus_transport") as create_transport:
                client = backend._client()

            self.assertIs(client, backend._client_cache)
            create_transport.assert_not_called()
