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


class _EnergyProbeBase(unittest.TestCase):
    @staticmethod
    def _write_config(directory: str, filename: str, content: str) -> str:
        path = Path(directory) / filename
        path.write_text(content, encoding="utf-8")
        return str(path)


__all__ = [name for name in globals() if not name.startswith("__")]
