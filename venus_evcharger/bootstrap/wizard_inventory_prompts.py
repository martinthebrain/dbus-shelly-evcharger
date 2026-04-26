# SPDX-License-Identifier: GPL-3.0-or-later
"""Interactive inventory prompt helpers for the setup wizard."""

from __future__ import annotations

import argparse

from venus_evcharger.bootstrap.wizard_cli import prompt_yes_no


def _namespace_string(namespace: argparse.Namespace, attr: str) -> str | None:
    value = getattr(namespace, attr, None)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _inventory_flag_name(attr: str) -> str:
    return attr.replace("_", "-")


def _choice_from_raw(raw: str, choices: tuple[str, ...]) -> str | None:
    if raw.isdigit():
        numeric = int(raw)
        if 1 <= numeric <= len(choices):
            return choices[numeric - 1]
    if raw in choices:
        return raw
    return None


def _interactive_choice(default: str, choices: tuple[str, ...]) -> str:
    while True:
        raw = input(f"Select [{default}]: ").strip()
        if not raw:
            return default
        resolved = _choice_from_raw(raw, choices)
        if resolved is not None:
            return resolved
        print("Invalid selection, please try again.")


def inventory_field(namespace: argparse.Namespace, attr: str, prompt: str) -> str:
    """Read one required inventory field from args or interactive input."""
    value = _namespace_string(namespace, attr)
    if value is not None:
        return value
    if getattr(namespace, "non_interactive", False):
        action = namespace.inventory_action
        flag = _inventory_flag_name(attr)
        raise ValueError(f"--{flag} is required for --inventory-action {action}")
    entered = input(f"{prompt}: ").strip()
    if not entered:
        raise ValueError(f"{prompt} must not be empty")
    return entered


def inventory_optional_field(namespace: argparse.Namespace, attr: str, prompt: str) -> str | None:
    """Read one optional inventory field from args or interactive input."""
    value = _namespace_string(namespace, attr)
    if value is not None:
        return value
    if isinstance(getattr(namespace, attr, None), str):
        return None
    if getattr(namespace, "non_interactive", False):
        return None
    entered = input(f"{prompt} [leave blank to clear]: ").strip()
    return entered or None


def inventory_field_with_default(
    namespace: argparse.Namespace,
    attr: str,
    prompt: str,
    default: str,
) -> str:
    """Read one inventory field and fall back to one default value."""
    value = _namespace_string(namespace, attr)
    if value is not None:
        return value
    if getattr(namespace, "non_interactive", False):
        return default
    entered = input(f"{prompt} [{default}]: ").strip()
    return entered or default


def inventory_bool_field(
    namespace: argparse.Namespace,
    attr: str,
    prompt: str,
    default: bool = False,
) -> bool:
    """Read one yes/no inventory field from args or interactive input."""
    value = getattr(namespace, attr, None)
    if value is True:
        return True
    if getattr(namespace, "non_interactive", False):
        return False
    return prompt_yes_no(f"{prompt}?", default)


def inventory_choice_field(
    namespace: argparse.Namespace,
    attr: str,
    prompt: str,
    choices: tuple[str, ...],
    default: str,
) -> str:
    """Read one inventory choice from args or interactive input."""
    value = _namespace_string(namespace, attr)
    if value is not None:
        if value in choices:
            return value
        joined = ", ".join(choices)
        raise ValueError(f"{prompt} must be one of: {joined}")
    if getattr(namespace, "non_interactive", False):
        return default
    print(prompt)
    for index, choice in enumerate(choices, start=1):
        print(f"  {index}. {choice}")
    return _interactive_choice(default, choices)
