# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal Modbus transport layer for serial RTU, TCP, and UDP."""

from __future__ import annotations

import atexit
import configparser
import errno
import os
import select
import socket
import subprocess
import termios
import time
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


class ModbusTransportError(RuntimeError):
    """Base error for Modbus transport failures."""


class ModbusPortBusyError(ModbusTransportError):
    """Raised when the serial Modbus port cannot be accessed exclusively enough."""


class ModbusPortOwnershipError(ModbusTransportError):
    """Raised when Venus serial-starter ownership handoff fails."""


class ModbusTimeoutError(ModbusTransportError):
    """Raised when a Modbus request times out before a full response arrives."""


class ModbusResponseError(ModbusTransportError):
    """Raised when a Modbus response is malformed or otherwise unusable."""


class ModbusSlaveOfflineError(ModbusTimeoutError):
    """Raised when repeated Modbus timeouts indicate an offline slave."""


def modbus_transport_issue_reason(error: BaseException) -> str | None:
    """Return one normalized reason label for a transport-layer Modbus failure."""
    for error_type, reason in _MODBUS_TRANSPORT_ISSUE_REASONS:
        if isinstance(error, error_type):
            return reason
    return "error" if isinstance(error, (ModbusTransportError, OSError)) else None


_MODBUS_TRANSPORT_ISSUE_REASONS: tuple[tuple[type[BaseException] | tuple[type[BaseException], ...], str], ...] = (
    (ModbusPortBusyError, "busy"),
    (ModbusPortOwnershipError, "ownership"),
    (ModbusSlaveOfflineError, "offline"),
    ((ModbusTimeoutError, TimeoutError), "timeout"),
    (ModbusResponseError, "response"),
)


def _normalized_transport_kind(value: object) -> ModbusTransportKind:
    """Return one supported transport kind."""
    normalized = str(value).strip().lower()
    if normalized in {"serial", "rtu", "serial_rtu"}:
        return "serial_rtu"
    if normalized == "udp":
        return "udp"
    return "tcp"


def _normalized_unit_id(value: object) -> int:
    """Return one validated Modbus unit/slave identifier."""
    unit_id = int(str(value).strip() or "1")
    if unit_id < 0 or unit_id > 247:
        raise ValueError(f"Unsupported Modbus unit id '{value}'")
    return unit_id


def _normalized_timeout_seconds(value: object, default: float) -> float:
    """Return one validated timeout value."""
    try:
        timeout = float(str(value).strip())
    except (TypeError, ValueError):
        timeout = default
    if timeout <= 0.0:
        return float(default)
    return timeout


def _normalized_port(value: object, default: int) -> int:
    """Return one validated TCP/UDP port."""
    port = int(str(value).strip() or str(default))
    if port <= 0 or port > 65535:
        raise ValueError(f"Unsupported Modbus port '{value}'")
    return port


def _normalized_device(value: object) -> str:
    """Return one required serial device path."""
    device = str(value).strip()
    if not device:
        raise ValueError("Modbus serial_rtu transport requires Transport.Device")
    return device


def _normalized_baudrate(value: object) -> int:
    """Return one validated serial baudrate."""
    baudrate = int(str(value).strip() or "9600")
    if baudrate <= 0:
        raise ValueError(f"Unsupported Modbus baudrate '{value}'")
    return baudrate


def _normalized_bytesize(value: object) -> int:
    """Return one validated serial bytesize."""
    bytesize = int(str(value).strip() or "8")
    if bytesize not in {5, 6, 7, 8}:
        raise ValueError(f"Unsupported Modbus bytesize '{value}'")
    return bytesize


def _normalized_parity(value: object) -> ModbusParity:
    """Return one validated serial parity setting."""
    parity = str(value).strip().upper() or "N"
    if parity not in {"N", "E", "O"}:
        raise ValueError(f"Unsupported Modbus parity '{value}'")
    return parity  # type: ignore[return-value]


