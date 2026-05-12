# SPDX-License-Identifier: GPL-3.0-or-later
"""Rendering helpers for device inventory configs."""

from __future__ import annotations

from .schema import DeviceCapability, DeviceInstance, DeviceInventory, DeviceProfile, RoleBinding, RoleBindingMember


def render_validated_device_inventory_config(inventory: DeviceInventory) -> str:
    """Render one already validated inventory into INI-style text."""
    sections = _inventory_sections(inventory)
    return "\n\n".join(sections) + ("\n" if sections else "")


def _inventory_sections(inventory: DeviceInventory) -> list[str]:
    sections: list[str] = []
    sections.extend(_profile_sections(inventory))
    sections.extend(_device_sections(inventory))
    sections.extend(_binding_sections(inventory))
    return sections


def _profile_sections(inventory: DeviceInventory) -> list[str]:
    sections: list[str] = []
    for profile in inventory.profiles:
        sections.append(_render_profile(profile))
        sections.extend(_render_profile_capabilities(profile))
    return sections


def _render_profile_capabilities(profile: DeviceProfile) -> list[str]:
    return [_render_capability(profile.id, capability) for capability in profile.capabilities]


def _device_sections(inventory: DeviceInventory) -> list[str]:
    return [_render_device(device) for device in inventory.devices]


def _binding_sections(inventory: DeviceInventory) -> list[str]:
    sections: list[str] = []
    for binding in inventory.bindings:
        sections.append(_render_binding(binding))
        sections.extend(_render_binding_members(binding))
    return sections


def _render_binding_members(binding: RoleBinding) -> list[str]:
    return [
        _render_binding_member(binding.id, index, member)
        for index, member in enumerate(binding.members, start=1)
    ]


def _render_profile(profile: DeviceProfile) -> str:
    lines = [f"[Profile:{profile.id}]", f"Label={profile.label}"]
    if profile.vendor is not None:
        lines.append(f"Vendor={profile.vendor}")
    if profile.model is not None:
        lines.append(f"Model={profile.model}")
    if profile.description is not None:
        lines.append(f"Description={profile.description}")
    return "\n".join(lines)


def _render_capability(profile_id: str, capability: DeviceCapability) -> str:
    lines = [
        f"[Capability:{profile_id}:{capability.id}]",
        f"Kind={capability.kind}",
        f"AdapterType={capability.adapter_type}",
        f"SupportedPhases={','.join(capability.supported_phases)}",
    ]
    if capability.channel is not None:
        lines.append(f"Channel={capability.channel}")
    lines.extend(_render_capability_kind_fields(capability))
    return "\n".join(lines)


def _render_capability_kind_fields(capability: DeviceCapability) -> list[str]:
    if capability.kind == "meter":
        return _render_meter_capability_fields(capability)
    if capability.kind == "switch":
        return _render_switch_capability_fields(capability)
    return []


def _render_meter_capability_fields(capability: DeviceCapability) -> list[str]:
    return [
        f"MeasuresPower={1 if capability.measures_power else 0}",
        f"MeasuresEnergy={1 if capability.measures_energy else 0}",
    ]


def _render_switch_capability_fields(capability: DeviceCapability) -> list[str]:
    if capability.switching_mode is None:
        return []
    return [
        f"SwitchingMode={capability.switching_mode}",
        f"SupportsFeedback={1 if capability.supports_feedback else 0}",
        f"SupportsPhaseSelection={1 if capability.supports_phase_selection else 0}",
    ]


def _render_device(device: DeviceInstance) -> str:
    lines = [
        f"[Device:{device.id}]",
        f"Profile={device.profile_id}",
        f"Label={device.label}",
        f"Enabled={1 if device.enabled else 0}",
    ]
    if device.endpoint is not None:
        lines.append(f"Endpoint={device.endpoint}")
    if device.notes is not None:
        lines.append(f"Notes={device.notes}")
    return "\n".join(lines)


def _render_binding(binding: RoleBinding) -> str:
    return "\n".join(
        [
            f"[Binding:{binding.id}]",
            f"Role={binding.role}",
            f"Label={binding.label}",
            f"PhaseScope={','.join(binding.phase_scope)}",
        ]
    )


def _render_binding_member(binding_id: str, index: int, member: RoleBindingMember) -> str:
    return "\n".join(
        [
            f"[BindingMember:{binding_id}:{index}]",
            f"Device={member.device_id}",
            f"Capability={member.capability_id}",
            f"Phases={','.join(member.phases)}",
        ]
    )
