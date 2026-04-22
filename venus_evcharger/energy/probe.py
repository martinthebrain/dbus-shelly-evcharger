# SPDX-License-Identifier: GPL-3.0-or-later
"""CLI helpers to actively probe Modbus-backed external energy-source configs."""

from __future__ import annotations

import argparse
import configparser
from dataclasses import replace
import json
from types import SimpleNamespace
from typing import Any, Mapping, cast

from venus_evcharger.backend.modbus_client import ModbusClient
from venus_evcharger.backend.modbus_transport import create_modbus_transport
from venus_evcharger.backend.modbus_transport_config import (
    load_modbus_transport_settings,
    modbus_transport_issue_reason,
)
from venus_evcharger.backend.modbus_transport_types import ModbusTransportSettings
from venus_evcharger.backend.template_support import load_template_config

from .profiles import energy_source_profile_details, energy_source_profile_probe_plan


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
    {
        "section": "MeterStatusRead",
        "register_type": "holding",
        "address": 37100,
        "data_type": "uint16",
        "word_order": "big",
        "scale": 1.0,
    },
    {
        "section": "HuaweiMeterActivePowerRead",
        "register_type": "holding",
        "address": 37113,
        "data_type": "int32",
        "word_order": "big",
        # Huawei uses >0 export, <0 import; the energy model uses
        # >0 import, <0 export.
        "scale": -1.0,
    },
    {
        "section": "HuaweiMeterPositiveActiveEnergyRead",
        "register_type": "holding",
        "address": 37119,
        "data_type": "int32",
        "word_order": "big",
        "scale": 1.0,
    },
    {
        "section": "HuaweiMeterReverseActiveEnergyRead",
        "register_type": "holding",
        "address": 37121,
        "data_type": "int32",
        "word_order": "big",
        "scale": 1.0,
    },
    {
        "section": "MeterTypeRead",
        "register_type": "holding",
        "address": 37125,
        "data_type": "uint16",
        "word_order": "big",
        "scale": 1.0,
    },
)


def detect_modbus_energy_source(
    config_path: str,
    *,
    profile_name: str = "",
    host: str = "",
    port: int | None = None,
    unit_id: int | None = None,
) -> dict[str, object]:
    """Actively test one Modbus energy config against candidate endpoints."""
    parser = load_template_config(config_path)
    transport = _config_transport_section(parser)
    probe_plan = dict(
        energy_source_profile_probe_plan(
            profile_name,
            configured_host=host or transport.get("Host", ""),
            configured_port=port if port is not None else transport.get("Port", ""),
            configured_unit_id=unit_id if unit_id is not None else transport.get("UnitId", transport.get("SlaveId", "")),
        )
    )
    default_host = str(probe_plan.get("host", "")).strip()
    if default_host and not transport.get("Host", "").strip():
        transport["Host"] = default_host
    base_transport = load_modbus_transport_settings(parser, _probe_service())
    field = _probe_field(parser)
    attempts: list[dict[str, object]] = []
    for candidate in _probe_candidates(base_transport, probe_plan):
        attempts.append(_attempt_probe(candidate, field))
        if attempts[-1]["ok"]:
            return {
                "config_path": config_path,
                "profile_name": str(profile_name).strip().lower(),
                "profile_details": dict(energy_source_profile_details(profile_name)),
                "probe_plan": dict(probe_plan),
                "probe_field": dict(field),
                "detected": dict(attempts[-1]),
                "attempts": attempts,
            }
    return {
        "config_path": config_path,
        "profile_name": str(profile_name).strip().lower(),
        "profile_details": dict(energy_source_profile_details(profile_name)),
        "probe_plan": dict(probe_plan),
        "probe_field": dict(field),
        "detected": None,
        "attempts": attempts,
    }