def _normalized_stopbits(value: object) -> int:
    """Return one validated serial stopbit count."""
    stopbits = int(str(value).strip() or "1")
    if stopbits not in {1, 2}:
        raise ValueError(f"Unsupported Modbus stopbits '{value}'")
    return stopbits


def _normalized_serial_port_owner(value: object) -> SerialPortOwnerKind:
    """Return one supported serial port-owner strategy."""
    normalized = str(value).strip().lower()
    if normalized in {"venus", "venus_serial_starter", "serial-starter", "victron"}:
        return "venus_serial_starter"
    return "none"


def _normalized_retry_count(value: object, default: int) -> int:
    """Return one validated non-negative retry counter."""
    try:
        retry_count = int(str(value).strip())
    except (TypeError, ValueError):
        retry_count = default
    return max(0, retry_count)


def _normalized_retry_delay_seconds(value: object, default: float) -> float:
    """Return one validated non-negative retry delay."""
    try:
        retry_delay_seconds = float(str(value).strip())
    except (TypeError, ValueError):
        retry_delay_seconds = default
    return max(0.0, retry_delay_seconds)


def load_modbus_transport_settings(
    parser: configparser.ConfigParser, service: object
) -> ModbusTransportSettings:
    """Return normalized Modbus transport settings from backend config."""
    adapter = parser["Adapter"] if parser.has_section("Adapter") else parser["DEFAULT"]
    transport = parser["Transport"] if parser.has_section("Transport") else parser["DEFAULT"]
    transport_kind = _normalized_transport_kind(
        adapter.get("Transport", transport.get("Type", "tcp"))
    )
    default_timeout_seconds = float(getattr(service, "shelly_request_timeout_seconds", 2.0) or 2.0)
    timeout_seconds = _normalized_timeout_seconds(
        transport.get("RequestTimeoutSeconds", str(default_timeout_seconds)),
        default_timeout_seconds,
    )
    unit_id = _normalized_unit_id(transport.get("UnitId", transport.get("SlaveId", "1")))
    host = str(transport.get("Host", "")).strip() or None
    (
        port,
        device,
        baudrate,
        bytesize,
        parity,
        stopbits,
        serial_port_owner,
        serial_port_owner_stop_command,
        serial_port_owner_start_command,
        serial_retry_count,
        serial_retry_delay_seconds,
    ) = _transport_runtime_fields(transport_kind, transport, host)
    return ModbusTransportSettings(
        transport_kind=transport_kind,
        unit_id=unit_id,
        timeout_seconds=timeout_seconds,
        host=host,
        port=port,
        device=device,
        baudrate=baudrate,
        bytesize=bytesize,
        parity=parity,
        stopbits=stopbits,
        serial_port_owner=serial_port_owner,
        serial_port_owner_stop_command=serial_port_owner_stop_command,
        serial_port_owner_start_command=serial_port_owner_start_command,
        serial_retry_count=serial_retry_count,
        serial_retry_delay_seconds=serial_retry_delay_seconds,
    )


def _transport_runtime_fields(
    transport_kind: ModbusTransportKind,
    transport: configparser.SectionProxy,
    host: str | None,
) -> tuple[int | None, str | None, int, int, ModbusParity, int, SerialPortOwnerKind, str | None, str | None, int, float]:
    """Return transport-specific runtime fields for one normalized transport kind."""
    port, device, baudrate, bytesize, parity, stopbits = _default_modbus_serial_fields()
    serial_port_owner, serial_port_owner_stop_command, serial_port_owner_start_command = _default_port_owner_fields()
    serial_retry_count = 0
    serial_retry_delay_seconds = 0.2
    if transport_kind == "serial_rtu":
        (
            device,
            baudrate,
            bytesize,
            parity,
            stopbits,
            serial_port_owner,
            serial_port_owner_stop_command,
            serial_port_owner_start_command,
            serial_retry_count,
            serial_retry_delay_seconds,
        ) = _serial_transport_runtime_fields(transport)
        return (
            port,
            device,
            baudrate,
            bytesize,
            parity,
            stopbits,
            serial_port_owner,
            serial_port_owner_stop_command,
            serial_port_owner_start_command,
            serial_retry_count,
            serial_retry_delay_seconds,
        )
    port = _required_host_port(transport_kind, transport, host)
    return (
        port,
        device,
        baudrate,
        bytesize,
        parity,
        stopbits,
        serial_port_owner,
        serial_port_owner_stop_command,
        serial_port_owner_start_command,
        serial_retry_count,
        serial_retry_delay_seconds,
    )


