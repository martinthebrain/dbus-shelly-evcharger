# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import errno
import termios
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from shelly_wallbox.backend.modbus_transport import (
    ModbusPortBusyError,
    ModbusPortOwnershipError,
    ModbusRequest,
    ModbusResponseError,
    ModbusSerialRtuTransport,
    ModbusSlaveOfflineError,
    ModbusTcpTransport,
    ModbusTimeoutError,
    ModbusTransportSettings,
    ModbusTransportError,
    ModbusUdpTransport,
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
)


class TestShellyWallboxBackendModbusTransport(unittest.TestCase):
    @staticmethod
    def _service() -> SimpleNamespace:
        return SimpleNamespace(shelly_request_timeout_seconds=2.0)

    def test_load_modbus_transport_settings_parses_venus_port_owner(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            "[Adapter]\nType=modbus_charger\nTransport=serial_rtu\n"
            "[Transport]\nDevice=/dev/ttyS7\nBaudrate=9600\nParity=N\nStopBits=1\n"
            "PortOwner=venus\nRetryCount=2\nRetryDelaySeconds=0.5\n",
        )

        settings = load_modbus_transport_settings(parser, self._service())

        self.assertEqual(settings.transport_kind, "serial_rtu")
        self.assertEqual(settings.device, "/dev/ttyS7")
        self.assertEqual(settings.serial_port_owner, "venus_serial_starter")
        self.assertEqual(
            settings.serial_port_owner_stop_command,
            "/opt/victronenergy/serial-starter/stop-tty.sh",
        )
        self.assertEqual(
            settings.serial_port_owner_start_command,
            "/opt/victronenergy/serial-starter/start-tty.sh",
        )
        self.assertEqual(settings.serial_retry_count, 2)
        self.assertEqual(settings.serial_retry_delay_seconds, 0.5)

    def test_venus_serial_port_owner_stops_once_and_releases(self) -> None:
        owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop-tty.sh", "/start-tty.sh")
        completed = SimpleNamespace(returncode=0, stderr="", stdout="")

        with (
            patch("shelly_wallbox.backend.modbus_transport.subprocess.run", return_value=completed) as run_mock,
            patch("shelly_wallbox.backend.modbus_transport.atexit.register") as register_mock,
        ):
            owner.ensure_owned()
            owner.ensure_owned()
            owner.release()

        self.assertEqual(
            run_mock.call_args_list,
            [
                call(
                    ["/stop-tty.sh", "/dev/ttyS7"],
                    check=False,
                    capture_output=True,
                    text=True,
                ),
                call(
                    ["/start-tty.sh", "/dev/ttyS7"],
                    check=False,
                    capture_output=True,
                    text=True,
                ),
            ],
        )
        register_mock.assert_called_once_with(owner.release)

    def test_serial_rtu_transport_retries_timeout_once(self) -> None:
        settings = ModbusTransportSettings(
            transport_kind="serial_rtu",
            unit_id=1,
            timeout_seconds=2.0,
            host=None,
            port=None,
            device="/dev/ttyS7",
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            serial_port_owner="none",
            serial_port_owner_stop_command=None,
            serial_port_owner_start_command=None,
            serial_retry_count=1,
            serial_retry_delay_seconds=0.0,
        )
        request = ModbusRequest(unit_id=1, function_code=0x03, payload=b"\x00\x00\x00\x01")
        transport = ModbusSerialRtuTransport(settings)

        with patch.object(
            transport,
            "_exchange_once",
            side_effect=[ModbusTimeoutError("timeout"), b"\x03\x02\x00\xa0"],
        ) as exchange_mock:
            response = transport.exchange(request, timeout_seconds=1.0)

        self.assertEqual(response, b"\x03\x02\x00\xa0")
        self.assertEqual(exchange_mock.call_count, 2)

    def test_serial_rtu_transport_raises_slave_offline_after_retries(self) -> None:
        settings = ModbusTransportSettings(
            transport_kind="serial_rtu",
            unit_id=1,
            timeout_seconds=2.0,
            host=None,
            port=None,
            device="/dev/ttyS7",
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            serial_port_owner="none",
            serial_port_owner_stop_command=None,
            serial_port_owner_start_command=None,
            serial_retry_count=1,
            serial_retry_delay_seconds=0.0,
        )
        request = ModbusRequest(unit_id=1, function_code=0x03, payload=b"\x00\x00\x00\x01")
        transport = ModbusSerialRtuTransport(settings)

        with patch.object(
            transport,
            "_exchange_once",
            side_effect=ModbusTimeoutError("timeout"),
        ):
            with self.assertRaises(ModbusSlaveOfflineError):
                transport.exchange(request, timeout_seconds=1.0)

    def test_serial_rtu_transport_maps_busy_os_errors(self) -> None:
        settings = ModbusTransportSettings(
            transport_kind="serial_rtu",
            unit_id=1,
            timeout_seconds=2.0,
            host=None,
            port=None,
            device="/dev/ttyS7",
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            serial_port_owner="none",
            serial_port_owner_stop_command=None,
            serial_port_owner_start_command=None,
            serial_retry_count=0,
            serial_retry_delay_seconds=0.0,
        )
        request = ModbusRequest(unit_id=1, function_code=0x03, payload=b"\x00\x00\x00\x01")
        transport = ModbusSerialRtuTransport(settings)

        with patch.object(
            transport,
            "_exchange_once",
            side_effect=OSError(errno.EBUSY, "busy"),
        ):
            with self.assertRaises(ModbusPortBusyError):
                transport.exchange(request, timeout_seconds=1.0)

    def test_transport_issue_reason_maps_known_error_types(self) -> None:
        self.assertEqual(modbus_transport_issue_reason(ModbusPortBusyError("busy")), "busy")
        self.assertEqual(modbus_transport_issue_reason(ModbusPortOwnershipError("ownership")), "ownership")
        self.assertEqual(modbus_transport_issue_reason(ModbusSlaveOfflineError("offline")), "offline")
        self.assertEqual(modbus_transport_issue_reason(ModbusTimeoutError("timeout")), "timeout")
        self.assertEqual(modbus_transport_issue_reason(TimeoutError("timeout")), "timeout")
        self.assertEqual(modbus_transport_issue_reason(ModbusResponseError("response")), "response")
        self.assertEqual(modbus_transport_issue_reason(ModbusTransportError("transport")), "error")
        self.assertEqual(modbus_transport_issue_reason(OSError("os")), "error")
        self.assertIsNone(modbus_transport_issue_reason(RuntimeError("other")))

    def test_normalization_helpers_validate_supported_values(self) -> None:
        self.assertEqual(_normalized_transport_kind("serial"), "serial_rtu")
        self.assertEqual(_normalized_transport_kind("udp"), "udp")
        self.assertEqual(_normalized_transport_kind("tcp"), "tcp")
        self.assertEqual(_normalized_serial_port_owner("something-else"), "none")
        self.assertEqual(_normalized_unit_id("247"), 247)
        self.assertEqual(_normalized_timeout_seconds("1.5", 2.0), 1.5)
        self.assertEqual(_normalized_timeout_seconds("0", 2.0), 2.0)
        self.assertEqual(_normalized_timeout_seconds(object(), 2.0), 2.0)
        self.assertEqual(_normalized_port("502", 503), 502)
        self.assertEqual(_normalized_device("/dev/ttyS1"), "/dev/ttyS1")
        self.assertEqual(_normalized_baudrate("9600"), 9600)
        self.assertEqual(_normalized_bytesize("8"), 8)
        self.assertEqual(_normalized_parity("e"), "E")
        self.assertEqual(_normalized_stopbits("2"), 2)
        self.assertEqual(_normalized_serial_port_owner("victron"), "venus_serial_starter")
        self.assertEqual(_normalized_retry_count(object(), 2), 2)
        self.assertEqual(_normalized_retry_count("-1", 2), 0)
        self.assertEqual(_normalized_retry_delay_seconds(object(), 0.2), 0.2)
        self.assertEqual(_normalized_retry_delay_seconds("-1", 0.2), 0.0)

        with self.assertRaises(ValueError):
            _normalized_unit_id("248")
        with self.assertRaises(ValueError):
            _normalized_port("70000", 502)
        with self.assertRaises(ValueError):
            _normalized_device("")
        with self.assertRaises(ValueError):
            _normalized_baudrate("0")
        with self.assertRaises(ValueError):
            _normalized_bytesize("9")
        with self.assertRaises(ValueError):
            _normalized_parity("X")
        with self.assertRaises(ValueError):
            _normalized_stopbits("3")

    def test_load_modbus_transport_settings_supports_udp_and_requires_host(self) -> None:
        parser = configparser.ConfigParser()
        parser.read_string(
            "[Adapter]\nTransport=udp\n"
            "[Transport]\nHost=192.0.2.10\nPort=1502\nUnitId=7\nRequestTimeoutSeconds=3.5\n",
        )

        settings = load_modbus_transport_settings(parser, self._service())

        self.assertEqual(settings.transport_kind, "udp")
        self.assertEqual(settings.host, "192.0.2.10")
        self.assertEqual(settings.port, 1502)
        self.assertEqual(settings.unit_id, 7)
        self.assertEqual(settings.timeout_seconds, 3.5)

        parser = configparser.ConfigParser()
        parser.read_string("[Adapter]\nTransport=tcp\n[Transport]\nPort=502\n")
        with self.assertRaises(ValueError):
            load_modbus_transport_settings(parser, self._service())

    def test_crc_helpers_and_expected_rtu_response_length_cover_supported_shapes(self) -> None:
        payload = b"\x01\x03\x00\x00\x00\x01"
        framed = _crc_frame(payload)
        self.assertEqual(len(framed), len(payload) + 2)
        self.assertEqual(_modbus_crc(payload), framed[-2] | (framed[-1] << 8))

        self.assertEqual(_expected_rtu_response_length(0x03, b"\x01\x03\x02"), 7)
        self.assertEqual(_expected_rtu_response_length(0x06, b"\x01\x06\x00"), 8)
        self.assertEqual(_expected_rtu_response_length(0x03, b"\x01\x83\x02"), 5)
        with self.assertRaises(ValueError):
            _expected_rtu_response_length(0x03, b"\x01\x03")
        with self.assertRaises(ValueError):
            _expected_rtu_response_length(0x11, b"\x01\x11\x00")

    def test_serial_baudrate_and_configured_attrs_cover_parity_and_stopbits(self) -> None:
        self.assertIsInstance(_serial_baudrate_constant(9600), int)
        with self.assertRaises(ValueError):
            _serial_baudrate_constant(12345)

        settings = ModbusTransportSettings(
            transport_kind="serial_rtu",
            unit_id=1,
            timeout_seconds=1.0,
            host=None,
            port=None,
            device="/dev/ttyS7",
            baudrate=9600,
            bytesize=7,
            parity="E",
            stopbits=2,
            serial_port_owner="none",
            serial_port_owner_stop_command=None,
            serial_port_owner_start_command=None,
            serial_retry_count=0,
            serial_retry_delay_seconds=0.0,
        )
        cc = [0] * (max(termios.VMIN, termios.VTIME) + 1)
        with patch("shelly_wallbox.backend.modbus_transport.termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0, cc.copy()]):
            attrs = _configured_serial_attrs(3, settings)

        self.assertEqual(attrs[0], 0)
        self.assertEqual(attrs[1], 0)
        self.assertEqual(attrs[3], 0)
        self.assertEqual(attrs[6][termios.VMIN], 0)
        self.assertEqual(attrs[6][termios.VTIME], 0)
        self.assertTrue(attrs[2] & termios.PARENB)
        self.assertFalse(attrs[2] & termios.PARODD)
        self.assertTrue(attrs[2] & termios.CSTOPB)

        odd_settings = ModbusTransportSettings(
            transport_kind="serial_rtu",
            unit_id=1,
            timeout_seconds=1.0,
            host=None,
            port=None,
            device="/dev/ttyS7",
            baudrate=9600,
            bytesize=8,
            parity="O",
            stopbits=1,
            serial_port_owner="none",
            serial_port_owner_stop_command=None,
            serial_port_owner_start_command=None,
            serial_retry_count=0,
            serial_retry_delay_seconds=0.0,
        )
        with patch("shelly_wallbox.backend.modbus_transport.termios.tcgetattr", return_value=[0, 0, 0, 0, 0, 0, cc.copy()]):
            attrs = _configured_serial_attrs(3, odd_settings)
        self.assertTrue(attrs[2] & termios.PARENB)
        self.assertTrue(attrs[2] & termios.PARODD)

    def test_venus_serial_port_owner_handles_failures_and_optional_release(self) -> None:
        owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop.sh", None)

        with patch("shelly_wallbox.backend.modbus_transport.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(ModbusPortOwnershipError):
                owner.ensure_owned()

        owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop.sh", "/start.sh")
        failed = SimpleNamespace(returncode=1, stderr="bad", stdout="")
        with patch("shelly_wallbox.backend.modbus_transport.subprocess.run", return_value=failed):
            with self.assertRaises(ModbusPortOwnershipError):
                owner.ensure_owned()

        owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop.sh", None)
        with patch.object(owner, "_run_command") as run_command:
            owner.release()
            owner.recover()
        run_command.assert_not_called()

    def test_venus_serial_port_owner_recover_and_release_cover_owned_branches(self) -> None:
        owner = _VenusSerialPortOwner("/dev/ttyS7", "/stop.sh", "/start.sh")
        owner._owned = True

        with patch.object(owner, "_run_command") as run_command:
            owner.recover()
        run_command.assert_called_once_with("/stop.sh")

        with patch.object(owner, "_run_command", side_effect=ModbusPortOwnershipError("boom")):
            owner.release()
        self.assertTrue(owner._owned)

    def test_serial_port_owner_factory_validates_required_stop_command(self) -> None:
        tcp_settings = ModbusTransportSettings(
            transport_kind="tcp",
            unit_id=1,
            timeout_seconds=1.0,
            host="127.0.0.1",
            port=502,
            device=None,
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            serial_port_owner="none",
            serial_port_owner_stop_command=None,
            serial_port_owner_start_command=None,
            serial_retry_count=0,
            serial_retry_delay_seconds=0.0,
        )
        self.assertIsNone(_serial_port_owner(tcp_settings))

        bad_settings = ModbusTransportSettings(
            transport_kind="serial_rtu",
            unit_id=1,
            timeout_seconds=1.0,
            host=None,
            port=None,
            device="/dev/ttyS7",
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            serial_port_owner="venus_serial_starter",
            serial_port_owner_stop_command=None,
            serial_port_owner_start_command=None,
            serial_retry_count=0,
            serial_retry_delay_seconds=0.0,
        )
        with self.assertRaises(ValueError):
            _serial_port_owner(bad_settings)

        no_device_settings = ModbusTransportSettings(
            transport_kind="serial_rtu",
            unit_id=1,
            timeout_seconds=1.0,
            host=None,
            port=None,
            device=None,
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            serial_port_owner="venus_serial_starter",
            serial_port_owner_stop_command="/stop.sh",
            serial_port_owner_start_command=None,
            serial_retry_count=0,
            serial_retry_delay_seconds=0.0,
        )
        self.assertIsNone(_serial_port_owner(no_device_settings))
        self.assertIsInstance(_serial_port_owner(bad_settings.replace(serial_port_owner_stop_command="/stop.sh") if False else ModbusTransportSettings(
            transport_kind="serial_rtu",
            unit_id=1,
            timeout_seconds=1.0,
            host=None,
            port=None,
            device="/dev/ttyS7",
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            serial_port_owner="venus_serial_starter",
            serial_port_owner_stop_command="/stop.sh",
            serial_port_owner_start_command="/start.sh",
            serial_retry_count=0,
            serial_retry_delay_seconds=0.0,
        )), _VenusSerialPortOwner)

    def test_recv_exact_collects_multiple_chunks_and_detects_disconnect(self) -> None:
        sock = MagicMock()
        sock.recv.side_effect = [b"\x01", b"\x02\x03"]
        self.assertEqual(_recv_exact(sock, 3), b"\x01\x02\x03")

        sock = MagicMock()
        sock.recv.side_effect = [b"\x01", b""]
        with self.assertRaises(TimeoutError):
            _recv_exact(sock, 2)

    def test_tcp_and_udp_transports_exchange_with_mbap_framing(self) -> None:
        settings = ModbusTransportSettings(
            transport_kind="tcp",
            unit_id=1,
            timeout_seconds=1.0,
            host="127.0.0.1",
            port=502,
            device=None,
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            serial_port_owner="none",
            serial_port_owner_stop_command=None,
            serial_port_owner_start_command=None,
            serial_retry_count=0,
            serial_retry_delay_seconds=0.0,
        )
        transport = ModbusTcpTransport(settings)
        fake_sock = MagicMock()
        fake_sock.__enter__.return_value = fake_sock
        fake_sock.__exit__.return_value = False
        with (
            patch("shelly_wallbox.backend.modbus_transport.socket.create_connection", return_value=fake_sock),
            patch(
                "shelly_wallbox.backend.modbus_transport._recv_exact",
                side_effect=[b"\x00\x01\x00\x00\x00\x05\x01", b"\x03\x02\x00\xa0"],
            ),
        ):
            response = transport.exchange(ModbusRequest(1, 0x03, b"\x00\x00\x00\x01"), timeout_seconds=1.0)
        self.assertEqual(response, b"\x03\x02\x00\xa0")
        fake_sock.sendall.assert_called_once()

        udp_settings = ModbusTransportSettings(
            transport_kind="udp",
            unit_id=1,
            timeout_seconds=1.0,
            host="127.0.0.1",
            port=502,
            device=None,
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            serial_port_owner="none",
            serial_port_owner_stop_command=None,
            serial_port_owner_start_command=None,
            serial_retry_count=0,
            serial_retry_delay_seconds=0.0,
        )
        transport = ModbusUdpTransport(udp_settings)
        udp_sock = MagicMock()
        udp_sock.__enter__.return_value = udp_sock
        udp_sock.__exit__.return_value = False
        udp_sock.recvfrom.return_value = (b"\x00\x01\x00\x00\x00\x05\x01\x03\x02\x00\xa0", ("127.0.0.1", 502))
        with patch("shelly_wallbox.backend.modbus_transport.socket.socket", return_value=udp_sock):
            response = transport.exchange(ModbusRequest(1, 0x03, b"\x00\x00\x00\x01"), timeout_seconds=1.0)
        self.assertEqual(response, b"\x03\x02\x00\xa0")

        udp_sock.recvfrom.return_value = (b"\x00\x01", ("127.0.0.1", 502))
        with patch("shelly_wallbox.backend.modbus_transport.socket.socket", return_value=udp_sock):
            with self.assertRaises(TimeoutError):
                transport.exchange(ModbusRequest(1, 0x03, b"\x00\x00\x00\x01"), timeout_seconds=1.0)

    def test_serial_rtu_exchange_once_validates_crc_and_transport_helpers(self) -> None:
        settings = ModbusTransportSettings(
            transport_kind="serial_rtu",
            unit_id=1,
            timeout_seconds=1.0,
            host=None,
            port=None,
            device="/dev/ttyS7",
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            serial_port_owner="none",
            serial_port_owner_stop_command=None,
            serial_port_owner_start_command=None,
            serial_retry_count=0,
            serial_retry_delay_seconds=0.0,
        )
        transport = ModbusSerialRtuTransport(settings)
        request = ModbusRequest(1, 0x03, b"\x00\x00\x00\x01")
        response_payload = b"\x01\x03\x02\x00\xa0"
        response_frame = _crc_frame(response_payload)

        with (
            patch("shelly_wallbox.backend.modbus_transport.os.open", return_value=5),
            patch("shelly_wallbox.backend.modbus_transport.os.close") as close_mock,
            patch("shelly_wallbox.backend.modbus_transport.termios.tcsetattr"),
            patch("shelly_wallbox.backend.modbus_transport.termios.tcflush"),
            patch("shelly_wallbox.backend.modbus_transport._configured_serial_attrs", return_value=[]),
            patch.object(transport, "_write_all") as write_all,
            patch.object(transport, "_read_exact", side_effect=[response_frame[:3], response_frame[3:]]),
        ):
            response = transport._exchange_once(request, 1.0)

        self.assertEqual(response, response_payload[1:])
        write_all.assert_called_once()
        close_mock.assert_called_once_with(5)

        bad_frame = response_frame[:-1] + bytes((response_frame[-1] ^ 0xFF,))
        with (
            patch("shelly_wallbox.backend.modbus_transport.os.open", return_value=5),
            patch("shelly_wallbox.backend.modbus_transport.os.close"),
            patch("shelly_wallbox.backend.modbus_transport.termios.tcsetattr"),
            patch("shelly_wallbox.backend.modbus_transport.termios.tcflush"),
            patch("shelly_wallbox.backend.modbus_transport._configured_serial_attrs", return_value=[]),
            patch.object(transport, "_write_all"),
            patch.object(transport, "_read_exact", side_effect=[bad_frame[:3], bad_frame[3:]]),
        ):
            with self.assertRaises(ModbusResponseError):
                transport._exchange_once(request, 1.0)

        short_frame = b"\x01\x03\x02\x00"
        with (
            patch("shelly_wallbox.backend.modbus_transport.os.open", return_value=5),
            patch("shelly_wallbox.backend.modbus_transport.os.close"),
            patch("shelly_wallbox.backend.modbus_transport.termios.tcsetattr"),
            patch("shelly_wallbox.backend.modbus_transport.termios.tcflush"),
            patch("shelly_wallbox.backend.modbus_transport._configured_serial_attrs", return_value=[]),
            patch.object(transport, "_write_all"),
            patch.object(transport, "_read_exact", side_effect=[short_frame[:3], short_frame[3:]]),
        ):
            with self.assertRaises(ModbusTimeoutError):
                transport._exchange_once(request, 1.0)

    def test_serial_rtu_write_and_read_helpers_cover_partial_io_and_timeouts(self) -> None:
        with patch("shelly_wallbox.backend.modbus_transport.os.write", side_effect=[2, 2]):
            ModbusSerialRtuTransport._write_all(5, b"abcd")

        with patch("shelly_wallbox.backend.modbus_transport.os.write", return_value=0):
            with self.assertRaises(ModbusPortBusyError):
                ModbusSerialRtuTransport._write_all(5, b"ab")

        with (
            patch("shelly_wallbox.backend.modbus_transport.select.select", return_value=([5], [], [])),
            patch("shelly_wallbox.backend.modbus_transport.os.read", side_effect=[b"\x01", b"\x02"]),
            patch("shelly_wallbox.backend.modbus_transport.time.monotonic", side_effect=[0.0, 0.0, 0.1]),
        ):
            self.assertEqual(ModbusSerialRtuTransport._read_exact(5, 2, 1.0), b"\x01\x02")

        with (
            patch("shelly_wallbox.backend.modbus_transport.select.select", return_value=([], [], [])),
            patch("shelly_wallbox.backend.modbus_transport.time.monotonic", side_effect=[0.0, 0.0]),
        ):
            with self.assertRaises(ModbusTimeoutError):
                ModbusSerialRtuTransport._read_exact(5, 1, 1.0)

        with (
            patch("shelly_wallbox.backend.modbus_transport.select.select", return_value=([5], [], [])),
            patch("shelly_wallbox.backend.modbus_transport.os.read", return_value=b""),
            patch("shelly_wallbox.backend.modbus_transport.time.monotonic", side_effect=[0.0, 0.0]),
        ):
            with self.assertRaises(ModbusTimeoutError):
                ModbusSerialRtuTransport._read_exact(5, 1, 1.0)

    def test_create_modbus_transport_returns_expected_transport_classes(self) -> None:
        base_kwargs = dict(
            unit_id=1,
            timeout_seconds=1.0,
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            serial_port_owner="none",
            serial_port_owner_stop_command=None,
            serial_port_owner_start_command=None,
            serial_retry_count=0,
            serial_retry_delay_seconds=0.0,
        )
        self.assertIsInstance(
            create_modbus_transport(
                ModbusTransportSettings(
                    transport_kind="serial_rtu",
                    host=None,
                    port=None,
                    device="/dev/ttyS7",
                    **base_kwargs,
                )
            ),
            ModbusSerialRtuTransport,
        )
        self.assertIsInstance(
            create_modbus_transport(
                ModbusTransportSettings(
                    transport_kind="udp",
                    host="127.0.0.1",
                    port=502,
                    device=None,
                    **base_kwargs,
                )
            ),
            ModbusUdpTransport,
        )
        self.assertIsInstance(
            create_modbus_transport(
                ModbusTransportSettings(
                    transport_kind="tcp",
                    host="127.0.0.1",
                    port=502,
                    device=None,
                    **base_kwargs,
                )
            ),
            ModbusTcpTransport,
        )

    def test_serial_rtu_exchange_covers_remaining_error_and_recovery_paths(self) -> None:
        settings = ModbusTransportSettings(
            transport_kind="serial_rtu",
            unit_id=1,
            timeout_seconds=1.0,
            host=None,
            port=None,
            device="/dev/ttyS7",
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            serial_port_owner="venus_serial_starter",
            serial_port_owner_stop_command="/stop.sh",
            serial_port_owner_start_command="/start.sh",
            serial_retry_count=1,
            serial_retry_delay_seconds=0.1,
        )
        request = ModbusRequest(unit_id=1, function_code=0x03, payload=b"\x00\x00\x00\x01")
        transport = ModbusSerialRtuTransport(settings)
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

        transport._port_owner.reset_mock()
        transport._ensure_port_owned()
        transport._port_owner.ensure_owned.assert_called_once()

        self.assertIsInstance(
            ModbusSerialRtuTransport._normalized_serial_os_error(OSError(errno.EIO, "io")),
            ModbusTransportError,
        )
