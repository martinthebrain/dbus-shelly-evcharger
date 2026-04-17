# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

from shelly_wallbox.backend.modbus_transport import ModbusRequest
from shelly_wallbox.backend.probe import (
    main,
    probe_charger_backend,
    probe_meter_backend,
    probe_switch_backend,
    read_charger_backend,
    validate_backend_config,
    validate_wallbox_config,
)


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _FakeSimpleEvseTransport:
    def __init__(self) -> None:
        self.holding_registers: dict[int, int] = {
            1000: 16,
            1001: 13,
            1002: 3,
            1004: 0,
            1006: 2,
            1007: 1,
        }

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes:
        if request.function_code == 0x03:
            address = int.from_bytes(request.payload[0:2], "big")
            count = int.from_bytes(request.payload[2:4], "big")
            payload = b"".join(
                int(self.holding_registers.get(address + index, 0)).to_bytes(2, "big")
                for index in range(count)
            )
            return bytes((0x03, len(payload))) + payload
        raise AssertionError(f"Unexpected Modbus function code {request.function_code}")


class _FakeSmartEvseTransport:
    def __init__(self) -> None:
        self.holding_registers: dict[int, int] = {
            0x0000: 2,
            0x0001: 0,
            0x0002: 16,
            0x0005: 1,
            0x0007: 32,
        }

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes:
        if request.function_code == 0x03:
            address = int.from_bytes(request.payload[0:2], "big")
            count = int.from_bytes(request.payload[2:4], "big")
            payload = b"".join(
                int(self.holding_registers.get(address + index, 0)).to_bytes(2, "big")
                for index in range(count)
            )
            return bytes((0x03, len(payload))) + payload
        raise AssertionError(f"Unexpected Modbus function code {request.function_code}")


class BackendProbeTestCase(unittest.TestCase):
    @staticmethod
    def _write_config(directory: str, filename: str, content: str) -> str:
        path = Path(directory) / filename
        path.write_text(content, encoding="utf-8")
        return str(path)


__all__ = [
    "Any",
    "BackendProbeTestCase",
    "MagicMock",
    "Path",
    "_FakeResponse",
    "_FakeSimpleEvseTransport",
    "_FakeSmartEvseTransport",
    "cast",
    "io",
    "json",
    "main",
    "patch",
    "probe_charger_backend",
    "probe_meter_backend",
    "probe_switch_backend",
    "read_charger_backend",
    "redirect_stdout",
    "tempfile",
    "validate_backend_config",
    "validate_wallbox_config",
]
