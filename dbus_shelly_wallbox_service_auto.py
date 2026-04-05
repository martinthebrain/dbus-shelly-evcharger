# SPDX-License-Identifier: GPL-3.0-or-later
"""DBus-input and Auto-logic mixins for the Shelly wallbox service."""

from dbus_shelly_wallbox_auto_controller import AutoDecisionController
from dbus_shelly_wallbox_service_factory import ServiceControllerFactoryMixin


class DbusAutoLogicMixin(ServiceControllerFactoryMixin):
    """Static DBus-input, Auto-decision, and write-controller delegations."""

    @staticmethod
    def _get_available_surplus_watts(pv_power, grid_power):
        return AutoDecisionController.get_available_surplus_watts(pv_power, grid_power)

    def _mode_uses_auto_logic(self, mode):
        return self._mode_uses_auto_logic_func(mode)

    def _normalize_mode(self, value):
        return self._normalize_mode_func(value)

    def _get_dbus_value(self, service_name, object_path):
        self._ensure_dbus_input_controller()
        return self._dbus_input_controller.get_dbus_value(service_name, object_path)

    def _list_dbus_services(self):
        self._ensure_dbus_input_controller()
        return self._dbus_input_controller.list_dbus_services()

    def _invalidate_auto_pv_services(self):
        self._ensure_dbus_input_controller()
        return self._dbus_input_controller.invalidate_auto_pv_services()

    def _invalidate_auto_battery_service(self):
        self._ensure_dbus_input_controller()
        return self._dbus_input_controller.invalidate_auto_battery_service()

    def _resolve_auto_pv_services(self):
        self._ensure_dbus_input_controller()
        return self._dbus_input_controller.resolve_auto_pv_services()

    def _get_pv_power(self):
        self._ensure_dbus_input_controller()
        return self._dbus_input_controller.get_pv_power()

    def _resolve_auto_battery_service(self):
        self._ensure_dbus_input_controller()
        return self._dbus_input_controller.resolve_auto_battery_service()

    def _get_battery_soc(self):
        self._ensure_dbus_input_controller()
        return self._dbus_input_controller.get_battery_soc()

    def _get_grid_power(self):
        self._ensure_dbus_input_controller()
        return self._dbus_input_controller.get_grid_power()

    def _add_auto_sample(self, now, surplus_power, grid_power):
        self._ensure_auto_controller()
        return self._auto_controller.add_auto_sample(now, surplus_power, grid_power)

    def _clear_auto_samples(self):
        self._ensure_auto_controller()
        return self._auto_controller.clear_auto_samples()

    def _average_auto_metric(self, index):
        self._ensure_auto_controller()
        return self._auto_controller.average_auto_metric(index)

    def _mark_relay_changed(self, relay_on, now=None):
        self._ensure_auto_controller()
        return self._auto_controller.mark_relay_changed(relay_on, now)

    def _is_within_auto_daytime_window(self, current_dt=None):
        self._ensure_auto_controller()
        return self._auto_controller.is_within_auto_daytime_window(current_dt)

    def _set_health(self, reason, cached=False):
        self._ensure_auto_controller()
        return self._auto_controller.set_health(reason, cached)

    def _auto_decide_relay(self, relay_on, pv_power, battery_soc, grid_power):
        self._ensure_auto_controller()
        return self._auto_controller.auto_decide_relay(relay_on, pv_power, battery_soc, grid_power)

    def _handle_write(self, path, value):
        self._ensure_write_controller()
        return self._write_controller.handle_write(path, value)

    def _register_paths(self):
        self._ensure_bootstrap_controller()
        return self._bootstrap_controller.register_paths()

    def _fetch_device_info_with_fallback(self):
        self._ensure_bootstrap_controller()
        return self._bootstrap_controller.fetch_device_info_with_fallback()
