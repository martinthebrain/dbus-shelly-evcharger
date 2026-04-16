# SPDX-License-Identifier: GPL-3.0-or-later
"""Native SmartEVSE Modbus charger backend."""

from __future__ import annotations

import configparser
import math
from dataclasses import dataclass

from .modbus_client import ModbusClient
from .modbus_transport import (
    ModbusTransport,
    ModbusTransportSettings,
    create_modbus_transport,
    load_modbus_transport_settings,
)
from .models import ChargerState, PhaseSelection, normalize_phase_selection


_SMARTEVSE_SUPPORTED_PHASE_SELECTIONS: tuple[PhaseSelection, ...] = ("P1",)
_SMARTEVSE_STATE_REGISTER = 0x0000
_SMARTEVSE_ERROR_REGISTER = 0x0001
_SMARTEVSE_CURRENT_REGISTER = 0x0002
_SMARTEVSE_ACCESS_REGISTER = 0x0005
_SMARTEVSE_MAX_CURRENT_REGISTER = 0x0007
_SMARTEVSE_MIN_CURRENT_AMPS = 6
_SMARTEVSE_MAX_CURRENT_AMPS = 80
_SMARTEVSE_ERROR_LESS_THAN_6A_BIT = 0x0001
_SMARTEVSE_ERROR_NO_COMM_BIT = 0x0002
_SMARTEVSE_ERROR_TEMP_HIGH_BIT = 0x0004
_SMARTEVSE_ERROR_RCD_BIT = 0x0010
_SMARTEVSE_ERROR_NO_SUN_BIT = 0x0020
_SMARTEVSE_HARD_ERROR_BITS = (
    _SMARTEVSE_ERROR_LESS_THAN_6A_BIT
    | _SMARTEVSE_ERROR_NO_COMM_BIT
    | _SMARTEVSE_ERROR_TEMP_HIGH_BIT
    | _SMARTEVSE_ERROR_RCD_BIT
)


@dataclass(frozen=True)
class SmartEvseChargerSettings:
    """Normalized SmartEVSE Modbus settings."""

    transport_settings: ModbusTransportSettings
    profile_name: str
    supported_phase_selections: tuple[PhaseSelection, ...]
    state_register: int
    error_register: int
    current_register: int
    access_register: int
    max_current_register: int


def _config(config_path: str) -> configparser.ConfigParser:
    """Load one SmartEVSE backend config file."""
    parser = configparser.ConfigParser()
    read_files = parser.read(str(config_path).strip())
    if not read_files:
        raise FileNotFoundError(config_path)
    return parser


def load_smartevse_charger_settings(service: object, config_path: str) -> SmartEvseChargerSettings:
    """Return normalized SmartEVSE charger settings."""
    parser = _config(config_path)
    return SmartEvseChargerSettings(
        transport_settings=load_modbus_transport_settings(parser, service),
        profile_name="smartevse",
        supported_phase_selections=_SMARTEVSE_SUPPORTED_PHASE_SELECTIONS,
        state_register=_SMARTEVSE_STATE_REGISTER,
        error_register=_SMARTEVSE_ERROR_REGISTER,
        current_register=_SMARTEVSE_CURRENT_REGISTER,
        access_register=_SMARTEVSE_ACCESS_REGISTER,
        max_current_register=_SMARTEVSE_MAX_CURRENT_REGISTER,
    )


def _normalized_current_amps(raw_value: int) -> float:
    """Return one SmartEVSE current register value in amps."""
    value = max(0, int(raw_value))
    if value > _SMARTEVSE_MAX_CURRENT_AMPS:
        return float(value) / 10.0
    return float(value)


def _rounded_current_setting(amps: float) -> int:
    """Return one SmartEVSE-compatible current setpoint."""
    rounded = int(math.floor(float(amps) + 0.5))
    if rounded == 0:
        return 0
    if rounded < _SMARTEVSE_MIN_CURRENT_AMPS or rounded > _SMARTEVSE_MAX_CURRENT_AMPS:
        raise ValueError(
            "Unsupported charger current "
            f"'{amps}' for SmartEVSE backend (expected 0 or {_SMARTEVSE_MIN_CURRENT_AMPS}..{_SMARTEVSE_MAX_CURRENT_AMPS} A)"
        )
    return rounded


