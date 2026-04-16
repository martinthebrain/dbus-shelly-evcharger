# SPDX-License-Identifier: GPL-3.0-or-later
"""Small controller ports that decouple controllers from the full service object."""

from __future__ import annotations

import time
from typing import Any, cast


from shelly_wallbox.backend.models import (
    effective_supported_phase_selections,
    PhaseSelection,
    normalize_phase_selection,
    normalize_phase_selection_tuple,
)
from shelly_wallbox.core.contracts import (
    finite_float_or_none,
    non_negative_int,
    normalized_worker_snapshot,
    normalize_binary_flag,
)
from .base import _BaseServicePort

class WriteControllerPort(_BaseServicePort):
    """Expose only the write-path surface needed by ``DbusWriteController``."""

    _ALLOWED_ATTRS = {
        "virtual_mode",
        "virtual_autostart",
        "virtual_startstop",
        "virtual_enable",
        "virtual_set_current",
        "requested_phase_selection",
        "active_phase_selection",
        "supported_phase_selections",
        "min_current",
        "max_current",
        "auto_start_surplus_watts",
        "auto_stop_surplus_watts",
        "auto_min_soc",
        "auto_resume_soc",
        "auto_start_delay_seconds",
        "auto_stop_delay_seconds",
        "auto_start_condition_since",
        "auto_stop_condition_since",
        "manual_override_until",
    }
    _MUTABLE_ATTRS = _ALLOWED_ATTRS

    def __init__(self, service: Any) -> None:
        super().__init__(service)

    @property
    def virtual_mode(self) -> int:
        return non_negative_int(getattr(self._service, "virtual_mode", 0))

    @virtual_mode.setter
    def virtual_mode(self, value: Any) -> None:
        normalize_mode = getattr(self._service, "_normalize_mode", None)
        self._service.virtual_mode = (
            normalize_mode(value) if callable(normalize_mode) else non_negative_int(value)
        )

    @property
    def virtual_autostart(self) -> int:
        return normalize_binary_flag(getattr(self._service, "virtual_autostart", 1), default=1)

    @virtual_autostart.setter
    def virtual_autostart(self, value: Any) -> None:
        self._service.virtual_autostart = normalize_binary_flag(value)

    @property
    def virtual_startstop(self) -> int:
        return normalize_binary_flag(getattr(self._service, "virtual_startstop", 1), default=1)

    @virtual_startstop.setter
    def virtual_startstop(self, value: Any) -> None:
        self._service.virtual_startstop = normalize_binary_flag(value)

    @property
    def virtual_enable(self) -> int:
        return normalize_binary_flag(getattr(self._service, "virtual_enable", 1), default=1)

    @virtual_enable.setter
    def virtual_enable(self, value: Any) -> None:
        self._service.virtual_enable = normalize_binary_flag(value)

    @property
    def auto_manual_override_seconds(self) -> Any:
        return self._service.auto_manual_override_seconds

    @property
    def virtual_set_current(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "virtual_set_current", 0.0)) or 0.0)

    @virtual_set_current.setter
    def virtual_set_current(self, value: Any) -> None:
        self._service.virtual_set_current = float(finite_float_or_none(value) or 0.0)

    @property
    def min_current(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "min_current", 0.0)) or 0.0)

    @min_current.setter
    def min_current(self, value: Any) -> None:
        self._service.min_current = float(finite_float_or_none(value) or 0.0)

    @property
    def max_current(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "max_current", 0.0)) or 0.0)

    @max_current.setter
    def max_current(self, value: Any) -> None:
        self._service.max_current = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_start_surplus_watts(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_start_surplus_watts", 0.0)) or 0.0)

    @auto_start_surplus_watts.setter
    def auto_start_surplus_watts(self, value: Any) -> None:
        self._service.auto_start_surplus_watts = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_stop_surplus_watts(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_stop_surplus_watts", 0.0)) or 0.0)

    @auto_stop_surplus_watts.setter
    def auto_stop_surplus_watts(self, value: Any) -> None:
        self._service.auto_stop_surplus_watts = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_min_soc(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_min_soc", 0.0)) or 0.0)

    @auto_min_soc.setter
    def auto_min_soc(self, value: Any) -> None:
        self._service.auto_min_soc = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_resume_soc(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_resume_soc", 0.0)) or 0.0)

    @auto_resume_soc.setter
    def auto_resume_soc(self, value: Any) -> None:
        self._service.auto_resume_soc = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_start_delay_seconds(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_start_delay_seconds", 0.0)) or 0.0)

    @auto_start_delay_seconds.setter
    def auto_start_delay_seconds(self, value: Any) -> None:
        self._service.auto_start_delay_seconds = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_stop_delay_seconds(self) -> float:
        return float(finite_float_or_none(getattr(self._service, "auto_stop_delay_seconds", 0.0)) or 0.0)

    @auto_stop_delay_seconds.setter
    def auto_stop_delay_seconds(self, value: Any) -> None:
        self._service.auto_stop_delay_seconds = float(finite_float_or_none(value) or 0.0)

    @property
    def auto_phase_switching_enabled(self) -> int:
        return normalize_binary_flag(getattr(self._service, "auto_phase_switching_enabled", 1), default=1)

    @auto_phase_switching_enabled.setter
    def auto_phase_switching_enabled(self, value: Any) -> None:
        self._service.auto_phase_switching_enabled = bool(normalize_binary_flag(value))

    @property
    def supported_phase_selections(self) -> tuple[str, ...]:
        normalized: tuple[PhaseSelection, ...] = normalize_phase_selection_tuple(
            getattr(self._service, "supported_phase_selections", ("P1",)),
            ("P1",),
        )
        current_time = self.time_now() if callable(getattr(self._service, "_time_now", None)) else time.time()
        return effective_supported_phase_selections(
            normalized,
            lockout_selection=getattr(self._service, "_phase_switch_lockout_selection", None),
            lockout_until=getattr(self._service, "_phase_switch_lockout_until", None),
            now=current_time,
        )

    @supported_phase_selections.setter
    def supported_phase_selections(self, value: Any) -> None:
        self._service.supported_phase_selections = normalize_phase_selection_tuple(value, ("P1",))

    @property
    def requested_phase_selection(self) -> str:
        normalized: PhaseSelection = normalize_phase_selection(
            getattr(self._service, "requested_phase_selection", self.supported_phase_selections[0]),
            cast(PhaseSelection, self.supported_phase_selections[0]),
        )
        return str(normalized)

    @requested_phase_selection.setter
    def requested_phase_selection(self, value: Any) -> None:
        fallback = cast(PhaseSelection, self.supported_phase_selections[0])
        self._service.requested_phase_selection = normalize_phase_selection(value, fallback)

    @property
    def active_phase_selection(self) -> str:
        normalized: PhaseSelection = normalize_phase_selection(
            getattr(self._service, "active_phase_selection", self.requested_phase_selection),
            cast(PhaseSelection, self.requested_phase_selection),
        )
        return str(normalized)

    @active_phase_selection.setter
    def active_phase_selection(self, value: Any) -> None:
        fallback = cast(PhaseSelection, self.requested_phase_selection)
        self._service.active_phase_selection = normalize_phase_selection(value, fallback)

    @property
    def auto_mode_cutover_pending(self) -> bool:
        return bool(getattr(self._service, "_auto_mode_cutover_pending", False))

    @auto_mode_cutover_pending.setter
    def auto_mode_cutover_pending(self, value: Any) -> None:
        self._service._auto_mode_cutover_pending = bool(value)

    @property
    def ignore_min_offtime_once(self) -> bool:
        return bool(getattr(self._service, "_ignore_min_offtime_once", False))

    @ignore_min_offtime_once.setter
    def ignore_min_offtime_once(self, value: Any) -> None:
        self._service._ignore_min_offtime_once = bool(value)

    def clear_auto_samples(self) -> Any:
        return self._service._clear_auto_samples()

    def queue_relay_command(self, relay_on: bool, current_time: float) -> Any:
        return self._service._queue_relay_command(relay_on, current_time)

    def publish_local_pm_status(self, relay_on: bool, current_time: float) -> Any:
        return self._service._publish_local_pm_status(relay_on, current_time)

    def get_worker_snapshot(self) -> Any:
        return self._service._get_worker_snapshot()

    def _relay_status_freshness_seconds(self) -> float:
        """Return how old a confirmed relay sample may be for cutover decisions."""
        candidates = [2.0]
        worker_poll_seconds = getattr(self._service, "_worker_poll_interval_seconds", None)
        if worker_poll_seconds is not None and float(worker_poll_seconds) > 0:
            candidates.append(float(worker_poll_seconds) * 2.0)
        relay_sync_timeout_seconds = getattr(self._service, "relay_sync_timeout_seconds", None)
        if relay_sync_timeout_seconds is not None and float(relay_sync_timeout_seconds) > 0:
            candidates.append(float(relay_sync_timeout_seconds))
        return max(1.0, min(candidates))

    @staticmethod
    def _fresh_snapshot_output(snapshot: Any, current_time: float, max_age_seconds: float) -> bool | None:
        """Return a fresh confirmed relay output directly from the worker snapshot."""
        normalized_snapshot = normalized_worker_snapshot(snapshot, now=current_time, clamp_future_timestamps=False)
        pm_status = normalized_snapshot.get("pm_status")
        pm_confirmed = bool(normalized_snapshot.get("pm_confirmed", False))
        captured_at = normalized_snapshot.get("pm_captured_at", normalized_snapshot.get("captured_at"))
        if not WriteControllerPort._relay_output_payload_present(pm_confirmed, pm_status, captured_at):
            return None
        captured_at_value = WriteControllerPort._relay_output_timestamp(captured_at)
        if captured_at_value is None:
            return None
        if not WriteControllerPort._relay_output_timestamp_fresh(current_time, captured_at_value, max_age_seconds):
            return None
        return bool(cast(dict[str, Any], pm_status).get("output"))

    def _fresh_last_output(self, current_time: float, max_age_seconds: float) -> bool | None:
        """Return a fresh confirmed relay output from the last remembered Shelly read."""
        last_pm_status, last_pm_status_at = self._last_relay_output_sample()
        if not self._relay_output_payload_present(True, last_pm_status, last_pm_status_at):
            return None
        last_pm_status_at_value = self._relay_output_timestamp(last_pm_status_at)
        if last_pm_status_at_value is None:
            return None
        if not self._relay_output_timestamp_fresh(current_time, last_pm_status_at_value, max_age_seconds):
            return None
        return bool(cast(dict[str, Any], last_pm_status).get("output"))

    def _last_relay_output_sample(self) -> tuple[Any, Any]:
        """Return the best remembered relay-output sample for cutover freshness checks."""
        last_pm_status = getattr(self._service, "_last_confirmed_pm_status", None)
        last_pm_status_at = getattr(self._service, "_last_confirmed_pm_status_at", None)
        if last_pm_status is not None:
            return last_pm_status, last_pm_status_at
        if bool(getattr(self._service, "_last_pm_status_confirmed", False)):
            return getattr(self._service, "_last_pm_status", None), getattr(self._service, "_last_pm_status_at", None)
        return None, None

    @staticmethod
    def _relay_output_payload_present(
        confirmed: bool,
        pm_status: Any,
        captured_at: Any,
    ) -> bool:
        """Return whether a relay-output payload has the required shape for freshness checks."""
        return bool(
            confirmed
            and isinstance(pm_status, dict)
            and "output" in pm_status
            and captured_at is not None
        )

    @staticmethod
    def _relay_output_timestamp(captured_at: Any) -> float | None:
        """Return a numeric relay-output timestamp when one is present and valid."""
        if not isinstance(captured_at, (int, float)) or isinstance(captured_at, bool):
            return None
        return float(captured_at)

    @staticmethod
    def _relay_output_timestamp_fresh(current_time: float, captured_at: float, max_age_seconds: float) -> bool:
        """Return whether a relay-output timestamp is fresh enough for cutover logic."""
        age_seconds = current_time - float(captured_at)
        return -1.0 <= age_seconds <= max_age_seconds

    def _fresh_confirmed_relay_output(self, snapshot: Any) -> bool | None:
        """Return the latest confirmed relay output only when it is still fresh."""
        current_time = self.time_now()
        max_age_seconds = self._relay_status_freshness_seconds()
        output = self._fresh_snapshot_output(snapshot, current_time, max_age_seconds)
        if output is not None:
            return output
        return self._fresh_last_output(current_time, max_age_seconds)

    def relay_may_be_on_for_cutover(self) -> bool:
        snapshot = self.get_worker_snapshot()
        peek_pending = getattr(self._service, "_peek_pending_relay_command", None)
        if callable(peek_pending):
            pending_state, _ = cast(tuple[Any, Any], peek_pending())
            if bool(pending_state):
                return True

        confirmed_output = self._fresh_confirmed_relay_output(snapshot)
        if confirmed_output is not None:
            return bool(confirmed_output)
        # Without a fresh confirmed relay sample, cutover must stay conservative.
        # A virtual/manual display state alone is not enough to prove the Shelly
        # relay is already off after startup or an external relay change.
        return True

    def update_worker_snapshot(self, **kwargs: Any) -> Any:
        return self._service._update_worker_snapshot(**kwargs)

    def publish_dbus_path(self, path: str, value: Any, current_time: float, force: bool = False) -> Any:
        return self._service._publish_dbus_path(path, value, current_time, force=force)

    def time_now(self) -> float:
        return float(self._service._time_now())

    def charger_enable_available(self) -> bool:
        backend = getattr(self._service, "_charger_backend", None)
        return backend is not None and hasattr(backend, "set_enabled")

    def charger_current_available(self) -> bool:
        backend = getattr(self._service, "_charger_backend", None)
        return backend is not None and hasattr(backend, "set_current")

    def charger_set_enabled(self, enabled: bool) -> Any:
        backend = getattr(self._service, "_charger_backend", None)
        if backend is None or not hasattr(backend, "set_enabled"):
            raise RuntimeError("No charger backend with set_enabled configured")
        return backend.set_enabled(bool(enabled))

    def charger_set_current(self, amps: float) -> Any:
        backend = getattr(self._service, "_charger_backend", None)
        if backend is None or not hasattr(backend, "set_current"):
            raise RuntimeError("No charger backend with set_current configured")
        return backend.set_current(float(amps))

    def phase_selection_requires_pause(self) -> bool:
        return bool(self._service._phase_selection_requires_pause())

    def apply_phase_selection(self, selection: Any) -> str:
        return cast(str, self._service._apply_phase_selection(selection))

    def normalize_phase_selection(self, value: Any, default: str | None = None) -> str:
        fallback: PhaseSelection = (
            cast(PhaseSelection, self.supported_phase_selections[0])
            if default is None
            else cast(PhaseSelection, str(default))
        )
        normalized: PhaseSelection = normalize_phase_selection(value, fallback)
        return str(normalized)

    def normalize_mode(self, value: Any) -> int:
        return int(self._service._normalize_mode(value))

    def mode_uses_auto_logic(self, mode: Any) -> bool:
        return bool(self._service._mode_uses_auto_logic(mode))

    def state_summary(self) -> str:
        return cast(str, self._service._state_summary())

    def save_runtime_state(self) -> Any:
        return self._service._save_runtime_state()

    def save_runtime_overrides(self) -> Any:
        save_overrides = getattr(self._service, "_save_runtime_overrides", None)
        if callable(save_overrides):
            return save_overrides()
        return None

    def validate_runtime_config(self) -> Any:
        validate_runtime = getattr(self._service, "_validate_runtime_config", None)
        if callable(validate_runtime):
            return validate_runtime()
        return None
