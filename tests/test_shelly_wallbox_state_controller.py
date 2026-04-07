# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dbus_shelly_wallbox_auto_policy import AutoPolicy, AutoStopEwmaPolicy, AutoThresholdProfile
from dbus_shelly_wallbox_state import ServiceStateController
from tests.wallbox_test_fixtures import make_runtime_state_service, make_state_validation_service


class TestServiceStateController(unittest.TestCase):
    def test_config_path_and_coercion_helpers_and_state_summary(self):
        service = SimpleNamespace(
            virtual_mode=1,
            virtual_enable=0,
            virtual_startstop=1,
            virtual_autostart=1,
            _auto_mode_cutover_pending=True,
            _ignore_min_offtime_once=False,
            _last_health_reason="running",
        )
        controller = ServiceStateController(service, int)

        self.assertTrue(controller.config_path().endswith("config.shelly_wallbox.ini"))
        self.assertEqual(controller.coerce_runtime_int("7"), 7)
        self.assertEqual(controller.coerce_runtime_int("bad", 3), 3)
        self.assertEqual(controller.coerce_runtime_float("7.5"), 7.5)
        self.assertEqual(controller.coerce_runtime_float("bad", 3.5), 3.5)
        self.assertIsNone(controller._coerce_optional_runtime_float(None))
        self.assertEqual(controller._coerce_optional_runtime_float("7.5"), 7.5)
        self.assertIn("mode=1", controller.state_summary())
        self.assertIn("health=running", controller.state_summary())

    def test_load_runtime_state_missing_file_and_load_config_success(self):
        service = SimpleNamespace(runtime_state_path="/tmp/does-not-exist.json")
        controller = ServiceStateController(service, int)
        controller.load_runtime_state()

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("[DEFAULT]\nHost=192.168.1.20\n")
            config_path = handle.name
        self.addCleanup(lambda: os.path.exists(config_path) and os.unlink(config_path))

        with patch.object(ServiceStateController, "config_path", return_value=config_path):
            config = controller.load_config()

        self.assertEqual(config["DEFAULT"]["Host"], "192.168.1.20")

    def test_validate_runtime_config_clamps_invalid_values(self):
        service = make_state_validation_service(
            poll_interval_ms=0,
            sign_of_life_minutes=0,
            auto_pv_max_services=0,
            auto_pv_scan_interval_seconds=-1,
            auto_battery_scan_interval_seconds=-1,
            auto_dbus_backoff_base_seconds=-1,
            auto_dbus_backoff_max_seconds=-1,
            auto_grid_missing_stop_seconds=-1,
            auto_grid_recovery_start_seconds=-1,
            auto_average_window_seconds=-1,
            auto_min_runtime_seconds=-1,
            auto_min_offtime_seconds=-1,
            auto_start_delay_seconds=-1,
            auto_stop_delay_seconds=-1,
            auto_stop_surplus_delay_seconds=-1,
            auto_stop_ewma_alpha=-1,
            auto_stop_ewma_alpha_stable=-1,
            auto_stop_ewma_alpha_volatile=-1,
            auto_stop_surplus_volatility_low_watts=-1,
            auto_stop_surplus_volatility_high_watts=-2,
            auto_input_cache_seconds=-1,
            auto_input_helper_restart_seconds=-1,
            auto_input_helper_stale_seconds=-1,
            auto_shelly_soft_fail_seconds=-1,
            auto_watchdog_stale_seconds=-1,
            auto_watchdog_recovery_seconds=-1,
            auto_startup_warmup_seconds=-1,
            auto_manual_override_seconds=-1,
            startup_device_info_retry_seconds=-1,
            startup_device_info_retries=-1,
            shelly_request_timeout_seconds=-1,
            dbus_method_timeout_seconds=-1,
            auto_min_soc=120,
            auto_resume_soc=-5,
            auto_start_surplus_watts=1500,
            auto_stop_surplus_watts=2400,
        )

        controller = ServiceStateController(service, int)
        controller.validate_runtime_config()

        self.assertEqual(service.poll_interval_ms, 100)
        self.assertEqual(service.sign_of_life_minutes, 1)
        self.assertEqual(service.auto_pv_max_services, 1)
        self.assertEqual(service.auto_input_helper_restart_seconds, 0.0)
        self.assertEqual(service.auto_input_helper_stale_seconds, 0.0)
        self.assertEqual(service.auto_stop_ewma_alpha, 0.35)
        self.assertEqual(service.auto_stop_ewma_alpha_stable, 0.55)
        self.assertEqual(service.auto_stop_ewma_alpha_volatile, 0.15)
        self.assertEqual(service.auto_stop_surplus_volatility_low_watts, 0.0)
        self.assertEqual(service.auto_stop_surplus_volatility_high_watts, 0.0)
        self.assertEqual(service.shelly_request_timeout_seconds, 2.0)
        self.assertEqual(service.dbus_method_timeout_seconds, 1.0)
        self.assertEqual(service.auto_min_soc, 100.0)
        self.assertEqual(service.auto_resume_soc, 100.0)
        self.assertEqual(service.auto_stop_surplus_watts, 1500)

    def test_validate_runtime_config_keeps_valid_values_and_clamps_audit_settings(self):
        service = make_state_validation_service(
            auto_grid_recovery_start_seconds=1.0,
            auto_stop_surplus_delay_seconds=1.0,
            auto_stop_ewma_alpha=1.0,
            auto_stop_ewma_alpha_stable=0.6,
            auto_stop_ewma_alpha_volatile=0.2,
            auto_stop_surplus_volatility_low_watts=120.0,
            auto_stop_surplus_volatility_high_watts=300.0,
            auto_audit_log_max_age_hours=0.0,
            auto_audit_log_repeat_seconds=0.0,
            auto_min_soc=30,
            auto_resume_soc=40,
            auto_high_soc_threshold=120,
            auto_high_soc_release_threshold=140,
            auto_start_surplus_watts=1500,
            auto_stop_surplus_watts=1100,
            auto_high_soc_start_surplus_watts=1650,
            auto_high_soc_stop_surplus_watts=2400,
        )

        controller = ServiceStateController(service, int)
        controller.validate_runtime_config()

        self.assertEqual(service.auto_audit_log_max_age_hours, 168.0)
        self.assertEqual(service.auto_audit_log_repeat_seconds, 30.0)
        self.assertEqual(service.auto_resume_soc, 40)
        self.assertEqual(service.auto_high_soc_threshold, 100.0)
        self.assertEqual(service.auto_high_soc_release_threshold, 100.0)
        self.assertEqual(service.auto_stop_surplus_watts, 1100)
        self.assertEqual(service.auto_high_soc_stop_surplus_watts, 1650)

    def test_validate_runtime_config_clamps_structured_auto_policy_and_syncs_attrs(self):
        service = make_state_validation_service(
            auto_policy=AutoPolicy(
                normal_profile=AutoThresholdProfile(1850.0, 2400.0),
                high_soc_profile=AutoThresholdProfile(1650.0, 1800.0),
                high_soc_threshold=55.0,
                high_soc_release_threshold=60.0,
                min_soc=35.0,
                resume_soc=30.0,
                start_max_grid_import_watts=50.0,
                stop_grid_import_watts=300.0,
                grid_recovery_start_seconds=-1.0,
                stop_surplus_delay_seconds=-1.0,
                ewma=AutoStopEwmaPolicy(
                    base_alpha=-1.0,
                    stable_alpha=-1.0,
                    volatile_alpha=-1.0,
                    volatility_low_watts=200.0,
                    volatility_high_watts=100.0,
                ),
            ),
        )

        controller = ServiceStateController(service, int)
        controller.validate_runtime_config()

        self.assertEqual(service.auto_policy.normal_profile.stop_surplus_watts, 1850.0)
        self.assertEqual(service.auto_policy.high_soc_profile.stop_surplus_watts, 1650.0)
        self.assertEqual(service.auto_policy.high_soc_release_threshold, 55.0)
        self.assertEqual(service.auto_policy.resume_soc, 35.0)
        self.assertEqual(service.auto_policy.grid_recovery_start_seconds, 0.0)
        self.assertEqual(service.auto_policy.stop_surplus_delay_seconds, 0.0)
        self.assertEqual(service.auto_policy.ewma.base_alpha, 0.35)
        self.assertEqual(service.auto_policy.ewma.stable_alpha, 0.55)
        self.assertEqual(service.auto_policy.ewma.volatile_alpha, 0.15)
        self.assertEqual(service.auto_policy.ewma.volatility_high_watts, 200.0)
        self.assertEqual(service.auto_start_surplus_watts, 1850.0)
        self.assertEqual(service.auto_high_soc_stop_surplus_watts, 1650.0)

    def test_validate_runtime_config_legacy_auto_thresholds_clamp_release_and_volatility_order(self):
        service = make_state_validation_service(
            auto_min_soc=35.0,
            auto_resume_soc=40.0,
            auto_high_soc_threshold=55.0,
            auto_high_soc_release_threshold=60.0,
            auto_start_surplus_watts=1850.0,
            auto_stop_surplus_watts=1350.0,
            auto_high_soc_start_surplus_watts=1650.0,
            auto_high_soc_stop_surplus_watts=800.0,
            auto_stop_surplus_volatility_low_watts=200.0,
            auto_stop_surplus_volatility_high_watts=100.0,
        )

        controller = ServiceStateController(service, int)
        controller.validate_runtime_config()

        self.assertEqual(service.auto_high_soc_release_threshold, 55.0)
        self.assertEqual(service.auto_stop_surplus_volatility_high_watts, 200.0)

    def test_runtime_state_roundtrip_restores_ram_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/state.json"
            service = SimpleNamespace(
                runtime_state_path=path,
                virtual_mode=1,
                virtual_autostart=0,
                virtual_enable=1,
                virtual_startstop=0,
                manual_override_until=123.5,
                _auto_mode_cutover_pending=True,
                _ignore_min_offtime_once=True,
                relay_last_changed_at=111.0,
                relay_last_off_at=112.0,
                _runtime_state_serialized=None,
                _last_health_reason="init",
            )
            controller = ServiceStateController(service, lambda value: int(value))

            controller.save_runtime_state()

            with open(path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)

            self.assertEqual(saved["mode"], 1)
            self.assertEqual(saved["autostart"], 0)
            self.assertEqual(saved["auto_mode_cutover_pending"], 1)

            service.virtual_mode = 0
            service.virtual_autostart = 1
            service.virtual_enable = 0
            service.virtual_startstop = 1
            service.manual_override_until = 0.0
            service._auto_mode_cutover_pending = False
            service._ignore_min_offtime_once = False
            service.relay_last_changed_at = None
            service.relay_last_off_at = None

            controller.load_runtime_state()

            self.assertEqual(service.virtual_mode, 1)
            self.assertEqual(service.virtual_autostart, 0)
            self.assertEqual(service.virtual_enable, 1)
            self.assertEqual(service.virtual_startstop, 0)
            self.assertEqual(service.manual_override_until, 123.5)
            self.assertTrue(service._auto_mode_cutover_pending)
            self.assertTrue(service._ignore_min_offtime_once)
            self.assertEqual(service.relay_last_changed_at, 111.0)
            self.assertEqual(service.relay_last_off_at, 112.0)

    def test_save_runtime_state_skips_empty_path_deduplicates_and_warns_on_failure(self):
        service = make_runtime_state_service()
        controller = ServiceStateController(service, int)
        controller.save_runtime_state()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "state.json")
            service.runtime_state_path = path
            controller.save_runtime_state()
            first_serialized = service._runtime_state_serialized
            controller.save_runtime_state()
            self.assertEqual(service._runtime_state_serialized, first_serialized)

            with patch("dbus_shelly_wallbox_state.write_text_atomically", side_effect=RuntimeError("boom")):
                service._runtime_state_serialized = None
                with patch("dbus_shelly_wallbox_state.logging.warning") as warning_mock:
                    controller.save_runtime_state()
                warning_mock.assert_called_once()

    def test_load_runtime_state_handles_missing_path_invalid_json_and_load_config_errors(self):
        service = make_runtime_state_service(
            virtual_mode=0,
            virtual_enable=0,
            virtual_startstop=1,
        )
        controller = ServiceStateController(service, lambda value: int(value))
        controller.load_runtime_state()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "state.json")
            service.runtime_state_path = path
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{bad json")
            with patch("dbus_shelly_wallbox_state.logging.warning") as warning_mock:
                controller.load_runtime_state()
            warning_mock.assert_called_once()

        parser = tempfile.TemporaryDirectory()
        self.addCleanup(parser.cleanup)
        missing_config_controller = ServiceStateController(service, int)
        with patch.object(ServiceStateController, "config_path", return_value=os.path.join(parser.name, "missing.ini")):
            with self.assertRaises(ValueError):
                missing_config_controller.load_config()
