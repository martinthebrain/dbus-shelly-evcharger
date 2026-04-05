# SPDX-License-Identifier: GPL-3.0-or-later
"""State and DBus-publish mixins for the Shelly wallbox service."""

from dbus_shelly_wallbox_runtime_support import RuntimeSupportController
from dbus_shelly_wallbox_service_factory import ServiceControllerFactoryMixin
from dbus_shelly_wallbox_state import ServiceStateController


class StatePublishMixin(ServiceControllerFactoryMixin):
    """Static state and DBus publish delegations."""

    @staticmethod
    def _config_path():
        return ServiceStateController.config_path()

    @staticmethod
    def _coerce_runtime_int(value, default=0):
        return ServiceStateController.coerce_runtime_int(value, default)

    @staticmethod
    def _coerce_runtime_float(value, default=0.0):
        return ServiceStateController.coerce_runtime_float(value, default)

    @staticmethod
    def _empty_worker_snapshot():
        return RuntimeSupportController.empty_worker_snapshot()

    @staticmethod
    def _clone_worker_snapshot(snapshot):
        return RuntimeSupportController.clone_worker_snapshot(snapshot)

    @staticmethod
    def _observability_state_defaults():
        return RuntimeSupportController.observability_state_defaults()

    def _state_summary(self):
        self._ensure_state_controller()
        return self._state_controller.state_summary()

    def _current_runtime_state(self):
        self._ensure_state_controller()
        return self._state_controller.current_runtime_state()

    def _load_runtime_state(self):
        self._ensure_state_controller()
        return self._state_controller.load_runtime_state()

    def _save_runtime_state(self):
        self._ensure_state_controller()
        return self._state_controller.save_runtime_state()

    def _validate_runtime_config(self):
        self._ensure_state_controller()
        return self._state_controller.validate_runtime_config()

    def _load_config(self):
        self._ensure_state_controller()
        return self._state_controller.load_config()

    def _ensure_dbus_publish_state(self):
        self._ensure_dbus_publisher()
        return self._dbus_publisher.ensure_state()

    def _publish_dbus_path(self, path, value, current_time, force=False):
        self._ensure_dbus_publisher()
        return self._dbus_publisher.publish_path(path, value, current_time, force=force)

    def _bump_update_index(self, current_time):
        self._ensure_dbus_publisher()
        return self._dbus_publisher.bump_update_index(current_time)

    def _publish_live_measurements(self, power, voltage, total_current, phase_data, now):
        self._ensure_dbus_publisher()
        return self._dbus_publisher.publish_live_measurements(power, voltage, total_current, phase_data, now)

    def _publish_energy_time_measurements(self, current_total_energy, phase_energies, charging_time, session_energy, now):
        self._ensure_dbus_publisher()
        return self._dbus_publisher.publish_energy_time_measurements(
            current_total_energy,
            phase_energies,
            charging_time,
            session_energy,
            now,
        )

    def _publish_config_paths(self, startstop_display, now):
        self._ensure_dbus_publisher()
        return self._dbus_publisher.publish_config_paths(startstop_display, now)

    def _publish_diagnostic_paths(self, now):
        self._ensure_dbus_publisher()
        return self._dbus_publisher.publish_diagnostic_paths(now)
