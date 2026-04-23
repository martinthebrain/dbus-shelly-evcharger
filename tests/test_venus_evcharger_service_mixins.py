# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.modules["vedbus"] = MagicMock()

from venus_evcharger.control import ControlCommand
from venus_evcharger.energy import EnergySourceDefinition
from venus_evcharger.service.factory import ServiceControllerFactoryMixin
from venus_evcharger.service.auto import DbusAutoLogicMixin
from venus_evcharger.service.control import ControlApiMixin
from venus_evcharger.service.runtime import RuntimeHelperMixin
from venus_evcharger.service.state_publish import StatePublishMixin
from venus_evcharger.service.update import UpdateCycleMixin


class _RuntimeService(RuntimeHelperMixin):
    def _ensure_runtime_support_controller(self):
        return None

    def _ensure_auto_input_supervisor(self):
        return None

    def _ensure_shelly_io_controller(self):
        return None


class _UpdateService(UpdateCycleMixin):
    def _ensure_update_controller(self):
        return None


class _AutoService(DbusAutoLogicMixin):
    def _ensure_dbus_input_controller(self):
        return None

    def _ensure_auto_controller(self):
        return None

    def _ensure_write_controller(self):
        return None

    def _ensure_bootstrap_controller(self):
        return None


class _StateService(StatePublishMixin):
    def _ensure_state_controller(self):
        return None

    def _ensure_dbus_publisher(self):
        return None


class _ControlService(ControlApiMixin):
    def _ensure_write_controller(self):
        return None

    def _ensure_dbus_publisher(self):
        return None

    def _state_summary(self):
        return "mode=1 enable=1"

    def _current_runtime_state(self):
        return {"mode": 1, "autostart": 1, "combined_battery_soc": 62.0}

    def _get_worker_snapshot(self):
        return {
            "battery_combined_soc": 62.0,
            "battery_source_count": 2,
            "battery_online_source_count": 2,
            "battery_combined_charge_power_w": 800.0,
            "battery_combined_discharge_power_w": 1200.0,
            "battery_combined_net_power_w": 400.0,
            "battery_combined_ac_power_w": 1800.0,
            "battery_combined_pv_input_power_w": 2600.0,
            "battery_combined_grid_interaction_w": -350.0,
            "battery_headroom_charge_w": 900.0,
            "battery_headroom_discharge_w": 1100.0,
            "expected_near_term_export_w": 425.0,
            "expected_near_term_import_w": 50.0,
            "battery_discharge_balance_mode": "capacity_reserve_weighted",
            "battery_discharge_balance_target_distribution_mode": "capacity_reserve_weighted",
            "battery_discharge_balance_error_w": 250.0,
            "battery_discharge_balance_max_abs_error_w": 250.0,
            "battery_discharge_balance_total_discharge_w": 1200.0,
            "battery_discharge_balance_eligible_source_count": 2,
            "battery_discharge_balance_active_source_count": 1,
            "battery_discharge_balance_control_candidate_count": 1,
            "battery_discharge_balance_control_ready_count": 1,
            "battery_discharge_balance_supported_control_source_count": 0,
            "battery_discharge_balance_experimental_control_source_count": 1,
            "battery_average_confidence": 0.75,
            "battery_battery_source_count": 1,
            "battery_hybrid_inverter_source_count": 1,
            "battery_inverter_source_count": 0,
            "battery_sources": [
                {"source_id": "victron", "discharge_balance_control_support": "unsupported"},
                {"source_id": "hybrid", "discharge_balance_control_support": "experimental"},
            ],
            "battery_learning_profiles": {
                "victron": {
                    "sample_count": 2,
                    "charge_sample_count": 1,
                    "discharge_sample_count": 1,
                    "observed_max_charge_power_w": 700.0,
                    "observed_max_grid_import_w": 400.0,
                    "average_active_charge_power_w": 700.0,
                    "average_active_discharge_power_w": 900.0,
                    "average_active_power_delta_w": 100.0,
                    "typical_response_delay_seconds": 4.0,
                    "import_support_sample_count": 1,
                    "export_discharge_sample_count": 1,
                    "day_charge_sample_count": 1,
                    "night_discharge_sample_count": 1,
                    "smoothing_sample_count": 1,
                    "observed_min_discharge_soc": 35.0,
                    "observed_max_charge_soc": 85.0,
                    "direction_change_count": 1,
                },
                "hybrid": {
                    "sample_count": 3,
                    "charge_sample_count": 2,
                    "discharge_sample_count": 1,
                    "observed_max_discharge_power_w": 1400.0,
                    "observed_max_ac_power_w": 2000.0,
                    "observed_max_pv_input_power_w": 2600.0,
                    "observed_max_grid_export_w": 600.0,
                    "average_active_charge_power_w": 500.0,
                    "average_active_discharge_power_w": 1400.0,
                    "average_active_power_delta_w": 200.0,
                    "typical_response_delay_seconds": 8.0,
                    "export_charge_sample_count": 2,
                    "export_idle_sample_count": 1,
                    "day_charge_sample_count": 1,
                    "day_discharge_sample_count": 1,
                    "night_charge_sample_count": 1,
                    "smoothing_sample_count": 2,
                    "observed_min_discharge_soc": 45.0,
                    "observed_max_charge_soc": 90.0,
                    "direction_change_count": 2,
                },
            },
        }


class _FactoryService(ServiceControllerFactoryMixin):
    _normalize_mode_func = staticmethod(lambda value: int(value))
    _mode_uses_auto_logic_func = staticmethod(lambda mode: bool(mode))
    _normalize_phase_func = staticmethod(lambda value: str(value))
    _month_window_func = staticmethod(lambda *args, **kwargs: None)
    _age_seconds_func = staticmethod(lambda _captured_at, _now: 0)
    _health_code_func = staticmethod(lambda _reason: 0)
    _phase_values_func = staticmethod(lambda *args, **kwargs: {})
    _read_version_func = staticmethod(lambda _path: "")
    _gobject_module = MagicMock()
    _script_path_value = ""
    _formatter_bundle = {}


