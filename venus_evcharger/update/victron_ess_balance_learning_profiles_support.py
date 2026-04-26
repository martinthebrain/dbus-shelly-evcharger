# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="no-any-return"
# pyright: reportReturnType=false
"""Shared helpers for Victron ESS balance-bias learning profiles."""

from __future__ import annotations

from typing import Any


def _victron_ess_balance_grid_site_regime(grid_interaction_w: float | None) -> str:
    if grid_interaction_w is not None and grid_interaction_w <= -25.0:
        return "export"
    if grid_interaction_w is not None and grid_interaction_w >= 25.0:
        return "import"
    return ""


def _victron_ess_balance_forecast_site_regime(expected_export_w: float, expected_import_w: float) -> str:
    if expected_export_w > max(25.0, expected_import_w):
        return "export"
    if expected_import_w > max(25.0, expected_export_w):
        return "import"
    return ""


def _victron_ess_balance_action_direction_site_regime(action_direction: str) -> str:
    return "export" if action_direction == "more_export" else "import"


def _victron_ess_balance_near_discharge_limit(site_regime: str, combined_discharge_headroom_w: float | None) -> bool:
    return site_regime == "export" and combined_discharge_headroom_w is not None and combined_discharge_headroom_w <= 300.0


def _victron_ess_balance_near_charge_limit(site_regime: str, combined_charge_headroom_w: float | None) -> bool:
    return site_regime == "import" and combined_charge_headroom_w is not None and combined_charge_headroom_w <= 300.0


def _victron_ess_balance_pv_phase(expected_export_w: float, pv_input_power_w: float) -> str:
    return "pv_strong" if max(expected_export_w, pv_input_power_w) >= 1500.0 else "pv_weak"


def _victron_ess_balance_learning_profile_key(
    action_direction: str,
    site_regime: str,
    day_phase: str,
    reserve_phase: str,
    ev_phase: str,
    pv_phase: str,
    battery_limit_phase: str,
) -> str:
    return f"{action_direction}:{site_regime}:{day_phase}:{reserve_phase}:{ev_phase}:{pv_phase}:{battery_limit_phase}"


def _victron_ess_balance_profile_identity(profile_key: str) -> dict[str, str]:
    parts = profile_key.split(":")
    action_direction = ""
    site_regime = ""
    day_phase = ""
    reserve_phase = ""
    ev_phase = "ev_idle"
    pv_phase = "pv_weak"
    battery_limit_phase = "mid_band"
    if len(parts) >= 4:
        action_direction, site_regime, day_phase, reserve_phase = parts[:4]
    elif len(parts) >= 3:
        site_regime, day_phase, reserve_phase = parts[:3]
    elif parts:
        site_regime = parts[0]
    if len(parts) >= 7:
        ev_phase, pv_phase, battery_limit_phase = parts[4:7]
    return {
        "key": profile_key,
        "action_direction": action_direction,
        "site_regime": site_regime,
        "direction": site_regime,
        "day_phase": day_phase,
        "reserve_phase": reserve_phase,
        "ev_phase": ev_phase,
        "pv_phase": pv_phase,
        "battery_limit_phase": battery_limit_phase,
    }


def _victron_ess_balance_profile_counter(profile: dict[str, Any], field: str) -> int:
    return max(0, int(profile.get(field, 0) or 0))


def _victron_ess_balance_profile_scalar_snapshot(
    profile: dict[str, Any],
    scalar_fields: tuple[str, ...],
) -> dict[str, str]:
    return {
        field: str(profile.get(field, "") or "")
        for field in scalar_fields[1:]
    }


def _victron_ess_balance_prefixed_scalar_metrics(
    snapshot: dict[str, Any],
    scalar_fields: tuple[str, ...],
) -> dict[str, str]:
    return {
        f"battery_discharge_balance_victron_bias_learning_profile_{field}": str(snapshot.get(field, "") or "")
        for field in scalar_fields
    }


def _victron_ess_balance_active_profile_fields() -> tuple[tuple[str, str], ...]:
    return (
        ("_victron_ess_balance_active_learning_profile_key", "key"),
        ("_victron_ess_balance_active_learning_profile_action_direction", "action_direction"),
        ("_victron_ess_balance_active_learning_profile_site_regime", "site_regime"),
        ("_victron_ess_balance_active_learning_profile_direction", "direction"),
        ("_victron_ess_balance_active_learning_profile_day_phase", "day_phase"),
        ("_victron_ess_balance_active_learning_profile_reserve_phase", "reserve_phase"),
        ("_victron_ess_balance_active_learning_profile_ev_phase", "ev_phase"),
        ("_victron_ess_balance_active_learning_profile_pv_phase", "pv_phase"),
        ("_victron_ess_balance_active_learning_profile_battery_limit_phase", "battery_limit_phase"),
    )


def _victron_ess_balance_energy_ids(svc: Any) -> list[str]:
    energy_ids: list[str] = []
    for definition in tuple(getattr(svc, "auto_energy_sources", ()) or ()):
        normalized_id = str(getattr(definition, "source_id", "") or "").strip()
        if normalized_id:
            energy_ids.append(normalized_id)
    return energy_ids


def _victron_ess_balance_adaptive_scalar_specs() -> tuple[tuple[str, str, str], ...]:
    return (
        ("auto_apply_generation", "_victron_ess_balance_auto_apply_generation", "int"),
        ("auto_apply_observe_until", "_victron_ess_balance_auto_apply_observe_until", "optional_float"),
        ("auto_apply_last_applied_param", "_victron_ess_balance_auto_apply_last_applied_param", "str"),
        ("auto_apply_last_applied_at", "_victron_ess_balance_auto_apply_last_applied_at", "optional_float"),
        ("oscillation_lockout_until", "_victron_ess_balance_oscillation_lockout_until", "optional_float"),
        ("oscillation_lockout_reason", "_victron_ess_balance_oscillation_lockout_reason", "str"),
        ("last_stable_at", "_victron_ess_balance_last_stable_at", "optional_float"),
        ("last_stable_profile_key", "_victron_ess_balance_last_stable_profile_key", "str"),
        ("auto_apply_suspend_until", "_victron_ess_balance_auto_apply_suspend_until", "optional_float"),
        ("auto_apply_suspend_reason", "_victron_ess_balance_auto_apply_suspend_reason", "str"),
        ("overshoot_cooldown_until", "_victron_ess_balance_overshoot_cooldown_until", "optional_float"),
        ("overshoot_cooldown_reason", "_victron_ess_balance_overshoot_cooldown_reason", "str"),
        ("safe_state_active", "_victron_ess_balance_safe_state_active", "bool"),
        ("safe_state_reason", "_victron_ess_balance_safe_state_reason", "str"),
    )


def _victron_ess_balance_float_attr(svc: Any, attr_name: str) -> float:
    return float(getattr(svc, attr_name, 0.0) or 0.0)
