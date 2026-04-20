# SPDX-License-Identifier: GPL-3.0-or-later
"""Low-level adapter config rendering helpers for the setup wizard."""

from __future__ import annotations


def serial_transport_block(device: str, unit_id: int) -> str:
    return (
        "[Transport]\n"
        f"Device={device}\n"
        "Baudrate=9600\n"
        "Parity=N\n"
        "StopBits=1\n"
        f"UnitId={unit_id}\n"
    )


def tcp_transport_block(host: str, port: int, unit_id: int) -> str:
    return "[Transport]\n" f"Host={host}\n" f"Port={port}\n" f"UnitId={unit_id}\n"


def transport_block(
    transport_kind: str,
    *,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> str:
    if transport_kind == "tcp":
        return tcp_transport_block(transport_host, transport_port, transport_unit_id)
    return serial_transport_block(transport_device, transport_unit_id)


def template_meter_config(config_base_url: str) -> str:
    return (
        "[Adapter]\n"
        "Type=template_meter\n"
        f"BaseUrl={config_base_url}\n"
        "[MeterRequest]\n"
        "Url=/wizard/meter\n"
        "[MeterResponse]\n"
        "PowerPath=power_watts\n"
    )


def template_switch_config(config_base_url: str, prefix: str) -> str:
    return (
        "[Adapter]\n"
        "Type=template_switch\n"
        f"BaseUrl={config_base_url}\n"
        "[StateRequest]\n"
        f"Url={prefix}/state\n"
        "[StateResponse]\n"
        "EnabledPath=enabled\n"
        "[CommandRequest]\n"
        f"Url={prefix}/control\n"
    )


def switch_group_config(supported_phase_selections: str) -> str:
    return (
        "[Adapter]\nType=switch_group\n"
        "[Members]\nP1=wizard-phase1-switch.ini\nP2=wizard-phase2-switch.ini\nP3=wizard-phase3-switch.ini\n"
        "[Capabilities]\n"
        f"SupportedPhaseSelections={supported_phase_selections}\n"
    )


def template_switch_group_files(config_base_url: str, supported_phase_selections: str) -> dict[str, str]:
    return {
        "wizard-switch-group.ini": switch_group_config(supported_phase_selections),
        "wizard-phase1-switch.ini": template_switch_config(config_base_url, "/wizard/phase1"),
        "wizard-phase2-switch.ini": template_switch_config(config_base_url, "/wizard/phase2"),
        "wizard-phase3-switch.ini": template_switch_config(config_base_url, "/wizard/phase3"),
    }


def template_charger_config(config_base_url: str) -> str:
    return (
        "[Adapter]\n"
        "Type=template_charger\n"
        f"BaseUrl={config_base_url}\n"
        "[EnableRequest]\n"
        "Url=/wizard/charger/enable\n"
        "[CurrentRequest]\n"
        "Url=/wizard/charger/current\n"
    )


def shelly_meter_config(host: str) -> str:
    return (
        "[Adapter]\n"
        "Type=shelly_meter\n"
        f"Host={host}\n"
        "ShellyProfile=em1_meter_single_or_dual\n"
    )


def shelly_switch_config(host: str) -> str:
    return (
        "[Adapter]\n"
        "Type=shelly_switch\n"
        f"Host={host}\n"
        "ShellyProfile=switch_1ch_with_pm\n"
    )


def modbus_charger_config(
    transport_kind: str,
    *,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> str:
    return (
        "[Adapter]\n"
        "Type=modbus_charger\n"
        "Profile=generic\n"
        f"Transport={transport_kind}\n"
        f"{transport_block(transport_kind, transport_host=transport_host, transport_port=transport_port, transport_device=transport_device, transport_unit_id=transport_unit_id)}"
        "[EnableWrite]\n"
        "RegisterType=coil\n"
        "Address=20\n"
        "TrueValue=1\n"
        "FalseValue=0\n"
        "[CurrentWrite]\n"
        "RegisterType=holding\n"
        "Address=30\n"
        "DataType=uint16\n"
        "Scale=10\n"
    )


def native_charger_config(
    backend: str,
    config_base_url: str,
    *,
    request_timeout_seconds: float | None,
    transport_kind: str,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> str:
    timeout_line = (
        f"RequestTimeoutSeconds={float(request_timeout_seconds):g}\n" if request_timeout_seconds is not None else ""
    )
    if backend == "goe_charger":
        return f"[Adapter]\nType=goe_charger\nBaseUrl={config_base_url}\n{timeout_line}"
    if backend == "template_charger":
        return template_charger_config(config_base_url)
    if backend == "modbus_charger":
        return modbus_charger_config(
            transport_kind,
            transport_host=transport_host,
            transport_port=transport_port,
            transport_device=transport_device,
            transport_unit_id=transport_unit_id,
        )
    return (
        "[Adapter]\n"
        f"Type={backend}\n"
        f"{timeout_line}"
        f"Transport={transport_kind}\n"
        f"{transport_block(transport_kind, transport_host=transport_host, transport_port=transport_port, transport_device=transport_device, transport_unit_id=transport_unit_id)}"
    )
