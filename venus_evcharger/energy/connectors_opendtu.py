# SPDX-License-Identifier: GPL-3.0-or-later
"""OpenDTU connector for external energy sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from venus_evcharger.backend.template_support import (
    TemplateAuthSettings,
    TemplateHttpBackendBase,
    config_section,
    load_template_auth_settings,
    load_template_config,
    resolved_url,
)
from venus_evcharger.core.contracts import finite_float_or_none

from .connectors_common import _cache_map, _csv_filter, _runtime_owner, _sum_optional
from .models import EnergySourceDefinition, EnergySourceSnapshot
from .profiles import resolve_energy_source_profile


@dataclass(frozen=True)
class OpenDtuEnergySourceSettings:
    """Normalized config for one OpenDTU-backed energy source."""

    base_url: str
    auth_settings: TemplateAuthSettings
    timeout_seconds: float
    status_url: str
    inverter_status_url: str
    serial_filter: tuple[str, ...]
    max_data_age_seconds: float


def _opendtu_energy_source_snapshot(owner: Any, source: EnergySourceDefinition, now: float) -> EnergySourceSnapshot:
    runtime = _runtime_owner(owner)
    settings = _opendtu_energy_source_settings(runtime, source)
    client = TemplateHttpBackendBase(
        runtime,
        settings.timeout_seconds,
        auth_settings=settings.auth_settings,
    )
    payload = client._perform_request("GET", settings.status_url)
    inverters = _opendtu_selected_inverters(payload, settings, client)
    ac_power = _opendtu_total_ac_power(payload, inverters, settings.serial_filter)
    pv_input_power = _opendtu_total_dc_power(inverters)
    filtered_count = len(inverters)
    reachable_count = sum(1 for inverter in inverters if _opendtu_inverter_online(inverter, settings.max_data_age_seconds))
    plausible_idle = _opendtu_plausible_idle_snapshot(
        payload,
        inverters,
        ac_power_w=ac_power,
        pv_input_power_w=pv_input_power,
        max_data_age_seconds=settings.max_data_age_seconds,
        allow_unreachable_idle=_energy_source_allows_unreachable_idle(source),
    )
    online = filtered_count > 0 and (reachable_count > 0 or plausible_idle)
    confidence = 0.0 if filtered_count <= 0 else float(reachable_count) / float(filtered_count)
    if plausible_idle:
        confidence = max(confidence, 1.0)
    return EnergySourceSnapshot(
        source_id=source.source_id,
        role=source.role,
        service_name=_opendtu_source_name(source, settings),
        ac_power_w=ac_power,
        pv_input_power_w=pv_input_power,
        operating_mode="producing" if _opendtu_any_producing(inverters) else "idle",
        online=online,
        confidence=confidence,
        captured_at=now,
    )


def _opendtu_source_name(source: EnergySourceDefinition, settings: OpenDtuEnergySourceSettings) -> str:
    if source.service_name:
        return source.service_name
    if settings.base_url:
        return settings.base_url
    return source.config_path or source.source_id


def _opendtu_energy_source_settings(runtime: Any, source: EnergySourceDefinition) -> OpenDtuEnergySourceSettings:
    cache = cast(
        dict[str, OpenDtuEnergySourceSettings],
        _cache_map(runtime, "_energy_opendtu_settings_cache"),
    )
    cache_key = str(source.config_path).strip()
    cached = cache.get(cache_key)
    if isinstance(cached, OpenDtuEnergySourceSettings):
        return cached
    if not cache_key:
        raise ValueError(f"Energy source '{source.source_id}' requires ConfigPath for opendtu_http connector")
    parser = load_template_config(cache_key)
    adapter = config_section(parser, "Adapter")
    opendtu = config_section(parser, "OpenDTU")
    base_url = str(adapter.get("BaseUrl", "")).strip()
    default_timeout = float(getattr(runtime, "shelly_request_timeout_seconds", 2.0) or 2.0)
    timeout = finite_float_or_none(adapter.get("RequestTimeoutSeconds", str(default_timeout)))
    max_data_age = finite_float_or_none(opendtu.get("MaxDataAgeSeconds", "600"))
    settings = OpenDtuEnergySourceSettings(
        base_url=base_url,
        auth_settings=load_template_auth_settings(adapter),
        timeout_seconds=default_timeout if timeout is None or timeout <= 0.0 else float(timeout),
        status_url=resolved_url(base_url, opendtu.get("StatusUrl", "/api/livedata/status")),
        inverter_status_url=resolved_url(base_url, opendtu.get("InverterStatusUrl", "/api/livedata/status?inv=${serial}")),
        serial_filter=_csv_filter(opendtu.get("InverterSerials", "")),
        max_data_age_seconds=600.0 if max_data_age is None or max_data_age < 0.0 else float(max_data_age),
    )
    _validate_opendtu_energy_source_settings(source, settings)
    cache[cache_key] = settings
    return settings


def _validate_opendtu_energy_source_settings(source: EnergySourceDefinition, settings: OpenDtuEnergySourceSettings) -> None:
    if not settings.status_url:
        raise ValueError(f"Energy source '{source.source_id}' requires OpenDTU.StatusUrl or Adapter.BaseUrl")


def _opendtu_selected_inverters(
    payload: dict[str, object],
    settings: OpenDtuEnergySourceSettings,
    client: TemplateHttpBackendBase,
) -> tuple[dict[str, object], ...]:
    raw_inverters = payload.get("inverters")
    if not isinstance(raw_inverters, list):
        return ()
    selected: list[dict[str, object]] = []
    for raw_inverter in raw_inverters:
        if not isinstance(raw_inverter, dict):
            continue
        serial = str(raw_inverter.get("serial", "")).strip()
        if settings.serial_filter and serial not in settings.serial_filter:
            continue
        if "AC" in raw_inverter or _opendtu_unreachable_idle_stub(raw_inverter):
            selected.append(cast(dict[str, object], raw_inverter))
            continue
        if not serial:
            continue
        detail = client._perform_request("GET", settings.inverter_status_url, context={"serial": serial})
        detail_inverter = _opendtu_detail_inverter(detail)
        if detail_inverter is not None:
            selected.append(detail_inverter)
    return tuple(selected)


def _opendtu_detail_inverter(payload: dict[str, object]) -> dict[str, object] | None:
    raw_inverters = payload.get("inverters")
    if not isinstance(raw_inverters, list) or not raw_inverters:
        return None
    first = raw_inverters[0]
    return cast(dict[str, object], first) if isinstance(first, dict) else None


def _opendtu_total_ac_power(
    payload: dict[str, object],
    inverters: tuple[dict[str, object], ...],
    serial_filter: tuple[str, ...],
) -> float | None:
    if serial_filter:
        return _sum_optional(_opendtu_ac_power(inverter) for inverter in inverters)
    total = payload.get("total")
    if isinstance(total, dict):
        total_power = _opendtu_metric_value(total, "Power")
        if total_power is not None:
            return total_power
    return _sum_optional(_opendtu_ac_power(inverter) for inverter in inverters)


def _opendtu_total_dc_power(inverters: tuple[dict[str, object], ...]) -> float | None:
    return _sum_optional(_opendtu_dc_power(inverter) for inverter in inverters)


def _opendtu_any_producing(inverters: tuple[dict[str, object], ...]) -> bool:
    return any(bool(inverter.get("producing")) for inverter in inverters)


def _opendtu_plausible_idle_snapshot(
    payload: dict[str, object],
    inverters: tuple[dict[str, object], ...],
    *,
    ac_power_w: float | None,
    pv_input_power_w: float | None,
    max_data_age_seconds: float,
    allow_unreachable_idle: bool,
) -> bool:
    if not allow_unreachable_idle or not inverters or _opendtu_any_producing(inverters):
        return False
    if any(_opendtu_inverter_online(inverter, max_data_age_seconds) for inverter in inverters):
        return False
    if not _opendtu_zeroish_power(ac_power_w) or not _opendtu_zeroish_power(pv_input_power_w):
        return False
    hints = payload.get("hints")
    if isinstance(hints, dict) and bool(hints.get("radio_problem")):
        return False
    return all(_opendtu_unreachable_idle_stub(inverter) for inverter in inverters)


def _opendtu_unreachable_idle_stub(inverter: dict[str, object]) -> bool:
    return not bool(inverter.get("reachable")) and not bool(inverter.get("producing"))


def _energy_source_allows_unreachable_idle(source: EnergySourceDefinition) -> bool:
    profile = resolve_energy_source_profile(source.profile_name)
    if profile is not None:
        return profile.idle_unreachable_policy == "allow_plausible_idle"
    return source.role == "inverter"


def _opendtu_inverter_online(inverter: dict[str, object], max_data_age_seconds: float) -> bool:
    reachable = bool(inverter.get("reachable"))
    if not reachable:
        return False
    data_age = finite_float_or_none(inverter.get("data_age"))
    if data_age is None:
        return reachable
    return float(data_age) <= float(max_data_age_seconds)


def _opendtu_ac_power(inverter: dict[str, object]) -> float | None:
    ac = inverter.get("AC")
    if not isinstance(ac, dict):
        return None
    phase = ac.get("0")
    if not isinstance(phase, dict):
        return None
    return _opendtu_metric_value(phase, "Power")


def _opendtu_dc_power(inverter: dict[str, object]) -> float | None:
    dc = inverter.get("DC")
    if not isinstance(dc, dict):
        return None
    values: list[float] = []
    for channel in dc.values():
        if not isinstance(channel, dict):
            continue
        power = _opendtu_metric_value(channel, "Power")
        if power is not None:
            values.append(power)
    return _sum_optional(values)


def _opendtu_metric_value(container: dict[str, object], key: str) -> float | None:
    raw_metric = container.get(key)
    if not isinstance(raw_metric, dict):
        return None
    return finite_float_or_none(raw_metric.get("v"))


def _opendtu_zeroish_power(value: float | None) -> bool:
    return value is None or abs(float(value)) <= 0.5

