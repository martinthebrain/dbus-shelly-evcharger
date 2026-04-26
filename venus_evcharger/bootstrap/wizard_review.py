# SPDX-License-Identifier: GPL-3.0-or-later
"""Manual review hints for wizard-generated setups."""

from __future__ import annotations

from venus_evcharger.bootstrap.wizard_support import backend_requires_transport

_ENDPOINT_PROFILES = frozenset({"native_device", "hybrid_topology", "multi_adapter_topology"})
_INDIVIDUAL_ADDRESS_PRESETS = frozenset(
    {
        "shelly-io-template-charger",
        "shelly-io-modbus-charger",
        "shelly-meter-goe",
        "shelly-meter-goe-switch-group",
        "shelly-meter-modbus-switch-group",
    }
)
_PHASE_WIRING_PRESETS = frozenset(
    {
        "goe-external-switch-group",
        "template-meter-goe-switch-group",
        "shelly-meter-goe-switch-group",
        "shelly-meter-modbus-switch-group",
    }
)


def _conditional_review_items(profile: str, policy_mode: str, topology_preset: str | None) -> tuple[str, ...]:
    checks = (
        (profile in _ENDPOINT_PROFILES, "adapter endpoints or serial transport"),
        (profile == "hybrid_topology", "phase-switch member wiring"),
        (profile == "multi_adapter_topology" and topology_preset in _INDIVIDUAL_ADDRESS_PRESETS, "individual meter/switch device addresses"),
        (profile == "multi_adapter_topology" and topology_preset in _PHASE_WIRING_PRESETS, "phase-switch member wiring"),
        (policy_mode in {"auto", "scheduled"}, "Auto thresholds"),
        (policy_mode == "scheduled", "scheduled settings"),
    )
    return tuple(item for enabled, item in checks if enabled)


def manual_review_items(
    profile: str,
    policy_mode: str,
    charger_backend: str | None,
    transport_kind: str,
    topology_preset: str | None,
) -> tuple[str, ...]:
    items = ["Auth", "DBus selector pinning"]
    if backend_requires_transport(charger_backend):
        items.append(f"transport wiring ({transport_kind})")
    items.extend(_conditional_review_items(profile, policy_mode, topology_preset))
    return tuple(items)
