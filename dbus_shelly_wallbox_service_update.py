# SPDX-License-Identifier: GPL-3.0-or-later
"""Update-cycle mixin for the Shelly wallbox service."""

from dbus_shelly_wallbox_service_factory import ServiceControllerFactoryMixin


class UpdateCycleMixin(ServiceControllerFactoryMixin):
    """Static update-cycle delegations."""

    def _ensure_virtual_state_defaults(self):
        self._ensure_update_controller()
        return self._update_controller.ensure_virtual_state_defaults()

    def _session_state_from_status(self, status, current_total_energy, relay_on, now):
        self._ensure_update_controller()
        return self._update_controller.session_state_from_status(self, status, current_total_energy, relay_on, now)

    def _startstop_display_for_state(self, relay_on):
        self._ensure_update_controller()
        return self._update_controller.startstop_display_for_state(self, relay_on)

    def _phase_energies_for_total(self, current_total_energy):
        self._ensure_update_controller()
        return self._update_controller.phase_energies_for_total(self, current_total_energy)

    def _publish_virtual_state_paths(self, current_total_energy, charging_time, session_energy, startstop_display, now):
        self._ensure_update_controller()
        return self._update_controller.publish_virtual_state_paths(
            current_total_energy,
            charging_time,
            session_energy,
            startstop_display,
            now,
        )

    def _update_virtual_state(self, status, current_total_energy, relay_on):
        self._ensure_update_controller()
        return self._update_controller.update_virtual_state(status, current_total_energy, relay_on)

    def _prepare_update_cycle(self, now):
        self._ensure_update_controller()
        return self._update_controller.prepare_update_cycle(self, now)

    def _resolve_pm_status_for_update(self, worker_snapshot, now):
        self._ensure_update_controller()
        return self._update_controller.resolve_pm_status_for_update(self, worker_snapshot, now)

    def _publish_offline_update(self, now):
        self._ensure_update_controller()
        return self._update_controller.publish_offline_update(now)

    def _extract_pm_measurements(self, pm_status):
        self._ensure_update_controller()
        return self._update_controller.extract_pm_measurements(self, pm_status)

    def _resolve_cached_input_value(self, value, snapshot_at, last_value_attr, last_at_attr, now):
        self._ensure_update_controller()
        return self._update_controller.resolve_cached_input_value(
            self,
            value,
            snapshot_at,
            last_value_attr,
            last_at_attr,
            now,
        )

    def _resolve_auto_inputs(self, worker_snapshot, now):
        self._ensure_update_controller()
        return self._update_controller.resolve_auto_inputs(worker_snapshot, now)

    def _log_auto_relay_change(self, desired_relay, relay_on, pv_power, battery_soc, grid_power):
        self._ensure_update_controller()
        return self._update_controller.log_auto_relay_change(
            self,
            desired_relay,
            relay_on,
            pv_power,
            battery_soc,
            grid_power,
        )

    def _apply_relay_decision(self, desired_relay, relay_on, now):
        self._ensure_update_controller()
        return self._update_controller.apply_relay_decision(desired_relay, relay_on, now)

    def _derive_status_code(self, relay_on, power):
        self._ensure_update_controller()
        return self._update_controller.derive_status_code(self, relay_on, power)

    def _publish_online_update(self, pm_status, now):
        self._ensure_update_controller()
        return self._update_controller.publish_online_update(pm_status, now)

    def _complete_update_cycle(self, changed):
        self._ensure_update_controller()
        return self._update_controller.complete_update_cycle(self, changed)

    def _sign_of_life(self):
        self._ensure_update_controller()
        return self._update_controller.sign_of_life()

    def _update(self):
        self._ensure_update_controller()
        return self._update_controller.update()
