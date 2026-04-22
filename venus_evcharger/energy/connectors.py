# SPDX-License-Identifier: GPL-3.0-or-later
"""Connector registry for DBus and external energy-source transports."""

from __future__ import annotations

from dataclasses import dataclass
import json
import shlex
import subprocess
from typing import Any, cast

from venus_evcharger.backend.modbus_client import ModbusClient
from venus_evcharger.backend.modbus_transport import create_modbus_transport
from venus_evcharger.backend.modbus_transport_config import load_modbus_transport_settings
from venus_evcharger.backend.modbus_transport_types import ModbusTransportSettings
from venus_evcharger.backend.template_support import (
    TemplateAuthSettings,
    TemplateHttpBackendBase,
    config_section,
    json_path_value,
    load_template_auth_settings,
    load_template_config,
    normalize_http_method,
    resolved_url,
)
from venus_evcharger.core.contracts import finite_float_or_none, normalize_binary_flag

from .models import EnergySourceDefinition, EnergySourceSnapshot


@dataclass(frozen=True)
class TemplateHttpEnergySourceSettings:
    """Normalized config for one HTTP/JSON-backed external energy source."""

    base_url: str
    auth_settings: TemplateAuthSettings
    timeout_seconds: float
    request_method: str
    request_url: str
    soc_path: str | None
    usable_capacity_wh_path: str | None
    battery_power_path: str | None
    ac_power_path: str | None
    pv_input_power_path: str | None
    grid_interaction_path: str | None
    operating_mode_path: str | None
    online_path: str | None
    confidence_path: str | None


@dataclass(frozen=True)
class ModbusEnergyFieldSettings:
    """One optional Modbus read mapping used by the energy connector."""

    register_type: str
    address: int
    data_type: str
    scale: float
    word_order: str


@dataclass(frozen=True)
class ModbusEnergySourceSettings:
    """Normalized config for one Modbus-backed external energy source."""

    transport_settings: ModbusTransportSettings
    soc_field: ModbusEnergyFieldSettings | None
    usable_capacity_field: ModbusEnergyFieldSettings | None
    battery_power_field: ModbusEnergyFieldSettings | None
    ac_power_field: ModbusEnergyFieldSettings | None
    pv_input_power_field: ModbusEnergyFieldSettings | None
    grid_interaction_field: ModbusEnergyFieldSettings | None


@dataclass(frozen=True)
class CommandJsonEnergySourceSettings:
    """Normalized config for one local helper command returning JSON."""

    command: tuple[str, ...]
    timeout_seconds: float
    soc_path: str | None
    usable_capacity_wh_path: str | None
    battery_power_path: str | None
    ac_power_path: str | None
    pv_input_power_path: str | None
    grid_interaction_path: str | None
    operating_mode_path: str | None
    online_path: str | None
    confidence_path: str | None


def read_energy_source_snapshot(owner: Any, source: EnergySourceDefinition, now: float) -> EnergySourceSnapshot:
    """Read one energy-source snapshot through the configured connector layer."""
    connector_type = _normalized_connector_type(source.connector_type)
    if connector_type == "template_http":
        return _template_http_energy_source_snapshot(owner, source, now)
    if connector_type == "modbus":
        return _modbus_energy_source_snapshot(owner, source, now)
    if connector_type == "command_json":
        return _command_json_energy_source_snapshot(owner, source, now)
    return cast(EnergySourceSnapshot, owner._dbus_energy_source_snapshot(source, now))


def _normalized_connector_type(raw_value: object) -> str:
    normalized = str(raw_value).strip().lower()
    if normalized == "template_http_energy":
        return "template_http"
    return normalized or "dbus"


