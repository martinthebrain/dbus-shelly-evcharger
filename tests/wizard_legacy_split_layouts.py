# SPDX-License-Identifier: GPL-3.0-or-later
"""Legacy split-layout helpers kept only for branch-coverage tests."""

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
from venus_evcharger.bootstrap.wizard_support import host_from_input


def split_topology_files(
    *,
    topology_preset: str | None,
    role_hosts: dict[str, str],
    meter_base_url: str,
    switch_base_url: str,
    charger_base_url: str,
    charger_preset: str | None,
    request_timeout_seconds: float | None,
    switch_group_supported_phase_selections: str,
    transport_kind: str,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    simple_variant = _simple_split_variant(
        topology_preset,
        role_hosts,
        charger_base_url,
        charger_preset,
        request_timeout_seconds,
        transport_kind,
        transport_host,
        transport_port,
        transport_device,
        transport_unit_id,
    )
    if simple_variant is not None:
        return simple_variant
    if topology_preset in {"goe-external-switch-group", "template-meter-goe-switch-group", "shelly-meter-goe-switch-group"}:
        return _goe_switch_group_variant(
            topology_preset,
            role_hosts,
            meter_base_url,
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
    if topology_preset == "shelly-meter-modbus-switch-group":
        return _shelly_meter_modbus_switch_group(
            role_hosts,
            switch_base_url,
            charger_preset,
            switch_group_supported_phase_selections,
            transport_kind,
            transport_host,
            transport_port,
            transport_device,
            transport_unit_id,
        )
    return _template_stack_files(role_hosts, meter_base_url, switch_base_url, charger_base_url)


def _simple_split_variant(
    topology_preset: str | None,
    role_hosts: dict[str, str],
    charger_base_url: str,
    charger_preset: str | None,
    request_timeout_seconds: float | None,
    transport_kind: str,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> tuple[list[str], dict[str, str], dict[str, str]] | None:
    if topology_preset == "shelly-io-template-charger":
        return _shelly_io_template_files(role_hosts, charger_base_url)
    if topology_preset == "shelly-io-modbus-charger":
        return _shelly_io_modbus_files(
            role_hosts,
            charger_preset,
            transport_kind,
            transport_host,
            transport_port,
            transport_device,
            transport_unit_id,
        )
    if topology_preset == "shelly-meter-goe":
        return _shelly_meter_goe_files(
            role_hosts,
            charger_base_url,
            charger_preset,
            request_timeout_seconds,
            transport_kind,
            transport_host,
            transport_port,
            transport_device,
            transport_unit_id,
        )
    if topology_preset == "shelly-meter-modbus-charger":
        return _shelly_meter_modbus_files(
            role_hosts,
            charger_preset,
            transport_kind,
            transport_host,
            transport_port,
            transport_device,
            transport_unit_id,
        )
    return None


def _shelly_io_template_files(role_hosts: dict[str, str], charger_base_url: str) -> tuple[list[str], dict[str, str], dict[str, str]]:
    return (
        ["Mode=split", "MeterType=shelly_meter", "MeterConfigPath=wizard-meter.ini", "SwitchType=shelly_switch", "SwitchConfigPath=wizard-switch.ini", "ChargerType=template_charger", "ChargerConfigPath=wizard-charger.ini"],
        {
            "wizard-meter.ini": shelly_meter_config(host_from_input(role_hosts["meter"])),
            "wizard-switch.ini": shelly_switch_config(host_from_input(role_hosts["switch"])),
            "wizard-charger.ini": template_charger_config(charger_base_url),
        },
        role_hosts,
    )


def _shelly_io_modbus_files(
    role_hosts: dict[str, str],
    charger_preset: str | None,
    transport_kind: str,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    return (
        ["Mode=split", "MeterType=shelly_meter", "MeterConfigPath=wizard-meter.ini", "SwitchType=shelly_switch", "SwitchConfigPath=wizard-switch.ini", "ChargerType=modbus_charger", "ChargerConfigPath=wizard-charger.ini"],
        {
            "wizard-meter.ini": shelly_meter_config(host_from_input(role_hosts["meter"])),
            "wizard-switch.ini": shelly_switch_config(host_from_input(role_hosts["switch"])),
            "wizard-charger.ini": modbus_charger_config(
                transport_kind,
                transport_host=transport_host,
                transport_port=transport_port,
                transport_device=transport_device,
                transport_unit_id=transport_unit_id,
            ) if charger_preset is None else native_charger_config(
                "modbus_charger",
                "",
                charger_preset=charger_preset,
                request_timeout_seconds=None,
                transport_kind=transport_kind,
                transport_host=transport_host,
                transport_port=transport_port,
                transport_device=transport_device,
                transport_unit_id=transport_unit_id,
            ),
        },
        role_hosts,
    )


def _shelly_meter_goe_files(
    role_hosts: dict[str, str],
    charger_base_url: str,
    charger_preset: str | None,
    request_timeout_seconds: float | None,
    transport_kind: str,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    return (
        ["Mode=split", "MeterType=shelly_meter", "MeterConfigPath=wizard-meter.ini", "SwitchType=none", "ChargerType=goe_charger", "ChargerConfigPath=wizard-charger.ini"],
        {
            "wizard-meter.ini": shelly_meter_config(host_from_input(role_hosts["meter"])),
            "wizard-charger.ini": native_charger_config(
                "goe_charger",
                charger_base_url,
                charger_preset=charger_preset,
                request_timeout_seconds=request_timeout_seconds,
                transport_kind=transport_kind,
                transport_host=transport_host,
                transport_port=transport_port,
                transport_device=transport_device,
                transport_unit_id=transport_unit_id,
            ),
        },
        role_hosts,
    )


def _goe_switch_group_variant(
    topology_preset: str,
    role_hosts: dict[str, str],
    meter_base_url: str,
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
    meter_type, meter_config = _goe_switch_group_meter(topology_preset, role_hosts, meter_base_url)
    backend_lines = ["Mode=split", f"MeterType={meter_type}"]
    files = {
        "wizard-charger.ini": native_charger_config(
            "goe_charger",
            charger_base_url,
            charger_preset=None,
            request_timeout_seconds=request_timeout_seconds,
            transport_kind=transport_kind,
            transport_host=transport_host,
            transport_port=transport_port,
            transport_device=transport_device,
            transport_unit_id=transport_unit_id,
        )
    }
    if meter_config is not None:
        backend_lines.append("MeterConfigPath=wizard-meter.ini")
        files["wizard-meter.ini"] = meter_config
    backend_lines.extend(
        [
            "SwitchType=switch_group",
            "SwitchConfigPath=wizard-switch-group.ini",
            "ChargerType=goe_charger",
            "ChargerConfigPath=wizard-charger.ini",
        ]
    )
    files.update(template_switch_group_files(switch_base_url, switch_group_supported_phase_selections))
    return backend_lines, files, role_hosts


def _goe_switch_group_meter(
    topology_preset: str,
    role_hosts: dict[str, str],
    meter_base_url: str,
) -> tuple[str, str | None]:
    if topology_preset == "goe-external-switch-group":
        return "none", None
    if topology_preset == "template-meter-goe-switch-group":
        return "template_meter", template_meter_config(meter_base_url)
    return "shelly_meter", shelly_meter_config(host_from_input(role_hosts["meter"]))


def _shelly_meter_modbus_switch_group(
    role_hosts: dict[str, str],
    switch_base_url: str,
    charger_preset: str | None,
    switch_group_supported_phase_selections: str,
    transport_kind: str,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    files = {
        "wizard-meter.ini": shelly_meter_config(host_from_input(role_hosts["meter"])),
        "wizard-charger.ini": modbus_charger_config(
            transport_kind,
            transport_host=transport_host,
            transport_port=transport_port,
            transport_device=transport_device,
            transport_unit_id=transport_unit_id,
        ) if charger_preset is None else native_charger_config(
            "modbus_charger",
            "",
            charger_preset=charger_preset,
            request_timeout_seconds=None,
            transport_kind=transport_kind,
            transport_host=transport_host,
            transport_port=transport_port,
            transport_device=transport_device,
            transport_unit_id=transport_unit_id,
        ),
    }
    files.update(template_switch_group_files(switch_base_url, switch_group_supported_phase_selections))
    return (
        ["Mode=split", "MeterType=shelly_meter", "MeterConfigPath=wizard-meter.ini", "SwitchType=switch_group", "SwitchConfigPath=wizard-switch-group.ini", "ChargerType=modbus_charger", "ChargerConfigPath=wizard-charger.ini"],
        files,
        role_hosts,
    )


def _shelly_meter_modbus_files(
    role_hosts: dict[str, str],
    charger_preset: str | None,
    transport_kind: str,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    charger_config = modbus_charger_config(
        transport_kind,
        transport_host=transport_host,
        transport_port=transport_port,
        transport_device=transport_device,
        transport_unit_id=transport_unit_id,
    )
    if charger_preset is not None:
        charger_config = native_charger_config(
            "modbus_charger",
            "",
            charger_preset=charger_preset,
            request_timeout_seconds=None,
            transport_kind=transport_kind,
            transport_host=transport_host,
            transport_port=transport_port,
            transport_device=transport_device,
            transport_unit_id=transport_unit_id,
        )
    return (
        ["Mode=split", "MeterType=shelly_meter", "MeterConfigPath=wizard-meter.ini", "SwitchType=none", "ChargerType=modbus_charger", "ChargerConfigPath=wizard-charger.ini"],
        {
            "wizard-meter.ini": shelly_meter_config(host_from_input(role_hosts["meter"])),
            "wizard-charger.ini": charger_config,
        },
        role_hosts,
    )


def _template_stack_files(
    role_hosts: dict[str, str],
    meter_base_url: str,
    switch_base_url: str,
    charger_base_url: str,
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    return (
        ["Mode=split", "MeterType=template_meter", "MeterConfigPath=wizard-meter.ini", "SwitchType=template_switch", "SwitchConfigPath=wizard-switch.ini", "ChargerType=template_charger", "ChargerConfigPath=wizard-charger.ini"],
        {
            "wizard-meter.ini": template_meter_config(meter_base_url),
            "wizard-switch.ini": template_switch_config(switch_base_url, "/wizard/switch"),
            "wizard-charger.ini": template_charger_config(charger_base_url),
        },
        role_hosts,
    )
