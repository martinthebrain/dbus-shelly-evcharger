# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers for throttled DBus publishing in the Shelly wallbox service."""

import logging
import math
import time
from typing import Any, Callable, Mapping, Sequence, TypeAlias, cast

from dbus_shelly_wallbox_contracts import (
    displayable_confirmed_read_timestamp,
    finite_float_or_none,
    normalized_auto_state_pair,
    normalize_binary_flag,
    normalize_learning_phase,
    normalize_learning_state,
)

PublishStateEntry: TypeAlias = dict[str, Any]
PhaseMeasurement: TypeAlias = dict[str, float]
PhaseData: TypeAlias = dict[str, PhaseMeasurement]


class DbusPublishController:
    """Publish Shelly wallbox DBus paths with simple change and interval throttling."""

    PHASE_NAMES: tuple[str, str, str] = ("L1", "L2", "L3")

    def __init__(self, service: Any, age_seconds_func: Callable[[Any, float], float]) -> None:
        self.service: Any = service
        self._age_seconds = age_seconds_func

    def ensure_state(self) -> None:
        """Initialize DBus publish throttling helpers for tests or partial instances."""
        if not hasattr(self.service, "_dbus_publish_state"):
            self.service._dbus_publish_state = {}
        if not hasattr(self.service, "_dbus_live_publish_interval_seconds"):
            self.service._dbus_live_publish_interval_seconds = 1.0
        if not hasattr(self.service, "_dbus_slow_publish_interval_seconds"):
            self.service._dbus_slow_publish_interval_seconds = 5.0

    def publish_path(
        self,
        path: str,
        value: Any,
        now: float | None = None,
        interval_seconds: float | None = None,
        force: bool = False,
    ) -> bool:
        """Publish a DBus path immediately, on change, or with a minimum interval."""
        self.ensure_state()
        current = time.time() if now is None else float(now)
        should_write, _entry = self._publish_decision(path, value, current, interval_seconds, force)
        if not should_write:
            return False

        self.service._dbusservice[path] = value
        self.service._dbus_publish_state[path] = {"value": value, "updated_at": current}
        return True

    def _publish_decision(
        self,
        path: str,
        value: Any,
        current: float,
        interval_seconds: float | None,
        force: bool,
    ) -> tuple[bool, PublishStateEntry | None]:
        """Return whether one path should be written plus its current publish-state entry."""
        entry = cast(PublishStateEntry | None, self.service._dbus_publish_state.get(path))
        last_value = None if entry is None else entry.get("value")
        last_updated_at = None if entry is None else entry.get("updated_at")
        should_write = force or entry is None

        if not should_write:
            if interval_seconds is None:
                should_write = value != last_value
            else:
                if last_updated_at is None:
                    should_write = True
                else:
                    should_write = (current - float(last_updated_at)) >= float(interval_seconds)
        return should_write, entry

    def _publish_group_failure(self, group_name: str, failed_paths: Sequence[str], current: float) -> None:
        """Record one DBus publish-group failure without raising into the caller."""
        mark_failure = getattr(self.service, "_mark_failure", None)
        if callable(mark_failure):
            mark_failure("dbus")
        warning_throttled = getattr(self.service, "_warning_throttled", None)
        if callable(warning_throttled):
            warning_throttled(
                f"dbus-publish-{group_name}-failed",
                1.0,
                "DBus publish group %s failed for paths %s",
                group_name,
                ",".join(failed_paths),
            )
        else:
            # Fallback for narrow unit-test doubles that only expose the publisher.
            logging.warning(
                "DBus publish group %s failed for paths %s at %.3f",
                group_name,
                ",".join(failed_paths),
                current,
            )

    def _restore_group_publish_state(self, staged_entries: Mapping[str, PublishStateEntry | None]) -> None:
        """Best-effort restore of local DBus publish bookkeeping after a failed group publish."""
        for path, entry in staged_entries.items():
            if entry is None:
                self.service._dbus_publish_state.pop(path, None)
            else:
                self.service._dbus_publish_state[path] = dict(entry)

    def _publish_values_transactional(
        self,
        group_name: str,
        values: Mapping[str, Any],
        now: float | None,
        interval_seconds: float | None = None,
        force: bool = False,
    ) -> bool:
        """Publish one DBus path group with shared best-effort rollback and failure reporting."""
        self.ensure_state()
        current = time.time() if now is None else float(now)
        staged_values: list[tuple[str, Any]] = []
        staged_entries: dict[str, PublishStateEntry | None] = {}
        original_service_values: dict[str, tuple[bool, Any]] = {}

        for path, value in values.items():
            should_write, entry = self._publish_decision(path, value, current, interval_seconds, force)
            if not should_write:
                continue
            staged_values.append((path, value))
            staged_entries[path] = None if entry is None else dict(entry)
            try:
                original_service_values[path] = (True, self.service._dbusservice[path])
            except Exception:  # pylint: disable=broad-except
                original_service_values[path] = (False, None)

        if not staged_values:
            return False

        changed = False
        failed_paths: list[str] = []
        published_paths: list[str] = []
        for path, value in staged_values:
            try:
                self.service._dbusservice[path] = value
            except Exception:  # pylint: disable=broad-except
                failed_paths.append(path)
                break
            self.service._dbus_publish_state[path] = {"value": value, "updated_at": current}
            published_paths.append(path)
            changed = True

        if not failed_paths:
            return changed

        for path in published_paths:
            had_original, original_value = original_service_values.get(path, (False, None))
            if not had_original:
                try:
                    del self.service._dbusservice[path]
                except Exception:  # pylint: disable=broad-except
                    pass
                continue
            try:
                self.service._dbusservice[path] = original_value
            except Exception:  # pylint: disable=broad-except
                pass
        self._restore_group_publish_state(staged_entries)
        self._publish_group_failure(group_name, failed_paths, current)
        return False

    def _publish_values(
        self,
        values: Mapping[str, Any],
        now: float | None,
        interval_seconds: float | None = None,
        force: bool = False,
    ) -> bool:
        """Publish a group of DBus values with shared throttling rules."""
        return self._publish_values_transactional(
            "generic",
            values,
            now,
            interval_seconds=interval_seconds,
            force=force,
        )

    def bump_update_index(self, now: float | None = None) -> None:
        """Increment UpdateIndex when a set of published values changed."""
        self.ensure_state()
        current = time.time() if now is None else float(now)
        index = int(self.service._dbusservice["/UpdateIndex"]) + 1
        next_index = 0 if index > 255 else index
        self.service._dbusservice["/UpdateIndex"] = next_index
        self.service._dbus_publish_state["/UpdateIndex"] = {"value": next_index, "updated_at": current}

    def _live_measurement_values(
        self,
        power: float,
        voltage: float,
        total_current: float,
        phase_data: PhaseData,
    ) -> dict[str, float]:
        """Return fast-moving AC measurement values keyed by DBus path."""
        values: dict[str, float] = {
            "/Ac/Power": power,
            "/Ac/Voltage": voltage,
            "/Ac/Current": total_current,
            "/Current": total_current,
        }
        for phase_name in self.PHASE_NAMES:
            values[f"/Ac/{phase_name}/Power"] = phase_data[phase_name]["power"]
            values[f"/Ac/{phase_name}/Current"] = phase_data[phase_name]["current"]
            values[f"/Ac/{phase_name}/Voltage"] = phase_data[phase_name]["voltage"]
        return values

    def publish_live_measurements(
        self,
        power: float,
        voltage: float,
        total_current: float,
        phase_data: PhaseData,
        now: float | None,
    ) -> bool:
        """Publish fast-changing AC measurements once per second."""
        self.ensure_state()
        return self._publish_values_transactional(
            "live-measurements",
            self._live_measurement_values(power, voltage, total_current, phase_data),
            now,
            interval_seconds=self.service._dbus_live_publish_interval_seconds,
        )

    def _energy_time_values(
        self,
        energy_forward: float,
        phase_energies: Mapping[str, float],
        charging_time: int,
        session_energy: float,
    ) -> dict[str, float | int]:
        """Return slower-moving energy and time values keyed by DBus path."""
        return {
            "/Ac/Energy/Forward": energy_forward,
            "/Ac/L1/Energy/Forward": phase_energies["L1"],
            "/Ac/L2/Energy/Forward": phase_energies["L2"],
            "/Ac/L3/Energy/Forward": phase_energies["L3"],
            "/ChargingTime": charging_time,
            "/Session/Energy": session_energy,
            "/Session/Time": charging_time,
        }

    def publish_energy_time_measurements(
        self,
        energy_forward: float,
        phase_energies: Mapping[str, float],
        charging_time: int,
        session_energy: float,
        now: float | None,
    ) -> bool:
        """Publish energy and time related values at most every five seconds."""
        self.ensure_state()
        return self._publish_values_transactional(
            "energy-time",
            self._energy_time_values(energy_forward, phase_energies, charging_time, session_energy),
            now,
            interval_seconds=self.service._dbus_slow_publish_interval_seconds,
        )

    def _display_uses_learned_set_current(self) -> bool:
        """Return whether the GUI SetCurrent field should mirror the learned EVSE current."""
        charger_backend = getattr(self.service, "_charger_backend", None)
        if charger_backend is not None and hasattr(charger_backend, "set_current"):
            return False
        return bool(normalize_binary_flag(getattr(self.service, "display_learned_set_current", 1), 1))

    def _charger_state_max_age_seconds(self) -> float:
        """Return how fresh charger readback must be before it overrides display state."""
        candidates = [2.0]
        live_interval = finite_float_or_none(
            getattr(self.service, "_dbus_live_publish_interval_seconds", 1.0)
        )
        if live_interval is not None and live_interval > 0.0:
            candidates.append(float(live_interval) * 2.0)
        soft_fail_seconds = finite_float_or_none(
            getattr(self.service, "auto_shelly_soft_fail_seconds", 10.0)
        )
        if soft_fail_seconds is not None and soft_fail_seconds > 0.0:
            candidates.append(float(soft_fail_seconds))
        return max(1.0, min(candidates))

    def _charger_state_fresh(self, now: float | None) -> bool:
        """Return whether a native charger readback is fresh enough to drive GUI state."""
        if getattr(self.service, "_charger_backend", None) is None:
            return False
        state_at = finite_float_or_none(getattr(self.service, "_last_charger_state_at", None))
        if state_at is None:
            return False
        current = time.time() if now is None else float(now)
        return abs(current - state_at) <= self._charger_state_max_age_seconds()

    def _charger_enabled_readback(self, now: float | None) -> bool | None:
        """Return fresh native charger enabled-state readback when available."""
        if not self._charger_state_fresh(now):
            return None
        raw_value = getattr(self.service, "_last_charger_state_enabled", None)
        return None if raw_value is None else bool(raw_value)

    def _charger_current_readback(self, now: float | None) -> float | None:
        """Return fresh native charger current readback when available."""
        if not self._charger_state_fresh(now):
            return None
        current_amps = finite_float_or_none(getattr(self.service, "_last_charger_state_current_amps", None))
        if current_amps is None or current_amps <= 0.0:
            return None
        return float(current_amps)

    def _charger_text_observed(self, attribute_name: str) -> str:
        """Return the last observed charger text field for diagnostics."""
        raw_value = getattr(self.service, attribute_name, None)
        text = str(raw_value).strip() if raw_value is not None else ""
        return text

    def _learned_charge_power_expired_for_display(self, now: float | None) -> bool:
        """Return True when learned charging power is too old for GUI display reuse."""
        max_age_seconds = finite_float_or_none(
            getattr(self.service, "auto_learn_charge_power_max_age_seconds", 21600.0)
        )
        if max_age_seconds is None or max_age_seconds <= 0:
            return False
        updated_at = finite_float_or_none(getattr(self.service, "learned_charge_power_updated_at", None))
        if updated_at is None:
            return True
        current = time.time() if now is None else float(now)
        return (current - updated_at) > max_age_seconds

    def _derived_learned_set_current(self, now: float | None) -> float | None:
        """Return one rounded display current derived from the stable learned charging power."""
        if not self._display_uses_learned_set_current():
            return None
        if normalize_learning_state(getattr(self.service, "learned_charge_power_state", "unknown")) != "stable":
            return None
        if self._learned_charge_power_expired_for_display(now):
            return None

        learned_power = finite_float_or_none(getattr(self.service, "learned_charge_power_watts", None))
        voltage = finite_float_or_none(getattr(self.service, "learned_charge_power_voltage", None))
        phase = normalize_learning_phase(
            getattr(self.service, "learned_charge_power_phase", getattr(self.service, "phase", "L1"))
        )
        if learned_power is None or learned_power <= 0 or voltage is None or voltage <= 0 or phase is None:
            return None

        phase_voltage = voltage
        if phase == "3P" and str(getattr(self.service, "voltage_mode", "phase")).strip().lower() != "phase":
            phase_voltage = phase_voltage / math.sqrt(3.0)
        phase_count = 3.0 if phase == "3P" else 1.0
        if phase_voltage <= 0:
            return None

        display_current = learned_power / (phase_voltage * phase_count)
        rounded_current = finite_float_or_none(round(display_current))
        if rounded_current is None or rounded_current <= 0:
            return None

        min_current = finite_float_or_none(getattr(self.service, "min_current", None))
        max_current = finite_float_or_none(getattr(self.service, "max_current", None))
        if min_current is not None:
            rounded_current = max(rounded_current, min_current)
        if max_current is not None and max_current > 0:
            rounded_current = min(rounded_current, max_current)
        return float(rounded_current)

    def _display_set_current(self, now: float | None) -> float:
        """Return the GUI-facing SetCurrent value with learned-current display fallback."""
        charger_current = self._charger_current_readback(now)
        if charger_current is not None:
            return charger_current
        learned_current = self._derived_learned_set_current(now)
        if learned_current is not None:
            return learned_current
        return float(self.service.virtual_set_current)

    def _config_values(self, startstop_display: int, now: float | None) -> dict[str, Any]:
        """Return mode and control values keyed by DBus path."""
        charger_enabled = self._charger_enabled_readback(now)
        enable_display = (
            int(bool(charger_enabled))
            if charger_enabled is not None
            else int(getattr(self.service, "virtual_enable", 1))
        )
        startstop_value = int(bool(charger_enabled)) if charger_enabled is not None else int(startstop_display)
        return {
            "/Mode": int(getattr(self.service, "virtual_mode", 0)),
            "/AutoStart": int(getattr(self.service, "virtual_autostart", 1)),
            "/StartStop": startstop_value,
            "/Enable": enable_display,
            "/PhaseSelection": str(getattr(self.service, "requested_phase_selection", "P1")),
            "/PhaseSelectionActive": str(getattr(self.service, "active_phase_selection", "P1")),
            "/SupportedPhaseSelections": ",".join(getattr(self.service, "supported_phase_selections", ("P1",))),
            "/SetCurrent": self._display_set_current(now),
            "/MinCurrent": getattr(self.service, "min_current", 0.0),
            "/MaxCurrent": getattr(self.service, "max_current", 0.0),
        }

    @staticmethod
    def _backend_mode_value(service: Any) -> str:
        """Return one stable backend-mode label for diagnostics."""
        raw_value = getattr(service, "backend_mode", "combined")
        normalized = str(raw_value).strip()
        return normalized or "combined"

    @staticmethod
    def _backend_type_value(service: Any, attribute_name: str, default: str = "") -> str:
        """Return one stable backend-type label for diagnostics."""
        raw_value = getattr(service, attribute_name, default)
        normalized = str(raw_value).strip() if raw_value is not None else ""
        return normalized or default

    @staticmethod
    def _charger_current_target_value(service: Any) -> float:
        """Return the last applied native-charger current target or -1 when absent."""
        target_amps = finite_float_or_none(getattr(service, "_charger_target_current_amps", None))
        return -1.0 if target_amps is None else float(target_amps)

    def publish_config_paths(self, startstop_display: int, now: float | None) -> bool:
        """Publish configuration-like EV charger paths only when they change."""
        self.ensure_state()
        return self._publish_values_transactional("config", self._config_values(startstop_display, now), now)

    def _diagnostic_counter_values(self, now: float) -> dict[str, str | int | float]:
        """Return change-driven diagnostic counters keyed by DBus path."""
        error_state = cast(dict[str, Any], self.service._error_state)
        auto_state, auto_state_code = normalized_auto_state_pair(
            getattr(self.service, "_last_auto_state", "idle"),
            getattr(self.service, "_last_auto_state_code", 0),
        )
        error_count = int(
            error_state.get("dbus", 0)
            + error_state.get("shelly", 0)
            + error_state.get("charger", 0)
            + error_state.get("pv", 0)
            + error_state.get("battery", 0)
            + error_state.get("grid", 0)
        )
        return {
            "/Status": int(self.service.last_status),
            "/Auto/Health": str(self.service._last_health_reason),
            "/Auto/HealthCode": int(self.service._last_health_code),
            "/Auto/State": auto_state,
            "/Auto/StateCode": auto_state_code,
            "/Auto/StatusSource": str(getattr(self.service, "_last_status_source", "unknown")),
            "/Auto/BackendMode": self._backend_mode_value(self.service),
            "/Auto/MeterBackend": self._backend_type_value(self.service, "meter_backend_type", "shelly_combined"),
            "/Auto/SwitchBackend": self._backend_type_value(self.service, "switch_backend_type", "shelly_combined"),
            "/Auto/ChargerBackend": self._backend_type_value(self.service, "charger_backend_type"),
            "/Auto/ChargerStatus": self._charger_text_observed("_last_charger_state_status"),
            "/Auto/ChargerFault": self._charger_text_observed("_last_charger_state_fault"),
            "/Auto/ChargerFaultActive": int(bool(getattr(self.service, "_last_charger_fault_active", 0))),
            "/Auto/ErrorCount": error_count,
            "/Auto/DbusReadErrors": int(error_state.get("dbus", 0)),
            "/Auto/ShellyReadErrors": int(error_state.get("shelly", 0)),
            "/Auto/ChargerWriteErrors": int(error_state.get("charger", 0)),
            "/Auto/PvReadErrors": int(error_state.get("pv", 0)),
            "/Auto/BatteryReadErrors": int(error_state.get("battery", 0)),
            "/Auto/GridReadErrors": int(error_state.get("grid", 0)),
            "/Auto/InputCacheHits": int(error_state.get("cache_hits", 0)),
            "/Auto/ChargerCurrentTarget": self._charger_current_target_value(self.service),
            "/Auto/Stale": 1 if self.service._is_update_stale(now) else 0,
            "/Auto/RecoveryAttempts": int(self.service._recovery_attempts),
        }

    def _diagnostic_age_values(self, now: float) -> dict[str, float]:
        """Return slower-changing age-like diagnostic values keyed by DBus path."""
        svc = self.service
        stale_base = (
            svc._last_successful_update_at
            if svc._last_successful_update_at is not None
            else svc.started_at
        )
        last_shelly_read_at = displayable_confirmed_read_timestamp(
            last_confirmed_at=getattr(svc, "_last_confirmed_pm_status_at", None),
            last_pm_at=getattr(svc, "_last_pm_status_at", None),
            last_pm_confirmed=bool(getattr(svc, "_last_pm_status_confirmed", False)),
            now=now,
        )
        return {
            "/Auto/LastShellyReadAge": self._age_seconds(last_shelly_read_at, now),
            "/Auto/LastPvReadAge": self._age_seconds(svc._last_pv_at, now),
            "/Auto/LastBatteryReadAge": self._age_seconds(svc._last_battery_soc_at, now),
            "/Auto/LastGridReadAge": self._age_seconds(svc._last_grid_at, now),
            "/Auto/LastDbusReadAge": self._age_seconds(svc._last_dbus_ok_at, now),
            "/Auto/ChargerCurrentTargetAge": self._age_seconds(
                getattr(svc, "_charger_target_current_applied_at", None), now
            ),
            "/Auto/LastChargerReadAge": self._age_seconds(
                getattr(svc, "_last_charger_state_at", None), now
            ),
            "/Auto/LastSuccessfulUpdateAge": self._age_seconds(svc._last_successful_update_at, now),
            "/Auto/StaleSeconds": self._age_seconds(stale_base, now),
        }

    def publish_diagnostic_paths(self, now: float) -> bool:
        """Publish diagnostics on change, except age-like values every five seconds."""
        self.ensure_state()
        changed = self._publish_values_transactional("diagnostic-counters", self._diagnostic_counter_values(now), now)
        changed |= self._publish_values_transactional(
            "diagnostic-ages",
            self._diagnostic_age_values(now),
            now,
            interval_seconds=self.service._dbus_slow_publish_interval_seconds,
        )
        return changed