def _template_http_energy_source_snapshot(owner: Any, source: EnergySourceDefinition, now: float) -> EnergySourceSnapshot:
    runtime = _runtime_owner(owner)
    settings = _template_http_energy_source_settings(runtime, source)
    payload = TemplateHttpBackendBase(
        runtime,
        settings.timeout_seconds,
        auth_settings=settings.auth_settings,
    )._perform_request(settings.request_method, settings.request_url)
    soc_value = _optional_float_path(payload, settings.soc_path)
    if soc_value is not None and not 0.0 <= soc_value <= 100.0:
        soc_value = None
    usable_capacity_wh = _optional_float_path(payload, settings.usable_capacity_wh_path)
    if usable_capacity_wh is None:
        usable_capacity_wh = source.usable_capacity_wh
    elif usable_capacity_wh <= 0.0:
        usable_capacity_wh = None
    online = _optional_bool_path(payload, settings.online_path)
    confidence = _optional_confidence_path(payload, settings.confidence_path)
    return EnergySourceSnapshot(
        source_id=source.source_id,
        role=source.role,
        service_name=_template_source_name(source, settings),
        soc=soc_value,
        usable_capacity_wh=usable_capacity_wh,
        net_battery_power_w=_optional_float_path(payload, settings.battery_power_path),
        ac_power_w=_optional_float_path(payload, settings.ac_power_path),
        pv_input_power_w=_optional_float_path(payload, settings.pv_input_power_path),
        grid_interaction_w=_optional_float_path(payload, settings.grid_interaction_path),
        operating_mode=_optional_text_path(payload, settings.operating_mode_path) or "",
        online=True if online is None else bool(online),
        confidence=1.0 if confidence is None else confidence,
        captured_at=now,
    )


def _runtime_owner(owner: Any) -> Any:
    return getattr(owner, "service", owner)


def _template_source_name(source: EnergySourceDefinition, settings: TemplateHttpEnergySourceSettings) -> str:
    if source.service_name:
        return source.service_name
    if settings.base_url:
        return settings.base_url
    return source.config_path or source.source_id


def _template_http_energy_source_settings(runtime: Any, source: EnergySourceDefinition) -> TemplateHttpEnergySourceSettings:
    cache = _template_settings_cache(runtime)
    cache_key = str(source.config_path).strip()
    cached = cache.get(cache_key)
    if isinstance(cached, TemplateHttpEnergySourceSettings):
        return cached
    if not cache_key:
        raise ValueError(f"Energy source '{source.source_id}' requires ConfigPath for template_http connector")
    parser = load_template_config(cache_key)
    adapter = config_section(parser, "Adapter")
    request = config_section(parser, "EnergyRequest")
    response = config_section(parser, "EnergyResponse")
    base_url = str(adapter.get("BaseUrl", "")).strip()
    default_timeout = float(getattr(runtime, "shelly_request_timeout_seconds", 2.0) or 2.0)
    timeout = finite_float_or_none(adapter.get("RequestTimeoutSeconds", str(default_timeout)))
    request_url = resolved_url(base_url, request.get("Url", ""))
    settings = TemplateHttpEnergySourceSettings(
        base_url=base_url,
        auth_settings=load_template_auth_settings(adapter),
        timeout_seconds=default_timeout if timeout is None or timeout <= 0.0 else float(timeout),
        request_method=normalize_http_method(request.get("Method", "GET"), "GET"),
        request_url=request_url,
        soc_path=_optional_path(response.get("SocPath", "")),
        usable_capacity_wh_path=_optional_path(response.get("UsableCapacityWhPath", "")),
        battery_power_path=_optional_path(response.get("BatteryPowerPath", "")),
        ac_power_path=_optional_path(response.get("AcPowerPath", "")),
        pv_input_power_path=_optional_path(response.get("PvInputPowerPath", "")),
        grid_interaction_path=_optional_path(response.get("GridInteractionPath", "")),
        operating_mode_path=_optional_path(response.get("OperatingModePath", "")),
        online_path=_optional_path(response.get("OnlinePath", "")),
        confidence_path=_optional_path(response.get("ConfidencePath", "")),
    )
    _validate_template_http_energy_source_settings(source, settings)
    cache[cache_key] = settings
    return settings


def _template_settings_cache(runtime: Any) -> dict[str, TemplateHttpEnergySourceSettings]:
    cache = getattr(runtime, "_energy_template_settings_cache", None)
    if isinstance(cache, dict):
        return cast(dict[str, TemplateHttpEnergySourceSettings], cache)
    runtime._energy_template_settings_cache = {}
    return cast(dict[str, TemplateHttpEnergySourceSettings], runtime._energy_template_settings_cache)


