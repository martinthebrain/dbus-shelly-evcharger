# SPDX-License-Identifier: GPL-3.0-or-later
"""Virtual-state publishing and update-cycle helpers for the Shelly wallbox service.

The update cycle is the heartbeat of the wallbox integration. Every pass reads
the latest Shelly snapshot, lets Auto mode decide whether the relay should be
on, applies corrections if needed, and then publishes the resulting charger
state back to Venus OS.
"""

from __future__ import annotations

import logging
from typing import Any


class UpdateCycleController:
    """Encapsulate the periodic Shelly/Auto update pipeline."""

    def __init__(self, service: Any, phase_values_func: Any, health_code_func: Any) -> None:
        self.service = service
        self._phase_values = phase_values_func
        self._health_code = health_code_func

    def apply_startup_manual_target(self, pm_status: dict[str, Any], now: float) -> dict[str, Any]:
        """Synchronize the configured manual on/off state once after startup."""
        svc = self.service
        if not hasattr(svc, "_startup_manual_target"):
            svc._startup_manual_target = None
        if svc._startup_manual_target is None or svc._mode_uses_auto_logic(svc.virtual_mode):
            return pm_status

        target_on = bool(svc._startup_manual_target)
        svc._startup_manual_target = None
        relay_on = bool(pm_status.get("output", False))
        if relay_on == target_on:
            return pm_status

        try:
            # Startup manual state is best-effort. If Shelly access is currently
            # unavailable, we keep the live status and let the normal update loop
            # retry on the next cycle instead of failing startup.
            svc._queue_relay_command(target_on, now)
        except Exception as error:  # pylint: disable=broad-except
            svc._mark_failure("shelly")
            svc._warning_throttled(
                "startup-manual-target-failed",
                svc.auto_shelly_soft_fail_seconds,
                "Failed to queue startup manual relay state %s: %s",
                target_on,
                error,
                exc_info=error,
            )
            return pm_status

        pm_status = dict(pm_status)
        pm_status["output"] = target_on
        if not target_on:
            pm_status["apower"] = 0.0
            pm_status["current"] = 0.0
        return pm_status

    def ensure_virtual_state_defaults(self) -> None:
        """Populate defaults used by virtual session and health publishing."""
        svc = self.service
        svc._ensure_observability_state()
        if not hasattr(svc, "_last_health_reason"):
            svc._last_health_reason = "init"
        if not hasattr(svc, "_last_health_code"):
            svc._last_health_code = self._health_code(svc._last_health_reason)

    @staticmethod
    def session_state_from_status(
        svc: Any,
        status: int,
        current_total_energy: float,
        relay_on: bool,
        now: float,
    ) -> tuple[int, float]:
        """Compute current session timing and energy values."""
        session_active = status == 2 or (relay_on and svc.charging_started_at is not None)
        if session_active:
            if svc.charging_started_at is None:
                svc.charging_started_at = now
                svc.energy_at_start = current_total_energy
            charging_time = int(now - svc.charging_started_at)
            session_energy = round(max(0.0, current_total_energy - svc.energy_at_start), 3)
            return charging_time, session_energy
        if not relay_on:
            svc.charging_started_at = None
            svc.energy_at_start = current_total_energy
            return 0, 0.0
        session_energy = round(max(0.0, current_total_energy - svc.energy_at_start), 3)
        return 0, session_energy

    @staticmethod
    def startstop_display_for_state(svc: Any, relay_on: bool) -> int:
        """Return the GUI start/stop indicator for the current mode."""
        svc.virtual_startstop = 1 if relay_on else 0
        if svc._mode_uses_auto_logic(svc.virtual_mode):
            return int(relay_on or svc.virtual_enable)
        return int(svc.virtual_startstop)

    @staticmethod
    def phase_energies_for_total(svc: Any, current_total_energy: float) -> dict[str, float]:
        """Split total energy across phases according to configured wiring."""
        phase = getattr(svc, "phase", "L1")
        if phase == "3P":
            per_phase = current_total_energy / 3.0
            return {"L1": per_phase, "L2": per_phase, "L3": per_phase}
        return {
            "L1": current_total_energy if phase == "L1" else 0.0,
            "L2": current_total_energy if phase == "L2" else 0.0,
            "L3": current_total_energy if phase == "L3" else 0.0,
        }

    def publish_virtual_state_paths(
        self,
        current_total_energy: float,
        charging_time: int,
        session_energy: float,
        startstop_display: int,
        now: float,
    ) -> bool:
        """Publish session, config, and diagnostic values derived from the live state."""
        svc = self.service
        phase_energies = self.phase_energies_for_total(svc, current_total_energy)
        changed = svc._publish_energy_time_measurements(
            current_total_energy,
            phase_energies,
            charging_time,
            session_energy,
            now,
        )
        changed |= svc._publish_config_paths(startstop_display, now)
        changed |= svc._publish_diagnostic_paths(now)
        return changed

    @staticmethod
    def _total_phase_current(phase_data: dict[str, dict[str, float]]) -> float:
        """Return the summed AC current across all published phases."""
        return sum(phase_data[phase_name]["current"] for phase_name in ("L1", "L2", "L3"))

    def update_virtual_state(self, status: int, current_total_energy: float, relay_on: bool) -> bool:
        """Update DBus state that is derived from relay state and energy."""
        svc = self.service
        status = int(status)
        current_total_energy = float(current_total_energy)
        relay_on = bool(relay_on)
        self.ensure_virtual_state_defaults()
        now = svc._time_now()
        charging_time, session_energy = self.session_state_from_status(
            svc,
            status,
            current_total_energy,
            relay_on,
            now,
        )
        startstop_display = self.startstop_display_for_state(svc, relay_on)
        svc.last_status = status
        changed = self.publish_virtual_state_paths(
            current_total_energy,
            charging_time,
            session_energy,
            startstop_display,
            now,
        )
        svc._save_runtime_state()
        return changed

    @staticmethod
    def prepare_update_cycle(svc: Any, now: float) -> Any:
        """Run pre-update recovery/supervision hooks and return the latest worker snapshot."""
        svc._watchdog_recover(now)
        svc._ensure_auto_input_helper_process(now)
        svc._refresh_auto_input_snapshot(now)
        return svc._get_worker_snapshot()

    @staticmethod
    def resolve_pm_status_for_update(svc: Any, worker_snapshot: dict[str, Any], now: float) -> dict[str, Any] | None:
        """Return the freshest Shelly status, including short soft-fail reuse."""
        pm_status = worker_snapshot.get("pm_status")
        if pm_status is not None:
            pm_status = dict(pm_status)
            snapshot_at = worker_snapshot.get("pm_captured_at", worker_snapshot.get("captured_at", now))
            snapshot_at = now if snapshot_at is None else float(snapshot_at)
            svc._last_pm_status = dict(pm_status)
            svc._last_pm_status_at = snapshot_at
            return pm_status

        if (
            svc._last_pm_status is not None
            and svc._last_pm_status_at is not None
            and (now - svc._last_pm_status_at) <= svc.auto_shelly_soft_fail_seconds
        ):
            return dict(svc._last_pm_status)
        return None

    def publish_offline_update(self, now: float) -> bool:
        """Publish a disconnected Shelly state when no recent status is available."""
        svc = self.service
        voltage = svc._last_voltage if svc._last_voltage else 230.0
        relay_on = bool(svc.virtual_startstop)
        power = 0.0
        energy_forward = 0.0
        status = 0
        phase_data = self._phase_values(power, voltage, svc.phase, svc.voltage_mode)
        svc._set_health("shelly-offline", cached=False)
        total_current = self._total_phase_current(phase_data)

        changed = False
        changed |= svc._publish_live_measurements(power, voltage, total_current, phase_data, now)
        changed |= self.update_virtual_state(status, energy_forward, relay_on)
        if changed:
            svc._bump_update_index(now)
        svc.last_update = svc._time_now()
        return True

    @staticmethod
    def extract_pm_measurements(svc: Any, pm_status: dict[str, Any]) -> tuple[bool, float, float, float, float]:
        """Extract normalized relay/power/current/energy values from a Shelly status dict."""
        relay_on = bool(pm_status.get("output", False))
        power = svc._safe_float(pm_status.get("apower", 0.0), 0.0)
        voltage = svc._safe_float(pm_status.get("voltage", 0.0), 0.0)
        current = svc._safe_float(pm_status.get("current", 0.0), 0.0)
        energy_forward = svc._safe_float(pm_status.get("aenergy", {}).get("total", 0.0), 0.0) / 1000.0
        return relay_on, power, voltage, current, energy_forward

    @staticmethod
    def resolve_cached_input_value(
        svc: Any,
        value: Any,
        snapshot_at: float | None,
        last_value_attr: str,
        last_at_attr: str,
        now: float,
    ) -> tuple[Any, bool]:
        """Use fresh input values immediately and short-lived cached values as fallback."""
        if value is not None:
            setattr(svc, last_value_attr, value)
            setattr(svc, last_at_attr, now if snapshot_at is None else float(snapshot_at))
            return value, False

        last_value = getattr(svc, last_value_attr)
        last_at = getattr(svc, last_at_attr)
        if last_value is not None and last_at is not None and (now - last_at) <= svc.auto_input_cache_seconds:
            return last_value, True
        return None, False

    def resolve_auto_inputs(
        self,
        worker_snapshot: dict[str, Any],
        now: float,
        auto_mode_active: bool,
    ) -> tuple[Any, Any, Any]:
        """Resolve Auto inputs from helper snapshots with short cache fallback."""
        svc = self.service
        if not auto_mode_active:
            svc._auto_cached_inputs_used = False
            return None, None, None

        pv_power, pv_cached = self.resolve_cached_input_value(
            svc,
            worker_snapshot.get("pv_power"),
            worker_snapshot.get("pv_captured_at", worker_snapshot.get("captured_at")),
            "_last_pv_value",
            "_last_pv_at",
            now,
        )
        grid_power, grid_cached = self.resolve_cached_input_value(
            svc,
            worker_snapshot.get("grid_power"),
            worker_snapshot.get("grid_captured_at", worker_snapshot.get("captured_at")),
            "_last_grid_value",
            "_last_grid_at",
            now,
        )
        battery_soc, battery_cached = self.resolve_cached_input_value(
            svc,
            worker_snapshot.get("battery_soc"),
            worker_snapshot.get("battery_captured_at", worker_snapshot.get("captured_at")),
            "_last_battery_soc_value",
            "_last_battery_soc_at",
            now,
        )
        svc._auto_cached_inputs_used = pv_cached or grid_cached or battery_cached
        if svc._auto_cached_inputs_used:
            svc._error_state["cache_hits"] += 1
        return pv_power, battery_soc, grid_power

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

    def apply_relay_decision(self, desired_relay, relay_on, pm_status, power, current, now, auto_mode_active):
        """Queue relay changes and update optimistic local Shelly state."""
        svc = self.service
        if desired_relay == relay_on:
            return relay_on, power, current

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
            return relay_on, power, current

        relay_on = desired_relay
        pm_status = dict(pm_status)
        pm_status["output"] = relay_on
        if not relay_on:
            power = 0.0
            current = 0.0
        svc._publish_local_pm_status(relay_on, now)
        return relay_on, power, current

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

    @staticmethod
    def complete_update_cycle(svc, changed, now, relay_on, power, current, status, pv_power, battery_soc, grid_power):
        """Finalize a successful update cycle and log the current state."""
        if changed:
            svc._bump_update_index(now)
        completed_at = svc._time_now()
        svc._last_successful_update_at = completed_at
        svc._last_recovery_attempt_at = None
        svc.last_update = completed_at
        logging.debug(
            "Wallbox relay=%s power=%sW current=%sA status=%s pv=%sW soc=%s%% grid=%sW mode=%s",
            relay_on,
            power,
            current,
            status,
            pv_power,
            battery_soc,
            grid_power,
            svc.virtual_mode,
        )

    def sign_of_life(self):
        """Periodic heartbeat log for troubleshooting."""
        svc = self.service
        logging.info("[%s] Last '/Ac/Power': %s", svc.service_name, svc._dbusservice["/Ac/Power"])
        return True

    def update(self):
        """Periodic update loop: read Shelly, compute auto logic, update DBus."""
        svc = self.service
        try:
            now = svc._time_now()
            worker_snapshot = self.prepare_update_cycle(svc, now)
            pm_status = self.resolve_pm_status_for_update(svc, worker_snapshot, now)
            if pm_status is None:
                return self.publish_offline_update(now)

            relay_on, power, voltage, current, energy_forward = self.extract_pm_measurements(svc, pm_status)
            pm_status = self.apply_startup_manual_target(pm_status, now)
            relay_on, power, voltage, current, energy_forward = self.extract_pm_measurements(svc, pm_status)
            if voltage > 0.0:
                svc._last_voltage = voltage
            auto_mode_active = svc._mode_uses_auto_logic(svc.virtual_mode)
            pv_power, battery_soc, grid_power = self.resolve_auto_inputs(worker_snapshot, now, auto_mode_active)

            desired_relay = svc._auto_decide_relay(relay_on, pv_power, battery_soc, grid_power)
            relay_on, power, current = self.apply_relay_decision(
                desired_relay,
                relay_on,
                pm_status,
                power,
                current,
                now,
                auto_mode_active,
            )
            status = self.derive_status_code(svc, relay_on, power, auto_mode_active)
            changed = self.publish_online_update(status, energy_forward, relay_on, power, voltage, now)
            self.complete_update_cycle(
                svc,
                changed,
                now,
                relay_on,
                power,
                current,
                status,
                pv_power,
                battery_soc,
                grid_power,
            )
        except Exception as error:  # pylint: disable=broad-except
            logging.warning(
                "Error updating Shelly wallbox data: %s (%s)",
                error,
                svc._state_summary(),
                exc_info=error,
            )
        return True
