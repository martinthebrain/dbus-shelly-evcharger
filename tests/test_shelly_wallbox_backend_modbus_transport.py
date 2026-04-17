# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import errno
import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

from shelly_wallbox.backend.modbus_transport import (
    ModbusPortBusyError,
    ModbusRequest,
    ModbusSerialRtuTransport,
    ModbusSlaveOfflineError,
    ModbusTimeoutError,
    ModbusTransportSettings,
    _VenusSerialPortOwner,
    load_modbus_transport_settings,
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
