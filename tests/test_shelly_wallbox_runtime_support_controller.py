# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from dbus_shelly_wallbox_runtime_support import RuntimeSupportController
from tests.wallbox_test_fixtures import make_auto_metrics, make_runtime_support_service


class TestRuntimeSupportController(unittest.TestCase):
    def test_runtime_and_worker_state_helpers_cover_defaults_snapshot_and_retries(self):
        service = make_runtime_support_service(_time_now=lambda: 100.0)
        controller = RuntimeSupportController(service, lambda *_args, **_kwargs: 5, lambda *_args, **_kwargs: 9)

        controller.initialize_runtime_support()
        controller.init_worker_state()
        self.assertEqual(service._worker_poll_interval_seconds, 1.0)
        self.assertIsNotNone(service._worker_snapshot_lock)
        self.assertEqual(service._worker_snapshot["captured_at"], 0.0)

        partial_service = SimpleNamespace(poll_interval_ms=500, deviceinstance=61)
        partial_controller = RuntimeSupportController(partial_service, lambda *_args, **_kwargs: 0, lambda *_args, **_kwargs: 0)
        partial_controller.ensure_worker_state()
        partial_controller.ensure_observability_state()
        self.assertEqual(partial_service.auto_input_snapshot_path, "/run/dbus-shelly-wallbox-auto-61.json")
        self.assertIn("cache_hits", partial_service._error_state)

        snapshot = {"captured_at": 1.0, "pm_status": {"output": True}}
        partial_service._ensure_worker_state = MagicMock()
        partial_controller.set_worker_snapshot(snapshot)
        snapshot["pm_status"]["output"] = False
        self.assertTrue(partial_service._worker_snapshot["pm_status"]["output"])

        partial_controller.update_worker_snapshot(grid_power=-500.0)
        self.assertEqual(partial_controller.get_worker_snapshot()["grid_power"], -500.0)

        partial_service._ensure_observability_state = MagicMock()
        partial_service._warning_state = {}
        partial_service._failure_active = {"dbus": True}
        partial_service._source_retry_after = {}
        partial_service._error_state = {"dbus": 0}
        partial_service.auto_dbus_backoff_base_seconds = 5.0
        partial_controller.mark_failure("dbus")
        self.assertEqual(partial_service._error_state["dbus"], 1)
        partial_controller.mark_recovery("dbus", "recovered %s", "ok")
        self.assertFalse(partial_service._failure_active["dbus"])
        self.assertEqual(partial_service._source_retry_after["dbus"], 0.0)
        with patch("dbus_shelly_wallbox_runtime_support.time.time", return_value=100.0):
            partial_controller.delay_source_retry("dbus")
        self.assertFalse(partial_controller.source_retry_ready("dbus", 100.0))
        self.assertTrue(partial_controller.source_retry_ready("dbus", 106.0))

    def test_audit_helpers_and_watchdog_cover_remaining_branches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto.log"
            service = make_runtime_support_service(
                _time_now=lambda: 1000.0,
                _last_pm_status=None,
                virtual_startstop=1,
                _last_auto_metrics={"surplus": None, "grid": None, "soc": None},
                auto_audit_log_path=path,
                auto_audit_log_max_age_hours=0.0,
                auto_audit_log_repeat_seconds=0.0,
                _last_auto_audit_key=("waiting", None, 0, 1, 1, 1, 1, None, None),
                _last_auto_audit_event_at=995.0,
                auto_watchdog_stale_seconds=0.0,
                started_at=900.0,
                auto_watchdog_recovery_seconds=0.0,
                _last_recovery_attempt_at=990.0,
            )
            controller = RuntimeSupportController(service, lambda *_args, **_kwargs: 10, lambda *_args, **_kwargs: 0)

            self.assertEqual(controller._relay_state_for_audit(service), 1)
            self.assertIn("surplus=na", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertIn("detail=na", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertEqual(
                controller._prune_auto_audit_payload(["", "bad-line", "500\told\n", "1500\tnew\n"], 1000.0),
                ["bad-line", "1500\tnew\n"],
            )
            self.assertFalse(controller.is_update_stale(1000.0))
            self.assertTrue(controller._watchdog_recovery_suppressed(service, 1000.0))
            service._is_update_stale = lambda now: False
            controller.watchdog_recover(1000.0)
            service._reset_system_bus.assert_not_called()

            controller.write_auto_audit_event("waiting", cached=False)
            self.assertFalse(os.path.exists(path))

            service.auto_audit_log_path = ""
            service._last_auto_audit_key = None
            service._last_auto_audit_event_at = None
            controller.write_auto_audit_event("running", cached=True)
            self.assertIsNone(service._last_auto_audit_key)
            self.assertIsNone(service._last_auto_audit_event_at)

            service.auto_audit_log_path = path
            service.auto_audit_log_max_age_hours = 1.0
            service._last_auto_audit_cleanup_at = 0.0
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("1\told\n")
            with patch("dbus_shelly_wallbox_runtime_support.write_text_atomically", side_effect=RuntimeError("boom")):
                controller._cleanup_auto_audit_log(10000.0)

            service.auto_watchdog_stale_seconds = 10.0
            service.auto_watchdog_recovery_seconds = 5.0
            service._last_recovery_attempt_at = None
            service.started_at = 0.0
            service._is_update_stale = lambda now: True
            controller.watchdog_recover(20.0)
            service._reset_system_bus.assert_called_once_with()

    def test_audit_and_watchdog_early_returns_cover_remaining_branches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto.log"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("999999\tfresh\n")

            service = make_runtime_support_service(
                _time_now=lambda: 1000.0,
                auto_audit_log=False,
                auto_audit_log_path=path,
                _last_pm_status=None,
                _last_auto_metrics={"surplus": None, "grid": None, "soc": None},
                _last_recovery_attempt_at=995.0,
                _is_update_stale=lambda now: True,
            )
            controller = RuntimeSupportController(service, lambda *_args, **_kwargs: 10, lambda *_args, **_kwargs: 0)

            controller._cleanup_auto_audit_log(1000.0)
            with open(path, "r", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "999999\tfresh\n")

            controller.write_auto_audit_event("waiting-grid", cached=False)
            self.assertIsNone(service._last_auto_audit_key)

            controller.watchdog_recover(1000.0)
            service._reset_system_bus.assert_not_called()

    def test_warning_throttled_logs_once_per_interval(self):
        service = SimpleNamespace(
            _ensure_observability_state=MagicMock(),
            _warning_state={},
        )
        controller = RuntimeSupportController(service, lambda *_args, **_kwargs: 0, lambda *_args, **_kwargs: 0)

        with patch("dbus_shelly_wallbox_runtime_support.time.time", side_effect=[100.0, 105.0, 131.0]):
            with patch("dbus_shelly_wallbox_runtime_support.logging.warning") as warning_mock:
                controller.warning_throttled("worker-failed", 30.0, "worker failed: %s", "boom")
                controller.warning_throttled("worker-failed", 30.0, "worker failed: %s", "boom")
                controller.warning_throttled("worker-failed", 30.0, "worker failed: %s", "boom")

        service._ensure_observability_state.assert_called()
        self.assertEqual(warning_mock.call_count, 2)
        self.assertEqual(service._warning_state["worker-failed"], 131.0)

    def test_write_auto_audit_event_deduplicates_identical_reason_within_repeat_window(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            current_time = [1000.0]
            service = make_runtime_support_service(
                _time_now=lambda: current_time[0],
                auto_audit_log_path=path,
                _last_auto_metrics=make_auto_metrics(),
            )

            controller = RuntimeSupportController(service, lambda *_args, **_kwargs: 0, lambda *_args, **_kwargs: 0)
            controller.write_auto_audit_event("waiting-surplus", cached=False)
            current_time[0] = 1005.0
            service._last_auto_metrics = {
                "surplus": 620.0,
                "grid": -710.0,
                "soc": 55.0,
                "profile": "normal",
                "start_threshold": 1850.0,
                "stop_threshold": 1350.0,
            }
            controller.write_auto_audit_event("waiting-surplus", cached=False)

            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()

        self.assertEqual(len(lines), 1)
        self.assertIn("reason=waiting-surplus", lines[0])

    def test_write_auto_audit_event_repeats_same_reason_after_repeat_window(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            current_time = [1000.0]
            service = make_runtime_support_service(
                _time_now=lambda: current_time[0],
                auto_audit_log_path=path,
                _last_auto_metrics=make_auto_metrics(),
            )

            controller = RuntimeSupportController(service, lambda *_args, **_kwargs: 0, lambda *_args, **_kwargs: 0)
            controller.write_auto_audit_event("waiting-surplus", cached=False)
            current_time[0] = 1031.0
            service._last_auto_metrics = {
                "surplus": 810.0,
                "grid": -860.0,
                "soc": 55.0,
                "profile": "normal",
                "start_threshold": 1850.0,
                "stop_threshold": 1350.0,
            }
            controller.write_auto_audit_event("waiting-surplus", cached=False)

            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()

        self.assertEqual(len(lines), 2)
        self.assertTrue(all("reason=waiting-surplus" in line for line in lines))

    def test_write_auto_audit_event_prunes_old_entries_by_age(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            current_time = [1000.0]
            service = make_runtime_support_service(
                _time_now=lambda: current_time[0],
                auto_audit_log_path=path,
                auto_audit_log_max_age_hours=0.1,
                _last_auto_metrics=make_auto_metrics(),
            )

            controller = RuntimeSupportController(service, lambda *_args, **_kwargs: 0, lambda *_args, **_kwargs: 0)
            controller.write_auto_audit_event("waiting-surplus", cached=False)

            current_time[0] = 2000.0
            controller.write_auto_audit_event("waiting-grid", cached=True)

            with open(path, "r", encoding="utf-8") as handle:
                payload = handle.read()

        self.assertNotIn("reason=waiting-surplus", payload)
        self.assertIn("reason=waiting-grid", payload)
        self.assertIn("cached=1", payload)

    def test_write_auto_audit_event_includes_stop_reason_detail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            service = make_runtime_support_service(
                _time_now=lambda: 1000.0,
                auto_audit_log_path=path,
                _last_pm_status={"output": True},
                virtual_startstop=1,
                auto_stop_condition_reason="auto-stop-surplus",
                _last_auto_metrics=make_auto_metrics(
                    surplus=900.0,
                    grid=-100.0,
                    soc=61.0,
                    profile="high-soc",
                    start_threshold=1650.0,
                    stop_threshold=800.0,
                    stop_alpha=0.15,
                    stop_alpha_stage="volatile",
                    surplus_volatility=520.0,
                ),
            )
            controller = RuntimeSupportController(service, lambda *_args, **_kwargs: 0, lambda *_args, **_kwargs: 0)
            controller.write_auto_audit_event("auto-stop", cached=False)

            with open(path, "r", encoding="utf-8") as handle:
                payload = handle.read()

        self.assertIn("reason=auto-stop", payload)
        self.assertIn("detail=surplus", payload)
        self.assertIn("profile=high-soc", payload)
        self.assertIn("start_threshold=1650W", payload)
        self.assertIn("stop_threshold=800W", payload)
        self.assertIn("stop_alpha=0.15", payload)
        self.assertIn("stop_alpha_stage=volatile", payload)
        self.assertIn("surplus_volatility=520W", payload)

    def test_auto_audit_reason_detail_ignores_non_string_stop_reasons(self):
        service = make_runtime_support_service(auto_stop_condition_reason=1)

        self.assertIsNone(RuntimeSupportController._auto_audit_reason_detail(service, "auto-stop"))

    def test_write_auto_audit_event_deduplicates_by_stop_reason_detail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            current_time = [1000.0]
            service = make_runtime_support_service(
                _time_now=lambda: current_time[0],
                auto_audit_log_path=path,
                _last_pm_status={"output": True},
                virtual_startstop=1,
                auto_stop_condition_reason="auto-stop-grid",
                _last_auto_metrics=make_auto_metrics(
                    surplus=1200.0,
                    grid=350.0,
                    soc=61.0,
                    profile="high-soc",
                    start_threshold=1650.0,
                    stop_threshold=800.0,
                ),
            )

            controller = RuntimeSupportController(service, lambda *_args, **_kwargs: 0, lambda *_args, **_kwargs: 0)
            controller.write_auto_audit_event("auto-stop", cached=False)
            current_time[0] = 1005.0
            service.auto_stop_condition_reason = "auto-stop-soc"
            service._last_auto_metrics = {
                "surplus": 1700.0,
                "grid": 0.0,
                "soc": 25.0,
                "profile": "normal",
                "start_threshold": 1850.0,
                "stop_threshold": 1350.0,
            }
            controller.write_auto_audit_event("auto-stop", cached=False)

            with open(path, "r", encoding="utf-8") as handle:
                payload = handle.read()

        self.assertIn("detail=grid", payload)
        self.assertIn("detail=soc", payload)

    def test_write_auto_audit_event_ignores_unwritable_log_path(self):
        service = make_runtime_support_service(
            _time_now=lambda: 1000.0,
            auto_audit_log_path="/root/forbidden/auto-reasons.log",
            _last_auto_metrics=make_auto_metrics(),
        )

        controller = RuntimeSupportController(service, lambda *_args, **_kwargs: 0, lambda *_args, **_kwargs: 0)
        with patch("dbus_shelly_wallbox_runtime_support.os.makedirs", side_effect=PermissionError("nope")):
            controller.write_auto_audit_event("waiting-surplus", cached=False)

        self.assertIsNone(service._last_auto_audit_key)
        self.assertIsNone(service._last_auto_audit_event_at)

    def test_write_auto_audit_event_retries_identical_reason_after_failed_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            current_time = [1000.0]
            service = make_runtime_support_service(
                _time_now=lambda: current_time[0],
                auto_audit_log_path=path,
                _last_auto_metrics=make_auto_metrics(),
            )

            controller = RuntimeSupportController(service, lambda *_args, **_kwargs: 0, lambda *_args, **_kwargs: 0)
            with patch("dbus_shelly_wallbox_runtime_support.os.makedirs", side_effect=PermissionError("nope")):
                controller.write_auto_audit_event("waiting-surplus", cached=False)

            self.assertIsNone(service._last_auto_audit_key)

            current_time[0] = 1005.0
            controller.write_auto_audit_event("waiting-surplus", cached=False)

            with open(path, "r", encoding="utf-8") as handle:
                payload = handle.read()

        self.assertIn("reason=waiting-surplus", payload)
