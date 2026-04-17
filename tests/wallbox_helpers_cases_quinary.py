# SPDX-License-Identifier: GPL-3.0-or-later
from tests.wallbox_helpers_support import *


class TestShellyWallboxHelpersQuinary(ShellyWallboxHelpersTestBase):
    def test_auto_mode_does_not_restart_until_resume_soc(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(105.0, 2200.0, -2200.0)])
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=111.0):
            self.assertFalse(service._auto_decide_relay(False, 2200, 31, -2200))

    def test_auto_mode_keeps_running_inside_hysteresis_band(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(205.0, 1800.0, -1800.0)])
        service.relay_last_changed_at = 0.0
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=211.0):
            self.assertTrue(service._auto_decide_relay(True, 1800, 31, -1800))

    def test_auto_mode_does_not_stop_before_min_runtime(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(110.0, 1200.0, 500.0)])
        service.relay_last_changed_at = 100.0
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=200.0):
            self.assertTrue(service._auto_decide_relay(True, 1200, 45, 500))

    def test_auto_mode_does_not_start_when_average_grid_import_is_too_high(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(105.0, 2200.0, 150.0)])
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=111.0):
            self.assertFalse(service._auto_decide_relay(False, 2200, 45, 150))

    def test_auto_mode_does_not_restart_before_min_offtime(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = 100.0
        service.auto_stop_condition_since = None
        service.auto_samples = deque([(105.0, 2200.0, -2200.0)])
        service.relay_last_changed_at = 100.0
        service.relay_last_off_at = 100.0
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=111.0):
            self.assertFalse(service._auto_decide_relay(False, 2200, 45, -2200))

    def test_auto_mode_waits_during_startup_warmup(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 100.0
        service.auto_startup_warmup_seconds = 30.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=110.0):
            self.assertFalse(service._auto_decide_relay(False, 2200, 45, -2200))
        self.assertEqual(service._last_health_reason, "warmup")

    def test_auto_mode_respects_manual_override_holdoff(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 200.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=150.0):
            self.assertFalse(service._auto_decide_relay(False, 2200, 45, -2200))
        self.assertEqual(service._last_health_reason, "manual-override")

    def test_auto_mode_scenario_cloud_passages_hold_charge_then_stop_after_persistent_import(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 0
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = 0.0
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            self.assertTrue(service._auto_decide_relay(True, 2200.0, 45.0, -1800.0))
        self.assertIsNone(service.auto_stop_condition_since)

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=110.0):
            self.assertTrue(service._auto_decide_relay(True, 900.0, 45.0, 420.0))

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=130.0):
            self.assertTrue(service._auto_decide_relay(True, 950.0, 45.0, 390.0))

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=141.0):
            self.assertFalse(service._auto_decide_relay(True, 900.0, 45.0, 410.0))
        self.assertEqual(service._last_health_reason, "auto-stop")

    def test_auto_mode_scenario_grid_missing_for_45_seconds_then_recovering_keeps_session_running(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 0
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_grid_missing_stop_seconds = 60
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = 0.0
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0
        service._last_grid_at = 100.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=145.0):
            self.assertTrue(service._auto_decide_relay(True, 0.0, 45.0, None))

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=146.0):
            self.assertTrue(service._auto_decide_relay(True, 0.0, 45.0, -250.0))

        self.assertNotEqual(service._last_health_reason, "grid-missing")

    def test_auto_mode_scenario_resume_soc_crossing_arms_then_allows_start(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 1
        service.virtual_autostart = 1
        service.virtual_enable = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 40
        service.auto_resume_soc = 50
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 300
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = None
        service.auto_samples = deque()
        service.relay_last_changed_at = None
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.manual_override_until = 0.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=100.0):
            self.assertFalse(service._auto_decide_relay(False, 2400.0, 49.8, -2200.0))
        self.assertIsNone(service.auto_start_condition_since)

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=101.0):
            self.assertFalse(service._auto_decide_relay(False, 2400.0, 50.2, -2200.0))
        self.assertEqual(service.auto_start_condition_since, 101.0)

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=112.0):
            self.assertTrue(service._auto_decide_relay(False, 2400.0, 50.2, -2200.0))

    def test_manual_override_scenario_after_auto_stop_keeps_manual_reenable_in_control(self):
        service = ShellyWallboxService.__new__(ShellyWallboxService)
        service.virtual_mode = 0
        service.virtual_autostart = 1
        service.virtual_startstop = 0
        service.virtual_enable = 0
        service.virtual_set_current = 16.0
        service.max_current = 16.0
        service.min_current = 6.0
        service.auto_manual_override_seconds = 300
        service.manual_override_until = 0.0
        service._dbusservice = {"/Mode": 0, "/StartStop": 0, "/Enable": 0}
        service._queue_relay_command = MagicMock()
        service._publish_local_pm_status = MagicMock()

        service.virtual_mode = 1
        service.virtual_enable = 1
        service.virtual_autostart = 1
        service.auto_allow_without_battery_soc = False
        service.auto_min_soc = 30
        service.auto_resume_soc = 33
        service.auto_start_surplus_watts = 2000
        service.auto_stop_surplus_watts = 1600
        service.auto_average_window_seconds = 30
        service.auto_min_runtime_seconds = 0
        service.auto_min_offtime_seconds = 120
        service.auto_start_max_grid_import_watts = 50
        service.auto_stop_grid_import_watts = 300
        service.auto_start_delay_seconds = 10
        service.auto_stop_delay_seconds = 30
        service.auto_start_condition_since = None
        service.auto_stop_condition_since = 100.0
        service.auto_samples = deque([(105.0, 900.0, 400.0)])
        service.relay_last_changed_at = 0.0
        service.relay_last_off_at = None
        service.started_at = 0.0
        service.auto_startup_warmup_seconds = 0.0

        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=131.0):
            self.assertFalse(service._auto_decide_relay(True, 900.0, 45.0, 400.0))

        service.virtual_mode = 0
        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=132.0):
            self.assertTrue(service._handle_write("/StartStop", 1))

        service.virtual_mode = 1
        with unittest.mock.patch("dbus_shelly_wallbox.time.time", return_value=150.0):
            self.assertTrue(service._auto_decide_relay(True, 900.0, 45.0, 400.0))
        self.assertEqual(service._last_health_reason, "manual-override")
