# SPDX-License-Identifier: GPL-3.0-or-later
"""Shelly HTTP and relay-worker helpers for the Shelly wallbox service."""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping
from typing import Any, Protocol, TypedDict, cast
from urllib.parse import urlencode

from requests.auth import HTTPDigestAuth

from shelly_wallbox.backend.models import (
    ChargerState,
    MeterReading,
    PhaseSelection,
    normalize_phase_selection,
    normalize_phase_selection_tuple,
    phase_selection_count,
)
from shelly_wallbox.backend.modbus_transport import modbus_transport_issue_reason
from shelly_wallbox.core.common import (
    _charger_transport_retry_delay_seconds,
    _fresh_charger_retry_until,
)
from shelly_wallbox.core.contracts import finite_float_or_none


JsonObject = dict[str, object]
ShellyRpcScalar = str | int | float | bool
EncodedRpcScalar = str | int | float
PendingRelayCommand = tuple[bool | None, float | None]


def _single_phase_vector(total: float, single_phase_line: object) -> tuple[float, float, float]:
    """Return one single-phase vector mapped to the configured display line."""
    line = str(single_phase_line).strip().upper() if single_phase_line is not None else "L1"
    if line == "L2":
        return 0.0, float(total), 0.0
    if line == "L3":
        return 0.0, 0.0, float(total)
    return float(total), 0.0, 0.0


def _distributed_phase_vector(total: float, divisor: float) -> tuple[float, float, float]:
    """Return evenly distributed per-phase values for two- or three-phase totals."""
    per_phase = float(total) / float(divisor)
    return per_phase, per_phase, per_phase


def _phase_powers_for_selection(
    power_w: float,
    selection: PhaseSelection,
    single_phase_line: object = "L1",
) -> tuple[float, float, float]:
    """Split total power across the selected active phases for display."""
    total = float(power_w)
    if selection == "P1_P2_P3":
        return _distributed_phase_vector(total, 3.0)
    if selection == "P1_P2":
        distributed = _distributed_phase_vector(total, 2.0)
        return distributed[0], distributed[1], 0.0
    return _single_phase_vector(total, single_phase_line)


def _phase_currents_for_selection(
    current_a: float | None,
    selection: PhaseSelection,
    single_phase_line: object = "L1",
) -> tuple[float, float, float] | None:
    """Split total current across the selected active phases for display."""
    if current_a is None:
        return None
    total = float(current_a)
    if selection == "P1_P2_P3":
        return _distributed_phase_vector(total, 3.0)
    if selection == "P1_P2":
        distributed = _distributed_phase_vector(total, 2.0)
        return distributed[0], distributed[1], 0.0
    return _single_phase_vector(total, single_phase_line)


class ShellyEnergyData(TypedDict, total=False):
    """Known Shelly energy counters used by the wallbox service."""

    total: float


class ShellyPmStatus(TypedDict, total=False):
    """Known Shelly PM fields consumed by the wallbox service."""

    output: bool
    apower: float
    current: float
    voltage: float
    aenergy: ShellyEnergyData
    _pm_confirmed: bool
    _phase_selection: str
    _phase_powers_w: tuple[float, float, float]
    _phase_currents_a: tuple[float, float, float]


class _ResponseLike(Protocol):
    """Small protocol for requests-like responses used in tests and runtime."""

    def raise_for_status(self) -> None: ...

    def json(self) -> object: ...


class _SessionLike(Protocol):
    """Requests-session subset used by the Shelly I/O controller."""

    def get(self, **kwargs: object) -> _ResponseLike: ...


class _WorkerStopEventLike(Protocol):
    """Threading event subset used by the background I/O worker."""

    def is_set(self) -> bool: ...

    def wait(self, timeout: float) -> bool: ...


class _WorkerThreadLike(Protocol):
    """Thread subset used for the optional background I/O worker."""

    def is_alive(self) -> bool: ...

    def start(self) -> None: ...


class _RequestAuthKwargs(TypedDict, total=False):
    """Optional auth kwargs accepted by ``requests.Session.get``."""

    auth: HTTPDigestAuth | tuple[str, str]


class _RequestKwargs(_RequestAuthKwargs):
    """Common kwargs used for main and worker Shelly HTTP requests."""

    url: str
    timeout: float


class ShellyIoHost(Protocol):
    """Host attributes and callbacks required by ``ShellyIoController``."""

    session: _SessionLike
    use_digest_auth: bool
    username: str
    password: str
    host: str
    shelly_request_timeout_seconds: float
    pm_component: str
    pm_id: int
    _worker_session: _SessionLike | object
    auto_shelly_soft_fail_seconds: float
    virtual_mode: int
    _worker_poll_interval_seconds: float
    _worker_stop_event: _WorkerStopEventLike
    _worker_thread: _WorkerThreadLike | None
    _last_pm_status: ShellyPmStatus | JsonObject | None
    _last_pm_status_at: float | None
    _last_pm_status_confirmed: bool
    _last_voltage: float | None
    _relay_command_lock: Any
    _pending_relay_state: bool | None
    _pending_relay_requested_at: float | None
    relay_sync_timeout_seconds: float
    _relay_sync_expected_state: bool | None
    _relay_sync_requested_at: float | None
    _relay_sync_deadline_at: float | None
    _relay_sync_failure_reported: bool
    supported_phase_selections: tuple[str, ...]
    requested_phase_selection: str
    active_phase_selection: str
    virtual_startstop: int
    virtual_enable: int
    virtual_set_current: float
    _last_charger_state_enabled: bool | None
    _last_charger_state_current_amps: float | None
    _last_charger_state_phase_selection: PhaseSelection | None
    _last_charger_state_actual_current_amps: float | None
    _last_charger_state_power_w: float | None
    _last_charger_state_energy_kwh: float | None
    _last_charger_state_status: str | None
    _last_charger_state_fault: str | None
    _last_charger_state_at: float | None
    _last_charger_estimate_source: str | None
    _last_charger_estimate_at: float | None
    _charger_estimated_energy_kwh: float | None
    _charger_estimated_energy_at: float | None
    _charger_estimated_power_w: float | None
    _last_charger_transport_reason: str | None
    _last_charger_transport_source: str | None
    _last_charger_transport_detail: str | None
    _last_charger_transport_at: float | None
    _charger_retry_reason: str | None
    _charger_retry_source: str | None
    _charger_retry_until: float | None
    _source_retry_after: dict[str, float]
    _last_switch_feedback_closed: bool | None
    _last_switch_interlock_ok: bool | None
    _last_switch_feedback_at: float | None
    _charger_target_current_amps: float | None
    _charger_target_current_applied_at: float | None

    def _time_now(self) -> float: ...

    def _request(self, url: str) -> JsonObject: ...

    def _request_with_session(self, session: object, url: str) -> JsonObject: ...

    def rpc_call(self, method: str, **params: ShellyRpcScalar) -> JsonObject: ...

    def _rpc_call_with_session(
        self,
        session: object,
        method: str,
        **params: ShellyRpcScalar,
    ) -> JsonObject: ...

    def _build_local_pm_status(self, relay_on: bool) -> ShellyPmStatus: ...

    def _publish_local_pm_status(self, relay_on: bool, now: float | None = None) -> ShellyPmStatus: ...

    def _peek_pending_relay_command(self) -> PendingRelayCommand: ...

    def _clear_pending_relay_command(self, relay_on: bool) -> None: ...

    def _worker_fetch_pm_status(self) -> JsonObject: ...

    def _worker_apply_pending_relay_command(self) -> None: ...

    def _ensure_worker_state(self) -> None: ...

    def _update_worker_snapshot(self, **fields: object) -> None: ...

    def _mark_failure(self, source_key: str) -> None: ...

    def _warning_throttled(
        self,
        key: str,
        interval_seconds: float,
        message: str,
        *args: object,
        **kwargs: object,
    ) -> None: ...

    def _mark_recovery(self, source_key: str, message: str, *args: object) -> None: ...

    def _source_retry_ready(self, source_key: str, now: float) -> bool: ...

    def _delay_source_retry(
        self,
        source_key: str,
        now: float,
        delay_seconds: float | None = None,
    ) -> None: ...

    def _mark_relay_changed(self, relay_on: bool, changed_at: float) -> None: ...

    def _mode_uses_auto_logic(self, mode: object) -> bool: ...

    def _ensure_auto_input_helper_process(self) -> None: ...