def _modbus_crc(frame: bytes) -> int:
    """Return one Modbus RTU CRC16."""
    crc = 0xFFFF
    for byte in frame:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def _crc_frame(frame: bytes) -> bytes:
    """Append Modbus RTU CRC16 to one frame."""
    crc = _modbus_crc(frame)
    return frame + bytes((crc & 0xFF, (crc >> 8) & 0xFF))


def _serial_baudrate_constant(baudrate: int) -> int:
    """Return the termios baudrate constant for one supported speed."""
    constant_name = f"B{baudrate}"
    if not hasattr(termios, constant_name):
        raise ValueError(f"Unsupported Modbus serial baudrate '{baudrate}'")
    return int(getattr(termios, constant_name))


def _configured_serial_attrs(fd: int, settings: ModbusTransportSettings) -> list[int | list[bytes | int]]:
    """Return one configured termios attr list for the serial Modbus port."""
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = termios.CREAD | termios.CLOCAL
    attrs[3] = 0
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    baud = _serial_baudrate_constant(settings.baudrate)
    attrs[4] = baud
    attrs[5] = baud
    attrs[2] &= ~termios.CSIZE
    attrs[2] |= {
        5: termios.CS5,
        6: termios.CS6,
        7: termios.CS7,
        8: termios.CS8,
    }[settings.bytesize]
    attrs[2] &= ~(termios.PARENB | termios.PARODD | termios.CSTOPB)
    if settings.parity == "E":
        attrs[2] |= termios.PARENB
    elif settings.parity == "O":
        attrs[2] |= termios.PARENB | termios.PARODD
    if settings.stopbits == 2:
        attrs[2] |= termios.CSTOPB
    return attrs


class _VenusSerialPortOwner:
    """Own one Venus serial-starter managed tty for the lifetime of this process."""

    def __init__(
        self,
        device: str,
        stop_command: str,
        start_command: str | None,
    ) -> None:
        self.device = device
        self.stop_command = stop_command
        self.start_command = start_command
        self._owned = False
        self._release_registered = False

    def ensure_owned(self) -> None:
        """Stop Venus serial-starter once so the current process can use the tty."""
        if self._owned:
            return
        self._run_command(self.stop_command)
        self._owned = True
        if self.start_command is not None and not self._release_registered:
            atexit.register(self.release)
            self._release_registered = True

    def recover(self) -> None:
        """Re-run the stop command after a failed serial exchange."""
        if self._owned:
            self._run_command(self.stop_command)

    def release(self) -> None:
        """Return the tty to Venus serial-starter when possible."""
        if not self._owned or self.start_command is None:
            return
        try:
            self._run_command(self.start_command)
        except ModbusPortOwnershipError:
            return
        self._owned = False

    def _run_command(self, command: str) -> None:
        """Run one stop/start helper with the current tty path."""
        try:
            result = self._command_result(command)
        except FileNotFoundError as error:
            raise ModbusPortOwnershipError(
                f"Venus serial ownership helper '{command}' is unavailable for {self.device}"
            ) from error
        if result.returncode == 0:
            return
        detail = self._command_detail(result)
        suffix = f": {detail}" if detail else ""
        raise ModbusPortOwnershipError(
            f"Venus serial ownership helper '{command}' failed for {self.device}{suffix}"
        )

    def _command_result(self, command: str) -> subprocess.CompletedProcess[str]:
        """Return the subprocess result for one tty ownership helper."""
        return subprocess.run(
            [command, self.device],
            check=False,
            capture_output=True,
            text=True,
        )

    @staticmethod
    def _command_detail(result: subprocess.CompletedProcess[str]) -> str:
        """Return the best available stderr/stdout detail for one helper result."""
        return (result.stderr or result.stdout or "").strip()