def _modbus_settings_cache(runtime: Any) -> dict[str, ModbusEnergySourceSettings]:
    cache = getattr(runtime, "_energy_modbus_settings_cache", None)
    if isinstance(cache, dict):
        return cast(dict[str, ModbusEnergySourceSettings], cache)
    runtime._energy_modbus_settings_cache = {}
    return cast(dict[str, ModbusEnergySourceSettings], runtime._energy_modbus_settings_cache)


def _modbus_client_cache(runtime: Any) -> dict[str, ModbusClient]:
    cache = getattr(runtime, "_energy_modbus_client_cache", None)
    if isinstance(cache, dict):
        return cast(dict[str, ModbusClient], cache)
    runtime._energy_modbus_client_cache = {}
    return cast(dict[str, ModbusClient], runtime._energy_modbus_client_cache)


def _command_json_settings_cache(runtime: Any) -> dict[str, CommandJsonEnergySourceSettings]:
    cache = getattr(runtime, "_energy_command_settings_cache", None)
    if isinstance(cache, dict):
        return cast(dict[str, CommandJsonEnergySourceSettings], cache)
    runtime._energy_command_settings_cache = {}
    return cast(dict[str, CommandJsonEnergySourceSettings], runtime._energy_command_settings_cache)


def _validate_template_http_energy_source_settings(
    source: EnergySourceDefinition,
    settings: TemplateHttpEnergySourceSettings,
) -> None:
    if not settings.request_url:
        raise ValueError(f"Energy source '{source.source_id}' requires [EnergyRequest] Url")
    if (
        settings.soc_path is None
        and settings.battery_power_path is None
        and settings.ac_power_path is None
        and settings.pv_input_power_path is None
        and settings.grid_interaction_path is None
        and settings.usable_capacity_wh_path is None
        and source.usable_capacity_wh is None
    ):
        raise ValueError(
            f"Energy source '{source.source_id}' requires at least one readable EnergyResponse path or UsableCapacityWh"
        )


def _modbus_energy_source_snapshot(owner: Any, source: EnergySourceDefinition, now: float) -> EnergySourceSnapshot:
    runtime = _runtime_owner(owner)
    settings = _modbus_energy_source_settings(runtime, source)
    client = _modbus_energy_source_client(runtime, source, settings)
    soc_value = _modbus_field_value(client, settings.soc_field)
    if soc_value is not None and not 0.0 <= soc_value <= 100.0:
        soc_value = None
    usable_capacity_wh = _modbus_field_value(client, settings.usable_capacity_field)
    if usable_capacity_wh is None:
        usable_capacity_wh = source.usable_capacity_wh
    elif usable_capacity_wh <= 0.0:
        usable_capacity_wh = None
    return EnergySourceSnapshot(
        source_id=source.source_id,
        role=source.role,
        service_name=_modbus_source_name(source, settings.transport_settings),
        soc=soc_value,
        usable_capacity_wh=usable_capacity_wh,
        net_battery_power_w=_modbus_field_value(client, settings.battery_power_field),
        ac_power_w=_modbus_field_value(client, settings.ac_power_field),
        pv_input_power_w=_modbus_field_value(client, settings.pv_input_power_field),
        grid_interaction_w=_modbus_field_value(client, settings.grid_interaction_field),
        online=True,
        confidence=1.0,
        captured_at=now,
    )


def _modbus_source_name(source: EnergySourceDefinition, transport_settings: ModbusTransportSettings) -> str:
    if source.service_name:
        return source.service_name
    if transport_settings.host:
        return transport_settings.host
    if transport_settings.device:
        return transport_settings.device
    return source.config_path or source.source_id


def _modbus_energy_source_settings(runtime: Any, source: EnergySourceDefinition) -> ModbusEnergySourceSettings:
    cache = _modbus_settings_cache(runtime)
    cache_key = str(source.config_path).strip()
    cached = cache.get(cache_key)
    if isinstance(cached, ModbusEnergySourceSettings):
        return cached
    if not cache_key:
        raise ValueError(f"Energy source '{source.source_id}' requires ConfigPath for modbus connector")
    parser = load_template_config(cache_key)
    settings = ModbusEnergySourceSettings(
        transport_settings=load_modbus_transport_settings(parser, runtime),
        soc_field=_modbus_field_settings(parser, "SocRead"),
        usable_capacity_field=_modbus_field_settings(parser, "UsableCapacityRead"),
        battery_power_field=_modbus_field_settings(parser, "BatteryPowerRead"),
        ac_power_field=_modbus_field_settings(parser, "AcPowerRead"),
        pv_input_power_field=_modbus_field_settings(parser, "PvInputPowerRead"),
        grid_interaction_field=_modbus_field_settings(parser, "GridInteractionRead"),
    )
    _validate_modbus_energy_source_settings(source, settings)
    cache[cache_key] = settings
    return settings


