# SPDX-License-Identifier: GPL-3.0-or-later
"""Shelly HTTP and relay-worker helpers for the Shelly wallbox service."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any, Protocol, TypedDict, cast
from urllib.parse import urlencode

from requests.auth import HTTPDigestAuth


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

    def fetch_pm_status(self) -> JsonObject:
        """Fetch Shelly component status for power data."""
        svc = self.service
        return svc.rpc_call(f"{svc.pm_component}.GetStatus", id=svc.pm_id)

    def set_relay(self, on: bool) -> JsonObject:
        """Switch the Shelly relay output."""
        svc = self.service
        return svc.rpc_call("Switch.Set", id=svc.pm_id, on=bool(on))

    def worker_fetch_pm_status(self) -> JsonObject:
        """Fetch Shelly power status from the background worker session."""
        svc = self.service
        return svc._rpc_call_with_session(
            svc._worker_session,
            f"{svc.pm_component}.GetStatus",
            id=svc.pm_id,
        )

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
        try:
            svc._rpc_call_with_session(
                svc._worker_session,
                "Switch.Set",
                id=svc.pm_id,
                on=bool(target_on),
            )
        except Exception as error:  # pylint: disable=broad-except
            svc._mark_failure("shelly")
            svc._warning_throttled(
                "worker-shelly-switch-failed",
                svc.auto_shelly_soft_fail_seconds,
                "Shelly relay switch failed: %s",
                error,
                exc_info=error,
            )
            return

        completed_at = svc._time_now()
        svc._clear_pending_relay_command(bool(target_on))
        svc._mark_relay_changed(bool(target_on), completed_at)
        svc._mark_recovery("shelly", "Shelly relay writes recovered")
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