def _default_modbus_serial_fields() -> tuple[int | None, str | None, int, int, ModbusParity, int]:
    """Return default serial-field values before transport-specific normalization."""
    return None, None, 9600, 8, "N", 1


def _default_port_owner_fields() -> tuple[SerialPortOwnerKind, str | None, str | None]:
    """Return default serial-port ownership settings."""
    return "none", None, None


def _serial_transport_fields(
    transport: configparser.SectionProxy,
) -> tuple[str, int, int, ModbusParity, int]:
    """Return normalized serial transport fields from config."""
    return (
        _normalized_device(transport.get("Device", "")),
        _normalized_baudrate(transport.get("Baudrate", "9600")),
        _normalized_bytesize(transport.get("Bytesize", "8")),
        _normalized_parity(transport.get("Parity", "N")),
        _normalized_stopbits(transport.get("StopBits", "1")),
    )


def _port_owner_commands(transport: configparser.SectionProxy) -> tuple[str | None, str | None]:
    """Return normalized stop/start commands for serial port ownership."""
    return (
        _optional_transport_command(
            transport,
            "PortOwnerStopCommand",
            "/opt/victronenergy/serial-starter/stop-tty.sh",
        ),
        _optional_transport_command(
            transport,
            "PortOwnerStartCommand",
            "/opt/victronenergy/serial-starter/start-tty.sh",
        ),
    )


def _optional_transport_command(
    transport: configparser.SectionProxy,
    key: str,
    default: str,
) -> str | None:
    """Return one optional transport command string."""
    return str(transport.get(key, default)).strip() or None


def _serial_transport_runtime_fields(
    transport: configparser.SectionProxy,
) -> tuple[str, int, int, ModbusParity, int, SerialPortOwnerKind, str | None, str | None, int, float]:
    """Return normalized runtime fields for serial RTU transport settings."""
    device, baudrate, bytesize, parity, stopbits = _serial_transport_fields(transport)
    return (
        device,
        baudrate,
        bytesize,
        parity,
        stopbits,
        _normalized_serial_port_owner(transport.get("PortOwner", "none")),
        *_port_owner_commands(transport),
        _normalized_retry_count(transport.get("RetryCount", "1"), 1),
        _normalized_retry_delay_seconds(transport.get("RetryDelaySeconds", "0.2"), 0.2),
    )


def _required_host_port(
    transport_kind: ModbusTransportKind,
    transport: configparser.SectionProxy,
    host: str | None,
) -> int:
    """Return the validated TCP/UDP port and require a host when needed."""
    if not host:
        raise ValueError(f"Modbus {transport_kind} transport requires Transport.Host")
    return _normalized_port(transport.get("Port", "502"), 502)


def _serial_port_owner(settings: ModbusTransportSettings) -> _VenusSerialPortOwner | None:
    """Return one optional serial port-owner helper for the given settings."""
    if settings.transport_kind != "serial_rtu" or settings.serial_port_owner == "none":
        return None
    if settings.device is None:
        return None
    stop_command = settings.serial_port_owner_stop_command
    if stop_command is None:
        raise ValueError("Modbus serial port ownership requires Transport.PortOwnerStopCommand")
    return _VenusSerialPortOwner(
        settings.device,
        stop_command,
        settings.serial_port_owner_start_command,
    )


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    """Return exactly size bytes from one connected socket."""
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise TimeoutError("Modbus transport closed before full response was received")
        chunks.extend(chunk)
    return bytes(chunks)


