# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct topology-model resolution for wizard-generated setups."""

from __future__ import annotations

from venus_evcharger.bootstrap.wizard_models import WizardAnswers
from venus_evcharger.topology import (
    ActuatorConfig,
    ChargerConfig,
    EvChargerTopologyConfig,
    MeasurementConfig,
    PolicyConfig,
    TopologyConfig,
    validate_topology_config,
)


def build_wizard_topology_config(
    answers: WizardAnswers,
) -> EvChargerTopologyConfig:
    """Build one normalized topology config directly from wizard selections."""
    if answers.profile in {"simple_relay", "advanced_manual"}:
        return _simple_relay_topology(answers)
    if answers.profile == "native_device":
        return _native_charger_topology(answers)
    if answers.profile == "hybrid_topology":
        return _phase_switch_topology(answers)
    if answers.profile == "multi_adapter_topology":
        return _split_topology(answers)
    raise ValueError(f"unsupported wizard profile for topology mapping: {answers.profile}")


def _policy(answers: WizardAnswers) -> PolicyConfig:
    return PolicyConfig(mode=answers.policy_mode, phase=answers.phase)


def _simple_relay_topology(answers: WizardAnswers) -> EvChargerTopologyConfig:
    return validate_topology_config(
        EvChargerTopologyConfig(
            topology=TopologyConfig(type="simple_relay"),
            actuator=ActuatorConfig(type="shelly_contactor_switch", config_path=None),
            measurement=MeasurementConfig(type="actuator_native"),
            charger=None,
            policy=_policy(answers),
        )
    )


def _native_charger_topology(
    answers: WizardAnswers,
) -> EvChargerTopologyConfig:
    return validate_topology_config(
        EvChargerTopologyConfig(
            topology=TopologyConfig(type="native_device"),
            actuator=None,
            measurement=MeasurementConfig(type="charger_native"),
            charger=ChargerConfig(
                type=answers.charger_backend or "goe_charger",
                config_path="wizard-charger.ini",
            ),
            policy=_policy(answers),
        )
    )


def _phase_switch_topology(
    answers: WizardAnswers,
) -> EvChargerTopologyConfig:
    return validate_topology_config(
        EvChargerTopologyConfig(
            topology=TopologyConfig(type="hybrid_topology"),
            actuator=ActuatorConfig(
                type="switch_group",
                config_path="wizard-switch-group.ini",
            ),
            measurement=MeasurementConfig(type="charger_native"),
            charger=ChargerConfig(
                type=answers.charger_backend or "simpleevse_charger",
                config_path="wizard-charger.ini",
            ),
            policy=_policy(answers),
        )
    )


def _split_topology(
    answers: WizardAnswers,
) -> EvChargerTopologyConfig:
    topology_preset = answers.topology_preset or "template-stack"
    hybrid_external = _hybrid_external_meter_options(topology_preset)
    if hybrid_external is not None:
        return _hybrid_external_meter_topology(answers, **hybrid_external)
    native_external = _native_external_meter_charger_type(topology_preset)
    if native_external is not None:
        return _native_external_meter_topology(answers, charger_type=native_external)
    charger_native = _hybrid_charger_native_type(topology_preset)
    if charger_native is not None:
        return _hybrid_charger_native_topology(answers, charger_type=charger_native)
    raise ValueError(f"unsupported topology preset for topology mapping: {topology_preset}")


def _hybrid_external_meter_options(topology_preset: str) -> dict[str, str] | None:
    """Return hybrid external-meter builder options for one topology preset."""
    return {
        "template-stack": {
            "actuator_type": "template_switch",
            "switch_config_name": "wizard-switch.ini",
            "charger_type": "template_charger",
        },
        "shelly-io-template-charger": {
            "actuator_type": "shelly_switch",
            "switch_config_name": "wizard-switch.ini",
            "charger_type": "template_charger",
        },
        "shelly-io-modbus-charger": {
            "actuator_type": "shelly_switch",
            "switch_config_name": "wizard-switch.ini",
            "charger_type": "modbus_charger",
        },
        "template-meter-goe-switch-group": {
            "actuator_type": "switch_group",
            "switch_config_name": "wizard-switch-group.ini",
            "charger_type": "goe_charger",
        },
        "shelly-meter-goe-switch-group": {
            "actuator_type": "switch_group",
            "switch_config_name": "wizard-switch-group.ini",
            "charger_type": "goe_charger",
        },
        "shelly-meter-modbus-switch-group": {
            "actuator_type": "switch_group",
            "switch_config_name": "wizard-switch-group.ini",
            "charger_type": "modbus_charger",
        },
    }.get(topology_preset)


def _native_external_meter_charger_type(topology_preset: str) -> str | None:
    """Return charger type for native-device external-meter presets."""
    return {
        "shelly-meter-goe": "goe_charger",
        "shelly-meter-modbus-charger": "modbus_charger",
    }.get(topology_preset)


def _hybrid_charger_native_type(topology_preset: str) -> str | None:
    """Return charger type for hybrid charger-native presets."""
    return {
        "goe-external-switch-group": "goe_charger",
    }.get(topology_preset)


def _native_external_meter_topology(
    answers: WizardAnswers,
    *,
    charger_type: str,
) -> EvChargerTopologyConfig:
    return validate_topology_config(
        EvChargerTopologyConfig(
            topology=TopologyConfig(type="native_device"),
            actuator=None,
            measurement=MeasurementConfig(
                type="external_meter",
                config_path="wizard-meter.ini",
            ),
            charger=ChargerConfig(
                type=charger_type,
                config_path="wizard-charger.ini",
            ),
            policy=_policy(answers),
        )
    )


def _hybrid_charger_native_topology(
    answers: WizardAnswers,
    *,
    charger_type: str,
) -> EvChargerTopologyConfig:
    return validate_topology_config(
        EvChargerTopologyConfig(
            topology=TopologyConfig(type="hybrid_topology"),
            actuator=ActuatorConfig(
                type="switch_group",
                config_path="wizard-switch-group.ini",
            ),
            measurement=MeasurementConfig(type="charger_native"),
            charger=ChargerConfig(
                type=charger_type,
                config_path="wizard-charger.ini",
            ),
            policy=_policy(answers),
        )
    )


def _hybrid_external_meter_topology(
    answers: WizardAnswers,
    *,
    actuator_type: str,
    switch_config_name: str,
    charger_type: str,
) -> EvChargerTopologyConfig:
    return validate_topology_config(
        EvChargerTopologyConfig(
            topology=TopologyConfig(type="hybrid_topology"),
            actuator=ActuatorConfig(
                type=actuator_type,
                config_path=switch_config_name,
            ),
            measurement=MeasurementConfig(
                type="external_meter",
                config_path="wizard-meter.ini",
            ),
            charger=ChargerConfig(
                type=charger_type,
                config_path="wizard-charger.ini",
            ),
            policy=_policy(answers),
        )
    )