def _modbus_field_settings(parser: Any, section_name: str) -> ModbusEnergyFieldSettings | None:
    if not parser.has_section(section_name):
        return None
    section = parser[section_name]
    address_text = str(section.get("Address", "")).strip()
    if not address_text:
        return None
    return ModbusEnergyFieldSettings(
        register_type=str(section.get("RegisterType", "holding")).strip().lower() or "holding",
        address=int(address_text),
        data_type=str(section.get("DataType", "uint16")).strip().lower() or "uint16",
        scale=float(str(section.get("Scale", "1")).strip() or "1"),
        word_order=str(section.get("WordOrder", "big")).strip().lower() or "big",
    )


def _validate_modbus_energy_source_settings(
    source: EnergySourceDefinition,
    settings: ModbusEnergySourceSettings,
) -> None:
    if (
        settings.soc_field is None
        and settings.usable_capacity_field is None
        and settings.battery_power_field is None
        and settings.ac_power_field is None
        and settings.pv_input_power_field is None
        and settings.grid_interaction_field is None
        and source.usable_capacity_wh is None
    ):
        raise ValueError(
            f"Energy source '{source.source_id}' requires at least one Modbus read section or UsableCapacityWh"
        )


def _modbus_energy_source_client(
    runtime: Any,
    source: EnergySourceDefinition,
    settings: ModbusEnergySourceSettings,
) -> ModbusClient:
    cache = _modbus_client_cache(runtime)
    cache_key = str(source.config_path).strip()
    cached = cache.get(cache_key)
    if isinstance(cached, ModbusClient):
        return cached
    transport = create_modbus_transport(settings.transport_settings)
    client = ModbusClient(transport, settings.transport_settings.unit_id, settings.transport_settings.timeout_seconds)
    cache[cache_key] = client
    return client


def _modbus_field_value(client: ModbusClient, field: ModbusEnergyFieldSettings | None) -> float | None:
    if field is None:
        return None
    raw_value = client.read_scalar(field.register_type, field.address, field.data_type, field.word_order)
    if isinstance(raw_value, bool):
        numeric_value = 1.0 if raw_value else 0.0
    else:
        numeric_value = float(raw_value)
    return numeric_value * float(field.scale)


def _command_json_energy_source_snapshot(owner: Any, source: EnergySourceDefinition, now: float) -> EnergySourceSnapshot:
    runtime = _runtime_owner(owner)
    settings = _command_json_energy_source_settings(runtime, source)
    completed = subprocess.run(  # noqa: S603
        settings.command,
        check=True,
        capture_output=True,
        text=True,
        timeout=settings.timeout_seconds,
    )
    payload = json.loads(completed.stdout.strip() or "{}")
    if not isinstance(payload, dict):
        raise ValueError(f"Energy source '{source.source_id}' helper did not return a JSON object")
    soc_value = _optional_float_path(payload, settings.soc_path)
    if soc_value is not None and not 0.0 <= soc_value <= 100.0:
        soc_value = None
    usable_capacity_wh = _optional_float_path(payload, settings.usable_capacity_wh_path)
    if usable_capacity_wh is None:
        usable_capacity_wh = source.usable_capacity_wh
    elif usable_capacity_wh <= 0.0:
        usable_capacity_wh = None
    online = _optional_bool_path(payload, settings.online_path)
    confidence = _optional_confidence_path(payload, settings.confidence_path)
    return EnergySourceSnapshot(
        source_id=source.source_id,
        role=source.role,
        service_name=_command_source_name(source, settings),
        soc=soc_value,
        usable_capacity_wh=usable_capacity_wh,
        net_battery_power_w=_optional_float_path(payload, settings.battery_power_path),
        ac_power_w=_optional_float_path(payload, settings.ac_power_path),
        pv_input_power_w=_optional_float_path(payload, settings.pv_input_power_path),
        grid_interaction_w=_optional_float_path(payload, settings.grid_interaction_path),
        operating_mode=_optional_text_path(payload, settings.operating_mode_path) or "",
        online=True if online is None else bool(online),
        confidence=1.0 if confidence is None else confidence,
        captured_at=now,
    )