def _expected_rtu_response_length(function_code: int, header: bytes) -> int:
    """Return the full RTU response length from the first header bytes."""
    if len(header) < 3:
        raise ValueError("Incomplete Modbus RTU header")
    response_function = header[1]
    if response_function & 0x80:
        return 5
    if function_code in {0x01, 0x02, 0x03, 0x04}:
        return 3 + int(header[2]) + 2
    if function_code in {0x05, 0x06, 0x0F, 0x10}:
        return 8
    raise ValueError(f"Unsupported Modbus RTU function code 0x{function_code:02x}")


class ModbusTcpTransport:
    """Simple one-shot Modbus TCP transport."""

    def __init__(self, settings: ModbusTransportSettings) -> None:
        self.settings = settings
        self._transaction_id = 0

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes:
        """Send one Modbus TCP request and return the response PDU."""
        self._transaction_id = (self._transaction_id + 1) & 0xFFFF
        pdu = bytes((request.function_code,)) + request.payload
        adu = (
            self._transaction_id.to_bytes(2, "big")
            + b"\x00\x00"
            + (len(pdu) + 1).to_bytes(2, "big")
            + bytes((request.unit_id,))
            + pdu
        )
        assert self.settings.host is not None
        assert self.settings.port is not None
        with socket.create_connection((self.settings.host, self.settings.port), timeout=timeout_seconds) as sock:
            sock.settimeout(timeout_seconds)
            sock.sendall(adu)
            header = _recv_exact(sock, 7)
            length = int.from_bytes(header[4:6], "big")
            body = _recv_exact(sock, max(0, length - 1))
        return body


class ModbusUdpTransport:
    """Simple one-shot Modbus UDP transport using MBAP framing."""

    def __init__(self, settings: ModbusTransportSettings) -> None:
        self.settings = settings
        self._transaction_id = 0

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes:
        """Send one Modbus UDP request and return the response PDU."""
        self._transaction_id = (self._transaction_id + 1) & 0xFFFF
        pdu = bytes((request.function_code,)) + request.payload
        adu = (
            self._transaction_id.to_bytes(2, "big")
            + b"\x00\x00"
            + (len(pdu) + 1).to_bytes(2, "big")
            + bytes((request.unit_id,))
            + pdu
        )
        assert self.settings.host is not None
        assert self.settings.port is not None
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout_seconds)
            sock.sendto(adu, (self.settings.host, self.settings.port))
            response, _ = sock.recvfrom(260)
        if len(response) < 7:
            raise TimeoutError("Incomplete Modbus UDP response")
        length = int.from_bytes(response[4:6], "big")
        return response[7 : 7 + max(0, length - 1)]


