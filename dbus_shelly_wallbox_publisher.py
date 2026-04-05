# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers for throttled DBus publishing in the Shelly wallbox service."""

import time


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

        if not should_write:
            return False

        self.service._dbusservice[path] = value
        self.service._dbus_publish_state[path] = {"value": value, "updated_at": current}
        return True

    def _publish_values(self, values, now, interval_seconds=None, force=False):
        """Publish a group of DBus values with shared throttling rules."""
        changed = False
        for path, value in values.items():
            changed |= self.publish_path(
                path,
                value,
                now,
                interval_seconds=interval_seconds,
                force=force,
            )
        return changed

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
        return self._publish_values(
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
        return self._publish_values(
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
        return self._publish_values(self._config_values(startstop_display), now)

    def _diagnostic_counter_values(self, now):
        """Return change-driven diagnostic counters keyed by DBus path."""
        error_state = self.service._error_state
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
        return {
            "/Auto/LastShellyReadAge": self._age_seconds(svc._last_pm_status_at, now),
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
        changed = self._publish_values(self._diagnostic_counter_values(now), now)
        changed |= self._publish_values(
            self._diagnostic_age_values(now),
            now,
            interval_seconds=self.service._dbus_slow_publish_interval_seconds,
        )
        return changed