def _command_source_name(source: EnergySourceDefinition, settings: CommandJsonEnergySourceSettings) -> str:
    if source.service_name:
        return source.service_name
    return settings.command[0] if settings.command else (source.config_path or source.source_id)


def _command_json_energy_source_settings(runtime: Any, source: EnergySourceDefinition) -> CommandJsonEnergySourceSettings:
    cache = _command_json_settings_cache(runtime)
    cache_key = str(source.config_path).strip()
    cached = cache.get(cache_key)
    if isinstance(cached, CommandJsonEnergySourceSettings):
        return cached
    if not cache_key:
        raise ValueError(f"Energy source '{source.source_id}' requires ConfigPath for command_json connector")
    parser = load_template_config(cache_key)
    adapter = config_section(parser, "Adapter")
    command = config_section(parser, "Command")
    response = config_section(parser, "Response")
    default_timeout = float(getattr(runtime, "shelly_request_timeout_seconds", 2.0) or 2.0)
    timeout_seconds = finite_float_or_none(
        command.get("TimeoutSeconds", adapter.get("RequestTimeoutSeconds", str(default_timeout)))
    )
    settings = CommandJsonEnergySourceSettings(
        command=_command_args(command),
        timeout_seconds=default_timeout if timeout_seconds is None or timeout_seconds <= 0.0 else float(timeout_seconds),
        soc_path=_optional_path(response.get("SocPath", "")),
        usable_capacity_wh_path=_optional_path(response.get("UsableCapacityWhPath", "")),
        battery_power_path=_optional_path(response.get("BatteryPowerPath", "")),
        ac_power_path=_optional_path(response.get("AcPowerPath", "")),
        pv_input_power_path=_optional_path(response.get("PvInputPowerPath", "")),
        grid_interaction_path=_optional_path(response.get("GridInteractionPath", "")),
        operating_mode_path=_optional_path(response.get("OperatingModePath", "")),
        online_path=_optional_path(response.get("OnlinePath", "")),
        confidence_path=_optional_path(response.get("ConfidencePath", "")),
    )
    _validate_command_json_energy_source_settings(source, settings)
    cache[cache_key] = settings
    return settings


def _command_args(command: Any) -> tuple[str, ...]:
    args_text = str(command.get("Args", "")).strip()
    if not args_text:
        return ()
    return tuple(shlex.split(args_text))


def _validate_command_json_energy_source_settings(
    source: EnergySourceDefinition,
    settings: CommandJsonEnergySourceSettings,
) -> None:
    if not settings.command:
        raise ValueError(f"Energy source '{source.source_id}' requires [Command] Args")
    if (
        settings.soc_path is None
        and settings.usable_capacity_wh_path is None
        and settings.battery_power_path is None
        and settings.ac_power_path is None
        and settings.pv_input_power_path is None
        and settings.grid_interaction_path is None
        and source.usable_capacity_wh is None
    ):
        raise ValueError(
            f"Energy source '{source.source_id}' requires at least one Response path or UsableCapacityWh"
        )


def _optional_path(value: object) -> str | None:
    normalized = str(value).strip()
    return normalized or None


def _optional_float_path(payload: dict[str, object], path: str | None) -> float | None:
    if path is None:
        return None
    return finite_float_or_none(json_path_value(payload, path))


def _optional_text_path(payload: dict[str, object], path: str | None) -> str | None:
    if path is None:
        return None
    value = json_path_value(payload, path)
    text = "" if value is None else str(value).strip()
    return text or None


def _optional_bool_path(payload: dict[str, object], path: str | None) -> bool | None:
    if path is None:
        return None
    value = json_path_value(payload, path)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "false", "yes", "no", "on", "off", "enabled", "disabled"}:
            return bool(normalize_binary_flag(text))
    return bool(normalize_binary_flag(value))


def _optional_confidence_path(payload: dict[str, object], path: str | None) -> float | None:
    value = _optional_float_path(payload, path)
    if value is None:
        return None
    return min(1.0, max(0.0, float(value)))
