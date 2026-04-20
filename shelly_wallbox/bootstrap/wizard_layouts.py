# SPDX-License-Identifier: GPL-3.0-or-later
"""Preset rendering helpers for the optional wallbox setup wizard."""

from __future__ import annotations

from typing import Callable

from shelly_wallbox.bootstrap.wizard_adapters import native_charger_config, template_switch_group_files
from shelly_wallbox.bootstrap.wizard_split_layouts import split_topology_files
from shelly_wallbox.bootstrap.wizard_support import base_url_from_input

_SPLIT_ROLE_HOSTS: dict[str, tuple[str, ...]] = {
    "template-stack": ("meter", "switch", "charger"),
    "shelly-io-template-charger": ("meter", "switch", "charger"),
    "shelly-io-modbus-charger": ("meter", "switch"),
    "shelly-meter-goe": ("meter", "charger"),
    "goe-external-switch-group": ("switch", "charger"),
    "template-meter-goe-switch-group": ("meter", "switch", "charger"),
    "shelly-meter-goe-switch-group": ("meter", "switch", "charger"),
    "shelly-meter-modbus-switch-group": ("meter", "switch"),
}
_PROFILE_ROLE_HOSTS: dict[str, tuple[str, ...]] = {
    "native-charger": ("charger",),
    "native-charger-phase-switch": ("charger", "switch"),
}
_ROLE_INPUTS = ("meter", "switch", "charger")
ProfileRenderer = Callable[
    [dict[str, str], str | None, str, str, float | None, str, str, str, int, str, int],
    tuple[list[str], dict[str, str], dict[str, str]],
]


def resolve_role_hosts(
    *,
    profile: str,
    primary_host_input: str,
    meter_host_input: str | None,
    switch_host_input: str | None,
    charger_host_input: str | None,
    split_preset: str | None,
) -> dict[str, str]:
    role_defaults = _role_defaults(primary_host_input, meter_host_input, switch_host_input, charger_host_input)
    return {role: role_defaults[role] for role in _resolved_roles(profile, split_preset)}


def _role_defaults(
    primary_host_input: str,
    meter_host_input: str | None,
    switch_host_input: str | None,
    charger_host_input: str | None,
) -> dict[str, str]:
    provided_hosts = (meter_host_input, switch_host_input, charger_host_input)
    return {role: host or primary_host_input for role, host in zip(_ROLE_INPUTS, provided_hosts)}


def _resolved_roles(profile: str, split_preset: str | None) -> tuple[str, ...]:
    if profile == "split-topology":
        return _SPLIT_ROLE_HOSTS.get(split_preset or "", ())
    return _PROFILE_ROLE_HOSTS.get(profile, ())


def _native_charger_files(
    backend: str,
    charger_base_url: str,
    request_timeout_seconds: float | None,
    transport_kind: str,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> dict[str, str]:
    return {
        "wizard-charger.ini": native_charger_config(
            backend,
            charger_base_url,
            request_timeout_seconds=request_timeout_seconds,
            transport_kind=transport_kind,
            transport_host=transport_host,
            transport_port=transport_port,
            transport_device=transport_device,
            transport_unit_id=transport_unit_id,
        )
    }


def _base_urls(primary_host_input: str, role_hosts: dict[str, str]) -> tuple[str, str, str]:
    return (
        base_url_from_input(role_hosts.get("meter", primary_host_input)),
        base_url_from_input(role_hosts.get("switch", primary_host_input)),
        base_url_from_input(role_hosts.get("charger", primary_host_input)),
    )


def generated_adapter_files(
    *,
    profile: str,
    primary_host_input: str,
    meter_host_input: str | None,
    switch_host_input: str | None,
    charger_host_input: str | None,
    split_preset: str | None,
    charger_backend: str | None,
    request_timeout_seconds: float | None,
    switch_group_supported_phase_selections: str,
    transport_kind: str,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    role_hosts = resolve_role_hosts(
        profile=profile,
        primary_host_input=primary_host_input,
        meter_host_input=meter_host_input,
        switch_host_input=switch_host_input,
        charger_host_input=charger_host_input,
        split_preset=split_preset,
    )
    meter_base_url, switch_base_url, charger_base_url = _base_urls(primary_host_input, role_hosts)
    renderer = _profile_renderer(profile)
    if renderer is not None:
        return renderer(
            role_hosts,
            charger_backend,
            switch_base_url,
            charger_base_url,
            request_timeout_seconds,
            switch_group_supported_phase_selections,
            transport_kind,
            transport_host,
            transport_port,
            transport_device,
            transport_unit_id,
        )
    return split_topology_files(
        split_preset=split_preset,
        role_hosts=role_hosts,
        meter_base_url=meter_base_url,
        switch_base_url=switch_base_url,
        charger_base_url=charger_base_url,
        request_timeout_seconds=request_timeout_seconds,
        switch_group_supported_phase_selections=switch_group_supported_phase_selections,
        transport_kind=transport_kind,
        transport_host=transport_host,
        transport_port=transport_port,
        transport_device=transport_device,
        transport_unit_id=transport_unit_id,
    )


def _profile_renderer(profile: str) -> ProfileRenderer | None:
    if profile in {"simple-relay", "advanced-manual"}:
        return _passive_profile_files
    if profile == "native-charger":
        return _native_profile_renderer
    if profile == "native-charger-phase-switch":
        return _phase_switch_profile_renderer
    return None


def _passive_profile_files(
    role_hosts: dict[str, str],
    charger_backend: str | None,
    switch_base_url: str,
    charger_base_url: str,
    request_timeout_seconds: float | None,
    switch_group_supported_phase_selections: str,
    transport_kind: str,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    return [], {}, role_hosts


def _native_profile_renderer(
    role_hosts: dict[str, str],
    charger_backend: str | None,
    switch_base_url: str,
    charger_base_url: str,
    request_timeout_seconds: float | None,
    switch_group_supported_phase_selections: str,
    transport_kind: str,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    backend = charger_backend or "goe_charger"
    return (
        ["Mode=split", "MeterType=none", "SwitchType=none", f"ChargerType={backend}", "ChargerConfigPath=wizard-charger.ini"],
        _native_charger_files(
            backend,
            charger_base_url,
            request_timeout_seconds,
            transport_kind,
            transport_host,
            transport_port,
            transport_device,
            transport_unit_id,
        ),
        role_hosts,
    )


def _phase_switch_profile_renderer(
    role_hosts: dict[str, str],
    charger_backend: str | None,
    switch_base_url: str,
    charger_base_url: str,
    request_timeout_seconds: float | None,
    switch_group_supported_phase_selections: str,
    transport_kind: str,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    backend = charger_backend or "simpleevse_charger"
    files = _native_charger_files(
        backend,
        charger_base_url,
        request_timeout_seconds,
        transport_kind,
        transport_host,
        transport_port,
        transport_device,
        transport_unit_id,
    )
    files.update(template_switch_group_files(switch_base_url, switch_group_supported_phase_selections))
    return (
        [
            "Mode=split",
            "MeterType=none",
            "SwitchType=switch_group",
            "SwitchConfigPath=wizard-switch-group.ini",
            f"ChargerType={backend}",
            "ChargerConfigPath=wizard-charger.ini",
        ],
        files,
        role_hosts,
    )
