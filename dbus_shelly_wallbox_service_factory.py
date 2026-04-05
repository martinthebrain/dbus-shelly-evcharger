# SPDX-License-Identifier: GPL-3.0-or-later
"""Lazy controller factory mixin for the Shelly wallbox service."""

from dbus_shelly_wallbox_auto_controller import AutoDecisionController
from dbus_shelly_wallbox_auto_input_supervisor import AutoInputSupervisor
from dbus_shelly_wallbox_bootstrap import ServiceBootstrapController
from dbus_shelly_wallbox_dbus_inputs import DbusInputController
from dbus_shelly_wallbox_ports import AutoDecisionPort, DbusInputPort, UpdateCyclePort, WriteControllerPort
from dbus_shelly_wallbox_publisher import DbusPublishController
from dbus_shelly_wallbox_runtime_support import RuntimeSupportController
from dbus_shelly_wallbox_shelly_io import ShellyIoController
from dbus_shelly_wallbox_state import ServiceStateController
from dbus_shelly_wallbox_update_cycle import UpdateCycleController
from dbus_shelly_wallbox_write_controller import DbusWriteController


class ServiceControllerFactoryMixin:
    """Lazy controller builders shared by the wallbox service mixins."""

    _normalize_mode_func = None
    _mode_uses_auto_logic_func = None
    _normalize_phase_func = None
    _month_window_func = None
    _age_seconds_func = None
    _health_code_func = None
    _phase_values_func = None
    _read_version_func = None
    _gobject_module = None
    _script_path_value = None
    _formatter_bundle = None

    def _ensure_dbus_publisher(self):
        if not hasattr(self, "_dbus_publisher") or self._dbus_publisher is None:
            self._dbus_publisher = DbusPublishController(self, self._age_seconds_func)

    def _ensure_auto_controller(self):
        if not hasattr(self, "_auto_controller") or self._auto_controller is None:
            self._auto_controller = AutoDecisionController(
                AutoDecisionPort(self),
                self._health_code_func,
                self._mode_uses_auto_logic_func,
            )

    def _ensure_shelly_io_controller(self):
        if not hasattr(self, "_shelly_io_controller") or self._shelly_io_controller is None:
            self._shelly_io_controller = ShellyIoController(self)

    def _ensure_state_controller(self):
        if not hasattr(self, "_state_controller") or self._state_controller is None:
            self._state_controller = ServiceStateController(self, self._normalize_mode_func)

    def _ensure_write_controller(self):
        if not hasattr(self, "_write_controller") or self._write_controller is None:
            self._write_controller = DbusWriteController(WriteControllerPort(self))

    def _ensure_auto_input_supervisor(self):
        if not hasattr(self, "_auto_input_supervisor") or self._auto_input_supervisor is None:
            self._auto_input_supervisor = AutoInputSupervisor(self)

    def _ensure_runtime_support_controller(self):
        if not hasattr(self, "_runtime_support_controller") or self._runtime_support_controller is None:
            self._runtime_support_controller = RuntimeSupportController(self, self._age_seconds_func, self._health_code_func)

    def _ensure_dbus_input_controller(self):
        if not hasattr(self, "_dbus_input_controller") or self._dbus_input_controller is None:
            self._dbus_input_controller = DbusInputController(DbusInputPort(self))

    def _ensure_bootstrap_controller(self):
        if not hasattr(self, "_bootstrap_controller") or self._bootstrap_controller is None:
            self._bootstrap_controller = ServiceBootstrapController(
                self,
                normalize_phase_func=self._normalize_phase_func,
                normalize_mode_func=self._normalize_mode_func,
                mode_uses_auto_logic_func=self._mode_uses_auto_logic_func,
                month_window_func=self._month_window_func,
                age_seconds_func=self._age_seconds_func,
                health_code_func=self._health_code_func,
                phase_values_func=self._phase_values_func,
                read_version_func=self._read_version_func,
                gobject_module=self._gobject_module,
                script_path=self._script_path_value,
                formatters=self._formatter_bundle,
            )

    def _ensure_update_controller(self):
        if not hasattr(self, "_update_controller") or self._update_controller is None:
            self._update_controller = UpdateCycleController(
                UpdateCyclePort(self),
                self._phase_values_func,
                self._health_code_func,
            )
