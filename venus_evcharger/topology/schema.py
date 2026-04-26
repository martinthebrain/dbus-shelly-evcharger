# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalized topology schema for controllable load setups."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

TopologyType = Literal["simple_relay", "native_device", "hybrid_topology", "custom_topology"]
PolicyMode = Literal["manual", "auto", "scheduled"]

ActuatorType = Literal[
    "shelly_switch",
    "shelly_contactor_switch",
    "template_switch",
    "tasmota_switch",
    "switch_group",
    "custom",
]

MeasurementType = Literal[
    "actuator_native",
    "charger_native",
    "external_meter",
    "fixed_reference",
    "learned_reference",
    "none",
]

ChargerType = Literal[
    "goe_charger",
    "simpleevse_charger",
    "smartevse_charger",
    "modbus_charger",
    "template_charger",
    "custom",
]


@dataclass(frozen=True)
class TopologyConfig:
    type: TopologyType


@dataclass(frozen=True)
class ActuatorConfig:
    type: ActuatorType
    config_path: str | None = None


@dataclass(frozen=True)
class MeasurementConfig:
    type: MeasurementType
    config_path: str | None = None
    reference_watts: float | None = None
    allow_auto_estimate: bool = False


@dataclass(frozen=True)
class ChargerConfig:
    type: ChargerType
    config_path: str | None = None


@dataclass(frozen=True)
class PolicyConfig:
    mode: PolicyMode = "manual"
    phase: str = "L1"


@dataclass(frozen=True)
class EvChargerTopologyConfig:
    topology: TopologyConfig
    actuator: ActuatorConfig | None = None
    measurement: MeasurementConfig | None = None
    charger: ChargerConfig | None = None
    policy: PolicyConfig = field(default_factory=PolicyConfig)
