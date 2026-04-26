# SPDX-License-Identifier: GPL-3.0-or-later
"""Constants and small utility helpers for the optional wallbox setup wizard."""

from __future__ import annotations

from urllib.parse import urlparse

PROFILE_LABELS: tuple[tuple[str, str], ...] = (
    ("simple_relay", "Single switched path"),
    ("native_device", "Native device"),
    ("hybrid_topology", "Native device + external phase switch"),
    ("multi_adapter_topology", "Multi-adapter topology"),
    ("advanced_manual", "Advanced/manual"),
)
PROFILE_VALUES = tuple(item[0] for item in PROFILE_LABELS)
POLICY_VALUES: tuple[str, ...] = ("manual", "auto", "scheduled")
TOPOLOGY_PRESET_LABELS: tuple[tuple[str, str], ...] = (
    ("template-stack", "Template meter + switch + charger"),
    ("shelly-io-template-charger", "Shelly meter + Shelly switch + template charger"),
    ("shelly-io-modbus-charger", "Shelly meter + Shelly switch + Modbus charger"),
    ("shelly-meter-goe", "Shelly meter + go-e charger"),
    ("shelly-meter-modbus-charger", "Shelly meter + Modbus charger"),
    ("goe-external-switch-group", "go-e charger + external 3-phase switch group"),
    ("template-meter-goe-switch-group", "Template meter + go-e charger + external 3-phase switch group"),
    ("shelly-meter-goe-switch-group", "Shelly meter + go-e charger + external 3-phase switch group"),
    ("shelly-meter-modbus-switch-group", "Shelly meter + Modbus charger + external 3-phase switch group"),
)
TOPOLOGY_PRESET_VALUES = tuple(item[0] for item in TOPOLOGY_PRESET_LABELS)
PROFILE_LABEL_MAP = dict(PROFILE_LABELS)
TOPOLOGY_PRESET_LABEL_MAP = dict(TOPOLOGY_PRESET_LABELS)
NATIVE_CHARGER_VALUES: tuple[str, ...] = (
    "goe_charger",
    "simpleevse_charger",
    "smartevse_charger",
    "template_charger",
    "modbus_charger",
)
PHASE_SWITCH_CHARGER_VALUES: tuple[str, ...] = ("simpleevse_charger", "smartevse_charger")
TRANSPORT_VALUES: tuple[str, ...] = ("serial_rtu", "tcp")


def host_from_input(host_input: str) -> str:
    parsed = urlparse(host_input.strip())
    if parsed.scheme:
        if parsed.hostname:
            return parsed.hostname
        raise ValueError(f"Invalid host input '{host_input}'")
    normalized = host_input.strip()
    if not normalized:
        raise ValueError("Host must not be empty")
    return normalized


def base_url_from_input(host_input: str) -> str:
    normalized = host_input.strip()
    if not normalized:
        raise ValueError("Host must not be empty")
    parsed = urlparse(normalized)
    return normalized.rstrip("/") if parsed.scheme else f"http://{normalized.rstrip('/')}"


def backend_requires_transport(backend: str | None) -> bool:
    return backend in {"simpleevse_charger", "smartevse_charger", "modbus_charger"}


def default_transport_kind(backend: str | None) -> str:
    return "tcp" if backend == "modbus_charger" else "serial_rtu"


def transport_summary(backend: str | None, transport_kind: str) -> str | None:
    return transport_kind if backend_requires_transport(backend) else None


def profile_label(profile: str) -> str:
    return PROFILE_LABEL_MAP.get(profile, profile)


def topology_preset_label(topology_preset: str | None) -> str | None:
    if topology_preset is None:
        return None
    return TOPOLOGY_PRESET_LABEL_MAP.get(topology_preset, topology_preset)
