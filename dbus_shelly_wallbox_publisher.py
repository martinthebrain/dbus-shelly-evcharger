# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers for throttled DBus publishing in the Shelly wallbox service."""

import time

from dbus_shelly_wallbox_contracts import displayable_confirmed_read_timestamp, normalized_auto_state_pair


class DbusPublishController:
    """Publish Shelly wallbox DBus paths with simple change and interval throttling."""

    PHASE_NAMES = ("L1", "L2", "L3")

    def __init__(self, service, age_seconds_func):
        self.service = service
        self._age_seconds = age_seconds_func

    def ensure_state(self):
        """Initialize DBus publish throttling helpers for tests or partial instances."""
        if not hasattr(self.service, "_dbus_publish_state"):
            self.service._dbus_publish_state = {}
        if not hasattr(self.service, "_dbus_live_publish_interval_seconds"):
            self.service._dbus_live_publish_interval_seconds = 1.0
        if not hasattr(self.service, "_dbus_slow_publish_interval_seconds"):
            self.service._dbus_slow_publish_interval_seconds = 5.0

    def publish_path(self, path, value, now=None, interval_seconds=None, force=False):
        """Publish a DBus path immediately, on change, or with a minimum interval."""
        self.ensure_state()
        current = time.time() if now is None else float(now)
        should_write, _entry = self._publish_decision(path, value, current, interval_seconds, force)
        if not should_write:
            return False

        self.service._dbusservice[path] = value
        self.service._dbus_publish_state[path] = {"value": value, "updated_at": current}
        return True

    def _publish_decision(self, path, value, current, interval_seconds, force):
        """Return whether one path should be written plus its current publish-state entry."""
        entry = self.service._dbus_publish_state.get(path)
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

    def _publish_group_failure(self, group_name, failed_paths, current):
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
            import logging

            logging.warning(
                "DBus publish group %s failed for paths %s at %.3f",
                group_name,
                ",".join(failed_paths),
                current,
            )

    def _restore_group_publish_state(self, staged_entries):
        """Best-effort restore of local DBus publish bookkeeping after a failed group publish."""
        for path, entry in staged_entries.items():
            if entry is None:
                self.service._dbus_publish_state.pop(path, None)
            else:
                self.service._dbus_publish_state[path] = dict(entry)

    def _publish_values_transactional(self, group_name, values, now, interval_seconds=None, force=False):
        """Publish one DBus path group with shared best-effort rollback and failure reporting."""
        self.ensure_state()
        current = time.time() if now is None else float(now)
        staged_values = []
        staged_entries = {}
        original_service_values = {}

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
        failed_paths = []
        published_paths = []
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
                continue
            try:
                self.service._dbusservice[path] = original_value
            except Exception:  # pylint: disable=broad-except
                pass
        self._restore_group_publish_state(staged_entries)
        self._publish_group_failure(group_name, failed_paths, current)
        return False

    def _publish_values(self, values, now, interval_seconds=None, force=False):
        """Publish a group of DBus values with shared throttling rules."""
        return self._publish_values_transactional(
            "generic",
            values,
            now,
            interval_seconds=interval_seconds,
            force=force,
        )

    def bump_update_index(self, now=None):
        """Increment UpdateIndex when a set of published values changed."""
        self.ensure_state()
        current = time.time() if now is None else float(now)
        index = self.service._dbusservice["/UpdateIndex"] + 1
        next_index = 0 if index > 255 else index
        self.service._dbusservice["/UpdateIndex"] = next_index
        self.service._dbus_publish_state["/UpdateIndex"] = {"value": next_index, "updated_at": current}

    def _live_measurement_values(self, power, voltage, total_current, phase_data):
        """Return fast-moving AC measurement values keyed by DBus path."""
        values = {
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

    def publish_live_measurements(self, power, voltage, total_current, phase_data, now):
        """Publish fast-changing AC measurements once per second."""
        self.ensure_state()
        return self._publish_values_transactional(
            "live-measurements",
            self._live_measurement_values(power, voltage, total_current, phase_data),
            now,
            interval_seconds=self.service._dbus_live_publish_interval_seconds,
        )

    def _energy_time_values(self, energy_forward, phase_energies, charging_time, session_energy):
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

    def publish_energy_time_measurements(self, energy_forward, phase_energies, charging_time, session_energy, now):
        """Publish energy and time related values at most every five seconds."""
        self.ensure_state()
        return self._publish_values_transactional(
            "energy-time",
            self._energy_time_values(energy_forward, phase_energies, charging_time, session_energy),
            now,
            interval_seconds=self.service._dbus_slow_publish_interval_seconds,
        )

    def _config_values(self, startstop_display):
        """Return mode and control values keyed by DBus path."""
        return {
            "/Mode": int(self.service.virtual_mode),
            "/AutoStart": int(self.service.virtual_autostart),
            "/StartStop": int(startstop_display),
            "/Enable": int(self.service.virtual_enable),
            "/SetCurrent": self.service.virtual_set_current,
            "/MinCurrent": self.service.min_current,
            "/MaxCurrent": self.service.max_current,
        }

    def publish_config_paths(self, startstop_display, now):
        """Publish configuration-like EV charger paths only when they change."""
        self.ensure_state()
        return self._publish_values_transactional("config", self._config_values(startstop_display), now)

    def _diagnostic_counter_values(self, now):
        """Return change-driven diagnostic counters keyed by DBus path."""
        error_state = self.service._error_state
        auto_state, auto_state_code = normalized_auto_state_pair(
            getattr(self.service, "_last_auto_state", "idle"),
            getattr(self.service, "_last_auto_state_code", 0),
        )
        error_count = int(
            error_state.get("dbus", 0)
            + error_state.get("shelly", 0)
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
            "/Auto/ErrorCount": error_count,
            "/Auto/DbusReadErrors": int(error_state.get("dbus", 0)),
            "/Auto/ShellyReadErrors": int(error_state.get("shelly", 0)),
            "/Auto/PvReadErrors": int(error_state.get("pv", 0)),
            "/Auto/BatteryReadErrors": int(error_state.get("battery", 0)),
            "/Auto/GridReadErrors": int(error_state.get("grid", 0)),
            "/Auto/InputCacheHits": int(error_state.get("cache_hits", 0)),
            "/Auto/Stale": 1 if self.service._is_update_stale(now) else 0,
            "/Auto/RecoveryAttempts": int(self.service._recovery_attempts),
        }

    def _diagnostic_age_values(self, now):
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
            "/Auto/LastSuccessfulUpdateAge": self._age_seconds(svc._last_successful_update_at, now),
            "/Auto/StaleSeconds": self._age_seconds(stale_base, now),
        }

    def publish_diagnostic_paths(self, now):
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
