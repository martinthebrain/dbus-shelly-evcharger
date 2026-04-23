# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined"
# pyright: reportAttributeAccessIssue=false
"""Runtime-state restore helpers for the state controller."""

from __future__ import annotations

import logging
from typing import Any, cast

from venus_evcharger.core.contracts import non_negative_float_or_none, non_negative_int
from venus_evcharger.core.shared import write_text_atomically


class _StateRuntimeRestoreMixin:
    def _restore_basic_runtime_state(self, svc: Any, state: dict[str, object]) -> None:
        svc.virtual_mode = self._normalize_mode(state.get("mode", svc.virtual_mode))
        svc.virtual_autostart = self.coerce_runtime_int(state.get("autostart"), svc.virtual_autostart)
        svc.virtual_enable = self.coerce_runtime_int(state.get("enable"), svc.virtual_enable)
        svc.virtual_startstop = self.coerce_runtime_int(state.get("startstop"), svc.virtual_startstop)
        svc.manual_override_until = self.coerce_runtime_float(state.get("manual_override_until"), svc.manual_override_until)
        svc._auto_mode_cutover_pending = bool(
            self.coerce_runtime_int(state.get("auto_mode_cutover_pending"), svc._auto_mode_cutover_pending)
        )
        svc._ignore_min_offtime_once = False

    def _restore_learned_charge_power_state(self, svc: Any, state: dict[str, object], current_time: float) -> None:
        svc.learned_charge_power_watts = non_negative_float_or_none(
            state.get("learned_charge_power_watts", getattr(svc, "learned_charge_power_watts", None))
        )
        svc.learned_charge_power_updated_at = self._coerce_optional_runtime_past_time(
            state.get("learned_charge_power_updated_at", getattr(svc, "learned_charge_power_updated_at", None)),
            current_time,
        )
        svc.learned_charge_power_state = self._normalize_learned_charge_power_state(
            state.get("learned_charge_power_state", getattr(svc, "learned_charge_power_state", "unknown"))
        )
        svc.learned_charge_power_learning_since = self._coerce_optional_runtime_past_time(
            state.get("learned_charge_power_learning_since", getattr(svc, "learned_charge_power_learning_since", None)),
            current_time,
        )
        svc.learned_charge_power_sample_count = non_negative_int(
            state.get("learned_charge_power_sample_count", getattr(svc, "learned_charge_power_sample_count", 0)),
            0,
        )
        svc.learned_charge_power_phase = self._normalize_learned_charge_power_phase(
            state.get("learned_charge_power_phase", getattr(svc, "learned_charge_power_phase", None))
        )
        svc.learned_charge_power_voltage = non_negative_float_or_none(
            state.get("learned_charge_power_voltage", getattr(svc, "learned_charge_power_voltage", None))
        )
        svc.learned_charge_power_signature_mismatch_sessions = non_negative_int(
            state.get(
                "learned_charge_power_signature_mismatch_sessions",
                getattr(svc, "learned_charge_power_signature_mismatch_sessions", 0),
            ),
            0,
        )
        svc.learned_charge_power_signature_checked_session_started_at = self._coerce_optional_runtime_past_time(
            state.get(
                "learned_charge_power_signature_checked_session_started_at",
                getattr(svc, "learned_charge_power_signature_checked_session_started_at", None),
            ),
            current_time,
        )

    def _restore_phase_switch_runtime_state(self, svc: Any, state: dict[str, object], current_time: float) -> None:
        supported_phase_selections = self._normalize_runtime_supported_phase_selections(
            state.get("supported_phase_selections", getattr(svc, "supported_phase_selections", ("P1",)))
        )
        svc.supported_phase_selections = supported_phase_selections
        default_phase_selection = supported_phase_selections[0]
        svc.requested_phase_selection = self._normalize_runtime_phase_selection(
            state.get("requested_phase_selection", getattr(svc, "requested_phase_selection", default_phase_selection)),
            default_phase_selection,
        )
        svc.active_phase_selection = self._normalize_runtime_phase_selection(
            state.get("active_phase_selection", getattr(svc, "active_phase_selection", svc.requested_phase_selection)),
            svc.requested_phase_selection,
        )
        svc._phase_switch_pending_selection = self._normalized_optional_runtime_phase_selection(
            state.get("phase_switch_pending_selection", getattr(svc, "_phase_switch_pending_selection", None)),
            svc.requested_phase_selection,
        )
        svc._phase_switch_state = self._normalize_phase_switch_state(
            state.get("phase_switch_state", getattr(svc, "_phase_switch_state", None))
        )
        svc._phase_switch_requested_at = self._coerce_optional_runtime_past_time(
            state.get("phase_switch_requested_at", getattr(svc, "_phase_switch_requested_at", None)),
            current_time,
        )
        svc._phase_switch_stable_until = self._coerce_optional_runtime_float(
            state.get("phase_switch_stable_until", getattr(svc, "_phase_switch_stable_until", None))
        )
        svc._phase_switch_resume_relay = bool(
            self.coerce_runtime_int(
                state.get("phase_switch_resume_relay", getattr(svc, "_phase_switch_resume_relay", False)),
                1 if bool(getattr(svc, "_phase_switch_resume_relay", False)) else 0,
            )
        )
        svc._phase_switch_mismatch_counts = self._normalized_phase_switch_mismatch_counts(
            state.get("phase_switch_mismatch_counts", getattr(svc, "_phase_switch_mismatch_counts", {})),
            svc.requested_phase_selection,
        )
        svc._phase_switch_last_mismatch_selection = self._normalized_optional_runtime_phase_selection(
            state.get("phase_switch_last_mismatch_selection", getattr(svc, "_phase_switch_last_mismatch_selection", None)),
            svc.requested_phase_selection,
        )
        svc._phase_switch_last_mismatch_at = self._coerce_optional_runtime_past_time(
            state.get("phase_switch_last_mismatch_at", getattr(svc, "_phase_switch_last_mismatch_at", None)),
            current_time,
        )
        svc._phase_switch_lockout_selection = self._normalized_optional_runtime_phase_selection(
            state.get("phase_switch_lockout_selection", getattr(svc, "_phase_switch_lockout_selection", None)),
            svc.requested_phase_selection,
        )
        svc._phase_switch_lockout_reason = str(
            state.get("phase_switch_lockout_reason", getattr(svc, "_phase_switch_lockout_reason", "")) or ""
        )
        svc._phase_switch_lockout_at = self._coerce_optional_runtime_past_time(
            state.get("phase_switch_lockout_at", getattr(svc, "_phase_switch_lockout_at", None)),
            current_time,
        )
        svc._phase_switch_lockout_until = self._coerce_optional_runtime_float(
            state.get("phase_switch_lockout_until", getattr(svc, "_phase_switch_lockout_until", None))
        )
        if svc._phase_switch_state is None or svc._phase_switch_pending_selection is None:
            svc._phase_switch_pending_selection = None
            svc._phase_switch_state = None
            svc._phase_switch_requested_at = None
            svc._phase_switch_stable_until = None
            svc._phase_switch_resume_relay = False

    def _normalized_phase_switch_mismatch_counts(self, raw_counts: object, default_selection: str) -> dict[str, int]:
        normalized_counts: dict[str, int] = {}
        if not isinstance(raw_counts, dict):
            return normalized_counts
        for raw_selection, raw_count in raw_counts.items():
            normalized_selection = self._normalize_runtime_phase_selection(raw_selection, cast(Any, default_selection))
            normalized_counts[normalized_selection] = non_negative_int(raw_count, 0)
        return normalized_counts

    def _restore_relay_runtime_state(self, svc: Any, state: dict[str, object], current_time: float) -> None:
        svc.relay_last_changed_at = self._coerce_optional_runtime_past_time(
            state.get("relay_last_changed_at", svc.relay_last_changed_at),
            current_time,
        )
        svc.relay_last_off_at = self._coerce_optional_runtime_past_time(
            state.get("relay_last_off_at", svc.relay_last_off_at),
            current_time,
        )

    def _restore_contactor_runtime_state(self, svc: Any, state: dict[str, object], current_time: float) -> None:
        svc._contactor_fault_counts = self._normalized_contactor_fault_counts(
            state.get("contactor_fault_counts", getattr(svc, "_contactor_fault_counts", {}))
        )
        svc._contactor_fault_active_reason = self._normalized_contactor_fault_reason(
            state.get("contactor_fault_active_reason", getattr(svc, "_contactor_fault_active_reason", ""))
        )
        svc._contactor_fault_active_since = self._coerce_optional_runtime_past_time(
            state.get("contactor_fault_active_since", getattr(svc, "_contactor_fault_active_since", None)),
            current_time,
        )
        svc._contactor_lockout_reason = (
            self._normalized_contactor_fault_reason(
                state.get("contactor_lockout_reason", getattr(svc, "_contactor_lockout_reason", ""))
            )
            or ""
        )
        svc._contactor_lockout_source = str(
            state.get("contactor_lockout_source", getattr(svc, "_contactor_lockout_source", "")) or ""
        )
        svc._contactor_lockout_at = self._coerce_optional_runtime_past_time(
            state.get("contactor_lockout_at", getattr(svc, "_contactor_lockout_at", None)),
            current_time,
        )

    @staticmethod
    def _victron_ess_balance_runtime_topology_key(svc: Any, source_id: str) -> str:
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

    @classmethod
    def _restore_victron_ess_balance_runtime_state(cls, svc: Any, state: dict[str, object]) -> None:
        raw_learning_state = state.get("victron_ess_balance_learning_state")
        if isinstance(raw_learning_state, dict):
            cls._restore_victron_ess_balance_learning_state_payload(svc, raw_learning_state)
        raw_adaptive_state = state.get("victron_ess_balance_adaptive_tuning_state")
        if isinstance(raw_adaptive_state, dict):
            cls._restore_victron_ess_balance_adaptive_tuning_payload(svc, raw_adaptive_state)

    @classmethod
    def _restore_victron_ess_balance_learning_state_payload(cls, svc: Any, payload: dict[str, object]) -> None:
        schema_version = non_negative_int(payload.get("schema_version"), 0)
        if schema_version not in {1, 2}:
            return
        source_id = str(payload.get("source_id", getattr(svc, "auto_battery_discharge_balance_victron_bias_source_id", "")) or "").strip()
        expected_topology_key = cls._victron_ess_balance_runtime_topology_key(svc, source_id)
        payload_topology_key = str(payload.get("topology_key", "") or "").strip()
        if payload_topology_key not in {expected_topology_key, expected_topology_key.replace("/v2", "/v1")}:
            return
        raw_profiles = payload.get("profiles")
        if not isinstance(raw_profiles, dict):
            return
        normalized_profiles: dict[str, dict[str, object]] = {}
        for raw_key, raw_profile in raw_profiles.items():
            profile_key = str(raw_key or "").strip()
            if not profile_key or not isinstance(raw_profile, dict):
                continue
            normalized_profiles[profile_key] = {
                "key": profile_key,
                "action_direction": str(raw_profile.get("action_direction", "") or ""),
                "site_regime": str(raw_profile.get("site_regime", raw_profile.get("direction", "")) or ""),
                "direction": str(raw_profile.get("direction", raw_profile.get("site_regime", "")) or ""),
                "day_phase": str(raw_profile.get("day_phase", "") or ""),
                "reserve_phase": str(raw_profile.get("reserve_phase", "") or ""),
                "ev_phase": str(raw_profile.get("ev_phase", "ev_idle") or "ev_idle"),
                "pv_phase": str(raw_profile.get("pv_phase", "pv_weak") or "pv_weak"),
                "battery_limit_phase": str(
                    raw_profile.get("battery_limit_phase", "mid_band") or "mid_band"
                ),
                "delay_samples": non_negative_int(raw_profile.get("delay_samples"), 0),
                "gain_samples": non_negative_int(raw_profile.get("gain_samples"), 0),
                "response_delay_seconds": non_negative_float_or_none(
                    raw_profile.get("response_delay_seconds", raw_profile.get("typical_response_delay_seconds"))
                ),
                "estimated_gain": non_negative_float_or_none(
                    raw_profile.get("estimated_gain", raw_profile.get("effective_gain"))
                ),
                "response_delay_mad_seconds": non_negative_float_or_none(raw_profile.get("response_delay_mad_seconds")),
                "gain_mad": non_negative_float_or_none(raw_profile.get("gain_mad")),
                "overshoot_count": non_negative_int(raw_profile.get("overshoot_count"), 0),
                "settled_count": non_negative_int(raw_profile.get("settled_count"), 0),
                "stability_score": non_negative_float_or_none(raw_profile.get("stability_score")),
                "regime_consistency_score": non_negative_float_or_none(
                    raw_profile.get("regime_consistency_score")
                ),
                "response_variance_score": non_negative_float_or_none(
                    raw_profile.get("response_variance_score")
                ),
                "reproducibility_score": non_negative_float_or_none(
                    raw_profile.get("reproducibility_score")
                ),
                "safe_ramp_rate_watts_per_second": non_negative_float_or_none(
                    raw_profile.get("safe_ramp_rate_watts_per_second")
                ),
                "preferred_bias_limit_watts": non_negative_float_or_none(
                    raw_profile.get("preferred_bias_limit_watts")
                ),
            }
        svc._victron_ess_balance_learning_profiles = normalized_profiles

    @classmethod
    def _restore_victron_ess_balance_adaptive_tuning_payload(cls, svc: Any, payload: dict[str, object]) -> None:
        schema_version = non_negative_int(payload.get("schema_version"), 0)
        if schema_version not in {1, 2}:
            return
        source_id = str(payload.get("source_id", getattr(svc, "auto_battery_discharge_balance_victron_bias_source_id", "")) or "").strip()
        expected_topology_key = cls._victron_ess_balance_runtime_topology_key(svc, source_id)
        payload_topology_key = str(payload.get("topology_key", "") or "").strip()
        if payload_topology_key not in {expected_topology_key, expected_topology_key.replace("/v2", "/v1")}:
            return
        svc.auto_battery_discharge_balance_victron_bias_kp = float(
            non_negative_float_or_none(payload.get("kp")) or 0.0
        )
        svc.auto_battery_discharge_balance_victron_bias_ki = float(
            non_negative_float_or_none(payload.get("ki")) or 0.0
        )
        svc.auto_battery_discharge_balance_victron_bias_kd = float(
            non_negative_float_or_none(payload.get("kd")) or 0.0
        )
        svc.auto_battery_discharge_balance_victron_bias_deadband_watts = float(
            non_negative_float_or_none(payload.get("deadband_watts")) or 0.0
        )
        svc.auto_battery_discharge_balance_victron_bias_max_abs_watts = float(
            non_negative_float_or_none(payload.get("max_abs_watts")) or 0.0
        )
        svc.auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second = float(
            non_negative_float_or_none(payload.get("ramp_rate_watts_per_second")) or 0.0
        )
        activation_mode = str(
            payload.get(
                "activation_mode",
                getattr(svc, "auto_battery_discharge_balance_victron_bias_activation_mode", "always"),
            )
            or "always"
        ).strip().lower()
        if activation_mode in {"always", "export_only", "above_reserve_band", "export_and_above_reserve_band"}:
            svc.auto_battery_discharge_balance_victron_bias_activation_mode = activation_mode
        svc._victron_ess_balance_auto_apply_generation = non_negative_int(
            payload.get("auto_apply_generation"),
            getattr(svc, "_victron_ess_balance_auto_apply_generation", 0),
        )
        svc._victron_ess_balance_auto_apply_observe_until = non_negative_float_or_none(
            payload.get("auto_apply_observe_until")
        )
        svc._victron_ess_balance_auto_apply_last_applied_param = str(
            payload.get("auto_apply_last_applied_param", "") or ""
        )
        svc._victron_ess_balance_auto_apply_last_applied_at = non_negative_float_or_none(
            payload.get("auto_apply_last_applied_at")
        )
        svc._victron_ess_balance_oscillation_lockout_until = non_negative_float_or_none(
            payload.get("oscillation_lockout_until")
        )
        svc._victron_ess_balance_oscillation_lockout_reason = str(
            payload.get("oscillation_lockout_reason", "") or ""
        )
        svc._victron_ess_balance_overshoot_cooldown_until = non_negative_float_or_none(
            payload.get("overshoot_cooldown_until")
        )
        svc._victron_ess_balance_overshoot_cooldown_reason = str(
            payload.get("overshoot_cooldown_reason", "") or ""
        )
        raw_last_stable = payload.get("last_stable_tuning")
        svc._victron_ess_balance_last_stable_tuning = dict(raw_last_stable) if isinstance(raw_last_stable, dict) else {}
        svc._victron_ess_balance_last_stable_at = non_negative_float_or_none(payload.get("last_stable_at"))
        svc._victron_ess_balance_last_stable_profile_key = str(
            payload.get("last_stable_profile_key", "") or ""
        )
        raw_conservative = payload.get("conservative_tuning")
        svc._victron_ess_balance_conservative_tuning = (
            dict(raw_conservative) if isinstance(raw_conservative, dict) else {}
        )
        svc._victron_ess_balance_auto_apply_suspend_until = non_negative_float_or_none(
            payload.get("auto_apply_suspend_until")
        )
        svc._victron_ess_balance_auto_apply_suspend_reason = str(
            payload.get("auto_apply_suspend_reason", "") or ""
        )
        svc._victron_ess_balance_safe_state_active = bool(payload.get("safe_state_active"))
        svc._victron_ess_balance_safe_state_reason = str(payload.get("safe_state_reason", "") or "")

    @staticmethod
    def _normalized_contactor_fault_counts(raw_counts: object) -> dict[str, int]:
        allowed_reasons = {"contactor-suspected-open", "contactor-suspected-welded"}
        normalized_counts: dict[str, int] = {}
        if not isinstance(raw_counts, dict):
            return normalized_counts
        for raw_reason, raw_count in raw_counts.items():
            reason = str(raw_reason).strip()
            if reason in allowed_reasons:
                normalized_counts[reason] = non_negative_int(raw_count, 0)
        return normalized_counts

    @staticmethod
    def _normalized_contactor_fault_reason(value: object) -> str | None:
        reason = str(value or "").strip()
        if reason not in {"contactor-suspected-open", "contactor-suspected-welded"}:
            return None
        return reason

    def load_runtime_state(self) -> None:
        svc = self.service
        path = getattr(svc, "runtime_state_path", "").strip()
        if not path:
            return
        state = self._read_runtime_state_payload(path)
        if state is None:
            return
        current_time = self._runtime_load_time(svc)
        self._restore_basic_runtime_state(svc, state)
        self._restore_learned_charge_power_state(svc, state, current_time)
        self._restore_phase_switch_runtime_state(svc, state, current_time)
        self._restore_contactor_runtime_state(svc, state, current_time)
        self._restore_relay_runtime_state(svc, state, current_time)
        self._restore_victron_ess_balance_runtime_state(svc, state)
        svc._runtime_state_serialized = self._serialized_runtime_state()
        logging.info("Restored runtime state from %s: %s", path, self.state_summary())

    def save_runtime_state(self) -> None:
        svc = self.service
        path = getattr(svc, "runtime_state_path", "").strip()
        if not path:
            return
        payload = self._serialized_runtime_state()
        if payload == getattr(svc, "_runtime_state_serialized", None):
            return
        try:
            write_text_atomically(path, payload)
            svc._runtime_state_serialized = payload
            logging.debug("Saved runtime state to %s: %s", path, self.state_summary())
        except Exception as error:  # pylint: disable=broad-except
            logging.warning("Unable to write runtime state to %s: %s", path, error)
