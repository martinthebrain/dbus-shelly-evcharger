# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

from shelly_wallbox.runtime.audit import _RuntimeSupportAuditMixin
from shelly_wallbox.runtime.support import RuntimeSupportController
from tests.wallbox_test_fixtures import make_auto_metrics, make_runtime_support_service


class TestRuntimeSupportController(unittest.TestCase):
    @staticmethod
    def _age_zero(_captured_at: float | int | None, _now: float | int | None) -> int:
        return 0

    @staticmethod
    def _age_five(_captured_at: float | int | None, _now: float | int | None) -> int:
        return 5

    @staticmethod
    def _health_zero(_reason: str) -> int:
        return 0

    @staticmethod
    def _health_nine(_reason: str) -> int:
        return 9

    @staticmethod
    def _health_ten(_reason: str) -> int:
        return 10

    @staticmethod
    def _never_stale(_now: float) -> bool:
        return False

    @staticmethod
    def _always_stale(_now: float) -> bool:
        return True

    def test_runtime_and_worker_state_helpers_cover_defaults_snapshot_and_retries(self) -> None:
        service = make_runtime_support_service(_time_now=lambda: 100.0)
        controller = RuntimeSupportController(service, self._age_five, self._health_nine)

        controller.initialize_runtime_support()
        controller.init_worker_state()
        self.assertEqual(service._worker_poll_interval_seconds, 1.0)
        self.assertIsNotNone(service._worker_snapshot_lock)
        self.assertEqual(service._worker_snapshot["captured_at"], 0.0)
        self.assertFalse(service._worker_snapshot["pm_confirmed"])
        self.assertFalse(service._last_pm_status_confirmed)
        self.assertIsNone(service._last_confirmed_pm_status)
        self.assertIsNone(service._last_confirmed_pm_status_at)
        self.assertEqual(service._last_auto_state, "idle")
        self.assertEqual(service._last_auto_state_code, 0)
        self.assertEqual(service.relay_sync_timeout_seconds, 3.0)
        self.assertIsNone(service._relay_sync_expected_state)
        self.assertIsNone(service._auto_input_snapshot_last_captured_at)
        self.assertIsNone(service._auto_input_snapshot_version)
        self.assertIsNone(service._last_charger_transport_reason)
        self.assertIsNone(service._last_charger_transport_source)
        self.assertIsNone(service._last_charger_transport_detail)
        self.assertIsNone(service._last_charger_transport_at)
        self.assertIsNone(service._charger_retry_reason)
        self.assertIsNone(service._charger_retry_source)
        self.assertIsNone(service._charger_retry_until)

        partial_service = SimpleNamespace(poll_interval_ms=500, deviceinstance=61)
        partial_controller = RuntimeSupportController(partial_service, self._age_zero, self._health_zero)
        partial_controller.ensure_worker_state()
        partial_controller.ensure_observability_state()
        self.assertEqual(partial_service.auto_input_snapshot_path, "/run/dbus-shelly-wallbox-auto-61.json")
        self.assertIn("cache_hits", partial_service._error_state)
        self.assertFalse(partial_service._worker_snapshot["pm_confirmed"])
        self.assertFalse(partial_service._last_pm_status_confirmed)
        self.assertIsNone(partial_service._last_confirmed_pm_status)
        self.assertIsNone(partial_service._last_confirmed_pm_status_at)
        self.assertIsNone(partial_service._auto_input_snapshot_last_captured_at)
        self.assertIsNone(partial_service._auto_input_snapshot_version)
        self.assertIsNone(partial_service._last_charger_transport_reason)
        self.assertIsNone(partial_service._last_charger_transport_source)
        self.assertIsNone(partial_service._last_charger_transport_detail)
        self.assertIsNone(partial_service._last_charger_transport_at)
        self.assertIsNone(partial_service._charger_retry_reason)
        self.assertIsNone(partial_service._charger_retry_source)
        self.assertIsNone(partial_service._charger_retry_until)

        pm_status: dict[str, object] = {"output": True}
        snapshot: dict[str, object] = {"captured_at": 1.0, "pm_status": pm_status}
        partial_service._ensure_worker_state = MagicMock()
        partial_controller.set_worker_snapshot(snapshot)
        pm_status["output"] = False
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
        with patch("shelly_wallbox.runtime.support.time.time", return_value=100.0):
            partial_controller.delay_source_retry("dbus")
        self.assertFalse(partial_controller.source_retry_ready("dbus", 100.0))
        self.assertEqual(partial_controller.source_retry_remaining("dbus", 102.0), 3)
        self.assertTrue(partial_controller.source_retry_ready("dbus", 106.0))

    def test_runtime_support_setup_helpers_cover_uptime_and_local_version_edges(self) -> None:
        controller = RuntimeSupportController(SimpleNamespace(), self._age_zero, self._health_zero)

        with patch("builtins.open", side_effect=OSError("no uptime")):
            self.assertIsNone(controller._system_uptime_seconds())
        with patch("builtins.open", mock_open(read_data="bad value\n")):
            self.assertIsNone(controller._system_uptime_seconds())
        with patch("builtins.open", mock_open(read_data="12.5 0.0\n")):
            self.assertEqual(controller._system_uptime_seconds(), 12.5)
        self.assertIsNone(controller._boot_delayed_update_due_at(100.0, 10.0))
        with patch.object(RuntimeSupportController, "_system_uptime_seconds", return_value=3.0):
            self.assertEqual(controller._boot_delayed_update_due_at(100.0, 10.0), 107.0)

        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = temp_dir
            state_dir = os.path.join(repo_root, ".bootstrap-state")
            os.makedirs(state_dir, exist_ok=True)
            with open(os.path.join(state_dir, "installed_version"), "w", encoding="utf-8") as handle:
                handle.write("\n")
            with open(os.path.join(repo_root, "version.txt"), "w", encoding="utf-8") as handle:
                handle.write("2.3.4\n")
            self.assertEqual(controller._read_local_version(repo_root), "2.3.4")

        with patch("os.path.isfile", return_value=True), patch("builtins.open", side_effect=OSError("locked")):
            self.assertEqual(controller._read_local_version("/tmp/repo"), "")

    def test_runtime_audit_helpers_cover_remaining_scalar_edges(self) -> None:
        service = SimpleNamespace(
            _last_charger_state_phase_selection=0,
            _time_now=lambda: "bad",
            _phase_switch_lockout_selection=None,
            _phase_switch_lockout_until=200.0,
            _contactor_fault_counts=[],
            _contactor_fault_active_reason="",
        )

        self.assertEqual(_RuntimeSupportAuditMixin._observed_phase_for_audit(service), "0")
        self.assertFalse(_RuntimeSupportAuditMixin._phase_lockout_active_for_audit(service))
        self.assertIsNone(_RuntimeSupportAuditMixin._phase_lockout_target_for_audit(service))
        self.assertEqual(_RuntimeSupportAuditMixin._contactor_fault_count_for_audit(service), 0)
        self.assertIsNone(_RuntimeSupportAuditMixin._callable_time_or_none(lambda: "bad"))

    def test_runtime_audit_phase_lockout_target_returns_none_for_missing_selection_even_when_active(self) -> None:
        service = SimpleNamespace(_phase_switch_lockout_selection=None)

        with patch.object(_RuntimeSupportAuditMixin, "_phase_lockout_active_for_audit", return_value=True):
            self.assertIsNone(_RuntimeSupportAuditMixin._phase_lockout_target_for_audit(service))

    def test_worker_snapshot_contract_normalizes_pm_invariants(self) -> None:
        partial_service = SimpleNamespace(poll_interval_ms=500, deviceinstance=61, _time_now=lambda: 100.0)
        controller = RuntimeSupportController(partial_service, self._age_zero, self._health_zero)
        controller.ensure_worker_state()
        partial_service._ensure_worker_state = MagicMock()

        controller.set_worker_snapshot(
            {
                "captured_at": 10.0,
                "pm_captured_at": 12.0,
                "pm_status": {"apower": 1800.0},
                "pm_confirmed": True,
            }
        )
        snapshot = controller.get_worker_snapshot()
        self.assertEqual(snapshot["captured_at"], 10.0)
        self.assertIsNone(snapshot["pm_status"])
        self.assertIsNone(snapshot["pm_captured_at"])
        self.assertFalse(snapshot["pm_confirmed"])

        controller.update_worker_snapshot(captured_at=20.0, pm_status={"output": True}, pm_confirmed=True)
        snapshot = controller.get_worker_snapshot()
        self.assertEqual(snapshot["captured_at"], 20.0)
        self.assertEqual(snapshot["pm_captured_at"], 20.0)
        self.assertEqual(snapshot["pm_status"], {"output": True})
        self.assertTrue(snapshot["pm_confirmed"])

    def test_audit_helpers_and_watchdog_cover_remaining_branches(self) -> None:
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
                _last_auto_audit_event_at=995.0,
                auto_watchdog_stale_seconds=0.0,
                started_at=900.0,
                auto_watchdog_recovery_seconds=0.0,
                _last_recovery_attempt_at=990.0,
            )
            controller = RuntimeSupportController(service, self._age_zero, self._health_ten)

            self.assertEqual(controller._relay_state_for_audit(service), 0)
            self.assertIn("surplus=na", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertIn("detail=na", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertIn("state=idle", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertIn("relay=0", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertIn(
                "threshold_mode=na",
                controller._format_auto_audit_line(service, "waiting", False, 1000.0),
            )
            self.assertIn(
                "learned_charge_power_state=na",
                controller._format_auto_audit_line(service, "waiting", False, 1000.0),
            )
            self.assertIn(
                "charger_transport_reason=na",
                controller._format_auto_audit_line(service, "waiting", False, 1000.0),
            )
            self.assertIn(
                "charger_transport_source=na",
                controller._format_auto_audit_line(service, "waiting", False, 1000.0),
            )
            self.assertIn(
                "charger_retry_reason=na",
                controller._format_auto_audit_line(service, "waiting", False, 1000.0),
            )
            self.assertIn(
                "charger_retry_source=na",
                controller._format_auto_audit_line(service, "waiting", False, 1000.0),
            )
            self.assertIn("phase_observed=na", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertIn("phase_mismatch=0", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertIn("phase_lockout_target=na", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertIn("phase_lockout=0", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertIn("phase_effective=P1", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertIn("phase_degraded=0", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertIn("switch_feedback=na", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertIn("switch_interlock=na", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertIn(
                "switch_feedback_mismatch=0",
                controller._format_auto_audit_line(service, "waiting", False, 1000.0),
            )
            self.assertIn("contactor_fault_count=0", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertIn("contactor_lockout_reason=na", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertIn("contactor_lockout=0", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertIn("fault=0", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertIn("fault_reason=na", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertIn("recovery=0", controller._format_auto_audit_line(service, "waiting", False, 1000.0))
            self.assertEqual(
                controller._prune_auto_audit_payload(["", "bad-line", "500\told\n", "1500\tnew\n"], 1000.0),
                ["bad-line", "1500\tnew\n"],
            )
            self.assertFalse(controller.is_update_stale(1000.0))
            self.assertTrue(controller._watchdog_recovery_suppressed(service, 1000.0))
            service._is_update_stale = self._never_stale
            controller.watchdog_recover(1000.0)
            service._reset_system_bus.assert_not_called()

            service._last_auto_audit_key = controller._auto_audit_key(service, "waiting", False)
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
            with patch("shelly_wallbox.runtime.audit.write_text_atomically", side_effect=RuntimeError("boom")):
                controller._cleanup_auto_audit_log(10000.0)

            service.auto_watchdog_stale_seconds = 10.0
            service.auto_watchdog_recovery_seconds = 5.0
            service._last_recovery_attempt_at = None
            service.started_at = 0.0
            service._is_update_stale = self._always_stale
            controller.watchdog_recover(20.0)
            service._reset_system_bus.assert_called_once_with()

    def test_audit_prefers_last_confirmed_relay_state_over_local_placeholder(self) -> None:
        service = make_runtime_support_service(
            _last_pm_status={"output": True},
            _last_pm_status_confirmed=False,
            _last_confirmed_pm_status={"output": False},
            _last_confirmed_pm_status_at=95.0,
            virtual_startstop=1,
        )
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)

        self.assertEqual(controller._relay_state_for_audit(service), 0)
        self.assertIn("relay=0", controller._format_auto_audit_line(service, "waiting", False, 100.0))

    def test_audit_normalizes_state_and_sanitizes_invalid_threshold_metrics(self) -> None:
        service = make_runtime_support_service(
            _last_auto_state="odd-state",
            _last_auto_state_code=99,
            _last_auto_metrics={
                "surplus": "bad",
                "grid": -900.0,
                "soc": 150.0,
                "profile": 7,
                "start_threshold": 1200.0,
                "stop_threshold": 1850.0,
                "learned_charge_power": -1.0,
                "learned_charge_power_state": "mystery",
                "threshold_scale": "bad",
                "threshold_mode": 4,
            },
        )
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)

        line = controller._format_auto_audit_line(service, "waiting", False, 100.0)
        audit_key = controller._auto_audit_key(service, "waiting", False)

        self.assertIn("state=idle", line)
        self.assertIn("profile=7", line)
        self.assertIn("surplus=na", line)
        self.assertIn("soc=na", line)
        self.assertIn("start_threshold=na", line)
        self.assertIn("stop_threshold=na", line)
        self.assertIn("learned_charge_power=na", line)
        self.assertIn("learned_charge_power_state=unknown", line)
        self.assertIn("threshold_scale=na", line)
        self.assertEqual(audit_key[7], "idle")
        self.assertIsNone(audit_key[12])
        self.assertIsNone(audit_key[13])
        self.assertIsNone(audit_key[14])

    def test_audit_ignores_stale_confirmed_relay_state_instead_of_virtual_placeholder(self) -> None:
        service = make_runtime_support_service(
            _time_now=lambda: 100.0,
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=80.0,
            virtual_startstop=1,
        )
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)

        self.assertEqual(controller._relay_state_for_audit(service), 0)
        self.assertIn("relay=0", controller._format_auto_audit_line(service, "waiting", False, 100.0))

    def test_audit_and_watchdog_early_returns_cover_remaining_branches(self) -> None:
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
                _is_update_stale=self._always_stale,
            )
            controller = RuntimeSupportController(service, self._age_zero, self._health_ten)

            controller._cleanup_auto_audit_log(1000.0)
            with open(path, "r", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "999999\tfresh\n")

            controller.write_auto_audit_event("waiting-grid", cached=False)
            self.assertIsNone(service._last_auto_audit_key)

            controller.watchdog_recover(1000.0)
            service._reset_system_bus.assert_not_called()

    def test_warning_throttled_logs_once_per_interval(self) -> None:
        service = SimpleNamespace(
            _ensure_observability_state=MagicMock(),
            _warning_state={},
        )
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)

        with patch("shelly_wallbox.runtime.support.time.time", side_effect=[100.0, 105.0, 131.0]):
            with patch("shelly_wallbox.runtime.support.logging.warning") as warning_mock:
                controller.warning_throttled("worker-failed", 30.0, "worker failed: %s", "boom")
                controller.warning_throttled("worker-failed", 30.0, "worker failed: %s", "boom")
                controller.warning_throttled("worker-failed", 30.0, "worker failed: %s", "boom")

        service._ensure_observability_state.assert_called()
        self.assertEqual(warning_mock.call_count, 2)
        self.assertEqual(service._warning_state["worker-failed"], 131.0)

    def test_bucket_metric_returns_raw_value_when_step_is_not_positive(self) -> None:
        self.assertEqual(RuntimeSupportController._bucket_metric(1.23, step=0.0), 1.23)
        self.assertIsNone(RuntimeSupportController._bucket_metric(None, step=50.0))

    def test_write_auto_audit_event_deduplicates_identical_reason_within_repeat_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            current_time = [1000.0]
            service = make_runtime_support_service(
                _time_now=lambda: current_time[0],
                auto_audit_log_path=path,
                supported_phase_selections=("P1", "P1_P2", "P1_P2_P3"),
                _last_auto_metrics=make_auto_metrics(),
            )

            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
            controller.write_auto_audit_event("waiting-surplus", cached=False)
            current_time[0] = 1005.0
            service._last_auto_metrics = {
                "surplus": 620.0,
                "grid": -710.0,
                "soc": 55.0,
                "profile": "normal",
                "start_threshold": 1850.0,
                "stop_threshold": 1350.0,
                "learned_charge_power": None,
                "learned_charge_power_state": "unknown",
                "threshold_scale": 1.0,
                "threshold_mode": "static",
            }
            controller.write_auto_audit_event("waiting-surplus", cached=False)

            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()

        self.assertEqual(len(lines), 1)
        self.assertIn("reason=waiting-surplus", lines[0])

    def test_write_auto_audit_event_repeats_same_reason_after_repeat_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            current_time = [1000.0]
            service = make_runtime_support_service(
                _time_now=lambda: current_time[0],
                auto_audit_log_path=path,
                _last_auto_metrics=make_auto_metrics(),
            )

            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
            controller.write_auto_audit_event("waiting-surplus", cached=False)
            current_time[0] = 1031.0
            service._last_auto_metrics = {
                "surplus": 810.0,
                "grid": -860.0,
                "soc": 55.0,
                "profile": "normal",
                "start_threshold": 1850.0,
                "stop_threshold": 1350.0,
                "learned_charge_power": None,
                "learned_charge_power_state": "unknown",
                "threshold_scale": 1.0,
                "threshold_mode": "static",
            }
            controller.write_auto_audit_event("waiting-surplus", cached=False)

            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()

        self.assertEqual(len(lines), 2)
        self.assertTrue(all("reason=waiting-surplus" in line for line in lines))

    def test_write_auto_audit_event_repeats_same_reason_when_threshold_bucket_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            current_time = [1000.0]
            service = make_runtime_support_service(
                _time_now=lambda: current_time[0],
                auto_audit_log_path=path,
                _last_auto_metrics=make_auto_metrics(
                    learned_charge_power=1900.0,
                    learned_charge_power_state="stable",
                    threshold_scale=1.0,
                    threshold_mode="adaptive",
                ),
            )

            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
            controller.write_auto_audit_event("waiting-surplus", cached=False)
            current_time[0] = 1005.0
            service._last_auto_metrics = make_auto_metrics(
                start_threshold=1980.0,
                stop_threshold=960.0,
                learned_charge_power=2280.0,
                learned_charge_power_state="stable",
                threshold_scale=1.2,
                threshold_mode="adaptive",
            )
            controller.write_auto_audit_event("waiting-surplus", cached=False)

            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()

        self.assertEqual(len(lines), 2)
        self.assertIn("threshold_scale=1.000", lines[0])
        self.assertIn("threshold_scale=1.200", lines[1])

    def test_write_auto_audit_event_prunes_old_entries_by_age(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            current_time = [1000.0]
            service = make_runtime_support_service(
                _time_now=lambda: current_time[0],
                auto_audit_log_path=path,
                auto_audit_log_max_age_hours=0.1,
                _last_auto_metrics=make_auto_metrics(),
            )

            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
            controller.write_auto_audit_event("waiting-surplus", cached=False)

            current_time[0] = 2000.0
            controller.write_auto_audit_event("waiting-grid", cached=True)

            with open(path, "r", encoding="utf-8") as handle:
                payload = handle.read()

        self.assertNotIn("reason=waiting-surplus", payload)
        self.assertIn("reason=waiting-grid", payload)
        self.assertIn("cached=1", payload)

    def test_write_auto_audit_event_includes_stop_reason_detail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            service = make_runtime_support_service(
                _time_now=lambda: 1000.0,
                auto_audit_log_path=path,
                _last_pm_status={"output": True},
                virtual_startstop=1,
                backend_mode="split",
                meter_backend_type="template_meter",
                switch_backend_type="template_switch",
                charger_backend_type="template_charger",
                _charger_target_current_amps=13.0,
                _last_charger_transport_reason="offline",
                _last_charger_transport_source="read",
                _last_charger_transport_detail="Modbus slave 1 did not respond",
                _last_charger_transport_at=1000.0,
                _charger_retry_reason="offline",
                _charger_retry_source="read",
                _charger_retry_until=1005.0,
                _last_confirmed_pm_status={"_phase_selection": "P1_P2", "output": True},
                _last_confirmed_pm_status_at=1000.0,
                _phase_switch_mismatch_active=True,
                _last_switch_feedback_closed=False,
                _last_switch_interlock_ok=True,
                auto_stop_condition_reason="auto-stop-surplus",
                _last_auto_metrics=make_auto_metrics(
                    surplus=900.0,
                    grid=-100.0,
                    soc=61.0,
                    profile="high-soc",
                    start_threshold=1650.0,
                    stop_threshold=800.0,
                    learned_charge_power=2280.0,
                    learned_charge_power_state="stable",
                    threshold_scale=1.2,
                    threshold_mode="adaptive",
                    stop_alpha=0.15,
                    stop_alpha_stage="volatile",
                    surplus_volatility=520.0,
                ),
            )
            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
            controller.write_auto_audit_event("auto-stop", cached=False)

            with open(path, "r", encoding="utf-8") as handle:
                payload = handle.read()

        self.assertIn("reason=auto-stop", payload)
        self.assertIn("detail=surplus", payload)
        self.assertIn("profile=high-soc", payload)
        self.assertIn("start_threshold=1650W", payload)
        self.assertIn("stop_threshold=800W", payload)
        self.assertIn("learned_charge_power=2280W", payload)
        self.assertIn("learned_charge_power_state=stable", payload)
        self.assertIn("threshold_scale=1.200", payload)
        self.assertIn("threshold_mode=adaptive", payload)
        self.assertIn("backend_mode=split", payload)
        self.assertIn("meter_backend=template_meter", payload)
        self.assertIn("switch_backend=template_switch", payload)
        self.assertIn("charger_backend=template_charger", payload)
        self.assertIn("charger_target=13.0A", payload)
        self.assertIn("charger_transport_reason=offline", payload)
        self.assertIn("charger_transport_source=read", payload)
        self.assertIn("charger_retry_reason=offline", payload)
        self.assertIn("charger_retry_source=read", payload)
        self.assertIn("phase_observed=P1_P2", payload)
        self.assertIn("phase_mismatch=1", payload)
        self.assertIn("switch_feedback=0", payload)
        self.assertIn("switch_interlock=1", payload)
        self.assertIn("switch_feedback_mismatch=1", payload)
        self.assertIn("stop_alpha=0.15", payload)
        self.assertIn("stop_alpha_stage=volatile", payload)
        self.assertIn("surplus_volatility=520W", payload)

    def test_auto_audit_reason_detail_ignores_non_string_stop_reasons(self) -> None:
        service = make_runtime_support_service(auto_stop_condition_reason=1)

        self.assertIsNone(RuntimeSupportController._auto_audit_reason_detail(service, "auto-stop"))

    def test_write_auto_audit_event_deduplicates_by_stop_reason_detail(self) -> None:
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

            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
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
                "learned_charge_power": None,
                "learned_charge_power_state": "unknown",
                "threshold_scale": 1.0,
                "threshold_mode": "static",
            }
            controller.write_auto_audit_event("auto-stop", cached=False)

            with open(path, "r", encoding="utf-8") as handle:
                payload = handle.read()

        self.assertIn("detail=grid", payload)
        self.assertIn("detail=soc", payload)

    def test_write_auto_audit_event_ignores_unwritable_log_path(self) -> None:
        service = make_runtime_support_service(
            _time_now=lambda: 1000.0,
            auto_audit_log_path="/root/forbidden/auto-reasons.log",
            _last_auto_metrics=make_auto_metrics(),
        )

        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        with patch("shelly_wallbox.runtime.support.os.makedirs", side_effect=PermissionError("nope")):
            controller.write_auto_audit_event("waiting-surplus", cached=False)

        self.assertIsNone(service._last_auto_audit_key)
        self.assertIsNone(service._last_auto_audit_event_at)

    def test_write_auto_audit_event_retries_identical_reason_after_failed_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            current_time = [1000.0]
            service = make_runtime_support_service(
                _time_now=lambda: current_time[0],
                auto_audit_log_path=path,
                _last_auto_metrics=make_auto_metrics(),
            )

            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
            with patch("shelly_wallbox.runtime.support.os.makedirs", side_effect=PermissionError("nope")):
                controller.write_auto_audit_event("waiting-surplus", cached=False)

            self.assertIsNone(service._last_auto_audit_key)

            current_time[0] = 1005.0
            controller.write_auto_audit_event("waiting-surplus", cached=False)

            with open(path, "r", encoding="utf-8") as handle:
                payload = handle.read()

        self.assertIn("reason=waiting-surplus", payload)

    def test_write_auto_audit_event_repeats_same_reason_when_charger_target_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            current_time = [1000.0]
            service = make_runtime_support_service(
                _time_now=lambda: current_time[0],
                auto_audit_log_path=path,
                backend_mode="split",
                charger_backend_type="template_charger",
                _charger_target_current_amps=10.0,
                _last_auto_metrics=make_auto_metrics(),
            )

            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
            controller.write_auto_audit_event("waiting-surplus", cached=False)
            current_time[0] = 1005.0
            service._charger_target_current_amps = 13.0
            controller.write_auto_audit_event("waiting-surplus", cached=False)

            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()

        self.assertEqual(len(lines), 2)
        self.assertIn("charger_target=10.0A", lines[0])
        self.assertIn("charger_target=13.0A", lines[1])

    def test_write_auto_audit_event_repeats_same_reason_when_phase_lockout_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            current_time = [1000.0]
            service = make_runtime_support_service(
                _time_now=lambda: current_time[0],
                auto_audit_log_path=path,
                _last_auto_metrics=make_auto_metrics(),
                supported_phase_selections=("P1", "P1_P2", "P1_P2_P3"),
            )

            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
            controller.write_auto_audit_event("waiting-surplus", cached=False)
            current_time[0] = 1005.0
            service._phase_switch_lockout_selection = "P1_P2"
            service._phase_switch_lockout_reason = "mismatch-threshold"
            service._phase_switch_lockout_at = 1005.0
            service._phase_switch_lockout_until = 1065.0
            controller.write_auto_audit_event("waiting-surplus", cached=False)

            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()

        self.assertEqual(len(lines), 2)
        self.assertIn("phase_lockout=0", lines[0])
        self.assertIn("phase_lockout=1", lines[1])
        self.assertIn("phase_lockout_target=P1_P2", lines[1])
        self.assertIn("phase_degraded=1", lines[1])

    def test_write_auto_audit_event_repeats_same_reason_when_contactor_lockout_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            current_time = [1000.0]
            service = make_runtime_support_service(
                _time_now=lambda: current_time[0],
                auto_audit_log_path=path,
                _last_auto_metrics=make_auto_metrics(),
            )

            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
            controller.write_auto_audit_event("waiting-surplus", cached=False)
            current_time[0] = 1005.0
            service._last_health_reason = "contactor-lockout-open"
            service._last_auto_state = "recovery"
            service._last_auto_state_code = 5
            service._contactor_fault_counts = {"contactor-suspected-open": 3}
            service._contactor_lockout_reason = "contactor-suspected-open"
            service._contactor_lockout_source = "count-threshold"
            service._contactor_lockout_at = 1005.0
            controller.write_auto_audit_event("waiting-surplus", cached=False)

            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()

        self.assertEqual(len(lines), 2)
        self.assertIn("contactor_lockout=0", lines[0])
        self.assertIn("contactor_lockout=1", lines[1])
        self.assertIn("contactor_lockout_reason=contactor-suspected-open", lines[1])
        self.assertIn("contactor_fault_count=3", lines[1])
        self.assertIn("fault=1", lines[1])
        self.assertIn("fault_reason=contactor-lockout-open", lines[1])
        self.assertIn("recovery=1", lines[1])
