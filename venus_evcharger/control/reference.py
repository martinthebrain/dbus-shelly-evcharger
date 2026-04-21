# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared human-facing command reference for Control API v1."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ControlApiCommandReference:
    """One human-facing command summary kept in sync with the API contract."""

    name: str
    required_fields: tuple[str, ...]
    value_type: str
    allowed_values: str
    idempotent_shape: str
    accepted_in_flight: str
    required_scope: str
    typical_restrictions: str


CONTROL_API_COMMAND_SCOPE_REQUIREMENTS: dict[str, str] = {
    "legacy_unknown_write": "control_admin",
    "reset_contactor_lockout": "control_admin",
    "reset_phase_lockout": "control_admin",
    "set_auto_runtime_setting": "control_admin",
    "set_auto_start": "control_basic",
    "set_current_setting": "control_basic",
    "set_enable": "control_basic",
    "set_mode": "control_basic",
    "set_phase_selection": "control_basic",
    "set_start_stop": "control_basic",
    "trigger_software_update": "update_admin",
}

CONTROL_API_COMMAND_REFERENCE: tuple[ControlApiCommandReference, ...] = (
    ControlApiCommandReference(
        name="set_mode",
        required_fields=("name", "value"),
        value_type="integer",
        allowed_values="`0`, `1`, `2`",
        idempotent_shape="yes",
        accepted_in_flight="possible",
        required_scope="control_basic",
        typical_restrictions="mode-specific runtime rules",
    ),
    ControlApiCommandReference(
        name="set_auto_start",
        required_fields=("name", "value"),
        value_type="boolean or `0/1`",
        allowed_values="binary",
        idempotent_shape="yes",
        accepted_in_flight="uncommon",
        required_scope="control_basic",
        typical_restrictions="none beyond local policy",
    ),
    ControlApiCommandReference(
        name="set_start_stop",
        required_fields=("name", "value"),
        value_type="boolean or `0/1`",
        allowed_values="binary",
        idempotent_shape="yes",
        accepted_in_flight="possible",
        required_scope="control_basic",
        typical_restrictions="mode/backend policy",
    ),
    ControlApiCommandReference(
        name="set_enable",
        required_fields=("name", "value"),
        value_type="boolean or `0/1`",
        allowed_values="binary",
        idempotent_shape="yes",
        accepted_in_flight="possible",
        required_scope="control_basic",
        typical_restrictions="backend/health policy",
    ),
    ControlApiCommandReference(
        name="set_current_setting",
        required_fields=("name", "path", "value"),
        value_type="number",
        allowed_values="`>= 0`",
        idempotent_shape="yes",
        accepted_in_flight="possible",
        required_scope="control_basic",
        typical_restrictions="backend/current limits",
    ),
    ControlApiCommandReference(
        name="set_phase_selection",
        required_fields=("name", "value"),
        value_type="string",
        allowed_values="`P1`, `P1_P2`, `P1_P2_P3`",
        idempotent_shape="yes",
        accepted_in_flight="possible",
        required_scope="control_basic",
        typical_restrictions="supported topology and phase hardware",
    ),
    ControlApiCommandReference(
        name="set_auto_runtime_setting",
        required_fields=("name", "path", "value"),
        value_type="float, integer, string, or binary depending on `path`",
        allowed_values="path-specific schema",
        idempotent_shape="yes",
        accepted_in_flight="uncommon",
        required_scope="control_admin",
        typical_restrictions="only supported runtime-setting paths",
    ),
    ControlApiCommandReference(
        name="reset_phase_lockout",
        required_fields=("name", "value"),
        value_type="boolean or `0/1`",
        allowed_values="binary",
        idempotent_shape="no",
        accepted_in_flight="uncommon",
        required_scope="control_admin",
        typical_restrictions="only meaningful when lockout exists",
    ),
    ControlApiCommandReference(
        name="reset_contactor_lockout",
        required_fields=("name", "value"),
        value_type="boolean or `0/1`",
        allowed_values="binary",
        idempotent_shape="no",
        accepted_in_flight="uncommon",
        required_scope="control_admin",
        typical_restrictions="only meaningful when lockout exists",
    ),
    ControlApiCommandReference(
        name="trigger_software_update",
        required_fields=("name", "value"),
        value_type="boolean or `0/1`",
        allowed_values="binary",
        idempotent_shape="no",
        accepted_in_flight="possible",
        required_scope="update_admin",
        typical_restrictions="update policy, availability, current update state",
    ),
    ControlApiCommandReference(
        name="legacy_unknown_write",
        required_fields=("name", "path", "value"),
        value_type="implementation-defined",
        allowed_values="only for explicitly mapped compatibility writes",
        idempotent_shape="compatibility-only",
        accepted_in_flight="implementation-defined",
        required_scope="control_admin",
        typical_restrictions="not for new clients",
    ),
)

CONTROL_API_COMMAND_REFERENCE_BY_NAME = {
    item.name: item for item in CONTROL_API_COMMAND_REFERENCE
}


def render_control_api_command_matrix_markdown() -> str:
    """Render the shared command matrix as a Markdown table."""
    header = [
        "| Command name | Required fields | Value type | Allowed values / ranges | Idempotent shape | `accepted_in_flight` | Required scope | Typical restrictions |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    rows = [
        (
            f"| `{item.name}` | "
            f"{', '.join(f'`{field}`' for field in item.required_fields)} | "
            f"{item.value_type} | "
            f"{item.allowed_values} | "
            f"{item.idempotent_shape} | "
            f"{item.accepted_in_flight} | "
            f"`{item.required_scope}` | "
            f"{item.typical_restrictions} |"
        )
        for item in CONTROL_API_COMMAND_REFERENCE
    ]
    return "\n".join([*header, *rows])
