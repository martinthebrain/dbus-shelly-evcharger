# SPDX-License-Identifier: GPL-3.0-or-later
"""Constants and small utility helpers for the optional wallbox setup wizard."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class TopologyPresetSpec:
    """Wizard-facing metadata for one modular topology preset."""

    preset_id: str
    label: str
    role_hosts: tuple[str, ...]
    responsibility: str
    intro: str
    recommended_rank: int = 100
    charger_backend: str | None = None
    uses_cerbo_relay: bool = False


PROFILE_LABELS: tuple[tuple[str, str], ...] = (
    ("simple_relay", "Recommended: Shelly PM/PM1 Gen4 measures and switches"),
    ("native_device", "Native wallbox backend (go-e, Modbus, SmartEVSE, SimpleEVSE)"),
    ("hybrid_topology", "Native charger plus external phase switch"),
    ("multi_adapter_topology", "Guided modular multi-adapter setup"),
    ("advanced_manual", "Advanced/manual configuration"),
)
PROFILE_VALUES = tuple(item[0] for item in PROFILE_LABELS)
POLICY_VALUES: tuple[str, ...] = ("manual", "auto", "scheduled")
POLICY_LABELS: tuple[tuple[str, str], ...] = (
    ("manual", "Manual charging"),
    ("auto", "PV surplus charging"),
    ("scheduled", "PV surplus plus scheduled fallback"),
)

TOPOLOGY_PRESET_SPECS: tuple[TopologyPresetSpec, ...] = (
    TopologyPresetSpec(
        "template-stack",
        "Generic template meter + switch + charger",
        ("meter", "switch", "charger"),
        "template adapters keep meter, switch, and charger responsibilities separate",
        "This topology keeps meter, switch, and charger as separate adapter roles backed by template adapters.",
        recommended_rank=90,
    ),
    TopologyPresetSpec(
        "shelly-io-template-charger",
        "Shelly PM/relay measures and switches + template charger",
        ("meter", "switch", "charger"),
        "Shelly owns metering/switching; the template charger owns charger status/control",
        "This topology lets one Shelly path measure and switch while a template charger adapter owns charger control.",
        recommended_rank=40,
        charger_backend="template_charger",
    ),
    TopologyPresetSpec(
        "shelly-io-modbus-charger",
        "Shelly PM/relay measures and switches + Modbus wallbox",
        ("meter", "switch"),
        "Shelly owns metering/switching; the Modbus wallbox backend owns charger status/control",
        "This topology lets one Shelly path measure and switch while a Modbus wallbox backend owns charger control.",
        recommended_rank=50,
        charger_backend="modbus_charger",
    ),
    TopologyPresetSpec(
        "template-meter-cerbo-relay",
        "Template meter + Cerbo GX Relay switch",
        ("meter",),
        "template meter measures energy; Cerbo GX relay backend only switches the contactor",
        "This topology uses an external meter and one local Cerbo GX relay as the switching actuator.",
        recommended_rank=20,
        uses_cerbo_relay=True,
    ),
    TopologyPresetSpec(
        "shelly-meter-cerbo-relay",
        "Recommended: Shelly meter + Cerbo GX Relay switch",
        ("meter",),
        "Shelly measures energy; Cerbo GX relay backend only switches the contactor",
        "This topology uses a Shelly meter and one local Cerbo GX relay as the switching actuator.",
        recommended_rank=10,
        uses_cerbo_relay=True,
    ),
    TopologyPresetSpec(
        "shelly-meter-goe",
        "Recommended: Shelly meter + native go-e charger",
        ("meter", "charger"),
        "Shelly measures energy; go-e backend owns charger enable/current/status",
        "This topology uses a separate meter plus a native go-e charger.",
        recommended_rank=30,
        charger_backend="goe_charger",
    ),
    TopologyPresetSpec(
        "shelly-meter-modbus-charger",
        "Recommended: Shelly meter + native Modbus wallbox",
        ("meter", "charger"),
        "Shelly measures energy; the Modbus wallbox backend owns charger enable/current/status",
        "This topology uses a separate Shelly meter plus a native Modbus wallbox backend.",
        recommended_rank=35,
        charger_backend="modbus_charger",
    ),
    TopologyPresetSpec(
        "goe-external-switch-group",
        "go-e charger + external 3-phase switch group",
        ("switch", "charger"),
        "go-e owns charger metering/control; the switch group owns external phase switching",
        "This topology uses a go-e charger plus one external switch-group adapter for phase switching.",
        recommended_rank=60,
        charger_backend="goe_charger",
    ),
    TopologyPresetSpec(
        "template-meter-goe-switch-group",
        "Template meter + go-e charger + external 3-phase switch group",
        ("meter", "switch", "charger"),
        "template meter measures energy; go-e owns charger control; switch group owns phase switching",
        "This topology uses a template meter, a go-e charger, and an external switch-group adapter.",
        recommended_rank=80,
        charger_backend="goe_charger",
    ),
    TopologyPresetSpec(
        "shelly-meter-goe-switch-group",
        "Shelly meter + go-e charger + external 3-phase switch group",
        ("meter", "switch", "charger"),
        "Shelly measures energy; go-e owns charger control; switch group owns phase switching",
        "This topology uses a Shelly meter, a go-e charger, and an external switch-group adapter.",
        recommended_rank=70,
        charger_backend="goe_charger",
    ),
    TopologyPresetSpec(
        "shelly-meter-modbus-switch-group",
        "Shelly meter + Modbus charger + external 3-phase switch group",
        ("meter", "switch"),
        "Shelly measures energy; Modbus owns charger control; switch group owns phase switching",
        "This topology uses a Shelly meter, a Modbus charger, and an external switch-group adapter.",
        recommended_rank=75,
        charger_backend="modbus_charger",
    ),
)
ORDERED_TOPOLOGY_PRESET_SPECS: tuple[TopologyPresetSpec, ...] = tuple(
    sorted(TOPOLOGY_PRESET_SPECS, key=lambda spec: (spec.recommended_rank, spec.label))
)
TOPOLOGY_PRESET_LABELS: tuple[tuple[str, str], ...] = tuple((spec.preset_id, spec.label) for spec in ORDERED_TOPOLOGY_PRESET_SPECS)
TOPOLOGY_PRESET_VALUES = tuple(item[0] for item in TOPOLOGY_PRESET_LABELS)
PROFILE_LABEL_MAP = dict(PROFILE_LABELS)
POLICY_LABEL_MAP = dict(POLICY_LABELS)
TOPOLOGY_PRESET_LABEL_MAP = dict(TOPOLOGY_PRESET_LABELS)
PROFILE_ROLE_HOSTS: dict[str, tuple[str, ...]] = {
    "simple_relay": ("meter", "switch"),
    "native_device": ("charger",),
    "hybrid_topology": ("charger", "switch"),
}
TOPOLOGY_ROLE_HOSTS: dict[str, tuple[str, ...]] = {spec.preset_id: spec.role_hosts for spec in TOPOLOGY_PRESET_SPECS}
TOPOLOGY_PRESET_INTROS: dict[str, str] = {spec.preset_id: spec.intro for spec in TOPOLOGY_PRESET_SPECS}
TOPOLOGY_PRESET_BACKENDS: dict[str, str] = {
    spec.preset_id: spec.charger_backend for spec in TOPOLOGY_PRESET_SPECS if spec.charger_backend is not None
}
CERBO_RELAY_TOPOLOGY_PRESETS = frozenset(spec.preset_id for spec in TOPOLOGY_PRESET_SPECS if spec.uses_cerbo_relay)
PROFILE_RESPONSIBILITY_SUMMARIES: dict[str, str] = {
    "simple_relay": "one Shelly-compatible device provides both metering and switching; runtime uses the combined backend path",
    "native_device": "the charger backend owns metering, enable/disable, current control, and status where supported",
    "hybrid_topology": "the charger backend owns charging; a separate switch backend owns phase/contact switching",
    "multi_adapter_topology": "the wizard selects independent meter, switch, and charger adapters; runtime still uses the same role interfaces",
    "advanced_manual": "the operator owns the final backend wiring in the generated config",
}
TOPOLOGY_RESPONSIBILITY_SUMMARIES: dict[str, str] = {
    spec.preset_id: spec.responsibility for spec in TOPOLOGY_PRESET_SPECS
}
POLICY_MODE_NOTES: dict[str, str] = {
    "manual": "Manual mode follows direct GUI/API start-stop commands; surplus thresholds are not used.",
    "auto": "Auto mode waits for configured PV surplus and SOC conditions before enabling charging.",
    "scheduled": "Scheduled mode behaves like Auto during the day window, then uses the configured night fallback after the latest end time.",
}
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


def policy_mode_label(policy_mode: str) -> str:
    return POLICY_LABEL_MAP.get(policy_mode, policy_mode)


def policy_mode_note(policy_mode: str) -> str:
    return POLICY_MODE_NOTES.get(policy_mode, policy_mode)


def topology_preset_label(topology_preset: str | None) -> str | None:
    if topology_preset is None:
        return None
    return TOPOLOGY_PRESET_LABEL_MAP.get(topology_preset, topology_preset)


def topology_uses_cerbo_relay(topology_preset: str | None) -> bool:
    return topology_preset in CERBO_RELAY_TOPOLOGY_PRESETS


def setup_responsibility_summary(profile: str, topology_preset: str | None) -> str:
    if topology_preset is not None:
        return TOPOLOGY_RESPONSIBILITY_SUMMARIES.get(topology_preset, topology_preset)
    return PROFILE_RESPONSIBILITY_SUMMARIES.get(profile, profile)