def validate_huawei_energy_source(
    config_path: str,
    *,
    profile_name: str,
    host: str = "",
    port: int | None = None,
    unit_id: int | None = None,
) -> dict[str, object]:
    """Validate one Huawei-backed energy config against a reachable endpoint."""
    normalized_profile = str(profile_name).strip().lower()
    detection = detect_modbus_energy_source(
        config_path,
        profile_name=normalized_profile,
        host=host,
        port=port,
        unit_id=unit_id,
    )
    detected = detection.get("detected")
    if not isinstance(detected, Mapping):
        return {
            **detection,
            "validation_ok": False,
            "field_results": [],
            "required_fields_ok": False,
            "meter_block_detected": False,
        }
    parser = load_template_config(config_path)
    transport = _config_transport_section(parser)
    transport["Host"] = str(detected.get("host", "") or "")
    if detected.get("port") is not None:
        transport["Port"] = str(int(detected["port"]))
    if detected.get("unit_id") is not None:
        transport["UnitId"] = str(int(detected["unit_id"]))
    base_transport = load_modbus_transport_settings(parser, _probe_service())
    candidate_transport = replace(
        base_transport,
        host=str(detected.get("host", "") or base_transport.host),
        port=int(detected.get("port", base_transport.port)),
        unit_id=int(detected.get("unit_id", base_transport.unit_id)),
    )
    field_results = _validate_fields(candidate_transport, parser, normalized_profile)
    required_results = [result for result in field_results if bool(result.get("required"))]
    required_fields_ok = all(bool(result.get("ok")) for result in required_results)
    meter_block_detected = any(
        bool(result.get("ok"))
        for result in field_results
        if str(result.get("section", "")).startswith("HuaweiMeter") or str(result.get("section", "")) == "MeterStatusRead"
    )
    return {
        **detection,
        "validation_ok": required_fields_ok,
        "required_fields_ok": required_fields_ok,
        "meter_block_detected": meter_block_detected,
        "field_results": field_results,
    }


def _probe_service() -> Any:
    return SimpleNamespace(shelly_request_timeout_seconds=2.0)


def _config_transport_section(parser: configparser.ConfigParser) -> configparser.SectionProxy:
    if parser.has_section("Transport"):
        return parser["Transport"]
    return parser["DEFAULT"]


def _probe_field(parser: configparser.ConfigParser) -> dict[str, object]:
    for section_name in _FIELD_PROBE_SECTIONS:
        field = _field_settings(parser, section_name)
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
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for section_name in _FIELD_PROBE_SECTIONS:
        field = _field_settings(parser, section_name)
        if field is None:
            continue
        attempt = _attempt_probe(transport_settings, field)
        attempt["section"] = section_name
        attempt["required"] = True
        results.append(attempt)
    if profile_name.startswith("huawei_"):
        for field in _HUAWEI_METER_FIELDS:
            attempt = _attempt_probe(transport_settings, field)
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
                candidates.append(
                    replace(
                        base_transport,
                        host=host,
                        port=candidate_port,
                        unit_id=int(candidate_unit_id),
                    )
                )
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


def _attempt_probe(
    transport_settings: ModbusTransportSettings,
    field: dict[str, object],
) -> dict[str, object]:
    address = cast(int, field["address"])
    scale = cast(float, field["scale"])
    register_type = cast(str, field["register_type"])
    data_type = cast(str, field["data_type"])
    word_order = cast(str, field["word_order"])
    try:
        transport = create_modbus_transport(transport_settings)
        client = ModbusClient(transport, transport_settings.unit_id, transport_settings.timeout_seconds)
        raw_value = client.read_scalar(
            register_type,
            address,
            data_type,
            word_order,
        )
        numeric_value = float(raw_value) if not isinstance(raw_value, bool) else (1.0 if raw_value else 0.0)
        scaled_value = numeric_value * scale
        return {
            "host": transport_settings.host,
            "port": transport_settings.port,
            "unit_id": transport_settings.unit_id,
            "ok": True,
            "raw_value": raw_value,
            "scaled_value": scaled_value,
        }
    except Exception as error:  # noqa: BLE001
        return {
            "host": transport_settings.host,
            "port": transport_settings.port,
            "unit_id": transport_settings.unit_id,
            "ok": False,
            "reason": modbus_transport_issue_reason(error) or error.__class__.__name__.lower(),
            "detail": str(error),
        }


def main(argv: list[str] | None = None) -> int:
    """Run the energy probe CLI."""
    parser = argparse.ArgumentParser(description="Probe external energy-source connector configs")
    parser.add_argument("command", choices=("detect-modbus-energy", "validate-huawei-energy"))
    parser.add_argument("config_path")
    parser.add_argument("--profile", default="")
    parser.add_argument("--host", default="")
    parser.add_argument("--port", type=int)
    parser.add_argument("--unit-id", type=int)
    args = parser.parse_args(argv)
    payload = _command_payload(args)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _command_payload(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "detect-modbus-energy":
        return detect_modbus_energy_source(
            args.config_path,
            profile_name=str(args.profile or ""),
            host=str(args.host or ""),
            port=args.port,
            unit_id=args.unit_id,
        )
    if args.command == "validate-huawei-energy":
        return validate_huawei_energy_source(
            args.config_path,
            profile_name=str(args.profile or ""),
            host=str(args.host or ""),
            port=args.port,
            unit_id=args.unit_id,
        )
    raise ValueError(f"Unsupported energy probe command '{args.command}'")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
