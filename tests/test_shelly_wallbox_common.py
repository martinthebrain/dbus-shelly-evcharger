# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import unittest
from unittest.mock import mock_open, patch

from dbus_shelly_wallbox_common import (
    _a,
    _age_seconds,
    _health_code,
    _kwh,
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
)


class TestShellyWallboxCommon(unittest.TestCase):
    def test_formatters_health_code_and_age_seconds(self):
        self.assertEqual(_kwh(None, 1.234), "1.23")
        self.assertEqual(_a(None, 5), "5.0A")
        self.assertEqual(_w(None, 7), "7.0W")
        self.assertEqual(_v(None, 230), "230.0V")
        self.assertEqual(_status_label(None, 2), "Laden")
        self.assertEqual(_status_label(None, 999), "Unbekannt")
        self.assertEqual(_health_code("running"), 5)
        self.assertEqual(_health_code("unknown"), 99)
        self.assertEqual(_age_seconds(None), -1)
        self.assertEqual(_age_seconds(100.0, 90.0), 0)
        self.assertEqual(_age_seconds(100.0, 105.9), 5)

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
