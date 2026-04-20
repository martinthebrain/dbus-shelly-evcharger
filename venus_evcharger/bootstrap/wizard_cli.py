# SPDX-License-Identifier: GPL-3.0-or-later
"""CLI, prompt, and answer-building helpers for the setup wizard."""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path
from typing import cast

from venus_evcharger.bootstrap.wizard_charger_presets import (
    CHARGER_PRESET_LABELS,
    CHARGER_PRESET_VALUES,
    apply_charger_preset_backend,
    relevant_charger_presets,
)
from venus_evcharger.bootstrap.wizard_cli_output import result_text
from venus_evcharger.bootstrap.wizard_guidance import (
    default_backend,
    prompt_role_hosts,
    prompt_split_preset,
    resolved_primary_host,
    role_host_defaults,
    role_prompt_intro,
)
from venus_evcharger.bootstrap.wizard_import import ImportedWizardDefaults, load_imported_defaults
from venus_evcharger.bootstrap.wizard_models import (
    WizardAnswers,
    WizardChargerBackend,
    WizardPolicyMode,
    WizardProfile,
    WizardTransportKind,
)
from venus_evcharger.bootstrap.wizard_policy_guidance import policy_defaults, prompt_policy_defaults
from venus_evcharger.bootstrap.wizard_support import (
    NATIVE_CHARGER_VALUES,
    PHASE_SWITCH_CHARGER_VALUES,
    POLICY_VALUES,
    PROFILE_LABELS,
    PROFILE_VALUES,
    SPLIT_PRESET_VALUES,
    TRANSPORT_VALUES,
    backend_requires_transport,
    host_from_input,
)
from venus_evcharger.bootstrap.wizard_transport_guidance import (
    SWITCH_GROUP_PHASE_LAYOUT_VALUES,
    non_interactive_transport_inputs,
    preset_specific_defaults,
    prompt_preset_specific_defaults,
    prompt_transport_inputs,
)

__all__ = ["build_answers", "build_parser", "prompt_yes_no", "result_text"]


def _empty_imported_defaults() -> ImportedWizardDefaults:
    return ImportedWizardDefaults(
        imported_from="",
        profile=None,
        host_input=None,
        meter_host_input=None,
        switch_host_input=None,
        charger_host_input=None,
        device_instance=None,
        phase=None,
        policy_mode=None,
        digest_auth=None,
        username=None,
        password=None,
        split_preset=None,
        charger_backend=None,
        charger_preset=None,
        request_timeout_seconds=None,
        switch_group_phase_layout=None,
        auto_start_surplus_watts=None,
        auto_stop_surplus_watts=None,
        auto_min_soc=None,
        auto_resume_soc=None,
        scheduled_enabled_days=None,
        scheduled_latest_end_time=None,
        scheduled_night_current_amps=None,
        transport_kind=None,
        transport_host=None,
        transport_port=None,
        transport_device=None,
        transport_unit_id=None,
    )


def _prompt_text(prompt: str, default: str) -> str:
    value = input(f"{prompt} [{default}]: ").strip()
    return value or default


def _prompt_password(default: str) -> str:
    if default and prompt_yes_no("Reuse imported password?", True):
        return default
    return getpass.getpass("Password: ")


