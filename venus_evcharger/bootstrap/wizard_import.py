# SPDX-License-Identifier: GPL-3.0-or-later
"""Import helpers for cloning or seeding wizard answers from an existing config."""

from __future__ import annotations

import configparser
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from venus_evcharger.bootstrap.wizard_models import (
    WizardChargerBackend,
    WizardPolicyMode,
    WizardProfile,
    WizardTransportKind,
)
from venus_evcharger.bootstrap.wizard_support import (
    NATIVE_CHARGER_VALUES,
    PHASE_SWITCH_CHARGER_VALUES,
    backend_requires_transport,
)

_PROFILE_DEFAULTS_BY_BACKENDS: dict[tuple[str, str, str], tuple[WizardProfile, str | None, WizardChargerBackend | None]] = {
    ("template_meter", "template_switch", "template_charger"): ("split-topology", "template-stack", "template_charger"),
    ("shelly_meter", "shelly_switch", "template_charger"): ("split-topology", "shelly-io-template-charger", "template_charger"),
    ("shelly_meter", "shelly_switch", "modbus_charger"): ("split-topology", "shelly-io-modbus-charger", "modbus_charger"),
    ("shelly_meter", "none", "goe_charger"): ("split-topology", "shelly-meter-goe", "goe_charger"),
    ("shelly_meter", "none", "modbus_charger"): ("split-topology", "shelly-meter-modbus-charger", "modbus_charger"),
    ("none", "switch_group", "goe_charger"): ("split-topology", "goe-external-switch-group", "goe_charger"),
    ("template_meter", "switch_group", "goe_charger"): ("split-topology", "template-meter-goe-switch-group", "goe_charger"),
    ("shelly_meter", "switch_group", "goe_charger"): ("split-topology", "shelly-meter-goe-switch-group", "goe_charger"),
    ("shelly_meter", "switch_group", "modbus_charger"): ("split-topology", "shelly-meter-modbus-switch-group", "modbus_charger"),
}


@dataclass(frozen=True)
class ImportedWizardDefaults:
    imported_from: str
    profile: WizardProfile | None
    host_input: str | None
    meter_host_input: str | None
    switch_host_input: str | None
    charger_host_input: str | None
    device_instance: int | None
    phase: str | None
    policy_mode: WizardPolicyMode | None
    digest_auth: bool | None
    username: str | None
    password: str | None
    split_preset: str | None
    charger_backend: WizardChargerBackend | None
    charger_preset: str | None
    request_timeout_seconds: float | None
    switch_group_phase_layout: str | None
    auto_start_surplus_watts: float | None
    auto_stop_surplus_watts: float | None
    auto_min_soc: float | None
    auto_resume_soc: float | None
    scheduled_enabled_days: str | None
    scheduled_latest_end_time: str | None
    scheduled_night_current_amps: float | None
    transport_kind: WizardTransportKind | None
    transport_host: str | None
    transport_port: int | None
    transport_device: str | None
    transport_unit_id: int | None


