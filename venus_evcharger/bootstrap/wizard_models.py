# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared models for the optional wallbox setup wizard."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

WizardProfile = Literal[
    "simple-relay",
    "native-charger",
    "native-charger-phase-switch",
    "split-topology",
    "advanced-manual",
]
WizardPolicyMode = Literal["manual", "auto", "scheduled"]
WizardChargerBackend = Literal[
    "goe_charger",
    "simpleevse_charger",
    "smartevse_charger",
    "template_charger",
    "modbus_charger",
]
WizardTransportKind = Literal["serial_rtu", "tcp"]


@dataclass(frozen=True)
class WizardAnswers:
    profile: WizardProfile
    host_input: str
    meter_host_input: str | None
    switch_host_input: str | None
    charger_host_input: str | None
    device_instance: int
    phase: str
    policy_mode: WizardPolicyMode
    digest_auth: bool
    username: str
    password: str
    split_preset: str | None = None
    charger_backend: WizardChargerBackend | None = None
    charger_preset: str | None = None
    request_timeout_seconds: float | None = None
    switch_group_supported_phase_selections: str = "P1,P1_P2,P1_P2_P3"
    auto_start_surplus_watts: float | None = None
    auto_stop_surplus_watts: float | None = None
    auto_min_soc: float | None = None
    auto_resume_soc: float | None = None
    scheduled_enabled_days: str | None = None
    scheduled_latest_end_time: str | None = None
    scheduled_night_current_amps: float | None = None
    transport_kind: WizardTransportKind = "serial_rtu"
    transport_host: str = ""
    transport_port: int = 502
    transport_device: str = "/dev/ttyUSB0"
    transport_unit_id: int = 1


@dataclass(frozen=True)
class WizardResult:
    created_at: str
    config_path: str
    imported_from: str | None
    profile: WizardProfile
    policy_mode: WizardPolicyMode
    split_preset: str | None
    charger_backend: WizardChargerBackend | None
    charger_preset: str | None
    transport_kind: WizardTransportKind | None
    role_hosts: dict[str, str]
    validation: dict[str, object]
    live_check: dict[str, object] | None
    generated_files: tuple[str, ...]
    backup_files: tuple[str, ...]
    result_path: str | None
    audit_path: str | None
    topology_summary_path: str | None
    manual_review: tuple[str, ...]
    dry_run: bool
    warnings: tuple[str, ...] = field(default_factory=tuple)
    answer_defaults: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "created_at": self.created_at,
            "config_path": self.config_path,
            "imported_from": self.imported_from,
            "profile": self.profile,
            "policy_mode": self.policy_mode,
            "split_preset": self.split_preset,
            "charger_backend": self.charger_backend,
            "charger_preset": self.charger_preset,
            "transport_kind": self.transport_kind,
            "role_hosts": dict(self.role_hosts),
            "validation": self.validation,
            "live_check": self.live_check,
            "warnings": list(self.warnings),
            "answer_defaults": dict(self.answer_defaults),
            "generated_files": list(self.generated_files),
            "backup_files": list(self.backup_files),
            "result_path": self.result_path,
            "audit_path": self.audit_path,
            "topology_summary_path": self.topology_summary_path,
            "manual_review": list(self.manual_review),
            "dry_run": self.dry_run,
        }
