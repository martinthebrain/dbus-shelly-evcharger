# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers for throttled DBus publishing in the Shelly wallbox service."""

from dataclasses import dataclass
import logging
import math
import time
from typing import Any, Callable, Mapping, Sequence, TypeAlias, cast

from shelly_wallbox.backend.models import (
    effective_supported_phase_selections,
    switch_feedback_mismatch,
)
from shelly_wallbox.core.common import (
    _charger_retry_remaining_seconds,
    _fresh_charger_retry_reason,
    _fresh_charger_retry_source,
    _fresh_charger_retry_until,
    _fresh_charger_transport_detail,
    _fresh_charger_transport_reason,
    _fresh_charger_transport_source,
    _fresh_charger_transport_timestamp,
    evse_fault_reason,
)
from shelly_wallbox.core.contracts import (
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
PublishServiceValueSnapshot: TypeAlias = tuple[bool, Any]


@dataclass(frozen=True)
class _LearnedDisplayCurrentInputs:
    """Stable learned charging-power inputs used for SetCurrent display derivation."""

    power_w: float
    phase_voltage_v: float
    phase_count: float


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
        if force or entry is None:
            return True, entry
        last_value, last_updated_at = self._publish_state_fields(entry)
        if interval_seconds is None:
            return value != last_value, entry
        return self._publish_interval_elapsed(last_updated_at, current, interval_seconds), entry

    @staticmethod
    def _publish_state_fields(entry: PublishStateEntry) -> tuple[Any, Any]:
        """Return the stored publish-state value and timestamp."""
        return entry.get("value"), entry.get("updated_at")

    @staticmethod
    def _publish_interval_elapsed(last_updated_at: Any, current: float, interval_seconds: float) -> bool:
        """Return whether the publish interval is due for one path."""
        if last_updated_at is None:
            return True
        return (current - float(last_updated_at)) >= float(interval_seconds)

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

    def _service_value_snapshot(self, path: str) -> PublishServiceValueSnapshot:
        """Return whether one DBus path existed before publishing plus its previous value."""
        try:
            return True, self.service._dbusservice[path]
        except Exception:  # pylint: disable=broad-except
            return False, None

    def _stage_publish_values(
        self,
        values: Mapping[str, Any],
        current: float,
        interval_seconds: float | None,
        force: bool,
    ) -> tuple[list[tuple[str, Any]], dict[str, PublishStateEntry | None], dict[str, PublishServiceValueSnapshot]]:
        """Collect the DBus values that should be written in one transactional batch."""
        staged_values: list[tuple[str, Any]] = []
        staged_entries: dict[str, PublishStateEntry | None] = {}
        original_service_values: dict[str, PublishServiceValueSnapshot] = {}
        for path, value in values.items():
            should_write, entry = self._publish_decision(path, value, current, interval_seconds, force)
            if not should_write:
                continue
            staged_values.append((path, value))
            staged_entries[path] = None if entry is None else dict(entry)
            original_service_values[path] = self._service_value_snapshot(path)
        return staged_values, staged_entries, original_service_values

    def _apply_staged_publish_values(
        self,
        staged_values: Sequence[tuple[str, Any]],
        current: float,
    ) -> tuple[bool, list[str], str | None]:
        """Apply one staged publish batch and report any failed path."""
        changed = False
        published_paths: list[str] = []
        for path, value in staged_values:
            try:
                self.service._dbusservice[path] = value
            except Exception:  # pylint: disable=broad-except
                return changed, published_paths, path
            self.service._dbus_publish_state[path] = {"value": value, "updated_at": current}
            published_paths.append(path)
            changed = True
        return changed, published_paths, None

    def _restore_service_values(
        self,
        published_paths: Sequence[str],
        original_service_values: Mapping[str, PublishServiceValueSnapshot],
    ) -> None:
        """Best-effort restore of DBus path values after a failed transactional publish."""
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
        staged_values, staged_entries, original_service_values = self._stage_publish_values(
            values,
            current,
            interval_seconds,
            force,
        )

        if not staged_values:
            return False

        changed, published_paths, failed_path = self._apply_staged_publish_values(staged_values, current)
        if failed_path is None:
            return changed

        self._restore_service_values(published_paths, original_service_values)
        self._restore_group_publish_state(staged_entries)
        self._publish_group_failure(group_name, [failed_path], current)
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

    def _charger_estimate_active(self) -> int:
        """Return whether meterless charger values are currently estimated."""
        return int(bool(getattr(self.service, "_last_charger_estimate_source", None)))

    def _charger_estimate_source(self) -> str:
        """Return the current charger estimate source label for diagnostics."""
        return self._diagnostic_text_value(getattr(self.service, "_last_charger_estimate_source", None))

    def _charger_transport_active(self, now: float) -> int:
        """Return whether a charger-transport issue is currently active."""
        return int(_fresh_charger_transport_timestamp(self.service, now) is not None)

    def _charger_transport_reason(self, now: float) -> str:
        """Return the current charger-transport reason label for diagnostics."""
        return self._diagnostic_text_value(_fresh_charger_transport_reason(self.service, now))

    def _charger_transport_source(self, now: float) -> str:
        """Return the current charger-transport source label for diagnostics."""
        return self._diagnostic_text_value(_fresh_charger_transport_source(self.service, now))

    def _charger_transport_detail(self, now: float) -> str:
        """Return the current charger-transport detail text for diagnostics."""
        return self._diagnostic_text_value(_fresh_charger_transport_detail(self.service, now))

    def _charger_retry_active(self, now: float) -> int:
        """Return whether charger retry backoff is currently active."""
        return int(_fresh_charger_retry_until(self.service, now) is not None)

    def _charger_retry_reason(self, now: float) -> str:
        """Return the current charger retry reason label for diagnostics."""
        return self._diagnostic_text_value(_fresh_charger_retry_reason(self.service, now))

    def _charger_retry_source(self, now: float) -> str:
        """Return the current charger retry source label for diagnostics."""
        return self._diagnostic_text_value(_fresh_charger_retry_source(self.service, now))

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

    def _learned_display_current_allowed(self, now: float | None) -> bool:
        """Return whether learned charging power may currently drive the GUI current display."""
        if not self._display_uses_learned_set_current():
            return False
        if normalize_learning_state(getattr(self.service, "learned_charge_power_state", "unknown")) != "stable":
            return False
        return not self._learned_charge_power_expired_for_display(now)

    @staticmethod
    def _validated_learned_display_scalars(
        learned_power: float | None,
        voltage: float | None,
    ) -> tuple[float, float] | None:
        """Return validated positive learned-power scalars for display-current derivation."""
        if learned_power is None or learned_power <= 0 or voltage is None or voltage <= 0:
            return None
        return float(learned_power), float(voltage)

    def _learned_display_phase(self) -> str | None:
        """Return the normalized learned/display phase signature."""
        return normalize_learning_phase(
            getattr(self.service, "learned_charge_power_phase", getattr(self.service, "phase", "L1"))
        )

    def _raw_learned_display_values(self) -> tuple[float, float, str] | None:
        """Return raw learned display values before phase-voltage normalization."""
        scalars = self._validated_learned_display_scalars(
            finite_float_or_none(getattr(self.service, "learned_charge_power_watts", None)),
            finite_float_or_none(getattr(self.service, "learned_charge_power_voltage", None)),
        )
        phase = self._learned_display_phase()
        if scalars is None or phase is None:
            return None
        learned_power, voltage = scalars
        return learned_power, voltage, phase

    def _stable_learned_display_inputs(self, now: float | None) -> _LearnedDisplayCurrentInputs | None:
        """Return the validated inputs used to derive SetCurrent from learned charging power."""
        if not self._learned_display_current_allowed(now):
            return None
        raw_values = self._raw_learned_display_values()
        if raw_values is None:
            return None
        learned_power, voltage, phase = raw_values
        phase_voltage = self._phase_voltage_for_display_current(voltage, phase)
        if phase_voltage is None:
            return None
        return _LearnedDisplayCurrentInputs(
            power_w=learned_power,
            phase_voltage_v=phase_voltage,
            phase_count=3.0 if phase == "3P" else 1.0,
        )

    def _phase_voltage_for_display_current(self, voltage: float, phase: str) -> float | None:
        """Return the per-phase voltage used for learned-current display derivation."""
        phase_voltage = float(voltage)
        if phase == "3P" and str(getattr(self.service, "voltage_mode", "phase")).strip().lower() != "phase":
            phase_voltage = phase_voltage / math.sqrt(3.0)
        return None if phase_voltage <= 0 else phase_voltage

    @staticmethod
    def _rounded_display_current(current_amps: float) -> float | None:
        """Return one positive rounded display current or None when unusable."""
        rounded_current = finite_float_or_none(round(current_amps))
        if rounded_current is None or rounded_current <= 0:
            return None
        return float(rounded_current)

    def _clamped_display_current(self, current_amps: float) -> float:
        """Clamp one display current to the configured min/max current range."""
        normalized_current = float(current_amps)
        min_current = finite_float_or_none(getattr(self.service, "min_current", None))
        max_current = finite_float_or_none(getattr(self.service, "max_current", None))
        if min_current is not None:
            normalized_current = max(normalized_current, min_current)
        if max_current is not None and max_current > 0:
            normalized_current = min(normalized_current, max_current)
        return float(normalized_current)

    def _derived_learned_set_current(self, now: float | None) -> float | None:
        """Return one rounded display current derived from the stable learned charging power."""
        inputs = self._stable_learned_display_inputs(now)
        if inputs is None:
            return None
        display_current = inputs.power_w / (inputs.phase_voltage_v * inputs.phase_count)
        rounded_current = self._rounded_display_current(display_current)
        if rounded_current is None:
            return None
        return self._clamped_display_current(rounded_current)

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
        current_time = time.time() if now is None else float(now)
        effective_supported = effective_supported_phase_selections(
            getattr(self.service, "supported_phase_selections", ("P1",)),
            lockout_selection=getattr(self.service, "_phase_switch_lockout_selection", None),
            lockout_until=getattr(self.service, "_phase_switch_lockout_until", None),
            now=current_time,
        )
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
            "/SupportedPhaseSelections": ",".join(effective_supported),
            "/SetCurrent": self._display_set_current(now),
            "/MinCurrent": getattr(self.service, "min_current", 0.0),
            "/MaxCurrent": getattr(self.service, "max_current", 0.0),
            "/Auto/StartSurplusWatts": getattr(self.service, "auto_start_surplus_watts", 0.0),
            "/Auto/StopSurplusWatts": getattr(self.service, "auto_stop_surplus_watts", 0.0),
            "/Auto/MinSoc": getattr(self.service, "auto_min_soc", 0.0),
            "/Auto/ResumeSoc": getattr(self.service, "auto_resume_soc", 0.0),
            "/Auto/StartDelaySeconds": getattr(self.service, "auto_start_delay_seconds", 0.0),
            "/Auto/StopDelaySeconds": getattr(self.service, "auto_stop_delay_seconds", 0.0),
            "/Auto/DbusBackoffBaseSeconds": getattr(self.service, "auto_dbus_backoff_base_seconds", 0.0),
            "/Auto/DbusBackoffMaxSeconds": getattr(self.service, "auto_dbus_backoff_max_seconds", 0.0),
            "/Auto/GridRecoveryStartSeconds": getattr(self.service, "auto_grid_recovery_start_seconds", 0.0),
            "/Auto/StopSurplusDelaySeconds": getattr(self.service, "auto_stop_surplus_delay_seconds", 0.0),
            "/Auto/StopSurplusVolatilityLowWatts": getattr(
                self.service,
                "auto_stop_surplus_volatility_low_watts",
                0.0,
            ),
            "/Auto/StopSurplusVolatilityHighWatts": getattr(
                self.service,
                "auto_stop_surplus_volatility_high_watts",
                0.0,
            ),
            "/Auto/ReferenceChargePowerWatts": getattr(self.service, "auto_reference_charge_power_watts", 0.0),
            "/Auto/LearnChargePowerEnabled": int(bool(getattr(self.service, "auto_learn_charge_power_enabled", True))),
            "/Auto/LearnChargePowerMinWatts": getattr(self.service, "auto_learn_charge_power_min_watts", 0.0),
            "/Auto/LearnChargePowerAlpha": getattr(self.service, "auto_learn_charge_power_alpha", 0.0),
            "/Auto/LearnChargePowerStartDelaySeconds": getattr(
                self.service,
                "auto_learn_charge_power_start_delay_seconds",
                0.0,
            ),
            "/Auto/LearnChargePowerWindowSeconds": getattr(
                self.service,
                "auto_learn_charge_power_window_seconds",
                0.0,
            ),
            "/Auto/LearnChargePowerMaxAgeSeconds": getattr(
                self.service,
                "auto_learn_charge_power_max_age_seconds",
                0.0,
            ),
            "/Auto/PhaseSwitching": int(bool(getattr(self.service, "auto_phase_switching_enabled", True))),
            "/Auto/PhasePreferLowestWhenIdle": int(
                bool(getattr(self.service, "auto_phase_prefer_lowest_when_idle", True))
            ),
            "/Auto/PhaseUpshiftDelaySeconds": getattr(self.service, "auto_phase_upshift_delay_seconds", 0.0),
            "/Auto/PhaseDownshiftDelaySeconds": getattr(self.service, "auto_phase_downshift_delay_seconds", 0.0),
            "/Auto/PhaseUpshiftHeadroomWatts": getattr(self.service, "auto_phase_upshift_headroom_watts", 0.0),
            "/Auto/PhaseDownshiftMarginWatts": getattr(self.service, "auto_phase_downshift_margin_watts", 0.0),
            "/Auto/PhaseMismatchRetrySeconds": getattr(self.service, "auto_phase_mismatch_retry_seconds", 0.0),
            "/Auto/PhaseMismatchLockoutCount": getattr(self.service, "auto_phase_mismatch_lockout_count", 0),
            "/Auto/PhaseMismatchLockoutSeconds": getattr(self.service, "auto_phase_mismatch_lockout_seconds", 0.0),
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

    @staticmethod
    def _auto_metrics(service: Any) -> dict[str, Any]:
        """Return the latest Auto metrics mapping used for outward diagnostics."""
        metrics = getattr(service, "_last_auto_metrics", None)
        return dict(cast(dict[str, Any], metrics)) if isinstance(metrics, dict) else {}

    @classmethod
    def _auto_phase_metric_text(cls, service: Any, field_name: str) -> str:
        """Return one outward-safe Auto phase metric text value."""
        raw_value = cls._auto_metrics(service).get(field_name)
        return "" if raw_value is None else str(raw_value).strip()

    @staticmethod
    def _diagnostic_text_value(raw_value: Any) -> str:
        """Return one stripped diagnostic text value or an empty string."""
        return "" if raw_value is None else str(raw_value).strip()

    @staticmethod
    def _fault_reason(service: Any) -> str:
        """Return the active hard EVSE-fault reason or an empty string."""
        reason = evse_fault_reason(getattr(service, "_last_health_reason", ""))
        return "" if reason is None else reason

    @classmethod
    def _fault_active(cls, service: Any) -> int:
        """Return whether a hard EVSE fault is currently active."""
        return int(bool(cls._fault_reason(service)))

    @staticmethod
    def _recovery_active(service: Any) -> int:
        """Return whether the broad Auto state is currently in recovery mode."""
        auto_state, _auto_state_code = normalized_auto_state_pair(
            getattr(service, "_last_auto_state", "idle"),
            getattr(service, "_last_auto_state_code", 0),
        )
        return int(auto_state == "recovery")

    @classmethod
    def _observed_phase_value(cls, service: Any) -> str:
        """Return the latest observed phase selection from PM status or charger readback."""
        pm_status = getattr(service, "_last_confirmed_pm_status", None)
        if isinstance(pm_status, Mapping):
            observed = cls._diagnostic_text_value(pm_status.get("_phase_selection"))
            if observed:
                return observed
        return cls._diagnostic_text_value(getattr(service, "_last_charger_state_phase_selection", None))

    @staticmethod
    def _phase_switch_mismatch_active(service: Any) -> int:
        """Return whether a phase-switch mismatch is currently active."""
        active = bool(getattr(service, "_phase_switch_mismatch_active", False))
        if active:
            return 1
        return int(str(getattr(service, "_last_health_reason", "")) == "phase-switch-mismatch")

    @staticmethod
    def _phase_switch_lockout_active(service: Any, now: float) -> int:
        """Return whether a phase-switch lockout is currently active."""
        lockout_selection = getattr(service, "_phase_switch_lockout_selection", None)
        lockout_until = finite_float_or_none(getattr(service, "_phase_switch_lockout_until", None))
        if lockout_selection is None or lockout_until is None:
            return 0
        return 1 if float(now) < lockout_until else 0

    @classmethod
    def _phase_switch_lockout_target(cls, service: Any, now: float) -> str:
        """Return the active phase-switch lockout target or an empty string."""
        if cls._phase_switch_lockout_active(service, now) == 0:
            return ""
        return cls._diagnostic_text_value(getattr(service, "_phase_switch_lockout_selection", None))

    @classmethod
    def _phase_switch_lockout_reason(cls, service: Any, now: float) -> str:
        """Return the active phase-switch lockout reason or an empty string."""
        if cls._phase_switch_lockout_active(service, now) == 0:
            return ""
        return cls._diagnostic_text_value(getattr(service, "_phase_switch_lockout_reason", None))

    @staticmethod
    def _phase_supported_configured(service: Any) -> str:
        """Return the configured supported phase selections without runtime degradation."""
        return ",".join(tuple(getattr(service, "supported_phase_selections", ("P1",))))

    @classmethod
    def _phase_supported_effective(cls, service: Any, now: float) -> str:
        """Return the effective supported phase selections after lockout degradation."""
        effective_supported = effective_supported_phase_selections(
            getattr(service, "supported_phase_selections", ("P1",)),
            lockout_selection=getattr(service, "_phase_switch_lockout_selection", None),
            lockout_until=getattr(service, "_phase_switch_lockout_until", None),
            now=now,
        )
        return ",".join(effective_supported)

    @classmethod
    def _phase_degraded_active(cls, service: Any, now: float) -> int:
        """Return whether runtime phase support is currently degraded."""
        return int(cls._phase_supported_configured(service) != cls._phase_supported_effective(service, now))

    @staticmethod
    def _switch_feedback_closed(service: Any) -> int:
        """Return explicit switch feedback as 0/1, or -1 when unavailable."""
        feedback_closed = getattr(service, "_last_switch_feedback_closed", None)
        return -1 if feedback_closed is None else int(bool(feedback_closed))

    @staticmethod
    def _switch_interlock_ok(service: Any) -> int:
        """Return explicit switch interlock state as 0/1, or -1 when unavailable."""
        interlock_ok = getattr(service, "_last_switch_interlock_ok", None)
        return -1 if interlock_ok is None else int(bool(interlock_ok))

    @classmethod
    def _switch_feedback_mismatch(cls, service: Any) -> int:
        """Return whether explicit switch feedback currently disagrees with relay state."""
        feedback_closed = getattr(service, "_last_switch_feedback_closed", None)
        if feedback_closed is None:
            return int(str(getattr(service, "_last_health_reason", "")) == "contactor-feedback-mismatch")
        pm_status = getattr(service, "_last_confirmed_pm_status", None)
        relay_on = False if not isinstance(pm_status, Mapping) else bool(pm_status.get("output", False))
        return int(switch_feedback_mismatch(relay_on, feedback_closed))

    @staticmethod
    def _contactor_suspected_open(service: Any) -> int:
        """Return whether runtime currently suspects an open contactor without explicit feedback."""
        return int(str(getattr(service, "_last_health_reason", "")) == "contactor-suspected-open")

    @staticmethod
    def _contactor_suspected_welded(service: Any) -> int:
        """Return whether runtime currently suspects a welded contactor without explicit feedback."""
        return int(str(getattr(service, "_last_health_reason", "")) == "contactor-suspected-welded")

    @staticmethod
    def _contactor_lockout_reason(service: Any) -> str:
        """Return the active contactor-fault lockout reason or an empty string."""
        reason = str(getattr(service, "_contactor_lockout_reason", "") or "").strip()
        return reason

    @classmethod
    def _contactor_lockout_active(cls, service: Any) -> int:
        """Return whether a contactor-fault lockout is currently latched."""
        return int(bool(cls._contactor_lockout_reason(service)))

    @staticmethod
    def _contactor_lockout_source(service: Any) -> str:
        """Return the active contactor-fault lockout source or an empty string."""
        source = str(getattr(service, "_contactor_lockout_source", "") or "").strip()
        return source

    @classmethod
    def _contactor_fault_count(cls, service: Any) -> int:
        """Return the current contactor-fault counter for the active or latched reason."""
        counts = getattr(service, "_contactor_fault_counts", None)
        if not isinstance(counts, dict):
            return 0
        reason = cls._contactor_lockout_reason(service)
        if not reason:
            reason = str(getattr(service, "_contactor_fault_active_reason", "") or "").strip()
        if not reason:
            return 0
        return int(counts.get(reason, 0))

    @classmethod
    def _auto_phase_metric_float(cls, service: Any, field_name: str) -> float:
        """Return one outward-safe Auto phase metric float value or -1 when absent."""
        value = finite_float_or_none(cls._auto_metrics(service).get(field_name))
        return -1.0 if value is None else float(value)

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
            "/Auto/RecoveryActive": self._recovery_active(self.service),
            "/Auto/StatusSource": str(getattr(self.service, "_last_status_source", "unknown")),
            "/Auto/FaultActive": self._fault_active(self.service),
            "/Auto/FaultReason": self._fault_reason(self.service),
            "/Auto/BackendMode": self._backend_mode_value(self.service),
            "/Auto/MeterBackend": self._backend_type_value(self.service, "meter_backend_type", "shelly_combined"),
            "/Auto/SwitchBackend": self._backend_type_value(self.service, "switch_backend_type", "shelly_combined"),
            "/Auto/ChargerBackend": self._backend_type_value(self.service, "charger_backend_type"),
            "/Auto/ChargerStatus": self._charger_text_observed("_last_charger_state_status"),
            "/Auto/ChargerFault": self._charger_text_observed("_last_charger_state_fault"),
            "/Auto/ChargerFaultActive": int(bool(getattr(self.service, "_last_charger_fault_active", 0))),
            "/Auto/ChargerEstimateActive": self._charger_estimate_active(),
            "/Auto/ChargerEstimateSource": self._charger_estimate_source(),
            "/Auto/RuntimeOverridesActive": int(bool(getattr(self.service, "_runtime_overrides_active", False))),
            "/Auto/RuntimeOverridesPath": str(getattr(self.service, "runtime_overrides_path", "") or ""),
            "/Auto/ChargerTransportActive": self._charger_transport_active(now),
            "/Auto/ChargerTransportReason": self._charger_transport_reason(now),
            "/Auto/ChargerTransportSource": self._charger_transport_source(now),
            "/Auto/ChargerTransportDetail": self._charger_transport_detail(now),
            "/Auto/ChargerRetryActive": self._charger_retry_active(now),
            "/Auto/ChargerRetryReason": self._charger_retry_reason(now),
            "/Auto/ChargerRetrySource": self._charger_retry_source(now),
            "/Auto/ErrorCount": error_count,
            "/Auto/DbusReadErrors": int(error_state.get("dbus", 0)),
            "/Auto/ShellyReadErrors": int(error_state.get("shelly", 0)),
            "/Auto/ChargerWriteErrors": int(error_state.get("charger", 0)),
            "/Auto/PvReadErrors": int(error_state.get("pv", 0)),
            "/Auto/BatteryReadErrors": int(error_state.get("battery", 0)),
            "/Auto/GridReadErrors": int(error_state.get("grid", 0)),
            "/Auto/InputCacheHits": int(error_state.get("cache_hits", 0)),
            "/Auto/ChargerCurrentTarget": self._charger_current_target_value(self.service),
            "/Auto/PhaseCurrent": self._auto_phase_metric_text(self.service, "phase_current"),
            "/Auto/PhaseObserved": self._observed_phase_value(self.service),
            "/Auto/PhaseTarget": self._auto_phase_metric_text(self.service, "phase_target"),
            "/Auto/PhaseReason": self._auto_phase_metric_text(self.service, "phase_reason"),
            "/Auto/PhaseMismatchActive": self._phase_switch_mismatch_active(self.service),
            "/Auto/PhaseLockoutActive": self._phase_switch_lockout_active(self.service, now),
            "/Auto/PhaseLockoutTarget": self._phase_switch_lockout_target(self.service, now),
            "/Auto/PhaseLockoutReason": self._phase_switch_lockout_reason(self.service, now),
            "/Auto/PhaseSupportedConfigured": self._phase_supported_configured(self.service),
            "/Auto/PhaseSupportedEffective": self._phase_supported_effective(self.service, now),
            "/Auto/PhaseDegradedActive": self._phase_degraded_active(self.service, now),
            "/Auto/SwitchFeedbackClosed": self._switch_feedback_closed(self.service),
            "/Auto/SwitchInterlockOk": self._switch_interlock_ok(self.service),
            "/Auto/SwitchFeedbackMismatch": self._switch_feedback_mismatch(self.service),
            "/Auto/ContactorSuspectedOpen": self._contactor_suspected_open(self.service),
            "/Auto/ContactorSuspectedWelded": self._contactor_suspected_welded(self.service),
            "/Auto/ContactorFaultCount": self._contactor_fault_count(self.service),
            "/Auto/ContactorLockoutActive": self._contactor_lockout_active(self.service),
            "/Auto/ContactorLockoutReason": self._contactor_lockout_reason(self.service),
            "/Auto/ContactorLockoutSource": self._contactor_lockout_source(self.service),
            "/Auto/PhaseThresholdWatts": self._auto_phase_metric_float(self.service, "phase_threshold_watts"),
            "/Auto/PhaseCandidate": self._auto_phase_metric_text(self.service, "phase_candidate"),
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
            "/Auto/PhaseCandidateAge": self._age_seconds(
                getattr(svc, "_auto_phase_target_since", None), now
            ),
            "/Auto/PhaseLockoutAge": self._age_seconds(
                getattr(svc, "_phase_switch_lockout_at", None) if self._phase_switch_lockout_active(svc, now) else None,
                now,
            ),
            "/Auto/ContactorLockoutAge": self._age_seconds(
                getattr(svc, "_contactor_lockout_at", None) if self._contactor_lockout_active(svc) else None,
                now,
            ),
            "/Auto/LastSwitchFeedbackAge": self._age_seconds(
                getattr(svc, "_last_switch_feedback_at", None), now
            ),
            "/Auto/LastChargerReadAge": self._age_seconds(
                getattr(svc, "_last_charger_state_at", None), now
            ),
            "/Auto/LastChargerEstimateAge": self._age_seconds(
                getattr(svc, "_last_charger_estimate_at", None)
                if self._charger_estimate_active()
                else None,
                now,
            ),
            "/Auto/LastChargerTransportAge": self._age_seconds(
                _fresh_charger_transport_timestamp(svc, now), now
            ),
            "/Auto/ChargerRetryRemaining": float(_charger_retry_remaining_seconds(svc, now)),
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