def _config_parser(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    with path.open(encoding="utf-8") as handle:
        parser.read_file(handle)
    return parser


def _as_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    return int(value)


def _as_float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    return float(value)


def _adapter_path(config_path: Path, backends: configparser.SectionProxy | None, key: str) -> Path | None:
    if backends is None:
        return None
    adapter_path_value = backends.get(key)
    if not adapter_path_value:
        return None
    adapter_path = Path(adapter_path_value)
    if not adapter_path.is_absolute():
        adapter_path = config_path.parent / adapter_path
    return adapter_path if adapter_path.exists() else None


def _adapter_host_value(adapter_path: Path | None) -> str | None:
    if adapter_path is None:
        return None
    adapter = _config_parser(adapter_path)
    adapter_defaults = adapter["DEFAULT"] if "DEFAULT" in adapter else adapter.defaults()
    adapter_section = adapter["Adapter"] if adapter.has_section("Adapter") else adapter_defaults
    return adapter_section.get("Host") or adapter_section.get("BaseUrl")


def _switch_group_host_value(adapter_path: Path | None) -> str | None:
    if adapter_path is None:
        return None
    adapter = _config_parser(adapter_path)
    members = adapter["Members"] if adapter.has_section("Members") else None
    if members is None:
        return _adapter_host_value(adapter_path)
    return _switch_group_member_host(adapter_path, members.get("P1"))


def _switch_group_member_host(adapter_path: Path, phase_path_value: str | None) -> str | None:
    if not phase_path_value:
        return None
    phase_path = Path(phase_path_value)
    if not phase_path.is_absolute():
        phase_path = adapter_path.parent / phase_path
    return _adapter_host_value(phase_path if phase_path.exists() else None)


def _policy_mode(value: str | None) -> WizardPolicyMode | None:
    normalized = (value or "").strip()
    if normalized == "1":
        return "auto"
    if normalized == "2":
        return "scheduled"
    if normalized == "0":
        return "manual"
    return None


def _profile_defaults(backends: configparser.SectionProxy | None) -> tuple[WizardProfile | None, str | None, WizardChargerBackend | None]:
    if backends is None:
        return "simple-relay", None, None
    return _profile_defaults_from_types(*_backend_types(backends))


def _profile_defaults_from_types(
    meter_type: str,
    switch_type: str,
    charger_type: str,
) -> tuple[WizardProfile | None, str | None, WizardChargerBackend | None]:
    backend = cast(WizardChargerBackend | None, charger_type or None)
    native_defaults = _native_profile_defaults(meter_type, switch_type, charger_type, backend)
    if native_defaults is not None:
        return native_defaults
    preset_match = _PROFILE_DEFAULTS_BY_BACKENDS.get((meter_type, switch_type, charger_type))
    if preset_match is not None:
        return preset_match
    return ("advanced-manual", None, backend) if any((meter_type, switch_type, charger_type)) else (None, None, None)


def _backend_types(backends: configparser.SectionProxy) -> tuple[str, str, str]:
    return (
        backends.get("MeterType", "").strip(),
        backends.get("SwitchType", "").strip(),
        backends.get("ChargerType", "").strip(),
    )


def _native_profile_defaults(
    meter_type: str,
    switch_type: str,
    charger_type: str,
    backend: WizardChargerBackend | None,
) -> tuple[WizardProfile, str | None, WizardChargerBackend | None] | None:
    if (meter_type, switch_type) == ("none", "none") and charger_type in NATIVE_CHARGER_VALUES:
        return "native-charger", None, backend
    if switch_type == "switch_group" and charger_type in PHASE_SWITCH_CHARGER_VALUES:
        return "native-charger-phase-switch", None, backend
    return None


def _transport_defaults(config_path: Path, backends: configparser.SectionProxy | None, backend: str | None) -> tuple[
    WizardTransportKind | None,
    str | None,
    int | None,
    str | None,
    int | None,
]:
    adapter_path = _charger_adapter_path(config_path, backends, backend)
    if adapter_path is None:
        return None, None, None, None, None
    adapter = _config_parser(adapter_path)
    adapter_defaults = adapter["DEFAULT"] if "DEFAULT" in adapter else adapter.defaults()
    adapter_section = adapter["Adapter"] if adapter.has_section("Adapter") else adapter_defaults
    transport_kind = cast(WizardTransportKind | None, adapter_section.get("Transport"))
    transport_section = adapter["Transport"] if adapter.has_section("Transport") else adapter_defaults
    return (
        transport_kind,
        transport_section.get("Host"),
        _as_int(transport_section.get("Port")),
        transport_section.get("Device"),
        _as_int(transport_section.get("UnitId")),
    )


def _charger_adapter_path(config_path: Path, backends: configparser.SectionProxy | None, backend: str | None) -> Path | None:
    if not backend_requires_transport(backend) or backends is None:
        return None
    return _adapter_path(config_path, backends, "ChargerConfigPath")


def _request_timeout_seconds(config_path: Path, backends: configparser.SectionProxy | None, backend: str | None) -> float | None:
    adapter_path = _goe_charger_adapter_path(config_path, backends, backend)
    if adapter_path is None:
        return None
    adapter = _config_parser(adapter_path)
    adapter_defaults = adapter["DEFAULT"] if "DEFAULT" in adapter else adapter.defaults()
    adapter_section = adapter["Adapter"] if adapter.has_section("Adapter") else adapter_defaults
    return _as_float(adapter_section.get("RequestTimeoutSeconds"))


def _charger_preset(config_path: Path, backends: configparser.SectionProxy | None) -> str | None:
    adapter_path = _adapter_path(config_path, backends, "ChargerConfigPath")
    if adapter_path is None:
        return None
    adapter = _config_parser(adapter_path)
    adapter_defaults = adapter["DEFAULT"] if "DEFAULT" in adapter else adapter.defaults()
    adapter_section = adapter["Adapter"] if adapter.has_section("Adapter") else adapter_defaults
    preset = str(adapter_section.get("Preset", "")).strip()
    return preset or None


def _goe_charger_adapter_path(config_path: Path, backends: configparser.SectionProxy | None, backend: str | None) -> Path | None:
    if backend != "goe_charger":
        return None
    return _adapter_path(config_path, backends, "ChargerConfigPath")


def _switch_group_phase_layout(config_path: Path, backends: configparser.SectionProxy | None) -> str | None:
    adapter_path = _adapter_path(config_path, backends, "SwitchConfigPath")
    if adapter_path is None:
        return None
    adapter = _config_parser(adapter_path)
    if not adapter.has_section("Capabilities"):
        return None
    return adapter["Capabilities"].get("SupportedPhaseSelections")


def _load_from_result_json(config_path: Path) -> ImportedWizardDefaults:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Wizard result does not contain a JSON object: {config_path}")
    defaults = payload.get("answer_defaults")
    if not isinstance(defaults, dict):
        raise ValueError(f"Wizard result is missing answer_defaults: {config_path}")
    return ImportedWizardDefaults(
        imported_from=str(config_path),
        profile=cast(WizardProfile | None, defaults.get("profile")),
        host_input=cast(str | None, defaults.get("host_input")),
        meter_host_input=cast(str | None, defaults.get("meter_host_input")),
        switch_host_input=cast(str | None, defaults.get("switch_host_input")),
        charger_host_input=cast(str | None, defaults.get("charger_host_input")),
        device_instance=cast(int | None, defaults.get("device_instance")),
        phase=cast(str | None, defaults.get("phase")),
        policy_mode=cast(WizardPolicyMode | None, defaults.get("policy_mode")),
        digest_auth=cast(bool | None, defaults.get("digest_auth")),
        username=cast(str | None, defaults.get("username")),
        password=None,
        split_preset=cast(str | None, defaults.get("split_preset")),
        charger_backend=cast(WizardChargerBackend | None, defaults.get("charger_backend")),
        charger_preset=cast(str | None, defaults.get("charger_preset")),
        request_timeout_seconds=cast(float | None, defaults.get("request_timeout_seconds")),
        switch_group_phase_layout=cast(str | None, defaults.get("switch_group_supported_phase_selections")),
        auto_start_surplus_watts=cast(float | None, defaults.get("auto_start_surplus_watts")),
        auto_stop_surplus_watts=cast(float | None, defaults.get("auto_stop_surplus_watts")),
        auto_min_soc=cast(float | None, defaults.get("auto_min_soc")),
        auto_resume_soc=cast(float | None, defaults.get("auto_resume_soc")),
        scheduled_enabled_days=cast(str | None, defaults.get("scheduled_enabled_days")),
        scheduled_latest_end_time=cast(str | None, defaults.get("scheduled_latest_end_time")),
        scheduled_night_current_amps=cast(float | None, defaults.get("scheduled_night_current_amps")),
        transport_kind=cast(WizardTransportKind | None, defaults.get("transport_kind")),
        transport_host=cast(str | None, defaults.get("transport_host")),
        transport_port=cast(int | None, defaults.get("transport_port")),
        transport_device=cast(str | None, defaults.get("transport_device")),
        transport_unit_id=cast(int | None, defaults.get("transport_unit_id")),
    )


def load_imported_defaults(config_path: Path) -> ImportedWizardDefaults:
    if not config_path.exists():
        raise ValueError(f"Import config does not exist: {config_path}")
    if config_path.name.endswith(".wizard-result.json"):
        return _load_from_result_json(config_path)
    parser = _config_parser(config_path)
    defaults = parser["DEFAULT"] if "DEFAULT" in parser else parser.defaults()
    backends = parser["Backends"] if parser.has_section("Backends") else None
    profile, split_preset, charger_backend = _profile_defaults(backends)
    meter_host_input = _adapter_host_value(_adapter_path(config_path, backends, "MeterConfigPath"))
    switch_host_input = _switch_group_host_value(_adapter_path(config_path, backends, "SwitchConfigPath"))
    charger_host_input = _adapter_host_value(_adapter_path(config_path, backends, "ChargerConfigPath"))
    transport_kind, transport_host, transport_port, transport_device, transport_unit_id = _transport_defaults(
        config_path,
        backends,
        charger_backend,
    )
    return ImportedWizardDefaults(
        imported_from=str(config_path),
        profile=profile,
        host_input=defaults.get("Host"),
        meter_host_input=meter_host_input,
        switch_host_input=switch_host_input,
        charger_host_input=charger_host_input,
        device_instance=_as_int(defaults.get("DeviceInstance")),
        phase=defaults.get("Phase"),
        policy_mode=_policy_mode(defaults.get("Mode")),
        digest_auth=_as_bool(defaults.get("DigestAuth")),
        username=defaults.get("Username"),
        password=defaults.get("Password"),
        split_preset=split_preset,
        charger_backend=charger_backend,
        charger_preset=_charger_preset(config_path, backends),
        request_timeout_seconds=_request_timeout_seconds(config_path, backends, charger_backend),
        switch_group_phase_layout=_switch_group_phase_layout(config_path, backends),
        auto_start_surplus_watts=_as_float(defaults.get("AutoStartSurplusWatts")),
        auto_stop_surplus_watts=_as_float(defaults.get("AutoStopSurplusWatts")),
        auto_min_soc=_as_float(defaults.get("AutoMinSoc")),
        auto_resume_soc=_as_float(defaults.get("AutoResumeSoc")),
        scheduled_enabled_days=defaults.get("AutoScheduledEnabledDays"),
        scheduled_latest_end_time=defaults.get("AutoScheduledLatestEndTime"),
        scheduled_night_current_amps=_as_float(defaults.get("AutoScheduledNightCurrentAmps")),
        transport_kind=transport_kind,
        transport_host=transport_host,
        transport_port=transport_port,
        transport_device=transport_device,
        transport_unit_id=transport_unit_id,
    )
