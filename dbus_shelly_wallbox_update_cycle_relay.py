# SPDX-License-Identifier: GPL-3.0-or-later
"""Virtual-state publishing and update-cycle helpers for the Shelly wallbox service.

The update cycle is the heartbeat of the wallbox integration. Every pass reads
the latest Shelly snapshot, lets Auto mode decide whether the relay should be
on, applies corrections if needed, and then publishes the resulting charger
state back to Venus OS.
"""

from __future__ import annotations

import logging
import math
from typing import Any
from dbus_shelly_wallbox_split_mixins import _ComposableControllerMixin



class _UpdateCycleRelayMixin(_ComposableControllerMixin):
    @staticmethod
    def log_auto_relay_change(svc: Any, desired_relay: bool) -> None:
        """Log the current averaged Auto metrics when Auto changes relay state."""
        metrics = svc._last_auto_metrics
        logging.info(
            "Auto relay %s reason=%s surplus=%sW grid=%sW soc=%s%%",
            "ON" if desired_relay else "OFF",
            svc._last_health_reason,
            f"{metrics.get('surplus'):.0f}" if metrics.get("surplus") is not None else "na",
            f"{metrics.get('grid'):.0f}" if metrics.get("grid") is not None else "na",
            f"{metrics.get('soc'):.1f}" if metrics.get("soc") is not None else "na",
        )

    @staticmethod
    def _clear_relay_sync_tracking(svc: Any) -> None:
        """Clear any outstanding relay-confirmation tracking."""
        svc._relay_sync_expected_state = None
        svc._relay_sync_requested_at = None
        svc._relay_sync_deadline_at = None
        svc._relay_sync_failure_reported = False

    @staticmethod
    def _pm_status_confirmed(pm_status: dict[str, Any]) -> bool:
        """Return whether a Shelly state originated from a confirmed device read."""
        return bool(pm_status.get("_pm_confirmed", False))

    def _publish_local_pm_status_best_effort(self, relay_on: bool, now: float) -> None:
        """Publish one optimistic placeholder after a queued relay change without aborting the cycle."""
        svc = self.service
        try:
            svc._publish_local_pm_status(relay_on, now)
        except Exception as error:  # pylint: disable=broad-except
            svc._warning_throttled(
                "relay-placeholder-publish-failed",
                max(1.0, float(getattr(svc, "relay_sync_timeout_seconds", 2.0) or 2.0)),
                "Local relay placeholder publish failed after queueing relay=%s: %s",
                int(bool(relay_on)),
                error,
                exc_info=error,
            )

    def relay_sync_health_override(self, relay_on: bool, pm_confirmed: bool, now: float) -> str | None:
        """Return an explicit health reason for pending relay confirmations."""
        svc = self.service
        expected_state = getattr(svc, "_relay_sync_expected_state", None)
        if expected_state is None:
            return None

        expected_relay = bool(expected_state)
        if pm_confirmed and bool(relay_on) == expected_relay:
            if getattr(svc, "_relay_sync_failure_reported", False):
                svc._mark_recovery("shelly", "Shelly relay confirmation recovered")
            self._clear_relay_sync_tracking(svc)
            return None

        deadline_at = getattr(svc, "_relay_sync_deadline_at", None)
        if deadline_at is None or float(now) < float(deadline_at):
            if pm_confirmed and bool(relay_on) != expected_relay:
                return "command-mismatch"
            return None

        if not getattr(svc, "_relay_sync_failure_reported", False):
            svc._relay_sync_failure_reported = True
            timeout_seconds = max(
                0.0,
                float(deadline_at) - float(getattr(svc, "_relay_sync_requested_at", deadline_at)),
            )
            svc._mark_failure("shelly")
            svc._warning_throttled(
                "relay-sync-failed",
                max(1.0, timeout_seconds),
                "Shelly relay state did not confirm to %s within %.1fs (actual=%s confirmed=%s)",
                expected_relay,
                timeout_seconds,
                bool(relay_on),
                int(bool(pm_confirmed)),
            )
        # A timed-out confirmation must not block a fresh retry of the same
        # relay target on the next decision cycle.
        self._clear_relay_sync_tracking(svc)
        return "relay-sync-failed"

    def apply_relay_decision(self, desired_relay, relay_on, pm_status, power, current, now, auto_mode_active):
        """Queue relay changes and update optimistic local Shelly state."""
        svc = self.service
        pm_confirmed = self._pm_status_confirmed(pm_status)
        if desired_relay == relay_on:
            return relay_on, power, current, pm_confirmed

        if getattr(svc, "_relay_sync_expected_state", None) == bool(desired_relay):
            return relay_on, power, current, pm_confirmed

        if auto_mode_active and svc.auto_audit_log:
            self.log_auto_relay_change(svc, desired_relay)

        try:
            svc._queue_relay_command(desired_relay, now)
        except Exception as error:  # pylint: disable=broad-except
            svc._mark_failure("shelly")
            svc._warning_throttled(
                "shelly-switch-failed",
                svc.auto_shelly_soft_fail_seconds,
                "Shelly relay switch queue failed: %s",
                error,
                exc_info=error,
            )
            return relay_on, power, current, pm_confirmed

        relay_on = desired_relay
        power = 0.0
        current = 0.0
        self._publish_local_pm_status_best_effort(relay_on, now)
        return relay_on, power, current, False

    @staticmethod
    def derive_status_code(svc, relay_on, power, auto_mode_active):
        """Translate relay/power state into the Venus EV charger status code."""
        if relay_on and power >= svc.charging_threshold_watts:
            return 2
        if relay_on:
            return svc.idle_status
        return 4 if auto_mode_active else 6

    def publish_online_update(self, status, energy_forward, relay_on, power, voltage, now):
        """Publish live measurements and derived runtime state for an online Shelly status."""
        svc = self.service
        phase_data = self._phase_values(power, voltage, svc.phase, svc.voltage_mode)
        total_current = self._total_phase_current(phase_data)

        changed = False
        changed |= svc._publish_live_measurements(power, voltage, total_current, phase_data, now)
        changed |= self.update_virtual_state(status, energy_forward, relay_on)
        return changed
