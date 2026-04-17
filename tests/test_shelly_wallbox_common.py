# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
from datetime import datetime
import unittest
from unittest.mock import mock_open, patch

from shelly_wallbox.core.contracts import (
    cutover_confirmed_off,
    displayable_confirmed_read_timestamp,
    finite_float_or_none,
    non_negative_float_or_none,
    non_negative_int,
    normalized_fault_state,
    normalized_auto_decision_trace,
    normalized_auto_state_pair,
    normalized_scheduled_state_fields,
    normalized_software_update_state_fields,
    normalized_status_source,
    normalized_worker_snapshot,
    normalize_learning_phase,
    normalize_learning_state,
    paired_optional_values,
    sanitized_auto_metrics,
    timestamp_age_within,
    timestamp_not_future,
    thresholds_ordered,
    valid_battery_soc,
    write_failure_is_reversible,
)
from shelly_wallbox.core.common import (
    _a,
    _age_seconds,
    _auto_state_code,
    _confirmed_relay_state_max_age_seconds,
    _derive_auto_state,
    _fresh_confirmed_relay_output,
    _health_code,
    _kwh,
    _normalize_auto_state,
    _status_label,
    _v,
    _w,
    mode_uses_auto_logic,
    month_in_ranges,
    month_window,
    normalize_mode,
    normalize_phase,
    parse_hhmm,
    phase_values,
    read_version,
    scheduled_enabled_days_text,
    scheduled_mode_snapshot,
)


