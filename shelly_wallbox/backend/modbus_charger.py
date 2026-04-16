# SPDX-License-Identifier: GPL-3.0-or-later
"""Generic Modbus charger backend built from transport and register-profile layers."""

from __future__ import annotations

import configparser
from dataclasses import dataclass

from .modbus_client import ModbusClient
from .modbus_profiles import GenericModbusChargerProfile, load_modbus_charger_profile
from .modbus_transport import (
    ModbusTransport,
    ModbusTransportSettings,
    create_modbus_transport,
    load_modbus_transport_settings,
)
from .models import ChargerState, PhaseSelection, normalize_phase_selection


@dataclass(frozen=True)
class ModbusChargerSettings:
    """Normalized Modbus charger backend settings."""

    transport_settings: ModbusTransportSettings
    profile_name: str
    supported_phase_selections: tuple[PhaseSelection, ...]


def _config(config_path: str) -> configparser.ConfigParser:
    """Load one Modbus charger backend config file."""
    parser = configparser.ConfigParser()
    read_files = parser.read(str(config_path).strip())
    if not read_files:
        raise FileNotFoundError(config_path)
    return parser


class ModbusChargerBackend:
    """Direct charger backend driven by a Modbus transport plus register schema."""

    def __init__(self, service: object, config_path: str = "") -> None:
        self.service = service
        self.config_path = str(config_path).strip()
        parser = _config(self.config_path)
        self.transport_settings = load_modbus_transport_settings(parser, service)
        self.profile: GenericModbusChargerProfile = load_modbus_charger_profile(parser)
        self.settings = ModbusChargerSettings(
            transport_settings=self.transport_settings,
            profile_name=self.profile.profile_name,
            supported_phase_selections=self.profile.supported_phase_selections,
        )
        default_phase_selection = self.settings.supported_phase_selections[0]
        self._enabled_state_cache: bool | None = None
        self._current_amps_cache: float | None = None
        self._phase_selection_cache: PhaseSelection = normalize_phase_selection(
            getattr(service, "requested_phase_selection", default_phase_selection),
            default_phase_selection,
        )
        if self._phase_selection_cache not in self.settings.supported_phase_selections:
            self._phase_selection_cache = default_phase_selection
        self._transport: ModbusTransport | None = None
        self._client_cache: ModbusClient | None = None

    def _client(self) -> ModbusClient:
        """Return the lazily created Modbus client for this backend instance."""
        if self._client_cache is None:
            if self._transport is None:
                self._transport = create_modbus_transport(self.transport_settings)
            self._client_cache = ModbusClient(
                self._transport,
                self.transport_settings.unit_id,
                self.transport_settings.timeout_seconds,
            )
        return self._client_cache

    def read_charger_state(self) -> ChargerState:
        """Read one normalized charger state through the configured Modbus profile."""
        state = self.profile.read_state(
            self._client(),
            cached_enabled=self._enabled_state_cache,
            cached_current_amps=self._current_amps_cache,
            cached_phase_selection=self._phase_selection_cache,
        )
        self._enabled_state_cache = state.enabled
        self._current_amps_cache = state.current_amps
        self._phase_selection_cache = (
            state.phase_selection
            if state.phase_selection in self.settings.supported_phase_selections
            else self.settings.supported_phase_selections[0]
        )
        return state

    def set_enabled(self, enabled: bool) -> None:
        """Apply one enable/disable command through the configured Modbus profile."""
        self.profile.set_enabled(self._client(), bool(enabled))
        self._enabled_state_cache = bool(enabled)

    def set_current(self, amps: float) -> None:
        """Apply one current command through the configured Modbus profile."""
        self.profile.set_current(self._client(), float(amps))
        self._current_amps_cache = float(amps)

    def set_phase_selection(self, selection: PhaseSelection) -> None:
        """Apply one supported phase selection through the configured Modbus profile."""
        normalized = normalize_phase_selection(selection, self.settings.supported_phase_selections[0])
        if normalized not in self.settings.supported_phase_selections:
            raise ValueError(f"Unsupported phase selection '{selection}' for Modbus charger backend")
        self.profile.set_phase_selection(self._client(), normalized)
        self._phase_selection_cache = normalized
