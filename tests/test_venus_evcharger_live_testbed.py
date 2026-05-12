# SPDX-License-Identifier: GPL-3.0-or-later
import json
import subprocess
import sys
import unittest
from pathlib import Path


TESTBED_SCRIPT = Path("scripts/dev/venus_cerbo_testbed.py")


class TestVenusEvchargerLiveTestbed(unittest.TestCase):
    def test_simulated_unplug_replug_scenario_is_machine_readable(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(TESTBED_SCRIPT), "simulate", "unplug-replug"],
            check=True,
            capture_output=True,
            text=True,
        )

        payload = json.loads(completed.stdout)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["kind"], "venus-cerbo-testbed")
        self.assertEqual(payload["scenario"], "unplug-replug")
        self.assertTrue(payload["expectations"]["session_energy_should_reset"])
        self.assertIn("/Session/Energy", {item["path"] for item in payload["services"]})

    def test_real_probe_without_dbus_cli_skips_or_reports_probe_results(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(TESTBED_SCRIPT), "probe-real", "--timeout", "0.1"],
            check=False,
            capture_output=True,
            text=True,
        )

        payload = json.loads(completed.stdout)

        self.assertEqual(payload["kind"], "venus-cerbo-testbed")
        self.assertIn(payload["mode"], {"probe-real"})
        self.assertIn("probes", payload)