class TestShellyWallboxServiceMixins(unittest.TestCase):
    def test_runtime_helper_mixin_delegates_runtime_supervisor_and_io_calls(self):
        service = _RuntimeService()
        service._runtime_support_controller = MagicMock()
        service._auto_input_supervisor = MagicMock()
        service._shelly_io_controller = MagicMock()

        service._reset_system_bus()
        service._ensure_system_bus_state()
        service._create_system_bus()
        service._init_worker_state()
        service._worker_state_defaults()
        service._ensure_missing_attributes({"x": 1})
        service._ensure_worker_state()
        service._set_worker_snapshot({"captured_at": 1.0})
        service._update_worker_snapshot(pv_power=123.0)
        service._get_worker_snapshot()
        service._ensure_observability_state()
        service._is_update_stale(12.0)
        service._watchdog_recover(12.0)
        service._warning_throttled("warn", 5.0, "msg %s", "x")
        service._write_auto_audit_event("waiting-surplus", cached=True)
        service._mark_failure("dbus")
        service._mark_recovery("dbus", "recovered %s", "ok")
        service._source_retry_ready("dbus", 12.0)
        service._source_retry_remaining("dbus", 12.0)
        service._delay_source_retry("dbus", 12.0)
        service._delay_source_retry("dbus", 12.0, 7.0)
        service._stop_auto_input_helper(force=True)
        service._spawn_auto_input_helper(12.0)
        service._ensure_auto_input_helper_process(12.0)
        service._refresh_auto_input_snapshot(12.0)
        service._request_with_session("sess", "http://example.invalid")
        service._rpc_call_with_session("sess", "Switch.Set", on=True)
        service._worker_fetch_pm_status()
        service._build_local_pm_status(True)
        service._publish_local_pm_status(True, 12.0)
        service._queue_relay_command(True, 12.0)
        service._peek_pending_relay_command()
        service._clear_pending_relay_command(True)
        service._worker_apply_pending_relay_command()
        service._io_worker_once()
        service._io_worker_loop()
        service._start_io_worker()
        service._request("http://example.invalid")
        service.rpc_call("Switch.GetStatus", id=0)
        service.fetch_rpc("Shelly.GetDeviceInfo")
        service.fetch_pm_status()
        service.set_relay(True)
        service._phase_selection_requires_pause()
        service._apply_phase_selection("P1_P2")

        runtime = service._runtime_support_controller
        runtime.reset_system_bus.assert_called_once_with()
        runtime.ensure_system_bus_state.assert_called_once_with()
        runtime.create_system_bus.assert_called_once_with()
        runtime.init_worker_state.assert_called_once_with()
        runtime.worker_state_defaults.assert_called_once_with()
        runtime.ensure_missing_attributes.assert_called_once_with(service, {"x": 1})
        runtime.ensure_worker_state.assert_called_once_with()
        runtime.set_worker_snapshot.assert_called_once_with({"captured_at": 1.0})
        runtime.update_worker_snapshot.assert_called_once_with(pv_power=123.0)
        runtime.get_worker_snapshot.assert_called_once_with()
        runtime.ensure_observability_state.assert_called_once_with()
        runtime.is_update_stale.assert_called_once_with(12.0)
        runtime.watchdog_recover.assert_called_once_with(12.0)
        runtime.warning_throttled.assert_called_once_with("warn", 5.0, "msg %s", "x")
        runtime.write_auto_audit_event.assert_called_once_with("waiting-surplus", True)
        runtime.mark_failure.assert_called_once_with("dbus")
        runtime.mark_recovery.assert_called_once_with("dbus", "recovered %s", "ok")
        runtime.source_retry_ready.assert_called_once_with("dbus", 12.0)
        runtime.source_retry_remaining.assert_called_once_with("dbus", 12.0)
        runtime.delay_source_retry.assert_any_call("dbus", 12.0)
        runtime.delay_source_retry.assert_any_call("dbus", 12.0, 7.0)
        service._auto_input_supervisor.stop_helper.assert_called_once_with(True)
        service._auto_input_supervisor.spawn_helper.assert_called_once_with(12.0)
        service._auto_input_supervisor.ensure_helper_process.assert_called_once_with(12.0)
        service._auto_input_supervisor.refresh_snapshot.assert_called_once_with(12.0)
        io = service._shelly_io_controller
        io.request_with_session.assert_called_once_with("sess", "http://example.invalid")
        io.rpc_call_with_session.assert_called_once_with("sess", "Switch.Set", on=True)
        io.worker_fetch_pm_status.assert_called_once_with()
        io.build_local_pm_status.assert_called_once_with(True)
        io.publish_local_pm_status.assert_called_once_with(True, 12.0)
        io.queue_relay_command.assert_called_once_with(True, 12.0)
        io.peek_pending_relay_command.assert_called_once_with()
        io.clear_pending_relay_command.assert_called_once_with(True)
        io.worker_apply_pending_relay_command.assert_called_once_with()
        io.io_worker_once.assert_called_once_with()
        io.io_worker_loop.assert_called_once_with()
        io.start_io_worker.assert_called_once_with()
        io.request.assert_called_once_with("http://example.invalid")
        io.rpc_call.assert_any_call("Switch.GetStatus", id=0)
        io.rpc_call.assert_any_call("Shelly.GetDeviceInfo")
        io.fetch_pm_status.assert_called_once_with()
        io.set_relay.assert_called_once_with(True)
        io.phase_selection_requires_pause.assert_called_once_with()
        io.set_phase_selection.assert_called_once_with("P1_P2")

    def test_control_api_mixin_builds_commands_and_manages_http_server(self):
        service = _ControlService()
        service._write_controller = MagicMock()
        service._write_controller.build_control_command_from_payload.return_value = ControlCommand(
            name="set_mode",
            path="/Mode",
            value=1,
            source="http",
        )
        service._control_api_server = MagicMock(bound_host="127.0.0.1", bound_port=8765)
        service.control_api_enabled = True

        command = service._control_command_from_payload({"name": "set_mode", "value": 1}, source="http")
        service._start_control_api_server()
        service._stop_control_api_server()

        self.assertEqual(command.name, "set_mode")
        service._write_controller.build_control_command_from_payload.assert_called_once_with(
            {"name": "set_mode", "value": 1},
            source="http",
        )
        service._control_api_server.start.assert_called_once_with()
        self.assertEqual(service.control_api_listen_host, "127.0.0.1")
        self.assertEqual(service.control_api_listen_port, 8765)
        service._control_api_server.stop.assert_called_once_with()

    def test_control_api_mixin_exposes_capabilities_and_state_payloads(self):
        service = _ControlService()
        service.virtual_mode = 1
        service.virtual_enable = 1
        service.virtual_startstop = 0
        service.virtual_autostart = 1
        service.active_phase_selection = "P1"
        service.requested_phase_selection = "P1_P2"
        service.backend_mode = "split"
        service.meter_backend_type = "template_meter"
        service.switch_backend_type = "switch_group"
        service.charger_backend_type = "goe_charger"
        service._last_auto_state = "charging"
        service._last_auto_state_code = 3
        service._last_health_reason = ""
        service._software_update_state = "available"
        service._software_update_available = 1
        service._software_update_no_update_active = 0
        service._runtime_overrides_active = True
        service.runtime_overrides_path = "/run/runtime.ini"
        service.control_api_audit_path = "/run/control-audit.jsonl"
        service.control_api_audit_max_entries = 3
        service.control_api_idempotency_path = "/run/control-idempotency.json"
        service.control_api_idempotency_max_entries = 4
        service.control_api_rate_limit_max_requests = 15
        service.control_api_rate_limit_window_seconds = 7.5
        service.control_api_critical_cooldown_seconds = 3.0
        service.auto_battery_discharge_balance_policy_enabled = True
        service.auto_battery_discharge_balance_warn_error_watts = 400.0
        service.auto_battery_discharge_balance_bias_start_error_watts = 500.0
        service.auto_battery_discharge_balance_bias_max_penalty_watts = 300.0
        service.auto_battery_discharge_balance_bias_mode = "export_only"
        service.auto_battery_discharge_balance_bias_reserve_margin_soc = 5.0
        service.auto_battery_discharge_balance_coordination_enabled = True
        service.auto_battery_discharge_balance_coordination_support_mode = "supported_only"
        service.auto_battery_discharge_balance_coordination_start_error_watts = 900.0
        service.auto_battery_discharge_balance_coordination_max_penalty_watts = 150.0
        service.auto_battery_discharge_balance_victron_bias_enabled = True
        service.auto_battery_discharge_balance_victron_bias_source_id = "victron"
        service.auto_battery_discharge_balance_victron_bias_service = "com.victronenergy.settings"
        service.auto_battery_discharge_balance_victron_bias_path = "/Settings/CGwacs/AcPowerSetPoint"
        service.auto_battery_discharge_balance_victron_bias_base_setpoint_watts = 50.0
        service.auto_battery_discharge_balance_victron_bias_deadband_watts = 100.0
        service.auto_battery_discharge_balance_victron_bias_activation_mode = "export_and_above_reserve_band"
        service.auto_battery_discharge_balance_victron_bias_support_mode = "supported_only"
        service.auto_battery_discharge_balance_victron_bias_kp = 0.2
        service.auto_battery_discharge_balance_victron_bias_ki = 0.02
        service.auto_battery_discharge_balance_victron_bias_kd = 0.0
        service.auto_battery_discharge_balance_victron_bias_integral_limit_watts = 250.0
        service.auto_battery_discharge_balance_victron_bias_max_abs_watts = 500.0
        service.auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second = 50.0
        service.auto_battery_discharge_balance_victron_bias_min_update_seconds = 2.0
        service.auto_battery_discharge_balance_victron_bias_auto_apply_enabled = True
        service.auto_battery_discharge_balance_victron_bias_auto_apply_min_confidence = 0.85
        service.auto_battery_discharge_balance_victron_bias_auto_apply_min_profile_samples = 3
        service.auto_battery_discharge_balance_victron_bias_auto_apply_min_stability_score = 0.75
        service.auto_battery_discharge_balance_victron_bias_auto_apply_blend = 0.25
        service.auto_battery_discharge_balance_victron_bias_observation_window_seconds = 30.0
        service.auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled = True
        service.auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds = 120.0
        service.auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes = 3
        service.auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds = 180.0
        service.auto_battery_discharge_balance_victron_bias_rollback_enabled = True
        service.auto_battery_discharge_balance_victron_bias_rollback_min_stability_score = 0.45
        service.auto_battery_discharge_balance_victron_bias_require_clean_phases = True
        service._last_auto_metrics = {
            "battery_discharge_balance_warning_active": 1,
            "battery_discharge_balance_warning_error_w": 250.0,
            "battery_discharge_balance_warn_threshold_w": 400.0,
            "battery_discharge_balance_bias_mode": "export_only",
            "battery_discharge_balance_bias_gate_active": 1,
            "battery_discharge_balance_bias_start_error_w": 500.0,
            "battery_discharge_balance_bias_penalty_w": 0.0,
            "battery_discharge_balance_coordination_policy_enabled": 1,
            "battery_discharge_balance_coordination_support_mode": "supported_only",
            "battery_discharge_balance_coordination_feasibility": "partial",
            "battery_discharge_balance_coordination_gate_active": 0,
            "battery_discharge_balance_coordination_start_error_w": 900.0,
            "battery_discharge_balance_coordination_penalty_w": 0.0,
            "battery_discharge_balance_coordination_advisory_active": 1,
            "battery_discharge_balance_coordination_advisory_reason": "only_some_sources_offer_a_write_path",
            "battery_discharge_balance_victron_bias_enabled": 1,
            "battery_discharge_balance_victron_bias_active": 1,
            "battery_discharge_balance_victron_bias_source_id": "victron",
            "battery_discharge_balance_victron_bias_topology_key": "victron-bias-learning/v1/source=victron/service=com.victronenergy.settings/path=/Settings/CGwacs/AcPowerSetPoint/energy=battery,hybrid",
            "battery_discharge_balance_victron_bias_activation_mode": "export_and_above_reserve_band",
            "battery_discharge_balance_victron_bias_activation_gate_active": 1,
            "battery_discharge_balance_victron_bias_support_mode": "supported_only",
            "battery_discharge_balance_victron_bias_learning_profile_key": "more_export:export:day:above_reserve_band",
            "battery_discharge_balance_victron_bias_learning_profile_action_direction": "more_export",
            "battery_discharge_balance_victron_bias_learning_profile_site_regime": "export",
            "battery_discharge_balance_victron_bias_learning_profile_direction": "export",
            "battery_discharge_balance_victron_bias_learning_profile_day_phase": "day",
            "battery_discharge_balance_victron_bias_learning_profile_reserve_phase": "above_reserve_band",
            "battery_discharge_balance_victron_bias_learning_profile_sample_count": 3,
            "battery_discharge_balance_victron_bias_learning_profile_response_delay_seconds": 4.0,
            "battery_discharge_balance_victron_bias_learning_profile_estimated_gain": 2.5,
            "battery_discharge_balance_victron_bias_learning_profile_overshoot_count": 0,
            "battery_discharge_balance_victron_bias_learning_profile_settled_count": 3,
            "battery_discharge_balance_victron_bias_learning_profile_stability_score": 0.9,
            "battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second": 60.0,
            "battery_discharge_balance_victron_bias_learning_profile_preferred_bias_limit_watts": 550.0,
            "battery_discharge_balance_victron_bias_source_error_w": -320.0,
            "battery_discharge_balance_victron_bias_pid_output_w": -64.0,
            "battery_discharge_balance_victron_bias_setpoint_w": -14.0,
            "battery_discharge_balance_victron_bias_response_delay_seconds": 4.0,
            "battery_discharge_balance_victron_bias_estimated_gain": 2.5,
            "battery_discharge_balance_victron_bias_overshoot_active": 0,
            "battery_discharge_balance_victron_bias_overshoot_count": 1,
            "battery_discharge_balance_victron_bias_settling_active": 1,
            "battery_discharge_balance_victron_bias_settled_count": 3,
            "battery_discharge_balance_victron_bias_stability_score": 0.82,
            "battery_discharge_balance_victron_bias_recommended_kp": 0.23,
            "battery_discharge_balance_victron_bias_recommended_ki": 0.022,
            "battery_discharge_balance_victron_bias_recommended_kd": 0.01,
            "battery_discharge_balance_victron_bias_recommended_deadband_watts": 90.0,
            "battery_discharge_balance_victron_bias_recommended_max_abs_watts": 550.0,
            "battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second": 55.0,
            "battery_discharge_balance_victron_bias_recommended_activation_mode": "export_and_above_reserve_band",
            "battery_discharge_balance_victron_bias_recommendation_confidence": 0.81,
            "battery_discharge_balance_victron_bias_recommendation_reason": "can_relax_conservatism",
            "battery_discharge_balance_victron_bias_recommendation_profile_key": "more_export:export:day:above_reserve_band",
            "battery_discharge_balance_victron_bias_recommendation_ini_snippet": (
                "AutoBatteryDischargeBalanceVictronBiasKp=0.23\n"
                "AutoBatteryDischargeBalanceVictronBiasKi=0.022\n"
                "AutoBatteryDischargeBalanceVictronBiasKd=0.01\n"
                "AutoBatteryDischargeBalanceVictronBiasDeadbandWatts=90\n"
                "AutoBatteryDischargeBalanceVictronBiasMaxAbsWatts=550\n"
                "AutoBatteryDischargeBalanceVictronBiasRampRateWattsPerSecond=55\n"
                "AutoBatteryDischargeBalanceVictronBiasActivationMode=export_and_above_reserve_band"
            ),
            "battery_discharge_balance_victron_bias_recommendation_hint": (
                "Telemetry looks stable; you can cautiously relax the current "
                "Victron bias tuning (confidence 0.81)."
            ),
            "battery_discharge_balance_victron_bias_auto_apply_enabled": 1,
            "battery_discharge_balance_victron_bias_auto_apply_active": 1,
            "battery_discharge_balance_victron_bias_auto_apply_reason": "applied",
            "battery_discharge_balance_victron_bias_auto_apply_generation": 2,
            "battery_discharge_balance_victron_bias_auto_apply_observation_window_active": 1,
            "battery_discharge_balance_victron_bias_auto_apply_observation_window_until": 1234.0,
            "battery_discharge_balance_victron_bias_auto_apply_last_param": (
                "auto_battery_discharge_balance_victron_bias_deadband_watts"
            ),
            "battery_discharge_balance_victron_bias_rollback_enabled": 1,
            "battery_discharge_balance_victron_bias_rollback_active": 0,
            "battery_discharge_balance_victron_bias_rollback_reason": "stable",
            "battery_discharge_balance_victron_bias_rollback_stable_profile_key": (
                "more_export:export:day:above_reserve_band"
            ),
            "battery_discharge_balance_victron_bias_telemetry_clean": 1,
            "battery_discharge_balance_victron_bias_telemetry_clean_reason": "clean",
            "battery_discharge_balance_victron_bias_oscillation_lockout_enabled": 1,
            "battery_discharge_balance_victron_bias_oscillation_lockout_active": 0,
            "battery_discharge_balance_victron_bias_oscillation_lockout_reason": "",
            "battery_discharge_balance_victron_bias_oscillation_lockout_until": None,
            "battery_discharge_balance_victron_bias_oscillation_direction_change_count": 2,
            "battery_discharge_balance_victron_bias_reason": "applied",
        }
        service._victron_ess_balance_last_stable_tuning = {
            "kp": 0.2,
            "ki": 0.02,
            "kd": 0.0,
            "deadband_watts": 100.0,
            "max_abs_watts": 500.0,
            "ramp_rate_watts_per_second": 50.0,
            "activation_mode": "always",
        }
        service._victron_ess_balance_last_stable_at = 1190.0
        service._victron_ess_balance_last_stable_profile_key = "more_export:export:day:above_reserve_band"
        service._victron_ess_balance_learning_profiles = {
            "more_export:export:day:above_reserve_band": {
                "key": "more_export:export:day:above_reserve_band",
                "action_direction": "more_export",
                "site_regime": "export",
                "direction": "export",
                "day_phase": "day",
                "reserve_phase": "above_reserve_band",
                "response_delay_seconds": 4.0,
                "delay_samples": 2,
                "estimated_gain": 2.5,
                "gain_samples": 2,
                "overshoot_count": 0,
                "settled_count": 3,
                "stability_score": 0.9,
                "safe_ramp_rate_watts_per_second": 60.0,
                "preferred_bias_limit_watts": 550.0,
            }
        }
        service.companion_dbus_bridge_enabled = True
        service.companion_battery_service_enabled = True
        service.companion_pvinverter_service_enabled = True
        service.companion_grid_service_enabled = True
        service.companion_grid_authoritative_source = "huawei"
        service.companion_source_services_enabled = True
        service.companion_source_grid_services_enabled = True
        service.companion_battery_deviceinstance = 100
        service.companion_pvinverter_deviceinstance = 101
        service.companion_grid_deviceinstance = 102
        service.companion_source_battery_deviceinstance_base = 200
        service.companion_source_pvinverter_deviceinstance_base = 300
        service.companion_source_grid_deviceinstance_base = 400
        service.companion_battery_service_name = "com.victronenergy.battery.external_100"
        service.companion_pvinverter_service_name = "com.victronenergy.pvinverter.external_101"
        service.companion_grid_service_name = "com.victronenergy.grid.external_102"
        service.companion_source_battery_service_prefix = "com.victronenergy.battery.external"
        service.companion_source_pvinverter_service_prefix = "com.victronenergy.pvinverter.external"
        service.companion_source_grid_service_prefix = "com.victronenergy.grid.external"
        service.auto_use_combined_battery_soc = True
        service.auto_energy_sources = (
            EnergySourceDefinition(source_id="battery", profile_name="dbus-battery"),
            EnergySourceDefinition(source_id="hybrid", profile_name="huawei_ma_native_ap"),
        )
        service.control_api_auth_token = "secret"
        service.product_name = "Venus EV Charger Service"
        service.service_name = "com.victronenergy.evcharger"
        service.connection_name = "HTTP"
        service.hardware_version = "HW-1"
        service.firmware_version = "FW-1"
        service.runtime_state_path = "/run/runtime.json"
        service.supported_phase_selections = ("P1", "P1_P2", "P1_P2_P3")
        service._dbus_publisher = MagicMock()
        service._dbus_publisher._diagnostic_counter_values.return_value = {"/Auto/State": "charging"}
        service._dbus_publisher._diagnostic_age_values.return_value = {"/Auto/LastShellyReadAge": 0.2}

        capabilities = service._control_api_capabilities_payload()
        summary = service._state_api_summary_payload()
        runtime = service._state_api_runtime_payload()
        operational = service._state_api_operational_payload()
        recommendation = service._state_api_victron_bias_recommendation_payload()
        diagnostics = service._state_api_dbus_diagnostics_payload()
        topology = service._state_api_topology_payload()
        update = service._state_api_update_payload()
        config_effective = service._state_api_config_effective_payload()
        health = service._state_api_health_payload()
        healthz = service._state_api_healthz_payload()
        version = service._state_api_version_payload()
        build = service._state_api_build_payload()
        contracts = service._state_api_contracts_payload()
        snapshot = service._state_api_event_snapshot_payload()

        self.assertTrue(capabilities["auth_required"])
        self.assertIn("set_mode", capabilities["command_names"])
        self.assertIn("/v1/capabilities", capabilities["endpoints"])
        self.assertIn("/v1/state/healthz", capabilities["endpoints"])
        self.assertIn("/v1/events", capabilities["versioning"]["experimental_endpoints"])
        self.assertTrue(capabilities["features"]["command_audit_trail"])
        self.assertTrue(capabilities["features"]["event_kind_filters"])
        self.assertTrue(capabilities["features"]["event_retry_hints"])
        self.assertTrue(capabilities["features"]["multi_phase_selection"])
        self.assertTrue(capabilities["features"]["optimistic_concurrency"])
        self.assertTrue(capabilities["features"]["per_command_request_schemas"])
        self.assertTrue(capabilities["features"]["rate_limiting"])
        self.assertEqual(capabilities["auth_scopes"], ["control_admin", "control_basic", "read", "update_admin"])
        self.assertEqual(capabilities["command_scope_requirements"]["set_mode"], "control_basic")
        self.assertEqual(capabilities["command_scope_requirements"]["trigger_software_update"], "update_admin")
        self.assertEqual(capabilities["topology"]["charger_backend"], "goe_charger")
        self.assertEqual(capabilities["supported_phase_selections"], ["P1", "P1_P2", "P1_P2_P3"])
        self.assertEqual(capabilities["available_modes"], [0, 1, 2])
        self.assertEqual(summary["kind"], "summary")
        self.assertEqual(summary["summary"], "mode=1 enable=1")
        self.assertEqual(runtime["kind"], "runtime")
        self.assertEqual(runtime["state"]["mode"], 1)
        self.assertEqual(runtime["state"]["combined_battery_soc"], 62.0)
        self.assertEqual(recommendation["kind"], "victron-bias-recommendation")
        self.assertEqual(
            recommendation["state"]["active_learning_profile_key"],
            "more_export:export:day:above_reserve_band",
        )
        self.assertEqual(recommendation["state"]["active_learning_profile"]["action_direction"], "more_export")
        self.assertEqual(recommendation["state"]["active_learning_profile"]["site_regime"], "export")
        self.assertEqual(recommendation["state"]["active_learning_profile"]["direction"], "export")
        self.assertEqual(recommendation["state"]["current_kp"], 0.2)
        self.assertEqual(recommendation["state"]["current_kd"], 0.0)
        self.assertEqual(recommendation["state"]["current_deadband_watts"], 100.0)
        self.assertEqual(recommendation["state"]["current_max_abs_watts"], 500.0)
        self.assertEqual(recommendation["state"]["recommended_kp"], 0.23)
        self.assertEqual(recommendation["state"]["recommended_kd"], 0.01)
        self.assertEqual(recommendation["state"]["recommended_deadband_watts"], 90.0)
        self.assertEqual(recommendation["state"]["recommended_max_abs_watts"], 550.0)
        self.assertEqual(recommendation["state"]["recommended_activation_mode"], "export_and_above_reserve_band")
        self.assertEqual(
            recommendation["state"]["recommendation_profile_key"],
            "more_export:export:day:above_reserve_band",
        )
        self.assertEqual(recommendation["state"]["auto_apply_enabled"], True)
        self.assertEqual(recommendation["state"]["auto_apply_active"], True)
        self.assertEqual(recommendation["state"]["auto_apply_generation"], 2)
        self.assertIn("more_export:export:day:above_reserve_band", recommendation["state"]["learning_profiles"])
        self.assertEqual(
            recommendation["state"]["recommendation_ini_snippet"],
            "AutoBatteryDischargeBalanceVictronBiasKp=0.23\n"
            "AutoBatteryDischargeBalanceVictronBiasKi=0.022\n"
            "AutoBatteryDischargeBalanceVictronBiasKd=0.01\n"
            "AutoBatteryDischargeBalanceVictronBiasDeadbandWatts=90\n"
            "AutoBatteryDischargeBalanceVictronBiasMaxAbsWatts=550\n"
            "AutoBatteryDischargeBalanceVictronBiasRampRateWattsPerSecond=55\n"
            "AutoBatteryDischargeBalanceVictronBiasActivationMode=export_and_above_reserve_band",
        )
        self.assertEqual(operational["kind"], "operational")
        self.assertEqual(operational["state"]["backend_mode"], "split")
        self.assertEqual(operational["state"]["auto_state"], "charging")
        self.assertEqual(operational["state"]["software_update_state"], "available")
        self.assertEqual(operational["state"]["runtime_overrides_path"], "/run/runtime.ini")
        self.assertEqual(operational["state"]["combined_battery_soc"], 62.0)
        self.assertEqual(operational["state"]["combined_battery_source_count"], 2)
        self.assertEqual(operational["state"]["combined_battery_charge_power_w"], 800.0)
        self.assertEqual(operational["state"]["combined_battery_discharge_power_w"], 1200.0)
        self.assertEqual(operational["state"]["combined_battery_net_power_w"], 400.0)
        self.assertEqual(operational["state"]["combined_battery_ac_power_w"], 1800.0)
        self.assertEqual(operational["state"]["combined_battery_pv_input_power_w"], 2600.0)
        self.assertEqual(operational["state"]["combined_battery_grid_interaction_w"], -350.0)
        self.assertEqual(operational["state"]["combined_battery_headroom_charge_w"], 900.0)
        self.assertEqual(operational["state"]["combined_battery_headroom_discharge_w"], 1100.0)
        self.assertEqual(operational["state"]["expected_near_term_export_w"], 425.0)
        self.assertEqual(operational["state"]["expected_near_term_import_w"], 50.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_mode"], "capacity_reserve_weighted")
        self.assertEqual(
            operational["state"]["battery_discharge_balance_target_distribution_mode"],
            "capacity_reserve_weighted",
        )
        self.assertEqual(operational["state"]["battery_discharge_balance_error_w"], 250.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_max_abs_error_w"], 250.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_total_discharge_w"], 1200.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_eligible_source_count"], 2)
        self.assertEqual(operational["state"]["battery_discharge_balance_active_source_count"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_control_candidate_count"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_control_ready_count"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_supported_control_source_count"], 0)
        self.assertEqual(operational["state"]["battery_discharge_balance_experimental_control_source_count"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_policy_enabled"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_warning_active"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_warning_error_w"], 250.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_warn_threshold_w"], 400.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_bias_mode"], "export_only")
        self.assertEqual(operational["state"]["battery_discharge_balance_bias_gate_active"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_bias_start_error_w"], 500.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_bias_penalty_w"], 0.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_coordination_policy_enabled"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_coordination_support_mode"], "supported_only")
        self.assertEqual(operational["state"]["battery_discharge_balance_coordination_feasibility"], "partial")
        self.assertEqual(operational["state"]["battery_discharge_balance_coordination_gate_active"], 0)
        self.assertEqual(operational["state"]["battery_discharge_balance_coordination_start_error_w"], 900.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_coordination_penalty_w"], 0.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_coordination_advisory_active"], 1)
        self.assertEqual(
            operational["state"]["battery_discharge_balance_coordination_advisory_reason"],
            "only_some_sources_offer_a_write_path",
        )
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_enabled"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_active"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_source_id"], "victron")
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_topology_key"],
            "victron-bias-learning/v1/source=victron/service=com.victronenergy.settings/path=/Settings/CGwacs/AcPowerSetPoint/energy=battery,hybrid",
        )
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_activation_mode"],
            "export_and_above_reserve_band",
        )
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_activation_gate_active"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_support_mode"], "supported_only")
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_learning_profile_key"],
            "more_export:export:day:above_reserve_band",
        )
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_learning_profile_action_direction"],
            "more_export",
        )
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_learning_profile_site_regime"],
            "export",
        )
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_learning_profile_direction"],
            "export",
        )
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_learning_profile_day_phase"], "day")
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_learning_profile_reserve_phase"],
            "above_reserve_band",
        )
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_source_error_w"], -320.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_pid_output_w"], -64.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_setpoint_w"], -14.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_response_delay_seconds"], 4.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_estimated_gain"], 2.5)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_overshoot_active"], 0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_overshoot_count"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_settling_active"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_settled_count"], 3)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_stability_score"], 0.82)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_telemetry_clean"], 1)
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_telemetry_clean_reason"],
            "clean",
        )
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_oscillation_lockout_enabled"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_oscillation_lockout_active"], 0)
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_oscillation_direction_change_count"],
            2,
        )
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_learning_profile_safe_ramp_rate_watts_per_second"],
            60.0,
        )
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_learning_profile_preferred_bias_limit_watts"],
            550.0,
        )
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_recommended_kp"], 0.23)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_recommended_ki"], 0.022)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_recommended_kd"], 0.01)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_recommended_deadband_watts"], 90.0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_recommended_max_abs_watts"], 550.0)
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_recommended_ramp_rate_watts_per_second"],
            55.0,
        )
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_recommended_activation_mode"],
            "export_and_above_reserve_band",
        )
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_recommendation_confidence"], 0.81)
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_recommendation_reason"],
            "can_relax_conservatism",
        )
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_recommendation_profile_key"],
            "more_export:export:day:above_reserve_band",
        )
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_recommendation_ini_snippet"],
            "AutoBatteryDischargeBalanceVictronBiasKp=0.23\n"
            "AutoBatteryDischargeBalanceVictronBiasKi=0.022\n"
            "AutoBatteryDischargeBalanceVictronBiasKd=0.01\n"
            "AutoBatteryDischargeBalanceVictronBiasDeadbandWatts=90\n"
            "AutoBatteryDischargeBalanceVictronBiasMaxAbsWatts=550\n"
            "AutoBatteryDischargeBalanceVictronBiasRampRateWattsPerSecond=55\n"
            "AutoBatteryDischargeBalanceVictronBiasActivationMode=export_and_above_reserve_band",
        )
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_recommendation_hint"],
            "Telemetry looks stable; you can cautiously relax the current "
            "Victron bias tuning (confidence 0.81).",
        )
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_auto_apply_enabled"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_auto_apply_active"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_auto_apply_reason"], "applied")
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_auto_apply_generation"], 2)
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_auto_apply_observation_window_active"],
            1,
        )
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_auto_apply_observation_window_until"],
            1234.0,
        )
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_auto_apply_last_param"],
            "auto_battery_discharge_balance_victron_bias_deadband_watts",
        )
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_rollback_enabled"], 1)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_rollback_active"], 0)
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_rollback_reason"], "stable")
        self.assertEqual(
            operational["state"]["battery_discharge_balance_victron_bias_rollback_stable_profile_key"],
            "more_export:export:day:above_reserve_band",
        )
        self.assertEqual(operational["state"]["battery_discharge_balance_victron_bias_reason"], "applied")
        self.assertEqual(operational["state"]["combined_battery_average_confidence"], 0.75)
        self.assertEqual(operational["state"]["combined_battery_battery_source_count"], 1)
        self.assertEqual(operational["state"]["combined_battery_hybrid_inverter_source_count"], 1)
        self.assertEqual(operational["state"]["combined_battery_inverter_source_count"], 0)
        self.assertEqual(operational["state"]["combined_battery_learning_profile_count"], 2)
        self.assertEqual(operational["state"]["combined_battery_observed_max_charge_power_w"], 700.0)
        self.assertEqual(operational["state"]["combined_battery_observed_max_discharge_power_w"], 1400.0)
        self.assertEqual(operational["state"]["combined_battery_observed_max_ac_power_w"], 2000.0)
        self.assertEqual(operational["state"]["combined_battery_observed_max_pv_input_power_w"], 2600.0)
        self.assertEqual(operational["state"]["combined_battery_observed_max_grid_import_w"], 400.0)
        self.assertEqual(operational["state"]["combined_battery_observed_max_grid_export_w"], 600.0)
        self.assertEqual(operational["state"]["combined_battery_average_active_charge_power_w"], 566.6666666666666)
        self.assertEqual(operational["state"]["combined_battery_average_active_discharge_power_w"], 1150.0)
        self.assertAlmostEqual(operational["state"]["combined_battery_average_active_power_delta_w"], 166.66666666666666)
        self.assertAlmostEqual(operational["state"]["combined_battery_power_smoothing_ratio"], 0.8179824561403508)
        self.assertEqual(operational["state"]["combined_battery_typical_response_delay_seconds"], 6.0)
        self.assertAlmostEqual(operational["state"]["combined_battery_support_bias"], -0.2)
        self.assertAlmostEqual(operational["state"]["combined_battery_day_support_bias"], -1.0 / 3.0)
        self.assertEqual(operational["state"]["combined_battery_night_support_bias"], 0.0)
        self.assertEqual(operational["state"]["combined_battery_import_support_bias"], 1.0)
        self.assertAlmostEqual(operational["state"]["combined_battery_export_bias"], 1.0 / 3.0)
        self.assertEqual(operational["state"]["combined_battery_battery_first_export_bias"], 0.0)
        self.assertEqual(operational["state"]["combined_battery_reserve_band_floor_soc"], 45.0)
        self.assertEqual(operational["state"]["combined_battery_reserve_band_ceiling_soc"], 85.0)
        self.assertEqual(operational["state"]["combined_battery_reserve_band_width_soc"], 40.0)
        self.assertEqual(operational["state"]["combined_battery_direction_change_count"], 3)
        self.assertEqual(diagnostics["kind"], "dbus-diagnostics")
        self.assertEqual(diagnostics["state"]["/Auto/State"], "charging")
        self.assertEqual(diagnostics["state"]["/Auto/LastShellyReadAge"], 0.2)
        self.assertEqual(topology["kind"], "topology")
        self.assertEqual(topology["state"]["charger_backend"], "goe_charger")
        self.assertEqual(update["kind"], "update")
        self.assertEqual(config_effective["kind"], "config-effective")
        self.assertEqual(config_effective["state"]["runtime_overrides_path"], "/run/runtime.ini")
        self.assertEqual(config_effective["state"]["control_api_audit_path"], "/run/control-audit.jsonl")
        self.assertEqual(config_effective["state"]["control_api_idempotency_path"], "/run/control-idempotency.json")
        self.assertEqual(config_effective["state"]["control_api_rate_limit_max_requests"], 15)
        self.assertEqual(config_effective["state"]["control_api_rate_limit_window_seconds"], 7.5)
        self.assertEqual(config_effective["state"]["control_api_critical_cooldown_seconds"], 3.0)
        self.assertTrue(config_effective["state"]["companion_dbus_bridge_enabled"])
        self.assertTrue(config_effective["state"]["companion_source_services_enabled"])
        self.assertTrue(config_effective["state"]["companion_grid_service_enabled"])
        self.assertEqual(config_effective["state"]["companion_grid_authoritative_source"], "huawei")
        self.assertTrue(config_effective["state"]["companion_source_grid_services_enabled"])
        self.assertEqual(config_effective["state"]["companion_battery_deviceinstance"], 100)
        self.assertEqual(config_effective["state"]["companion_grid_deviceinstance"], 102)
        self.assertEqual(config_effective["state"]["companion_source_battery_deviceinstance_base"], 200)
        self.assertEqual(config_effective["state"]["companion_source_grid_deviceinstance_base"], 400)
        self.assertTrue(config_effective["state"]["auto_use_combined_battery_soc"])
        self.assertTrue(config_effective["state"]["auto_battery_discharge_balance_policy_enabled"])
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_warn_error_watts"], 400.0)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_bias_start_error_watts"], 500.0)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_bias_max_penalty_watts"], 300.0)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_bias_mode"], "export_only")
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_bias_reserve_margin_soc"], 5.0)
        self.assertTrue(config_effective["state"]["auto_battery_discharge_balance_coordination_enabled"])
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_coordination_support_mode"], "supported_only")
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_coordination_start_error_watts"], 900.0)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_coordination_max_penalty_watts"], 150.0)
        self.assertTrue(config_effective["state"]["auto_battery_discharge_balance_victron_bias_enabled"])
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_source_id"], "victron")
        self.assertEqual(
            config_effective["state"]["auto_battery_discharge_balance_victron_bias_service"],
            "com.victronenergy.settings",
        )
        self.assertEqual(
            config_effective["state"]["auto_battery_discharge_balance_victron_bias_path"],
            "/Settings/CGwacs/AcPowerSetPoint",
        )
        self.assertEqual(
            config_effective["state"]["auto_battery_discharge_balance_victron_bias_base_setpoint_watts"],
            50.0,
        )
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_deadband_watts"], 100.0)
        self.assertEqual(
            config_effective["state"]["auto_battery_discharge_balance_victron_bias_support_mode"],
            "supported_only",
        )
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_kp"], 0.2)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_ki"], 0.02)
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_kd"], 0.0)
        self.assertEqual(
            config_effective["state"]["auto_battery_discharge_balance_victron_bias_integral_limit_watts"],
            250.0,
        )
        self.assertEqual(config_effective["state"]["auto_battery_discharge_balance_victron_bias_max_abs_watts"], 500.0)
        self.assertEqual(
            config_effective["state"]["auto_battery_discharge_balance_victron_bias_ramp_rate_watts_per_second"],
            50.0,
        )
        self.assertEqual(
            config_effective["state"]["auto_battery_discharge_balance_victron_bias_min_update_seconds"],
            2.0,
        )
        self.assertEqual(
            config_effective["state"]["auto_battery_discharge_balance_victron_bias_observation_window_seconds"],
            30.0,
        )
        self.assertTrue(
            config_effective["state"]["auto_battery_discharge_balance_victron_bias_oscillation_lockout_enabled"]
        )
        self.assertEqual(
            config_effective["state"]["auto_battery_discharge_balance_victron_bias_oscillation_lockout_window_seconds"],
            120.0,
        )
        self.assertEqual(
            config_effective["state"][
                "auto_battery_discharge_balance_victron_bias_oscillation_lockout_min_direction_changes"
            ],
            3,
        )
        self.assertEqual(
            config_effective["state"]["auto_battery_discharge_balance_victron_bias_oscillation_lockout_duration_seconds"],
            180.0,
        )
        self.assertTrue(config_effective["state"]["auto_battery_discharge_balance_victron_bias_rollback_enabled"])
        self.assertEqual(
            config_effective["state"]["auto_battery_discharge_balance_victron_bias_rollback_min_stability_score"],
            0.45,
        )
        self.assertTrue(config_effective["state"]["auto_battery_discharge_balance_victron_bias_require_clean_phases"])
        self.assertEqual(config_effective["state"]["auto_energy_source_ids"], ["battery", "hybrid"])
        self.assertEqual(
            config_effective["state"]["auto_energy_source_profiles"],
            {"battery": "dbus-battery", "hybrid": "huawei_ma_native_ap"},
        )
        self.assertEqual(
            config_effective["state"]["auto_energy_source_profile_details"]["hybrid"]["vendor_name"],
            "Huawei",
        )
        self.assertEqual(
            config_effective["state"]["auto_energy_source_profile_details"]["hybrid"]["platform"],
            "MA",
        )
        self.assertEqual(
            config_effective["state"]["auto_energy_source_profile_details"]["hybrid"]["access_mode"],
            "native_ap",
        )
        self.assertEqual(
            config_effective["state"]["companion_pvinverter_service_name"],
            "com.victronenergy.pvinverter.external_101",
        )
        self.assertEqual(
            config_effective["state"]["companion_grid_service_name"],
            "com.victronenergy.grid.external_102",
        )
        self.assertEqual(
            config_effective["state"]["companion_source_pvinverter_service_prefix"],
            "com.victronenergy.pvinverter.external",
        )
        self.assertEqual(
            config_effective["state"]["companion_source_grid_service_prefix"],
            "com.victronenergy.grid.external",
        )
        self.assertEqual(health["kind"], "health")
        self.assertEqual(health["state"]["command_audit_entries"], 0)
        self.assertEqual(health["state"]["command_audit_path"], "/run/control-audit.jsonl")
        self.assertEqual(health["state"]["idempotency_entries"], 0)
        self.assertEqual(health["state"]["idempotency_path"], "/run/control-idempotency.json")
        self.assertFalse(health["state"]["update_stale"])
        self.assertEqual(healthz["kind"], "healthz")
        self.assertTrue(healthz["state"]["alive"])
        self.assertEqual(version["kind"], "version")
        self.assertEqual(version["state"]["service_version"], "FW-1")
        self.assertEqual(build["kind"], "build")
        self.assertEqual(build["state"]["hardware_version"], "HW-1")
        self.assertEqual(contracts["kind"], "contracts")
        self.assertEqual(contracts["state"]["openapi_endpoint"], "/v1/openapi.json")
        self.assertIn("summary", snapshot)
        self.assertIn("health", snapshot)
        self.assertTrue(service._control_api_state_token())

    def test_control_api_mixin_health_payload_uses_stale_callback_and_event_bus_is_reused(self):
        service = _ControlService()
        service._last_health_reason = "init"
        service._is_update_stale = lambda now: now >= 0.0
        service.control_api_audit_path = "/run/control-audit.jsonl"
        service.control_api_audit_max_entries = 2
        service.control_api_idempotency_path = "/run/control-idempotency.json"
        service.control_api_idempotency_max_entries = 2

        health = service._state_api_health_payload()
        event_bus_a = service._control_api_event_bus()
        event_bus_b = service._control_api_event_bus()
        audit_a = service._control_api_audit_trail()
        audit_b = service._control_api_audit_trail()
        idem_a = service._control_api_idempotency_store()
        idem_b = service._control_api_idempotency_store()
        rate_a = service._control_api_rate_limiter()
        rate_b = service._control_api_rate_limiter()

        self.assertTrue(health["state"]["update_stale"])
        self.assertIs(event_bus_a, event_bus_b)
        self.assertIs(audit_a, audit_b)
        self.assertIs(idem_a, idem_b)
        self.assertIs(rate_a, rate_b)

    def test_control_api_state_token_changes_when_snapshot_changes(self):
        service = _ControlService()
        first_token = service._control_api_state_token()

        service.virtual_mode = 2

        second_token = service._control_api_state_token()

        self.assertNotEqual(first_token, second_token)

    def test_control_api_mixin_records_runtime_only_command_audit_entries(self):
        service = _ControlService()
        service.control_api_audit_path = "/run/control-audit.jsonl"
        service.control_api_audit_max_entries = 2
        service.control_api_idempotency_path = "/run/control-idempotency.json"
        service.control_api_idempotency_max_entries = 2
        command = ControlCommand(name="set_mode", path="/Mode", value=1, source="http", command_id="cmd-1")

        entry = service._record_control_api_command_audit(
            command=command,
            result={"status": "applied", "accepted": True},
            error=None,
            replayed=False,
            scope="control",
            client_host="127.0.0.1",
            status_code=200,
        )

        self.assertEqual(entry["seq"], 1)
        self.assertEqual(entry["command"]["name"], "set_mode")
        self.assertEqual(service._control_api_audit_trail().count(), 1)
        service._control_api_idempotency_store().put("idem-1", "fp", 200, {"ok": True})
        self.assertEqual(service._control_api_idempotency_store().count(), 1)

    def test_control_api_mixin_audit_payload_helpers_cover_dict_and_none_inputs(self):
        self.assertEqual(_ControlService._audit_command_payload({"name": "set_mode"}, "http"), {"name": "set_mode"})
        self.assertEqual(_ControlService._audit_command_payload(None, "http"), {})
        self.assertEqual(_ControlService._audit_result_payload({"status": "applied"}), {"status": "applied"})
        self.assertEqual(_ControlService._audit_result_payload(None), {})
        object_payload = _ControlService._audit_result_payload(
            SimpleNamespace(
                status="applied",
                accepted=True,
                applied=True,
                persisted=False,
                reversible_failure=False,
                external_side_effect_started=True,
                detail="ok",
            )
        )
        self.assertEqual(object_payload["status"], "applied")
        self.assertTrue(object_payload["external_side_effect_started"])

    def test_control_api_mixin_skips_disabled_server_and_can_create_one(self):
        service = _ControlService()
        service.control_api_enabled = False
        service._start_control_api_server()
        self.assertFalse(hasattr(service, "_control_api_server"))

        service.control_api_enabled = True
        service.control_api_host = "127.0.0.1"
        service.control_api_port = 8765
        service.control_api_auth_token = "token"

        fake_server = MagicMock(bound_host="127.0.0.1", bound_port=8765)
        with patch("venus_evcharger.service.control.LocalControlApiHttpServer", return_value=fake_server) as factory:
            service._start_control_api_server()

        factory.assert_called_once_with(
            service,
            host="127.0.0.1",
            port=8765,
            auth_token="token",
            read_token="",
            control_token="",
            admin_token="",
            update_token="",
            localhost_only=True,
            unix_socket_path="",
        )
        fake_server.start.assert_called_once_with()

        empty_service = _ControlService()
        empty_service._stop_control_api_server()

    def test_update_cycle_mixin_delegates_all_calls(self):
        service = _UpdateService()
        service._update_controller = MagicMock()

        service._ensure_virtual_state_defaults()
        service._session_state_from_status(2, 1.5, True, 100.0)
        service._startstop_display_for_state(True)
        service._phase_energies_for_total(2.5)
        service._publish_virtual_state_paths(3.0, 4, 5.0, 1, 100.0)
        service._update_virtual_state(2, 1.5, True)
        service._prepare_update_cycle(100.0)
        service._resolve_pm_status_for_update({"pm_status": {}}, 100.0)
        service._publish_offline_update(100.0)
        service._extract_pm_measurements({"output": True})
        service._resolve_cached_input_value(1.0, 90.0, "_last", "_last_at", 100.0, max_age_seconds=30.0)
        service._resolve_auto_inputs({"captured_at": 100.0}, 100.0, True)
        service._log_auto_relay_change(True)
        service._apply_relay_decision(True, False, {"output": False}, 0.0, 0.0, 100.0, True)
        service._derive_status_code(True, 2000.0, True)
        service._publish_online_update({"output": True}, 2, 1.5, True, 2000.0, 230.0, 100.0)
        service._complete_update_cycle(True, 100.0, True, 2000.0, 8.7, 2, 2500.0, 55.0, -1800.0)
        service._sign_of_life()
        service._update()

        controller = service._update_controller
        controller.ensure_virtual_state_defaults.assert_called_once_with()
        controller.session_state_from_status.assert_called_once_with(service, 2, 1.5, True, 100.0)
        controller.startstop_display_for_state.assert_called_once_with(service, True)
        controller.phase_energies_for_total.assert_called_once_with(service, 2.5)
        controller.publish_virtual_state_paths.assert_called_once_with(3.0, 4, 5.0, 1, 100.0)
        controller.update_virtual_state.assert_called_once_with(2, 1.5, True)
        controller.prepare_update_cycle.assert_called_once_with(service, 100.0)
        controller.resolve_pm_status_for_update.assert_called_once_with(service, {"pm_status": {}}, 100.0)
        controller.publish_offline_update.assert_called_once_with(100.0)
        controller.extract_pm_measurements.assert_called_once_with(service, {"output": True})
        controller.resolve_cached_input_value.assert_called_once_with(
            service,
            1.0,
            90.0,
            "_last",
            "_last_at",
            100.0,
            max_age_seconds=30.0,
        )
        controller.resolve_auto_inputs.assert_called_once_with({"captured_at": 100.0}, 100.0, True)
        controller.log_auto_relay_change.assert_called_once_with(service, True)
        controller.apply_relay_decision.assert_called_once_with(True, False, {"output": False}, 0.0, 0.0, 100.0, True)
        controller.derive_status_code.assert_called_once_with(service, True, 2000.0, True)
        controller.publish_online_update.assert_called_once_with({"output": True}, 2, 1.5, True, 2000.0, 230.0, 100.0)
        controller.complete_update_cycle.assert_called_once_with(
            service,
            True,
            100.0,
            True,
            2000.0,
            8.7,
            2,
            2500.0,
            55.0,
            -1800.0,
        )
        controller.sign_of_life.assert_called_once_with()
        controller.update.assert_called_once_with()

    def test_state_publish_mixin_delegates_state_and_publish_calls(self):
        service = _StateService()
        service._state_controller = MagicMock()
        service._dbus_publisher = MagicMock()
        service._companion_dbus_bridge = MagicMock()

        service._state_summary()
        service._current_runtime_state()
        service._load_runtime_state()
        service._save_runtime_state()
        service._save_runtime_overrides()
        service._flush_runtime_overrides(100.0)
        service._validate_runtime_config()
        service._load_config()
        service._ensure_dbus_publish_state()
        service._publish_dbus_path("/Path", 1, 100.0, force=True)
        service._bump_update_index(100.0)
        service._publish_live_measurements(1000.0, 230.0, 4.3, {"L1": {}}, 100.0)
        service._publish_energy_time_measurements(1.2, {"L1": 1.2}, 10, 0.3, 100.0)
        service._publish_config_paths(1, 100.0)
        service._publish_diagnostic_paths(100.0)
        service._start_companion_dbus_bridge()
        service._publish_companion_dbus_bridge(100.0)
        service._stop_companion_dbus_bridge()

        state = service._state_controller
        state.state_summary.assert_called_once_with()
        state.current_runtime_state.assert_called_once_with()
        state.load_runtime_state.assert_called_once_with()
        state.save_runtime_state.assert_called_once_with()
        state.save_runtime_overrides.assert_called_once_with()
        state.flush_runtime_overrides.assert_called_once_with(100.0)
        state.validate_runtime_config.assert_called_once_with()
        state.load_config.assert_called_once_with()
        publisher = service._dbus_publisher
        publisher.ensure_state.assert_called_once_with()
        publisher.publish_path.assert_called_once_with("/Path", 1, 100.0, force=True)
        publisher.bump_update_index.assert_called_once_with(100.0)
        publisher.publish_live_measurements.assert_called_once_with(1000.0, 230.0, 4.3, {"L1": {}}, 100.0)
        publisher.publish_energy_time_measurements.assert_called_once_with(1.2, {"L1": 1.2}, 10, 0.3, 100.0)
        publisher.publish_config_paths.assert_called_once_with(1, 100.0)
        publisher.publish_diagnostic_paths.assert_called_once_with(100.0)
        service._companion_dbus_bridge.start.assert_called_once_with()
        service._companion_dbus_bridge.publish.assert_called_once_with(100.0)
        service._companion_dbus_bridge.stop.assert_called_once_with()
        self.assertTrue(service._config_path().endswith("/config.venus_evcharger.ini"))
        self.assertEqual(service._coerce_runtime_int("7"), 7)
        self.assertEqual(service._coerce_runtime_float("1.5"), 1.5)
        self.assertIn("captured_at", service._empty_worker_snapshot())
        cloned = service._clone_worker_snapshot({"captured_at": 1.0, "pm_status": {"output": True}})
        self.assertEqual(cloned["pm_status"], {"output": True})
        defaults = service._observability_state_defaults()
        self.assertIn("_error_state", defaults)

    def test_service_controller_factory_skips_recreating_existing_controllers(self):
        service = _FactoryService()
        existing = object()
        service._dbus_publisher = existing
        service._auto_controller = existing
        service._shelly_io_controller = existing
        service._state_controller = existing
        service._write_controller = existing
        service._auto_input_supervisor = existing
        service._runtime_support_controller = existing
        service._dbus_input_controller = existing
        service._bootstrap_controller = existing
        service._update_controller = existing
        service._companion_dbus_bridge = existing

        service._ensure_dbus_publisher()
        service._ensure_auto_controller()
        service._ensure_shelly_io_controller()
        service._ensure_state_controller()
        service._ensure_write_controller()
        service._ensure_auto_input_supervisor()
        service._ensure_runtime_support_controller()
        service._ensure_dbus_input_controller()
        service._ensure_bootstrap_controller()
        service._ensure_update_controller()
        service._ensure_companion_dbus_bridge()

        self.assertIs(service._dbus_publisher, existing)
        self.assertIs(service._auto_controller, existing)
        self.assertIs(service._shelly_io_controller, existing)
        self.assertIs(service._state_controller, existing)
        self.assertIs(service._write_controller, existing)
        self.assertIs(service._auto_input_supervisor, existing)
        self.assertIs(service._runtime_support_controller, existing)
        self.assertIs(service._dbus_input_controller, existing)
        self.assertIs(service._bootstrap_controller, existing)
        self.assertIs(service._update_controller, existing)
        self.assertIs(service._companion_dbus_bridge, existing)

    def test_service_controller_factory_creates_companion_bridge_once(self):
        service = _FactoryService()

        with patch("venus_evcharger.service.factory.EnergyCompanionDbusBridge", return_value="bridge") as factory:
            service._ensure_companion_dbus_bridge()
            service._ensure_companion_dbus_bridge()

        factory.assert_called_once_with(service, "")
        self.assertEqual(service._companion_dbus_bridge, "bridge")

    def test_auto_logic_mixin_delegates_and_exposes_static_helpers(self):
        service = _AutoService()
        service._mode_uses_auto_logic_func = MagicMock(return_value=True)
        service._normalize_mode_func = MagicMock(return_value=1)
        service._dbus_input_controller = MagicMock()
        service._auto_controller = MagicMock()
        service._write_controller = MagicMock()
        service._bootstrap_controller = MagicMock()

        self.assertEqual(service._get_available_surplus_watts(2500, -1800), 1800.0)
        self.assertTrue(service._mode_uses_auto_logic(1))
        self.assertEqual(service._normalize_mode("1"), 1)
        service._get_dbus_value("svc", "/Path")
        service._list_dbus_services()
        service._invalidate_auto_pv_services()
        service._invalidate_auto_battery_service()
        service._resolve_auto_pv_services()
        service._get_pv_power()
        service._resolve_auto_battery_service()
        service._get_battery_soc()
        service._get_grid_power()
        service._add_auto_sample(100.0, 2000.0, -1800.0)
        service._clear_auto_samples()
        service._average_auto_metric(1)
        service._mark_relay_changed(True, 100.0)
        service._is_within_auto_daytime_window()
        service._set_health("running", cached=True)
        service._auto_decide_relay(False, 2200.0, 55.0, -1800.0)
        service._handle_write("/Mode", 1)
        service._control_command_from_write("/AutoStart", 1, source="mqtt")
        service._handle_control_command(ControlCommand(name="set_mode", path="/Mode", value=1))
        service._register_paths()
        service._fetch_device_info_with_fallback()

        service._mode_uses_auto_logic_func.assert_called_once_with(1)
        service._normalize_mode_func.assert_called_once_with("1")
        dbus_input = service._dbus_input_controller
        dbus_input.get_dbus_value.assert_called_once_with("svc", "/Path")
        dbus_input.list_dbus_services.assert_called_once_with()
        dbus_input.invalidate_auto_pv_services.assert_called_once_with()
        dbus_input.invalidate_auto_battery_service.assert_called_once_with()
        dbus_input.resolve_auto_pv_services.assert_called_once_with()
        dbus_input.get_pv_power.assert_called_once_with()
        dbus_input.resolve_auto_battery_service.assert_called_once_with()
        dbus_input.get_battery_soc.assert_called_once_with()
        dbus_input.get_grid_power.assert_called_once_with()
        auto = service._auto_controller
        auto.add_auto_sample.assert_called_once_with(100.0, 2000.0, -1800.0)
        auto.clear_auto_samples.assert_called_once_with()
        auto.average_auto_metric.assert_called_once_with(1)
        auto.mark_relay_changed.assert_called_once_with(True, 100.0)
        auto.is_within_auto_daytime_window.assert_called_once_with(None)
        auto.set_health.assert_called_once_with("running", True)
        auto.auto_decide_relay.assert_called_once_with(False, 2200.0, 55.0, -1800.0)
        service._write_controller.build_control_command.assert_any_call("/Mode", 1, source="dbus")
        service._write_controller.build_control_command.assert_any_call("/AutoStart", 1, source="mqtt")
        service._write_controller.handle_control_command.assert_any_call(
            service._write_controller.build_control_command.return_value
        )
        service._write_controller.handle_control_command.assert_any_call(
            ControlCommand(name="set_mode", path="/Mode", value=1)
        )
        service._bootstrap_controller.register_paths.assert_called_once_with()
        service._bootstrap_controller.fetch_device_info_with_fallback.assert_called_once_with()
