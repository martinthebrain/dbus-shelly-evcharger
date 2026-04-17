# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import errno
import termios
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from shelly_wallbox.backend.modbus_transport import (
    ModbusPortOwnershipError,
    ModbusRequest,
    ModbusSerialRtuTransport,
    ModbusTransportSettings,
    ModbusUdpTransport,
    ModbusTcpTransport,
    _VenusSerialPortOwner,
    _configured_serial_attrs,
    _crc_frame,
    _expected_rtu_response_length,
    _modbus_crc,
    _normalized_baudrate,
    _normalized_bytesize,
    _normalized_device,
    _normalized_parity,
    _normalized_port,
    _normalized_retry_count,
    _normalized_retry_delay_seconds,
    _normalized_serial_port_owner,
    _normalized_stopbits,
    _normalized_timeout_seconds,
    _normalized_transport_kind,
    _normalized_unit_id,
    _recv_exact,
    _serial_baudrate_constant,
    _serial_port_owner,
    create_modbus_transport,
    load_modbus_transport_settings,
    modbus_transport_issue_reason,
    ModbusPortBusyError,
    ModbusSlaveOfflineError,
    ModbusTimeoutError,
    ModbusResponseError,
    ModbusTransportError,
)


class TestShellyWallboxBackendModbusTransportConfig(unittest.TestCase):
    @staticmethod
    def _service() -> SimpleNamespace:
        return SimpleNamespace(shelly_request_timeout_seconds=2.0)

    def test_load_modbus_transport_settings_parses_venus_port_owner(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string("[Adapter]\nType=modbus_charger\nTransport=serial_rtu\n[Transport]\nDevice=/dev/ttyS7\nBaudrate=9600\nParity=N\nStopBits=1\nPortOwner=venus\nRetryCount=2\nRetryDelaySeconds=0.5\n")
        settings = load_modbus_transport_settings(parser, self._service())
        self.assertEqual(settings.transport_kind, "serial_rtu")
        self.assertEqual(settings.device, "/dev/ttyS7")
        self.assertEqual(settings.serial_port_owner, "venus_serial_starter")
        self.assertEqual(settings.serial_retry_count, 2)

    def test_venus_serial_port_owner_stops_once_and_releases(self) -> None:
        owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop-tty.sh", "/start-tty.sh")
        completed = SimpleNamespace(returncode=0, stderr="", stdout="")
        with patch("shelly_wallbox.backend.modbus_transport.subprocess.run", return_value=completed) as run_mock, patch("shelly_wallbox.backend.modbus_transport.atexit.register") as register_mock:
            owner.ensure_owned()
            owner.ensure_owned()
            owner.release()
        self.assertEqual(len(run_mock.call_args_list), 2)
        register_mock.assert_called_once_with(owner.release)

    def test_transport_issue_reason_maps_known_error_types(self) -> None:
        self.assertEqual(modbus_transport_issue_reason(ModbusPortBusyError("busy")), "busy")
        self.assertEqual(modbus_transport_issue_reason(ModbusPortOwnershipError("ownership")), "ownership")
        self.assertEqual(modbus_transport_issue_reason(ModbusSlaveOfflineError("offline")), "offline")
        self.assertEqual(modbus_transport_issue_reason(ModbusTimeoutError("timeout")), "timeout")
        self.assertEqual(modbus_transport_issue_reason(ModbusResponseError("response")), "response")
        self.assertEqual(modbus_transport_issue_reason(ModbusTransportError("transport")), "error")

    def test_normalization_helpers_validate_supported_values(self) -> None:
        self.assertEqual(_normalized_transport_kind("serial"), "serial_rtu")
        self.assertEqual(_normalized_serial_port_owner("something-else"), "none")
        self.assertEqual(_normalized_unit_id("247"), 247)
        self.assertEqual(_normalized_timeout_seconds("1.5", 2.0), 1.5)
        self.assertEqual(_normalized_port("502", 503), 502)
        self.assertEqual(_normalized_device("/dev/ttyS1"), "/dev/ttyS1")
        self.assertEqual(_normalized_baudrate("9600"), 9600)
        self.assertEqual(_normalized_bytesize("8"), 8)
        self.assertEqual(_normalized_parity("e"), "E")
        self.assertEqual(_normalized_stopbits("2"), 2)
        self.assertEqual(_normalized_retry_count("-1", 2), 0)
        self.assertEqual(_normalized_retry_delay_seconds("-1", 0.2), 0.0)

    def test_load_modbus_transport_settings_supports_udp_and_requires_host(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string("[Adapter]\nTransport=udp\n[Transport]\nHost=192.0.2.10\nPort=1502\nUnitId=7\nRequestTimeoutSeconds=3.5\n")
        settings = load_modbus_transport_settings(parser, self._service())
        self.assertEqual(settings.transport_kind, "udp")
        self.assertEqual(settings.host, "192.0.2.10")
        parser = configparser.ConfigParser()
        parser.read_string("[Adapter]\nTransport=tcp\n[Transport]\nPort=502\n")
        with self.assertRaises(ValueError):
            load_modbus_transport_settings(parser, self._service())

    def test_crc_helpers_and_expected_rtu_response_length_cover_supported_shapes(self) -> None:
        payload = b"\x01\x03\x00\x00\x00\x01"
        framed = _crc_frame(payload)
        self.assertEqual(_modbus_crc(payload), framed[-2] | (framed[-1] << 8))
        self.assertEqual(_expected_rtu_response_length(0x03, b"\x01\x03\x02"), 7)
        self.assertEqual(_expected_rtu_response_length(0x06, b"\x01\x06\x00"), 8)

    def test_serial_baudrate_and_configured_attrs_cover_parity_and_stopbits(self) -> None:
        self.assertIsInstance(_serial_baudrate_constant(9600), int)
        settings = ModbusTransportSettings(transport_kind="serial_rtu", unit_id=1, timeout_seconds=1.0, host=None, port=None, device="/dev/ttyS7", baudrate=9600, bytesize=7, parity="E", stopbits=2, serial_port_owner="none", serial_port_owner_stop_command=None, serial_port_owner_start_command=None, serial_retry_count=0, serial_retry_delay_seconds=0.0)
        cc = [0] * (max(termios.VMIN, termios.VTIME) + 1)
        with patch("shelly_wallbox.backend.modbus_transport.termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0, cc.copy()]):
            attrs = _configured_serial_attrs(3, settings)
        self.assertTrue(attrs[2] & termios.PARENB)
        self.assertTrue(attrs[2] & termios.CSTOPB)

    def test_venus_serial_port_owner_handles_failures_and_optional_release(self) -> None:
        owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop.sh", None)
        with patch("shelly_wallbox.backend.modbus_transport.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(ModbusPortOwnershipError):
                owner.ensure_owned()
        owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop.sh", None)
        with patch.object(owner, "_run_command") as run_command:
            owner.release()
            owner.recover()
        run_command.assert_not_called()

    def test_serial_port_owner_factory_validates_required_stop_command(self) -> None:
        tcp_settings = ModbusTransportSettings(transport_kind="tcp", unit_id=1, timeout_seconds=1.0, host="127.0.0.1", port=502, device=None, baudrate=9600, bytesize=8, parity="N", stopbits=1, serial_port_owner="none", serial_port_owner_stop_command=None, serial_port_owner_start_command=None, serial_retry_count=0, serial_retry_delay_seconds=0.0)
        self.assertIsNone(_serial_port_owner(tcp_settings))
        bad_settings = ModbusTransportSettings(transport_kind="serial_rtu", unit_id=1, timeout_seconds=1.0, host=None, port=None, device="/dev/ttyS7", baudrate=9600, bytesize=8, parity="N", stopbits=1, serial_port_owner="venus_serial_starter", serial_port_owner_stop_command=None, serial_port_owner_start_command=None, serial_retry_count=0, serial_retry_delay_seconds=0.0)
        with self.assertRaises(ValueError):
            _serial_port_owner(bad_settings)

    def test_recv_exact_collects_multiple_chunks_and_detects_disconnect(self) -> None:
        sock = unittest.mock.MagicMock()
        sock.recv.side_effect = [b"\x01", b"\x02\x03"]
        self.assertEqual(_recv_exact(sock, 3), b"\x01\x02\x03")
        sock = unittest.mock.MagicMock()
        sock.recv.side_effect = [b"\x01", b""]
        with self.assertRaises(TimeoutError):
            _recv_exact(sock, 2)

    def test_transport_normalizers_cover_invalid_edges_and_default_fallbacks(self) -> None:
        self.assertEqual(_normalized_timeout_seconds("bad", 2.5), 2.5)
        self.assertEqual(_normalized_timeout_seconds("0", 2.5), 2.5)
        self.assertEqual(_normalized_retry_count("bad", 3), 3)
        self.assertEqual(_normalized_retry_delay_seconds("bad", 0.4), 0.4)
        self.assertEqual(_normalized_transport_kind("udp"), "udp")
        self.assertEqual(_normalized_serial_port_owner("victron"), "venus_serial_starter")

        with self.assertRaises(ValueError):
            _normalized_unit_id("248")
        with self.assertRaises(ValueError):
            _normalized_port("70000", 502)
        with self.assertRaises(ValueError):
            _normalized_device("   ")
        with self.assertRaises(ValueError):
            _normalized_baudrate("0")
        with self.assertRaises(ValueError):
            _normalized_bytesize("9")
        with self.assertRaises(ValueError):
            _normalized_parity("X")
        with self.assertRaises(ValueError):
            _normalized_stopbits("3")

    def test_venus_serial_port_owner_release_and_baudrate_helper_cover_remaining_branches(self) -> None:
        owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop.sh", "/start.sh")
        owner._owned = True
        with patch.object(owner, "_run_command", side_effect=ModbusPortOwnershipError("boom")):
            owner.release()
        self.assertTrue(owner._owned)

        failing_owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop.sh", "/start.sh")
        failed = SimpleNamespace(returncode=1, stderr="bad", stdout="")
        with patch("shelly_wallbox.backend.modbus_transport.subprocess.run", return_value=failed):
            with self.assertRaisesRegex(ModbusPortOwnershipError, "bad"):
                failing_owner.ensure_owned()

        with self.assertRaises(ValueError):
            _serial_baudrate_constant(12345)
