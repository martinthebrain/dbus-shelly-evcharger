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
    @classmethod
    def _valid_victron_ess_balance_schema_version(cls, payload: dict[str, object]) -> bool:
        return non_negative_int(payload.get("schema_version"), 0) in {1, 2}

    @classmethod
    def _victron_ess_balance_payload_matches_topology(
        cls,
        svc: Any,
        payload: dict[str, object],
    ) -> bool:
        source_id = str(
            payload.get(
                "source_id",
                getattr(svc, "auto_battery_discharge_balance_victron_bias_source_id", ""),
            )
            or ""
        ).strip()
        expected_topology_key = cls._victron_ess_balance_runtime_topology_key(svc, source_id)
        payload_topology_key = str(payload.get("topology_key", "") or "").strip()
        return payload_topology_key in {expected_topology_key, expected_topology_key.replace("/v2", "/v1")}

    @staticmethod
    def _normalized_victron_ess_balance_learning_text(
        raw_profile: dict[str, object],
        key: str,
        fallback_key: str | None = None,
    ) -> str:
        if fallback_key is None:
            return str(raw_profile.get(key, "") or "")
        return str(raw_profile.get(key, raw_profile.get(fallback_key, "")) or "")

    @staticmethod
    def _normalized_victron_ess_balance_learning_metric(
        raw_profile: dict[str, object],
        key: str,
        fallback_key: str | None = None,
    ) -> float | None:
        if fallback_key is None:
            return non_negative_float_or_none(raw_profile.get(key))
        return non_negative_float_or_none(raw_profile.get(key, raw_profile.get(fallback_key)))

    @staticmethod
    def _normalized_victron_ess_balance_learning_phase(
        raw_profile: dict[str, object],
        key: str,
        fallback: str,
    ) -> str:
        return str(raw_profile.get(key, fallback) or fallback)

    @staticmethod
    def _normalized_victron_ess_balance_learning_profile(
        profile_key: str,
        raw_profile: dict[str, object],
    ) -> dict[str, object]:
        return {
            "key": profile_key,
            "action_direction": _StateRuntimeRestoreMixin._normalized_victron_ess_balance_learning_text(
                raw_profile,
                "action_direction",
            ),
            "site_regime": _StateRuntimeRestoreMixin._normalized_victron_ess_balance_learning_text(
                raw_profile,
                "site_regime",
                "direction",
            ),
            "direction": _StateRuntimeRestoreMixin._normalized_victron_ess_balance_learning_text(
                raw_profile,
                "direction",
                "site_regime",
            ),
            "day_phase": _StateRuntimeRestoreMixin._normalized_victron_ess_balance_learning_text(
                raw_profile,
                "day_phase",
            ),
            "reserve_phase": _StateRuntimeRestoreMixin._normalized_victron_ess_balance_learning_text(
                raw_profile,
                "reserve_phase",
            ),
            "ev_phase": _StateRuntimeRestoreMixin._normalized_victron_ess_balance_learning_phase(
                raw_profile,
                "ev_phase",
                "ev_idle",
            ),
            "pv_phase": _StateRuntimeRestoreMixin._normalized_victron_ess_balance_learning_phase(
                raw_profile,
                "pv_phase",
                "pv_weak",
            ),
            "battery_limit_phase": _StateRuntimeRestoreMixin._normalized_victron_ess_balance_learning_phase(
                raw_profile,
                "battery_limit_phase",
                "mid_band",
            ),
            "delay_samples": non_negative_int(raw_profile.get("delay_samples"), 0),
            "gain_samples": non_negative_int(raw_profile.get("gain_samples"), 0),
            "response_delay_seconds": _StateRuntimeRestoreMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "response_delay_seconds",
                "typical_response_delay_seconds",
            ),
            "estimated_gain": _StateRuntimeRestoreMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "estimated_gain",
                "effective_gain",
            ),
            "response_delay_mad_seconds": _StateRuntimeRestoreMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "response_delay_mad_seconds",
            ),
            "gain_mad": _StateRuntimeRestoreMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "gain_mad",
            ),
            "overshoot_count": non_negative_int(raw_profile.get("overshoot_count"), 0),
            "settled_count": non_negative_int(raw_profile.get("settled_count"), 0),
            "stability_score": _StateRuntimeRestoreMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "stability_score",
            ),
            "regime_consistency_score": _StateRuntimeRestoreMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "regime_consistency_score",
            ),
            "response_variance_score": _StateRuntimeRestoreMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "response_variance_score",
            ),
            "reproducibility_score": _StateRuntimeRestoreMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "reproducibility_score",
            ),
            "safe_ramp_rate_watts_per_second": _StateRuntimeRestoreMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "safe_ramp_rate_watts_per_second",
            ),
            "preferred_bias_limit_watts": _StateRuntimeRestoreMixin._normalized_victron_ess_balance_learning_metric(
                raw_profile,
                "preferred_bias_limit_watts",
            ),
        }

    @staticmethod
    def _restore_victron_ess_balance_pid_value(
        svc: Any,
        payload: dict[str, object],
        payload_key: str,
        attr_name: str,
    ) -> None:
        setattr(
            svc,
            attr_name,
            float(non_negative_float_or_none(payload.get(payload_key)) or 0.0),
        )

    @staticmethod
    def _victron_ess_balance_activation_mode(payload: dict[str, object], svc: Any) -> str | None:
        activation_mode = str(
            payload.get(
                "activation_mode",
                getattr(svc, "auto_battery_discharge_balance_victron_bias_activation_mode", "always"),
            )
            or "always"
        ).strip().lower()
        if activation_mode in {"always", "export_only", "above_reserve_band", "export_and_above_reserve_band"}:
            return activation_mode
        return None

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
        energy_ids = _victron_ess_balance_energy_ids(svc)
        service_name = _victron_ess_balance_runtime_string(
            svc, "auto_battery_discharge_balance_victron_bias_service"
        )
        path = _victron_ess_balance_runtime_string(
            svc, "auto_battery_discharge_balance_victron_bias_path"
        )
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
    def _normalized_victron_ess_balance_learning_profiles(
        cls,
        raw_profiles: dict[str, object],
    ) -> dict[str, dict[str, object]]:
        normalized_profiles: dict[str, dict[str, object]] = {}
        for raw_key, raw_profile in raw_profiles.items():
            profile_key = str(raw_key or "").strip()
            if not profile_key or not isinstance(raw_profile, dict):
                continue
            normalized_profiles[profile_key] = cls._normalized_victron_ess_balance_learning_profile(
                profile_key,
                raw_profile,
            )
        return normalized_profiles

    @classmethod
    def _restore_victron_ess_balance_learning_state_payload(cls, svc: Any, payload: dict[str, object]) -> None:
        if not cls._valid_victron_ess_balance_schema_version(payload):
            return
        if not cls._victron_ess_balance_payload_matches_topology(svc, payload):
            return
        raw_profiles = payload.get("profiles")
        if not isinstance(raw_profiles, dict):
            return
        svc._victron_ess_balance_learning_profiles = cls._normalized_victron_ess_balance_learning_profiles(
            raw_profiles
        )

    @staticmethod
    def _restore_victron_ess_balance_pid_tuning(svc: Any, payload: dict[str, object]) -> None:
        _StateRuntimeRestoreMixin._restore_victron_ess_balance_pid_value(
            svc,
            payload,
            "kp",
            "auto_battery_discharge_balance_victron_bias_kp",
        )
        _StateRuntimeRestoreMixin._restore_victron_ess_balance_pid_value(
            svc,
            payload,
            "ki",
            "auto_battery_discharge_balance_victron_bias_ki",
        )
        _StateRuntimeRestoreMixin._restore_victron_ess_balance_pid_value(
            svc,
            payload,
            "kd",
            "auto_battery_discharge_balance_victron_bias_kd",
        )
        _StateRuntimeRestoreMixin._restore_victron_ess_balance_pid_value(
            svc,
            payload,
            "deadband_watts",
            "auto_battery_discharge_balance_victron_bias_deadband_watts",
        )
        _StateRuntimeRestoreMixin._restore_victron_ess_balance_pid_value(
            svc,
            payload,
            "max_abs_watts",
            "auto_battery_discharge_balance_victron_bias_max_abs_watts",
        )
        _StateRuntimeRestoreMixin._restore_victron_ess_balance_pid_value(
            svc,
            payload,
            "ramp_rate_watts_per_second",
            "auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second",
        )

    @staticmethod
    def _normalized_victron_ess_balance_tuning_mapping(value: object) -> dict[str, object]:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _restore_victron_ess_balance_suspend_state(svc: Any, payload: dict[str, object]) -> None:
        svc._victron_ess_balance_auto_apply_suspend_until = non_negative_float_or_none(
            payload.get("auto_apply_suspend_until")
        )
        svc._victron_ess_balance_auto_apply_suspend_reason = str(
            payload.get("auto_apply_suspend_reason", "") or ""
        )
        svc._victron_ess_balance_safe_state_active = bool(payload.get("safe_state_active"))
        svc._victron_ess_balance_safe_state_reason = str(payload.get("safe_state_reason", "") or "")

    @staticmethod
    def _restore_victron_ess_balance_auto_apply_state(svc: Any, payload: dict[str, object]) -> None:
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

    @staticmethod
    def _restore_victron_ess_balance_stable_tuning_state(svc: Any, payload: dict[str, object]) -> None:
        svc._victron_ess_balance_last_stable_tuning = _StateRuntimeRestoreMixin._normalized_victron_ess_balance_tuning_mapping(
            payload.get("last_stable_tuning")
        )
        svc._victron_ess_balance_last_stable_at = non_negative_float_or_none(payload.get("last_stable_at"))
        svc._victron_ess_balance_last_stable_profile_key = str(
            payload.get("last_stable_profile_key", "") or ""
        )
        svc._victron_ess_balance_conservative_tuning = _StateRuntimeRestoreMixin._normalized_victron_ess_balance_tuning_mapping(
            payload.get("conservative_tuning")
        )
        _StateRuntimeRestoreMixin._restore_victron_ess_balance_suspend_state(svc, payload)

    @classmethod
    def _restore_victron_ess_balance_adaptive_tuning_payload(cls, svc: Any, payload: dict[str, object]) -> None:
        if not cls._valid_victron_ess_balance_schema_version(payload):
            return
        if not cls._victron_ess_balance_payload_matches_topology(svc, payload):
            return
        cls._restore_victron_ess_balance_pid_tuning(svc, payload)
        activation_mode = cls._victron_ess_balance_activation_mode(payload, svc)
        if activation_mode is not None:
            svc.auto_battery_discharge_balance_victron_bias_activation_mode = activation_mode
        cls._restore_victron_ess_balance_auto_apply_state(svc, payload)
        cls._restore_victron_ess_balance_stable_tuning_state(svc, payload)

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


def _victron_ess_balance_energy_ids(svc: Any) -> list[str]:
    energy_ids: list[str] = []
    for definition in tuple(getattr(svc, "auto_energy_sources", ()) or ()):
        normalized_id = str(getattr(definition, "source_id", "") or "").strip()
        if normalized_id:
            energy_ids.append(normalized_id)
    return energy_ids


def _victron_ess_balance_runtime_string(svc: Any, attr_name: str) -> str:
    return str(getattr(svc, attr_name, "") or "").strip()
