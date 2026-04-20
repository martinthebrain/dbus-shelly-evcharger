# SPDX-License-Identifier: GPL-3.0-or-later
"""Generic Modbus EVSE register-profile support."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from typing import cast

from venus_evcharger.core.contracts import finite_float_or_none

from .modbus_client import ModbusClient, encode_register_value
from .models import ChargerState, PhaseSelection, normalize_phase_selection, normalize_phase_selection_tuple


ModbusReadRegisterType = str
ModbusWriteRegisterType = str


@dataclass(frozen=True)
class ModbusReadField:
    """One configured Modbus read mapping."""

    register_type: ModbusReadRegisterType
    address: int
    data_type: str
    scale: float
    word_order: str
    value_map: dict[int, str] | None

    def read(self, client: ModbusClient) -> object:
        """Read one normalized value from the configured Modbus source."""
        raw_value = client.read_scalar(self.register_type, self.address, self.data_type, self.word_order)
        if self.value_map is not None:
            return self._mapped_read_value(raw_value)
        return self._scaled_read_value(raw_value)

    def _mapped_read_value(self, raw_value: object) -> object:
        """Return one mapped read value when a numeric value map is configured."""
        assert self.value_map is not None
        scalar = _int_scalar(raw_value)
        mapped = self.value_map.get(scalar)
        return mapped if mapped is not None else str(scalar)

    def _scaled_read_value(self, raw_value: object) -> object:
        """Return one raw or scaled read value when no value map is configured."""
        if isinstance(raw_value, bool):
            return raw_value
        if self.scale and self.scale not in {0.0, 1.0}:
            return _float_scalar(raw_value) / float(self.scale)
        return raw_value


@dataclass(frozen=True)
class ModbusEnableWrite:
    """One configured Modbus write mapping for charger enable/disable."""

    register_type: ModbusWriteRegisterType
    address: int
    true_value: int
    false_value: int

    def write(self, client: ModbusClient, enabled: bool) -> None:
        """Apply one enable/disable value through the configured Modbus sink."""
        target_value = self.true_value if enabled else self.false_value
        if self.register_type == "coil":
            client.write_single_coil(self.address, bool(target_value))
            return
        client.write_single_register(self.address, target_value)


@dataclass(frozen=True)
class ModbusNumericWrite:
    """One configured Modbus write mapping for numeric charger values."""

    register_type: ModbusWriteRegisterType
    address: int
    data_type: str
    scale: float
    word_order: str

    def write(self, client: ModbusClient, value: float) -> None:
        """Write one scaled numeric value through the configured Modbus sink."""
        if self.register_type != "holding":
            raise ValueError("Numeric Modbus writes currently require RegisterType=holding")
        registers = self._numeric_write_registers(value)
        self._write_registers(client, registers)

    def _numeric_write_registers(self, value: float) -> tuple[int, ...]:
        """Return encoded Modbus registers for one numeric write value."""
        scaled = float(value) * float(self.scale or 1.0)
        rounded = int(round(scaled)) if self.data_type != "float32" else scaled
        return encode_register_value(rounded, self.data_type, self.word_order)

    def _write_registers(self, client: ModbusClient, registers: tuple[int, ...]) -> None:
        """Write one or many registers depending on the encoded payload width."""
        if len(registers) == 1:
            client.write_single_register(self.address, registers[0])
            return
        client.write_multiple_registers(self.address, registers)


def _int_scalar(raw_value: object) -> int:
    """Return one Modbus scalar coerced to integer for mapped reads."""
    return int(cast(int | float | bool | str, raw_value))


def _float_scalar(raw_value: object) -> float:
    """Return one Modbus scalar coerced to float for scaled reads."""
    return float(cast(int | float | bool | str, raw_value))


@dataclass(frozen=True)
class ModbusPhaseWrite:
    """One configured Modbus write mapping for logical phase selection."""

    register_type: ModbusWriteRegisterType
    address: int
    data_type: str
    word_order: str
    selection_map: dict[PhaseSelection, int]

    def write(self, client: ModbusClient, selection: PhaseSelection) -> None:
        """Write one mapped phase-selection value through the configured Modbus sink."""
        if selection not in self.selection_map:
            raise ValueError(f"Unsupported phase selection '{selection}' for Modbus phase write")
        raw_value = self.selection_map[selection]
        if self.register_type == "coil":
            client.write_single_coil(self.address, bool(raw_value))
            return
        registers = encode_register_value(raw_value, self.data_type, self.word_order)
        if len(registers) == 1:
            client.write_single_register(self.address, registers[0])
            return
        client.write_multiple_registers(self.address, registers)


@dataclass(frozen=True)
class GenericModbusChargerProfile:
    """One generic register-schema profile consumed by the Modbus charger backend."""

    profile_name: str
    supported_phase_selections: tuple[PhaseSelection, ...]
    state_enabled: ModbusReadField | None
    state_current: ModbusReadField | None
    state_phase_selection: ModbusReadField | None
    state_actual_current: ModbusReadField | None
    state_power_watts: ModbusReadField | None
    state_energy_kwh: ModbusReadField | None
    state_status: ModbusReadField | None
    state_fault: ModbusReadField | None
    enable_write: ModbusEnableWrite | None
    current_write: ModbusNumericWrite
    phase_write: ModbusPhaseWrite | None
    enable_uses_current_write: bool = False
    enable_default_current_amps: float = 6.0

    def read_state(
        self,
        client: ModbusClient,
        *,
        cached_enabled: bool | None,
        cached_current_amps: float | None,
        cached_phase_selection: PhaseSelection,
    ) -> ChargerState:
        """Return one normalized charger state from configured Modbus registers."""
        enabled = _optional_field_value(self.state_enabled, client, cached_enabled)
        current_amps = _optional_float_value(self.state_current, client, cached_current_amps)
        phase_selection = self._resolved_phase_selection(client, cached_phase_selection)
        enabled = self._resolved_enabled(enabled, current_amps)
        return ChargerState(
            enabled=_optional_bool(enabled),
            current_amps=current_amps,
            phase_selection=phase_selection,
            actual_current_amps=_optional_float_value(self.state_actual_current, client, None),
            power_w=_optional_float_value(self.state_power_watts, client, None),
            energy_kwh=_optional_float_value(self.state_energy_kwh, client, None),
            status_text=_optional_text_value(self.state_status, client),
            fault_text=_optional_text_value(self.state_fault, client),
        )

    def _resolved_phase_selection(self, client: ModbusClient, cached_phase_selection: PhaseSelection) -> PhaseSelection:
        """Return one supported phase selection from cached and live inputs."""
        raw_phase_selection = _optional_field_value(self.state_phase_selection, client, None)
        if raw_phase_selection is None:
            return self._supported_phase_selection(cached_phase_selection)
        normalized = normalize_phase_selection(raw_phase_selection, cached_phase_selection)
        return self._supported_phase_selection(normalized)

    def _supported_phase_selection(self, selection: PhaseSelection) -> PhaseSelection:
        """Return one phase selection guaranteed to be supported by this profile."""
        if selection in self.supported_phase_selections:
            return selection
        return self.supported_phase_selections[0]

    def _resolved_enabled(self, enabled: object, current_amps: float | None) -> object:
        """Infer enabled state from current writes when no explicit state bit exists."""
        if enabled is not None or not self.enable_uses_current_write or current_amps is None:
            return enabled
        return current_amps > 0.0

    def set_enabled(self, client: ModbusClient, enabled: bool) -> None:
        """Apply one enable/disable command through the configured Modbus mapping."""
        if self.enable_write is None:
            raise ValueError("Configured Modbus charger profile does not expose direct enable writes")
        self.enable_write.write(client, enabled)

    def set_current(self, client: ModbusClient, amps: float) -> None:
        """Apply one current command through the configured Modbus mapping."""
        self.current_write.write(client, amps)

    def set_phase_selection(self, client: ModbusClient, selection: PhaseSelection) -> None:
        """Apply one phase-selection command when the profile exposes it."""
        if self.phase_write is None:
            raise ValueError("Configured Modbus charger profile does not expose phase selection writes")
        self.phase_write.write(client, selection)


def _optional_section(parser: configparser.ConfigParser, name: str) -> configparser.SectionProxy | None:
    """Return one optional config section."""
    return parser[name] if parser.has_section(name) else None


def _required_int(section: configparser.SectionProxy, key: str) -> int:
    """Return one required integer config value."""
    value = str(section.get(key, "")).strip()
    if not value:
        raise ValueError(f"Modbus config section [{section.name}] requires {key}")
    return int(value)


def _normalized_register_type(section: configparser.SectionProxy, *, write: bool = False) -> str:
    """Return one validated register kind."""
    normalized = str(section.get("RegisterType", "")).strip().lower()
    allowed = {"holding", "input", "coil", "discrete"}
    if normalized not in allowed:
        raise ValueError(f"Unsupported Modbus RegisterType '{normalized}' in [{section.name}]")
    if write and normalized in {"input", "discrete"}:
        raise ValueError(f"Modbus writes in [{section.name}] require RegisterType=coil or holding")
    return normalized


def _normalized_data_type(section: configparser.SectionProxy, default: str) -> str:
    """Return one validated Modbus scalar data type."""
    normalized = str(section.get("DataType", default)).strip().lower()
    if normalized not in {"bool", "uint16", "int16", "uint32", "int32", "float32"}:
        raise ValueError(f"Unsupported Modbus DataType '{normalized}' in [{section.name}]")
    return normalized


def _normalized_word_order(section: configparser.SectionProxy) -> str:
    """Return one validated Modbus word order."""
    normalized = str(section.get("WordOrder", "big")).strip().lower()
    if normalized not in {"big", "little"}:
        raise ValueError(f"Unsupported Modbus WordOrder '{normalized}' in [{section.name}]")
    return normalized


def _normalized_scale(section: configparser.SectionProxy) -> float:
    """Return one validated Modbus scale factor."""
    scale = finite_float_or_none(section.get("Scale", "1"))
    if scale is None or scale == 0.0:
        return 1.0
    return float(scale)


def _parsed_value_map(raw_value: object) -> dict[int, str] | None:
    """Return one optional integer-to-text value map."""
    text = str(raw_value).strip()
    if not text:
        return None
    parsed: dict[int, str] = {}
    for token in text.split(","):
        key_text, _, value_text = token.partition(":")
        key = int(key_text.strip())
        parsed[key] = value_text.strip()
    return parsed or None


def _parsed_phase_selection_map(raw_value: object) -> dict[PhaseSelection, int]:
    """Return one required phase-selection write map."""
    text = str(raw_value).strip()
    if not text:
        raise ValueError("Modbus phase write requires Map")
    parsed: dict[PhaseSelection, int] = {}
    for token in text.split(","):
        key_text, _, value_text = token.partition(":")
        selection = normalize_phase_selection(key_text.strip(), "P1")
        parsed[selection] = int(value_text.strip())
    return parsed


def _optional_read_field(parser: configparser.ConfigParser, section_name: str) -> ModbusReadField | None:
    """Return one optional read-field descriptor."""
    section = _optional_section(parser, section_name)
    if section is None:
        return None
    register_type = _normalized_register_type(section)
    data_type = _normalized_data_type(
        section,
        "bool" if register_type in {"coil", "discrete"} else "uint16",
    )
    return ModbusReadField(
        register_type=register_type,
        address=_required_int(section, "Address"),
        data_type=data_type,
        scale=_normalized_scale(section),
        word_order=_normalized_word_order(section),
        value_map=_parsed_value_map(section.get("ValueMap", "")),
    )


def _required_enable_write(parser: configparser.ConfigParser) -> ModbusEnableWrite:
    """Return the required enable-write descriptor."""
    section = parser["EnableWrite"] if parser.has_section("EnableWrite") else None
    if section is None:
        raise ValueError("Modbus charger backend requires [EnableWrite]")
    return ModbusEnableWrite(
        register_type=_normalized_register_type(section, write=True),
        address=_required_int(section, "Address"),
        true_value=int(str(section.get("TrueValue", "1")).strip() or "1"),
        false_value=int(str(section.get("FalseValue", "0")).strip() or "0"),
    )


def _required_current_write(parser: configparser.ConfigParser) -> ModbusNumericWrite:
    """Return the required current-write descriptor."""
    section = parser["CurrentWrite"] if parser.has_section("CurrentWrite") else None
    if section is None:
        raise ValueError("Modbus charger backend requires [CurrentWrite]")
    return ModbusNumericWrite(
        register_type=_normalized_register_type(section, write=True),
        address=_required_int(section, "Address"),
        data_type=_normalized_data_type(section, "uint16"),
        scale=_normalized_scale(section),
        word_order=_normalized_word_order(section),
    )


def _optional_phase_write(parser: configparser.ConfigParser) -> ModbusPhaseWrite | None:
    """Return the optional phase-write descriptor."""
    section = _optional_section(parser, "PhaseWrite")
    if section is None:
        return None
    return ModbusPhaseWrite(
        register_type=_normalized_register_type(section, write=True),
        address=_required_int(section, "Address"),
        data_type=_normalized_data_type(section, "uint16"),
        word_order=_normalized_word_order(section),
        selection_map=_parsed_phase_selection_map(section.get("Map", "")),
    )


def _supported_phase_selections(capabilities: configparser.SectionProxy | None, phase_write: ModbusPhaseWrite | None) -> tuple[PhaseSelection, ...]:
    """Return one normalized supported-phase tuple for the generic profile."""
    configured = _configured_supported_phase_selections(capabilities)
    normalized = normalize_phase_selection_tuple(configured, ("P1",))
    if phase_write is None:
        return normalized
    available = _mapped_supported_phase_selections(normalized, phase_write)
    mapped = tuple(phase_write.selection_map.keys())
    return available or mapped or ("P1",)


def _configured_supported_phase_selections(capabilities: configparser.SectionProxy | None) -> str:
    """Return the configured supported phase-selection text."""
    if capabilities is None:
        return "P1"
    return capabilities.get("SupportedPhaseSelections", "P1")


def _enable_uses_current_write(capabilities: configparser.SectionProxy | None) -> bool:
    """Return whether current writes should double as enable/disable control."""
    if capabilities is None:
        return False
    raw_value = str(capabilities.get("EnableUsesCurrentWrite", "0")).strip().lower()
    return raw_value in {"1", "true", "yes", "on"}


def _enable_default_current_amps(capabilities: configparser.SectionProxy | None) -> float:
    """Return the fallback current used when enabling via current writes."""
    if capabilities is None:
        return 6.0
    configured = finite_float_or_none(capabilities.get("EnableDefaultCurrentAmps", "6"))
    if configured is None or configured <= 0.0:
        return 6.0
    return float(configured)


def _mapped_supported_phase_selections(
    normalized: tuple[PhaseSelection, ...],
    phase_write: ModbusPhaseWrite,
) -> tuple[PhaseSelection, ...]:
    """Return supported selections that are also writable by the phase map."""
    return cast(
        tuple[PhaseSelection, ...],
        tuple(selection for selection in normalized if selection in phase_write.selection_map),
    )


def load_generic_modbus_charger_profile(parser: configparser.ConfigParser) -> GenericModbusChargerProfile:
    """Return one generic register-schema profile from config sections."""
    capabilities = _optional_section(parser, "Capabilities")
    enable_uses_current_write = _enable_uses_current_write(capabilities)
    phase_write = _optional_phase_write(parser)
    supported_phase_selections = _supported_phase_selections(capabilities, phase_write)
    _validate_supported_phase_writes(supported_phase_selections, phase_write)
    enable_write = _validated_enable_section(parser, enable_uses_current_write)
    return GenericModbusChargerProfile(
        profile_name="generic",
        supported_phase_selections=supported_phase_selections,
        state_enabled=_optional_read_field(parser, "StateEnabled"),
        state_current=_optional_read_field(parser, "StateCurrent"),
        state_phase_selection=_optional_read_field(parser, "StatePhase"),
        state_actual_current=_optional_read_field(parser, "StateActualCurrent"),
        state_power_watts=_optional_read_field(parser, "StatePower"),
        state_energy_kwh=_optional_read_field(parser, "StateEnergy"),
        state_status=_optional_read_field(parser, "StateStatus"),
        state_fault=_optional_read_field(parser, "StateFault"),
        enable_write=_required_enable_write(parser) if enable_write is not None else None,
        current_write=_required_current_write(parser),
        phase_write=phase_write,
        enable_uses_current_write=enable_uses_current_write,
        enable_default_current_amps=_enable_default_current_amps(capabilities),
    )


def _validate_supported_phase_writes(
    supported_phase_selections: tuple[PhaseSelection, ...],
    phase_write: ModbusPhaseWrite | None,
) -> None:
    """Reject multi-phase profiles that cannot actually switch phases."""
    if len(supported_phase_selections) > 1 and phase_write is None:
        raise ValueError("Multi-phase Modbus charger profiles require [PhaseWrite]")


def _validated_enable_section(
    parser: configparser.ConfigParser,
    enable_uses_current_write: bool,
) -> configparser.SectionProxy | None:
    """Return one validated enable section or allow current-write emulation."""
    enable_write = _optional_section(parser, "EnableWrite")
    if enable_write is not None or enable_uses_current_write:
        return enable_write
    raise ValueError("Modbus charger backend requires [EnableWrite]")


def load_modbus_charger_profile(parser: configparser.ConfigParser) -> GenericModbusChargerProfile:
    """Return one Modbus charger profile selected by Adapter.Profile."""
    adapter = parser["Adapter"] if parser.has_section("Adapter") else parser["DEFAULT"]
    profile_name = str(adapter.get("Profile", "generic")).strip().lower() or "generic"
    if profile_name != "generic":
        raise ValueError(f"Unsupported Modbus charger profile '{profile_name}'")
    return load_generic_modbus_charger_profile(parser)


def _optional_field_value(field: ModbusReadField | None, client: ModbusClient, default: object) -> object:
    """Return one optional field value or the given default."""
    return default if field is None else field.read(client)


def _optional_float_value(field: ModbusReadField | None, client: ModbusClient, default: float | None) -> float | None:
    """Return one optional float field value."""
    if field is None:
        return default
    value = field.read(client)
    number = finite_float_or_none(value)
    return default if number is None else float(number)


def _optional_text_value(field: ModbusReadField | None, client: ModbusClient) -> str | None:
    """Return one optional text field value."""
    if field is None:
        return None
    value = field.read(client)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_bool(value: object) -> bool | None:
    """Return one optional boolean."""
    return None if value is None else bool(value)
