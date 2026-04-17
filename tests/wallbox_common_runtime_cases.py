# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import unittest

from shelly_wallbox.core.common import (
    _derive_auto_state,
    mode_uses_auto_logic,
    month_in_ranges,
    month_window,
    normalize_mode,
    normalize_phase,
    parse_hhmm,
    phase_values,
)


class TestShellyWallboxCommonRuntime(unittest.TestCase):
    def test_auto_state_helpers_cover_normalization_and_reason_mapping(self):
        self.assertEqual(_derive_auto_state("init"), "idle")
        self.assertEqual(_derive_auto_state("waiting-surplus"), "waiting")
        self.assertEqual(_derive_auto_state("manual-override"), "blocked")
        self.assertEqual(_derive_auto_state("grid-missing"), "recovery")
        self.assertEqual(_derive_auto_state("running", relay_on=True, learned_charge_power_state="stable"), "charging")

    def test_confirmed_relay_helpers_cover_invalid_age_and_freshness(self):
        fresh_service = type("FreshService", (), {"auto_shelly_soft_fail_seconds": 10.0, "_last_confirmed_pm_status": {"output": True}, "_last_confirmed_pm_status_at": 95.0})()
        invalid_service = type("InvalidService", (), {"auto_shelly_soft_fail_seconds": "bad", "_worker_poll_interval_seconds": "bad", "relay_sync_timeout_seconds": "bad"})()
        self.assertEqual(__import__("shelly_wallbox.core.common", fromlist=["_confirmed_relay_state_max_age_seconds"])._confirmed_relay_state_max_age_seconds(invalid_service), 5.0)
        self.assertTrue(__import__("shelly_wallbox.core.common", fromlist=["_fresh_confirmed_relay_output"])._fresh_confirmed_relay_output(fresh_service, 100.0))

    def test_phase_values_covers_single_phase_and_invalid_three_phase_voltage(self):
        values = phase_values(2300, 230, "L2", "phase")
        self.assertEqual(values["L2"]["power"], 2300.0)
        values = phase_values(3000, 400, "3P", "line")
        self.assertAlmostEqual(values["L2"]["power"], 1000.0)
        with self.assertRaises(ValueError):
            phase_values(3000, 0, "3P", "phase")

    def test_normalizers_and_time_helpers_cover_fallbacks(self):
        self.assertEqual(normalize_phase("1P"), "L1")
        with self.assertRaises(ValueError):
            normalize_phase("invalid")
        self.assertEqual(normalize_mode("2"), 2)
        self.assertTrue(mode_uses_auto_logic(1))
        self.assertEqual(parse_hhmm("07:30", (8, 0)), (7, 30))
        self.assertTrue(month_in_ranges(3, [(1, 4)]))
        parser = configparser.ConfigParser()
        parser.read_dict({"DEFAULT": {"AutoJanStart": "06:15", "AutoJanEnd": "17:45"}})
        self.assertEqual(month_window(parser, 1, "08:00", "18:00"), ((6, 15), (17, 45)))
