# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code=attr-defined
# pyright: reportAttributeAccessIssue=false
"""Cached helper-input resolution for the update cycle."""

from __future__ import annotations

from typing import Any

from venus_evcharger.core.contracts import timestamp_not_future


class _UpdateCycleInputCacheMixin:
    @staticmethod
    def extract_pm_measurements(
        svc: Any,
        pm_status: dict[str, Any],
    ) -> tuple[bool, float, float, float, float]:
        """Extract normalized relay/power/current/energy values from a Shelly status dict."""
        relay_on = bool(pm_status.get("output", False))
        power = svc._safe_float(pm_status.get("apower", 0.0), 0.0)
        voltage = svc._safe_float(pm_status.get("voltage", 0.0), 0.0)
        current = svc._safe_float(pm_status.get("current", 0.0), 0.0)
        energy_forward = svc._safe_float(pm_status.get("aenergy", {}).get("total", 0.0), 0.0) / 1000.0
        return relay_on, power, voltage, current, energy_forward

    @classmethod
    def resolve_cached_input_value(
        cls,
        svc: Any,
        value: Any,
        snapshot_at: float | None,
        last_value_attr: str,
        last_at_attr: str,
        now: float,
        max_age_seconds: float | None = None,
    ) -> tuple[Any, bool]:
        """Use fresh input values immediately and short-lived cached values as fallback."""
        cache_owner = getattr(svc, "_service", svc)
        cache_max_age = float(svc.auto_input_cache_seconds)
        if max_age_seconds is not None:
            cache_max_age = min(cache_max_age, float(max_age_seconds))
        value, snapshot_at = cls._discard_invalid_snapshot_input(
            value,
            snapshot_at,
            now,
            max_age_seconds,
        )
        if value is not None:
            setattr(cache_owner, last_value_attr, value)
            setattr(cache_owner, last_at_attr, now if snapshot_at is None else float(snapshot_at))
            return value, False
        return cls._cached_input_from_service(
            cache_owner,
            last_value_attr,
            last_at_attr,
            now,
            cache_max_age,
        )

    @classmethod
    def _discard_invalid_snapshot_input(
        cls,
        value: Any,
        snapshot_at: float | None,
        now: float,
        max_age_seconds: float | None,
    ) -> tuple[Any, float | None]:
        """Drop future or over-age source values before cache fallback is considered."""
        if value is None or snapshot_at is None:
            return value, snapshot_at
        snapshot_time = float(snapshot_at)
        if cls._snapshot_input_from_future(snapshot_time, now):
            return None, None
        if cls._snapshot_input_too_old(snapshot_time, now, max_age_seconds):
            return None, None
        return value, snapshot_time

    @classmethod
    def _snapshot_input_from_future(cls, snapshot_time: float, now: float) -> bool:
        """Return True when one helper-fed source timestamp lies in the future."""
        return not timestamp_not_future(
            snapshot_time,
            now,
            cls.FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS,
        )

    @staticmethod
    def _snapshot_input_too_old(
        snapshot_time: float,
        now: float,
        max_age_seconds: float | None,
    ) -> bool:
        """Return True when one helper-fed source timestamp exceeds its max age."""
        return max_age_seconds is not None and (float(now) - snapshot_time) > float(max_age_seconds)

    @classmethod
    def _cached_input_from_service(
        cls,
        cache_owner: Any,
        last_value_attr: str,
        last_at_attr: str,
        now: float,
        cache_max_age: float,
    ) -> tuple[Any, bool]:
        """Return a recent cached helper-fed value when direct input is unavailable."""
        last_value = getattr(cache_owner, last_value_attr, None)
        last_at = getattr(cache_owner, last_at_attr, None)
        if (
            last_value is not None
            and last_at is not None
            and last_at <= (float(now) + cls.FUTURE_INPUT_TIMESTAMP_TOLERANCE_SECONDS)
            and (now - last_at) <= cache_max_age
        ):
            return last_value, True
        return None, False

    @staticmethod
    def _auto_input_source_max_age_seconds(svc: Any, poll_interval_attr: str) -> float:
        """Return the maximum tolerated age for one helper-fed source value."""
        poll_interval_seconds = max(0.0, float(getattr(svc, poll_interval_attr, 0.0) or 0.0))
        validation_seconds = max(0.0, float(getattr(svc, "auto_input_validation_poll_seconds", 30.0) or 30.0))
        freshness_limit = validation_seconds if poll_interval_seconds <= 0.0 else min(
            validation_seconds,
            poll_interval_seconds * 2.0,
        )
        return max(1.0, freshness_limit)

    def resolve_auto_inputs(
        self,
        worker_snapshot: dict[str, Any],
        now: float,
        auto_mode_active: bool,
    ) -> tuple[Any, Any, Any]:
        """Resolve Auto inputs from helper snapshots with short cache fallback."""
        svc = self.service
        cache_owner = getattr(svc, "_service", svc)
        if not auto_mode_active:
            svc._auto_cached_inputs_used = False
            return None, None, None
        pv_power, pv_cached = self.resolve_cached_input_value(
            svc,
            worker_snapshot.get("pv_power"),
            worker_snapshot.get("pv_captured_at", worker_snapshot.get("captured_at")),
            "_last_pv_value",
            "_last_pv_at",
            now,
            max_age_seconds=self._auto_input_source_max_age_seconds(
                svc,
                "auto_pv_poll_interval_seconds",
            ),
        )
        grid_power, grid_cached = self.resolve_cached_input_value(
            svc,
            worker_snapshot.get("grid_power"),
            worker_snapshot.get("grid_captured_at", worker_snapshot.get("captured_at")),
            "_last_grid_value",
            "_last_grid_at",
            now,
            max_age_seconds=self._auto_input_source_max_age_seconds(
                svc,
                "auto_grid_poll_interval_seconds",
            ),
        )
        battery_soc, battery_cached = self.resolve_cached_input_value(
            svc,
            worker_snapshot.get("battery_soc"),
            worker_snapshot.get("battery_captured_at", worker_snapshot.get("captured_at")),
            "_last_battery_soc_value",
            "_last_battery_soc_at",
            now,
            max_age_seconds=self._auto_input_source_max_age_seconds(
                svc,
                "auto_battery_poll_interval_seconds",
            ),
        )
        combined_charge_power, _ = self.resolve_cached_input_value(
            svc,
            worker_snapshot.get("battery_combined_charge_power_w"),
            worker_snapshot.get("battery_captured_at", worker_snapshot.get("captured_at")),
            "_last_combined_battery_charge_power_w",
            "_last_combined_battery_charge_power_at",
            now,
            max_age_seconds=self._auto_input_source_max_age_seconds(
                svc,
                "auto_battery_poll_interval_seconds",
            ),
        )
        combined_discharge_power, _ = self.resolve_cached_input_value(
            svc,
            worker_snapshot.get("battery_combined_discharge_power_w"),
            worker_snapshot.get("battery_captured_at", worker_snapshot.get("captured_at")),
            "_last_combined_battery_discharge_power_w",
            "_last_combined_battery_discharge_power_at",
            now,
            max_age_seconds=self._auto_input_source_max_age_seconds(
                svc,
                "auto_battery_poll_interval_seconds",
            ),
        )
        combined_net_power, _ = self.resolve_cached_input_value(
            svc,
            worker_snapshot.get("battery_combined_net_power_w"),
            worker_snapshot.get("battery_captured_at", worker_snapshot.get("captured_at")),
            "_last_combined_battery_net_power_w",
            "_last_combined_battery_net_power_at",
            now,
            max_age_seconds=self._auto_input_source_max_age_seconds(
                svc,
                "auto_battery_poll_interval_seconds",
            ),
        )
        combined_ac_power, _ = self.resolve_cached_input_value(
            svc,
            worker_snapshot.get("battery_combined_ac_power_w"),
            worker_snapshot.get("battery_captured_at", worker_snapshot.get("captured_at")),
            "_last_combined_battery_ac_power_w",
            "_last_combined_battery_ac_power_at",
            now,
            max_age_seconds=self._auto_input_source_max_age_seconds(
                svc,
                "auto_battery_poll_interval_seconds",
            ),
        )
        cache_owner._last_energy_cluster = {
            "battery_combined_soc": worker_snapshot.get("battery_combined_soc"),
            "battery_combined_usable_capacity_wh": worker_snapshot.get("battery_combined_usable_capacity_wh"),
            "battery_combined_charge_power_w": combined_charge_power,
            "battery_combined_discharge_power_w": combined_discharge_power,
            "battery_combined_net_power_w": combined_net_power,
            "battery_combined_ac_power_w": combined_ac_power,
            "battery_combined_pv_input_power_w": worker_snapshot.get("battery_combined_pv_input_power_w"),
            "battery_combined_grid_interaction_w": worker_snapshot.get("battery_combined_grid_interaction_w"),
            "battery_average_confidence": worker_snapshot.get("battery_average_confidence"),
            "battery_source_count": worker_snapshot.get("battery_source_count", 0),
            "battery_online_source_count": worker_snapshot.get("battery_online_source_count", 0),
            "battery_valid_soc_source_count": worker_snapshot.get("battery_valid_soc_source_count", 0),
            "battery_battery_source_count": worker_snapshot.get("battery_battery_source_count", 0),
            "battery_hybrid_inverter_source_count": worker_snapshot.get("battery_hybrid_inverter_source_count", 0),
            "battery_inverter_source_count": worker_snapshot.get("battery_inverter_source_count", 0),
            "battery_sources": list(worker_snapshot.get("battery_sources", []) or []),
        }
        raw_learning_profiles = worker_snapshot.get("battery_learning_profiles", {})
        if isinstance(raw_learning_profiles, dict):
            cache_owner._last_energy_learning_profiles = dict(raw_learning_profiles)
        svc._auto_cached_inputs_used = pv_cached or grid_cached or battery_cached
        if svc._auto_cached_inputs_used:
            svc._error_state["cache_hits"] += 1
        return pv_power, battery_soc, grid_power