def _fault_tokens(error_bits: int) -> list[str]:
    """Return one normalized SmartEVSE fault token list."""
    faults: list[str] = []
    if int(error_bits) & _SMARTEVSE_ERROR_LESS_THAN_6A_BIT:
        faults.append("less-than-6a")
    if int(error_bits) & _SMARTEVSE_ERROR_NO_COMM_BIT:
        faults.append("no-comm")
    if int(error_bits) & _SMARTEVSE_ERROR_TEMP_HIGH_BIT:
        faults.append("temp-high")
    if int(error_bits) & _SMARTEVSE_ERROR_RCD_BIT:
        faults.append("rcd")
    if int(error_bits) & _SMARTEVSE_ERROR_NO_SUN_BIT:
        faults.append("no-sun")
    return faults


def _fault_text(error_bits: int) -> str | None:
    """Return one normalized SmartEVSE fault text."""
    faults = _fault_tokens(error_bits)
    return ",".join(faults) if faults else None


def _enabled(access_value: int) -> bool:
    """Return whether the SmartEVSE currently grants charging access."""
    return bool(int(access_value))


def _status_text(state_value: int, error_bits: int, enabled: bool) -> str | None:
    """Return one normalized SmartEVSE status text."""
    if int(error_bits) & _SMARTEVSE_HARD_ERROR_BITS:
        return "error"
    if int(error_bits) & _SMARTEVSE_ERROR_NO_SUN_BIT:
        return "waiting-solar"
    if not bool(enabled) and int(state_value) not in {2, 6, 7, 10}:
        return "disabled"
    return {
        0: "idle",
        1: "ready",
        2: "charging",
        3: "waiting-ventilation",
        4: "ready",
        5: "ready",
        6: "charging",
        7: "charging",
        8: "waiting-activation",
        9: "ready",
        10: "charging",
    }.get(int(state_value))


class SmartEvseChargerBackend:
    """Native Modbus backend for SmartEVSE controllers."""

    def __init__(self, service: object, config_path: str = "") -> None:
        self.service = service
        self.config_path = str(config_path).strip()
        self.settings = load_smartevse_charger_settings(service, self.config_path)
        self._transport: ModbusTransport | None = None
        self._client_cache: ModbusClient | None = None

    def _client(self) -> ModbusClient:
        """Return the lazily created Modbus client for this backend instance."""
        if self._client_cache is None:
            if self._transport is None:
                self._transport = create_modbus_transport(self.settings.transport_settings)
            self._client_cache = ModbusClient(
                self._transport,
                self.settings.transport_settings.unit_id,
                self.settings.transport_settings.timeout_seconds,
            )
        return self._client_cache

    def _read_register(self, address: int) -> int:
        """Read one 16-bit holding register from the SmartEVSE."""
        return int(self._client().read_scalar("holding", address, "uint16"))

    def _write_register(self, address: int, value: int) -> None:
        """Write one 16-bit holding register to the SmartEVSE."""
        self._client().write_single_register(address, int(value) & 0xFFFF)

    def read_charger_state(self) -> ChargerState:
        """Read one normalized charger state from SmartEVSE Modbus registers."""
        state_value = self._read_register(self.settings.state_register)
        error_bits = self._read_register(self.settings.error_register)
        current_setting = self._read_register(self.settings.current_register)
        access_value = self._read_register(self.settings.access_register)
        enabled = _enabled(access_value)
        return ChargerState(
            enabled=enabled,
            current_amps=_normalized_current_amps(current_setting),
            phase_selection="P1",
            actual_current_amps=None,
            power_w=None,
            energy_kwh=None,
            status_text=_status_text(state_value, error_bits, enabled),
            fault_text=_fault_text(error_bits),
        )

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable charging through the documented access register."""
        self._write_register(self.settings.access_register, 1 if bool(enabled) else 0)

    def set_current(self, amps: float) -> None:
        """Apply one whole-amp current limit to the SmartEVSE current register."""
        self._write_register(self.settings.current_register, _rounded_current_setting(amps))

    def set_phase_selection(self, selection: PhaseSelection) -> None:
        """Reject native phase writes because SmartEVSE phase writes are not documented here."""
        normalized = normalize_phase_selection(selection, "P1")
        if normalized != "P1":
            raise ValueError("SmartEVSE charger backend does not support native phase switching")
