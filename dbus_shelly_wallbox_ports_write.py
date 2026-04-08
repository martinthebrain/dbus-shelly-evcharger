# SPDX-License-Identifier: GPL-3.0-or-later
"""Small controller ports that decouple controllers from the full service object."""

from __future__ import annotations

from typing import Any, cast


from dbus_shelly_wallbox_contracts import (
    non_negative_int,
    normalized_worker_snapshot,
    normalize_binary_flag,
)
from dbus_shelly_wallbox_ports_base import _BaseServicePort

class WriteControllerPort(_BaseServicePort):
    """Expose only the write-path surface needed by ``DbusWriteController``."""

    _ALLOWED_ATTRS = {
        "virtual_mode",
        "virtual_autostart",
        "virtual_startstop",
        "virtual_enable",
        "virtual_set_current",
        "min_current",
        "max_current",
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
        if not (pm_confirmed and isinstance(pm_status, dict) and "output" in pm_status and captured_at is not None):
            return None
        age_seconds = current_time - float(captured_at)
        if age_seconds < -1.0 or age_seconds > max_age_seconds:
            return None
        return bool(cast(dict[str, Any], pm_status).get("output"))

    def _fresh_last_output(self, current_time: float, max_age_seconds: float) -> bool | None:
        """Return a fresh confirmed relay output from the last remembered Shelly read."""
        last_pm_status = getattr(self._service, "_last_confirmed_pm_status", None)
        last_pm_status_at = getattr(self._service, "_last_confirmed_pm_status_at", None)
        if last_pm_status is None and bool(getattr(self._service, "_last_pm_status_confirmed", False)):
            last_pm_status = getattr(self._service, "_last_pm_status", None)
            last_pm_status_at = getattr(self._service, "_last_pm_status_at", None)
        if not (
            isinstance(last_pm_status, dict)
            and "output" in last_pm_status
            and last_pm_status_at is not None
        ):
            return None
        age_seconds = current_time - float(last_pm_status_at)
        if age_seconds < -1.0 or age_seconds > max_age_seconds:
            return None
        return bool(cast(dict[str, Any], last_pm_status).get("output"))

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

    def normalize_mode(self, value: Any) -> int:
        return int(self._service._normalize_mode(value))

    def mode_uses_auto_logic(self, mode: Any) -> bool:
        return bool(self._service._mode_uses_auto_logic(mode))

    def state_summary(self) -> str:
        return cast(str, self._service._state_summary())

    def save_runtime_state(self) -> Any:
        return self._service._save_runtime_state()
