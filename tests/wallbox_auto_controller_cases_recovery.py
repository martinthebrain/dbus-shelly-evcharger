# SPDX-License-Identifier: GPL-3.0-or-later
from tests.wallbox_auto_controller_support import (
    AutoDecisionController,
    AutoDecisionControllerTestCase,
    MagicMock,
    SimpleNamespace,
    _health_code,
    _mode_uses_auto_logic,
    make_auto_controller_service,
    patch,
)


class TestAutoDecisionControllerRecovery(AutoDecisionControllerTestCase):
    def test_average_metrics_records_active_threshold_profile_for_diagnostics(self):
        controller, service = self._make_controller()
        service._add_auto_sample = MagicMock()
        service._average_auto_metric = MagicMock(side_effect=[1700.0, 50.0])

        controller._update_average_metrics(100.0, 2500.0, -1800.0, 55.0, False)

        self.assertEqual(service._last_auto_metrics["profile"], "high-soc")
        self.assertEqual(service._last_auto_metrics["start_threshold"], 1650.0)
        self.assertEqual(service._last_auto_metrics["stop_threshold"], 800.0)
        self.assertEqual(service._last_auto_metrics["learned_charge_power"], None)
        self.assertEqual(service._last_auto_metrics["learned_charge_power_state"], "unknown")
        self.assertEqual(service._last_auto_metrics["threshold_scale"], 1.0)
        self.assertEqual(service._last_auto_metrics["threshold_mode"], "static")
        self.assertEqual(service._last_auto_metrics["stop_alpha"], 0.25)
        self.assertEqual(service._last_auto_metrics["stop_alpha_stage"], "base")
        self.assertIsNone(service._last_auto_metrics["surplus_volatility"])

    def test_average_metrics_records_scaled_thresholds_when_learned_power_is_available(self):
        controller, service = self._make_controller()
        service.learned_charge_power_watts = 2280.0
        service.learned_charge_power_updated_at = 995.0
        service.learned_charge_power_state = "stable"
        service._add_auto_sample = MagicMock()
        service._average_auto_metric = MagicMock(side_effect=[2100.0, 40.0])

        controller._update_average_metrics(100.0, 2600.0, -2200.0, 55.0, False)

        self.assertEqual(service._last_auto_metrics["profile"], "high-soc")
        self.assertEqual(service._last_auto_metrics["start_threshold"], 1980.0)
        self.assertEqual(service._last_auto_metrics["stop_threshold"], 960.0)
        self.assertEqual(service._last_auto_metrics["learned_charge_power"], 2280.0)
        self.assertEqual(service._last_auto_metrics["learned_charge_power_state"], "stable")
        self.assertEqual(service._last_auto_metrics["threshold_scale"], 1.2)
        self.assertEqual(service._last_auto_metrics["threshold_mode"], "adaptive")

    def test_average_metrics_falls_back_to_static_thresholds_when_learned_value_is_stale(self):
        controller, service = self._make_controller()
        service.learned_charge_power_watts = 2280.0
        service.learned_charge_power_updated_at = 100.0
        service.learned_charge_power_state = "stable"
        service.auto_learn_charge_power_max_age_seconds = 60.0
        service._add_auto_sample = MagicMock()
        service._average_auto_metric = MagicMock(side_effect=[2100.0, 40.0])

        controller._update_average_metrics(1000.0, 2600.0, -2200.0, 55.0, False)

        self.assertEqual(service._last_auto_metrics["start_threshold"], 1650.0)
        self.assertEqual(service._last_auto_metrics["stop_threshold"], 800.0)
        self.assertIsNone(service._last_auto_metrics["learned_charge_power"])
        self.assertEqual(service._last_auto_metrics["learned_charge_power_state"], "stale")
        self.assertEqual(service._last_auto_metrics["threshold_scale"], 1.0)
        self.assertEqual(service._last_auto_metrics["threshold_mode"], "static")

    def test_learning_state_blocks_adaptive_thresholds_until_value_is_stable(self):
        controller, service = self._make_controller()
        service.learned_charge_power_watts = 2280.0
        service.learned_charge_power_updated_at = 995.0
        service.learned_charge_power_state = "learning"
        service._add_auto_sample = MagicMock()
        service._average_auto_metric = MagicMock(side_effect=[2100.0, 40.0])

        controller._update_average_metrics(1000.0, 2600.0, -2200.0, 55.0, False)

        self.assertEqual(service._last_auto_metrics["start_threshold"], 1650.0)
        self.assertEqual(service._last_auto_metrics["stop_threshold"], 800.0)
        self.assertIsNone(service._last_auto_metrics["learned_charge_power"])
        self.assertEqual(service._last_auto_metrics["learned_charge_power_state"], "learning")
        self.assertEqual(service._last_auto_metrics["threshold_mode"], "static")

    def test_stop_timer_resets_when_reason_changes_from_grid_to_surplus(self):
        controller, service = self._make_controller()
        service.auto_stop_condition_since = 100.0
        service.auto_stop_condition_reason = "auto-stop-grid"

        self.assertTrue(
            controller._pending_stop_or_running(
                110.0,
                "auto-stop",
                False,
                "running",
                delay_seconds=90.0,
                stop_key="auto-stop-surplus",
            )
        )
        self.assertEqual(service.auto_stop_condition_since, 110.0)
        self.assertEqual(service.auto_stop_condition_reason, "auto-stop-surplus")

    def test_waiting_health_and_relay_off_cover_grid_wait_default_wait_and_autostart_disabled(self):
        controller, service = self._make_controller()

        controller._set_waiting_health(True, True, 2500.0, 100.0, 60.0, False)
        self.assertEqual(service._last_health_reason, "waiting-grid")

        controller._set_waiting_health(True, True, 2500.0, 0.0, 40.0, False)
        self.assertEqual(service._last_health_reason, "waiting-soc")

        controller._set_waiting_health(True, True, 2500.0, 0.0, 60.0, False)
        self.assertEqual(service._last_health_reason, "waiting")

        service.virtual_autostart = 0
        self.assertFalse(controller._handle_relay_off(2500.0, 0.0, 60.0, True, 100.0, False))
        self.assertEqual(service._last_health_reason, "autostart-disabled")

    def test_pre_average_and_auto_decide_cover_terminal_and_averaging_paths(self):
        controller, service = self._make_controller()

        with patch.object(controller, "_resolve_battery_soc", return_value=(None, True)):
            decision, battery_soc = controller._pre_average_decision(False, 2200.0, 55.0, -2100.0, 100.0, False)
        self.assertTrue(decision)
        self.assertIsNone(battery_soc)

        with patch("shelly_wallbox.auto.workflow.time.time", return_value=100.0):
            with patch.object(controller, "_pre_average_decision", return_value=(controller._NO_DECISION, 55.0)):
                with patch.object(controller, "_update_average_metrics", return_value=(None, None)):
                    self.assertTrue(controller.auto_decide_relay(True, 2200.0, 55.0, -2100.0))
        self.assertEqual(service._last_health_reason, "averaging")

    def test_grid_recovery_gate_blocks_restart_until_grid_is_stable_again(self):
        controller, service = self._make_controller()

        self.assertFalse(controller._handle_grid_missing(False, 100.0, False))
        self.assertEqual(service._last_health_reason, "grid-missing")
        self.assertTrue(service._grid_recovery_required)
        self.assertIsNone(service._grid_recovery_since)

        decision, battery_soc = controller._pre_average_decision(False, 2200.0, 55.0, -2100.0, 101.0, False)
        self.assertFalse(decision)
        self.assertIsNone(battery_soc)
        self.assertEqual(service._last_health_reason, "waiting-grid-recovery")
        self.assertEqual(service._grid_recovery_since, 101.0)

        decision, battery_soc = controller._pre_average_decision(False, 2200.0, 55.0, -2100.0, 105.0, False)
        self.assertFalse(decision)
        self.assertIsNone(battery_soc)
        self.assertEqual(service._last_health_reason, "waiting-grid-recovery")

        decision, battery_soc = controller._pre_average_decision(False, 2200.0, 55.0, -2100.0, 111.0, False)
        self.assertIs(decision, controller._NO_DECISION)
        self.assertEqual(battery_soc, 55.0)
        self.assertFalse(service._grid_recovery_required)

    def test_grid_recovery_gate_is_not_armed_on_clean_start_without_prior_grid_loss(self):
        controller, service = self._make_controller()

        decision, battery_soc = controller._pre_average_decision(False, 2200.0, 55.0, -2100.0, 101.0, False)

        self.assertIs(decision, controller._NO_DECISION)
        self.assertEqual(battery_soc, 55.0)
        self.assertFalse(service._grid_recovery_required)
        self.assertIsNone(service._grid_recovery_since)

    def test_grid_recovery_gate_does_not_block_running_relay(self):
        controller, service = self._make_controller()

        controller._handle_grid_missing(True, 100.0, False)

        decision, battery_soc = controller._pre_average_decision(True, 2200.0, 55.0, -2100.0, 101.0, False)
        self.assertIs(decision, controller._NO_DECISION)
        self.assertEqual(battery_soc, 55.0)
        self.assertTrue(service._grid_recovery_required)
        self.assertEqual(service._grid_recovery_since, 101.0)

    def test_cutover_pending_stays_blocked_until_relay_off_is_confirmed(self):
        controller, service = self._make_controller()
        service._auto_mode_cutover_pending = True
        service._last_pm_status_confirmed = False
        service._peek_pending_relay_command = MagicMock(return_value=(None, None))

        decision = controller._handle_cutover_pending(False, False)

        self.assertFalse(decision)
        self.assertTrue(service._auto_mode_cutover_pending)
        self.assertFalse(service._ignore_min_offtime_once)
        self.assertEqual(service._last_health_reason, "mode-transition")
        service._save_runtime_state.assert_not_called()

    def test_cutover_pending_clears_only_after_confirmed_relay_off(self):
        controller, service = self._make_controller()
        service._auto_mode_cutover_pending = True
        service._last_pm_status_confirmed = True
        service._last_confirmed_pm_status = {"output": False}
        service._last_confirmed_pm_status_at = 999.5
        service._relay_sync_requested_at = 999.0
        service._peek_pending_relay_command = MagicMock(return_value=(None, None))

        decision = controller._handle_cutover_pending(False, False)

        self.assertIs(decision, controller._NO_DECISION)
        self.assertFalse(service._auto_mode_cutover_pending)
        self.assertTrue(service._ignore_min_offtime_once)
        service._save_runtime_state.assert_called_once()

    def test_cutover_pending_ignores_confirmed_off_sample_from_before_cutover_request(self):
        controller, service = self._make_controller()
        service._auto_mode_cutover_pending = True
        service._last_pm_status_confirmed = True
        service._last_confirmed_pm_status = {"output": False}
        service._last_confirmed_pm_status_at = 998.0
        service._relay_sync_requested_at = 999.0
        service._peek_pending_relay_command = MagicMock(return_value=(None, None))

        decision = controller._handle_cutover_pending(False, False)

        self.assertFalse(decision)
        self.assertTrue(service._auto_mode_cutover_pending)
        self.assertFalse(service._ignore_min_offtime_once)
        self.assertEqual(service._last_health_reason, "mode-transition")
        service._save_runtime_state.assert_not_called()

    def test_cutover_pending_uses_fallback_last_pm_status_when_confirmed_cache_is_missing(self):
        controller, service = self._make_controller()
        service._auto_mode_cutover_pending = True
        service._peek_pending_relay_command = MagicMock(return_value=(None, None))
        service._last_confirmed_pm_status = None
        service._last_confirmed_pm_status_at = None
        service._last_pm_status_confirmed = True
        service._last_pm_status = {"output": False}
        service._last_pm_status_at = 999.5
        service._relay_sync_requested_at = 999.0
        controller._learning_policy_now = MagicMock(return_value=1000.0)

        decision = controller._handle_cutover_pending(False, False)

        self.assertIs(decision, controller._NO_DECISION)
        self.assertFalse(service._auto_mode_cutover_pending)
        self.assertTrue(service._ignore_min_offtime_once)

    def test_cutover_confirmed_helpers_cover_missing_and_stale_timestamps(self):
        controller, service = self._make_controller()
        service._relay_sync_requested_at = 999.0
        service._worker_poll_interval_seconds = 1.0
        service.relay_sync_timeout_seconds = 2.0

        self.assertFalse(controller._cutover_confirmed_sample_fresh(None, 1000.0))
        self.assertTrue(controller._cutover_confirmed_sample_fresh(999.5, 1000.0))
        self.assertFalse(controller._cutover_confirmed_after_request(None))
        self.assertFalse(controller._cutover_confirmed_after_request(998.0))

    def test_cutover_confirmed_after_request_accepts_missing_request_timestamp(self):
        controller, service = self._make_controller()
        service._relay_sync_requested_at = None

        self.assertTrue(controller._cutover_confirmed_after_request(998.0))

    def test_learned_charge_power_age_helpers_cover_missing_update_timestamp(self):
        controller, service = self._make_controller()
        service.learned_charge_power_updated_at = None

        self.assertIsNone(controller._learned_charge_power_age_seconds(1000.0))
        self.assertTrue(controller._learned_charge_power_expired(1000.0))

    def test_surplus_thresholds_fall_back_to_static_profile_when_scaled_thresholds_become_invalid(self):
        controller, _service = self._make_controller()
        with patch.object(controller, "_scale_surplus_thresholds", return_value=(800.0, 1200.0)):
            self.assertEqual(controller._surplus_thresholds_for_soc(55.0), (1650.0, 800.0, "high-soc"))

    def test_auto_policy_synthesis_tolerates_read_only_service_attributes(self):
        class LockedAutoPolicyService(SimpleNamespace):
            def __setattr__(self, name, value):
                if name == "auto_policy":
                    raise AttributeError("read-only")
                super().__setattr__(name, value)

        service = LockedAutoPolicyService(**vars(make_auto_controller_service()))
        controller = AutoDecisionController(service, _health_code, _mode_uses_auto_logic)

        policy = controller._auto_policy()

        self.assertEqual(policy.normal_profile.start_surplus_watts, 2000.0)
        self.assertFalse(hasattr(service, "auto_policy"))

    def test_grid_recovery_gate_clears_immediately_when_delay_is_zero(self):
        controller, service = self._make_controller()
        service.auto_grid_recovery_start_seconds = 0.0
        service._grid_recovery_required = True
        service._grid_recovery_since = None

        decision = controller._handle_grid_recovery_start_gate(False, 123.0, False)

        self.assertIs(decision, controller._NO_DECISION)
        self.assertFalse(service._grid_recovery_required)
        self.assertEqual(service._grid_recovery_since, 123.0)

    def test_grid_recovery_gate_keeps_running_relay_unchanged_while_recovery_window_is_open(self):
        controller, service = self._make_controller()
        service.auto_grid_recovery_start_seconds = 30.0
        service._grid_recovery_required = True
        service._grid_recovery_since = 110.0

        decision = controller._handle_grid_recovery_start_gate(True, 123.0, False)

        self.assertIs(decision, controller._NO_DECISION)
        self.assertTrue(service._grid_recovery_required)
        self.assertEqual(service._grid_recovery_since, 110.0)

    def test_missing_battery_soc_helpers_cover_suppressed_warning_and_invalid_value_without_callback(self):
        controller, service = self._make_controller()
        service._last_battery_allow_warning = 995.0
        service.auto_battery_scan_interval_seconds = 10.0
        service._warning_throttled = None

        with patch("shelly_wallbox.auto.logic_gates.logging.warning") as warning_mock:
            soc, decision = controller._allowed_missing_battery_soc(False, 1000.0, False)

        self.assertEqual(soc, float(controller._auto_policy().resume_soc))
        self.assertIs(decision, controller._NO_DECISION)
        warning_mock.assert_not_called()
        self.assertIsNone(controller._normalized_battery_soc(120.0, 1000.0))
