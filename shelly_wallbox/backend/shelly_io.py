# SPDX-License-Identifier: GPL-3.0-or-later
"""Shelly HTTP and relay-worker helpers for the Shelly wallbox service."""

from __future__ import annotations

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
)
from shelly_wallbox.core.contracts import finite_float_or_none


JsonObject = dict[str, object]
ShellyRpcScalar = str | int | float | bool
EncodedRpcScalar = str | int | float
PendingRelayCommand = tuple[bool | None, float | None]


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

    def _warn_if_direct_switching_under_load(self, relay_on: bool) -> None:
        """Warn when a direct Shelly relay is asked to break more load than configured."""
        if bool(relay_on):
            return
        limit_w = self._max_direct_switch_power_w()
        if limit_w is None:
            return
        power_w = self._current_confirmed_switch_load_power_w()
        if power_w is None or power_w <= limit_w:
            return
        svc = self.service
        warning = getattr(svc, "_warning_throttled", None)
        if not callable(warning):
            return
        interval = max(
            1.0,
            float(getattr(svc, "auto_shelly_soft_fail_seconds", 30.0) or 30.0),
        )
        warning(
            "direct-switch-under-load",
            interval,
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

    def _sync_charger_runtime_state(self, state: ChargerState, now: float | None = None) -> None:
        """Mirror one normalized charger readback into the service runtime state."""
        svc = self.service
        current_mode = getattr(svc, "virtual_mode", 0)
        state_at = svc._time_now() if now is None else float(now)
        svc._last_charger_state_enabled = None if state.enabled is None else bool(state.enabled)
        svc._last_charger_state_current_amps = (
            None if state.current_amps is None else float(state.current_amps)
        )
        svc._last_charger_state_phase_selection = (
            None if state.phase_selection is None else state.phase_selection
        )
        svc._last_charger_state_actual_current_amps = (
            None if state.actual_current_amps is None else float(state.actual_current_amps)
        )
        svc._last_charger_state_power_w = None if state.power_w is None else float(state.power_w)
        svc._last_charger_state_energy_kwh = (
            None if state.energy_kwh is None else float(state.energy_kwh)
        )
        status_text = getattr(state, "status_text", None)
        fault_text = getattr(state, "fault_text", None)
        svc._last_charger_state_status = None if status_text is None else str(status_text)
        svc._last_charger_state_fault = None if fault_text is None else str(fault_text)
        svc._last_charger_state_at = state_at
        mode_uses_auto_logic = getattr(svc, "_mode_uses_auto_logic", None)
        auto_mode_active = bool(mode_uses_auto_logic(current_mode)) if callable(mode_uses_auto_logic) else False
        if state.enabled is not None:
            svc.virtual_enable = int(bool(state.enabled))
            if not auto_mode_active:
                svc.virtual_startstop = int(bool(state.enabled))
        if state.current_amps is not None:
            svc.virtual_set_current = float(state.current_amps)
            svc._charger_target_current_amps = float(state.current_amps)
            svc._charger_target_current_applied_at = state_at
        if state.phase_selection is not None and self._phase_selection_switch_backend() is None:
            self._remember_phase_selection_state(
                supported=self._charger_supported_phase_selections(),
                requested=getattr(svc, "requested_phase_selection", state.phase_selection),
                active=state.phase_selection,
            )

    def _read_charger_state_best_effort(self, now: float | None = None) -> ChargerState | None:
        """Read native charger state without letting charger issues break meter/switch polling."""
        svc = self.service
        backend = self._charger_state_backend()
        if backend is None:
            return None
        try:
            state = cast(ChargerState, cast(Any, backend).read_charger_state())
        except Exception as error:  # pylint: disable=broad-except
            svc._mark_failure("charger")
            svc._warning_throttled(
                "charger-state-failed",
                svc.auto_shelly_soft_fail_seconds,
                "Charger state read failed: %s",
                error,
                exc_info=error,
            )
            return None
        self._sync_charger_runtime_state(state, now=now)
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
        if charger_backend is not None:
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
            return None
        switch_state: object = cast(Any, backend).read_switch_state()
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
        pm_status["_phase_selection"] = str(reading.phase_selection)
        if reading.phase_powers_w is not None:
            pm_status["_phase_powers_w"] = (
                float(reading.phase_powers_w[0]),
                float(reading.phase_powers_w[1]),
                float(reading.phase_powers_w[2]),
            )
        if reading.phase_currents_a is not None:
            pm_status["_phase_currents_a"] = (
                float(reading.phase_currents_a[0]),
                float(reading.phase_currents_a[1]),
                float(reading.phase_currents_a[2]),
            )
        return pm_status

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
        svc = self.service
        captured_at = finite_float_or_none(getattr(svc, "_last_charger_state_at", None))
        if captured_at is None:
            return None
        if max_age_seconds is not None:
            current = svc._time_now() if now is None else float(now)
            if (current - captured_at) > max(0.0, float(max_age_seconds)):
                return None
        enabled = getattr(svc, "_last_charger_state_enabled", None)
        current_amps = finite_float_or_none(getattr(svc, "_last_charger_state_current_amps", None))
        phase_selection_raw = getattr(svc, "_last_charger_state_phase_selection", None)
        actual_current_amps = finite_float_or_none(
            getattr(svc, "_last_charger_state_actual_current_amps", None)
        )
        power_w = finite_float_or_none(getattr(svc, "_last_charger_state_power_w", None))
        energy_kwh = finite_float_or_none(getattr(svc, "_last_charger_state_energy_kwh", None))
        status_text = getattr(svc, "_last_charger_state_status", None)
        fault_text = getattr(svc, "_last_charger_state_fault", None)
        if (
            enabled is None
            and current_amps is None
            and phase_selection_raw is None
            and actual_current_amps is None
            and power_w is None
            and energy_kwh is None
            and status_text is None
            and fault_text is None
        ):
            return None
        return ChargerState(
            enabled=None if enabled is None else bool(enabled),
            current_amps=current_amps,
            phase_selection=(
                None
                if phase_selection_raw is None
                else normalize_phase_selection(phase_selection_raw, "P1")
            ),
            actual_current_amps=actual_current_amps,
            power_w=power_w,
            energy_kwh=energy_kwh,
            status_text=None if status_text is None else str(status_text),
            fault_text=None if fault_text is None else str(fault_text),
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
        phase_selection = normalize_phase_selection(
            active_phase_selection
            if active_phase_selection is not None
            else (
                state.phase_selection
                if state.phase_selection is not None
                else getattr(svc, "active_phase_selection", "P1")
            ),
            "P1",
        )
        current_a = (
            float(state.actual_current_amps)
            if state.actual_current_amps is not None
            else (
                float(state.current_amps)
                if state.current_amps is not None
                else None
            )
        )
        power_w = 0.0 if state.power_w is None else float(state.power_w)
        energy_kwh = 0.0 if state.energy_kwh is None else float(state.energy_kwh)
        voltage_v = finite_float_or_none(getattr(svc, "_last_voltage", None))
        pm_status: ShellyPmStatus = {
            "apower": power_w,
            "aenergy": {"total": energy_kwh * 1000.0},
            "_phase_selection": phase_selection,
        }
        if relay_on is not None:
            pm_status["output"] = bool(relay_on)
        if current_a is not None:
            pm_status["current"] = current_a
        if voltage_v is not None:
            pm_status["voltage"] = float(voltage_v)
        return pm_status

    def _read_split_pm_status(
        self,
        charger_state: ChargerState | None = None,
        *,
        now: float | None = None,
    ) -> JsonObject:
        """Read one legacy-compatible PM payload through the configured split backends."""
        svc = self.service
        backend = self._split_meter_backend()
        supported_phase_selections = self._split_switch_supported_phase_selections()
        switch_state: object | None = None
        try:
            switch_state = self._split_switch_state()
        except Exception:  # pylint: disable=broad-except
            switch_state = None
        if backend is None:
            recent_charger_state = charger_state or self._runtime_cached_charger_state(
                now=now,
                max_age_seconds=float(getattr(svc, "auto_shelly_soft_fail_seconds", 0.0) or 0.0),
            )
            if recent_charger_state is None:
                raise RuntimeError(
                    "Split mode without meter backend requires fresh charger readback"
                )
            relay_on = recent_charger_state.enabled
            active_phase_selection: object | None = recent_charger_state.phase_selection
            if switch_state is not None:
                enabled = getattr(switch_state, "enabled", relay_on)
                relay_on = relay_on if enabled is None else bool(enabled)
                active_phase_selection = getattr(
                    switch_state,
                    "phase_selection",
                    active_phase_selection,
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
        reading = cast(Any, backend).read_meter()
        relay_on = reading.relay_on
        meter_phase_selection: object = reading.phase_selection
        if switch_state is not None:
            enabled = getattr(switch_state, "enabled", relay_on)
            relay_on = relay_on if enabled is None else bool(enabled)
            meter_phase_selection = getattr(switch_state, "phase_selection", meter_phase_selection)
        self._remember_phase_selection_state(
            supported=supported_phase_selections,
            requested=getattr(self.service, "requested_phase_selection", reading.phase_selection),
            active=meter_phase_selection,
        )
        return cast(JsonObject, self._pm_status_from_meter_reading(reading, relay_on=relay_on))

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
