# SPDX-License-Identifier: GPL-3.0-or-later
from collections import deque
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from shelly_wallbox.controllers.auto import AutoDecisionController
from tests.wallbox_test_fixtures import make_auto_controller_service


def _health_code(reason: str) -> int:
    return {
        "grid-missing": 1,
        "inputs-missing": 2,
        "auto-start": 3,
        "battery-soc-missing": 4,
        "battery-soc-missing-allowed": 5,
        "waiting-grid": 6,
        "waiting": 7,
        "autostart-disabled": 8,
        "averaging": 9,
        "mode-transition": 10,
        "waiting-grid-recovery": 11,
        "scheduled-night-charge": 12,
    }.get(reason, 99)


def _mode_uses_auto_logic(mode) -> bool:
    return int(mode) in (1, 2)


class TestAutoDecisionController(unittest.TestCase):
    def _make_controller(self):
        service = make_auto_controller_service()
        controller = AutoDecisionController(service, _health_code, _mode_uses_auto_logic)
        service._clear_auto_samples = controller.clear_auto_samples
        service._set_health = controller.set_health
        service._get_available_surplus_watts = controller.get_available_surplus_watts
        service._add_auto_sample = controller.add_auto_sample
        service._average_auto_metric = controller.average_auto_metric
        service._is_within_auto_daytime_window = lambda: True
        return controller, service

    def test_metric_helpers_and_relay_change_tracking_cover_empty_and_time_default(self):
        controller, service = self._make_controller()

        self.assertIsNone(controller.average_auto_metric(1))

        with patch("shelly_wallbox.auto.workflow.time.time", return_value=123.0):
            controller.mark_relay_changed(False)

        self.assertEqual(service.relay_last_changed_at, 123.0)
        self.assertEqual(service.relay_last_off_at, 123.0)

    def test_auto_decide_relay_uses_service_time_source(self):
        controller, service = self._make_controller()
        service._time_now = lambda: 150.0
        service.relay_last_off_at = 140.0
        service.auto_min_offtime_seconds = 20.0
        service.auto_samples = deque([(149.0, 2500.0, -2500.0)])

        with patch("shelly_wallbox.auto.workflow.time.time", return_value=999.0):
            self.assertFalse(controller.auto_decide_relay(False, 2500.0, 60.0, -2500.0))

        self.assertEqual(service._last_health_reason, "waiting-offtime")

    def test_daytime_window_handles_equal_and_wraparound_ranges(self):
        controller, service = self._make_controller()
        service.auto_daytime_only = True
        service.auto_month_windows = {
            4: ((8, 0), (8, 0)),
            5: ((20, 0), (6, 0)),
        }

        self.assertTrue(controller.is_within_auto_daytime_window(datetime(2026, 4, 4, 8, 0)))
        self.assertTrue(controller.is_within_auto_daytime_window(datetime(2026, 5, 4, 2, 0)))

    def test_scheduled_mode_starts_charging_one_hour_after_daytime_window_ends(self):
        controller, service = self._make_controller()
        service.virtual_mode = 2
        service.virtual_autostart = 1
        service.auto_month_windows = {4: ((7, 30), (19, 30))}
        service.auto_scheduled_enabled_days = "Mon,Tue,Wed,Thu,Fri"
        service.auto_scheduled_night_start_delay_seconds = 3600.0
        service.auto_scheduled_latest_end_time = "06:30"
        service._time_now = lambda: datetime(2026, 4, 20, 20, 45).timestamp()

        self.assertTrue(controller.auto_decide_relay(False, None, None, None))
        self.assertEqual(service._last_health_reason, "scheduled-night-charge")
        self.assertEqual(service._last_auto_state, "charging")

    def test_scheduled_mode_keeps_running_overnight_despite_missing_inputs(self):
        controller, service = self._make_controller()
        service.virtual_mode = 2
        service.virtual_autostart = 1
        service.auto_month_windows = {4: ((7, 30), (19, 30))}
        service.auto_scheduled_enabled_days = "Mon,Tue,Wed,Thu,Fri"
        service.auto_scheduled_night_start_delay_seconds = 3600.0
        service.auto_scheduled_latest_end_time = "06:30"
        service._time_now = lambda: datetime(2026, 4, 21, 3, 0).timestamp()

        self.assertTrue(controller.auto_decide_relay(True, None, None, None))
        self.assertEqual(service._last_health_reason, "scheduled-night-charge")
        self.assertEqual(service._last_auto_state, "charging")

    def test_scheduled_mode_respects_enabled_target_days(self):
        controller, service = self._make_controller()
        service.virtual_mode = 2
        service.virtual_autostart = 1
        service.auto_month_windows = {4: ((7, 30), (19, 30))}
        service.auto_scheduled_enabled_days = "Mon,Tue,Wed,Thu,Fri"
        service.auto_scheduled_night_start_delay_seconds = 3600.0
        service.auto_scheduled_latest_end_time = "06:30"
        service._time_now = lambda: datetime(2026, 4, 17, 21, 0).timestamp()

        self.assertFalse(controller._scheduled_night_charge_active())
        self.assertFalse(controller.auto_decide_relay(False, None, None, None))
        self.assertNotEqual(service._last_health_reason, "scheduled-night-charge")

    def test_scheduled_mode_stops_night_boost_after_latest_end_time(self):
        controller, service = self._make_controller()
        service.virtual_mode = 2
        service.virtual_autostart = 1
        service.auto_month_windows = {4: ((7, 30), (19, 30))}
        service.auto_scheduled_enabled_days = "Mon,Tue,Wed,Thu,Fri"
        service.auto_scheduled_night_start_delay_seconds = 3600.0
        service.auto_scheduled_latest_end_time = "06:30"
        service._time_now = lambda: datetime(2026, 4, 21, 6, 45).timestamp()

        self.assertFalse(controller._scheduled_night_charge_active())

    def test_set_health_cached_updates_code_and_audit_log(self):
        controller, service = self._make_controller()
        service.auto_audit_log = True

        controller.set_health("grid-missing", cached=True)

        self.assertEqual(service._last_health_reason, "grid-missing-cached")
        self.assertEqual(service._last_health_code, 101)
        self.assertEqual(service._last_auto_state, "recovery")
        self.assertEqual(service._last_auto_state_code, 5)
        service._write_auto_audit_event.assert_called_once_with("grid-missing", True)

    def test_set_health_uses_explicit_learning_state_while_running(self):
        controller, service = self._make_controller()
        service._last_confirmed_pm_status = {"output": True}
        service._last_confirmed_pm_status_at = 995.0
        service.learned_charge_power_state = "learning"

        controller.set_health("running", cached=False)

        self.assertEqual(service._last_auto_state, "learning")
        self.assertEqual(service._last_auto_state_code, 2)
        self.assertEqual(service._last_auto_metrics["state"], "learning")
        self.assertEqual(service._last_auto_metrics["relay_intent"], 1)

    def test_set_health_postconditions_sanitize_metrics_and_follow_explicit_relay_intent(self):
        controller, service = self._make_controller()
        service._last_auto_metrics = {
            "start_threshold": 1000.0,
            "stop_threshold": 1200.0,
            "threshold_mode": 7,
            "learned_charge_power_state": "odd",
        }

        controller.set_health("running", cached=False, relay_intent=True)

        self.assertEqual(service._last_health_reason, "running")
        self.assertEqual(service._last_health_code, 99)
        self.assertEqual(service._last_auto_state, "charging")
        self.assertEqual(service._last_auto_state_code, 3)
        self.assertEqual(service._last_auto_metrics["relay_intent"], 1)
        self.assertEqual(service._last_auto_metrics["state"], "charging")
        self.assertIsNone(service._last_auto_metrics["start_threshold"])
        self.assertIsNone(service._last_auto_metrics["stop_threshold"])
        self.assertEqual(service._last_auto_metrics["threshold_mode"], "7")

    def test_observed_relay_state_ignores_stale_confirmed_or_virtual_only_state(self):
        controller, service = self._make_controller()
        service._last_confirmed_pm_status = {"output": True}
        service._last_confirmed_pm_status_at = 980.0
        service.virtual_startstop = 1

        self.assertFalse(controller._observed_relay_state())

    def test_derive_auto_state_uses_observed_relay_hint_and_learning_state(self):
        controller, service = self._make_controller()
        service._last_confirmed_pm_status = {"output": True}
        service._last_confirmed_pm_status_at = 999.0
        service.learned_charge_power_state = "learning"

        self.assertEqual(controller._derive_auto_state("custom-reason"), "learning")

    def test_battery_soc_missing_without_override_returns_terminal_decision(self):
        controller, service = self._make_controller()

        battery_soc, decision = controller._resolve_battery_soc(None, True, 100.0, False)

        self.assertIsNone(battery_soc)
        self.assertTrue(decision)
        self.assertEqual(service._last_health_reason, "battery-soc-missing")

    def test_out_of_range_battery_soc_is_treated_as_missing(self):
        controller, service = self._make_controller()
        service._warning_throttled = MagicMock()

        battery_soc, decision = controller._resolve_battery_soc(150.0, True, 100.0, False)

        self.assertIsNone(battery_soc)
        self.assertTrue(decision)
        self.assertEqual(service._last_health_reason, "battery-soc-missing")
        service._warning_throttled.assert_called_once()

    def test_stop_and_missing_input_helpers_cover_running_idle_and_none_reason(self):
        controller, service = self._make_controller()
        service.auto_stop_condition_since = 50.0
        self.assertIs(controller._arm_or_fire_stop(60.0, "grid-missing", False), controller._NO_DECISION)

        service.relay_last_changed_at = 90.0
        self.assertTrue(controller._handle_grid_missing(True, 100.0, False))
        self.assertEqual(service._last_health_reason, "grid-missing")

        service.auto_night_lock_stop = True
        self.assertEqual(controller._known_missing_input_stop_reason(60.0, None, False), "night-lock")
        service.auto_night_lock_stop = False
        self.assertIsNone(controller._known_missing_input_stop_reason(60.0, None, True))

        self.assertFalse(controller._handle_missing_inputs(False, 60.0, None, 100.0, False))
        self.assertEqual(service._last_health_reason, "inputs-missing")
        self.assertTrue(controller._handle_missing_inputs(True, 60.0, None, 100.0, False))
        self.assertEqual(service._last_health_reason, "inputs-missing")

    def test_average_metrics_and_relay_on_helpers_cover_none_night_lock_and_delayed_start(self):
        controller, service = self._make_controller()
        service._add_auto_sample = MagicMock()
        service._average_auto_metric = MagicMock(side_effect=[None, -100.0])

        self.assertEqual(controller._update_average_metrics(100.0, 2500.0, -2000.0, 60.0, False), (None, None))
        service.auto_night_lock_stop = True
        self.assertEqual(
            controller._relay_on_stop_reason(1500.0, -2000.0, 60.0, False, True),
            "night-lock",
        )
        service.auto_night_lock_stop = False
        self.assertEqual(
            controller._relay_on_stop_reason(1500.0, -2000.0, 44.0, True, True),
            "auto-stop-surplus",
        )
        self.assertEqual(
            controller._relay_on_stop_reason(1700.0, 400.0, 60.0, True, True),
            "auto-stop-grid",
        )
        self.assertEqual(
            controller._relay_on_stop_reason(1700.0, 0.0, 20.0, True, True),
            "auto-stop-soc",
        )
        service.auto_start_condition_since = 100.0
        self.assertFalse(controller._arm_or_fire_start(105.0, False))

    def test_adaptive_stop_smoothing_applies_only_while_running(self):
        controller, service = self._make_controller()
        service._add_auto_sample = MagicMock()
        service._average_auto_metric = MagicMock(side_effect=[1200.0, 400.0, 1200.0, 400.0])
        service._stop_smoothed_surplus_power = 2000.0
        service._stop_smoothed_grid_power = -200.0

        smoothed_surplus, smoothed_grid = controller._update_average_metrics(100.0, 2200.0, 400.0, 60.0, True)
        self.assertEqual(smoothed_surplus, 1800.0)
        self.assertEqual(smoothed_grid, -50.0)
        self.assertEqual(service._last_auto_metrics["raw_surplus"], 1200.0)
        self.assertEqual(service._last_auto_metrics["raw_grid"], 400.0)

        raw_surplus, raw_grid = controller._update_average_metrics(101.0, 2200.0, 400.0, 60.0, False)
        self.assertEqual(raw_surplus, 1200.0)
        self.assertEqual(raw_grid, 400.0)
        self.assertIsNone(service._stop_smoothed_surplus_power)
        self.assertIsNone(service._stop_smoothed_grid_power)

    def test_adaptive_stop_alpha_uses_stable_medium_and_volatile_stages(self):
        controller, service = self._make_controller()
        service.auto_samples = deque(
            [
                (1.0, 1000.0, 0.0),
                (2.0, 1020.0, 0.0),
                (3.0, 980.0, 0.0),
            ]
        )
        alpha, stage, volatility = controller._adaptive_stop_alpha()
        self.assertEqual(alpha, 0.55)
        self.assertEqual(stage, "stable")
        self.assertAlmostEqual(volatility, 16.33, places=2)

        service.auto_samples = deque(
            [
                (1.0, 1000.0, 0.0),
                (2.0, 1200.0, 0.0),
                (3.0, 800.0, 0.0),
            ]
        )
        alpha, stage, volatility = controller._adaptive_stop_alpha()
        self.assertEqual(alpha, 0.25)
        self.assertEqual(stage, "medium")
        self.assertGreater(volatility, 150.0)
        self.assertLess(volatility, 400.0)

        service.auto_samples = deque(
            [
                (1.0, 1000.0, 0.0),
                (2.0, 1800.0, 0.0),
                (3.0, 200.0, 0.0),
            ]
        )
        alpha, stage, volatility = controller._adaptive_stop_alpha()
        self.assertEqual(alpha, 0.15)
        self.assertEqual(stage, "volatile")
        self.assertGreaterEqual(volatility, 400.0)

    def test_surplus_stop_uses_own_delay_but_grid_stop_stays_hard(self):
        controller, service = self._make_controller()
        service.relay_last_changed_at = -1000.0
        service.auto_stop_condition_since = 100.0
        service.auto_stop_condition_reason = "auto-stop-surplus"

        self.assertTrue(controller._handle_relay_on(1000.0, 0.0, 45.0, True, 150.0, False))
        self.assertEqual(service._last_health_reason, "running")

        self.assertFalse(controller._handle_relay_on(1000.0, 0.0, 45.0, True, 191.0, False))
        self.assertEqual(service._last_health_reason, "auto-stop")

        service.auto_stop_condition_since = 100.0
        service.auto_stop_condition_reason = "auto-stop-grid"
        self.assertFalse(controller._handle_relay_on(1700.0, 400.0, 60.0, True, 131.0, False))
        self.assertEqual(service._last_health_reason, "auto-stop")

    def test_high_soc_uses_more_aggressive_start_and_stop_surplus_thresholds(self):
        controller, service = self._make_controller()

        self.assertEqual(controller._surplus_thresholds_for_soc(50.0), (2000.0, 1600.0, "normal"))
        self.assertEqual(controller._surplus_thresholds_for_soc(55.0), (1650.0, 800.0, "high-soc"))

        self.assertTrue(
            controller._relay_off_start_conditions_met(
                True,
                True,
                1700.0,
                0.0,
                55.0,
                1650.0,
                service,
            )
        )
        self.assertFalse(
            controller._relay_off_start_conditions_met(
                True,
                True,
                1700.0,
                0.0,
                50.0,
                2000.0,
                service,
            )
        )
        self.assertIsNone(controller._relay_on_stop_reason(1000.0, 0.0, 55.0, True, True))
        service._auto_high_soc_profile_active = False
        self.assertEqual(controller._relay_on_stop_reason(1000.0, 0.0, 50.0, True, True), "auto-stop-surplus")

    def test_high_soc_profile_uses_release_hysteresis_before_falling_back_to_normal_thresholds(self):
        controller, service = self._make_controller()

        self.assertEqual(controller._surplus_thresholds_for_soc(55.0), (1650.0, 800.0, "high-soc"))
        self.assertTrue(service._auto_high_soc_profile_active)
        self.assertEqual(controller._surplus_thresholds_for_soc(49.0), (1650.0, 800.0, "high-soc"))
        self.assertTrue(service._auto_high_soc_profile_active)
        self.assertEqual(controller._surplus_thresholds_for_soc(44.0), (2000.0, 1600.0, "normal"))
        self.assertFalse(service._auto_high_soc_profile_active)

    def test_non_auto_mode_sets_explicit_idle_state(self):
        controller, service = self._make_controller()
        service.virtual_mode = 0

        result = controller._handle_non_auto_mode(True)

        self.assertTrue(result)
        self.assertEqual(service._last_auto_state, "idle")
        self.assertEqual(service._last_auto_state_code, 0)

    def test_learned_charge_power_scales_normal_and_high_soc_surplus_thresholds(self):
        controller, service = self._make_controller()
        service.learned_charge_power_watts = 2280.0
        service.learned_charge_power_updated_at = 995.0
        service.learned_charge_power_state = "stable"

        self.assertEqual(controller._surplus_thresholds_for_soc(50.0), (2400.0, 1920.0, "normal"))
        self.assertEqual(controller._surplus_thresholds_for_soc(55.0), (1980.0, 960.0, "high-soc"))

    def test_learned_charge_power_scale_falls_back_to_static_when_disabled_or_invalid(self):
        controller, service = self._make_controller()
        policy = controller._auto_policy()

        policy.learn_charge_power.enabled = False
        service.learned_charge_power_watts = 2280.0
        service.learned_charge_power_updated_at = 995.0
        service.learned_charge_power_state = "stable"
        self.assertEqual(controller._learned_charge_power_scale(), 1.0)

        policy.learn_charge_power.enabled = True
        service.learned_charge_power_watts = 0.0
        self.assertEqual(controller._learned_charge_power_scale(), 1.0)

    def test_learned_charge_power_scale_ignores_stale_runtime_value(self):
        controller, service = self._make_controller()
        service.learned_charge_power_watts = 2280.0
        service.learned_charge_power_updated_at = 100.0
        service.learned_charge_power_state = "stable"
        service.auto_learn_charge_power_max_age_seconds = 60.0

        self.assertEqual(controller._learned_charge_power_scale(), 1.0)
        self.assertIsNone(controller._active_learned_charge_power())

    def test_active_learned_charge_power_covers_unbounded_age_missing_timestamp_and_time_fallback(self):
        controller, service = self._make_controller()
        service.learned_charge_power_watts = 2280.0
        service.learned_charge_power_state = "stable"
        service.auto_learn_charge_power_max_age_seconds = 0.0
        service.learned_charge_power_updated_at = None

        self.assertEqual(controller._active_learned_charge_power(), 2280.0)

        controller._auto_policy().learn_charge_power.max_age_seconds = 60.0
        self.assertIsNone(controller._active_learned_charge_power())

        delattr(service, "_time_now")
        service.learned_charge_power_updated_at = 995.0
        with patch("shelly_wallbox.auto.workflow.time.time", return_value=1000.0):
            self.assertEqual(controller._active_learned_charge_power(), 2280.0)

    def test_learned_charge_power_helpers_cover_invalid_state_and_defensive_none_paths(self):
        controller, service = self._make_controller()
        service.learned_charge_power_watts = 2280.0
        service.learned_charge_power_updated_at = 995.0
        service.learned_charge_power_state = "unsupported"
        self.assertEqual(controller._current_learned_charge_power_state(1000.0), "unknown")

        with patch.object(controller, "_current_learned_charge_power_state", return_value="stable"):
            service.learned_charge_power_watts = None
            self.assertIsNone(controller._active_learned_charge_power())

            service.learned_charge_power_watts = 0.0
            self.assertIsNone(controller._active_learned_charge_power())

            controller._auto_policy().learn_charge_power.max_age_seconds = 60.0
            service.learned_charge_power_watts = 2280.0
            service.learned_charge_power_updated_at = None
            self.assertIsNone(controller._active_learned_charge_power())

            service.learned_charge_power_updated_at = 900.0
            self.assertIsNone(controller._active_learned_charge_power(1000.0))

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