class ShellyIoController:
    """Encapsulate Shelly HTTP access and relay queue/worker behavior."""

    def __init__(self, service: ShellyIoHost) -> None:
        self.service = service

    def _runtime_now(self) -> float:
        """Return one best-effort current timestamp for runtime helpers and tests."""
        time_now = getattr(self.service, "_time_now", None)
        if callable(time_now):
            value = time_now()
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
        return 0.0

    @staticmethod
    def _encoded_rpc_params(params: Mapping[str, ShellyRpcScalar]) -> dict[str, EncodedRpcScalar]:
        """Encode Shelly RPC query parameters, keeping booleans lowercase."""
        encoded: dict[str, EncodedRpcScalar] = {}
        for key, value in params.items():
            encoded[key] = str(value).lower() if isinstance(value, bool) else value
        return encoded

    def _request_auth_kwargs(self) -> _RequestAuthKwargs:
        """Return optional request auth kwargs for the configured Shelly auth mode."""
        svc = self.service
        if svc.use_digest_auth:
            return {"auth": HTTPDigestAuth(svc.username, svc.password)}
        if svc.username and svc.password:
            return {"auth": (svc.username, svc.password)}
        return {}

    def _request_kwargs(self, url: str) -> _RequestKwargs:
        """Return common request kwargs for main or worker HTTP sessions."""
        svc = self.service
        kwargs: _RequestKwargs = {
            "url": url,
            "timeout": float(getattr(svc, "shelly_request_timeout_seconds", 2.0)),
        }
        auth_kwargs = self._request_auth_kwargs()
        if "auth" in auth_kwargs:
            kwargs["auth"] = auth_kwargs["auth"]
        return kwargs

    def _rpc_url(self, method: str, params: Mapping[str, ShellyRpcScalar] | None) -> str:
        """Build a Shelly RPC URL including optional query parameters."""
        svc = self.service
        if not params:
            return f"http://{svc.host}/rpc/{method}"
        return f"http://{svc.host}/rpc/{method}?{urlencode(self._encoded_rpc_params(params))}"

    def _uses_split_backends(self) -> bool:
        """Return whether runtime configuration selected separate meter/switch backends."""
        selection = getattr(self.service, "_backend_selection", None)
        return str(getattr(selection, "mode", "")).strip().lower() == "split"

    def _split_meter_backend(self) -> object | None:
        """Return the configured split meter backend when available."""
        if not self._uses_split_backends():
            return None
        backend = getattr(self.service, "_meter_backend", None)
        return backend if hasattr(backend, "read_meter") else None

    def _split_switch_backend(self) -> object | None:
        """Return the configured split switch backend when available."""
        if not self._uses_split_backends():
            return None
        backend = getattr(self.service, "_switch_backend", None)
        return backend if hasattr(backend, "set_enabled") else None

    def _split_enable_backend(self) -> object | None:
        """Return the preferred split enable backend, falling back from switch to charger."""
        backend = self._split_switch_backend()
        if backend is not None:
            return backend
        if not self._uses_split_backends():
            return None
        charger_backend = getattr(self.service, "_charger_backend", None)
        return charger_backend if hasattr(charger_backend, "set_enabled") else None

    def _split_enable_source_key(self) -> str:
        """Return the observability source key for split enable/disable control."""
        backend = self._split_enable_backend()
        if backend is not None and backend is getattr(self.service, "_charger_backend", None):
            return "charger"
        return "shelly"

    def _split_enable_source_label(self) -> str:
        """Return one human-readable label for the current split enable backend."""
        return "charger backend" if self._split_enable_source_key() == "charger" else "Shelly relay"

    def _phase_selection_switch_backend(self) -> object | None:
        """Return the configured switch backend when it can switch phases."""
        backend = getattr(self.service, "_switch_backend", None)
        return backend if hasattr(backend, "set_phase_selection") else None

    def _phase_selection_charger_backend(self) -> object | None:
        """Return the configured charger backend when it can switch phases."""
        backend = getattr(self.service, "_charger_backend", None)
        return backend if hasattr(backend, "set_phase_selection") else None

    def _charger_supports_phase_selection(self, selection: PhaseSelection) -> bool:
        """Return whether the configured charger backend should receive this phase selection."""
        backend = self._phase_selection_charger_backend()
        if backend is None:
            return False
        settings = getattr(backend, "settings", None)
        if settings is None or not hasattr(settings, "supported_phase_selections"):
            return True
        supported = normalize_phase_selection_tuple(
            getattr(settings, "supported_phase_selections", ("P1",)),
            ("P1",),
        )
        return selection in supported

    def _charger_state_backend(self) -> object | None:
        """Return the configured charger backend when it exposes normalized state."""
        backend = getattr(self.service, "_charger_backend", None)
        return backend if hasattr(backend, "read_charger_state") else None

    def _charger_supported_phase_selections(self) -> tuple[PhaseSelection, ...]:
        """Return one supported-phase tuple derived from the charger backend when available."""
        backend = getattr(self.service, "_charger_backend", None)
        settings = getattr(backend, "settings", None)
        return normalize_phase_selection_tuple(
            getattr(settings, "supported_phase_selections", getattr(self.service, "supported_phase_selections", ("P1",))),
            ("P1",),
        )

    def _phase_switch_capabilities(self) -> object | None:
        """Return normalized switch capabilities when the backend exposes them."""
        backend = getattr(self.service, "_switch_backend", None)
        if backend is None or not hasattr(backend, "capabilities"):
            return None
        try:
            capabilities: object = backend.capabilities()
            return capabilities
        except Exception:  # pylint: disable=broad-except
            return None

    def _switching_mode(self) -> str:
        """Return the normalized switching mode of the current switch backend."""
        capabilities = self._phase_switch_capabilities()
        mode = str(getattr(capabilities, "switching_mode", "direct")).strip().lower()
        return "contactor" if mode == "contactor" else "direct"

    def _max_direct_switch_power_w(self) -> float | None:
        """Return the configured direct-switch power limit when one is enforced."""
        if self._switching_mode() == "contactor":
            return None
        capabilities = self._phase_switch_capabilities()
        limit = finite_float_or_none(getattr(capabilities, "max_direct_switch_power_w", None))
        return None if limit is None or limit <= 0.0 else float(limit)

    def _current_confirmed_switch_load_power_w(self) -> float | None:
        """Return the latest confirmed wallbox power used as a direct-switching safety hint."""
        svc = self.service
        if not bool(getattr(svc, "_last_pm_status_confirmed", False)):
            return None
        pm_status = getattr(svc, "_last_pm_status", None)
        if not isinstance(pm_status, dict):
            return None
        power_w = finite_float_or_none(pm_status.get("apower"))
        return None if power_w is None else abs(float(power_w))

    def _direct_switch_warning_context(self, relay_on: bool) -> tuple[float, float] | None:
        """Return power-limit context when a direct relay-off request deserves a warning."""
        if bool(relay_on):
            return None
        limit_w = self._max_direct_switch_power_w()
        if limit_w is None:
            return None
        power_w = self._current_confirmed_switch_load_power_w()
        if power_w is None or power_w <= limit_w:
            return None
        return power_w, limit_w

    def _direct_switch_warning_interval(self) -> float:
        """Return throttling interval for direct-switch under-load warnings."""
        return max(
            1.0,
            float(getattr(self.service, "auto_shelly_soft_fail_seconds", 30.0) or 30.0),
        )

    def _warn_if_direct_switching_under_load(self, relay_on: bool) -> None:
        """Warn when a direct Shelly relay is asked to break more load than configured."""
        warning_context = self._direct_switch_warning_context(relay_on)
        if warning_context is None:
            return
        power_w, limit_w = warning_context
        svc = self.service
        warning = getattr(svc, "_warning_throttled", None)
        if not callable(warning):
            return
        warning(
            "direct-switch-under-load",
            self._direct_switch_warning_interval(),
            "Direct Shelly relay OFF requested at %.1fW above configured direct switch limit %.1fW; consider switching_mode=contactor",
            power_w,
            limit_w,
        )

    def _remember_phase_selection_state(
        self,
        *,
        active: object | None = None,
        requested: object | None = None,
        supported: object | None = None,
    ) -> None:
        """Keep normalized runtime phase-selection state in sync with backend observations."""
        svc = self.service
        supported_default = tuple(getattr(svc, "supported_phase_selections", ("P1",)))
        normalized_supported = normalize_phase_selection_tuple(
            supported if supported is not None else supported_default,
            supported_default or ("P1",),
        )
        svc.supported_phase_selections = normalized_supported
        default_phase_selection = normalized_supported[0]
        normalized_requested = normalize_phase_selection(
            requested if requested is not None else getattr(svc, "requested_phase_selection", default_phase_selection),
            default_phase_selection,
        )
        svc.requested_phase_selection = normalized_requested
        svc.active_phase_selection = normalize_phase_selection(
            active if active is not None else getattr(svc, "active_phase_selection", normalized_requested),
            normalized_requested,
        )

    def _store_runtime_switch_snapshot(self, switch_state: object | None, now: float | None = None) -> None:
        """Mirror optional switch feedback/interlock readback into runtime state."""
        svc = self.service
        feedback_closed = self._optional_bool(None if switch_state is None else getattr(switch_state, "feedback_closed", None))
        interlock_ok = self._optional_bool(None if switch_state is None else getattr(switch_state, "interlock_ok", None))
        svc._last_switch_feedback_closed = feedback_closed
        svc._last_switch_interlock_ok = interlock_ok
        svc._last_switch_feedback_at = (
            None
            if feedback_closed is None and interlock_ok is None
            else (self._runtime_now() if now is None else float(now))
        )

    def _sync_charger_runtime_state(self, state: ChargerState, now: float | None = None) -> None:
        """Mirror one normalized charger readback into the service runtime state."""
        svc = self.service
        state_at = svc._time_now() if now is None else float(now)
        auto_mode_active = self._auto_mode_active(getattr(svc, "virtual_mode", 0))
        self._store_runtime_charger_snapshot(state, state_at)
        self._sync_virtual_enabled_state(state, auto_mode_active)
        self._sync_virtual_current_target(state, state_at)
        self._sync_runtime_phase_selection_from_charger(state)

    def _store_runtime_charger_snapshot(self, state: ChargerState, state_at: float) -> None:
        """Persist charger readback fields on the service runtime object."""
        svc = self.service
        enabled = getattr(state, "enabled", None)
        current_amps = getattr(state, "current_amps", None)
        phase_selection = getattr(state, "phase_selection", None)
        actual_current_amps = getattr(state, "actual_current_amps", None)
        power_w = getattr(state, "power_w", None)
        energy_kwh = getattr(state, "energy_kwh", None)
        status_text = getattr(state, "status_text", None)
        fault_text = getattr(state, "fault_text", None)
        svc._last_charger_state_enabled = self._optional_bool(enabled)
        svc._last_charger_state_current_amps = self._optional_float(current_amps)
        svc._last_charger_state_phase_selection = phase_selection
        svc._last_charger_state_actual_current_amps = self._optional_float(actual_current_amps)
        svc._last_charger_state_power_w = self._optional_float(power_w)
        svc._last_charger_state_energy_kwh = self._optional_float(energy_kwh)
        svc._last_charger_state_status = self._cached_optional_text(status_text)
        svc._last_charger_state_fault = self._cached_optional_text(fault_text)
        svc._last_charger_state_at = state_at

    @staticmethod
    def _optional_bool(value: object) -> bool | None:
        """Return one optional bool from runtime readback values."""
        return None if value is None else bool(value)

    @staticmethod
    def _optional_float(value: object) -> float | None:
        """Return one optional float from runtime readback values."""
        return finite_float_or_none(value)

    def _auto_mode_active(self, current_mode: object) -> bool:
        """Return whether the current runtime mode uses auto logic."""
        mode_uses_auto_logic = getattr(self.service, "_mode_uses_auto_logic", None)
        return bool(mode_uses_auto_logic(current_mode)) if callable(mode_uses_auto_logic) else False

    def _remember_charger_estimate(self, source: str, now: float | None = None) -> None:
        """Persist one charger estimate marker for DBus diagnostics."""
        svc = self.service
        captured_at = self._runtime_now() if now is None else float(now)
        svc._last_charger_estimate_source = str(source).strip() or None
        svc._last_charger_estimate_at = captured_at

    def _clear_charger_estimate(self) -> None:
        """Clear the current charger estimate marker after real readback resumed."""
        svc = self.service
        svc._last_charger_estimate_source = None
        svc._last_charger_estimate_at = None

    def _estimated_phase_voltage_v(self, selection: PhaseSelection) -> float:
        """Return the per-phase voltage used for meterless charger power estimation."""
        svc = self.service
        cached_voltage = finite_float_or_none(getattr(svc, "_last_voltage", None))
        if cached_voltage is None or cached_voltage <= 0.0:
            return 230.0
        phase_voltage = float(cached_voltage)
        if selection != "P1" and str(getattr(svc, "voltage_mode", "phase")).strip().lower() != "phase":
            phase_voltage = phase_voltage / math.sqrt(3.0)
        return 230.0 if phase_voltage <= 0.0 else float(phase_voltage)

    @staticmethod
    def _charging_like_status(state: ChargerState) -> bool:
        """Return whether charger status text indicates an active charging session."""
        status = str(getattr(state, "status_text", "") or "").strip().lower()
        return status.startswith("charging")

    @classmethod
    def _resolved_pm_charger_current(cls, state: ChargerState) -> float | None:
        """Return the best current value to expose on meterless synthesized PM status."""
        if state.actual_current_amps is not None:
            return float(state.actual_current_amps)
        if state.current_amps is None:
            return None
        if state.status_text is not None:
            return float(state.current_amps) if cls._charging_like_status(state) else 0.0
        if state.enabled is False:
            return 0.0
        return float(state.current_amps)

    def _estimated_charger_power_w(
        self,
        current_a: float | None,
        phase_selection: PhaseSelection,
    ) -> float | None:
        """Return one estimated total charge power from current, voltage, and phase count."""
        if current_a is None:
            return None
        return float(current_a) * self._estimated_phase_voltage_v(phase_selection) * float(
            phase_selection_count(phase_selection)
        )

    def _sync_estimated_charger_energy_cache(self, energy_kwh: float, power_w: float, now: float) -> None:
        """Keep the meterless charger energy estimate baseline in sync."""
        svc = self.service
        svc._charger_estimated_energy_kwh = max(0.0, float(energy_kwh))
        svc._charger_estimated_energy_at = float(now)
        svc._charger_estimated_power_w = max(0.0, float(power_w))

    def _integrated_estimated_charger_energy_kwh(self, power_w: float, now: float) -> float:
        """Integrate one running estimated energy counter from the last known charger power."""
        svc = self.service
        energy_kwh = finite_float_or_none(getattr(svc, "_charger_estimated_energy_kwh", None)) or 0.0
        last_at = finite_float_or_none(getattr(svc, "_charger_estimated_energy_at", None))
        last_power = finite_float_or_none(getattr(svc, "_charger_estimated_power_w", None))
        if last_at is not None and last_power is not None and float(now) > last_at:
            energy_kwh += (max(0.0, float(last_power)) * ((float(now) - last_at) / 3600.0)) / 1000.0
        self._sync_estimated_charger_energy_cache(energy_kwh, power_w, now)
        return energy_kwh

    def _sync_virtual_enabled_state(self, state: ChargerState, auto_mode_active: bool) -> None:
        """Mirror charger enabled readback onto virtual enable/start-stop state."""
        if state.enabled is None:
            return
        svc = self.service
        svc.virtual_enable = int(bool(state.enabled))
        if not auto_mode_active:
            svc.virtual_startstop = int(bool(state.enabled))

    def _sync_virtual_current_target(self, state: ChargerState, state_at: float) -> None:
        """Mirror charger target current readback into virtual current state."""
        if state.current_amps is None:
            return
        svc = self.service
        svc.virtual_set_current = float(state.current_amps)
        svc._charger_target_current_amps = float(state.current_amps)
        svc._charger_target_current_applied_at = state_at

    def _sync_runtime_phase_selection_from_charger(self, state: ChargerState) -> None:
        """Mirror charger-native phase selection into runtime state when switch cannot do it."""
        if state.phase_selection is None or self._phase_selection_switch_backend() is not None:
            return
        svc = self.service
        self._remember_phase_selection_state(
            supported=self._charger_supported_phase_selections(),
            requested=getattr(svc, "requested_phase_selection", state.phase_selection),
            active=state.phase_selection,
        )

    @staticmethod
    def _charger_transport_detail(error: BaseException) -> str:
        """Return one compact transport-error detail string for diagnostics."""
        detail = str(error).strip()
        return detail or error.__class__.__name__

    def _remember_charger_transport_issue(
        self,
        reason: str,
        source: str,
        error: BaseException,
        now: float | None = None,
    ) -> None:
        """Persist one normalized charger-transport issue for runtime diagnostics."""
        svc = self.service
        captured_at = self._runtime_now() if now is None else float(now)
        svc._last_charger_transport_reason = str(reason).strip() or None
        svc._last_charger_transport_source = str(source).strip() or None
        svc._last_charger_transport_detail = self._charger_transport_detail(error)
        svc._last_charger_transport_at = captured_at

    def _clear_charger_transport_issue(self) -> None:
        """Clear the remembered charger-transport issue after one successful request."""
        svc = self.service
        svc._last_charger_transport_reason = None
        svc._last_charger_transport_source = None
        svc._last_charger_transport_detail = None
        svc._last_charger_transport_at = None

    def _remember_charger_retry(
        self,
        reason: str,
        source: str,
        now: float | None = None,
    ) -> None:
        """Persist one charger retry-backoff window after a transport failure."""
        svc = self.service
        captured_at = self._runtime_now() if now is None else float(now)
        delay_seconds = _charger_transport_retry_delay_seconds(svc, reason)
        delay_retry = getattr(svc, "_delay_source_retry", None)
        if callable(delay_retry):
            delay_retry("charger", captured_at, delay_seconds)
        elif isinstance(getattr(svc, "_source_retry_after", None), dict):
            svc._source_retry_after["charger"] = captured_at + delay_seconds
        svc._charger_retry_reason = str(reason).strip() or None
        svc._charger_retry_source = str(source).strip() or None
        svc._charger_retry_until = captured_at + delay_seconds

    def _clear_charger_retry(self) -> None:
        """Clear the remembered charger retry-backoff state after recovery."""
        svc = self.service
        svc._charger_retry_reason = None
        svc._charger_retry_source = None
        svc._charger_retry_until = None
        if isinstance(getattr(svc, "_source_retry_after", None), dict):
            svc._source_retry_after["charger"] = 0.0

    def _charger_retry_active(self, now: float | None = None) -> bool:
        """Return whether charger polling/writes are still inside one retry backoff."""
        svc = self.service
        current = self._runtime_now() if now is None else float(now)
        return _fresh_charger_retry_until(svc, current) is not None

    def _read_charger_state_best_effort(self, now: float | None = None) -> ChargerState | None:
        """Read native charger state without letting charger issues break meter/switch polling."""
        svc = self.service
        backend = self._charger_state_backend()
        if backend is None:
            return None
        current = self._runtime_now() if now is None else float(now)
        if self._charger_retry_active(current):
            return None
        try:
            state = cast(ChargerState, cast(Any, backend).read_charger_state())
        except Exception as error:  # pylint: disable=broad-except
            transport_reason = modbus_transport_issue_reason(error)
            if transport_reason is not None:
                self._remember_charger_transport_issue(transport_reason, "read", error, current)
                self._remember_charger_retry(transport_reason, "read", current)
            svc._mark_failure("charger")
            svc._warning_throttled(
                "charger-state-failed",
                svc.auto_shelly_soft_fail_seconds,
                "Charger state read failed: %s",
                error,
                exc_info=error,
            )
            return None
        self._sync_charger_runtime_state(state, now=current)
        self._clear_charger_transport_issue()
        self._clear_charger_retry()
        svc._mark_recovery("charger", "Charger state reads recovered")
        return state

    def _split_switch_supported_phase_selections(self) -> tuple[str, ...]:
        """Return one normalized supported-phase tuple for the configured split switch backend."""
        capabilities = self._phase_switch_capabilities()
        if capabilities is None:
            if getattr(self.service, "_switch_backend", None) is None:
                return tuple(self._charger_supported_phase_selections())
            return tuple(getattr(self.service, "supported_phase_selections", ("P1",)))
        normalized: tuple[PhaseSelection, ...] = normalize_phase_selection_tuple(
            getattr(capabilities, "supported_phase_selections", getattr(self.service, "supported_phase_selections", ("P1",))),
            ("P1",),
        )
        return normalized

    def phase_selection_requires_pause(self) -> bool:
        """Return whether the current switch backend requires a paused charge for phase changes."""
        capabilities = self._phase_switch_capabilities()
        return bool(getattr(capabilities, "requires_charge_pause_for_phase_change", False))

    def set_phase_selection(self, selection: object) -> PhaseSelection:
        """Apply one normalized phase selection to the configured phase-capable backends."""
        supported_phase_selections = self._split_switch_supported_phase_selections()
        default_phase_selection = cast(PhaseSelection, supported_phase_selections[0])
        normalized_selection = normalize_phase_selection(selection, default_phase_selection)
        if normalized_selection not in supported_phase_selections:
            raise ValueError(
                f"Unsupported phase selection '{selection}' for configured backend "
                f"(supported: {','.join(supported_phase_selections)})"
            )

        switch_backend = self._phase_selection_switch_backend()
        if switch_backend is not None:
            cast(Any, switch_backend).set_phase_selection(normalized_selection)

        charger_backend = self._phase_selection_charger_backend()
        if charger_backend is not None and self._charger_supports_phase_selection(normalized_selection):
            cast(Any, charger_backend).set_phase_selection(normalized_selection)

        self._remember_phase_selection_state(
            supported=supported_phase_selections,
            requested=normalized_selection,
            active=normalized_selection,
        )
        return normalized_selection

    def _split_switch_state(self) -> object | None:
        """Return the current split switch state when the backend exposes one."""
        backend = self._split_switch_backend()
        if backend is None or not hasattr(backend, "read_switch_state"):
            self._store_runtime_switch_snapshot(None)
            return None
        switch_state: object = cast(Any, backend).read_switch_state()
        self._store_runtime_switch_snapshot(switch_state)
        return switch_state

    @staticmethod
    def _pm_status_from_meter_reading(
        reading: MeterReading,
        relay_on: bool | None = None,
    ) -> ShellyPmStatus:
        """Project one normalized backend reading onto the legacy Shelly PM payload shape."""
        pm_status: ShellyPmStatus = {
            "apower": float(reading.power_w),
            "aenergy": {"total": float(reading.energy_kwh) * 1000.0},
        }
        if reading.voltage_v is not None:
            pm_status["voltage"] = float(reading.voltage_v)
        if reading.current_a is not None:
            pm_status["current"] = float(reading.current_a)
        resolved_relay = reading.relay_on if relay_on is None else relay_on
        if resolved_relay is not None:
            pm_status["output"] = bool(resolved_relay)
        pm_status.update(cast(ShellyPmStatus, ShellyIoController._pm_status_phase_fields(reading)))
        return pm_status

    @staticmethod
    def _pm_status_phase_fields(reading: MeterReading) -> dict[str, object]:
        """Return optional phase-vector fields for one normalized meter reading."""
        fields: dict[str, object] = {"_phase_selection": str(reading.phase_selection)}
        if reading.phase_powers_w is not None:
            fields["_phase_powers_w"] = (
                float(reading.phase_powers_w[0]),
                float(reading.phase_powers_w[1]),
                float(reading.phase_powers_w[2]),
            )
        if reading.phase_currents_a is not None:
            fields["_phase_currents_a"] = (
                float(reading.phase_currents_a[0]),
                float(reading.phase_currents_a[1]),
                float(reading.phase_currents_a[2]),
            )
        return fields

    def _relay_state_from_split_switch(self, fallback: bool | None) -> bool | None:
        """Return the current split-switch state, falling back to meter-provided relay state."""
        try:
            state = self._split_switch_state()
        except Exception:  # pylint: disable=broad-except
            return fallback
        if state is None:
            return fallback
        enabled = getattr(state, "enabled", fallback)
        return fallback if enabled is None else bool(enabled)

    def _runtime_cached_charger_state(
        self,
        *,
        now: float | None = None,
        max_age_seconds: float | None = None,
    ) -> ChargerState | None:
        """Return cached charger state when one is present and optionally fresh enough."""
        captured_at = self._cached_charger_state_timestamp(
            now=now,
            max_age_seconds=max_age_seconds,
        )
        if captured_at is None:
            return None
        state = self._cached_charger_state_snapshot()
        if not self._charger_state_has_cached_data(state):
            return None
        return state

    def _cached_charger_state_timestamp(
        self,
        *,
        now: float | None = None,
        max_age_seconds: float | None = None,
    ) -> float | None:
        """Return the cached charger-state timestamp when one is present and fresh enough."""
        captured_at = finite_float_or_none(getattr(self.service, "_last_charger_state_at", None))
        if captured_at is None:
            return None
        if max_age_seconds is None:
            return captured_at
        current = self.service._time_now() if now is None else float(now)
        if (current - captured_at) > max(0.0, float(max_age_seconds)):
            return None
        return captured_at

    def _cached_charger_state_snapshot(self) -> ChargerState:
        """Return charger-state data reconstructed from runtime cache fields."""
        svc = self.service
        enabled = getattr(svc, "_last_charger_state_enabled", None)
        phase_selection_raw = getattr(svc, "_last_charger_state_phase_selection", None)
        return ChargerState(
            enabled=None if enabled is None else bool(enabled),
            current_amps=finite_float_or_none(getattr(svc, "_last_charger_state_current_amps", None)),
            phase_selection=(
                None if phase_selection_raw is None else normalize_phase_selection(phase_selection_raw, "P1")
            ),
            actual_current_amps=finite_float_or_none(getattr(svc, "_last_charger_state_actual_current_amps", None)),
            power_w=finite_float_or_none(getattr(svc, "_last_charger_state_power_w", None)),
            energy_kwh=finite_float_or_none(getattr(svc, "_last_charger_state_energy_kwh", None)),
            status_text=self._cached_optional_text(getattr(svc, "_last_charger_state_status", None)),
            fault_text=self._cached_optional_text(getattr(svc, "_last_charger_state_fault", None)),
        )

    @staticmethod
    def _cached_optional_text(value: object) -> str | None:
        """Return one cached runtime text field normalized to optional string."""
        return None if value is None else str(value)

    @staticmethod
    def _charger_state_has_cached_data(state: ChargerState) -> bool:
        """Return whether reconstructed charger state carries any cached information."""
        return any(
            value is not None
            for value in (
                state.enabled,
                state.current_amps,
                state.phase_selection,
                state.actual_current_amps,
                state.power_w,
                state.energy_kwh,
                state.status_text,
                state.fault_text,
            )
        )

    def _pm_status_from_charger_state(
        self,
        state: ChargerState,
        *,
        relay_on: bool | None,
        active_phase_selection: object | None = None,
    ) -> ShellyPmStatus:
        """Project charger-native readback onto the legacy Shelly PM payload shape."""
        svc = self.service
        current_time = self._runtime_now()
        phase_selection = self._resolved_pm_phase_selection(state, active_phase_selection)
        current_a = self._resolved_pm_charger_current(state)
        power_w = finite_float_or_none(state.power_w)
        power_estimated = False
        if power_w is None:
            power_w = self._estimated_charger_power_w(current_a, phase_selection)
            power_estimated = power_w is not None
        if power_w is None:
            power_w = 0.0
        energy_kwh = finite_float_or_none(state.energy_kwh)
        energy_estimated = False
        if energy_kwh is None:
            energy_kwh = self._integrated_estimated_charger_energy_kwh(power_w, current_time)
            energy_estimated = True
        else:
            self._sync_estimated_charger_energy_cache(energy_kwh, power_w, current_time)
        voltage_v = finite_float_or_none(getattr(svc, "_last_voltage", None))
        if voltage_v is None and (power_estimated or energy_estimated):
            voltage_v = self._estimated_phase_voltage_v(phase_selection)
        if power_estimated or energy_estimated:
            self._remember_charger_estimate(
                "current-voltage-phase" if power_estimated else "power-time",
                current_time,
            )
        else:
            self._clear_charger_estimate()
        pm_status = self._charger_pm_status_base(power_w, energy_kwh, phase_selection)
        self._apply_optional_pm_output(pm_status, relay_on)
        self._apply_optional_pm_current(pm_status, current_a)
        self._apply_optional_pm_voltage(pm_status, voltage_v)
        phase_currents = _phase_currents_for_selection(
            current_a,
            phase_selection,
            getattr(svc, "phase", "L1"),
        )
        if phase_currents is not None:
            pm_status["_phase_currents_a"] = phase_currents
        pm_status["_phase_powers_w"] = _phase_powers_for_selection(
            power_w,
            phase_selection,
            getattr(svc, "phase", "L1"),
        )
        return pm_status

    @staticmethod
    def _charger_pm_status_base(
        power_w: float,
        energy_kwh: float,
        phase_selection: PhaseSelection,
    ) -> ShellyPmStatus:
        """Return the base PM payload shared by charger-native projection."""
        return {
            "apower": power_w,
            "aenergy": {"total": energy_kwh * 1000.0},
            "_phase_selection": phase_selection,
        }

    @staticmethod
    def _apply_optional_pm_output(pm_status: ShellyPmStatus, value: bool | None) -> None:
        """Apply optional relay output field when one is available."""
        if value is not None:
            pm_status["output"] = bool(value)

    @staticmethod
    def _apply_optional_pm_current(pm_status: ShellyPmStatus, value: float | None) -> None:
        """Apply optional current field when one is available."""
        if value is not None:
            pm_status["current"] = float(value)

    @staticmethod
    def _apply_optional_pm_voltage(pm_status: ShellyPmStatus, value: float | None) -> None:
        """Apply optional voltage field when one is available."""
        if value is not None:
            pm_status["voltage"] = float(value)

    def _resolved_pm_phase_selection(
        self,
        state: ChargerState,
        active_phase_selection: object | None,
    ) -> PhaseSelection:
        """Return the effective phase selection for charger-native PM projection."""
        svc = self.service
        raw_selection = (
            active_phase_selection
            if active_phase_selection is not None
            else (
                state.phase_selection
                if state.phase_selection is not None
                else getattr(svc, "active_phase_selection", "P1")
            )
        )
        return normalize_phase_selection(raw_selection, "P1")

    @staticmethod
    def _resolved_charger_current(state: ChargerState) -> float | None:
        """Return the best current value available from charger-native readback."""
        if state.actual_current_amps is not None:
            return float(state.actual_current_amps)
        if state.current_amps is not None:
            return float(state.current_amps)
        return None

    def _safe_split_switch_state(self) -> object | None:
        """Return switch state best-effort without letting switch read failures bubble up."""
        try:
            return self._split_switch_state()
        except Exception:  # pylint: disable=broad-except
            self._store_runtime_switch_snapshot(None)
            return None

    def _runtime_cached_charger_state_for_split(self, now: float | None) -> ChargerState | None:
        """Return fresh enough cached charger state for split meterless fallback."""
        max_age_seconds = float(getattr(self.service, "auto_shelly_soft_fail_seconds", 0.0) or 0.0)
        return self._runtime_cached_charger_state(
            now=now,
            max_age_seconds=max_age_seconds,
        )

    def _resolved_switch_overrides(
        self,
        switch_state: object | None,
        relay_on: bool | None,
        phase_selection: object | None,
    ) -> tuple[bool | None, object | None]:
        """Return relay and phase values after best-effort switch-state overrides."""
        if switch_state is None:
            return relay_on, phase_selection
        enabled = getattr(switch_state, "enabled", relay_on)
        overridden_relay = relay_on if enabled is None else bool(enabled)
        overridden_phase = getattr(switch_state, "phase_selection", phase_selection)
        return overridden_relay, overridden_phase

    def _read_split_pm_status_without_meter(
        self,
        switch_state: object | None,
        supported_phase_selections: tuple[str, ...],
        charger_state: ChargerState | None,
        now: float | None,
    ) -> JsonObject:
        """Return split-mode PM payload synthesized from charger readback without meter backend."""
        svc = self.service
        recent_charger_state = charger_state or self._runtime_cached_charger_state_for_split(now)
        if recent_charger_state is None:
            raise RuntimeError("Split mode without meter backend requires fresh charger readback")
        relay_on, active_phase_selection = self._resolved_switch_overrides(
            switch_state,
            recent_charger_state.enabled,
            recent_charger_state.phase_selection,
        )
        self._remember_phase_selection_state(
            supported=supported_phase_selections,
            requested=getattr(
                svc,
                "requested_phase_selection",
                active_phase_selection if active_phase_selection is not None else "P1",
            ),
            active=active_phase_selection,
        )
        return cast(
            JsonObject,
            self._pm_status_from_charger_state(
                recent_charger_state,
                relay_on=relay_on,
                active_phase_selection=active_phase_selection,
            ),
        )

    def _read_split_pm_status_with_meter(
        self,
        backend: object,
        switch_state: object | None,
        supported_phase_selections: tuple[str, ...],
    ) -> JsonObject:
        """Return split-mode PM payload using the configured meter backend."""
        reading = cast(Any, backend).read_meter()
        relay_on, active_phase_selection = self._resolved_switch_overrides(
            switch_state,
            reading.relay_on,
            reading.phase_selection,
        )
        self._remember_phase_selection_state(
            supported=supported_phase_selections,
            requested=getattr(self.service, "requested_phase_selection", reading.phase_selection),
            active=active_phase_selection,
        )
        return cast(JsonObject, self._pm_status_from_meter_reading(reading, relay_on=relay_on))

    def _read_split_pm_status(
        self,
        charger_state: ChargerState | None = None,
        *,
        now: float | None = None,
    ) -> JsonObject:
        """Read one legacy-compatible PM payload through the configured split backends."""
        backend = self._split_meter_backend()
        supported_phase_selections = self._split_switch_supported_phase_selections()
        switch_state = self._safe_split_switch_state()
        if backend is None:
            return self._read_split_pm_status_without_meter(
                switch_state,
                supported_phase_selections,
                charger_state,
                now,
            )
        return self._read_split_pm_status_with_meter(
            backend,
            switch_state,
            supported_phase_selections,
        )

    @staticmethod
    def _json_object(value: object) -> JsonObject:
        """Return JSON responses as a typed object mapping."""
        return cast(JsonObject, value)

    def request(self, url: str) -> JsonObject:
        """Perform a Shelly HTTP request through the main-session client."""
        response = self.service.session.get(**self._request_kwargs(url))
        response.raise_for_status()
        return self._json_object(response.json())

    def request_with_session(self, session: _SessionLike, url: str) -> JsonObject:
        """Perform a Shelly HTTP request through a specific requests session."""
        response = session.get(**self._request_kwargs(url))
        response.raise_for_status()
        return self._json_object(response.json())

    def rpc_call(self, method: str, **params: ShellyRpcScalar) -> JsonObject:
        """Perform a Shelly RPC call with query encoding."""
        return self.service._request(self._rpc_url(method, params))

    def rpc_call_with_session(
        self,
        session: object,
        method: str,
        **params: ShellyRpcScalar,
    ) -> JsonObject:
        """Perform a Shelly RPC call through a specific requests session."""
        return self.service._request_with_session(session, self._rpc_url(method, params))

    def fetch_pm_status_rpc(self) -> JsonObject:
        """Fetch Shelly component status for power data through the legacy direct RPC path."""
        svc = self.service
        return svc.rpc_call(f"{svc.pm_component}.GetStatus", id=svc.pm_id)

    def fetch_pm_status(self) -> JsonObject:
        """Fetch PM status through the selected runtime backend seam."""
        now = self._runtime_now()
        charger_state = self._read_charger_state_best_effort(now=now)
        if self._uses_split_backends():
            return self._read_split_pm_status(charger_state, now=now)
        return self.fetch_pm_status_rpc()

    def set_relay_rpc(self, on: bool) -> JsonObject:
        """Switch the Shelly relay output through the legacy direct RPC path."""
        svc = self.service
        return svc.rpc_call("Switch.Set", id=svc.pm_id, on=bool(on))

    def set_relay(self, on: bool) -> JsonObject:
        """Switch relay state through the selected runtime backend seam."""
        backend = self._split_enable_backend()
        if backend is not None:
            cast(Any, backend).set_enabled(bool(on))
            return {"output": bool(on)}
        return self.set_relay_rpc(on)

    def worker_fetch_pm_status_rpc(self) -> JsonObject:
        """Fetch Shelly power status from the background worker session."""
        svc = self.service
        return svc._rpc_call_with_session(
            svc._worker_session,
            f"{svc.pm_component}.GetStatus",
            id=svc.pm_id,
        )

    def worker_fetch_pm_status(self) -> JsonObject:
        """Fetch PM status for the worker, using split backends when configured."""
        now = self._runtime_now()
        charger_state = self._read_charger_state_best_effort(now=now)
        if self._uses_split_backends():
            return self._read_split_pm_status(charger_state, now=now)
        return self.worker_fetch_pm_status_rpc()

    @staticmethod
    def _normalized_energy_payload(value: object) -> ShellyEnergyData:
        """Return a normalized Shelly energy payload with a safe ``total`` field."""
        payload: ShellyEnergyData = {}
        if isinstance(value, dict):
            total = value.get("total")
            if isinstance(total, (int, float)) and not isinstance(total, bool):
                payload["total"] = float(total)
        payload.setdefault("total", 0.0)
        return payload

    def build_local_pm_status(self, relay_on: bool) -> ShellyPmStatus:
        """Build an optimistic local Shelly status after a direct relay command."""
        svc = self.service
        source = getattr(svc, "_last_pm_status", None)
        raw_status = dict(source) if isinstance(source, dict) else {}
        pm_status = cast(ShellyPmStatus, raw_status)
        last_voltage = getattr(svc, "_last_voltage", None)
        voltage = float(last_voltage) if isinstance(last_voltage, (int, float)) and not isinstance(last_voltage, bool) else 230.0
        pm_status["output"] = bool(relay_on)
        pm_status["voltage"] = float(pm_status.get("voltage", voltage) or voltage)
        pm_status["aenergy"] = self._normalized_energy_payload(pm_status.get("aenergy"))
        # A locally published relay command is only an optimistic placeholder
        # until the next confirmed Shelly read. Keep instantaneous power/current
        # at zero so stale measurements are never interpreted as a fresh charge.
        pm_status["apower"] = 0.0
        pm_status["current"] = 0.0
        return pm_status

    def publish_local_pm_status(self, relay_on: bool, now: float | None = None) -> ShellyPmStatus:
        """Publish a best-effort local Shelly status immediately after relay writes."""
        svc = self.service
        current = svc._time_now() if now is None else float(now)
        pm_status = svc._build_local_pm_status(relay_on)
        pm_status["_pm_confirmed"] = False
        svc._last_pm_status = dict(pm_status)
        svc._last_pm_status_at = current
        svc._last_pm_status_confirmed = False
        svc._update_worker_snapshot(
            captured_at=current,
            pm_captured_at=current,
            pm_status=pm_status,
            pm_confirmed=False,
        )
        return pm_status

    def queue_relay_command(self, relay_on: bool, now: float | None = None) -> None:
        """Queue a relay command for the Shelly worker thread."""
        svc = self.service
        svc._ensure_worker_state()
        current = svc._time_now() if now is None else float(now)
        self._warn_if_direct_switching_under_load(bool(relay_on))
        with svc._relay_command_lock:
            svc._pending_relay_state = bool(relay_on)
            svc._pending_relay_requested_at = current
            svc._relay_sync_expected_state = bool(relay_on)
            svc._relay_sync_requested_at = current
            svc._relay_sync_deadline_at = current + float(getattr(svc, "relay_sync_timeout_seconds", 2.0))
            svc._relay_sync_failure_reported = False

    def peek_pending_relay_command(self) -> PendingRelayCommand:
        """Return the latest queued relay command without clearing it."""
        svc = self.service
        svc._ensure_worker_state()
        with svc._relay_command_lock:
            return svc._pending_relay_state, svc._pending_relay_requested_at

    def clear_pending_relay_command(self, relay_on: bool) -> None:
        """Clear a processed relay command if it still matches the latest request."""
        svc = self.service
        svc._ensure_worker_state()
        with svc._relay_command_lock:
            if svc._pending_relay_state == bool(relay_on):
                svc._pending_relay_state = None
                svc._pending_relay_requested_at = None

    def worker_apply_pending_relay_command(self) -> None:
        """Execute queued relay writes in the Shelly worker thread."""
        svc = self.service
        target_on, _requested_at = svc._peek_pending_relay_command()
        if target_on is None:
            return
        source_key = self._split_enable_source_key()
        source_label = self._split_enable_source_label()
        current = self._runtime_now()
        if source_key == "charger" and self._charger_retry_active(current):
            return
        try:
            backend = self._split_enable_backend()
            if backend is not None:
                cast(Any, backend).set_enabled(bool(target_on))
            else:
                svc._rpc_call_with_session(
                    svc._worker_session,
                    "Switch.Set",
                    id=svc.pm_id,
                    on=bool(target_on),
                )
        except Exception as error:  # pylint: disable=broad-except
            if source_key == "charger":
                transport_reason = modbus_transport_issue_reason(error)
                if transport_reason is not None:
                    self._remember_charger_transport_issue(transport_reason, "enable", error, current)
                    self._remember_charger_retry(transport_reason, "enable", current)
            svc._mark_failure(source_key)
            svc._warning_throttled(
                f"worker-{source_key}-switch-failed",
                svc.auto_shelly_soft_fail_seconds,
                "%s switch failed: %s",
                source_label,
                error,
                exc_info=error,
            )
            return

        completed_at = svc._time_now()
        svc._clear_pending_relay_command(bool(target_on))
        svc._mark_relay_changed(bool(target_on), completed_at)
        if source_key == "shelly":
            svc._mark_recovery("shelly", "Shelly relay writes recovered")
        else:
            self._clear_charger_transport_issue()
            self._clear_charger_retry()
            svc._mark_recovery(source_key, "%s writes recovered", source_label)
        svc._publish_local_pm_status(bool(target_on), completed_at)

    def io_worker_once(self) -> None:
        """Run one Shelly polling cycle and publish its RAM snapshot."""
        svc = self.service
        svc._ensure_worker_state()
        now = svc._time_now()
        svc._update_worker_snapshot(
            captured_at=now,
            auto_mode_active=svc._mode_uses_auto_logic(getattr(svc, "virtual_mode", 0)),
        )
        svc._worker_apply_pending_relay_command()

        try:
            pm_status = svc._worker_fetch_pm_status()
            read_at = svc._time_now()
            svc._mark_recovery("shelly", "Shelly status reads recovered")
            svc._update_worker_snapshot(
                captured_at=read_at,
                pm_captured_at=read_at,
                auto_mode_active=svc._mode_uses_auto_logic(getattr(svc, "virtual_mode", 0)),
                pm_status=pm_status,
                pm_confirmed=True,
            )
        except Exception as error:  # pylint: disable=broad-except
            svc._mark_failure("shelly")
            svc._warning_throttled(
                "worker-shelly-read-failed",
                svc.auto_shelly_soft_fail_seconds,
                "Shelly status read failed: %s",
                error,
                exc_info=error,
            )
            # Clear the live PM payload after a failed read so the main loop can
            # fall back to the short soft-fail cache instead of treating an old
            # confirmed snapshot as perpetually current.
            svc._update_worker_snapshot(
                captured_at=now,
                auto_mode_active=svc._mode_uses_auto_logic(getattr(svc, "virtual_mode", 0)),
                pm_status=None,
                pm_captured_at=None,
                pm_confirmed=False,
            )

    def io_worker_loop(self) -> None:
        """Continuously collect Shelly status without blocking the GLib main thread."""
        svc = self.service
        svc._ensure_worker_state()
        while not svc._worker_stop_event.is_set():
            cycle_started = svc._time_now()
            try:
                self.io_worker_once()
            except Exception as error:  # pylint: disable=broad-except
                svc._warning_throttled(
                    "io-worker-cycle-failed",
                    max(1.0, svc._worker_poll_interval_seconds),
                    "Background I/O worker cycle failed: %s",
                    error,
                    exc_info=error,
                )

            wait_seconds = max(0.05, svc._worker_poll_interval_seconds - (svc._time_now() - cycle_started))
            if svc._worker_stop_event.wait(wait_seconds):
                break

    def start_io_worker(self) -> None:
        """Start the Shelly worker and make sure the Auto helper process is running."""
        svc = self.service
        svc._ensure_worker_state()
        if svc._worker_thread is None or not svc._worker_thread.is_alive():
            svc._worker_thread = threading.Thread(
                target=self.io_worker_loop,
                name="shelly-wallbox-shelly-io",
                daemon=True,
            )
            svc._worker_thread.start()
        svc._ensure_auto_input_helper_process()