def prompt_yes_no(prompt: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    value = input(f"{prompt} [{suffix}]: ").strip().lower()
    if not value:
        return default
    return value in ("y", "yes", "1", "true", "on")


def _choice_from_raw(raw: str, choices: tuple[str, ...]) -> str | None:
    if raw.isdigit():
        numeric = int(raw)
        if 1 <= numeric <= len(choices):
            return choices[numeric - 1]
    return raw if raw in choices else None


def _prompt_choice_input(default: str | None) -> str:
    return input(f"Select [{default or 1}]: ").strip()


def _prompt_choice(prompt: str, choices: tuple[str, ...], labels: dict[str, str] | None = None, default: str | None = None) -> str:
    print(prompt)
    for index, choice in enumerate(choices, start=1):
        label = labels.get(choice, choice) if labels is not None else choice
        print(f"  {index}. {label}")
    while True:
        resolved = _resolved_choice_input(_prompt_choice_input(default), choices, default)
        if resolved is not None:
            return resolved
        print("Invalid selection, please try again.")


def _resolved_choice_input(raw: str, choices: tuple[str, ...], default: str | None) -> str | None:
    if not raw and default is not None:
        return default
    return _choice_from_raw(raw, choices)


def _resume_import_path(namespace: argparse.Namespace) -> Path | None:
    if not namespace.resume_last:
        return None
    candidate = Path(f"{namespace.config_path}.wizard-result.json")
    if not candidate.exists():
        raise ValueError(f"--resume-last requested but no prior wizard result exists: {candidate}")
    return candidate


def _clone_import_path(namespace: argparse.Namespace) -> Path | None:
    if not namespace.clone_current:
        return None
    candidate = Path(namespace.config_path)
    if not candidate.exists():
        raise ValueError(f"--clone-current requested but config does not exist: {candidate}")
    return candidate


def _resolve_import_path(namespace: argparse.Namespace) -> Path | None:
    if namespace.import_config:
        return Path(namespace.import_config)
    return _resume_import_path(namespace) or _clone_import_path(namespace)


def resolve_imported_defaults(namespace: argparse.Namespace) -> ImportedWizardDefaults | None:
    import_path = _resolve_import_path(namespace)
    if import_path is None:
        return None
    return load_imported_defaults(import_path)


def _interactive_profile(namespace: argparse.Namespace, imported: ImportedWizardDefaults) -> WizardProfile:
    labels: dict[str, str] = {key: value for key, value in PROFILE_LABELS}
    return cast(
        WizardProfile,
        namespace.profile or imported.profile or _prompt_choice("Choose the setup type:", PROFILE_VALUES, labels, "simple-relay"),
    )


def _interactive_backend(
    namespace: argparse.Namespace,
    profile: WizardProfile,
    imported: ImportedWizardDefaults,
    split_preset: str | None,
) -> WizardChargerBackend | None:
    backend = cast(WizardChargerBackend | None, namespace.charger_backend or default_backend(profile, imported))
    if namespace.charger_backend is None:
        backend = _interactive_backend_choice(profile, backend)
    return backend


def _interactive_backend_choice(profile: WizardProfile, backend: WizardChargerBackend | None) -> WizardChargerBackend | None:
    if profile == "native-charger":
        return cast(WizardChargerBackend, _prompt_choice("Choose the charger backend:", NATIVE_CHARGER_VALUES, default=backend or "goe_charger"))
    if profile == "native-charger-phase-switch":
        return cast(
            WizardChargerBackend,
            _prompt_choice("Choose the charger backend:", PHASE_SWITCH_CHARGER_VALUES, default=backend or "simpleevse_charger"),
        )
    return backend


def _interactive_auth_inputs(namespace: argparse.Namespace, imported: ImportedWizardDefaults) -> tuple[bool, str, str]:
    digest_auth = _interactive_digest_auth(namespace, imported)
    username = _interactive_username(namespace, imported, digest_auth)
    password = _interactive_password(namespace, imported, digest_auth)
    return digest_auth, username, password


def _interactive_digest_auth(namespace: argparse.Namespace, imported: ImportedWizardDefaults) -> bool:
    if namespace.digest_auth:
        return True
    return prompt_yes_no("Does this setup require authentication?", bool(imported.digest_auth)) if namespace.digest_auth is False else False


def _interactive_username(namespace: argparse.Namespace, imported: ImportedWizardDefaults, digest_auth: bool) -> str:
    username = namespace.username or imported.username or ""
    if _should_prompt_username(namespace, digest_auth):
        return _prompt_text("Username", username or "admin")
    return username


def _interactive_password(namespace: argparse.Namespace, imported: ImportedWizardDefaults, digest_auth: bool) -> str:
    password = namespace.password or imported.password or ""
    if digest_auth and namespace.password is None:
        return _prompt_password(password)
    return password


def _should_prompt_username(namespace: argparse.Namespace, digest_auth: bool) -> bool:
    return digest_auth and namespace.username is None


def _interactive_policy_mode(namespace: argparse.Namespace, imported: ImportedWizardDefaults) -> WizardPolicyMode:
    return cast(
        WizardPolicyMode,
        namespace.policy_mode or _prompt_choice("Choose the initial policy mode:", POLICY_VALUES, default=imported.policy_mode or "manual"),
    )


def _interactive_device_instance(namespace: argparse.Namespace, imported: ImportedWizardDefaults) -> int:
    if namespace.device_instance is not None:
        return int(namespace.device_instance)
    return int(_prompt_text("DeviceInstance", str(imported.device_instance or 60)))


def _interactive_phase(namespace: argparse.Namespace, imported: ImportedWizardDefaults) -> str:
    return namespace.phase or _prompt_choice("Choose the phase baseline:", ("L1", "L2", "L3", "3P"), default=imported.phase or "L1")


def _interactive_answers(namespace: argparse.Namespace, imported: ImportedWizardDefaults | None) -> WizardAnswers:
    imported = imported or _empty_imported_defaults()
    profile = _interactive_profile(namespace, imported)
    shared_host = namespace.host or imported.host_input or _prompt_text("Primary host or IP", "192.168.1.50")
    split_preset = _interactive_split_preset(namespace, imported, profile)
    backend = _interactive_backend(namespace, profile, imported, split_preset)
    charger_preset = _interactive_charger_preset(namespace, imported, backend)
    backend = _resolved_backend(split_preset, charger_preset, backend)
    intro = role_prompt_intro(profile, split_preset)
    if intro:
        print(intro)
    meter_host, switch_host, charger_host = prompt_role_hosts(
        namespace,
        imported,
        profile,
        split_preset,
        shared_host,
        prompt_text=_prompt_text,
    )
    host_input = resolved_primary_host(namespace, imported, meter_host, switch_host, charger_host)
    transport_kind, transport_host, transport_port, transport_device, transport_unit_id = _interactive_transport_inputs(
        backend,
        charger_preset,
        host_input,
        imported,
    )
    digest_auth, username, password = _interactive_auth_inputs(namespace, imported)
    policy_mode = _interactive_policy_mode(namespace, imported)
    request_timeout_seconds, switch_group_phase_layout = prompt_preset_specific_defaults(
        namespace,
        imported,
        profile=profile,
        backend=backend,
        split_preset=split_preset,
        charger_preset=charger_preset,
        prompt_choice=_prompt_choice,
        prompt_text=_prompt_text,
    )
    (
        auto_start_surplus_watts,
        auto_stop_surplus_watts,
        auto_min_soc,
        auto_resume_soc,
        scheduled_enabled_days,
        scheduled_latest_end_time,
        scheduled_night_current_amps,
    ) = prompt_policy_defaults(policy_mode, imported, namespace, prompt_text=_prompt_text)
    return WizardAnswers(
        profile=profile,
        host_input=host_input,
        meter_host_input=meter_host,
        switch_host_input=switch_host,
        charger_host_input=charger_host,
        device_instance=_interactive_device_instance(namespace, imported),
        phase=_interactive_phase(namespace, imported),
        policy_mode=policy_mode,
        digest_auth=digest_auth,
        username=username,
        password=password,
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


def _interactive_split_preset(namespace: argparse.Namespace, imported: ImportedWizardDefaults, profile: WizardProfile) -> str | None:
    split_preset = namespace.split_preset or imported.split_preset
    if profile == "split-topology" and split_preset is None:
        return prompt_split_preset(_prompt_choice, imported.split_preset or "template-stack")
    return split_preset


def _interactive_transport_inputs(
    backend: WizardChargerBackend | None,
    charger_preset: str | None,
    host_input: str,
    imported: ImportedWizardDefaults,
) -> tuple[WizardTransportKind, str, int, str, int]:
    if backend_requires_transport(backend):
        return prompt_transport_inputs(
            backend,
            charger_preset,
            host_input,
            imported,
            prompt_choice=_prompt_choice,
            prompt_text=_prompt_text,
        )
    return "serial_rtu", host_from_input(host_input), 502, "/dev/ttyUSB0", 1


def _non_interactive_profile(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> WizardProfile:
    profile = cast(WizardProfile | None, namespace.profile or imported_defaults.profile)
    if profile is None:
        raise ValueError("--profile is required in --non-interactive mode unless --import-config/--clone-current provides one")
    return profile


def _non_interactive_policy_mode(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> WizardPolicyMode:
    return cast(WizardPolicyMode, namespace.policy_mode or imported_defaults.policy_mode or "manual")


def _non_interactive_digest_auth(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> bool:
    if namespace.digest_auth:
        return True
    if imported_defaults.digest_auth is not None:
        return bool(imported_defaults.digest_auth)
    return False


def _non_interactive_answers(namespace: argparse.Namespace, imported: ImportedWizardDefaults | None) -> WizardAnswers:
    imported_defaults = imported or _empty_imported_defaults()
    profile = _non_interactive_profile(namespace, imported_defaults)
    shared_host = namespace.host or imported_defaults.host_input or "192.168.1.50"
    split_preset = _non_interactive_split_preset(namespace, imported_defaults, profile)
    backend = _non_interactive_backend(namespace, imported, profile, split_preset)
    charger_preset = _non_interactive_charger_preset(namespace, imported_defaults, backend)
    backend = _resolved_backend(split_preset, charger_preset, backend)
    meter_host, switch_host, charger_host = role_host_defaults(namespace, imported_defaults, profile, split_preset, shared_host)
    host_input = resolved_primary_host(namespace, imported_defaults, meter_host, switch_host, charger_host)
    transport_kind, transport_host, transport_port, transport_device, transport_unit_id = non_interactive_transport_inputs(
        namespace,
        backend,
        charger_preset,
        host_input,
        imported_defaults,
    )
    request_timeout_seconds, switch_group_phase_layout = preset_specific_defaults(
        namespace,
        imported_defaults,
        backend=backend,
        split_preset=split_preset,
        charger_preset=charger_preset,
    )
    (
        auto_start_surplus_watts,
        auto_stop_surplus_watts,
        auto_min_soc,
        auto_resume_soc,
        scheduled_enabled_days,
        scheduled_latest_end_time,
        scheduled_night_current_amps,
    ) = policy_defaults(_non_interactive_policy_mode(namespace, imported_defaults), imported_defaults, namespace)
    return WizardAnswers(
        profile=profile,
        host_input=host_input,
        meter_host_input=meter_host,
        switch_host_input=switch_host,
        charger_host_input=charger_host,
        device_instance=_non_interactive_device_instance(namespace, imported_defaults),
        phase=_non_interactive_phase(namespace, imported_defaults),
        policy_mode=_non_interactive_policy_mode(namespace, imported_defaults),
        digest_auth=_non_interactive_digest_auth(namespace, imported_defaults),
        username=_non_interactive_string(namespace.username, imported_defaults.username),
        password=_non_interactive_string(namespace.password, imported_defaults.password),
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


def _non_interactive_split_preset(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults, profile: WizardProfile) -> str | None:
    split_preset = namespace.split_preset or imported_defaults.split_preset
    if profile == "split-topology":
        return split_preset or "template-stack"
    return split_preset


def _non_interactive_backend(
    namespace: argparse.Namespace,
    imported: ImportedWizardDefaults | None,
    profile: WizardProfile,
    split_preset: str | None,
) -> WizardChargerBackend | None:
    return cast(WizardChargerBackend | None, namespace.charger_backend or default_backend(profile, imported))


def _resolved_backend(
    split_preset: str | None,
    charger_preset: str | None,
    backend: WizardChargerBackend | None,
) -> WizardChargerBackend | None:
    from venus_evcharger.bootstrap.wizard_guidance import apply_split_preset_backend

    return apply_split_preset_backend(split_preset, backend, charger_preset)


def _interactive_charger_preset(
    namespace: argparse.Namespace,
    imported: ImportedWizardDefaults,
    backend: WizardChargerBackend | None,
) -> str | None:
    options = relevant_charger_presets(backend)
    selected_preset = _validated_namespace_charger_preset(namespace.charger_preset, options, backend)
    if selected_preset is not None:
        return selected_preset
    if not options:
        return None
    labels = _charger_preset_labels()
    default = imported.charger_preset if imported.charger_preset in options else None
    return _prompt_optional_choice("Choose an optional device preset:", ("none", *options), labels, default)


def _validated_namespace_charger_preset(
    charger_preset: str | None,
    options: tuple[str, ...],
    backend: WizardChargerBackend | None,
) -> str | None:
    """Return one CLI-provided charger preset after backend compatibility validation."""
    if charger_preset is None:
        return None
    if charger_preset not in options:
        raise ValueError(f"--charger-preset {charger_preset} is not supported for backend {backend or 'none'}")
    return charger_preset


def _charger_preset_labels() -> dict[str, str]:
    """Return choice labels for optional charger presets."""
    return {"none": "Generic backend mapping", **{key: value for key, value in CHARGER_PRESET_LABELS}}


def _non_interactive_charger_preset(
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


def _prompt_optional_choice(
    prompt: str,
    choices: tuple[str, ...],
    labels: dict[str, str],
    default: str | None,
) -> str | None:
    selected = _prompt_choice(prompt, choices, labels, default or "none")
    return None if selected == "none" else selected


def _non_interactive_device_instance(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> int:
    return int(namespace.device_instance if namespace.device_instance is not None else (imported_defaults.device_instance or 60))


def _non_interactive_phase(namespace: argparse.Namespace, imported_defaults: ImportedWizardDefaults) -> str:
    return namespace.phase or imported_defaults.phase or "L1"


def _non_interactive_string(namespace_value: str | None, imported_value: str | None) -> str:
    return namespace_value or imported_value or ""


def build_answers(namespace: argparse.Namespace) -> tuple[WizardAnswers, ImportedWizardDefaults | None]:
    imported = resolve_imported_defaults(namespace)
    answers = _non_interactive_answers(namespace, imported) if namespace.non_interactive else _interactive_answers(namespace, imported)
    return answers, imported


def build_parser(default_config_path: str, default_template_path: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Optional setup wizard for the Venus EV charger config.")
    parser.add_argument("--config-path", default=default_config_path)
    parser.add_argument("--template-path", default=default_template_path)
    parser.add_argument("--profile", choices=PROFILE_VALUES)
    parser.add_argument("--split-preset", choices=SPLIT_PRESET_VALUES)
    parser.add_argument("--charger-backend", choices=NATIVE_CHARGER_VALUES)
    parser.add_argument("--charger-preset", choices=CHARGER_PRESET_VALUES)
    parser.add_argument("--host")
    parser.add_argument("--meter-host")
    parser.add_argument("--switch-host")
    parser.add_argument("--charger-host")
    parser.add_argument("--device-instance", type=int)
    parser.add_argument("--phase", choices=("L1", "L2", "L3", "3P", "1P"))
    parser.add_argument("--policy-mode", choices=POLICY_VALUES)
    parser.add_argument("--transport", choices=TRANSPORT_VALUES)
    parser.add_argument("--transport-host")
    parser.add_argument("--transport-port", type=int)
    parser.add_argument("--transport-device")
    parser.add_argument("--transport-unit-id", type=int)
    parser.add_argument("--digest-auth", action="store_true")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--import-config")
    parser.add_argument("--resume-last", action="store_true")
    parser.add_argument("--clone-current", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--live-check", action="store_true")
    parser.add_argument("--probe-role", dest="probe_roles", action="append", choices=("meter", "switch", "charger"))
    parser.add_argument("--request-timeout-seconds", type=float)
    parser.add_argument("--switch-group-phase-layout", choices=SWITCH_GROUP_PHASE_LAYOUT_VALUES)
    parser.add_argument("--auto-start-surplus-watts", type=float)
    parser.add_argument("--auto-stop-surplus-watts", type=float)
    parser.add_argument("--auto-min-soc", type=float)
    parser.add_argument("--auto-resume-soc", type=float)
    parser.add_argument("--scheduled-enabled-days")
    parser.add_argument("--scheduled-latest-end-time")
    parser.add_argument("--scheduled-night-current-amps", type=float)
    parser.add_argument("--non-interactive", action="store_true")
    return parser
