# SPDX-License-Identifier: GPL-3.0-or-later
import json
import subprocess
import sys
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


TESTBED_SCRIPT = Path("scripts/dev/venus_cerbo_testbed.py")


def _load_testbed_module() -> ModuleType:
    loader = SourceFileLoader("venus_cerbo_testbed_under_test", str(TESTBED_SCRIPT))
    spec = spec_from_loader(loader.name, loader)
    assert spec is not None
    module = module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


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

    def test_real_probe_treats_missing_relay_paths_as_skipped(self) -> None:
        module = _load_testbed_module()

        with patch.object(module.shutil, "which", return_value="/usr/bin/dbus"), patch.object(
            module,
            "_read_dbus_value",
            side_effect=[
                {"ok": False, "skipped": True},
                {"ok": False, "skipped": True},
                {"ok": False, "skipped": True},
                {"ok": True, "skipped": False},
            ],
        ):
            payload = module.probe_real_cerbo(0.1)

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["skipped"])

    def test_dbus_cli_traceback_for_missing_path_is_skippable(self) -> None:
        module = _load_testbed_module()

        self.assertTrue(module._is_missing_dbus_probe("AttributeError: 'NoneType' object has no attribute 'name'"))
        self.assertFalse(module._is_missing_dbus_probe("permission denied"))
