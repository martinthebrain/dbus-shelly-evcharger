# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapter-file rendering from the normalized wizard topology model."""

from __future__ import annotations

from venus_evcharger.bootstrap.wizard_adapters import (
    modbus_charger_config,
    native_charger_config,
    shelly_meter_config,
    shelly_switch_config,
    template_charger_config,
    template_meter_config,
    template_switch_config,
    template_switch_group_files,
)
from venus_evcharger.bootstrap.wizard_models import WizardAnswers
from venus_evcharger.bootstrap.wizard_support import base_url_from_input, host_from_input
from venus_evcharger.topology import EvChargerTopologyConfig


def render_adapter_files_from_topology(
    topology_config: EvChargerTopologyConfig,
    answers: WizardAnswers,
    role_hosts: dict[str, str],
) -> dict[str, str]:
    if topology_config.topology.type == "simple_relay":
        return {}
    if topology_config.topology.type == "native_device":
        return _native_device_files(topology_config, answers, role_hosts)
    if topology_config.topology.type == "hybrid_topology":
        return _hybrid_topology_files(topology_config, answers, role_hosts)
    return {}


def _native_device_files(
    topology_config: EvChargerTopologyConfig,
    answers: WizardAnswers,
    role_hosts: dict[str, str],
) -> dict[str, str]:
    files = {
        "wizard-charger.ini": _charger_config_text(topology_config.charger.type, answers, role_hosts),
    }
    if topology_config.measurement is not None and topology_config.measurement.type == "external_meter":
        files["wizard-meter.ini"] = _measurement_config_text(answers, role_hosts)
    return files


def _hybrid_topology_files(
    topology_config: EvChargerTopologyConfig,
    answers: WizardAnswers,
    role_hosts: dict[str, str],
) -> dict[str, str]:
    files = {
        "wizard-charger.ini": _charger_config_text(topology_config.charger.type, answers, role_hosts),
    }
    if topology_config.measurement is not None and topology_config.measurement.type == "external_meter":
        files["wizard-meter.ini"] = _measurement_config_text(answers, role_hosts)
    files.update(_actuator_files(topology_config, role_hosts, answers))
    return files


def _charger_config_text(charger_type: str, answers: WizardAnswers, role_hosts: dict[str, str]) -> str:
    charger_base_url = base_url_from_input(role_hosts.get("charger", answers.host_input))
    if charger_type == "template_charger":
        return template_charger_config(charger_base_url)
    if charger_type == "modbus_charger" and answers.charger_preset is None:
        return modbus_charger_config(
            answers.transport_kind,
            transport_host=answers.transport_host,
            transport_port=answers.transport_port,
            transport_device=answers.transport_device,
            transport_unit_id=answers.transport_unit_id,
        )
    return native_charger_config(
        charger_type,
        charger_base_url if charger_type != "modbus_charger" else "",
        charger_preset=answers.charger_preset,
        request_timeout_seconds=answers.request_timeout_seconds,
        transport_kind=answers.transport_kind,
        transport_host=answers.transport_host,
        transport_port=answers.transport_port,
        transport_device=answers.transport_device,
        transport_unit_id=answers.transport_unit_id,
    )


def _measurement_config_text(answers: WizardAnswers, role_hosts: dict[str, str]) -> str:
    meter_host = role_hosts.get("meter", answers.host_input)
    meter_base_url = base_url_from_input(meter_host)
    topology_preset = answers.topology_preset or ("template-stack" if answers.profile == "multi_adapter_topology" else None)
    if topology_preset in {"template-stack", "template-meter-goe-switch-group"}:
        return template_meter_config(meter_base_url)
    return shelly_meter_config(host_from_input(meter_host))


def _actuator_files(
    topology_config: EvChargerTopologyConfig,
    role_hosts: dict[str, str],
    answers: WizardAnswers,
) -> dict[str, str]:
    actuator = topology_config.actuator
    if actuator is None:
        return {}
    switch_host = role_hosts.get("switch", answers.host_input)
    switch_base_url = base_url_from_input(switch_host)
    if actuator.type == "switch_group":
        return template_switch_group_files(switch_base_url, answers.switch_group_supported_phase_selections)
    if actuator.type == "template_switch":
        return {"wizard-switch.ini": template_switch_config(switch_base_url, "/wizard/switch")}
    if actuator.type in {"shelly_switch", "shelly_contactor_switch"}:
        return {"wizard-switch.ini": shelly_switch_config(host_from_input(switch_host))}
    return {}
