# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined"
# pyright: reportAttributeAccessIssue=false
"""Relay queue and worker-loop helpers for Shelly I/O support."""

from __future__ import annotations

import threading
from typing import Any, cast

from venus_evcharger.backend.modbus_transport import modbus_transport_issue_reason
from venus_evcharger.backend.shelly_io_types import PendingRelayCommand, ShellyEnergyData, ShellyPmStatus


class ShellyIoWorkerMixin:
    """Handle optimistic PM publishing, queued relay writes, and the worker loop."""

    @staticmethod
    def _normalized_energy_payload(value: object) -> ShellyEnergyData:
        payload: ShellyEnergyData = {}
        if isinstance(value, dict):
            total = value.get("total")
            if isinstance(total, (int, float)) and not isinstance(total, bool):
                payload["total"] = float(total)
        payload.setdefault("total", 0.0)
        return payload

    def build_local_pm_status(self, relay_on: bool) -> ShellyPmStatus:
        svc = self.service
        source = getattr(svc, "_last_pm_status", None)
        raw_status = dict(source) if isinstance(source, dict) else {}
        pm_status = cast(ShellyPmStatus, raw_status)
        last_voltage = getattr(svc, "_last_voltage", None)
        voltage = (
            float(last_voltage)
            if isinstance(last_voltage, (int, float)) and not isinstance(last_voltage, bool)
            else 230.0
        )
        pm_status["output"] = bool(relay_on)
        pm_status["voltage"] = float(pm_status.get("voltage", voltage) or voltage)
        pm_status["aenergy"] = self._normalized_energy_payload(pm_status.get("aenergy"))
        pm_status["apower"] = 0.0
        pm_status["current"] = 0.0
        return pm_status

    def publish_local_pm_status(self, relay_on: bool, now: float | None = None) -> ShellyPmStatus:
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
        return cast(ShellyPmStatus, pm_status)

    def queue_relay_command(self, relay_on: bool, now: float | None = None) -> None:
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
        svc = self.service
        svc._ensure_worker_state()
        with svc._relay_command_lock:
            return svc._pending_relay_state, svc._pending_relay_requested_at

    def clear_pending_relay_command(self, relay_on: bool) -> None:
        svc = self.service
        svc._ensure_worker_state()
        with svc._relay_command_lock:
            if svc._pending_relay_state == bool(relay_on):
                svc._pending_relay_state = None
                svc._pending_relay_requested_at = None

    def worker_apply_pending_relay_command(self) -> None:
        command_context = self._pending_relay_command_context()
        if command_context is None:
            return
        svc, target_on, source_key, source_label, current = command_context
        try:
            self._apply_pending_relay_target(svc, bool(target_on))
        except Exception as error:
            self._handle_pending_relay_command_error(svc, source_key, source_label, current, error)
            return
        self._finalize_pending_relay_command(svc, bool(target_on), source_key, source_label)

    def _pending_relay_command_context(self) -> tuple[Any, bool, str, str, float] | None:
        svc = self.service
        target_on, _requested_at = svc._peek_pending_relay_command()
        if target_on is None:
            return None
        source_key = self._split_enable_source_key()
        current = self._runtime_now()
        if source_key == "charger" and self._charger_retry_active(current):
            return None
        return svc, bool(target_on), source_key, self._split_enable_source_label(), current

    def _apply_pending_relay_target(self, svc: Any, target_on: bool) -> None:
        backend = self._split_enable_backend()
        if backend is not None:
            cast(Any, backend).set_enabled(bool(target_on))
            return
        svc._rpc_call_with_session(
            svc._worker_session,
            "Switch.Set",
            id=svc.pm_id,
            on=bool(target_on),
        )

    def _handle_pending_relay_command_error(
        self,
        svc: Any,
        source_key: str,
        source_label: str,
        current: float,
        error: BaseException,
    ) -> None:
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

    def _finalize_pending_relay_command(
        self,
        svc: Any,
        target_on: bool,
        source_key: str,
        source_label: str,
    ) -> None:
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
        except Exception as error:
            svc._mark_failure("shelly")
            svc._warning_throttled(
                "worker-shelly-read-failed",
                svc.auto_shelly_soft_fail_seconds,
                "Shelly status read failed: %s",
                error,
                exc_info=error,
            )
            svc._update_worker_snapshot(
                captured_at=now,
                auto_mode_active=svc._mode_uses_auto_logic(getattr(svc, "virtual_mode", 0)),
                pm_status=None,
                pm_captured_at=None,
                pm_confirmed=False,
            )

    def io_worker_loop(self) -> None:
        svc = self.service
        svc._ensure_worker_state()
        while not svc._worker_stop_event.is_set():
            cycle_started = svc._time_now()
            try:
                self.io_worker_once()
            except Exception as error:
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
