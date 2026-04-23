# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined"
"""Grid hold/smoothing helpers for the DBus companion bridge."""

from __future__ import annotations

from typing import Any, Mapping, cast


class _EnergyCompanionDbusBridgeGridMixin:
    def _grid_connected(self, snapshot: Mapping[str, Any], now: float) -> int:
        held = self._grid_snapshot_values(snapshot, now)
        return 1 if held["connected"] else 0

    def _grid_power_w(self, snapshot: Mapping[str, Any], now: float) -> float:
        held = self._grid_snapshot_values(snapshot, now)
        return float(held["value"])

    def _grid_snapshot_values(self, snapshot: Mapping[str, Any], now: float) -> dict[str, Any]:
        raw_value, online = self._aggregate_grid_input(snapshot)
        held = self._resolved_grid_value(
            "aggregate-grid",
            raw_value=raw_value,
            online=online,
            now=now,
            hold_seconds=float(getattr(self.service, "companion_grid_hold_seconds", 0.0) or 0.0),
            smoothing_alpha=float(getattr(self.service, "companion_grid_smoothing_alpha", 1.0) or 1.0),
            smoothing_max_jump_watts=float(
                getattr(self.service, "companion_grid_smoothing_max_jump_watts", 0.0) or 0.0
            ),
        )
        return {"connected": bool(held["connected"]), "value": float(held["value"])}

    def _aggregate_grid_input(self, snapshot: Mapping[str, Any]) -> tuple[Any, bool]:
        authoritative_source_id = str(getattr(self.service, "companion_grid_authoritative_source", "")).strip()
        if authoritative_source_id:
            source = self._find_source_snapshot(snapshot, authoritative_source_id)
            if source is None:
                return None, False
            return source.get("grid_interaction_w"), bool(source.get("online", False))
        return (
            snapshot.get("battery_combined_grid_interaction_w"),
            bool(int(snapshot.get("battery_online_source_count", 0) or 0) > 0),
        )

    def _find_source_snapshot(self, snapshot: Mapping[str, Any], source_id: str) -> dict[str, Any] | None:
        for source in self._normalized_source_snapshots(snapshot):
            if str(source.get("source_id", "")).strip() == source_id:
                return cast(dict[str, Any], source)
        return None

    def _grid_source_values(self, source: Mapping[str, Any], now: float) -> dict[str, Any]:
        source_id = str(source.get("source_id", "")).strip() or "source"
        held = self._resolved_grid_value(
            f"source-grid:{source_id}",
            raw_value=source.get("grid_interaction_w"),
            online=bool(source.get("online", False)),
            now=now,
            hold_seconds=float(getattr(self.service, "companion_source_grid_hold_seconds", 0.0) or 0.0),
            smoothing_alpha=float(getattr(self.service, "companion_source_grid_smoothing_alpha", 1.0) or 1.0),
            smoothing_max_jump_watts=float(
                getattr(self.service, "companion_source_grid_smoothing_max_jump_watts", 0.0) or 0.0
            ),
        )
        value = float(held["value"])
        return {
            "/Connected": 1 if held["connected"] else 0,
            "/Ac/Power": value,
            "/Ac/L1/Power": value,
            "/Ac/L2/Power": 0.0,
            "/Ac/L3/Power": 0.0,
        }

    def _resolved_grid_value(
        self,
        state_key: str,
        *,
        raw_value: Any,
        online: bool,
        now: float,
        hold_seconds: float,
        smoothing_alpha: float,
        smoothing_max_jump_watts: float,
    ) -> dict[str, Any]:
        cached = self._grid_hold_state.get(state_key, {})
        numeric_value = float(raw_value) if isinstance(raw_value, (int, float)) else None
        normalized_alpha = min(1.0, max(0.0, float(smoothing_alpha)))
        if numeric_value is not None:
            previous_value = cached.get("value")
            if isinstance(previous_value, (int, float)) and 0.0 < normalized_alpha < 1.0:
                delta_watts = abs(float(numeric_value) - float(previous_value))
                if float(smoothing_max_jump_watts) <= 0.0 or delta_watts <= float(smoothing_max_jump_watts):
                    numeric_value = (normalized_alpha * numeric_value) + (
                        (1.0 - normalized_alpha) * float(previous_value)
                    )
            resolved = {
                "value": float(numeric_value),
                "connected": bool(online),
                "last_good_at": float(now),
            }
            self._grid_hold_state[state_key] = resolved
            return resolved
        last_good_at = cached.get("last_good_at")
        within_hold = (
            isinstance(last_good_at, (int, float))
            and hold_seconds > 0.0
            and float(now) - float(last_good_at) <= float(hold_seconds)
        )
        if within_hold and isinstance(cached.get("value"), (int, float)):
            held_last_good_at = cast(float, last_good_at)
            return {
                "value": float(cached["value"]),
                "connected": True,
                "last_good_at": held_last_good_at,
            }
        self._grid_hold_state.pop(state_key, None)
        return {"value": 0.0, "connected": False, "last_good_at": None}
