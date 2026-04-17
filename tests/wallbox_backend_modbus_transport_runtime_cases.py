# SPDX-License-Identifier: GPL-3.0-or-later
import errno
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from shelly_wallbox.backend.modbus_transport import (
    ModbusPortBusyError,
    ModbusPortOwnershipError,
    ModbusRequest,
    ModbusResponseError,
    ModbusSerialRtuTransport,
    ModbusSlaveOfflineError,
    ModbusTcpTransport,
    ModbusTimeoutError,
    ModbusTransportError,
    ModbusTransportSettings,
    ModbusUdpTransport,
    _VenusSerialPortOwner,
    _configured_serial_attrs,
    _serial_port_owner,
    _crc_frame,
    _expected_rtu_response_length,
    create_modbus_transport,
)


class TestShellyWallboxBackendModbusTransportRuntime(unittest.TestCase):
    def _serial_settings(self, retry_count=0, retry_delay=0.0, owner="none") -> ModbusTransportSettings:
        return ModbusTransportSettings(transport_kind="serial_rtu", unit_id=1, timeout_seconds=1.0, host=None, port=None, device="/dev/ttyS7", baudrate=9600, bytesize=8, parity="N", stopbits=1, serial_port_owner=owner, serial_port_owner_stop_command="/stop.sh" if owner != "none" else None, serial_port_owner_start_command="/start.sh" if owner != "none" else None, serial_retry_count=retry_count, serial_retry_delay_seconds=retry_delay)

    def test_serial_rtu_transport_retries_timeout_once(self) -> None:
        request = ModbusRequest(unit_id=1, function_code=0x03, payload=b"\x00\x00\x00\x01")
        transport = ModbusSerialRtuTransport(self._serial_settings(retry_count=1))
        with patch.object(transport, "_exchange_once", side_effect=[ModbusTimeoutError("timeout"), b"\x03\x02\x00\xa0"]) as exchange_mock:
            response = transport.exchange(request, timeout_seconds=1.0)
        self.assertEqual(response, b"\x03\x02\x00\xa0")
        self.assertEqual(exchange_mock.call_count, 2)

    def test_serial_rtu_transport_raises_slave_offline_after_retries(self) -> None:
        transport = ModbusSerialRtuTransport(self._serial_settings(retry_count=1))
        request = ModbusRequest(unit_id=1, function_code=0x03, payload=b"\x00\x00\x00\x01")
        with patch.object(transport, "_exchange_once", side_effect=ModbusTimeoutError("timeout")):
            with self.assertRaises(ModbusSlaveOfflineError):
                transport.exchange(request, timeout_seconds=1.0)

    def test_serial_rtu_transport_maps_busy_os_errors(self) -> None:
        transport = ModbusSerialRtuTransport(self._serial_settings())
        request = ModbusRequest(unit_id=1, function_code=0x03, payload=b"\x00\x00\x00\x01")
        with patch.object(transport, "_exchange_once", side_effect=OSError(errno.EBUSY, "busy")):
            with self.assertRaises(ModbusPortBusyError):
                transport.exchange(request, timeout_seconds=1.0)

    def test_tcp_and_udp_transports_exchange_with_mbap_framing(self) -> None:
        settings = ModbusTransportSettings(transport_kind="tcp", unit_id=1, timeout_seconds=1.0, host="127.0.0.1", port=502, device=None, baudrate=9600, bytesize=8, parity="N", stopbits=1, serial_port_owner="none", serial_port_owner_stop_command=None, serial_port_owner_start_command=None, serial_retry_count=0, serial_retry_delay_seconds=0.0)
        transport = ModbusTcpTransport(settings)
        fake_sock = MagicMock()
        fake_sock.__enter__.return_value = fake_sock
        fake_sock.__exit__.return_value = False
        with patch("shelly_wallbox.backend.modbus_transport.socket.create_connection", return_value=fake_sock), patch("shelly_wallbox.backend.modbus_transport._recv_exact", side_effect=[b"\x00\x01\x00\x00\x00\x05\x01", b"\x03\x02\x00\xa0"]):
            response = transport.exchange(ModbusRequest(1, 0x03, b"\x00\x00\x00\x01"), timeout_seconds=1.0)
        self.assertEqual(response, b"\x03\x02\x00\xa0")

        udp_settings = ModbusTransportSettings(transport_kind="udp", unit_id=1, timeout_seconds=1.0, host="127.0.0.1", port=502, device=None, baudrate=9600, bytesize=8, parity="N", stopbits=1, serial_port_owner="none", serial_port_owner_stop_command=None, serial_port_owner_start_command=None, serial_retry_count=0, serial_retry_delay_seconds=0.0)
        transport = ModbusUdpTransport(udp_settings)
        udp_sock = MagicMock()
        udp_sock.__enter__.return_value = udp_sock
        udp_sock.__exit__.return_value = False
        udp_sock.recvfrom.return_value = (b"\x00\x01\x00\x00\x00\x05\x01\x03\x02\x00\xa0", ("127.0.0.1", 502))
        with patch("shelly_wallbox.backend.modbus_transport.socket.socket", return_value=udp_sock):
            self.assertEqual(transport.exchange(ModbusRequest(1, 0x03, b"\x00\x00\x00\x01"), timeout_seconds=1.0), b"\x03\x02\x00\xa0")

    def test_serial_rtu_exchange_once_validates_crc_and_transport_helpers(self) -> None:
        transport = ModbusSerialRtuTransport(self._serial_settings())
        request = ModbusRequest(1, 0x03, b"\x00\x00\x00\x01")
        response_payload = b"\x01\x03\x02\x00\xa0"
        response_frame = _crc_frame(response_payload)
        with patch("shelly_wallbox.backend.modbus_transport.os.open", return_value=5), patch("shelly_wallbox.backend.modbus_transport.os.close"), patch("shelly_wallbox.backend.modbus_transport.termios.tcsetattr"), patch("shelly_wallbox.backend.modbus_transport.termios.tcflush"), patch("shelly_wallbox.backend.modbus_transport._configured_serial_attrs", return_value=[]), patch.object(transport, "_write_all"), patch.object(transport, "_read_exact", side_effect=[response_frame[:3], response_frame[3:]]):
            response = transport._exchange_once(request, 1.0)
        self.assertEqual(response, response_payload[1:])

    def test_serial_rtu_write_and_read_helpers_cover_partial_io_and_timeouts(self) -> None:
        with patch("shelly_wallbox.backend.modbus_transport.os.write", side_effect=[2, 2]):
            ModbusSerialRtuTransport._write_all(5, b"abcd")
        with patch("shelly_wallbox.backend.modbus_transport.os.write", return_value=0):
            with self.assertRaises(ModbusPortBusyError):
                ModbusSerialRtuTransport._write_all(5, b"ab")
        with patch("shelly_wallbox.backend.modbus_transport.select.select", return_value=([5], [], [])), patch("shelly_wallbox.backend.modbus_transport.os.read", side_effect=[b"\x01", b"\x02"]), patch("shelly_wallbox.backend.modbus_transport.time.monotonic", side_effect=[0.0, 0.0, 0.1]):
            self.assertEqual(ModbusSerialRtuTransport._read_exact(5, 2, 1.0), b"\x01\x02")

    def test_create_modbus_transport_returns_expected_transport_classes(self) -> None:
        base_kwargs = dict(unit_id=1, timeout_seconds=1.0, baudrate=9600, bytesize=8, parity="N", stopbits=1, serial_port_owner="none", serial_port_owner_stop_command=None, serial_port_owner_start_command=None, serial_retry_count=0, serial_retry_delay_seconds=0.0)
        self.assertIsInstance(create_modbus_transport(ModbusTransportSettings(transport_kind="serial_rtu", host=None, port=None, device="/dev/ttyS7", **base_kwargs)), ModbusSerialRtuTransport)
        self.assertIsInstance(create_modbus_transport(ModbusTransportSettings(transport_kind="udp", host="127.0.0.1", port=502, device=None, **base_kwargs)), ModbusUdpTransport)
        self.assertIsInstance(create_modbus_transport(ModbusTransportSettings(transport_kind="tcp", host="127.0.0.1", port=502, device=None, **base_kwargs)), ModbusTcpTransport)

    def test_serial_rtu_exchange_covers_remaining_error_and_recovery_paths(self) -> None:
        request = ModbusRequest(unit_id=1, function_code=0x03, payload=b"\x00\x00\x00\x01")
        transport = ModbusSerialRtuTransport(self._serial_settings(retry_count=1, retry_delay=0.1, owner="venus_serial_starter"))
        transport._port_owner = MagicMock()
        with patch.object(transport, "_exchange_once", side_effect=ModbusPortBusyError("busy")):
            with self.assertRaises(ModbusPortBusyError):
                transport.exchange(request, timeout_seconds=1.0)
        with patch.object(transport, "_exchange_once", side_effect=ModbusPortOwnershipError("ownership")):
            with self.assertRaises(ModbusPortOwnershipError):
                transport.exchange(request, timeout_seconds=1.0)
        with patch.object(transport, "_exchange_once", side_effect=ModbusResponseError("response")):
            with self.assertRaises(ModbusResponseError):
                transport.exchange(request, timeout_seconds=1.0)
        with patch.object(transport, "_exchange_once", side_effect=OSError(errno.EIO, "io")):
            with self.assertRaises(ModbusTransportError):
                transport.exchange(request, timeout_seconds=1.0)
        with patch("shelly_wallbox.backend.modbus_transport.time.sleep") as sleep_mock:
            transport._recover_after_failure(ModbusTimeoutError("timeout"))
        transport._port_owner.recover.assert_called()
        sleep_mock.assert_called_once_with(0.1)

    def test_modbus_transport_runtime_helpers_cover_remaining_error_paths(self) -> None:
        self.assertEqual(_expected_rtu_response_length(0x03, b"\x01\x83\x02"), 5)
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            _expected_rtu_response_length(0x03, b"\x01\x03")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            _expected_rtu_response_length(0x07, b"\x01\x07\x02")

        tcp_settings = ModbusTransportSettings(transport_kind="tcp", unit_id=1, timeout_seconds=1.0, host="127.0.0.1", port=502, device=None, baudrate=9600, bytesize=8, parity="N", stopbits=1, serial_port_owner="none", serial_port_owner_stop_command=None, serial_port_owner_start_command=None, serial_retry_count=0, serial_retry_delay_seconds=0.0)
        udp_settings = ModbusTransportSettings(transport_kind="udp", unit_id=1, timeout_seconds=1.0, host="127.0.0.1", port=502, device=None, baudrate=9600, bytesize=8, parity="N", stopbits=1, serial_port_owner="none", serial_port_owner_stop_command=None, serial_port_owner_start_command=None, serial_retry_count=0, serial_retry_delay_seconds=0.0)
        tcp = ModbusTcpTransport(tcp_settings)
        udp = ModbusUdpTransport(udp_settings)

        fake_sock = MagicMock()
        fake_sock.__enter__.return_value = fake_sock
        fake_sock.__exit__.return_value = False
        with patch("shelly_wallbox.backend.modbus_transport.socket.create_connection", return_value=fake_sock), patch("shelly_wallbox.backend.modbus_transport._recv_exact", side_effect=[b"\x00\x01\x00\x00\x00\x01\x01", b""]):
            self.assertEqual(tcp.exchange(ModbusRequest(1, 0x03, b"\x00\x00\x00\x01"), timeout_seconds=1.0), b"")

        udp_sock = MagicMock()
        udp_sock.__enter__.return_value = udp_sock
        udp_sock.__exit__.return_value = False
        udp_sock.recvfrom.return_value = (b"\x00\x01", ("127.0.0.1", 502))
        with patch("shelly_wallbox.backend.modbus_transport.socket.socket", return_value=udp_sock):
            with self.assertRaises(TimeoutError):
                udp.exchange(ModbusRequest(1, 0x03, b"\x00\x00\x00\x01"), timeout_seconds=1.0)

        transport = ModbusSerialRtuTransport(self._serial_settings())
        with patch("shelly_wallbox.backend.modbus_transport.os.open", return_value=5), patch("shelly_wallbox.backend.modbus_transport.os.close"), patch("shelly_wallbox.backend.modbus_transport.termios.tcsetattr"), patch("shelly_wallbox.backend.modbus_transport.termios.tcflush"), patch("shelly_wallbox.backend.modbus_transport._configured_serial_attrs", return_value=[]), patch.object(transport, "_write_all"), patch.object(transport, "_read_exact", side_effect=[b"\x01\x03\x02", b"\x00\xa0\x00\x00"]):
            with self.assertRaises(ModbusResponseError):
                transport._exchange_once(ModbusRequest(1, 0x03, b"\x00\x00\x00\x01"), 1.0)

        with patch("shelly_wallbox.backend.modbus_transport.select.select", return_value=([], [], [])), patch("shelly_wallbox.backend.modbus_transport.time.monotonic", side_effect=[0.0, 0.0]):
            with self.assertRaises(ModbusTimeoutError):
                ModbusSerialRtuTransport._read_exact(5, 1, 1.0)

        self.assertIsInstance(
            ModbusSerialRtuTransport._normalized_serial_os_error(OSError(errno.EPERM, "nope")),
            ModbusPortBusyError,
        )

    def test_modbus_transport_runtime_covers_parity_owner_and_short_reads(self) -> None:
        odd_settings = self._serial_settings()
        odd_settings = ModbusTransportSettings(**{**odd_settings.__dict__, "parity": "O", "stopbits": 2, "device": None})
        with patch("shelly_wallbox.backend.modbus_transport.termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0, [0, 0, 0, 0, 0, 0, 0]]):
            attrs = _configured_serial_attrs(5, odd_settings)
        self.assertTrue(attrs[2])

        even_settings = ModbusTransportSettings(**{**self._serial_settings().__dict__, "parity": "E", "stopbits": 1, "device": None})
        with patch("shelly_wallbox.backend.modbus_transport.termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0, [0, 0, 0, 0, 0, 0, 0]]):
            even_attrs = _configured_serial_attrs(5, even_settings)
        self.assertTrue(even_attrs[2])

        none_settings = ModbusTransportSettings(**{**self._serial_settings().__dict__, "parity": "N", "stopbits": 1, "device": None})
        with patch("shelly_wallbox.backend.modbus_transport.termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0, [0, 0, 0, 0, 0, 0, 0]]):
            none_attrs = _configured_serial_attrs(5, none_settings)
        self.assertTrue(isinstance(none_attrs, list))

        owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop.sh", None)
        owner._run_command = MagicMock()
        owner._owned = True
        owner.recover()
        owner._run_command.assert_called_once_with("/stop.sh")
        owner._owned = False
        owner.ensure_owned()
        self.assertTrue(owner._owned)

        owner_settings = ModbusTransportSettings(**{**self._serial_settings(owner="venus_serial_starter").__dict__, "device": None})
        self.assertIsNone(_serial_port_owner(owner_settings))

        transport = ModbusSerialRtuTransport(self._serial_settings())
        with patch("shelly_wallbox.backend.modbus_transport.os.open", return_value=5), patch("shelly_wallbox.backend.modbus_transport.os.close"), patch("shelly_wallbox.backend.modbus_transport.termios.tcsetattr"), patch("shelly_wallbox.backend.modbus_transport.termios.tcflush"), patch("shelly_wallbox.backend.modbus_transport._configured_serial_attrs", return_value=[]), patch.object(transport, "_write_all"), patch.object(transport, "_read_exact", side_effect=[b"\x01\x03\x02", b"\x00"]):
            with self.assertRaises(ModbusTimeoutError):
                transport._exchange_once(ModbusRequest(1, 0x03, b"\x00\x00\x00\x01"), 1.0)

        with patch("shelly_wallbox.backend.modbus_transport.select.select", return_value=([5], [], [])), patch("shelly_wallbox.backend.modbus_transport.os.read", return_value=b""), patch("shelly_wallbox.backend.modbus_transport.time.monotonic", side_effect=[0.0, 0.0]):
            with self.assertRaises(ModbusTimeoutError):
                ModbusSerialRtuTransport._read_exact(5, 1, 1.0)
