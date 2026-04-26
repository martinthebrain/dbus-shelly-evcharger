# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalized schema for reusable device profiles and local device inventories."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PhaseLabel = Literal["L1", "L2", "L3"]
CapabilityKind = Literal["switch", "meter", "charger"]
SwitchingMode = Literal["direct", "contactor"]
BindingRole = Literal["actuation", "measurement", "charger"]


@dataclass(frozen=True)
class DeviceCapability:
    """One reusable capability exposed by a device profile."""

    id: str
    kind: CapabilityKind
    adapter_type: str
    supported_phases: tuple[PhaseLabel, ...]
    channel: str | None = None
    measures_power: bool = False
    measures_energy: bool = False
    switching_mode: SwitchingMode | None = None
    supports_feedback: bool = False
    supports_phase_selection: bool = False


@dataclass(frozen=True)
class DeviceProfile:
    """One reusable device definition with one or more capabilities."""

    id: str
    label: str
    vendor: str | None = None
    model: str | None = None
    description: str | None = None
    capabilities: tuple[DeviceCapability, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DeviceInstance:
    """One concrete device present in one local installation."""

    id: str
    profile_id: str
    label: str
    endpoint: str | None = None
    enabled: bool = True
    notes: str | None = None


@dataclass(frozen=True)
class RoleBindingMember:
    """One concrete capability assignment inside one logical role binding."""

    device_id: str
    capability_id: str
    phases: tuple[PhaseLabel, ...]


@dataclass(frozen=True)
class RoleBinding:
    """One logical role assembled from one or more concrete device capabilities."""

    id: str
    role: BindingRole
    label: str
    phase_scope: tuple[PhaseLabel, ...]
    members: tuple[RoleBindingMember, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DeviceInventory:
    """One persistent set of profiles, instances, and logical role bindings."""

    profiles: tuple[DeviceProfile, ...] = field(default_factory=tuple)
    devices: tuple[DeviceInstance, ...] = field(default_factory=tuple)
    bindings: tuple[RoleBinding, ...] = field(default_factory=tuple)

