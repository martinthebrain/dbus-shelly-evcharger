# SPDX-License-Identifier: GPL-3.0-or-later
"""CLI helpers to actively probe Modbus-backed external energy-source configs."""

from __future__ import annotations

import argparse
import configparser
from dataclasses import replace
import json
from pathlib import Path
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
    source_id: str = "huawei",
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
            "recommendation": _huawei_recommendation(
                normalized_profile,
                detection=detection,
                required_fields_ok=False,
                meter_block_detected=False,
                source_id=source_id,
            ),
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
        "recommendation": _huawei_recommendation(
            normalized_profile,
            detection=detection,
            required_fields_ok=required_fields_ok,
            meter_block_detected=meter_block_detected,
            source_id=source_id,
        ),
    }


def _huawei_recommendation(
    profile_name: str,
    *,
    detection: Mapping[str, object],
    required_fields_ok: bool,
    meter_block_detected: bool,
    source_id: str,
) -> dict[str, object]:
    normalized_source_id = _normalized_source_id(source_id)
    detected = detection.get("detected")
    details = detection.get("profile_details")
    profile_details = details if isinstance(details, Mapping) else {}
    detected_mapping = detected if isinstance(detected, Mapping) else {}
    template_name = _recommended_huawei_template(profile_name)
    config_path = _recommended_huawei_config_path(template_name)
    status = "ready" if required_fields_ok else "incomplete"
    notes: list[str] = []
    if meter_block_detected:
        notes.append("Huawei meter block detected")
    else:
        notes.append("Huawei meter block not detected")
    if required_fields_ok:
        notes.append("Configured Huawei energy fields responded successfully")
    else:
        notes.append("One or more required Huawei energy fields did not respond")
    return {
        "status": status,
        "suggested_profile": profile_name,
        "suggested_template": template_name,
        "suggested_config_path": config_path,
        "host": detected_mapping.get("host", ""),
        "port": detected_mapping.get("port"),
        "unit_id": detected_mapping.get("unit_id"),
        "platform": profile_details.get("platform", ""),
        "access_mode": profile_details.get("access_mode", ""),
        "meter_block_detected": meter_block_detected,
        "required_fields_ok": required_fields_ok,
        "capacity_required_for_weighted_soc": True,
        "capacity_config_key": f"AutoEnergySource.{normalized_source_id}.UsableCapacityWh",
        "capacity_hint": "Set usable battery capacity in Wh when you want weighted combined SOC.",
        "summary": _recommendation_summary(profile_name, detected_mapping, meter_block_detected, template_name),
        "config_snippet": _recommendation_config_snippet(
            profile_name,
            detected_mapping,
            template_name=template_name,
            config_path=config_path,
            source_id=normalized_source_id,
        ),
        "wizard_hint_block": _recommendation_wizard_hint_block(
            profile_name,
            detected_mapping,
            meter_block_detected=meter_block_detected,
            template_name=template_name,
            config_path=config_path,
            source_id=normalized_source_id,
        ),
        "notes": notes,
    }


def _normalized_source_id(source_id: str) -> str:
    cleaned = str(source_id).strip()
    return cleaned or "huawei"


def _recommended_huawei_template(profile_name: str) -> str:
    normalized = str(profile_name).strip().lower()
    if normalized.startswith("huawei_ma_"):
        return "deploy/venus/template-energy-source-huawei-ma-modbus.ini"
    if normalized.startswith("huawei_mb_"):
        return "deploy/venus/template-energy-source-huawei-mb-modbus.ini"
    if normalized == "huawei_smartlogger_modbus_tcp":
        return "deploy/venus/template-energy-source-huawei-mb-modbus.ini"
    return "deploy/venus/template-energy-source-huawei-mb-modbus.ini"


def _recommended_huawei_config_path(template_name: str) -> str:
    filename = str(template_name).strip().rsplit("/", 1)[-1]
    if filename.startswith("template-energy-source-"):
        filename = filename[len("template-energy-source-") :]
    return f"/data/etc/{filename}"


def _recommendation_summary(
    profile_name: str,
    detected: Mapping[str, object],
    meter_block_detected: bool,
    template_name: str,
) -> str:
    host = str(detected.get("host", "") or "")
    port = detected.get("port")
    unit_id = detected.get("unit_id")
    meter_text = "present" if meter_block_detected else "missing"
    location_text = f"host={host}" if host else "host=unknown"
    if port is not None:
        location_text += f", port={port}"
    if unit_id is not None:
        location_text += f", unit={unit_id}"
    return (
        f"Use profile {profile_name} with template {template_name}; "
        f"{location_text}; meter block {meter_text}."
    )


