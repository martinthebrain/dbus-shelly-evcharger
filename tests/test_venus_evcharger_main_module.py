# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.modules["vedbus"] = MagicMock()
sys.modules["dbus"] = MagicMock()
sys.modules["dbus.mainloop.glib"] = MagicMock()
sys.modules["gi"] = MagicMock()
sys.modules["gi.repository"] = MagicMock()
sys.modules["gi.repository.GLib"] = MagicMock()

import venus_evcharger_service
from venus_evcharger_service import ShellyWallboxService


class TestShellyWallboxMainModule(unittest.TestCase):
    def test_helpers_and_wrappers_cover_safe_float_time_json_and_paths(self) -> None:
        self.assertEqual(ShellyWallboxService._safe_float(None, 7.5), 7.5)
        self.assertEqual(ShellyWallboxService._safe_float("bad", 2.5), 2.5)
        self.assertEqual(ShellyWallboxService._safe_float("4.5"), 4.5)

        with patch("venus_evcharger_service.time.time", return_value=123.0):
            self.assertEqual(ShellyWallboxService._time_now(), 123.0)

        helper_path = ShellyWallboxService._auto_input_helper_path()
        self.assertTrue(helper_path.endswith("venus_evcharger_auto_input_helper.py"))

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "payload.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"value": 42}, handle)
            self.assertEqual(ShellyWallboxService._load_json_file(path), {"value": 42})
            self.assertTrue(ShellyWallboxService._stat_path(path).st_size > 0)

    def test_init_and_main_delegate_to_bootstrap(self) -> None:
        bootstrap = MagicMock()
        state_controller = MagicMock()
        with patch("venus_evcharger_service.ServiceStateController", return_value=state_controller) as state_factory:
            with patch("venus_evcharger_service.ServiceBootstrapController", return_value=bootstrap) as bootstrap_factory:
                ShellyWallboxService()

        state_factory.assert_called_once()
        bootstrap_factory.assert_called_once()
        bootstrap.initialize_service.assert_called_once_with()

        with patch("venus_evcharger_service.run_service_main") as run_service_main:
            venus_evcharger_service.main()
        run_service_main.assert_called_once_with(
            ShellyWallboxService,
            ShellyWallboxService._config_path(),
            venus_evcharger_service.gobject,
        )
