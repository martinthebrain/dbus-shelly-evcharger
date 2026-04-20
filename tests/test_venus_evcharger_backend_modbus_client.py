# SPDX-License-Identifier: GPL-3.0-or-later
import struct
import unittest

from venus_evcharger.backend.modbus_client import (
    ModbusClient,
    ModbusDeviceError,
    ModbusProtocolError,
    decode_register_value,
    encode_register_value,
    register_count,
)
from venus_evcharger.backend.modbus_transport import ModbusRequest


class _Transport:
    def __init__(self, response: bytes = b"") -> None:
        self.response = response
        self.requests: list[ModbusRequest] = []

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes:
        self.requests.append(request)
        return self.response


class TestShellyWallboxBackendModbusClient(unittest.TestCase):
    def test_register_count_and_scalar_codec_cover_supported_types(self) -> None:
        self.assertEqual(register_count("uint32"), 2)
        self.assertEqual(register_count("int32"), 2)
        self.assertEqual(register_count("float32"), 2)
        self.assertEqual(register_count("uint16"), 1)

        self.assertTrue(decode_register_value((1,), "bool"))
        self.assertEqual(decode_register_value((12,), "uint16"), 12)
        self.assertEqual(decode_register_value((0xFFFE,), "int16"), -2)
        self.assertEqual(decode_register_value((0x1234, 0x5678), "uint32"), 0x12345678)
        self.assertEqual(decode_register_value((0xFFFF, 0xFFFE), "int32"), -2)
        self.assertAlmostEqual(
            float(decode_register_value((0x3FC0, 0x0000), "float32")),
            1.5,
        )
        self.assertEqual(
            decode_register_value((0x5678, 0x1234), "uint32", word_order="little"),
            0x12345678,
        )

        self.assertEqual(encode_register_value(True, "bool"), (1,))
        self.assertEqual(encode_register_value(12, "uint16"), (12,))
        self.assertEqual(encode_register_value(-2, "int16"), (0xFFFE,))
        self.assertEqual(encode_register_value(0x12345678, "uint32"), (0x1234, 0x5678))
        self.assertEqual(encode_register_value(-2, "int32"), (0xFFFF, 0xFFFE))
        encoded_float = encode_register_value(1.5, "float32")
        self.assertEqual(
            b"".join(part.to_bytes(2, "big") for part in encoded_float),
            struct.pack(">f", 1.5),
        )
        self.assertEqual(
            encode_register_value(0x12345678, "uint32", word_order="little"),
            (0x5678, 0x1234),
        )

        with self.assertRaisesRegex(ValueError, "at least one register"):
            decode_register_value((), "uint16")
        with self.assertRaisesRegex(ValueError, "Unsupported Modbus data type"):
            decode_register_value((1,), "weird")
        with self.assertRaisesRegex(ValueError, "Unsupported Modbus data type"):
            encode_register_value(1, "weird")

    def test_response_pdu_maps_empty_exception_and_wrong_function_frames(self) -> None:
        client = ModbusClient(_Transport(b""), unit_id=7, timeout_seconds=1.0)
        with self.assertRaisesRegex(ModbusProtocolError, "Empty Modbus response"):
            client._response_pdu(0x03, b"\x00\x00\x00\x01")

        client = ModbusClient(_Transport(bytes((0x83, 0x02))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaisesRegex(ModbusDeviceError, "0x02"):
            client._response_pdu(0x03, b"\x00\x00\x00\x01")

        client = ModbusClient(_Transport(bytes((0x83,))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaisesRegex(ModbusDeviceError, "0x-1"):
            client._response_pdu(0x03, b"\x00\x00\x00\x01")

        client = ModbusClient(_Transport(bytes((0x04, 0x00))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaisesRegex(ModbusProtocolError, "Unexpected Modbus response function"):
            client._response_pdu(0x03, b"\x00\x00\x00\x01")

    def test_read_bits_and_registers_validate_incomplete_frames(self) -> None:
        client = ModbusClient(_Transport(bytes((0x01,))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaisesRegex(ModbusProtocolError, "Missing Modbus bit byte-count"):
            client._read_bits("coil", 0, 1)

        client = ModbusClient(_Transport(bytes((0x01, 0x02, 0x01))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaisesRegex(ModbusProtocolError, "Incomplete Modbus bit response"):
            client._read_bits("coil", 0, 9)

        client = ModbusClient(_Transport(bytes((0x03,))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaisesRegex(ModbusProtocolError, "Missing Modbus register byte-count"):
            client._read_registers("holding", 0, 1)

        client = ModbusClient(_Transport(bytes((0x03, 0x04, 0x00, 0x01))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaisesRegex(ModbusProtocolError, "Incomplete Modbus register response"):
            client._read_registers("holding", 0, 2)

    def test_read_scalar_and_write_methods_cover_success_and_length_failures(self) -> None:
        client = ModbusClient(_Transport(bytes((0x01, 0x01, 0x01))), unit_id=7, timeout_seconds=1.0)
        self.assertTrue(client.read_scalar("coil", 4, "bool"))
        request = client.transport.requests[-1]
        self.assertEqual(request.unit_id, 7)
        self.assertEqual(request.function_code, 0x01)

        client = ModbusClient(_Transport(bytes((0x02, 0x01, 0x01))), unit_id=7, timeout_seconds=1.0)
        self.assertEqual(client.read_scalar("discrete", 4, "uint16"), 1)

        client = ModbusClient(_Transport(bytes((0x04, 0x04, 0x12, 0x34, 0x56, 0x78))), unit_id=7, timeout_seconds=1.0)
        self.assertEqual(client.read_scalar("input", 10, "uint32"), 0x12345678)

        client = ModbusClient(_Transport(bytes((0x05, 0x00, 0x01, 0xFF, 0x00))), unit_id=7, timeout_seconds=1.0)
        client.write_single_coil(1, True)

        client = ModbusClient(_Transport(bytes((0x06, 0x00, 0x02, 0x00, 0x09))), unit_id=7, timeout_seconds=1.0)
        client.write_single_register(2, 9)

        client = ModbusClient(_Transport(bytes((0x10, 0x00, 0x03, 0x00, 0x02))), unit_id=7, timeout_seconds=1.0)
        client.write_multiple_registers(3, [4, 5])

        client = ModbusClient(_Transport(bytes((0x05, 0x00, 0x01))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaisesRegex(ModbusProtocolError, "coil write response length"):
            client.write_single_coil(1, True)

        client = ModbusClient(_Transport(bytes((0x06, 0x00, 0x02))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaisesRegex(ModbusProtocolError, "register write response length"):
            client.write_single_register(2, 9)

        client = ModbusClient(_Transport(bytes((0x10, 0x00, 0x03))), unit_id=7, timeout_seconds=1.0)
        with self.assertRaisesRegex(ModbusProtocolError, "multi-register write response length"):
            client.write_multiple_registers(3, [4, 5])
