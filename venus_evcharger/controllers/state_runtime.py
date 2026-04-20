# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined"
# pyright: reportAttributeAccessIssue=false
"""Runtime-state and runtime-override helpers for the state controller."""

from __future__ import annotations

import configparser
from io import StringIO
import json
import logging
import time
from typing import Any, Callable, cast

from venus_evcharger.backend.models import PhaseSelection, normalize_phase_selection, normalize_phase_selection_tuple
from venus_evcharger.core.common import DEFAULT_SCHEDULED_ENABLED_DAYS, normalize_hhmm_text, scheduled_enabled_days_text
from venus_evcharger.core.contracts import finite_float_or_none, normalize_learning_phase, normalize_learning_state
from venus_evcharger.core.shared import compact_json, write_text_atomically
from venus_evcharger.controllers.state_specs import (
    RUNTIME_OVERRIDE_BY_CONFIG_KEY,
    RUNTIME_OVERRIDE_SPECS,
    RUNTIME_OVERRIDE_SECTION,
    RuntimeOverrideSpec,
    _CasePreservingConfigParser,
)


class _StateRuntimeMixin:
    @staticmethod
    def coerce_runtime_int(value: object, default: int = 0) -> int:
        if isinstance(value, bool):
            return int(default)
        if not isinstance(value, (str, int, float)):
            return int(default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def coerce_runtime_float(value: object, default: float = 0.0) -> float:
        normalized = finite_float_or_none(value)
        return float(default) if normalized is None else normalized

    def _base_runtime_state(self, svc: Any) -> dict[str, object]:
        return {
            "mode": int(svc.virtual_mode),
            "autostart": int(svc.virtual_autostart),
            "enable": int(svc.virtual_enable),
            "startstop": int(svc.virtual_startstop),
            "manual_override_until": float(svc.manual_override_until),
            "auto_mode_cutover_pending": 1 if svc._auto_mode_cutover_pending else 0,
            "relay_last_changed_at": svc.relay_last_changed_at,
            "relay_last_off_at": svc.relay_last_off_at,
        }

    @staticmethod
    def _learned_charge_power_runtime_state(svc: Any) -> dict[str, object]:
        return {
            "learned_charge_power_watts": getattr(svc, "learned_charge_power_watts", None),
            "learned_charge_power_updated_at": getattr(svc, "learned_charge_power_updated_at", None),
            "learned_charge_power_state": getattr(svc, "learned_charge_power_state", "unknown"),
            "learned_charge_power_learning_since": getattr(svc, "learned_charge_power_learning_since", None),
            "learned_charge_power_sample_count": int(getattr(svc, "learned_charge_power_sample_count", 0)),
            "learned_charge_power_phase": getattr(svc, "learned_charge_power_phase", None),
            "learned_charge_power_voltage": getattr(svc, "learned_charge_power_voltage", None),
            "learned_charge_power_signature_mismatch_sessions": int(
                getattr(svc, "learned_charge_power_signature_mismatch_sessions", 0)
            ),
            "learned_charge_power_signature_checked_session_started_at": getattr(
                svc,
                "learned_charge_power_signature_checked_session_started_at",
                None,
            ),
        }

    def _phase_selection_runtime_state(self, svc: Any) -> dict[str, object]:
        return {
            "active_phase_selection": self._normalize_runtime_phase_selection(getattr(svc, "active_phase_selection", "P1")),
            "requested_phase_selection": self._normalize_runtime_phase_selection(getattr(svc, "requested_phase_selection", "P1")),
            "supported_phase_selections": list(
                self._normalize_runtime_supported_phase_selections(getattr(svc, "supported_phase_selections", ("P1",)))
            ),
        }

    def _phase_switch_runtime_state(self, svc: Any) -> dict[str, object]:
        default_phase = self._normalize_runtime_phase_selection(getattr(svc, "requested_phase_selection", "P1"))
        return {
            "phase_switch_pending_selection": self._normalized_optional_runtime_phase_selection(
                getattr(svc, "_phase_switch_pending_selection", None),
                default_phase,
            ),
            "phase_switch_state": self._normalize_phase_switch_state(getattr(svc, "_phase_switch_state", None)),
            "phase_switch_requested_at": self._coerce_optional_runtime_past_time(getattr(svc, "_phase_switch_requested_at", None)),
            "phase_switch_stable_until": self._coerce_optional_runtime_float(getattr(svc, "_phase_switch_stable_until", None)),
            "phase_switch_resume_relay": 1 if bool(getattr(svc, "_phase_switch_resume_relay", False)) else 0,
            "phase_switch_mismatch_counts": dict(getattr(svc, "_phase_switch_mismatch_counts", {}) or {}),
            "phase_switch_last_mismatch_selection": self._normalized_optional_runtime_phase_selection(
                getattr(svc, "_phase_switch_last_mismatch_selection", None),
                default_phase,
            ),
            "phase_switch_last_mismatch_at": self._coerce_optional_runtime_past_time(
                getattr(svc, "_phase_switch_last_mismatch_at", None)
            ),
            "phase_switch_lockout_selection": self._normalized_optional_runtime_phase_selection(
                getattr(svc, "_phase_switch_lockout_selection", None),
                default_phase,
            ),
            "phase_switch_lockout_reason": str(getattr(svc, "_phase_switch_lockout_reason", "") or ""),
            "phase_switch_lockout_at": self._coerce_optional_runtime_past_time(getattr(svc, "_phase_switch_lockout_at", None)),
            "phase_switch_lockout_until": self._coerce_optional_runtime_float(getattr(svc, "_phase_switch_lockout_until", None)),
        }

    def _contactor_runtime_state(self, svc: Any) -> dict[str, object]:
        return {
            "contactor_fault_counts": dict(getattr(svc, "_contactor_fault_counts", {}) or {}),
            "contactor_fault_active_reason": self._normalized_optional_runtime_text(
                getattr(svc, "_contactor_fault_active_reason", "")
            ),
            "contactor_fault_active_since": self._coerce_optional_runtime_past_time(
                getattr(svc, "_contactor_fault_active_since", None)
            ),
            "contactor_lockout_reason": str(getattr(svc, "_contactor_lockout_reason", "") or ""),
            "contactor_lockout_source": str(getattr(svc, "_contactor_lockout_source", "") or ""),
            "contactor_lockout_at": self._coerce_optional_runtime_past_time(getattr(svc, "_contactor_lockout_at", None)),
        }

    def current_runtime_state(self) -> dict[str, object]:
        svc = self.service
        runtime_state = self._base_runtime_state(svc)
        runtime_state.update(self._learned_charge_power_runtime_state(svc))
        runtime_state.update(self._phase_selection_runtime_state(svc))
        runtime_state.update(self._phase_switch_runtime_state(svc))
        runtime_state.update(self._contactor_runtime_state(svc))
        return runtime_state

    @classmethod
    def _read_runtime_override_values(cls, path: str) -> dict[str, str]:
        if not str(path).strip():
            return {}
        parser = _CasePreservingConfigParser()
        try:
            read_files = parser.read(path)
        except Exception as error:  # pylint: disable=broad-except
            logging.warning("Unable to read runtime overrides from %s: %s", path, error)
            return {}
        if not read_files or not parser.has_section(RUNTIME_OVERRIDE_SECTION):
            return {}
        return cls._normalized_runtime_override_section_items(parser[RUNTIME_OVERRIDE_SECTION].items())

    @classmethod
    def _normalized_runtime_override_section_items(cls, items: Any) -> dict[str, str]:
        values: dict[str, str] = {}
        for config_key, raw_value in items:
            normalized_item = cls._normalized_runtime_override_item(config_key, raw_value)
            if normalized_item is not None:
                key, value = normalized_item
                values[key] = value
        return values

    @staticmethod
    def _normalized_runtime_override_item(config_key: object, raw_value: object) -> tuple[str, str] | None:
        spec = RUNTIME_OVERRIDE_BY_CONFIG_KEY.get(str(config_key).strip())
        if spec is None:
            return None
        return spec.config_key, str(raw_value).strip()

    @classmethod
    def _apply_runtime_overrides_to_config(cls, svc: Any, config: configparser.ConfigParser) -> configparser.ConfigParser:
        defaults = config["DEFAULT"]
        path = cls.runtime_overrides_path(defaults)
        values = cls._read_runtime_override_values(path)
        for config_key, value in values.items():
            defaults[config_key] = str(value)
        svc.runtime_overrides_path = path
        svc._runtime_overrides_active = bool(values)
        svc._runtime_overrides_values = dict(values)
        svc._runtime_overrides_serialized = compact_json(values)
        return config

    @staticmethod
    def _override_value_as_text(spec: RuntimeOverrideSpec, value: object) -> str:
        renderers: dict[str, Callable[[object], str]] = {
            "bool": lambda raw: str(int(bool(raw))),
            "int": lambda raw: str(_StateRuntimeMixin.coerce_runtime_int(raw)),
            "phase": lambda raw: str(_StateRuntimeMixin._normalize_runtime_phase_selection(raw)),
            "weekday_set": lambda raw: scheduled_enabled_days_text(raw, DEFAULT_SCHEDULED_ENABLED_DAYS),
            "hhmm": lambda raw: normalize_hhmm_text(raw, "06:30"),
            "float": lambda raw: str(_StateRuntimeMixin.coerce_runtime_float(raw)),
        }
        return renderers.get(spec.value_kind, renderers["float"])(value)

    @staticmethod
    def _runtime_override_default_value(spec: RuntimeOverrideSpec) -> object:
        if spec.value_kind in {"bool", "int"}:
            return 0
        if spec.value_kind == "phase":
            return "P1"
        if spec.value_kind == "weekday_set":
            return DEFAULT_SCHEDULED_ENABLED_DAYS
        if spec.value_kind == "hhmm":
            return "06:30"
        return 0.0

    def current_runtime_overrides(self) -> dict[str, str]:
        svc = self.service
        values: dict[str, str] = {}
        for spec in RUNTIME_OVERRIDE_SPECS:
            raw_value = getattr(svc, spec.attr_name, self._runtime_override_default_value(spec))
            values[spec.config_key] = self._override_value_as_text(spec, raw_value)
        return values

    def _serialized_runtime_overrides(self) -> str:
        return compact_json(self.current_runtime_overrides())

    @staticmethod
    def _runtime_override_write_min_interval_seconds(svc: Any) -> float:
        configured = finite_float_or_none(getattr(svc, "runtime_overrides_write_min_interval_seconds", None))
        return 1.0 if configured is None else max(0.0, float(configured))

    @staticmethod
    def _runtime_now(svc: Any) -> float:
        time_now = getattr(svc, "_time_now", None)
        raw_current_time: object = time_now() if callable(time_now) else time.time()
        return _StateRuntimeMixin.coerce_runtime_float(raw_current_time, time.time())

    @staticmethod
    def _clear_pending_runtime_overrides(svc: Any) -> None:
        svc._runtime_overrides_pending_serialized = None
        svc._runtime_overrides_pending_values = None
        svc._runtime_overrides_pending_text = None
        svc._runtime_overrides_pending_due_at = None

    @staticmethod
    def _runtime_override_ini_text(payload: dict[str, str]) -> str:
        parser = _CasePreservingConfigParser()
        parser[RUNTIME_OVERRIDE_SECTION] = payload
        handle = StringIO()
        parser.write(handle)
        return handle.getvalue()

    def _stage_runtime_overrides_write(self, svc: Any, payload: dict[str, str], serialized: str, rendered: str, due_at: float) -> None:
        svc._runtime_overrides_pending_serialized = serialized
        svc._runtime_overrides_pending_values = dict(payload)
        svc._runtime_overrides_pending_text = rendered
        svc._runtime_overrides_pending_due_at = float(due_at)
        svc._runtime_overrides_active = True
        svc._runtime_overrides_values = dict(payload)

    def _write_runtime_overrides_payload(self, svc: Any, path: str, payload: dict[str, str], serialized: str, rendered: str, now: float) -> None:
        write_text_atomically(path, rendered)
        svc._runtime_overrides_serialized = serialized
        svc._runtime_overrides_last_saved_at = float(now)
        svc._runtime_overrides_active = True
        svc._runtime_overrides_values = dict(payload)
        self._clear_pending_runtime_overrides(svc)

    def flush_runtime_overrides(self, now: float | None = None) -> None:
        svc = self.service
        path = str(getattr(svc, "runtime_overrides_path", "")).strip()
        pending_payload = self._pending_runtime_overrides_payload(svc, path)
        if pending_payload is None:
            return
        current_time = self._runtime_now(svc) if now is None else float(now)
        if not self._runtime_override_write_due(svc, current_time):
            return
        try:
            self._write_runtime_overrides_payload(svc, path, pending_payload[0], pending_payload[1], pending_payload[2], current_time)
        except Exception as error:  # pylint: disable=broad-except
            svc._runtime_overrides_pending_due_at = float(current_time + self._runtime_override_write_min_interval_seconds(svc))
            logging.warning("Unable to write runtime overrides to %s: %s", path, error)

    def _runtime_override_write_due(self, svc: Any, current_time: float) -> bool:
        due_at = self._coerce_optional_runtime_float(getattr(svc, "_runtime_overrides_pending_due_at", None))
        return due_at is None or current_time >= due_at

    @staticmethod
    def _pending_runtime_overrides_payload(svc: Any, path: str) -> tuple[dict[str, str], str, str] | None:
        pending_serialized = getattr(svc, "_runtime_overrides_pending_serialized", None)
        pending_values = getattr(svc, "_runtime_overrides_pending_values", None)
        pending_text = getattr(svc, "_runtime_overrides_pending_text", None)
        if not bool(path) or not bool(pending_serialized):
            return None
        if not isinstance(pending_values, dict) or not isinstance(pending_text, str):
            return None
        return dict(pending_values), str(pending_serialized), pending_text

    @classmethod
    def _runtime_override_due_at(cls, current_time: float, pending_due_at: float | None, last_saved_at: float | None, min_interval: float) -> float | None:
        if pending_due_at is not None and current_time < pending_due_at:
            return pending_due_at
        if last_saved_at is not None and (current_time - last_saved_at) < min_interval:
            return last_saved_at + min_interval
        return None

    def save_runtime_overrides(self) -> None:
        svc = self.service
        path = str(getattr(svc, "runtime_overrides_path", "")).strip()
        if not path:
            return
        payload = self.current_runtime_overrides()
        serialized = compact_json(payload)
        if serialized == getattr(svc, "_runtime_overrides_serialized", None):
            self._clear_pending_runtime_overrides(svc)
            return
        rendered = self._runtime_override_ini_text(payload)
        current_time = self._runtime_now(svc)
        last_saved_at = self._coerce_optional_runtime_float(getattr(svc, "_runtime_overrides_last_saved_at", None))
        pending_due_at = self._coerce_optional_runtime_float(getattr(svc, "_runtime_overrides_pending_due_at", None))
        min_interval = self._runtime_override_write_min_interval_seconds(svc)
        due_at = self._runtime_override_due_at(current_time, pending_due_at, last_saved_at, min_interval)
        if due_at is not None:
            self._stage_runtime_overrides_write(svc, payload, serialized, rendered, due_at)
            return
        try:
            self._write_runtime_overrides_payload(svc, path, payload, serialized, rendered, current_time)
        except Exception as error:  # pylint: disable=broad-except
            self._stage_runtime_overrides_write(svc, payload, serialized, rendered, current_time + min_interval)
            logging.warning("Unable to write runtime overrides to %s: %s", path, error)

    def _serialized_runtime_state(self) -> str:
        return compact_json(self.current_runtime_state())

    @staticmethod
    def _coerce_optional_runtime_float(value: object) -> float | None:
        if value is None:
            return None
        return _StateRuntimeMixin.coerce_runtime_float(value)

    @staticmethod
    def _coerce_optional_runtime_past_time(value: object, now: float | None = None) -> float | None:
        normalized = _StateRuntimeMixin._coerce_optional_runtime_float(value)
        if normalized is None:
            return None
        current = time.time() if now is None else float(now)
        if normalized > (current + 1.0):
            return None
        return normalized

    @staticmethod
    def _normalize_learned_charge_power_state(value: object) -> str:
        return normalize_learning_state(value)

    @staticmethod
    def _normalize_learned_charge_power_phase(value: object) -> str | None:
        return normalize_learning_phase(value)

    @staticmethod
    def _normalize_runtime_phase_selection(value: object, default: PhaseSelection = "P1") -> PhaseSelection:
        return normalize_phase_selection(value, default)

    @staticmethod
    def _normalize_runtime_supported_phase_selections(
        value: object,
        default: tuple[PhaseSelection, ...] = ("P1",),
    ) -> tuple[PhaseSelection, ...]:
        normalized: tuple[PhaseSelection, ...] = normalize_phase_selection_tuple(value, default)
        return normalized

    @classmethod
    def _normalized_optional_runtime_phase_selection(cls, value: object, default: PhaseSelection = "P1") -> PhaseSelection | None:
        if value is None:
            return None
        return cls._normalize_runtime_phase_selection(value, default)

    @staticmethod
    def _normalized_optional_runtime_text(value: object) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _normalize_phase_switch_state(value: object) -> str | None:
        state = str(value).strip().lower() if value is not None else ""
        if state in {"waiting-relay-off", "stabilizing"}:
            return state
        return None

    @staticmethod
    def _read_runtime_state_payload(path: str) -> dict[str, object] | None:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                loaded_state = cast(dict[str, object], json.load(handle))
        except FileNotFoundError:
            return None
        except Exception as error:  # pylint: disable=broad-except
            logging.warning("Unable to read runtime state from %s: %s", path, error)
            return None
        return loaded_state

    @staticmethod
    def _runtime_load_time(svc: Any) -> float:
        time_now = getattr(svc, "_time_now", None)
        raw_current_time: object = time_now() if callable(time_now) else time.time()
        return _StateRuntimeMixin.coerce_runtime_float(raw_current_time, time.time())
