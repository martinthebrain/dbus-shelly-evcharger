# SPDX-License-Identifier: GPL-3.0-or-later
"""Core Modbus transport type definitions shared across transport helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


ModbusTransportKind = Literal["serial_rtu", "tcp", "udp"]
ModbusParity = Literal["N", "E", "O"]
SerialPortOwnerKind = Literal["none", "venus_serial_starter"]


@dataclass(frozen=True)
class ModbusTransportSettings:
    """Normalized Modbus transport settings independent from EVSE register schema."""

    transport_kind: ModbusTransportKind
    unit_id: int
    timeout_seconds: float
    host: str | None
    port: int | None
    device: str | None
    baudrate: int
    bytesize: int
    parity: ModbusParity
    stopbits: int
    serial_port_owner: SerialPortOwnerKind
    serial_port_owner_stop_command: str | None
    serial_port_owner_start_command: str | None
    serial_retry_count: int
    serial_retry_delay_seconds: float


@dataclass(frozen=True)
class ModbusRequest:
    """One Modbus request PDU plus unit information."""

    unit_id: int
    function_code: int
    payload: bytes


class ModbusTransport(Protocol):
    """Transport boundary that exchanges one Modbus request and returns the response PDU."""

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes: ...
