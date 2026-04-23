# SPDX-License-Identifier: GPL-3.0-or-later
"""Core helper functions for Modbus energy probing."""

from __future__ import annotations

import configparser
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from venus_evcharger.backend.modbus_transport_types import ModbusTransportSettings

_FIELD_PROBE_SECTIONS = (
    "SocRead",
    "BatteryPowerRead",
    "ChargeLimitPowerRead",
    "DischargeLimitPowerRead",
    "AcPowerRead",
    "PvInputPowerRead",
    "GridInteractionRead",
    "OperatingModeRead",
    "UsableCapacityRead",
)

_HUAWEI_METER_FIELDS: tuple[dict[str, object], ...] = (
    {"section": "MeterStatusRead", "register_type": "holding", "address": 37100, "data_type": "uint16", "word_order": "big", "scale": 1.0},
    {"section": "HuaweiMeterActivePowerRead", "register_type": "holding", "address": 37113, "data_type": "int32", "word_order": "big", "scale": -1.0},
    {"section": "HuaweiMeterPositiveActiveEnergyRead", "register_type": "holding", "address": 37119, "data_type": "int32", "word_order": "big", "scale": 1.0},
    {"section": "HuaweiMeterReverseActiveEnergyRead", "register_type": "holding", "address": 37121, "data_type": "int32", "word_order": "big", "scale": 1.0},
    {"section": "MeterTypeRead", "register_type": "holding", "address": 37125, "data_type": "uint16", "word_order": "big", "scale": 1.0},
)


def _probe_service() -> Any:
    return SimpleNamespace(shelly_request_timeout_seconds=2.0)


def _config_transport_section(parser: configparser.ConfigParser) -> configparser.SectionProxy:
    if parser.has_section("Transport"):
        return parser["Transport"]
    return parser["DEFAULT"]


def _probe_field(
    parser: configparser.ConfigParser,
    field_settings: Callable[[configparser.ConfigParser, str], dict[str, object] | None],
) -> dict[str, object]:
    for section_name in _FIELD_PROBE_SECTIONS:
        field = field_settings(parser, section_name)
        if field is not None:
            return field
    raise ValueError("Energy probe requires at least one Modbus read section")


def _field_settings(parser: configparser.ConfigParser, section_name: str) -> dict[str, object] | None:
    if not parser.has_section(section_name):
        return None
    section = parser[section_name]
    address_text = str(section.get("Address", "")).strip()
    if not address_text:
        return None
    return {
        "section": section_name,
        "register_type": str(section.get("RegisterType", "holding")).strip().lower() or "holding",
        "address": int(address_text),
        "data_type": str(section.get("DataType", "uint16")).strip().lower() or "uint16",
        "word_order": str(section.get("WordOrder", "big")).strip().lower() or "big",
        "scale": float(str(section.get("Scale", "1")).strip() or "1"),
    }


def _validate_fields(
    transport_settings: ModbusTransportSettings,
    parser: configparser.ConfigParser,
    profile_name: str,
    field_settings: Callable[[configparser.ConfigParser, str], dict[str, object] | None],
    attempt_probe: Callable[[ModbusTransportSettings, dict[str, object]], dict[str, object]],
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for section_name in _FIELD_PROBE_SECTIONS:
        field = field_settings(parser, section_name)
        if field is None:
            continue
        attempt = attempt_probe(transport_settings, field)
        attempt["section"] = section_name
        attempt["required"] = True
        results.append(attempt)
    if profile_name.startswith("huawei_"):
        for field in _HUAWEI_METER_FIELDS:
            attempt = attempt_probe(transport_settings, field)
            attempt["section"] = field["section"]
            attempt["required"] = False
            results.append(attempt)
    return results


def _probe_candidates(
    base_transport: ModbusTransportSettings,
    probe_plan: Mapping[str, Any],
) -> tuple[ModbusTransportSettings, ...]:
    host_candidates = _text_candidates(probe_plan.get("host"))
    if not host_candidates:
        host_candidates = [base_transport.host] if base_transport.host else []
    port_candidates = _int_candidates(probe_plan.get("port_candidates"), base_transport.port)
    unit_id_candidates = _int_candidates(probe_plan.get("unit_id_candidates"), base_transport.unit_id)
    if not host_candidates and base_transport.transport_kind != "serial_rtu":
        raise ValueError("Energy probe requires a host candidate for TCP/UDP Modbus detection")
    candidates: list[ModbusTransportSettings] = []
    default_hosts = host_candidates or ([base_transport.host] if base_transport.host else [])
    default_ports = port_candidates or ([base_transport.port] if base_transport.port is not None else [])
    for host in default_hosts:
        for candidate_port in default_ports:
            for candidate_unit_id in unit_id_candidates or [base_transport.unit_id]:
                candidates.append(replace(base_transport, host=host, port=candidate_port, unit_id=int(candidate_unit_id)))
    return tuple(candidates)


def _text_candidates(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _int_candidates(value: object, fallback: int | None) -> list[int]:
    candidates: list[int] = []
    raw_values = value if isinstance(value, (list, tuple)) else [value]
    for item in raw_values:
        if item is None or isinstance(item, bool):
            continue
        try:
            candidates.append(int(str(item).strip()))
        except (TypeError, ValueError):
            continue
    if not candidates and fallback is not None:
        candidates.append(int(fallback))
    return candidates
