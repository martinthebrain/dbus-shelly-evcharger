# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-interactive answer builders for the setup wizard CLI."""

from __future__ import annotations

import argparse
from typing import cast

from venus_evcharger.bootstrap.wizard_charger_presets import apply_charger_preset_backend, relevant_charger_presets
from venus_evcharger.bootstrap.wizard_guidance import default_backend, resolved_primary_host, role_host_defaults
from venus_evcharger.bootstrap.wizard_import import ImportedWizardDefaults
from venus_evcharger.bootstrap.wizard_models import (
    WizardAnswers,
    WizardChargerBackend,
    WizardPolicyMode,
    WizardProfile,
    WizardTransportKind,
)
from venus_evcharger.bootstrap.wizard_policy_guidance import policy_defaults
from venus_evcharger.bootstrap.wizard_support import host_from_input
from venus_evcharger.bootstrap.wizard_support import (
    backend_requires_transport,
)
from venus_evcharger.bootstrap.wizard_transport_guidance import (
    non_interactive_transport_inputs,
    preset_specific_defaults,
)


def non_interactive_profile(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> WizardProfile:
    profile = cast(WizardProfile | None, namespace.profile or imported_defaults.profile)
    if profile is None:
        raise ValueError("--profile is required in --non-interactive mode unless --import-config/--clone-current provides one")
    return profile


def non_interactive_policy_mode(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> WizardPolicyMode:
    return cast(WizardPolicyMode, namespace.policy_mode or imported_defaults.policy_mode or "manual")


def non_interactive_digest_auth(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> bool:
    if namespace.digest_auth:
        return True
    if imported_defaults.digest_auth is not None:
        return bool(imported_defaults.digest_auth)
    return False


def non_interactive_split_preset(
    namespace: argparse.Namespace,
    imported_defaults: ImportedWizardDefaults,
    profile: WizardProfile,
) -> str | None:
    split_preset = namespace.split_preset or imported_defaults.split_preset
    if profile == "split-topology":
        return split_preset or "template-stack"
    return split_preset


def non_interactive_backend(
    namespace: argparse.Namespace,
    imported: ImportedWizardDefaults | None,
    profile: WizardProfile,
    split_preset: str | None,
) -> WizardChargerBackend | None:
    _ = split_preset
    return cast(WizardChargerBackend | None, namespace.charger_backend or default_backend(profile, imported))


def resolved_backend(
    split_preset: str | None,
    charger_preset: str | None,
    backend: WizardChargerBackend | None,
) -> WizardChargerBackend | None:
    from venus_evcharger.bootstrap.wizard_guidance import apply_split_preset_backend

    return apply_split_preset_backend(split_preset, backend, charger_preset)


def non_interactive_charger_preset(
    namespace: argparse.Namespace,
    imported_defaults: ImportedWizardDefaults,
    backend: WizardChargerBackend | None,
) -> str | None:
    charger_preset = namespace.charger_preset or imported_defaults.charger_preset
    if charger_preset is None:
        return None
    if charger_preset not in relevant_charger_presets(apply_charger_preset_backend(charger_preset, backend)):
        raise ValueError(f"--charger-preset {charger_preset} is not supported for backend {backend or 'none'}")
    return charger_preset


def non_interactive_device_instance(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> int:
    return int(namespace.device_instance if namespace.device_instance is not None else (imported_defaults.device_instance or 60))


def non_interactive_phase(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> str:
    return namespace.phase or imported_defaults.phase or "L1"


def non_interactive_string(namespace_value: str | None, imported_value: str | None) -> str:
    return namespace_value or imported_value or ""


def _non_interactive_transport_answers(
    namespace: argparse.Namespace,
    imported_defaults: ImportedWizardDefaults,
    *,
    backend: WizardChargerBackend | None,
    charger_preset: str | None,
    host_input: str,
    split_preset: str | None,
) -> tuple[WizardTransportKind, str, int, str, int, float | None, str]:
    transport_kind, transport_host, transport_port, transport_device, transport_unit_id = (
        non_interactive_transport_inputs(
            namespace,
            backend,
            charger_preset,
            host_input,
            imported_defaults,
        )
    )
    request_timeout_seconds, switch_group_phase_layout = preset_specific_defaults(
        namespace,
        imported_defaults,
        backend=backend,
        split_preset=split_preset,
        charger_preset=charger_preset,
    )
    return (
        transport_kind,
        transport_host,
        transport_port,
        transport_device,
        transport_unit_id,
        request_timeout_seconds,
        switch_group_phase_layout,
    )


def _non_interactive_policy_answers(
    namespace: argparse.Namespace,
    imported_defaults: ImportedWizardDefaults,
) -> tuple[
    WizardPolicyMode,
    float | None,
    float | None,
    float | None,
    float | None,
    str | None,
    str | None,
    float | None,
]:
    policy_mode = non_interactive_policy_mode(namespace, imported_defaults)
    (
        auto_start_surplus_watts,
        auto_stop_surplus_watts,
        auto_min_soc,
        auto_resume_soc,
        scheduled_enabled_days,
        scheduled_latest_end_time,
        scheduled_night_current_amps,
    ) = policy_defaults(policy_mode, imported_defaults, namespace)
    return (
        policy_mode,
        auto_start_surplus_watts,
        auto_stop_surplus_watts,
        auto_min_soc,
        auto_resume_soc,
        scheduled_enabled_days,
        scheduled_latest_end_time,
        scheduled_night_current_amps,
    )


def _effective_transport_answers(
    backend: WizardChargerBackend | None,
    host_input: str,
    transport_kind: WizardTransportKind,
    transport_host: str,
    transport_port: int,
    transport_device: str,
    transport_unit_id: int,
) -> tuple[WizardTransportKind, str, int, str, int]:
    if backend_requires_transport(backend):
        return (
            transport_kind,
            transport_host,
            transport_port,
            transport_device,
            transport_unit_id,
        )
    return ("serial_rtu", host_from_input(host_input), 502, "/dev/ttyUSB0", 1)


def non_interactive_answers(
    namespace: argparse.Namespace,
    imported_defaults: ImportedWizardDefaults,
) -> WizardAnswers:
    profile = non_interactive_profile(namespace, imported_defaults)
    shared_host = namespace.host or imported_defaults.host_input or "192.168.1.50"
    split_preset = non_interactive_split_preset(namespace, imported_defaults, profile)
    backend = non_interactive_backend(namespace, imported_defaults, profile, split_preset)
    charger_preset = non_interactive_charger_preset(namespace, imported_defaults, backend)
    backend = resolved_backend(split_preset, charger_preset, backend)
    meter_host, switch_host, charger_host = role_host_defaults(namespace, imported_defaults, profile, split_preset, shared_host)
    host_input = resolved_primary_host(namespace, imported_defaults, meter_host, switch_host, charger_host)
    (
        transport_kind,
        transport_host,
        transport_port,
        transport_device,
        transport_unit_id,
        request_timeout_seconds,
        switch_group_phase_layout,
    ) = _non_interactive_transport_answers(
        namespace,
        imported_defaults,
        backend=backend,
        charger_preset=charger_preset,
        host_input=host_input,
        split_preset=split_preset,
    )
    (
        policy_mode,
        auto_start_surplus_watts,
        auto_stop_surplus_watts,
        auto_min_soc,
        auto_resume_soc,
        scheduled_enabled_days,
        scheduled_latest_end_time,
        scheduled_night_current_amps,
    ) = _non_interactive_policy_answers(namespace, imported_defaults)
    transport_kind, transport_host, transport_port, transport_device, transport_unit_id = (
        _effective_transport_answers(
            backend,
            host_input,
            transport_kind,
            transport_host,
            transport_port,
            transport_device,
            transport_unit_id,
        )
    )
    return WizardAnswers(
        profile=profile,
        host_input=host_input,
        meter_host_input=meter_host,
        switch_host_input=switch_host,
        charger_host_input=charger_host,
        device_instance=non_interactive_device_instance(namespace, imported_defaults),
        phase=non_interactive_phase(namespace, imported_defaults),
        policy_mode=policy_mode,
        digest_auth=non_interactive_digest_auth(namespace, imported_defaults),
        username=non_interactive_string(namespace.username, imported_defaults.username),
        password=non_interactive_string(namespace.password, imported_defaults.password),
        split_preset=split_preset,
        charger_backend=backend,
        charger_preset=charger_preset,
        request_timeout_seconds=request_timeout_seconds,
        switch_group_supported_phase_selections=switch_group_phase_layout,
        auto_start_surplus_watts=auto_start_surplus_watts,
        auto_stop_surplus_watts=auto_stop_surplus_watts,
        auto_min_soc=auto_min_soc,
        auto_resume_soc=auto_resume_soc,
        scheduled_enabled_days=scheduled_enabled_days,
        scheduled_latest_end_time=scheduled_latest_end_time,
        scheduled_night_current_amps=scheduled_night_current_amps,
        transport_kind=transport_kind,
        transport_host=transport_host,
        transport_port=transport_port,
        transport_device=transport_device,
        transport_unit_id=transport_unit_id,
    )
