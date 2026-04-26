# SPDX-License-Identifier: GPL-3.0-or-later
"""Reusable device profile and inventory helpers."""

from .config import (
    DeviceInventoryConfigError,
    parse_device_inventory_config,
    render_device_inventory_config,
    validate_device_inventory,
)
from .schema import (
    BindingRole,
    CapabilityKind,
    DeviceCapability,
    DeviceInstance,
    DeviceInventory,
    DeviceProfile,
    PhaseLabel,
    RoleBinding,
    RoleBindingMember,
    SwitchingMode,
)

__all__ = [
    "BindingRole",
    "CapabilityKind",
    "DeviceCapability",
    "DeviceInstance",
    "DeviceInventory",
    "DeviceInventoryConfigError",
    "DeviceProfile",
    "PhaseLabel",
    "RoleBinding",
    "RoleBindingMember",
    "SwitchingMode",
    "parse_device_inventory_config",
    "render_device_inventory_config",
    "validate_device_inventory",
]
