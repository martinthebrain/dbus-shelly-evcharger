# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from typing import Any, Mapping

from venus_evcharger.energy import derive_energy_forecast, summarize_energy_learning_profiles
from venus_evcharger.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin


class _AutoDecisionBatteryBalanceMixin(_ComposableControllerMixin):
    def _combined_battery_activity_context(self) -> dict[str, float | int | str | None]:
        """Return a conservative battery activity picture used to de-bias surplus decisions."""
        svc = self.service
        cluster, sources, profiles = self._battery_activity_inputs()
        learning_summary = summarize_energy_learning_profiles(profiles)
        if sources:
            charge_penalty, discharge_penalty, max_charge_ratio, max_discharge_ratio = self._source_activity_penalties(
                sources,
                profiles,
            )
        else:
            charge_penalty, discharge_penalty, max_charge_ratio, max_discharge_ratio = self._cluster_activity_penalties(
                cluster,
                learning_summary,
            )

        behavior = self._battery_learning_behavior(learning_summary)
        forecast = derive_energy_forecast(cluster, learning_summary)
        charge_penalty *= self._battery_penalty_multiplier(
            direction="charge",
            response_delay_seconds=behavior["response_delay_seconds"],
            support_bias=behavior["support_bias"],
            import_support_bias=behavior["import_support_bias"],
            export_bias=behavior["export_bias"],
        )
        discharge_penalty *= self._battery_penalty_multiplier(
            direction="discharge",
            response_delay_seconds=behavior["response_delay_seconds"],
            support_bias=behavior["support_bias"],
            import_support_bias=behavior["import_support_bias"],
            export_bias=behavior["export_bias"],
        )
        (
            discharge_balance_warning_active,
            discharge_balance_error_w,
            discharge_balance_warn_threshold_w,
            discharge_balance_bias_mode,
            discharge_balance_bias_gate_active,
            discharge_balance_bias_start_error_w,
            discharge_balance_bias_penalty_w,
        ) = self._battery_discharge_balance_policy_context(
            cluster,
            expected_export_w=self._cluster_or_forecast_metric(
                cluster,
                forecast,
                "expected_near_term_export_w",
            ),
            reserve_floor_soc=behavior["reserve_band_floor_soc"],
        )
        (
            discharge_balance_coordination_feasibility,
            discharge_balance_coordination_advisory_active,
            discharge_balance_coordination_advisory_reason,
        ) = self._battery_discharge_balance_coordination_advisory(
            cluster,
            warning_active=discharge_balance_warning_active,
        )
        (
            discharge_balance_coordination_policy_enabled,
            discharge_balance_coordination_support_mode,
            discharge_balance_coordination_gate_active,
            discharge_balance_coordination_start_error_w,
            discharge_balance_coordination_penalty_w,
        ) = self._battery_discharge_balance_coordination_policy_context(
            cluster,
            feasibility=discharge_balance_coordination_feasibility,
        )
        if discharge_balance_warning_active:
            warning_throttled = getattr(svc, "_warning_throttled", None)
            if callable(warning_throttled):
                warning_throttled(
                    "battery-discharge-balance-warning",
                    max(30.0, float(getattr(svc, "auto_battery_scan_interval_seconds", 60.0) or 60.0)),
                    "Auto mode observed battery discharge imbalance: error=%s W active=%s eligible=%s",
                    round(discharge_balance_error_w, 1),
                    int(cluster.get("battery_discharge_balance_active_source_count", 0) or 0),
                    int(cluster.get("battery_discharge_balance_eligible_source_count", 0) or 0),
                )
        if discharge_balance_coordination_advisory_active:
            warning_throttled = getattr(svc, "_warning_throttled", None)
            if callable(warning_throttled):
                warning_throttled(
                    "battery-discharge-balance-coordination-advisory",
                    max(30.0, float(getattr(svc, "auto_battery_scan_interval_seconds", 60.0) or 60.0)),
                    "Auto mode observed ESS imbalance but coordination feasibility is limited: %s",
                    discharge_balance_coordination_advisory_reason,
                )
        discharge_balance_effective_penalty_w = max(
            float(discharge_balance_bias_penalty_w),
            float(discharge_balance_coordination_penalty_w),
        )
        return {
            "surplus_penalty_w": float(charge_penalty + discharge_penalty + discharge_balance_effective_penalty_w),
            "charge_power_w": charge_penalty if charge_penalty > 0.0 else None,
            "discharge_power_w": discharge_penalty if discharge_penalty > 0.0 else None,
            "charge_activity_ratio": max_charge_ratio,
            "discharge_activity_ratio": max_discharge_ratio,
            "learning_profile_count": int(learning_summary.get("profile_count", 0) or 0),
            "observed_max_charge_power_w": self._non_negative_optional_float(
                learning_summary.get("observed_max_charge_power_w")
            ),
            "observed_max_discharge_power_w": self._non_negative_optional_float(
                learning_summary.get("observed_max_discharge_power_w")
            ),
            "typical_response_delay_seconds": behavior["response_delay_seconds"],
            "support_bias": behavior["support_bias"],
            "day_support_bias": behavior["day_support_bias"],
            "night_support_bias": behavior["night_support_bias"],
            "import_support_bias": behavior["import_support_bias"],
            "export_bias": behavior["export_bias"],
            "battery_first_export_bias": behavior["battery_first_export_bias"],
            "power_smoothing_ratio": behavior["power_smoothing_ratio"],
            "reserve_band_floor_soc": behavior["reserve_band_floor_soc"],
            "reserve_band_ceiling_soc": behavior["reserve_band_ceiling_soc"],
            "reserve_band_width_soc": behavior["reserve_band_width_soc"],
            "battery_headroom_charge_w": self._cluster_or_forecast_metric(
                cluster,
                forecast,
                "battery_headroom_charge_w",
            ),
            "battery_headroom_discharge_w": self._cluster_or_forecast_metric(
                cluster,
                forecast,
                "battery_headroom_discharge_w",
            ),
            "expected_near_term_export_w": self._cluster_or_forecast_metric(
                cluster,
                forecast,
                "expected_near_term_export_w",
            ),
            "expected_near_term_import_w": self._cluster_or_forecast_metric(
                cluster,
                forecast,
                "expected_near_term_import_w",
            ),
            "discharge_balance_policy_enabled": 1
            if bool(getattr(svc, "auto_battery_discharge_balance_policy_enabled", False))
            else 0,
            "discharge_balance_warning_active": 1 if discharge_balance_warning_active else 0,
            "discharge_balance_warning_error_w": discharge_balance_error_w if discharge_balance_warning_active else None,
            "discharge_balance_warn_threshold_w": discharge_balance_warn_threshold_w,
            "discharge_balance_bias_mode": discharge_balance_bias_mode,
            "discharge_balance_bias_gate_active": 1 if discharge_balance_bias_gate_active else 0,
            "discharge_balance_bias_start_error_w": discharge_balance_bias_start_error_w,
            "discharge_balance_bias_penalty_w": discharge_balance_bias_penalty_w,
            "discharge_balance_coordination_policy_enabled": (
                1 if discharge_balance_coordination_policy_enabled else 0
            ),
            "discharge_balance_coordination_support_mode": discharge_balance_coordination_support_mode,
            "discharge_balance_coordination_feasibility": discharge_balance_coordination_feasibility,
            "discharge_balance_coordination_gate_active": (
                1 if discharge_balance_coordination_gate_active else 0
            ),
            "discharge_balance_coordination_start_error_w": discharge_balance_coordination_start_error_w,
            "discharge_balance_coordination_penalty_w": discharge_balance_coordination_penalty_w,
            "discharge_balance_coordination_advisory_active": (
                1 if discharge_balance_coordination_advisory_active else 0
            ),
            "discharge_balance_coordination_advisory_reason": discharge_balance_coordination_advisory_reason,
            "mode": self._battery_activity_mode(charge_penalty, discharge_penalty),
        }

    def _battery_discharge_balance_policy_context(
        self,
        cluster: Mapping[str, Any],
        *,
        expected_export_w: float | None,
        reserve_floor_soc: float | None,
    ) -> tuple[bool, float, float, str, bool, float, float]:
        svc = self.service
        if not bool(getattr(svc, "auto_battery_discharge_balance_policy_enabled", False)):
            return False, 0.0, 0.0, "always", False, 0.0, 0.0
        error_w = self._non_negative_optional_float(cluster.get("battery_discharge_balance_error_w")) or 0.0
        eligible_source_count = int(cluster.get("battery_discharge_balance_eligible_source_count", 0) or 0)
        active_source_count = int(cluster.get("battery_discharge_balance_active_source_count", 0) or 0)
        warn_threshold_w = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_warn_error_watts", 0.0) or 0.0),
        )
        bias_start_error_w = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_bias_start_error_watts", 0.0) or 0.0),
        )
        bias_max_penalty_w = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_bias_max_penalty_watts", 0.0) or 0.0),
        )
        bias_mode = self._discharge_balance_bias_mode()
        reserve_margin_soc = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_bias_reserve_margin_soc", 0.0) or 0.0),
        )
        warning_active = (
            eligible_source_count >= 2 and active_source_count >= 1 and error_w > 0.0 and error_w >= warn_threshold_w
        )
        bias_penalty_w = 0.0
        gate_active = self._discharge_balance_bias_gate_active(
            bias_mode=bias_mode,
            cluster=cluster,
            expected_export_w=expected_export_w,
            reserve_floor_soc=reserve_floor_soc,
            reserve_margin_soc=reserve_margin_soc,
        )
        if eligible_source_count >= 2 and active_source_count >= 1 and gate_active and bias_max_penalty_w > 0.0:
            if bias_start_error_w <= 0.0:
                bias_penalty_w = bias_max_penalty_w if error_w > 0.0 else 0.0
            elif error_w > bias_start_error_w:
                scale = min((error_w - bias_start_error_w) / max(bias_start_error_w, 1.0), 1.0)
                bias_penalty_w = bias_max_penalty_w * scale
        return (
            bool(warning_active),
            float(error_w),
            float(warn_threshold_w),
            bias_mode,
            bool(gate_active),
            float(bias_start_error_w),
            float(bias_penalty_w),
        )

    def _discharge_balance_bias_mode(self) -> str:
        raw_mode = str(getattr(self.service, "auto_battery_discharge_balance_bias_mode", "always") or "").strip().lower()
        if raw_mode in {"always", "export_only", "above_reserve_band", "export_and_above_reserve_band"}:
            return raw_mode
        return "always"

    @staticmethod
    def _battery_discharge_balance_coordination_advisory(
        cluster: Mapping[str, Any],
        *,
        warning_active: bool,
    ) -> tuple[str, bool, str]:
        eligible_source_count = int(cluster.get("battery_discharge_balance_eligible_source_count", 0) or 0)
        control_candidate_count = int(cluster.get("battery_discharge_balance_control_candidate_count", 0) or 0)
        control_ready_count = int(cluster.get("battery_discharge_balance_control_ready_count", 0) or 0)
        supported_count = int(cluster.get("battery_discharge_balance_supported_control_source_count", 0) or 0)
        experimental_count = int(cluster.get("battery_discharge_balance_experimental_control_source_count", 0) or 0)
        if eligible_source_count < 2:
            return "not_needed", False, "single_source_or_insufficient_sources"
        if supported_count >= 2 and control_ready_count >= 2:
            return "supported", False, "multiple_supported_control_sources_ready"
        if control_ready_count >= 2 and (supported_count + experimental_count) >= 2 and experimental_count > 0:
            return "experimental", bool(warning_active), "coordination_depends_on_experimental_write_paths"
        if control_candidate_count >= 2 and control_ready_count < 2:
            return "blocked_by_source_availability", bool(warning_active), "candidate_sources_not_ready"
        if control_candidate_count >= 1:
            return "partial", bool(warning_active), "only_some_sources_offer_a_write_path"
        return "observe_only", bool(warning_active), "no_configured_source_offers_a_write_path"

    def _battery_discharge_balance_coordination_policy_context(
        self,
        cluster: Mapping[str, Any],
        *,
        feasibility: str,
    ) -> tuple[bool, str, bool, float, float]:
        svc = self.service
        support_mode = self._discharge_balance_coordination_support_mode()
        if not bool(getattr(svc, "auto_battery_discharge_balance_coordination_enabled", False)):
            return False, support_mode, False, 0.0, 0.0
        error_w = self._non_negative_optional_float(cluster.get("battery_discharge_balance_error_w")) or 0.0
        control_ready_count = int(cluster.get("battery_discharge_balance_control_ready_count", 0) or 0)
        start_error_w = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_coordination_start_error_watts", 0.0) or 0.0),
        )
        max_penalty_w = max(
            0.0,
            float(getattr(svc, "auto_battery_discharge_balance_coordination_max_penalty_watts", 0.0) or 0.0),
        )
        allowed_feasibilities = {"supported"}
        if support_mode == "allow_experimental":
            allowed_feasibilities.add("experimental")
        gate_active = control_ready_count >= 2 and feasibility in allowed_feasibilities
        penalty_w = 0.0
        if gate_active and max_penalty_w > 0.0:
            if start_error_w <= 0.0:
                penalty_w = max_penalty_w if error_w > 0.0 else 0.0
            elif error_w > start_error_w:
                scale = min((error_w - start_error_w) / max(start_error_w, 1.0), 1.0)
                penalty_w = max_penalty_w * scale
        return True, support_mode, bool(gate_active), float(start_error_w), float(penalty_w)

    def _discharge_balance_coordination_support_mode(self) -> str:
        raw_mode = str(
            getattr(self.service, "auto_battery_discharge_balance_coordination_support_mode", "supported_only") or ""
        ).strip().lower()
        if raw_mode in {"supported_only", "allow_experimental"}:
            return raw_mode
        return "supported_only"

    def _discharge_balance_bias_gate_active(
        self,
        *,
        bias_mode: str,
        cluster: Mapping[str, Any],
        expected_export_w: float | None,
        reserve_floor_soc: float | None,
        reserve_margin_soc: float,
    ) -> bool:
        export_active = bool((expected_export_w or 0.0) > 0.0)
        combined_soc = self._non_negative_optional_float(cluster.get("battery_combined_soc"))
        reserve_gate_active = (
            combined_soc is not None
            and reserve_floor_soc is not None
            and combined_soc >= (float(reserve_floor_soc) + float(reserve_margin_soc))
        )
        if bias_mode == "always":
            return True
        if bias_mode == "export_only":
            return export_active
        if bias_mode == "above_reserve_band":
            return reserve_gate_active
        if bias_mode == "export_and_above_reserve_band":
            return export_active and reserve_gate_active
        return True
