# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for Shelly-backed meter and switch backends."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from typing import Any, Mapping, cast
from urllib.parse import urlencode

import requests
from requests.auth import HTTPDigestAuth

from dbus_shelly_wallbox_backend_types import (
    PhaseSelection,
    SwitchingMode,
    normalize_phase_selection,
    normalize_phase_selection_tuple,
)
from dbus_shelly_wallbox_contracts import finite_float_or_none, normalize_binary_flag
from dbus_shelly_wallbox_shelly_io import ShellyPmStatus, ShellyRpcScalar


def parse_phase_selection_list(value: object, default: tuple[PhaseSelection, ...] = ("P1",)) -> tuple[PhaseSelection, ...]:
    """Return one normalized supported-phase tuple."""
    return normalize_phase_selection_tuple(value, default)


def normalize_switching_mode(value: object, default: SwitchingMode = "direct") -> SwitchingMode:
    """Return one normalized switching mode."""
    mode = str(value).strip().lower() if value is not None else ""
    if mode == "contactor":
        return "contactor"
    if mode == "direct":
        return "direct"
    return default


_PHASE_MAP_KEYS: dict[str, PhaseSelection] = {
    "p1": "P1",
    "l1": "P1",
    "l2": "P1",
    "l3": "P1",
    "1p": "P1",
    "p1_p2": "P1_P2",
    "p1+p2": "P1_P2",
    "2p": "P1_P2",
    "p1_p2_p3": "P1_P2_P3",
    "p1+p2+p3": "P1_P2_P3",
    "3p": "P1_P2_P3",
}


def _parse_switch_channel_ids(value: object, default: tuple[int, ...]) -> tuple[int, ...]:
    """Return one normalized tuple of Shelly switch channel IDs."""
    text = str(value).strip() if value is not None else ""
    if not text:
        return default
    normalized: list[int] = []
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            channel_id = int(token)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid Shelly switch channel id '{token}'") from exc
        if channel_id < 0:
            raise ValueError(f"Invalid Shelly switch channel id '{token}'")
        if channel_id not in normalized:
            normalized.append(channel_id)
    return tuple(normalized) if normalized else default


def _default_phase_switch_targets(
    device_id: int,
    supported_phase_selections: tuple[PhaseSelection, ...],
) -> dict[PhaseSelection, tuple[int, ...]]:
    """Return backward-compatible one-channel targets for every supported selection."""
    default_target = (int(device_id),)
    return {
        selection: default_target
        for selection in supported_phase_selections
    }


def _phase_switch_targets(
    phase_map: configparser.SectionProxy,
    device_id: int,
    supported_phase_selections: tuple[PhaseSelection, ...],
) -> dict[PhaseSelection, tuple[int, ...]]:
    """Return configured phase-selection to relay-channel mappings."""
    targets = _default_phase_switch_targets(device_id, supported_phase_selections)
    if str(getattr(phase_map, "name", "")).upper() == "DEFAULT" and not tuple(phase_map.items()):
        return targets
    if not phase_map:
        return targets
    for raw_key, raw_value in phase_map.items():
        key = str(raw_key).strip().lower()
        selection = _PHASE_MAP_KEYS.get(key)
        if selection is None:
            raise ValueError(f"Unsupported PhaseMap key '{raw_key}'")
        if selection not in supported_phase_selections:
            continue
        targets[selection] = _parse_switch_channel_ids(raw_value, targets[selection])
    return targets


def phase_powers_for_selection(
    power_w: float,
    selection: PhaseSelection,
    single_phase_line: object = "L1",
) -> tuple[float, float, float]:
    """Split total power across the selected active phases for display."""
    total = float(power_w)
    if selection == "P1_P2_P3":
        per_phase = total / 3.0
        return per_phase, per_phase, per_phase
    if selection == "P1_P2":
        per_phase = total / 2.0
        return per_phase, per_phase, 0.0
    line = str(single_phase_line).strip().upper() if single_phase_line is not None else "L1"
    if line == "L2":
        return 0.0, total, 0.0
    if line == "L3":
        return 0.0, 0.0, total
    return total, 0.0, 0.0


def phase_currents_for_selection(
    current_a: float | None,
    selection: PhaseSelection,
    single_phase_line: object = "L1",
) -> tuple[float, float, float] | None:
    """Split total current across the selected active phases for display."""
    if current_a is None:
        return None
    total = float(current_a)
    if selection == "P1_P2_P3":
        per_phase = total / 3.0
        return per_phase, per_phase, per_phase
    if selection == "P1_P2":
        per_phase = total / 2.0
        return per_phase, per_phase, 0.0
    line = str(single_phase_line).strip().upper() if single_phase_line is not None else "L1"
    if line == "L2":
        return 0.0, total, 0.0
    if line == "L3":
        return 0.0, 0.0, total
    return total, 0.0, 0.0


@dataclass(frozen=True)
class ShellyBackendSettings:
    """Normalized Shelly backend config independent from service defaults."""

    host: str
    component: str
    device_id: int
    timeout_seconds: float
    username: str
    password: str
    use_digest_auth: bool
    phase_selection: PhaseSelection
    switching_mode: SwitchingMode
    supported_phase_selections: tuple[PhaseSelection, ...]
    requires_charge_pause_for_phase_change: bool
    max_direct_switch_power_w: float | None
    phase_switch_targets: dict[PhaseSelection, tuple[int, ...]]


def _config_value(defaults: configparser.SectionProxy, key: str, fallback: object) -> str:
    """Return one config value with a typed fallback."""
    return defaults.get(key, str(fallback))


