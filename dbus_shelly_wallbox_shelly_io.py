# SPDX-License-Identifier: GPL-3.0-or-later
"""Shelly HTTP and relay-worker helpers for the Shelly wallbox service."""

import logging
import threading
from urllib.parse import urlencode

from requests.auth import HTTPDigestAuth


class ShellyIoController:
    """Encapsulate Shelly HTTP access and relay queue/worker behavior."""

    def __init__(self, service):
        self.service = service

    @staticmethod
    def _encoded_rpc_params(params):
        """Encode Shelly RPC query parameters, keeping booleans lowercase."""
        encoded = {}
        for key, value in params.items():
            encoded[key] = str(value).lower() if isinstance(value, bool) else value
        return encoded

    def _request_auth_kwargs(self):
        """Return optional request auth kwargs for the configured Shelly auth mode."""
        svc = self.service
        if svc.use_digest_auth:
            return {"auth": HTTPDigestAuth(svc.username, svc.password)}
        if svc.username and svc.password:
            return {"auth": (svc.username, svc.password)}
        return {}

    def _request_kwargs(self, url):
        """Return common request kwargs for main or worker HTTP sessions."""
        svc = self.service
        kwargs = {"url": url, "timeout": getattr(svc, "shelly_request_timeout_seconds", 2.0)}
        kwargs.update(self._request_auth_kwargs())
        return kwargs

    def _rpc_url(self, method, params):
        """Build a Shelly RPC URL including optional query parameters."""
        svc = self.service
        if not params:
            return f"http://{svc.host}/rpc/{method}"
        return f"http://{svc.host}/rpc/{method}?{urlencode(self._encoded_rpc_params(params))}"

    def request(self, url):
        """Perform a Shelly HTTP request through the main-session client."""
        response = self.service.session.get(**self._request_kwargs(url))
        response.raise_for_status()
        return response.json()

    def request_with_session(self, session, url):
        """Perform a Shelly HTTP request through a specific requests session."""
        response = session.get(**self._request_kwargs(url))
        response.raise_for_status()
        return response.json()

    def rpc_call(self, method, **params):
        """Perform a Shelly RPC call with query encoding."""
        return self.service._request(self._rpc_url(method, params))

    def rpc_call_with_session(self, session, method, **params):
        """Perform a Shelly RPC call through a specific requests session."""
        return self.service._request_with_session(session, self._rpc_url(method, params))

    def fetch_pm_status(self):
        """Fetch Shelly component status for power data."""
        svc = self.service
        return svc.rpc_call(f"{svc.pm_component}.GetStatus", id=svc.pm_id)

    def set_relay(self, on):
        """Switch the Shelly relay output."""
        svc = self.service
        return svc.rpc_call("Switch.Set", id=svc.pm_id, on=bool(on))

    def worker_fetch_pm_status(self):
        """Fetch Shelly power status from the background worker session."""
        svc = self.service
        return svc._rpc_call_with_session(
            svc._worker_session,
            f"{svc.pm_component}.GetStatus",
            id=svc.pm_id,
        )

    def build_local_pm_status(self, relay_on):
        """Build an optimistic local Shelly status after a direct relay command."""
        svc = self.service
        source = getattr(svc, "_last_pm_status", None)
        source = source if isinstance(source, dict) else {}
        pm_status = dict(source)
        pm_status["output"] = bool(relay_on)
        pm_status.setdefault("apower", 0.0)
        pm_status.setdefault("current", 0.0)
        last_voltage = getattr(svc, "_last_voltage", None)
        pm_status.setdefault("voltage", last_voltage if last_voltage else 230.0)
        pm_status.setdefault("aenergy", {})
        if not isinstance(pm_status["aenergy"], dict):
            pm_status["aenergy"] = {}
        pm_status["aenergy"].setdefault("total", 0.0)
        if not relay_on:
            pm_status["apower"] = 0.0
            pm_status["current"] = 0.0
        return pm_status

    def publish_local_pm_status(self, relay_on, now=None):
        """Publish a best-effort local Shelly status immediately after relay writes."""
        svc = self.service
        current = svc._time_now() if now is None else float(now)
        pm_status = svc._build_local_pm_status(relay_on)
        svc._last_pm_status = dict(pm_status)
        svc._last_pm_status_at = current
        svc._update_worker_snapshot(
            captured_at=current,
            pm_captured_at=current,
            pm_status=pm_status,
        )
        return pm_status

    def queue_relay_command(self, relay_on, now=None):
        """Queue a relay command for the Shelly worker thread."""
        svc = self.service
        svc._ensure_worker_state()
        current = svc._time_now() if now is None else float(now)
        with svc._relay_command_lock:
            svc._pending_relay_state = bool(relay_on)
            svc._pending_relay_requested_at = current

    def peek_pending_relay_command(self):
        """Return the latest queued relay command without clearing it."""
        svc = self.service
        svc._ensure_worker_state()
        with svc._relay_command_lock:
            return svc._pending_relay_state, svc._pending_relay_requested_at

    def clear_pending_relay_command(self, relay_on):
        """Clear a processed relay command if it still matches the latest request."""
        svc = self.service
        svc._ensure_worker_state()
        with svc._relay_command_lock:
            if svc._pending_relay_state == bool(relay_on):
                svc._pending_relay_state = None
                svc._pending_relay_requested_at = None

    def worker_apply_pending_relay_command(self):
        """Execute queued relay writes in the Shelly worker thread."""
        svc = self.service
        target_on, _requested_at = svc._peek_pending_relay_command()
        if target_on is None:
            return
        try:
            svc._rpc_call_with_session(svc._worker_session, "Switch.Set", id=svc.pm_id, on=bool(target_on))
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
        svc._clear_pending_relay_command(target_on)
        svc._mark_relay_changed(target_on, completed_at)
        svc._mark_recovery("shelly", "Shelly relay writes recovered")
        svc._publish_local_pm_status(target_on, completed_at)

    def io_worker_once(self):
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

    def io_worker_loop(self):
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

    def start_io_worker(self):
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
