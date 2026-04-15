# SPDX-License-Identifier: GPL-3.0-or-later
"""Bootstrap and service-registration helpers for the Shelly wallbox service.

This module is the place to look first when you want to understand how the
service comes up:
- read config
- normalize and validate wallbox state
- build controller objects
- register DBus paths
- start the helper/worker processes
- hand control over to the GLib main loop
"""

from __future__ import annotations

import logging
import platform
from collections.abc import Callable
from typing import Any

from dbus_shelly_wallbox_split_mixins import _ComposableControllerMixin
PathSpec = tuple[Any, Callable[[Any, Any], str] | None]
PathMap = dict[str, PathSpec]

class _ServiceBootstrapPathMixin(_ComposableControllerMixin):
    def register_paths(self) -> None:
        """Register all DBus paths exposed by the emulated EV charger."""
        svc = self.service
        self._register_management_paths()
        for path, (initial, formatter) in self._all_service_paths().items():
            logging.debug("Registering path: %s initial=%r formatter=%r", path, initial, formatter)
            try:
                svc._dbusservice.add_path(
                    path,
                    initial,
                    gettextcallback=formatter,
                    writeable=path in self.WRITABLE_PATHS,
                    onchangecallback=svc._handle_write,
                )
            except Exception as error:  # pylint: disable=broad-except
                logging.error("Failed to register path %s: %s", path, error, exc_info=error)
                raise
        svc._dbusservice.register()

    def _register_management_paths(self) -> None:
        """Register immutable management and identity DBus paths."""
        svc = self.service
        svc._dbusservice.add_path("/Mgmt/ProcessName", self._script_path)
        svc._dbusservice.add_path(
            "/Mgmt/ProcessVersion",
            "Unknown version, and running on Python " + platform.python_version(),
        )
        svc._dbusservice.add_path("/Mgmt/Connection", svc.connection_name)
        svc._dbusservice.add_path("/DeviceInstance", svc.deviceinstance)
        svc._dbusservice.add_path("/ProductId", 0xFFFF)
        svc._dbusservice.add_path("/ProductName", svc.product_name)
        svc._dbusservice.add_path("/CustomName", svc.custom_name)
        svc._dbusservice.add_path("/FirmwareVersion", svc.firmware_version)
        svc._dbusservice.add_path("/HardwareVersion", svc.hardware_version)
        svc._dbusservice.add_path("/Serial", svc.serial)
        svc._dbusservice.add_path("/Connected", 1)
        svc._dbusservice.add_path("/Position", svc.position)
        svc._dbusservice.add_path("/UpdateIndex", 0)

    def _measurement_paths(self) -> PathMap:
        """Return measurement and energy paths shown on the EV charger tile."""
        return {
            "/Ac/Power": (0.0, self._formatters["w"]),
            "/Ac/Voltage": (0.0, self._formatters["v"]),
            "/Ac/L1/Power": (0.0, self._formatters["w"]),
            "/Ac/L2/Power": (0.0, self._formatters["w"]),
            "/Ac/L3/Power": (0.0, self._formatters["w"]),
            "/Ac/L1/Voltage": (0.0, self._formatters["v"]),
            "/Ac/L2/Voltage": (0.0, self._formatters["v"]),
            "/Ac/L3/Voltage": (0.0, self._formatters["v"]),
            "/Ac/L1/Current": (0.0, self._formatters["a"]),
            "/Ac/L2/Current": (0.0, self._formatters["a"]),
            "/Ac/L3/Current": (0.0, self._formatters["a"]),
            "/Ac/Energy/Forward": (0.0, self._formatters["kwh"]),
            "/Ac/L1/Energy/Forward": (0.0, self._formatters["kwh"]),
            "/Ac/L2/Energy/Forward": (0.0, self._formatters["kwh"]),
            "/Ac/L3/Energy/Forward": (0.0, self._formatters["kwh"]),
            "/Session/Energy": (0.0, None),
            "/Session/Time": (0, None),
            "/Ac/Current": (0.0, self._formatters["a"]),
            "/Current": (0.0, self._formatters["a"]),
        }

    def _control_paths(self) -> PathMap:
        """Return writable and status-like EV charger control paths."""
        svc = self.service
        return {
            "/MinCurrent": (svc.min_current, self._formatters["a"]),
            "/MaxCurrent": (svc.max_current, self._formatters["a"]),
            "/SetCurrent": (svc.virtual_set_current, self._formatters["a"]),
            "/PhaseSelection": (getattr(svc, "requested_phase_selection", "P1"), None),
            "/PhaseSelectionActive": (getattr(svc, "active_phase_selection", "P1"), None),
            "/SupportedPhaseSelections": (
                ",".join(getattr(svc, "supported_phase_selections", ("P1",))),
                None,
            ),
            "/AutoStart": (svc.virtual_autostart, None),
            "/ChargingTime": (0, None),
            "/Mode": (svc.virtual_mode, None),
            "/StartStop": (svc.virtual_startstop, None),
            "/Enable": (svc.virtual_enable, None),
            "/Status": (0, self._formatters["status"]),
        }

    def _diagnostic_paths(self) -> PathMap:
        """Return Auto-diagnostic DBus paths published by the service."""
        svc = self.service
        return {
            "/Auto/Health": (svc._last_health_reason, None),
            "/Auto/HealthCode": (svc._last_health_code, None),
            "/Auto/State": (getattr(svc, "_last_auto_state", "idle"), None),
            "/Auto/StateCode": (getattr(svc, "_last_auto_state_code", 0), None),
            "/Auto/StatusSource": (str(getattr(svc, "_last_status_source", "unknown")), None),
            "/Auto/BackendMode": (str(getattr(svc, "backend_mode", "combined")), None),
            "/Auto/MeterBackend": (str(getattr(svc, "meter_backend_type", "shelly_combined")), None),
            "/Auto/SwitchBackend": (str(getattr(svc, "switch_backend_type", "shelly_combined")), None),
            "/Auto/ChargerBackend": (str(getattr(svc, "charger_backend_type", "") or ""), None),
            "/Auto/ChargerStatus": ("", None),
            "/Auto/ChargerFault": ("", None),
            "/Auto/ChargerFaultActive": (0, None),
            "/Auto/ErrorCount": (0, None),
            "/Auto/DbusReadErrors": (0, None),
            "/Auto/ShellyReadErrors": (0, None),
            "/Auto/ChargerWriteErrors": (0, None),
            "/Auto/PvReadErrors": (0, None),
            "/Auto/BatteryReadErrors": (0, None),
            "/Auto/GridReadErrors": (0, None),
            "/Auto/InputCacheHits": (0, None),
            "/Auto/ChargerCurrentTarget": (-1.0, None),
            "/Auto/LastShellyReadAge": (-1, None),
            "/Auto/LastPvReadAge": (-1, None),
            "/Auto/LastBatteryReadAge": (-1, None),
            "/Auto/LastGridReadAge": (-1, None),
            "/Auto/LastDbusReadAge": (-1, None),
            "/Auto/ChargerCurrentTargetAge": (-1, None),
            "/Auto/LastChargerReadAge": (-1, None),
            "/Auto/LastSuccessfulUpdateAge": (-1, None),
            "/Auto/Stale": (0, None),
            "/Auto/StaleSeconds": (0, None),
            "/Auto/RecoveryAttempts": (0, None),
        }

    def _all_service_paths(self) -> PathMap:
        """Return the complete dynamic EV charger DBus path map."""
        return {
            **self._measurement_paths(),
            **self._control_paths(),
            **self._diagnostic_paths(),
        }