class TestShellyWallboxCommon(unittest.TestCase):
    def test_contract_helpers_cover_numeric_state_pairing_and_time_rules(self):
        self.assertEqual(finite_float_or_none("7.5"), 7.5)
        self.assertIsNone(finite_float_or_none(float("nan")))
        self.assertIsNone(non_negative_float_or_none(-1.0))
        self.assertEqual(non_negative_float_or_none("8.5"), 8.5)
        self.assertEqual(non_negative_int("-3", 2), 0)
        self.assertEqual(non_negative_int(True, 2), 2)
        self.assertEqual(non_negative_int("bad", 2), 2)
        self.assertEqual(normalize_learning_state(" stable "), "stable")
        self.assertEqual(normalize_learning_state("weird"), "unknown")
        self.assertEqual(normalize_learning_phase("3p"), "3P")
        self.assertIsNone(normalize_learning_phase("bad"))
        self.assertTrue(paired_optional_values(None, None))
        self.assertTrue(paired_optional_values(1, 2))
        self.assertFalse(paired_optional_values(None, 2))
        self.assertTrue(valid_battery_soc(50.0))
        self.assertFalse(valid_battery_soc(150.0))
        self.assertTrue(timestamp_not_future(100.5, 100.0, 1.0))
        self.assertFalse(timestamp_not_future(102.0, 100.0, 1.0))
        self.assertTrue(timestamp_age_within(99.0, 100.0, 5.0))
        self.assertFalse(timestamp_age_within(90.0, 100.0, 5.0))
        self.assertFalse(timestamp_age_within(None, 100.0, 5.0))
        self.assertTrue(thresholds_ordered(1850.0, 1350.0))
        self.assertFalse(thresholds_ordered(1350.0, 1850.0))
        self.assertTrue(
            cutover_confirmed_off(
                relay_on=False,
                pending_state=None,
                confirmed_output=False,
                confirmed_at=100.0,
                requested_at=99.0,
                now=100.5,
                max_age_seconds=2.0,
            )
        )
        self.assertFalse(
            cutover_confirmed_off(
                relay_on=False,
                pending_state=None,
                confirmed_output=False,
                confirmed_at=98.0,
                requested_at=99.0,
                now=100.5,
                max_age_seconds=2.0,
            )
        )
        self.assertTrue(write_failure_is_reversible(False))
        self.assertFalse(write_failure_is_reversible(True))
        self.assertEqual(normalized_auto_state_pair(" charging ", 99), ("charging", 3))
        self.assertEqual(normalized_auto_state_pair("bogus", 4), ("idle", 0))
        self.assertEqual(normalized_auto_state_pair("idle", "bad"), ("idle", 0))
        self.assertEqual(normalized_status_source(" charger-status-ready "), "charger-status-ready")
        self.assertEqual(normalized_status_source(""), "unknown")
        self.assertEqual(normalized_fault_state("contactor-lockout-open"), ("contactor-lockout-open", 1))
        self.assertEqual(normalized_fault_state(""), ("", 0))
        self.assertEqual(
            normalized_scheduled_state_fields(True, "night-boost", 99, "night-boost-window", -1, 1),
            ("night-boost", 4, "night-boost-window", 4, 1),
        )
        self.assertEqual(
            normalized_scheduled_state_fields(True, "waiting-fallback", 3, "waiting-fallback-delay", 3, 1),
            ("waiting-fallback", 3, "waiting-fallback-delay", 3, 0),
        )
        self.assertEqual(
            normalized_scheduled_state_fields(False, "night-boost", 4, "night-boost-window", 4, 1),
            ("disabled", 0, "disabled", 0, 0),
        )
        self.assertEqual(
            normalized_software_update_state_fields("available", 1, 0),
            ("available", 3, 1, 0),
        )
        self.assertEqual(
            normalized_software_update_state_fields("available", 1, 1),
            ("available-blocked", 4, 1, 1),
        )
        self.assertEqual(
            normalized_software_update_state_fields("available-blocked", 1, 0),
            ("available", 3, 1, 0),
        )
        self.assertEqual(
            normalized_software_update_state_fields("weird", 0, 0),
            ("idle", 0, 0, 0),
        )
        self.assertEqual(
            displayable_confirmed_read_timestamp(
                last_confirmed_at=95.0,
                last_pm_at=96.0,
                last_pm_confirmed=True,
                now=100.0,
            ),
            95.0,
        )
        self.assertEqual(
            displayable_confirmed_read_timestamp(
                last_confirmed_at=105.0,
                last_pm_at=96.0,
                last_pm_confirmed=True,
                now=100.0,
            ),
            96.0,
        )
        self.assertIsNone(
            displayable_confirmed_read_timestamp(
                last_confirmed_at=None,
                last_pm_at=105.0,
                last_pm_confirmed=True,
                now=100.0,
            )
        )
        self.assertEqual(sanitized_auto_metrics(["bad"]), {})
        sanitized = sanitized_auto_metrics(
            {
                "surplus": "bad",
                "grid": -500.0,
                "soc": 150.0,
                "start_threshold": 1000.0,
                "stop_threshold": 1200.0,
                "learned_charge_power": -10.0,
                "learned_charge_power_state": "odd",
                "threshold_mode": 7,
            }
        )
        self.assertIsNone(sanitized["surplus"])
        self.assertEqual(sanitized["grid"], -500.0)
        self.assertIsNone(sanitized["soc"])
        self.assertIsNone(sanitized["start_threshold"])
        self.assertIsNone(sanitized["stop_threshold"])
        self.assertIsNone(sanitized["learned_charge_power"])
        self.assertEqual(sanitized["learned_charge_power_state"], "unknown")
        self.assertEqual(sanitized["threshold_mode"], "7")
        trace = normalized_auto_decision_trace(
            health_reason="running",
            cached_inputs=True,
            relay_intent=1,
            learned_charge_power_state="learning",
            metrics={
                "start_threshold": 1000.0,
                "stop_threshold": 1200.0,
                "threshold_mode": 5,
            },
            health_code_func=lambda reason: {"running": 5}.get(reason, 99),
            derive_auto_state_func=_derive_auto_state,
        )
        self.assertEqual(trace["health_reason"], "running-cached")
        self.assertEqual(trace["health_code"], 105)
        self.assertEqual(trace["state"], "learning")
        self.assertEqual(trace["state_code"], 2)
        self.assertEqual(trace["metrics"]["relay_intent"], 1)
        self.assertIsNone(trace["metrics"]["start_threshold"])
        self.assertIsNone(trace["metrics"]["stop_threshold"])
        self.assertEqual(trace["metrics"]["state"], "learning")
        worker_snapshot = normalized_worker_snapshot(
            {
                "captured_at": 100.0,
                "pm_status": {"output": 1},
                "pm_confirmed": True,
            },
            now=100.0,
        )
        self.assertEqual(worker_snapshot["pm_captured_at"], 100.0)
        self.assertTrue(worker_snapshot["pm_confirmed"])
        broken_worker_snapshot = normalized_worker_snapshot(
            {
                "captured_at": 100.0,
                "pm_captured_at": 105.0,
                "pm_status": {"apower": 1800.0},
                "pm_confirmed": True,
            },
            now=100.0,
        )
        self.assertIsNone(broken_worker_snapshot["pm_status"])
        self.assertIsNone(broken_worker_snapshot["pm_captured_at"])
        self.assertFalse(broken_worker_snapshot["pm_confirmed"])
        minimal_worker_snapshot = normalized_worker_snapshot({}, now=None)
        self.assertEqual(minimal_worker_snapshot["captured_at"], 0.0)
        future_worker_snapshot = normalized_worker_snapshot({"captured_at": 105.0}, now=100.0)
        self.assertEqual(future_worker_snapshot["captured_at"], 100.0)
        future_pm_worker_snapshot = normalized_worker_snapshot(
            {
                "captured_at": 100.0,
                "pm_captured_at": 105.0,
                "pm_status": {"output": True},
                "pm_confirmed": True,
            },
            now=100.0,
        )
        self.assertIsNone(future_pm_worker_snapshot["pm_status"])
        self.assertIsNone(future_pm_worker_snapshot["pm_captured_at"])
        self.assertFalse(future_pm_worker_snapshot["pm_confirmed"])
        self.assertFalse(
            cutover_confirmed_off(
                relay_on=False,
                pending_state=True,
                confirmed_output=False,
                confirmed_at=100.0,
                requested_at=99.0,
                now=100.5,
                max_age_seconds=2.0,
            )
        )

    def test_scheduled_helpers_normalize_days_and_derive_states(self):
        self.assertEqual(scheduled_enabled_days_text("weekdays"), "Mon,Tue,Wed,Thu,Fri")
        self.assertEqual(scheduled_enabled_days_text("sat-sun"), "Sat,Sun")
        self.assertEqual(scheduled_enabled_days_text("mon,wed,fri"), "Mon,Wed,Fri")

        waiting = scheduled_mode_snapshot(
            datetime(2026, 4, 19, 20, 0),
            {4: ((7, 30), (19, 30))},
            "Mon,Tue,Wed,Thu,Fri",
            delay_seconds=3600.0,
            latest_end_time="06:30",
        )
        self.assertEqual(waiting.state, "waiting-fallback")
        self.assertEqual(waiting.reason, "waiting-fallback-delay")
        self.assertEqual(waiting.target_day_label, "Mon")
        self.assertEqual(waiting.fallback_start_text, "2026-04-19 20:30")
        self.assertEqual(waiting.boost_until_text, "2026-04-20 06:30")

        night_boost = scheduled_mode_snapshot(
            datetime(2026, 4, 19, 21, 0),
            {4: ((7, 30), (19, 30))},
            "Mon,Tue,Wed,Thu,Fri",
            delay_seconds=3600.0,
            latest_end_time="06:30",
        )
        self.assertEqual(night_boost.state, "night-boost")
        self.assertEqual(night_boost.reason, "night-boost-window")
        self.assertTrue(night_boost.night_boost_active)

        after_end = scheduled_mode_snapshot(
            datetime(2026, 4, 20, 6, 45),
            {4: ((7, 30), (19, 30))},
            "Mon,Tue,Wed,Thu,Fri",
            delay_seconds=3600.0,
            latest_end_time="06:30",
        )
        self.assertEqual(after_end.state, "after-latest-end")
        self.assertEqual(after_end.reason, "latest-end-reached")

        inactive = scheduled_mode_snapshot(
            datetime(2026, 4, 17, 21, 0),
            {4: ((7, 30), (19, 30))},
            "Mon,Tue,Wed,Thu,Fri",
            delay_seconds=3600.0,
            latest_end_time="06:30",
        )
        self.assertEqual(inactive.state, "inactive-day")
        self.assertEqual(inactive.reason, "target-day-disabled")
        self.assertFalse(inactive.target_day_enabled)
        self.assertFalse(
            cutover_confirmed_off(
                relay_on=False,
                pending_state=None,
                confirmed_output=False,
                confirmed_at=100.0,
                requested_at="bad",
                now=100.5,
                max_age_seconds=2.0,
            )
        )

    def test_formatters_health_code_and_age_seconds(self):
        self.assertEqual(_kwh(None, 1.234), "1.23")
        self.assertEqual(_a(None, 5), "5.0A")
        self.assertEqual(_w(None, 7), "7.0W")
        self.assertEqual(_v(None, 230), "230.0V")
        self.assertEqual(_status_label(None, 2), "Laden")
        self.assertEqual(_status_label(None, 999), "Unbekannt")
        self.assertEqual(_health_code("running"), 5)
        self.assertEqual(_health_code("charger-fault"), 26)
        self.assertEqual(_health_code("phase-switch-mismatch"), 27)
        self.assertEqual(_health_code("contactor-interlock"), 28)
        self.assertEqual(_health_code("contactor-feedback-mismatch"), 29)
        self.assertEqual(_health_code("contactor-suspected-open"), 30)
        self.assertEqual(_health_code("contactor-suspected-welded"), 31)
        self.assertEqual(_health_code("contactor-lockout-open"), 32)
        self.assertEqual(_health_code("contactor-lockout-welded"), 33)
        self.assertEqual(_health_code("unknown"), 99)
        self.assertEqual(_auto_state_code("charging"), 3)
        self.assertEqual(_auto_state_code("invalid"), 0)
        self.assertEqual(_age_seconds(None), -1)
        self.assertEqual(_age_seconds(100.0, 90.0), 0)
        self.assertEqual(_age_seconds(100.0, 105.9), 5)

    def test_auto_state_helpers_cover_normalization_and_reason_mapping(self):
        self.assertEqual(_normalize_auto_state(" LEARNING "), "learning")
        self.assertEqual(_normalize_auto_state("invalid"), "idle")
        self.assertEqual(_derive_auto_state("init"), "idle")
        self.assertEqual(_derive_auto_state("waiting-surplus"), "waiting")
        self.assertEqual(_derive_auto_state("manual-override"), "blocked")
        self.assertEqual(_derive_auto_state("grid-missing"), "recovery")
        self.assertEqual(_derive_auto_state("phase-switch-mismatch"), "recovery")
        self.assertEqual(_derive_auto_state("contactor-interlock"), "blocked")
        self.assertEqual(_derive_auto_state("contactor-feedback-mismatch"), "recovery")
        self.assertEqual(_derive_auto_state("contactor-suspected-open"), "recovery")
        self.assertEqual(_derive_auto_state("contactor-suspected-welded"), "recovery")
        self.assertEqual(_derive_auto_state("contactor-lockout-open"), "recovery")
        self.assertEqual(_derive_auto_state("contactor-lockout-welded"), "recovery")
        self.assertEqual(_derive_auto_state("running", relay_on=True, learned_charge_power_state="learning"), "learning")
        self.assertEqual(_derive_auto_state("running", relay_on=True, learned_charge_power_state="stable"), "charging")
        self.assertEqual(_derive_auto_state("custom-reason", relay_on=True, learned_charge_power_state="learning"), "learning")
        self.assertEqual(_derive_auto_state("custom-reason", relay_on=True, learned_charge_power_state="stable"), "charging")
        self.assertEqual(_derive_auto_state("custom-reason", relay_on=False, learned_charge_power_state="stable"), "idle")

    def test_confirmed_relay_helpers_cover_invalid_age_and_freshness(self):
        fresh_service = type(
            "FreshService",
            (),
            {
                "auto_shelly_soft_fail_seconds": 10.0,
                "_last_confirmed_pm_status": {"output": True},
                "_last_confirmed_pm_status_at": 95.0,
            },
        )()
        invalid_service = type(
            "InvalidService",
            (),
            {
                "auto_shelly_soft_fail_seconds": "bad",
                "_worker_poll_interval_seconds": "bad",
                "relay_sync_timeout_seconds": "bad",
            },
        )()
        zero_soft_fail_service = type(
            "ZeroSoftFailService",
            (),
            {
                "auto_shelly_soft_fail_seconds": 0.0,
                "_worker_poll_interval_seconds": 1.0,
                "relay_sync_timeout_seconds": 2.0,
                "_last_confirmed_pm_status": {"output": True},
                "_last_confirmed_pm_status_at": 99.0,
            },
        )()
        large_soft_fail_service = type(
            "LargeSoftFailService",
            (),
            {
                "auto_shelly_soft_fail_seconds": 600.0,
                "_worker_poll_interval_seconds": 1.0,
                "relay_sync_timeout_seconds": 2.0,
                "_last_confirmed_pm_status": {"output": True},
                "_last_confirmed_pm_status_at": 95.0,
            },
        )()

        self.assertEqual(_confirmed_relay_state_max_age_seconds(invalid_service), 5.0)
        self.assertTrue(_fresh_confirmed_relay_output(fresh_service, 100.0))
        self.assertEqual(_confirmed_relay_state_max_age_seconds(zero_soft_fail_service), 2.0)
        self.assertTrue(_fresh_confirmed_relay_output(zero_soft_fail_service, 100.0))
        self.assertEqual(_confirmed_relay_state_max_age_seconds(large_soft_fail_service), 2.0)
        self.assertIsNone(_fresh_confirmed_relay_output(large_soft_fail_service, 100.0))

        fallback_service = type(
            "FallbackService",
            (),
            {
                "_last_confirmed_pm_status": None,
                "_last_confirmed_pm_status_at": None,
                "_last_pm_status_confirmed": True,
                "_last_pm_status": {"output": False},
                "_last_pm_status_at": 99.0,
                "_worker_poll_interval_seconds": 1.0,
                "relay_sync_timeout_seconds": 2.0,
            },
        )()
        self.assertFalse(_fresh_confirmed_relay_output(fallback_service, 100.0))

        future_service = type(
            "FutureService",
            (),
            {
                "_last_confirmed_pm_status": {"output": True},
                "_last_confirmed_pm_status_at": 102.5,
                "_worker_poll_interval_seconds": 1.0,
                "relay_sync_timeout_seconds": 2.0,
            },
        )()
        self.assertIsNone(_fresh_confirmed_relay_output(future_service, 100.0))

    def test_read_version_success_and_missing_file(self):
        with patch("builtins.open", mock_open(read_data="version: 1.2.3\n")):
            self.assertEqual(read_version("version.txt"), "1.2.3")

        with patch("builtins.open", side_effect=FileNotFoundError):
            self.assertEqual(read_version("missing.txt"), "0.1")

    def test_phase_values_covers_single_phase_and_invalid_three_phase_voltage(self):
        values = phase_values(2300, 230, "L2", "phase")
        self.assertEqual(values["L1"]["power"], 0.0)
        self.assertEqual(values["L2"]["power"], 2300.0)
        self.assertAlmostEqual(values["L2"]["current"], 10.0)

        values = phase_values(3000, 400, "3P", "line")
        self.assertAlmostEqual(values["L1"]["voltage"], 400 / (3 ** 0.5))
        self.assertAlmostEqual(values["L2"]["power"], 1000.0)
        self.assertAlmostEqual(values["L3"]["current"], values["L1"]["current"])

        with self.assertRaises(ValueError):
            phase_values(3000, 0, "3P", "phase")

    def test_normalizers_and_time_helpers_cover_fallbacks(self):
        self.assertEqual(normalize_phase("1P"), "L1")
        self.assertEqual(normalize_phase("l3"), "L3")
        with self.assertRaises(ValueError):
            normalize_phase("invalid")

        self.assertEqual(normalize_mode("2"), 2)
        self.assertEqual(normalize_mode("invalid"), 0)
        self.assertEqual(normalize_mode(99), 0)
        self.assertTrue(mode_uses_auto_logic(1))
        self.assertFalse(mode_uses_auto_logic("invalid"))

        self.assertEqual(parse_hhmm("07:30", (8, 0)), (7, 30))
        self.assertEqual(parse_hhmm("99:99", (8, 0)), (8, 0))
        self.assertEqual(parse_hhmm(None, (8, 0)), (8, 0))

        self.assertTrue(month_in_ranges(3, [(1, 4)]))
        self.assertTrue(month_in_ranges(1, [(11, 2)]))
        self.assertFalse(month_in_ranges(6, [(11, 2)]))

        parser = configparser.ConfigParser()
        parser.read_dict({"DEFAULT": {"AutoJanStart": "06:15", "AutoJanEnd": "17:45"}})
        self.assertEqual(month_window(parser, 1, "08:00", "18:00"), ((6, 15), (17, 45)))