def _recommendation_config_snippet(
    profile_name: str,
    detected: Mapping[str, object],
    *,
    template_name: str,
    config_path: str,
    source_id: str,
) -> str:
    host = str(detected.get("host", "") or "").strip()
    port = _optional_int(detected.get("port"))
    unit_id = _optional_int(detected.get("unit_id"))
    lines = [
        "# Add the source id to AutoEnergySources in your main config.",
        f"# Example: AutoEnergySources=victron,{source_id}",
        f"AutoEnergySource.{source_id}.Profile=" + profile_name,
        f"AutoEnergySource.{source_id}.ConfigPath=" + config_path,
    ]
    if host:
        lines.append(f"AutoEnergySource.{source_id}.Host=" + host)
    if port is not None:
        lines.append(f"AutoEnergySource.{source_id}.Port={port}")
    if unit_id is not None:
        lines.append(f"AutoEnergySource.{source_id}.UnitId={unit_id}")
    lines.extend(
        (
            "# Copy the matching starter template to the ConfigPath above:",
            "# " + template_name,
            "# Optional but recommended when you want weighted combined SOC:",
            f"# AutoEnergySource.{source_id}.UsableCapacityWh=<set-me>",
        )
    )
    return "\n".join(lines)


def _recommendation_wizard_hint_block(
    profile_name: str,
    detected: Mapping[str, object],
    *,
    meter_block_detected: bool,
    template_name: str,
    config_path: str,
    source_id: str,
) -> str:
    host = str(detected.get("host", "") or "").strip() or "unknown"
    port = _optional_int(detected.get("port"))
    unit_id = _optional_int(detected.get("unit_id"))
    port_text = str(port) if port is not None else "unknown"
    unit_text = str(unit_id) if unit_id is not None else "unknown"
    meter_text = "present" if meter_block_detected else "not detected"
    lines = [
        "Huawei recommendation",
        f"- profile: {profile_name}",
        f"- template: {template_name}",
        f"- config path: {config_path}",
        f"- host: {host}",
        f"- port: {port_text}",
        f"- unit id: {unit_text}",
        f"- meter block: {meter_text}",
        f"- source id: {source_id}",
        f"- capacity follow-up: set AutoEnergySource.{source_id}.UsableCapacityWh for weighted combined SOC",
        "- next step: copy the template, then paste the config snippet into the main config",
    ]
    return "\n".join(lines)


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


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
    parser.add_argument("--source-id", default="huawei")
    parser.add_argument(
        "--emit",
        choices=("json", "ini", "wizard-hint", "summary"),
        default="json",
        help="Output format for validate-huawei-energy. detect-modbus-energy always emits JSON.",
    )
    parser.add_argument(
        "--write-recommendation-prefix",
        default="",
        help="Write Huawei recommendation files using the given file prefix.",
    )
    args = parser.parse_args(argv)
    payload = _command_payload(args)
    payload = _payload_with_written_files(args, payload)
    print(_render_payload(args, payload))
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


def _render_payload(args: argparse.Namespace, payload: Mapping[str, object]) -> str:
    if args.command != "validate-huawei-energy" or str(args.emit) == "json":
        return json.dumps(payload, indent=2, sort_keys=True)
    recommendation = payload.get("recommendation")
    if not isinstance(recommendation, Mapping):
        return json.dumps(payload, indent=2, sort_keys=True)
    emit_mode = str(args.emit)
    if emit_mode == "ini":
        return _render_recommendation_field(recommendation, "config_snippet", payload)
    if emit_mode == "wizard-hint":
        return _render_recommendation_field(recommendation, "wizard_hint_block", payload)
    if emit_mode == "summary":
        return _render_recommendation_field(recommendation, "summary", payload)
    return json.dumps(payload, indent=2, sort_keys=True)


def _render_recommendation_field(
    recommendation: Mapping[str, object],
    field_name: str,
    payload: Mapping[str, object],
) -> str:
    value = recommendation.get(field_name)
    if isinstance(value, str) and value.strip():
        return value
    return json.dumps(payload, indent=2, sort_keys=True)


def _payload_with_written_files(args: argparse.Namespace, payload: Mapping[str, object]) -> dict[str, object]:
    prefix = str(getattr(args, "write_recommendation_prefix", "") or "").strip()
    if args.command != "validate-huawei-energy" or not prefix:
        return dict(payload)
    recommendation = payload.get("recommendation")
    if not isinstance(recommendation, Mapping):
        return dict(payload)
    written_files = _write_recommendation_bundle(prefix, recommendation)
    enriched = dict(payload)
    enriched["written_files"] = written_files
    return enriched


def _write_recommendation_bundle(prefix: str, recommendation: Mapping[str, object]) -> dict[str, str]:
    base_prefix = str(Path(prefix))
    targets = {
        "config_snippet": Path(base_prefix + ".ini"),
        "wizard_hint": Path(base_prefix + ".wizard.txt"),
        "summary": Path(base_prefix + ".summary.txt"),
    }
    contents = {
        "config_snippet": _recommendation_text(recommendation, "config_snippet"),
        "wizard_hint": _recommendation_text(recommendation, "wizard_hint_block"),
        "summary": _recommendation_text(recommendation, "summary"),
    }
    for path in targets.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    for key, path in targets.items():
        path.write_text(contents[key], encoding="utf-8")
    return {key: str(path) for key, path in targets.items()}


def _recommendation_text(recommendation: Mapping[str, object], field_name: str) -> str:
    value = recommendation.get(field_name)
    if isinstance(value, str):
        return value
    return ""


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
