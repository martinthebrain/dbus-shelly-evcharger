# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
from unittest.mock import patch

from shelly_wallbox.runtime.support import RuntimeSupportController
from tests.wallbox_runtime_support_support import RuntimeSupportTestCaseBase
from tests.wallbox_test_fixtures import make_auto_metrics, make_runtime_support_service


class TestRuntimeSupportControllerAudit(RuntimeSupportTestCaseBase):
    def test_warning_throttled_logs_once_per_interval(self) -> None:
        service = type("Service", (), {"_ensure_observability_state": lambda _self: None, "_warning_state": {}})()
        controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
        with patch("shelly_wallbox.runtime.support.time.time", side_effect=[100.0, 105.0, 131.0]), patch("shelly_wallbox.runtime.support.logging.warning") as warning_mock:
            controller.warning_throttled("worker-failed", 30.0, "worker failed: %s", "boom")
            controller.warning_throttled("worker-failed", 30.0, "worker failed: %s", "boom")
            controller.warning_throttled("worker-failed", 30.0, "worker failed: %s", "boom")
        self.assertEqual(warning_mock.call_count, 2)

    def test_bucket_metric_returns_raw_value_when_step_is_not_positive(self) -> None:
        self.assertEqual(RuntimeSupportController._bucket_metric(1.23, step=0.0), 1.23)
        self.assertIsNone(RuntimeSupportController._bucket_metric(None, step=50.0))

    def test_write_auto_audit_event_variants_cover_repeat_pruning_and_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            current_time = [1000.0]
            service = make_runtime_support_service(_time_now=lambda: current_time[0], auto_audit_log_path=path, _last_auto_metrics=make_auto_metrics())
            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
            controller.write_auto_audit_event("waiting-surplus", cached=False)
            current_time[0] = 1031.0
            controller.write_auto_audit_event("waiting-surplus", cached=False)
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
            self.assertEqual(len(lines), 2)

    def test_write_auto_audit_event_includes_stop_reason_detail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            service = make_runtime_support_service(_time_now=lambda: 1000.0, auto_audit_log_path=path, _last_pm_status={"output": True}, virtual_startstop=1, backend_mode="split", meter_backend_type="template_meter", switch_backend_type="template_switch", charger_backend_type="template_charger", _charger_target_current_amps=13.0, _last_charger_transport_reason="offline", _last_charger_transport_source="read", _last_charger_transport_detail="Modbus slave 1 did not respond", _last_charger_transport_at=1000.0, _charger_retry_reason="offline", _charger_retry_source="read", _charger_retry_until=1005.0, _last_confirmed_pm_status={"_phase_selection": "P1_P2", "output": True}, _last_confirmed_pm_status_at=1000.0, _phase_switch_mismatch_active=True, _last_switch_feedback_closed=False, _last_switch_interlock_ok=True, auto_stop_condition_reason="auto-stop-surplus", _last_auto_metrics=make_auto_metrics(surplus=900.0, grid=-100.0, soc=61.0, profile="high-soc", start_threshold=1650.0, stop_threshold=800.0, learned_charge_power=2280.0, learned_charge_power_state="stable", threshold_scale=1.2, threshold_mode="adaptive", stop_alpha=0.15, stop_alpha_stage="volatile", surplus_volatility=520.0))
            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
            controller.write_auto_audit_event("auto-stop", cached=False)
            with open(path, "r", encoding="utf-8") as handle:
                payload = handle.read()
            self.assertIn("detail=surplus", payload)
            self.assertIn("charger_backend=template_charger", payload)

    def test_write_auto_audit_event_retry_and_state_change_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/auto-reasons.log"
            current_time = [1000.0]
            service = make_runtime_support_service(_time_now=lambda: current_time[0], auto_audit_log_path=path, _last_auto_metrics=make_auto_metrics())
            controller = RuntimeSupportController(service, self._age_zero, self._health_zero)
            with patch("shelly_wallbox.runtime.support.os.makedirs", side_effect=PermissionError("nope")):
                controller.write_auto_audit_event("waiting-surplus", cached=False)
            self.assertIsNone(service._last_auto_audit_key)
            current_time[0] = 1005.0
            controller.write_auto_audit_event("waiting-surplus", cached=False)
            with open(path, "r", encoding="utf-8") as handle:
                payload = handle.read()
            self.assertIn("reason=waiting-surplus", payload)
