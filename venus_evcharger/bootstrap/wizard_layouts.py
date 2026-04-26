# SPDX-License-Identifier: GPL-3.0-or-later
"""Role-host resolution helpers for the optional wallbox setup wizard."""

from __future__ import annotations

_TOPOLOGY_ROLE_HOSTS: dict[str, tuple[str, ...]] = {
    "template-stack": ("meter", "switch", "charger"),
    "shelly-io-template-charger": ("meter", "switch", "charger"),
    "shelly-io-modbus-charger": ("meter", "switch"),
    "shelly-meter-goe": ("meter", "charger"),
    "shelly-meter-modbus-charger": ("meter", "charger"),
    "goe-external-switch-group": ("switch", "charger"),
    "template-meter-goe-switch-group": ("meter", "switch", "charger"),
    "shelly-meter-goe-switch-group": ("meter", "switch", "charger"),
    "shelly-meter-modbus-switch-group": ("meter", "switch"),
}
_PROFILE_ROLE_HOSTS: dict[str, tuple[str, ...]] = {
    "simple_relay": ("meter", "switch"),
    "native_device": ("charger",),
    "hybrid_topology": ("charger", "switch"),
}
_ROLE_INPUTS = ("meter", "switch", "charger")


def resolve_role_hosts(
    *,
    profile: str,
    primary_host_input: str,
    meter_host_input: str | None,
    switch_host_input: str | None,
    charger_host_input: str | None,
    topology_preset: str | None,
) -> dict[str, str]:
    role_defaults = _role_defaults(primary_host_input, meter_host_input, switch_host_input, charger_host_input)
    return {role: role_defaults[role] for role in _resolved_roles(profile, topology_preset)}


def _role_defaults(
    primary_host_input: str,
    meter_host_input: str | None,
    switch_host_input: str | None,
    charger_host_input: str | None,
) -> dict[str, str]:
    provided_hosts = (meter_host_input, switch_host_input, charger_host_input)
    return {role: host or primary_host_input for role, host in zip(_ROLE_INPUTS, provided_hosts)}


def _resolved_roles(profile: str, topology_preset: str | None) -> tuple[str, ...]:
    if profile == "multi_adapter_topology":
        return _TOPOLOGY_ROLE_HOSTS.get(topology_preset or "", ())
    return _PROFILE_ROLE_HOSTS.get(profile, ())