def _config(defaults_path: str) -> configparser.ConfigParser:
    """Load one backend config file, or return an empty parser for inline defaults."""
    parser = configparser.ConfigParser()
    if defaults_path:
        read_files = parser.read(defaults_path)
        if not read_files:
            raise FileNotFoundError(defaults_path)
    return parser


def _section(parser: configparser.ConfigParser, name: str) -> configparser.SectionProxy:
    """Return one named section or DEFAULT when absent."""
    return parser[name] if parser.has_section(name) else parser["DEFAULT"]


def load_shelly_backend_settings(
    service: Any,
    config_path: str = "",
    *,
    default_switching_mode: SwitchingMode = "direct",
) -> ShellyBackendSettings:
    """Return one normalized Shelly backend config from file plus service defaults."""
    parser = _config(str(config_path).strip())
    adapter = _section(parser, "Adapter")
    phase = _section(parser, "Phase")
    capabilities = _section(parser, "Capabilities")
    phase_map = _section(parser, "PhaseMap")
    default_phase = normalize_phase_selection(getattr(service, "phase", "L1"))
    device_id = int(_config_value(adapter, "Id", getattr(service, "pm_id", 0)))
    switching_mode = normalize_switching_mode(
        capabilities.get("SwitchingMode", default_switching_mode),
        default_switching_mode,
    )
    supported_phase_selections = parse_phase_selection_list(
        capabilities.get("SupportedPhaseSelections", "P1"),
        default=("P1",),
    )
    max_power = None if switching_mode == "contactor" else finite_float_or_none(capabilities.get("MaxDirectSwitchPowerWatts", None))
    if max_power is None and switching_mode != "contactor":
        max_current = finite_float_or_none(getattr(service, "max_current", None))
        voltage = finite_float_or_none(getattr(service, "_last_voltage", None))
        if max_current is not None and voltage is not None and max_current > 0 and voltage > 0:
            max_power = max_current * voltage

    return ShellyBackendSettings(
        host=str(adapter.get("Host", getattr(service, "host", ""))).strip(),
        component=str(adapter.get("Component", getattr(service, "pm_component", "Switch"))).strip() or "Switch",
        device_id=device_id,
        timeout_seconds=float(_config_value(adapter, "RequestTimeoutSeconds", getattr(service, "shelly_request_timeout_seconds", 2.0))),
        username=str(adapter.get("Username", getattr(service, "username", ""))).strip(),
        password=str(adapter.get("Password", getattr(service, "password", ""))).strip(),
        use_digest_auth=bool(
            normalize_binary_flag(adapter.get("DigestAuth", getattr(service, "use_digest_auth", False)))
        ),
        phase_selection=normalize_phase_selection(
            phase.get("MeasuredPhaseSelection", phase.get("MeasuredPhase", default_phase)),
            default_phase,
        ),
        switching_mode=switching_mode,
        supported_phase_selections=supported_phase_selections,
        requires_charge_pause_for_phase_change=bool(
            normalize_binary_flag(capabilities.get("RequiresChargePauseForPhaseChange", 0))
        ),
        max_direct_switch_power_w=max_power,
        phase_switch_targets=_phase_switch_targets(phase_map, device_id, supported_phase_selections),
    )


class ShellyBackendBase:
    """Common Shelly RPC/config helpers shared by separate backend implementations."""

    def __init__(
        self,
        service: Any,
        config_path: str = "",
        *,
        default_switching_mode: SwitchingMode = "direct",
    ) -> None:
        self.service = service
        self.config_path = str(config_path).strip()
        self.settings = load_shelly_backend_settings(
            service,
            self.config_path,
            default_switching_mode=default_switching_mode,
        )
        session = getattr(service, "session", None)
        self._session = cast(Any, session if session is not None else requests.Session())

    def _auth(self) -> HTTPDigestAuth | tuple[str, str] | None:
        """Return one optional auth object for Shelly HTTP calls."""
        settings = self.settings
        if settings.use_digest_auth and settings.username and settings.password:
            return HTTPDigestAuth(settings.username, settings.password)
        if settings.username and settings.password:
            return settings.username, settings.password
        return None

    @staticmethod
    def _encoded_rpc_params(params: Mapping[str, ShellyRpcScalar]) -> dict[str, str | int | float]:
        """Encode Shelly RPC parameters, keeping booleans lowercase."""
        encoded: dict[str, str | int | float] = {}
        for key, value in params.items():
            encoded[key] = str(value).lower() if isinstance(value, bool) else value
        return encoded

    def _rpc_url(self, method: str, params: Mapping[str, ShellyRpcScalar] | None = None) -> str:
        """Return one Shelly RPC URL for the configured backend target."""
        base = f"http://{self.settings.host}/rpc/{method}"
        if not params:
            return base
        return f"{base}?{urlencode(self._encoded_rpc_params(params))}"

    def _request_json(self, url: str) -> dict[str, object]:
        """Perform one requests-based JSON call."""
        kwargs: dict[str, object] = {
            "url": url,
            "timeout": float(self.settings.timeout_seconds),
        }
        auth = self._auth()
        if auth is not None:
            kwargs["auth"] = auth
        response = self._session.get(**kwargs)
        response.raise_for_status()
        return cast(dict[str, object], response.json())

    def _rpc_call(self, method: str, **params: ShellyRpcScalar) -> dict[str, object]:
        """Call one Shelly RPC method on the configured backend target."""
        return self._request_json(self._rpc_url(method, params))

    def _pm_status(self) -> ShellyPmStatus:
        """Return one Shelly PM status payload."""
        return cast(ShellyPmStatus, self._rpc_call(f"{self.settings.component}.GetStatus", id=self.settings.device_id))
