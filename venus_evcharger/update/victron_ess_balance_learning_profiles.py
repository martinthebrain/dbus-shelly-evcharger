# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined,no-any-return"
# pyright: reportAttributeAccessIssue=false, reportReturnType=false
"""Victron ESS balance-bias learning-profile helpers."""

from __future__ import annotations

from typing import Any


class _UpdateCycleVictronEssBalanceLearningProfilesMixin:
    def _victron_ess_balance_learning_profile(
        self,
        svc: Any,
        cluster: dict[str, Any],
        source: dict[str, Any],
        source_error_w: float,
    ) -> dict[str, str]:
        grid_interaction_w = self._optional_float(cluster.get("battery_combined_grid_interaction_w"))
        expected_export_w = self._optional_float(cluster.get("expected_near_term_export_w")) or 0.0
        expected_import_w = self._optional_float(cluster.get("expected_near_term_import_w")) or 0.0
        pv_input_power_w = self._optional_float(cluster.get("battery_combined_pv_input_power_w")) or 0.0
        if float(source_error_w) < 0.0:
            action_direction = "more_export"
        elif float(source_error_w) > 0.0:
            action_direction = "less_export"
        elif expected_export_w > max(25.0, expected_import_w):
            action_direction = "more_export"
        else:
            action_direction = "less_export"
        if grid_interaction_w is not None and grid_interaction_w <= -25.0:
            site_regime = "export"
        elif grid_interaction_w is not None and grid_interaction_w >= 25.0:
            site_regime = "import"
        elif expected_export_w > max(25.0, expected_import_w):
            site_regime = "export"
        elif expected_import_w > max(25.0, expected_export_w):
            site_regime = "import"
        else:
            site_regime = "export" if action_direction == "more_export" else "import"
        day_phase = "day" if max(expected_export_w, pv_input_power_w) >= 50.0 else "night"
        ev_phase = "ev_active" if self._victron_ess_balance_ev_active(svc) else "ev_idle"
        pv_phase = "pv_strong" if max(expected_export_w, pv_input_power_w) >= 1500.0 else "pv_weak"
        source_soc = self._optional_float(source.get("soc"))
        reserve_floor_soc = self._optional_float(source.get("discharge_balance_reserve_floor_soc"))
        reserve_phase = "above_reserve_band"
        if source_soc is not None and reserve_floor_soc is not None and source_soc <= (reserve_floor_soc + 5.0):
            reserve_phase = "reserve_band"
        battery_limit_phase = "mid_band"
        combined_charge_headroom_w = self._optional_float(cluster.get("battery_headroom_charge_w"))
        combined_discharge_headroom_w = self._optional_float(cluster.get("battery_headroom_discharge_w"))
        if site_regime == "export" and combined_discharge_headroom_w is not None and combined_discharge_headroom_w <= 300.0:
            battery_limit_phase = "near_discharge_limit"
        elif site_regime == "import" and combined_charge_headroom_w is not None and combined_charge_headroom_w <= 300.0:
            battery_limit_phase = "near_charge_limit"
        key = (
            f"{action_direction}:{site_regime}:{day_phase}:{reserve_phase}:{ev_phase}:{pv_phase}:{battery_limit_phase}"
        )
        return {
            "key": key,
            "action_direction": action_direction,
            "site_regime": site_regime,
            "direction": site_regime,
            "day_phase": day_phase,
            "reserve_phase": reserve_phase,
            "ev_phase": ev_phase,
            "pv_phase": pv_phase,
            "battery_limit_phase": battery_limit_phase,
        }

    @staticmethod
    def _victron_ess_balance_learning_profiles(svc: Any) -> dict[str, dict[str, Any]]:
        profiles = getattr(svc, "_victron_ess_balance_learning_profiles", None)
        if isinstance(profiles, dict):
            return profiles
        profiles = {}
        svc._victron_ess_balance_learning_profiles = profiles
        return profiles

    def _victron_ess_balance_learning_profile_state(self, svc: Any, profile_key: str) -> dict[str, Any]:
        if not profile_key:
            return {}
        profiles = self._victron_ess_balance_learning_profiles(svc)
        profile = profiles.get(profile_key)
        return profile if isinstance(profile, dict) else {}

    def _ensure_victron_ess_balance_learning_profile_state(
        self,
        svc: Any,
        profile_key: str,
    ) -> dict[str, Any]:
        if not profile_key:
            return {}
        profiles = self._victron_ess_balance_learning_profiles(svc)
        profile = profiles.get(profile_key)
        if isinstance(profile, dict):
            return profile
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
        profile = {
            "key": profile_key,
            "action_direction": action_direction,
            "site_regime": site_regime,
            "direction": site_regime,
            "day_phase": day_phase,
            "reserve_phase": reserve_phase,
            "ev_phase": ev_phase,
            "pv_phase": pv_phase,
            "battery_limit_phase": battery_limit_phase,
            "response_delay_seconds": None,
            "delay_samples": 0,
            "estimated_gain": None,
            "gain_samples": 0,
            "response_delay_mad_seconds": None,
            "gain_mad": None,
            "overshoot_count": 0,
            "settled_count": 0,
            "stability_score": None,
            "regime_consistency_score": None,
            "response_variance_score": None,
            "reproducibility_score": None,
            "safe_ramp_rate_watts_per_second": None,
            "preferred_bias_limit_watts": None,
        }
        profiles[profile_key] = profile
        return profile

    @staticmethod
    def _victron_ess_balance_profile_sample_count(profile: dict[str, Any]) -> int:
        if not profile:
            return 0
        delay_samples = max(0, int(profile.get("delay_samples", 0) or 0))
        gain_samples = max(0, int(profile.get("gain_samples", 0) or 0))
        outcome_samples = max(0, int(profile.get("settled_count", 0) or 0)) + max(
            0,
            int(profile.get("overshoot_count", 0) or 0),
        )
        return max(delay_samples, gain_samples, outcome_samples)

    def _victron_ess_balance_profile_snapshot(self, svc: Any, profile_key: str) -> dict[str, Any]:
        profile = self._victron_ess_balance_learning_profile_state(svc, profile_key)
        if not profile:
            return {}
        return {
            "key": str(profile.get("key", profile_key) or profile_key),
            "action_direction": str(profile.get("action_direction", "") or ""),
            "site_regime": str(profile.get("site_regime", "") or ""),
            "direction": str(profile.get("direction", "") or ""),
            "day_phase": str(profile.get("day_phase", "") or ""),
            "reserve_phase": str(profile.get("reserve_phase", "") or ""),
            "ev_phase": str(profile.get("ev_phase", "") or ""),
            "pv_phase": str(profile.get("pv_phase", "") or ""),
            "battery_limit_phase": str(profile.get("battery_limit_phase", "") or ""),
            "sample_count": self._victron_ess_balance_profile_sample_count(profile),
            "response_delay_seconds": self._optional_float(profile.get("response_delay_seconds")),
            "estimated_gain": self._optional_float(profile.get("estimated_gain")),
            "overshoot_count": max(0, int(profile.get("overshoot_count", 0) or 0)),
            "settled_count": max(0, int(profile.get("settled_count", 0) or 0)),
            "stability_score": self._optional_float(profile.get("stability_score")),
            "regime_consistency_score": self._optional_float(profile.get("regime_consistency_score")),
            "response_variance_score": self._optional_float(profile.get("response_variance_score")),
            "reproducibility_score": self._optional_float(profile.get("reproducibility_score")),
            "typical_response_delay_seconds": self._optional_float(profile.get("response_delay_seconds")),
            "effective_gain": self._optional_float(profile.get("estimated_gain")),
            "safe_ramp_rate_watts_per_second": self._optional_float(profile.get("safe_ramp_rate_watts_per_second")),
            "preferred_bias_limit_watts": self._optional_float(profile.get("preferred_bias_limit_watts")),
        }

    def _merge_victron_ess_balance_learning_profile_metrics(
        self,
        svc: Any,
        metrics: dict[str, Any],
        profile_key: str,
    ) -> None:
        snapshot = self._victron_ess_balance_profile_snapshot(svc, profile_key)
        metrics["battery_discharge_balance_victron_bias_learning_profile_key"] = str(snapshot.get("key", "") or "")
        metrics["battery_discharge_balance_victron_bias_learning_profile_action_direction"] = str(
            snapshot.get("action_direction", "") or ""
        )
        metrics["battery_discharge_balance_victron_bias_learning_profile_site_regime"] = str(
            snapshot.get("site_regime", "") or ""
        )
        metrics["battery_discharge_balance_victron_bias_learning_profile_direction"] = str(
            snapshot.get("direction", "") or ""
        )
        metrics["battery_discharge_balance_victron_bias_learning_profile_day_phase"] = str(
            snapshot.get("day_phase", "") or ""
        )
        metrics["battery_discharge_balance_victron_bias_learning_profile_reserve_phase"] = str(
            snapshot.get("reserve_phase", "") or ""
        )
        metrics["battery_discharge_balance_victron_bias_learning_profile_ev_phase"] = str(
            snapshot.get("ev_phase", "") or ""
        )
        metrics["battery_discharge_balance_victron_bias_learning_profile_pv_phase"] = str(
            snapshot.get("pv_phase", "") or ""
        )
        metrics["battery_discharge_balance_victron_bias_learning_profile_battery_limit_phase"] = str(
            snapshot.get("battery_limit_phase", "") or ""
        )
        metrics["battery_discharge_balance_victron_bias_learning_profile_sample_count"] = int(
            snapshot.get("sample_count", 0) or 0
        )
        metrics["battery_discharge_balance_victron_bias_learning_profile_response_delay_seconds"] = snapshot.get(
            "response_delay_seconds"
        )
        metrics["battery_discharge_balance_victron_bias_learning_profile_estimated_gain"] = snapshot.get(
            "estimated_gain"
        )
        metrics["battery_discharge_balance_victron_bias_learning_profile_overshoot_count"] = int(
            snapshot.get("overshoot_count", 0) or 0
        )
        metrics["battery_discharge_balance_victron_bias_learning_profile_settled_count"] = int(
            snapshot.get("settled_count", 0) or 0
        )
        metrics["battery_discharge_balance_victron_bias_learning_profile_stability_score"] = snapshot.get(
            "stability_score"
        )
        metrics["battery_discharge_balance_victron_bias_learning_profile_regime_consistency_score"] = snapshot.get(
            "regime_consistency_score"
        )
        metrics["battery_discharge_balance_victron_bias_learning_profile_response_variance_score"] = snapshot.get(
            "response_variance_score"
        )
        metrics["battery_discharge_balance_victron_bias_learning_profile_reproducibility_score"] = snapshot.get(
            "reproducibility_score"
        )
        metrics["battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second"] = snapshot.get(
            "safe_ramp_rate_watts_per_second"
        )
        metrics["battery_discharge_balance_victron_bias_learning_profile_preferred_bias_limit_watts"] = snapshot.get(
            "preferred_bias_limit_watts"
        )

    @staticmethod
    def _set_victron_ess_balance_active_profile(svc: Any, learning_profile: dict[str, str]) -> None:
        svc._victron_ess_balance_active_learning_profile_key = str(learning_profile.get("key", "") or "")
        svc._victron_ess_balance_active_learning_profile_action_direction = str(
            learning_profile.get("action_direction", "") or ""
        )
        svc._victron_ess_balance_active_learning_profile_site_regime = str(
            learning_profile.get("site_regime", "") or ""
        )
        svc._victron_ess_balance_active_learning_profile_direction = str(learning_profile.get("direction", "") or "")
        svc._victron_ess_balance_active_learning_profile_day_phase = str(learning_profile.get("day_phase", "") or "")
        svc._victron_ess_balance_active_learning_profile_reserve_phase = str(
            learning_profile.get("reserve_phase", "") or ""
        )
        svc._victron_ess_balance_active_learning_profile_ev_phase = str(learning_profile.get("ev_phase", "") or "")
        svc._victron_ess_balance_active_learning_profile_pv_phase = str(learning_profile.get("pv_phase", "") or "")
        svc._victron_ess_balance_active_learning_profile_battery_limit_phase = str(
            learning_profile.get("battery_limit_phase", "") or ""
        )

    @staticmethod
    def _clear_victron_ess_balance_active_profile(svc: Any) -> None:
        svc._victron_ess_balance_active_learning_profile_key = ""
        svc._victron_ess_balance_active_learning_profile_action_direction = ""
        svc._victron_ess_balance_active_learning_profile_site_regime = ""
        svc._victron_ess_balance_active_learning_profile_direction = ""
        svc._victron_ess_balance_active_learning_profile_day_phase = ""
        svc._victron_ess_balance_active_learning_profile_reserve_phase = ""
        svc._victron_ess_balance_active_learning_profile_ev_phase = ""
        svc._victron_ess_balance_active_learning_profile_pv_phase = ""
        svc._victron_ess_balance_active_learning_profile_battery_limit_phase = ""

    def _victron_ess_balance_update_profile_delay(self, svc: Any, profile_key: str, sample: float) -> None:
        profile = self._ensure_victron_ess_balance_learning_profile_state(svc, profile_key)
        if not profile:
            return
        samples = max(0, int(profile.get("delay_samples", 0) or 0))
        current_delay = self._optional_float(profile.get("response_delay_seconds"))
        if current_delay is not None:
            profile["response_delay_mad_seconds"] = self._ewma_learned_value(
                self._optional_float(profile.get("response_delay_mad_seconds")),
                abs(float(sample) - float(current_delay)),
                samples,
            )
        profile["response_delay_seconds"] = self._ewma_learned_value(current_delay, float(sample), samples)
        profile["delay_samples"] = samples + 1

    def _victron_ess_balance_update_profile_gain(self, svc: Any, profile_key: str, sample: float) -> None:
        profile = self._ensure_victron_ess_balance_learning_profile_state(svc, profile_key)
        if not profile:
            return
        samples = max(0, int(profile.get("gain_samples", 0) or 0))
        current_gain = self._optional_float(profile.get("estimated_gain"))
        if current_gain is not None:
            profile["gain_mad"] = self._ewma_learned_value(
                self._optional_float(profile.get("gain_mad")),
                abs(float(sample) - float(current_gain)),
                samples,
            )
        profile["estimated_gain"] = self._ewma_learned_value(current_gain, float(sample), samples)
        profile["gain_samples"] = samples + 1

    def _victron_ess_balance_increment_profile_counter(self, svc: Any, profile_key: str, field: str) -> None:
        profile = self._ensure_victron_ess_balance_learning_profile_state(svc, profile_key)
        if not profile:
            return
        profile[field] = max(0, int(profile.get(field, 0) or 0)) + 1

    def _victron_ess_balance_refresh_profile_stability(self, svc: Any, profile_key: str) -> None:
        profile = self._victron_ess_balance_learning_profile_state(svc, profile_key)
        if not profile:
            return
        profile["stability_score"] = self._victron_ess_balance_stability_score_values(
            max(0, int(profile.get("settled_count", 0) or 0)),
            max(0, int(profile.get("overshoot_count", 0) or 0)),
            self._optional_float(profile.get("estimated_gain")),
            self._optional_float(profile.get("response_delay_seconds")),
        )
        profile["response_variance_score"] = self._victron_ess_balance_variance_score(
            self._optional_float(profile.get("response_delay_seconds")),
            self._optional_float(profile.get("response_delay_mad_seconds")),
            self._optional_float(profile.get("estimated_gain")),
            self._optional_float(profile.get("gain_mad")),
        )
        profile["regime_consistency_score"] = self._victron_ess_balance_regime_consistency_score(profile)
        profile["reproducibility_score"] = self._victron_ess_balance_reproducibility_score(profile)
        current_ramp = max(
            0.0,
            float(
                getattr(svc, "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second", 0.0) or 0.0
            ),
        )
        current_max_abs = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_victron_bias_max_abs_watts", 0.0) or 0.0),
        )
        stability = self._optional_float(profile.get("stability_score")) or 0.0
        overshoot_count = max(0, int(profile.get("overshoot_count", 0) or 0))
        if overshoot_count > 0 or stability < 0.55:
            profile["safe_ramp_rate_watts_per_second"] = current_ramp * 0.7 if current_ramp > 0.0 else 25.0
            profile["preferred_bias_limit_watts"] = current_max_abs * 0.8 if current_max_abs > 0.0 else 350.0
        elif stability >= 0.8:
            profile["safe_ramp_rate_watts_per_second"] = current_ramp * 1.1 if current_ramp > 0.0 else 60.0
            profile["preferred_bias_limit_watts"] = current_max_abs * 1.1 if current_max_abs > 0.0 else 550.0
        else:
            profile["safe_ramp_rate_watts_per_second"] = current_ramp if current_ramp > 0.0 else 50.0
            profile["preferred_bias_limit_watts"] = current_max_abs if current_max_abs > 0.0 else 500.0

    def _victron_ess_balance_current_topology_key(self, svc: Any, source_id: str) -> str:
        energy_ids: list[str] = []
        for definition in tuple(getattr(svc, "auto_energy_sources", ()) or ()):
            normalized_id = str(getattr(definition, "source_id", "") or "").strip()
            if normalized_id:
                energy_ids.append(normalized_id)
        service_name = str(getattr(svc, "auto_battery_discharge_balance_victron_bias_service", "") or "").strip()
        path = str(getattr(svc, "auto_battery_discharge_balance_victron_bias_path", "") or "").strip()
        return (
            "victron-bias-learning/v2"
            f"/source={str(source_id or '').strip()}"
            f"/service={service_name}"
            f"/path={path}"
            f"/energy={','.join(sorted(energy_ids))}"
        )

    def victron_ess_balance_learning_state_payload(self, svc: Any) -> dict[str, Any]:
        source_id = str(getattr(svc, "auto_battery_discharge_balance_victron_bias_source_id", "") or "").strip()
        topology_key = self._victron_ess_balance_current_topology_key(svc, source_id)
        profiles: dict[str, Any] = {}
        for profile_key in sorted(self._victron_ess_balance_learning_profiles(svc)):
            profiles[profile_key] = self._victron_ess_balance_profile_snapshot(svc, profile_key)
        return {
            "schema_version": 2,
            "topology_key": topology_key,
            "source_id": source_id,
            "profiles": profiles,
        }

    def victron_ess_balance_adaptive_tuning_payload(self, svc: Any) -> dict[str, Any]:
        source_id = str(getattr(svc, "auto_battery_discharge_balance_victron_bias_source_id", "") or "").strip()
        return {
            "schema_version": 2,
            "topology_key": self._victron_ess_balance_current_topology_key(svc, source_id),
            "source_id": source_id,
            **self._victron_ess_balance_current_tuning_snapshot(svc),
            "auto_apply_generation": max(0, int(getattr(svc, "_victron_ess_balance_auto_apply_generation", 0) or 0)),
            "auto_apply_observe_until": self._optional_float(
                getattr(svc, "_victron_ess_balance_auto_apply_observe_until", None)
            ),
            "auto_apply_last_applied_param": str(
                getattr(svc, "_victron_ess_balance_auto_apply_last_applied_param", "") or ""
            ),
            "auto_apply_last_applied_at": self._optional_float(
                getattr(svc, "_victron_ess_balance_auto_apply_last_applied_at", None)
            ),
            "oscillation_lockout_until": self._optional_float(
                getattr(svc, "_victron_ess_balance_oscillation_lockout_until", None)
            ),
            "oscillation_lockout_reason": str(
                getattr(svc, "_victron_ess_balance_oscillation_lockout_reason", "") or ""
            ),
            "last_stable_tuning": dict(getattr(svc, "_victron_ess_balance_last_stable_tuning", {}) or {}),
            "last_stable_at": self._optional_float(getattr(svc, "_victron_ess_balance_last_stable_at", None)),
            "last_stable_profile_key": str(getattr(svc, "_victron_ess_balance_last_stable_profile_key", "") or ""),
            "conservative_tuning": dict(getattr(svc, "_victron_ess_balance_conservative_tuning", {}) or {}),
            "auto_apply_suspend_until": self._optional_float(
                getattr(svc, "_victron_ess_balance_auto_apply_suspend_until", None)
            ),
            "auto_apply_suspend_reason": str(
                getattr(svc, "_victron_ess_balance_auto_apply_suspend_reason", "") or ""
            ),
            "overshoot_cooldown_until": self._optional_float(
                getattr(svc, "_victron_ess_balance_overshoot_cooldown_until", None)
            ),
            "overshoot_cooldown_reason": str(
                getattr(svc, "_victron_ess_balance_overshoot_cooldown_reason", "") or ""
            ),
            "safe_state_active": bool(getattr(svc, "_victron_ess_balance_safe_state_active", False)),
            "safe_state_reason": str(getattr(svc, "_victron_ess_balance_safe_state_reason", "") or ""),
        }

    def _victron_ess_balance_current_tuning_snapshot(self, svc: Any) -> dict[str, Any]:
        return {
            "kp": float(getattr(svc, "auto_battery_discharge_balance_victron_bias_kp", 0.0) or 0.0),
            "ki": float(getattr(svc, "auto_battery_discharge_balance_victron_bias_ki", 0.0) or 0.0),
            "kd": float(getattr(svc, "auto_battery_discharge_balance_victron_bias_kd", 0.0) or 0.0),
            "deadband_watts": float(
                getattr(svc, "auto_battery_discharge_balance_victron_bias_deadband_watts", 0.0) or 0.0
            ),
            "max_abs_watts": float(
                getattr(svc, "auto_battery_discharge_balance_victron_bias_max_abs_watts", 0.0) or 0.0
            ),
            "ramp_rate_watts_per_second": float(
                getattr(svc, "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second", 0.0) or 0.0
            ),
            "activation_mode": self._victron_ess_balance_activation_mode(svc),
        }