class ModbusSerialRtuTransport:
    """Simple one-shot Modbus RTU transport over a serial character device."""

    def __init__(self, settings: ModbusTransportSettings) -> None:
        self.settings = settings
        self._port_owner = _serial_port_owner(settings)

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes:
        """Send one Modbus RTU request and return the response PDU."""
        attempts = self._serial_attempt_count()
        last_error: ModbusTransportError | None = None
        for attempt_index in range(attempts):
            self._ensure_port_owned()
            exchange_result, last_error = self._exchange_attempt(request, timeout_seconds)
            if exchange_result is not None:
                return exchange_result
            if self._serial_retry_exhausted(attempt_index, attempts):
                break
            self._recover_after_failure(last_error)
        assert last_error is not None
        raise self._final_serial_exchange_error(request, last_error)

    def _exchange_attempt(
        self,
        request: ModbusRequest,
        timeout_seconds: float,
    ) -> tuple[bytes | None, ModbusTransportError]:
        """Return one exchange result or the normalized recoverable error."""
        try:
            return self._exchange_once(request, timeout_seconds), ModbusTransportError("unused")
        except (ModbusPortBusyError, ModbusPortOwnershipError):
            raise
        except (ModbusTimeoutError, ModbusResponseError) as error:
            return None, error
        except OSError as error:
            return None, self._normalized_serial_os_error(error)

    def _serial_attempt_count(self) -> int:
        """Return the number of serial exchange attempts including retries."""
        return max(1, self.settings.serial_retry_count + 1)

    @staticmethod
    def _serial_retry_exhausted(attempt_index: int, attempts: int) -> bool:
        """Return whether the current serial exchange attempt was the last one."""
        return attempt_index >= (attempts - 1)

    def _final_serial_exchange_error(
        self,
        request: ModbusRequest,
        last_error: ModbusTransportError,
    ) -> ModbusTransportError:
        """Return the final transport error after all serial retries are exhausted."""
        if isinstance(last_error, ModbusTimeoutError):
            return ModbusSlaveOfflineError(
                f"Modbus slave {request.unit_id} on {self.settings.device} did not respond"
            )
        return last_error

    def _exchange_once(self, request: ModbusRequest, timeout_seconds: float) -> bytes:
        """Perform one single Modbus RTU exchange without retry handling."""
        assert self.settings.device is not None
        frame = _crc_frame(bytes((request.unit_id, request.function_code)) + request.payload)
        fd = os.open(self.settings.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            termios.tcsetattr(fd, termios.TCSANOW, _configured_serial_attrs(fd, self.settings))
            termios.tcflush(fd, termios.TCIOFLUSH)
            self._write_all(fd, frame)
            header = self._read_exact(fd, 3, timeout_seconds)
            total_length = _expected_rtu_response_length(request.function_code, header)
            response = header + self._read_exact(fd, total_length - len(header), timeout_seconds)
        finally:
            os.close(fd)
        if len(response) < 5:
            raise ModbusTimeoutError("Incomplete Modbus RTU response")
        payload = response[:-2]
        expected_crc = _modbus_crc(payload)
        received_crc = response[-2] | (response[-1] << 8)
        if received_crc != expected_crc:
            raise ModbusResponseError("Invalid Modbus RTU CRC")
        return payload[1:]

    def _ensure_port_owned(self) -> None:
        """Claim the tty from Venus serial-starter when configured."""
        if self._port_owner is None:
            return
        self._port_owner.ensure_owned()

    def _recover_after_failure(self, error: ModbusTransportError) -> None:
        """Sleep briefly and refresh Venus tty ownership before one retry."""
        if self._port_owner is not None:
            self._port_owner.recover()
        if self.settings.serial_retry_delay_seconds > 0.0:
            time.sleep(self.settings.serial_retry_delay_seconds)

    @staticmethod
    def _write_all(fd: int, frame: bytes) -> None:
        """Write one full Modbus RTU frame to the serial file descriptor."""
        total_written = 0
        while total_written < len(frame):
            written = os.write(fd, frame[total_written:])
            if written <= 0:
                raise ModbusPortBusyError("Failed to write full Modbus RTU request")
            total_written += written

    @staticmethod
    def _normalized_serial_os_error(error: OSError) -> ModbusTransportError:
        """Return one stable Modbus transport error for serial OS-layer failures."""
        if error.errno in {errno.EBUSY, errno.EACCES, errno.EPERM}:
            return ModbusPortBusyError(str(error))
        return ModbusTransportError(str(error))

    @staticmethod
    def _read_exact(fd: int, size: int, timeout_seconds: float) -> bytes:
        """Read exactly size bytes from one serial file descriptor."""
        chunks = bytearray()
        deadline = time.monotonic() + timeout_seconds
        while len(chunks) < size:
            remaining = max(0.0, deadline - time.monotonic())
            readable, _, _ = select.select([fd], [], [], remaining)
            if not readable:
                raise ModbusTimeoutError("Timed out waiting for Modbus RTU response")
            chunk = os.read(fd, size - len(chunks))
            if not chunk:
                raise ModbusTimeoutError("Modbus RTU transport closed before full response was received")
            chunks.extend(chunk)
        return bytes(chunks)


def create_modbus_transport(settings: ModbusTransportSettings) -> ModbusTransport:
    """Create one concrete Modbus transport from normalized settings."""
    if settings.transport_kind == "serial_rtu":
        return ModbusSerialRtuTransport(settings)
    if settings.transport_kind == "udp":
        return ModbusUdpTransport(settings)
    return ModbusTcpTransport(settings)
