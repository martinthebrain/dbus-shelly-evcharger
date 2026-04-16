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
import os
import time
from collections import deque
from typing import Any, cast

from shelly_wallbox.backend.shelly_io import ShellyIoController
from shelly_wallbox.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin
from shelly_wallbox.inputs.supervisor import AutoInputSupervisor
from shelly_wallbox.publish.dbus import DbusPublishController
from shelly_wallbox.update.controller import UpdateCycleController
from shelly_wallbox.backend.factory import build_service_backends
from shelly_wallbox.backend.models import normalize_phase_selection_tuple
from shelly_wallbox.controllers.auto import AutoDecisionController
from shelly_wallbox.controllers.state import ServiceStateController
from shelly_wallbox.controllers.write import DbusWriteController
from shelly_wallbox.ports import AutoDecisionPort, UpdateCyclePort, WriteControllerPort
from shelly_wallbox.runtime import RuntimeSupportController
from vedbus import VeDbusService


class _ServiceBootstrapRuntimeMixin(_ComposableControllerMixin):
    @staticmethod
    def _switch_backend_supported_phase_selections(svc: Any) -> tuple[str, ...]:
        """Return normalized supported phase selections declared by the current switch backend."""
        backend = getattr(svc, "_switch_backend", None)
        if backend is None or not hasattr(backend, "capabilities"):
            return ("P1",)
        try:
            capabilities = backend.capabilities()
        except Exception:  # pylint: disable=broad-except
            return ("P1",)
        normalized = normalize_phase_selection_tuple(
            getattr(capabilities, "supported_phase_selections", ("P1",)),
            ("P1",),
        )
        return cast(tuple[str, ...], normalized)

    @staticmethod
    def _charger_backend_supported_phase_selections(svc: Any) -> tuple[str, ...]:
        """Return normalized supported phase selections declared by the current charger backend."""
        backend = getattr(svc, "_charger_backend", None)
        settings = getattr(backend, "settings", None)
        normalized = normalize_phase_selection_tuple(
            getattr(settings, "supported_phase_selections", ("P1",)),
            ("P1",),
        )
        return cast(tuple[str, ...], normalized)

    def initialize_controllers(self) -> None:
        """Create the controller objects used by the service runtime."""
        svc = self.service
        svc._runtime_support_controller = RuntimeSupportController(svc, self._age_seconds, self._health_code)
        svc._runtime_support_controller.initialize_runtime_support()
        svc._auto_controller = AutoDecisionController(
            AutoDecisionPort(svc),
            self._health_code,
            self._mode_uses_auto_logic,
        )
        svc._dbus_publisher = DbusPublishController(svc, self._age_seconds)
        svc._shelly_io_controller = ShellyIoController(svc)
        resolved_backends = build_service_backends(svc)
        svc._backend_selection = resolved_backends.selection
        svc._backend_bundle = resolved_backends
        svc._meter_backend = resolved_backends.meter
        svc._switch_backend = resolved_backends.switch
        svc._charger_backend = resolved_backends.charger
        if not hasattr(svc, "_state_controller") or svc._state_controller is None:
            svc._state_controller = ServiceStateController(svc, self._normalize_mode)
        svc._write_controller = DbusWriteController(WriteControllerPort(svc))
        svc._auto_input_supervisor = AutoInputSupervisor(svc)
        svc._update_controller = UpdateCycleController(
            UpdateCyclePort(svc),
            self._phase_values,
            self._health_code,
        )

    def initialize_virtual_state(self) -> None:
        """Initialize the writable EV charger state exposed on DBus."""
        svc = self.service
        defaults = svc.config["DEFAULT"]
        supported_phase_selections = self._switch_backend_supported_phase_selections(svc)
        if getattr(svc, "_switch_backend", None) is None and getattr(svc, "_charger_backend", None) is not None:
            supported_phase_selections = self._charger_backend_supported_phase_selections(svc)
        svc.manual_override_until = 0.0
        svc.virtual_mode = self._normalize_mode(defaults.get("Mode", 0))
        svc.virtual_autostart = int(defaults.get("AutoStart", 1))
        svc.virtual_startstop = int(defaults.get("StartStop", 1))
        svc.virtual_enable = int(defaults.get("Enable", defaults.get("StartStop", 1)))
        svc.virtual_set_current = float(defaults.get("SetCurrent", svc.max_current))
        svc.charging_started_at = None
        svc.energy_at_start = 0.0
        svc.last_status = 0
        svc.auto_start_condition_since = None
        svc.auto_stop_condition_since = None
        svc.auto_stop_condition_reason = None
        svc.auto_samples = deque()
        svc._auto_high_soc_profile_active = None
        svc._stop_smoothed_surplus_power = None
        svc._stop_smoothed_grid_power = None
        svc.learned_charge_power_watts = None
        svc.learned_charge_power_updated_at = None
        svc.learned_charge_power_state = "unknown"
        svc.learned_charge_power_learning_since = None
        svc.learned_charge_power_sample_count = 0
        svc.learned_charge_power_phase = None
        svc.learned_charge_power_voltage = None
        svc.learned_charge_power_signature_mismatch_sessions = 0
        svc.learned_charge_power_signature_checked_session_started_at = None
        svc.relay_last_changed_at = None
        svc.relay_last_off_at = None
        svc.supported_phase_selections = supported_phase_selections
        svc.requested_phase_selection = supported_phase_selections[0]
        svc.active_phase_selection = supported_phase_selections[0]
        svc._grid_recovery_required = False
        svc._grid_recovery_since = None
        svc._auto_mode_cutover_pending = False
        svc._ignore_min_offtime_once = False

    def restore_runtime_state(self) -> None:
        """Restore RAM-backed state and initialize worker bookkeeping."""
        svc = self.service
        svc._load_runtime_state()
        svc._startup_manual_target = (
            bool(svc.virtual_enable or svc.virtual_startstop)
            if not self._mode_uses_auto_logic(svc.virtual_mode)
            else None
        )
        svc._init_worker_state()

    def initialize_dbus_service(self) -> None:  # pragma: no cover
        """Create the Venus EV charger DBus service shell."""
        svc = self.service
        svc._dbusservice = VeDbusService(f"{svc.service_name}.http_{svc.deviceinstance}", register=False)

    def apply_device_metadata(self) -> None:
        """Fetch Shelly metadata and apply UI-facing identity fields."""
        svc = self.service
        device_info = self.fetch_device_info_with_fallback()
        defaults = svc.config["DEFAULT"]
        svc.product_name = defaults.get("ProductName", "Shelly Wallbox Meter").strip()
        svc.custom_name = svc.custom_name_override or device_info.get("name") or "Shelly Wallbox"
        svc.serial = device_info.get("mac", svc.host.replace(".", ""))
        svc.firmware_version = device_info.get("fw_id", self._read_version("version.txt"))
        svc.hardware_version = device_info.get("model", "Shelly 1PM Gen4")

    def start_runtime_loops(self) -> None:
        """Register DBus paths, start background workers, and arm timers."""
        svc = self.service
        svc._start_io_worker()
        logging.info(
            "Initialized Shelly wallbox service pid=%s runtime_state=%s %s",
            os.getpid(),
            svc.runtime_state_path,
            svc._state_summary(),
        )
        self._gobject.timeout_add(svc.poll_interval_ms, svc._update)
        self._gobject.timeout_add(svc.sign_of_life_minutes * 60 * 1000, svc._sign_of_life)

    def fetch_device_info_with_fallback(self) -> dict[str, Any]:
        """Try to fetch Shelly device info, but start with generic metadata if that fails."""
        svc = self.service
        last_error = None
        attempts = svc.startup_device_info_retries + 1
        for attempt in range(attempts):
            try:
                return cast(dict[str, Any], svc.fetch_rpc("Shelly.GetDeviceInfo"))
            except Exception as error:  # pylint: disable=broad-except
                last_error = error
                if attempt < (attempts - 1) and svc.startup_device_info_retry_seconds > 0:
                    logging.warning(
                        "Shelly.GetDeviceInfo failed during startup (attempt %s/%s): %s",
                        attempt + 1,
                        attempts,
                        error,
                    )
                    time.sleep(svc.startup_device_info_retry_seconds)
        logging.warning(
            "Shelly.GetDeviceInfo unavailable during startup, continuing with generic metadata: %s",
            last_error,
        )
        return {}
