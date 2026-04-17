# SPDX-License-Identifier: GPL-3.0-or-later
import math
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from shelly_wallbox.backend.factory import build_service_backends
from shelly_wallbox.backend.modbus_transport import ModbusRequest, ModbusSlaveOfflineError
from shelly_wallbox.backend.shelly_io import ShellyIoController
from shelly_wallbox.auto.policy import AutoPolicy
from shelly_wallbox.update.controller import UpdateCycleController


def _phase_values(total_power, voltage, _phase, _voltage_mode):
    current = (total_power / voltage) if voltage else 0.0
    return {
        "L1": {"power": total_power, "voltage": voltage, "current": current},
        "L2": {"power": 0.0, "voltage": voltage, "current": 0.0},
        "L3": {"power": 0.0, "voltage": voltage, "current": 0.0},
    }


class _FakeTemplateResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {}


class _FakeSmartEvseTransport:
    def __init__(self) -> None:
        self.requests: list[ModbusRequest] = []
        self.holding_registers: dict[int, int] = {
            0x0000: 2,
            0x0001: 0,
            0x0002: 16,
            0x0005: 1,
            0x0007: 32,
        }

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes:
        self.requests.append(request)
        if request.function_code == 0x03:
            address = int.from_bytes(request.payload[0:2], "big")
            count = int.from_bytes(request.payload[2:4], "big")
            payload = b"".join(
                int(self.holding_registers.get(address + index, 0)).to_bytes(2, "big")
                for index in range(count)
            )
            return bytes((0x03, len(payload))) + payload
        if request.function_code == 0x06:
            address = int.from_bytes(request.payload[0:2], "big")
            value = int.from_bytes(request.payload[2:4], "big")
            self.holding_registers[address] = value
            return bytes((0x06,)) + request.payload
        raise AssertionError(f"Unexpected Modbus function code {request.function_code}")


def _auto_phase_service(**overrides):
    auto_policy = AutoPolicy()
    auto_policy.phase.upshift_delay_seconds = 10.0
    auto_policy.phase.downshift_delay_seconds = 5.0
    auto_policy.phase.upshift_headroom_watts = 250.0
    auto_policy.phase.downshift_margin_watts = 150.0
    auto_policy.phase.mismatch_retry_seconds = 60.0
    data = {
        "auto_policy": auto_policy,
        "supported_phase_selections": ("P1", "P1_P2"),
        "requested_phase_selection": "P1",
        "active_phase_selection": "P1",
        "_last_auto_metrics": {"surplus": 3200.0},
        "min_current": 6.0,
        "voltage_mode": "phase",
        "_phase_selection_requires_pause": MagicMock(return_value=True),
        "_peek_pending_relay_command": MagicMock(return_value=(None, None)),
        "_apply_phase_selection": MagicMock(return_value="P1"),
        "_save_runtime_state": MagicMock(),
        "_publish_local_pm_status": MagicMock(),
        "_warning_throttled": MagicMock(),
        "_mark_failure": MagicMock(),
        "auto_shelly_soft_fail_seconds": 10.0,
        "_worker_poll_interval_seconds": 1.0,
        "relay_sync_timeout_seconds": 3.0,
        "_last_confirmed_pm_status": {"output": False},
        "_last_confirmed_pm_status_at": 99.0,
        "_phase_switch_pending_selection": None,
        "_phase_switch_state": None,
        "_phase_switch_requested_at": None,
        "_phase_switch_stable_until": None,
        "_phase_switch_resume_relay": False,
        "_phase_switch_mismatch_active": False,
        "_phase_switch_last_mismatch_selection": None,
        "_phase_switch_last_mismatch_at": None,
        "_auto_phase_target_candidate": None,
        "_auto_phase_target_since": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class TestUpdateCycleController(unittest.TestCase):
    @staticmethod
    def _write_config(directory: str, filename: str, content: str) -> str:
        path = Path(directory) / filename
        path.write_text(content, encoding="utf-8")
        return str(path)

    @staticmethod
    def _software_update_service(repo_root: str, **overrides: object) -> SimpleNamespace:
        data: dict[str, object] = {
            "software_update_repo_root": repo_root,
            "software_update_install_script": str(Path(repo_root) / "install.sh"),
            "software_update_restart_script": str(Path(repo_root) / "deploy/venus/restart_shelly_wallbox.sh"),
            "software_update_no_update_file": str(Path(repo_root) / "noUpdate"),
            "software_update_log_path": str(Path(repo_root) / "software-update.log"),
            "software_update_manifest_source": "https://example.invalid/bootstrap_manifest.json",
            "software_update_version_source": "https://example.invalid/version.txt",
            "_software_update_current_version": "",
            "_software_update_available_version": "",
            "_software_update_available": False,
            "_software_update_state": "idle",
            "_software_update_detail": "",
            "_software_update_last_check_at": None,
            "_software_update_last_run_at": None,
            "_software_update_last_result": "",
            "_software_update_process": None,
            "_software_update_process_log_handle": None,
            "_software_update_run_requested_at": None,
            "_software_update_no_update_active": 0,
            "_software_update_next_check_at": None,
            "_software_update_boot_auto_due_at": None,
        }
        data.update(overrides)
        return SimpleNamespace(**data)

    def test_auto_phase_selection_tracks_candidate_before_staged_upshift(self):
        service = _auto_phase_service(
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=99.5,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.maybe_apply_auto_phase_selection(
            service,
            True,
            True,
            230.0,
            100.0,
            True,
        )

        self.assertIsNone(override)
        self.assertEqual(service._auto_phase_target_candidate, "P1_P2")
        self.assertEqual(service._auto_phase_target_since, 100.0)
        self.assertIsNone(service._phase_switch_pending_selection)
        self.assertEqual(service._last_auto_metrics["phase_reason"], "phase-upshift-pending")

    def test_auto_phase_selection_stages_upshift_after_delay_when_relay_is_on(self):
        service = _auto_phase_service(
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=99.5,
            _auto_phase_target_candidate="P1_P2",
            _auto_phase_target_since=80.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.maybe_apply_auto_phase_selection(
            service,
            True,
            True,
            230.0,
            100.0,
            True,
        )

        self.assertFalse(override)
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service._phase_switch_pending_selection, "P1_P2")
        self.assertEqual(service._phase_switch_state, "waiting-relay-off")
        self.assertTrue(service._phase_switch_resume_relay)
        service._save_runtime_state.assert_called_once()
        service._publish_local_pm_status.assert_called_once_with(False, 100.0)

    def test_auto_phase_selection_blocks_repeated_upshift_after_confirmed_mismatch(self):
        service = _auto_phase_service(
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=99.5,
            _phase_switch_last_mismatch_selection="P1_P2",
            _phase_switch_last_mismatch_at=95.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.maybe_apply_auto_phase_selection(
            service,
            True,
            True,
            230.0,
            100.0,
            True,
        )

        self.assertIsNone(override)
        self.assertIsNone(service._auto_phase_target_candidate)
        self.assertIsNone(service._phase_switch_pending_selection)
        self.assertEqual(service._last_auto_metrics["phase_reason"], "phase-upshift-blocked-mismatch")

    def test_auto_phase_selection_retries_upshift_after_mismatch_cooldown_expires(self):
        service = _auto_phase_service(
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=99.5,
            _phase_switch_last_mismatch_selection="P1_P2",
            _phase_switch_last_mismatch_at=10.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.maybe_apply_auto_phase_selection(
            service,
            True,
            True,
            230.0,
            100.0,
            True,
        )

        self.assertIsNone(override)
        self.assertEqual(service._auto_phase_target_candidate, "P1_P2")
        self.assertEqual(service._last_auto_metrics["phase_reason"], "phase-upshift-pending")

    def test_auto_phase_selection_blocks_upshift_while_phase_lockout_is_active(self):
        service = _auto_phase_service(
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=99.5,
            _phase_switch_lockout_selection="P1_P2",
            _phase_switch_lockout_reason="mismatch-threshold",
            _phase_switch_lockout_at=95.0,
            _phase_switch_lockout_until=160.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.maybe_apply_auto_phase_selection(
            service,
            True,
            True,
            230.0,
            100.0,
            True,
        )

        self.assertIsNone(override)
        self.assertIsNone(service._auto_phase_target_candidate)
        self.assertEqual(service._last_auto_metrics["phase_reason"], "phase-upshift-blocked-lockout")

    def test_auto_phase_selection_applies_lowest_phase_while_idle_after_delay(self):
        service = _auto_phase_service(
            requested_phase_selection="P1_P2",
            active_phase_selection="P1_P2",
            _last_auto_metrics={"surplus": 400.0},
            _last_confirmed_pm_status={"output": False},
            _last_confirmed_pm_status_at=99.5,
            _auto_phase_target_candidate="P1",
            _auto_phase_target_since=90.0,
            _apply_phase_selection=MagicMock(return_value="P1"),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        override = controller.maybe_apply_auto_phase_selection(
            service,
            False,
            False,
            230.0,
            100.0,
            True,
        )

        self.assertIsNone(override)
        service._apply_phase_selection.assert_called_once_with("P1")
        self.assertEqual(service.requested_phase_selection, "P1")
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertIsNone(service._phase_switch_pending_selection)
        service._save_runtime_state.assert_called_once()

    def test_normalize_learned_charge_power_state_falls_back_to_unknown_for_invalid_values(self):
        self.assertEqual(UpdateCycleController._normalize_learned_charge_power_state("weird"), "unknown")

    def test_software_update_check_marks_update_available_from_manifest_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bootstrap_state = Path(temp_dir) / ".bootstrap-state"
            bootstrap_state.mkdir(parents=True, exist_ok=True)
            (bootstrap_state / "installed_bundle_sha256").write_text("oldhash\n", encoding="utf-8")
            (bootstrap_state / "installed_version").write_text("1.2.3\n", encoding="utf-8")
            service = self._software_update_service(temp_dir)
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
            response = MagicMock()
            response.raise_for_status.return_value = None
            response.json.return_value = {"version": "1.2.4", "bundle_sha256": "newhash"}

            with patch("shelly_wallbox.update.controller.requests.get", return_value=response) as mock_get:
                controller._run_software_update_check(service, 100.0)

            mock_get.assert_called_once_with(
                "https://example.invalid/bootstrap_manifest.json",
                timeout=UpdateCycleController.SOFTWARE_UPDATE_REQUEST_TIMEOUT_SECONDS,
            )
            self.assertEqual(service._software_update_state, "available")
            self.assertTrue(service._software_update_available)
            self.assertEqual(service._software_update_available_version, "1.2.4")
            self.assertEqual(service._software_update_current_version, "1.2.3")
            self.assertEqual(service._software_update_detail, "manifest")
            self.assertEqual(service._software_update_last_check_at, 100.0)
            self.assertEqual(
                service._software_update_next_check_at,
                100.0 + UpdateCycleController.SOFTWARE_UPDATE_CHECK_INTERVAL_SECONDS,
            )

    def test_software_update_run_is_blocked_by_no_update_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "noUpdate").write_text("", encoding="utf-8")
            service = self._software_update_service(
                temp_dir,
                _software_update_run_requested_at=50.0,
                _software_update_available=True,
                _software_update_last_check_at=100.0,
            )
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

            started = controller._start_software_update_run(service, 120.0, "manual")

            self.assertFalse(started)
            self.assertEqual(service._software_update_state, "available-blocked")
            self.assertEqual(service._software_update_detail, "noUpdate marker present")
            self.assertIsNone(service._software_update_run_requested_at)
            self.assertIsNone(service._software_update_process)

    def test_software_update_housekeeping_starts_boot_delayed_run_when_due(self):
        service = self._software_update_service(
            "",
            _software_update_next_check_at=10_000.0,
            _software_update_boot_auto_due_at=100.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(UpdateCycleController, "_start_software_update_run", return_value=True) as start_run:
            controller._software_update_housekeeping(service, 120.0)

        self.assertIsNone(service._software_update_boot_auto_due_at)
        start_run.assert_called_once_with(service, 120.0, "boot-auto")

    def test_software_update_housekeeping_starts_manual_run_when_requested(self):
        service = self._software_update_service(
            "",
            _software_update_next_check_at=10_000.0,
            _software_update_run_requested_at=110.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(UpdateCycleController, "_start_software_update_run", return_value=True) as start_run:
            controller._software_update_housekeeping(service, 120.0)

        start_run.assert_called_once_with(service, 120.0, "manual")

    def test_software_update_housekeeping_discards_manual_request_while_run_is_already_active(self):
        process = MagicMock()
        process.poll.return_value = None
        service = self._software_update_service(
            "",
            _software_update_process=process,
            _software_update_run_requested_at=110.0,
            _software_update_boot_auto_due_at=100.0,
            _software_update_next_check_at=10_000.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(UpdateCycleController, "_start_software_update_run", return_value=True) as start_run:
            controller._software_update_housekeeping(service, 120.0)

        self.assertIsNone(service._software_update_run_requested_at)
        self.assertIsNone(service._software_update_boot_auto_due_at)
        start_run.assert_not_called()

    def test_update_flushes_debounced_runtime_overrides_from_main_loop(self):
        service = self._software_update_service("")
        service._time_now = MagicMock(return_value=42.0)
        service._flush_runtime_overrides = MagicMock()
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller, "_run_update_cycle", return_value=True), patch.object(
            controller,
            "_software_update_housekeeping",
        ) as housekeeping_mock:
            result = controller.update()

        self.assertTrue(result)
        service._flush_runtime_overrides.assert_called_once_with(42.0)
        housekeeping_mock.assert_called_once_with(service, 42.0)

    def test_current_learning_voltage_signature_uses_last_voltage_fallback_and_none_without_cache(self):
        service = SimpleNamespace(_last_voltage=228.5)
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller._current_learning_voltage_signature(0.0), 228.5)

        service._last_voltage = None
        self.assertIsNone(controller._current_learning_voltage_signature(0.0))

    def test_update_learned_charge_power_requires_stable_active_charge(self):
        service = SimpleNamespace(
            charging_started_at=None,
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_state="unknown",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(controller.update_learned_charge_power(False, 2, 1900.0, 230.0, 100.0))
        self.assertFalse(controller.update_learned_charge_power(True, 1, 1900.0, 230.0, 100.0))

        service.charging_started_at = 90.0
        self.assertFalse(controller.update_learned_charge_power(True, 2, 1900.0, 230.0, 100.0))
        self.assertFalse(controller.update_learned_charge_power(True, 2, 400.0, 230.0, 130.0))
        self.assertIsNone(service.learned_charge_power_watts)

    def test_learning_window_status_waits_without_session_start(self):
        service = SimpleNamespace(
            charging_started_at=None,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(controller._learning_window_status(100.0), ("waiting", None))

    def test_update_learned_charge_power_learns_and_smooths_stable_power(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_state="unknown",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.update_learned_charge_power(True, 2, 1900.0, 230.0, 100.0))
        self.assertEqual(service.learned_charge_power_watts, 1900.0)
        self.assertEqual(service.learned_charge_power_updated_at, 100.0)
        self.assertEqual(service.learned_charge_power_state, "learning")
        self.assertEqual(service.learned_charge_power_learning_since, 100.0)
        self.assertEqual(service.learned_charge_power_sample_count, 1)

        self.assertTrue(controller.update_learned_charge_power(True, 2, 1940.0, 230.0, 110.0))
        self.assertEqual(service.learned_charge_power_watts, 1908.0)
        self.assertEqual(service.learned_charge_power_updated_at, 110.0)
        self.assertEqual(service.learned_charge_power_state, "learning")
        self.assertEqual(service.learned_charge_power_sample_count, 2)

        self.assertTrue(controller.update_learned_charge_power(True, 2, 1920.0, 230.0, 116.0))
        self.assertEqual(service.learned_charge_power_watts, 1910.4)
        self.assertEqual(service.learned_charge_power_updated_at, 116.0)
        self.assertEqual(service.learned_charge_power_state, "stable")
        self.assertIsNone(service.learned_charge_power_learning_since)
        self.assertEqual(service.learned_charge_power_sample_count, 3)

    def test_update_learned_charge_power_respects_disable_and_configurable_learning_parameters(self):
        disabled_service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_state="unknown",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            auto_learn_charge_power_enabled=False,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
        )
        disabled_controller = UpdateCycleController(
            disabled_service,
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )
        self.assertFalse(disabled_controller.update_learned_charge_power(True, 2, 1900.0, 230.0, 100.0))
        self.assertIsNone(disabled_service.learned_charge_power_watts)

        tuned_service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=1800.0,
            learned_charge_power_updated_at=80.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=40.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=700.0,
            auto_learn_charge_power_alpha=0.5,
            phase="L1",
            max_current=16.0,
        )
        tuned_controller = UpdateCycleController(
            tuned_service,
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )
        self.assertFalse(tuned_controller.update_learned_charge_power(True, 2, 650.0, 230.0, 95.0))
        self.assertTrue(tuned_controller.update_learned_charge_power(True, 2, 2000.0, 230.0, 100.0))
        self.assertEqual(tuned_service.learned_charge_power_watts, 1900.0)
        self.assertEqual(tuned_service.learned_charge_power_updated_at, 100.0)
        self.assertEqual(tuned_service.learned_charge_power_state, "stable")

    def test_update_learned_charge_power_uses_early_session_window_and_restarts_from_stale_value(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=2400.0,
            learned_charge_power_updated_at=-30.0,
            learned_charge_power_state="stale",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=60.0,
            auto_learn_charge_power_max_age_seconds=120.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.update_learned_charge_power(True, 2, 1900.0, 230.0, 100.0))
        self.assertEqual(service.learned_charge_power_watts, 1900.0)
        self.assertEqual(service.learned_charge_power_state, "learning")
        self.assertTrue(controller.update_learned_charge_power(True, 2, 2000.0, 230.0, 150.5))
        self.assertIsNone(service.learned_charge_power_watts)
        self.assertEqual(service.learned_charge_power_state, "unknown")

    def test_stored_positive_learned_charge_power_rejects_non_positive_values(self):
        service = SimpleNamespace(learned_charge_power_watts=0.0)
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertIsNone(controller._stored_positive_learned_charge_power())

    def test_update_learned_charge_power_rejects_implausible_spike(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=1900.0,
            learned_charge_power_updated_at=90.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(controller.update_learned_charge_power(True, 2, 5000.0, 230.0, 100.0))
        self.assertEqual(service.learned_charge_power_watts, 1900.0)

    def test_orchestrate_pending_phase_switch_enters_stabilization_after_confirmed_relay_off(self):
        service = SimpleNamespace(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="waiting-relay-off",
            _phase_switch_requested_at=98.0,
            _phase_switch_stable_until=None,
            _phase_switch_resume_relay=True,
            requested_phase_selection="P1_P2",
            active_phase_selection="P1",
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _apply_phase_selection=MagicMock(return_value="P1_P2"),
            _save_runtime_state=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed, desired_override = controller.orchestrate_pending_phase_switch(
            {"output": False, "_phase_selection": "P1"},
            False,
            0.0,
            0.0,
            True,
            100.0,
            False,
        )

        self.assertFalse(relay_on)
        self.assertEqual(power, 0.0)
        self.assertEqual(current, 0.0)
        self.assertFalse(confirmed)
        self.assertFalse(desired_override)
        service._apply_phase_selection.assert_called_once_with("P1_P2")
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertEqual(service._phase_switch_state, "stabilizing")
        self.assertEqual(service._phase_switch_stable_until, 102.0)

    def test_orchestrate_pending_phase_switch_resumes_manual_relay_after_stabilization(self):
        service = SimpleNamespace(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="stabilizing",
            _phase_switch_requested_at=98.0,
            _phase_switch_stable_until=99.0,
            _phase_switch_resume_relay=True,
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _save_runtime_state=MagicMock(),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed, desired_override = controller.orchestrate_pending_phase_switch(
            {"output": False, "_phase_selection": "P1_P2"},
            False,
            0.0,
            0.0,
            True,
            100.0,
            False,
        )

        self.assertTrue(relay_on)
        self.assertEqual(power, 0.0)
        self.assertEqual(current, 0.0)
        self.assertFalse(confirmed)
        self.assertIsNone(desired_override)
        service._queue_relay_command.assert_called_once_with(True, 100.0)
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)
        self.assertIsNone(service._phase_switch_pending_selection)
        self.assertIsNone(service._phase_switch_state)
        self.assertFalse(service._phase_switch_resume_relay)

    def test_auto_phase_switch_end_to_end_with_simpleevse_and_switch_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            phase1_path = self._write_config(
                temp_dir,
                "phase1-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1\nSwitchingMode=contactor\nRequiresChargePauseForPhaseChange=1\n"
                "[StateRequest]\nMethod=GET\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/control?on=${enabled_int}\n",
            )
            phase2_path = self._write_config(
                temp_dir,
                "phase2-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase2.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1\n"
                "[StateRequest]\nMethod=GET\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/control?on=${enabled_int}\n",
            )
            phase3_path = self._write_config(
                temp_dir,
                "phase3-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase3.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1\n"
                "[StateRequest]\nMethod=GET\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/control?on=${enabled_int}\n",
            )
            switch_path = self._write_config(
                temp_dir,
                "switch-group.ini",
                "[Adapter]\nType=switch_group\n"
                f"[Members]\nP1={Path(phase1_path).name}\nP2={Path(phase2_path).name}\nP3={Path(phase3_path).name}\n",
            )
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=simpleevse_charger\nTransport=serial_rtu\n"
                "[Transport]\nDevice=/dev/ttyUSB0\nBaudrate=9600\nParity=N\nStopBits=1\nUnitId=1\n",
            )
            session = MagicMock()
            session.post.return_value = _FakeTemplateResponse()
            service = _auto_phase_service(
                _last_confirmed_pm_status={"output": True},
                _last_confirmed_pm_status_at=99.5,
                _auto_phase_target_candidate="P1_P2",
                _auto_phase_target_since=80.0,
                backend_mode="split",
                meter_backend_type="none",
                switch_backend_type="switch_group",
                charger_backend_type="simpleevse_charger",
                meter_backend_config_path="",
                switch_backend_config_path=str(switch_path),
                charger_backend_config_path=str(charger_path),
                phase="L1",
                host="192.168.1.20",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=session,
                use_digest_auth=False,
                username="",
                password="",
                shelly_request_timeout_seconds=2.0,
            )

            resolved = build_service_backends(service)
            service._backend_selection = resolved.selection
            service._meter_backend = resolved.meter
            service._switch_backend = resolved.switch
            service._charger_backend = resolved.charger
            service.supported_phase_selections = resolved.switch.capabilities().supported_phase_selections

            io_controller = ShellyIoController(service)
            service._phase_selection_requires_pause = io_controller.phase_selection_requires_pause
            service._apply_phase_selection = io_controller.set_phase_selection

            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

            override = controller.maybe_apply_auto_phase_selection(
                service,
                True,
                True,
                230.0,
                100.0,
                True,
            )

            self.assertFalse(override)
            self.assertEqual(service.requested_phase_selection, "P1_P2")
            self.assertEqual(service._phase_switch_pending_selection, "P1_P2")
            self.assertEqual(service._phase_switch_state, "waiting-relay-off")

            relay_on, power, current, confirmed, desired_override = controller.orchestrate_pending_phase_switch(
                {"output": False, "_phase_selection": "P1"},
                False,
                0.0,
                0.0,
                True,
                102.0,
                True,
            )

            self.assertFalse(relay_on)
            self.assertEqual(power, 0.0)
            self.assertEqual(current, 0.0)
            self.assertFalse(confirmed)
            self.assertFalse(desired_override)
            self.assertEqual(service.requested_phase_selection, "P1_P2")
            self.assertEqual(service._phase_switch_state, "stabilizing")

            relay_on, power, current, confirmed, desired_override = controller.orchestrate_pending_phase_switch(
                {"output": False, "_phase_selection": "P1_P2"},
                False,
                0.0,
                0.0,
                True,
                105.0,
                True,
            )

            self.assertFalse(relay_on)
            self.assertEqual(power, 0.0)
            self.assertEqual(current, 0.0)
            self.assertTrue(confirmed)
            self.assertIsNone(desired_override)
            self.assertTrue(service._ignore_min_offtime_once)
            self.assertIsNone(service._phase_switch_pending_selection)
            self.assertIsNone(service._phase_switch_state)

            session.post.reset_mock()
            self.assertEqual(io_controller.set_relay(True), {"output": True})

            urls = [call.kwargs["url"] for call in session.post.call_args_list]
            self.assertCountEqual(
                urls,
                [
                    "http://phase1.local/control?on=1",
                    "http://phase2.local/control?on=1",
                    "http://phase3.local/control?on=0",
                ],
            )

    def test_auto_phase_switch_end_to_end_with_smartevse_and_switch_group(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            phase1_path = self._write_config(
                temp_dir,
                "phase1-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1\nSwitchingMode=contactor\nRequiresChargePauseForPhaseChange=1\n"
                "[StateRequest]\nMethod=GET\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/control?on=${enabled_int}\n",
            )
            phase2_path = self._write_config(
                temp_dir,
                "phase2-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase2.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1\n"
                "[StateRequest]\nMethod=GET\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/control?on=${enabled_int}\n",
            )
            phase3_path = self._write_config(
                temp_dir,
                "phase3-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase3.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1\n"
                "[StateRequest]\nMethod=GET\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/control?on=${enabled_int}\n",
            )
            switch_path = self._write_config(
                temp_dir,
                "switch-group.ini",
                "[Adapter]\nType=switch_group\n"
                f"[Members]\nP1={Path(phase1_path).name}\nP2={Path(phase2_path).name}\nP3={Path(phase3_path).name}\n",
            )
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=smartevse_charger\nTransport=serial_rtu\n"
                "[Transport]\nDevice=/dev/ttyUSB0\nBaudrate=9600\nParity=N\nStopBits=1\nUnitId=1\n",
            )
            session = MagicMock()
            session.post.return_value = _FakeTemplateResponse()
            service = _auto_phase_service(
                _last_confirmed_pm_status={"output": True},
                _last_confirmed_pm_status_at=99.5,
                _auto_phase_target_candidate="P1_P2",
                _auto_phase_target_since=80.0,
                backend_mode="split",
                meter_backend_type="none",
                switch_backend_type="switch_group",
                charger_backend_type="smartevse_charger",
                meter_backend_config_path="",
                switch_backend_config_path=str(switch_path),
                charger_backend_config_path=str(charger_path),
                phase="L1",
                host="192.168.1.20",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=session,
                use_digest_auth=False,
                username="",
                password="",
                shelly_request_timeout_seconds=2.0,
            )

            resolved = build_service_backends(service)
            service._backend_selection = resolved.selection
            service._meter_backend = resolved.meter
            service._switch_backend = resolved.switch
            service._charger_backend = resolved.charger
            service.supported_phase_selections = resolved.switch.capabilities().supported_phase_selections

            io_controller = ShellyIoController(service)
            service._phase_selection_requires_pause = io_controller.phase_selection_requires_pause
            service._apply_phase_selection = io_controller.set_phase_selection

            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

            override = controller.maybe_apply_auto_phase_selection(
                service,
                True,
                True,
                230.0,
                100.0,
                True,
            )

            self.assertFalse(override)
            self.assertEqual(service.requested_phase_selection, "P1_P2")
            self.assertEqual(service._phase_switch_pending_selection, "P1_P2")
            self.assertEqual(service._phase_switch_state, "waiting-relay-off")

            relay_on, power, current, confirmed, desired_override = controller.orchestrate_pending_phase_switch(
                {"output": False, "_phase_selection": "P1"},
                False,
                0.0,
                0.0,
                True,
                102.0,
                True,
            )

            self.assertFalse(relay_on)
            self.assertEqual(power, 0.0)
            self.assertEqual(current, 0.0)
            self.assertFalse(confirmed)
            self.assertFalse(desired_override)
            self.assertEqual(service.requested_phase_selection, "P1_P2")
            self.assertEqual(service._phase_switch_state, "stabilizing")

            relay_on, power, current, confirmed, desired_override = controller.orchestrate_pending_phase_switch(
                {"output": False, "_phase_selection": "P1_P2"},
                False,
                0.0,
                0.0,
                True,
                105.0,
                True,
            )

            self.assertFalse(relay_on)
            self.assertEqual(power, 0.0)
            self.assertEqual(current, 0.0)
            self.assertTrue(confirmed)
            self.assertIsNone(desired_override)
            self.assertTrue(service._ignore_min_offtime_once)
            self.assertIsNone(service._phase_switch_pending_selection)
            self.assertIsNone(service._phase_switch_state)

            session.post.reset_mock()
            self.assertEqual(io_controller.set_relay(True), {"output": True})

            urls = [call.kwargs["url"] for call in session.post.call_args_list]
            self.assertCountEqual(
                urls,
                [
                    "http://phase1.local/control?on=1",
                    "http://phase2.local/control?on=1",
                    "http://phase3.local/control?on=0",
                ],
            )

    def test_native_smartevse_backend_handles_update_and_write_cycle_without_external_switch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )
            fake_transport = _FakeSmartEvseTransport()
            service = SimpleNamespace(
                backend_mode="split",
                meter_backend_type="none",
                switch_backend_type="shelly_combined",
                charger_backend_type="smartevse_charger",
                meter_backend_config_path="",
                switch_backend_config_path="",
                charger_backend_config_path=str(charger_path),
                host="192.168.1.20",
                pm_component="Switch",
                pm_id=0,
                shelly_request_timeout_seconds=2.0,
                use_digest_auth=False,
                username="",
                password="",
                _last_auto_metrics={"surplus": None, "grid": None, "soc": None},
                _last_health_reason="init",
                auto_audit_log=False,
                auto_shelly_soft_fail_seconds=10.0,
                _queue_relay_command=MagicMock(),
                _mark_failure=MagicMock(),
                _mark_recovery=MagicMock(),
                _warning_throttled=MagicMock(),
                _publish_local_pm_status=MagicMock(side_effect=lambda relay, now: {"output": relay, "at": now}),
                _relay_sync_expected_state=None,
                _relay_sync_requested_at=None,
                _relay_sync_deadline_at=None,
                _relay_sync_failure_reported=False,
                _startup_manual_target=False,
                virtual_mode=0,
                _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            )

            with patch(
                "shelly_wallbox.backend.smartevse_charger.create_modbus_transport",
                return_value=fake_transport,
            ):
                resolved = build_service_backends(service)
                service._backend_selection = resolved.selection
                service._meter_backend = resolved.meter
                service._switch_backend = resolved.switch
                service._charger_backend = resolved.charger

                controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

                relay_on, power, current, confirmed = controller.apply_relay_decision(
                    True,
                    False,
                    {"output": False, "_pm_confirmed": True},
                    0.0,
                    0.0,
                    100.0,
                    False,
                )

                self.assertEqual((relay_on, power, current, confirmed), (True, 0.0, 0.0, False))
                self.assertEqual(fake_transport.holding_registers[0x0005], 1)
                service._queue_relay_command.assert_not_called()
                service._publish_local_pm_status.assert_called_once_with(True, 100.0)

                updated = controller.apply_startup_manual_target(
                    {"output": True, "apower": 1200.0, "current": 5.2},
                    123.0,
                )

            self.assertEqual(fake_transport.holding_registers[0x0005], 0)
            self.assertEqual(updated, {"output": False, "at": 123.0})
            self.assertIsNone(service._startup_manual_target)

    def test_orchestrate_pending_phase_switch_resumes_native_charger_after_stabilization(self):
        charger_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="stabilizing",
            _phase_switch_requested_at=98.0,
            _phase_switch_stable_until=99.0,
            _phase_switch_resume_relay=True,
            _charger_backend=charger_backend,
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _save_runtime_state=MagicMock(),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed, desired_override = controller.orchestrate_pending_phase_switch(
            {"output": False, "_phase_selection": "P1_P2"},
            False,
            0.0,
            0.0,
            True,
            100.0,
            False,
        )

        self.assertTrue(relay_on)
        self.assertEqual(power, 0.0)
        self.assertEqual(current, 0.0)
        self.assertFalse(confirmed)
        self.assertIsNone(desired_override)
        charger_backend.set_enabled.assert_called_once_with(True)
        service._queue_relay_command.assert_not_called()
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)
        self.assertIsNone(service._phase_switch_pending_selection)
        self.assertIsNone(service._phase_switch_state)
        self.assertFalse(service._phase_switch_resume_relay)

    def test_orchestrate_pending_phase_switch_allows_auto_resume_after_stabilization(self):
        service = SimpleNamespace(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="stabilizing",
            _phase_switch_requested_at=98.0,
            _phase_switch_stable_until=99.0,
            _phase_switch_resume_relay=True,
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _ignore_min_offtime_once=False,
            _save_runtime_state=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed, desired_override = controller.orchestrate_pending_phase_switch(
            {"output": False, "_phase_selection": "P1_P2"},
            False,
            0.0,
            0.0,
            True,
            100.0,
            True,
        )

        self.assertFalse(relay_on)
        self.assertEqual(power, 0.0)
        self.assertEqual(current, 0.0)
        self.assertTrue(confirmed)
        self.assertIsNone(desired_override)
        self.assertTrue(service._ignore_min_offtime_once)
        self.assertIsNone(service._phase_switch_pending_selection)
        self.assertIsNone(service._phase_switch_state)

    def test_orchestrate_pending_phase_switch_waits_for_observed_phase_match(self):
        service = SimpleNamespace(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="stabilizing",
            _phase_switch_requested_at=98.0,
            _phase_switch_stable_until=99.0,
            _phase_switch_resume_relay=True,
            requested_phase_selection="P1_P2",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _save_runtime_state=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _set_health=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed, desired_override = controller.orchestrate_pending_phase_switch(
            {"output": False, "_phase_selection": "P1"},
            False,
            0.0,
            0.0,
            True,
            100.0,
            False,
        )

        self.assertFalse(relay_on)
        self.assertEqual(power, 0.0)
        self.assertEqual(current, 0.0)
        self.assertFalse(confirmed)
        self.assertFalse(desired_override)
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertEqual(service._phase_switch_state, "stabilizing")
        service._queue_relay_command.assert_not_called()
        service._set_health.assert_not_called()

    def test_orchestrate_pending_phase_switch_marks_mismatch_after_timeout(self):
        service = SimpleNamespace(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="stabilizing",
            _phase_switch_requested_at=80.0,
            _phase_switch_stable_until=81.0,
            _phase_switch_resume_relay=True,
            requested_phase_selection="P1_P2",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            _phase_switch_mismatch_counts={},
            _phase_switch_lockout_selection=None,
            _phase_switch_lockout_reason="",
            _phase_switch_lockout_at=None,
            _phase_switch_lockout_until=None,
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _save_runtime_state=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _set_health=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed, desired_override = controller.orchestrate_pending_phase_switch(
            {"output": False, "_phase_selection": "P1"},
            False,
            0.0,
            0.0,
            True,
            100.0,
            False,
        )

        self.assertTrue(relay_on)
        self.assertEqual(power, 0.0)
        self.assertEqual(current, 0.0)
        self.assertFalse(confirmed)
        self.assertIsNone(desired_override)
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertEqual(service.requested_phase_selection, "P1")
        self.assertIsNone(service._phase_switch_state)
        self.assertIsNone(service._phase_switch_pending_selection)
        service._queue_relay_command.assert_called_once_with(True, 100.0)
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)
        service._mark_failure.assert_called_once_with("shelly")
        service._set_health.assert_called_once_with("phase-switch-mismatch", cached=False)
        service._warning_throttled.assert_called_once()
        self.assertFalse(service._phase_switch_mismatch_active)
        self.assertEqual(service._phase_switch_mismatch_counts["P1_P2"], 1)
        self.assertEqual(service._phase_switch_last_mismatch_selection, "P1_P2")
        self.assertEqual(service._phase_switch_last_mismatch_at, 100.0)
        self.assertIsNone(service._phase_switch_lockout_selection)

    def test_orchestrate_pending_phase_switch_engages_lockout_after_repeated_mismatches(self):
        service = SimpleNamespace(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="stabilizing",
            _phase_switch_requested_at=80.0,
            _phase_switch_stable_until=81.0,
            _phase_switch_resume_relay=True,
            requested_phase_selection="P1_P2",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            auto_phase_mismatch_lockout_count=3,
            auto_phase_mismatch_lockout_seconds=60.0,
            _phase_switch_mismatch_counts={"P1_P2": 2},
            _phase_switch_lockout_selection=None,
            _phase_switch_lockout_reason="",
            _phase_switch_lockout_at=None,
            _phase_switch_lockout_until=None,
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _save_runtime_state=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _set_health=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed, desired_override = controller.orchestrate_pending_phase_switch(
            {"output": False, "_phase_selection": "P1"},
            False,
            0.0,
            0.0,
            True,
            100.0,
            False,
        )

        self.assertTrue(relay_on)
        self.assertEqual(power, 0.0)
        self.assertEqual(current, 0.0)
        self.assertFalse(confirmed)
        self.assertIsNone(desired_override)
        self.assertEqual(service.requested_phase_selection, "P1")
        self.assertEqual(service.active_phase_selection, "P1")
        self.assertEqual(service._phase_switch_mismatch_counts["P1_P2"], 3)
        self.assertEqual(service._phase_switch_lockout_selection, "P1_P2")
        self.assertEqual(service._phase_switch_lockout_reason, "mismatch-threshold")
        self.assertEqual(service._phase_switch_lockout_at, 100.0)
        self.assertEqual(service._phase_switch_lockout_until, 160.0)

    def test_phase_change_scenario_repeated_feedback_mismatches_escalate_to_lockout(self):
        service = SimpleNamespace(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="stabilizing",
            _phase_switch_requested_at=80.0,
            _phase_switch_stable_until=81.0,
            _phase_switch_resume_relay=True,
            requested_phase_selection="P1_P2",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            auto_phase_mismatch_lockout_count=2,
            auto_phase_mismatch_lockout_seconds=60.0,
            _phase_switch_mismatch_counts={},
            _phase_switch_lockout_selection=None,
            _phase_switch_lockout_reason="",
            _phase_switch_lockout_at=None,
            _phase_switch_lockout_until=None,
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _save_runtime_state=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _set_health=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        controller.orchestrate_pending_phase_switch(
            {"output": False, "_phase_selection": "P1"},
            False,
            0.0,
            0.0,
            True,
            100.0,
            False,
        )
        self.assertEqual(service._phase_switch_mismatch_counts, {"P1_P2": 1})
        self.assertIsNone(service._phase_switch_lockout_selection)

        service._phase_switch_pending_selection = "P1_P2"
        service._phase_switch_state = "stabilizing"
        service._phase_switch_requested_at = 140.0
        service._phase_switch_stable_until = 141.0
        service._phase_switch_resume_relay = True
        service.requested_phase_selection = "P1_P2"

        controller.orchestrate_pending_phase_switch(
            {"output": False, "_phase_selection": "P1"},
            False,
            0.0,
            0.0,
            True,
            170.0,
            False,
        )

        self.assertEqual(service._phase_switch_mismatch_counts, {"P1_P2": 2})
        self.assertEqual(service._phase_switch_lockout_selection, "P1_P2")
        self.assertEqual(service._phase_switch_lockout_reason, "mismatch-threshold")
        self.assertEqual(service._phase_switch_lockout_at, 170.0)
        self.assertEqual(service._phase_switch_lockout_until, 230.0)

    def test_update_learned_charge_power_ignores_unconfirmed_measurements(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=1900.0,
            learned_charge_power_updated_at=90.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(
            controller.update_learned_charge_power(
                True,
                2,
                2400.0,
                230.0,
                100.0,
                pm_confirmed=False,
            )
        )
        self.assertEqual(service.learned_charge_power_watts, 1900.0)
        self.assertEqual(service.learned_charge_power_state, "stable")

    def test_is_learned_charge_power_stale_covers_disabled_expiry_and_missing_timestamp(self):
        service = SimpleNamespace(
            auto_learn_charge_power_max_age_seconds=0.0,
            learned_charge_power_updated_at=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(controller._is_learned_charge_power_stale(100.0))

        service.auto_learn_charge_power_max_age_seconds = 60.0
        self.assertTrue(controller._is_learned_charge_power_stale(100.0))

    def test_direct_pm_snapshot_max_age_seconds_ignores_invalid_worker_poll_interval(self):
        service = SimpleNamespace(_worker_poll_interval_seconds="bad")

        self.assertEqual(UpdateCycleController._direct_pm_snapshot_max_age_seconds(service), 1.0)

    def test_refresh_learned_charge_power_state_marks_stale_and_promotes_persisted_value_to_stable(self):
        stale_service = SimpleNamespace(
            learned_charge_power_watts=1980.0,
            learned_charge_power_updated_at=10.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=None,
            phase="L1",
            auto_learn_charge_power_max_age_seconds=60.0,
        )
        stale_controller = UpdateCycleController(stale_service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        self.assertTrue(stale_controller.refresh_learned_charge_power_state(100.0))
        self.assertEqual(stale_service.learned_charge_power_state, "stale")

        persisted_service = SimpleNamespace(
            learned_charge_power_watts=1980.0,
            learned_charge_power_updated_at=90.0,
            learned_charge_power_state="unknown",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            learned_charge_power_phase=None,
            learned_charge_power_voltage=None,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=None,
            phase="L1",
            auto_learn_charge_power_max_age_seconds=60.0,
        )
        persisted_controller = UpdateCycleController(
            persisted_service,
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )
        self.assertTrue(persisted_controller.refresh_learned_charge_power_state(100.0))
        self.assertEqual(persisted_service.learned_charge_power_state, "stable")

        unchanged_stale_service = SimpleNamespace(
            learned_charge_power_watts=1980.0,
            learned_charge_power_updated_at=90.0,
            learned_charge_power_state="stale",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=None,
            phase="L1",
            auto_learn_charge_power_max_age_seconds=60.0,
        )
        unchanged_stale_controller = UpdateCycleController(
            unchanged_stale_service,
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )
        self.assertFalse(unchanged_stale_controller.refresh_learned_charge_power_state(100.0))
        self.assertEqual(unchanged_stale_service.learned_charge_power_state, "stale")

        learning_service = SimpleNamespace(
            learned_charge_power_watts=1980.0,
            learned_charge_power_updated_at=90.0,
            learned_charge_power_state="learning",
            learned_charge_power_learning_since=85.0,
            learned_charge_power_sample_count=2,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=None,
            phase="L1",
            auto_learn_charge_power_max_age_seconds=60.0,
        )
        learning_controller = UpdateCycleController(
            learning_service,
            _phase_values,
            lambda reason: {"init": 0}.get(reason, 99),
        )
        self.assertFalse(learning_controller.refresh_learned_charge_power_state(100.0))
        self.assertEqual(learning_service.learned_charge_power_state, "learning")

    def test_refresh_learned_charge_power_state_discards_value_when_phase_signature_changes(self):
        service = SimpleNamespace(
            learned_charge_power_watts=1980.0,
            learned_charge_power_updated_at=90.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            learned_charge_power_phase="3P",
            learned_charge_power_voltage=230.0,
            learned_charge_power_signature_mismatch_sessions=1,
            learned_charge_power_signature_checked_session_started_at=50.0,
            phase="L1",
            auto_learn_charge_power_max_age_seconds=60.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.refresh_learned_charge_power_state(100.0))
        self.assertEqual(service.learned_charge_power_state, "unknown")
        self.assertIsNone(service.learned_charge_power_watts)
        self.assertIsNone(service.learned_charge_power_phase)
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 0)

    def test_update_learned_charge_power_discards_incomplete_learning_when_charge_stops(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=1900.0,
            learned_charge_power_updated_at=100.0,
            learned_charge_power_state="learning",
            learned_charge_power_learning_since=100.0,
            learned_charge_power_sample_count=1,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.update_learned_charge_power(False, 2, 1900.0, 230.0, 110.0))
        self.assertIsNone(service.learned_charge_power_watts)
        self.assertEqual(service.learned_charge_power_state, "unknown")

    def test_update_learned_charge_power_keeps_non_learning_value_when_window_is_already_over(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=1900.0,
            learned_charge_power_updated_at=100.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=10.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(controller.update_learned_charge_power(True, 2, 1920.0, 230.0, 100.5))
        self.assertEqual(service.learned_charge_power_watts, 1900.0)
        self.assertEqual(service.learned_charge_power_state, "stable")

    def test_update_learned_charge_power_recovers_missing_learning_since_and_restarts_on_unstable_sample(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=1900.0,
            learned_charge_power_updated_at=100.0,
            learned_charge_power_state="learning",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=1,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.update_learned_charge_power(True, 2, 1910.0, 230.0, 100.0))
        self.assertEqual(service.learned_charge_power_state, "learning")
        self.assertEqual(service.learned_charge_power_learning_since, 100.0)
        self.assertEqual(service.learned_charge_power_sample_count, 2)

        self.assertTrue(controller.update_learned_charge_power(True, 2, 2300.0, 230.0, 101.0))
        self.assertEqual(service.learned_charge_power_state, "learning")
        self.assertEqual(service.learned_charge_power_watts, 2300.0)
        self.assertEqual(service.learned_charge_power_learning_since, 101.0)
        self.assertEqual(service.learned_charge_power_sample_count, 1)

    def test_reconcile_learned_charge_power_signature_discards_after_two_mismatching_sessions(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=1900.0,
            learned_charge_power_updated_at=90.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=None,
            auto_learn_charge_power_start_delay_seconds=30.0,
            phase="L1",
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.reconcile_learned_charge_power_signature(True, 2300.0, 230.0, 100.0))
        self.assertEqual(service.learned_charge_power_state, "stable")
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 1)
        self.assertEqual(service.learned_charge_power_signature_checked_session_started_at, 50.0)

        service.charging_started_at = 120.0
        self.assertTrue(controller.reconcile_learned_charge_power_signature(True, 2320.0, 230.0, 160.0))
        self.assertEqual(service.learned_charge_power_state, "unknown")
        self.assertIsNone(service.learned_charge_power_watts)
        self.assertIsNone(service.learned_charge_power_phase)
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 0)

    def test_reconcile_learned_charge_power_signature_tracks_voltage_sessions_and_resets_on_match(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=1900.0,
            learned_charge_power_updated_at=90.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=None,
            auto_learn_charge_power_start_delay_seconds=30.0,
            phase="L1",
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.reconcile_learned_charge_power_signature(True, 1910.0, 255.0, 100.0))
        self.assertEqual(service.learned_charge_power_state, "stable")
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 1)

        service.charging_started_at = 130.0
        self.assertTrue(controller.reconcile_learned_charge_power_signature(True, 1910.0, 231.0, 170.0))
        self.assertEqual(service.learned_charge_power_state, "stable")
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 0)
        self.assertEqual(service.learned_charge_power_signature_checked_session_started_at, 130.0)

    def test_reconcile_learned_charge_power_signature_covers_phase_mismatch_and_early_session_guards(self):
        service = SimpleNamespace(
            charging_started_at=None,
            learned_charge_power_watts=1900.0,
            learned_charge_power_updated_at=90.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            learned_charge_power_phase="3P",
            learned_charge_power_voltage=230.0,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=None,
            auto_learn_charge_power_start_delay_seconds=30.0,
            phase="L1",
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.reconcile_learned_charge_power_signature(True, 1900.0, 230.0, 100.0))
        self.assertEqual(service.learned_charge_power_state, "unknown")

        service.learned_charge_power_watts = 1900.0
        service.learned_charge_power_updated_at = 90.0
        service.learned_charge_power_state = "stable"
        service.learned_charge_power_phase = "L1"
        service.learned_charge_power_voltage = 230.0
        self.assertFalse(controller.reconcile_learned_charge_power_signature(True, 1900.0, 230.0, 100.0))

        service.charging_started_at = 90.0
        self.assertFalse(controller.reconcile_learned_charge_power_signature(True, 1900.0, 230.0, 100.0))

        service.charging_started_at = 50.0
        service.learned_charge_power_signature_checked_session_started_at = 50.0
        self.assertFalse(controller.reconcile_learned_charge_power_signature(True, 1900.0, 230.0, 100.0))

    def test_reconcile_learned_charge_power_signature_ignores_unconfirmed_measurements(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=1900.0,
            learned_charge_power_updated_at=90.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            learned_charge_power_signature_mismatch_sessions=1,
            learned_charge_power_signature_checked_session_started_at=None,
            auto_learn_charge_power_start_delay_seconds=30.0,
            phase="L1",
            _last_voltage=230.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(
            controller.reconcile_learned_charge_power_signature(
                True,
                2600.0,
                250.0,
                100.0,
                pm_confirmed=False,
            )
        )
        self.assertEqual(service.learned_charge_power_watts, 1900.0)
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 1)

    def test_update_learned_charge_power_keeps_previous_voltage_signature_when_no_voltage_is_available(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=1900.0,
            learned_charge_power_updated_at=100.0,
            learned_charge_power_state="stable",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=3,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=229.0,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=50.0,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="L1",
            max_current=16.0,
            _last_voltage=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.update_learned_charge_power(True, 2, 1920.0, 0.0, 110.0))
        self.assertEqual(service.learned_charge_power_voltage, 229.0)

    def test_plausible_learning_power_max_uses_phase_voltage_for_three_phase_line_voltage(self):
        service = SimpleNamespace(
            phase="3P",
            voltage_mode="line",
            max_current=16.0,
            _last_voltage=400.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertAlmostEqual(
            controller._plausible_learning_power_max(400.0),
            16.0 * (400.0 / math.sqrt(3.0)) * 3.0 * 1.1,
            places=6,
        )

    def test_update_learned_charge_power_rejects_spike_for_three_phase_line_voltage(self):
        service = SimpleNamespace(
            charging_started_at=50.0,
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_state="unknown",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            learned_charge_power_phase=None,
            learned_charge_power_voltage=None,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=None,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            phase="3P",
            voltage_mode="line",
            max_current=16.0,
            _last_voltage=400.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertFalse(controller.update_learned_charge_power(True, 2, 15000.0, 400.0, 100.0))
        self.assertIsNone(service.learned_charge_power_watts)
        self.assertEqual(service.learned_charge_power_state, "unknown")

    def test_apply_startup_manual_target_returns_unchanged_when_relay_already_matches(self):
        service = SimpleNamespace(
            _startup_manual_target=True,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        pm_status = {"output": True, "apower": 1200.0, "current": 5.2}

        updated = controller.apply_startup_manual_target(pm_status, 123.0)

        self.assertIs(updated, pm_status)
        service._queue_relay_command.assert_not_called()
        self.assertIsNone(service._startup_manual_target)

    def test_apply_startup_manual_target_queues_requested_state(self):
        published_pm_status = {"output": False, "apower": 0.0, "current": 0.0}
        service = SimpleNamespace(
            _startup_manual_target=False,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(return_value=published_pm_status),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        updated = controller.apply_startup_manual_target(
            {"output": True, "apower": 1200.0, "current": 5.2},
            123.0,
        )

        service._queue_relay_command.assert_called_once_with(False, 123.0)
        service._publish_local_pm_status.assert_called_once_with(False, 123.0)
        self.assertIsNone(service._startup_manual_target)
        self.assertIs(updated, published_pm_status)

    def test_apply_startup_manual_target_uses_native_charger_backend_when_available(self):
        published_pm_status = {"output": False, "apower": 0.0, "current": 0.0}
        charger_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            _startup_manual_target=False,
            _charger_backend=charger_backend,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(return_value=published_pm_status),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        updated = controller.apply_startup_manual_target(
            {"output": True, "apower": 1200.0, "current": 5.2},
            123.0,
        )

        charger_backend.set_enabled.assert_called_once_with(False)
        service._queue_relay_command.assert_not_called()
        service._publish_local_pm_status.assert_called_once_with(False, 123.0)
        self.assertIsNone(service._startup_manual_target)
        self.assertIs(updated, published_pm_status)

    def test_apply_startup_manual_target_keeps_pending_target_while_charger_retry_is_active(self):
        charger_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            _startup_manual_target=False,
            _charger_backend=charger_backend,
            _charger_retry_reason="offline",
            _charger_retry_source="enable",
            _charger_retry_until=130.0,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        pm_status = {"output": True, "apower": 1200.0, "current": 5.2}

        updated = controller.apply_startup_manual_target(pm_status, 123.0)

        charger_backend.set_enabled.assert_not_called()
        service._publish_local_pm_status.assert_not_called()
        self.assertIs(updated, pm_status)
        self.assertIs(service._startup_manual_target, False)

    def test_apply_startup_manual_target_falls_back_to_local_pm_status_update_without_helper(self):
        service = SimpleNamespace(
            _startup_manual_target=False,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        updated = controller.apply_startup_manual_target(
            {"output": True, "apower": 1200.0, "current": 5.2},
            123.0,
        )

        service._queue_relay_command.assert_called_once_with(False, 123.0)
        self.assertIsNone(service._startup_manual_target)
        self.assertFalse(updated["output"])
        self.assertEqual(updated["apower"], 0.0)
        self.assertEqual(updated["current"], 0.0)

    def test_apply_startup_manual_target_marks_failure_when_queueing_raises(self):
        service = SimpleNamespace(
            _startup_manual_target=False,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(side_effect=RuntimeError("boom")),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        pm_status = {"output": True, "apower": 1200.0, "current": 5.2}

        updated = controller.apply_startup_manual_target(pm_status, 123.0)

        self.assertIs(updated, pm_status)
        self.assertIs(service._startup_manual_target, False)
        service._mark_failure.assert_called_once_with("shelly")
        service._warning_throttled.assert_called_once()

    def test_apply_startup_manual_target_marks_charger_failure_when_native_backend_raises(self):
        charger_backend = SimpleNamespace(set_enabled=MagicMock(side_effect=RuntimeError("boom")))
        service = SimpleNamespace(
            _startup_manual_target=False,
            _charger_backend=charger_backend,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        pm_status = {"output": True, "apower": 1200.0, "current": 5.2}

        updated = controller.apply_startup_manual_target(pm_status, 123.0)

        self.assertIs(updated, pm_status)
        self.assertIs(service._startup_manual_target, False)
        service._mark_failure.assert_called_once_with("charger")
        service._warning_throttled.assert_called_once()
        service._queue_relay_command.assert_not_called()

    def test_apply_startup_manual_target_falls_back_when_local_placeholder_publish_fails(self):
        service = SimpleNamespace(
            _startup_manual_target=False,
            virtual_mode=0,
            auto_shelly_soft_fail_seconds=10.0,
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(side_effect=RuntimeError("publish failed")),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        updated = controller.apply_startup_manual_target(
            {"output": True, "apower": 1200.0, "current": 5.2},
            123.0,
        )

        service._queue_relay_command.assert_called_once_with(False, 123.0)
        service._publish_local_pm_status.assert_called_once_with(False, 123.0)
        self.assertIsNone(service._startup_manual_target)
        self.assertFalse(updated["output"])
        self.assertEqual(updated["apower"], 0.0)
        self.assertEqual(updated["current"], 0.0)
        service._mark_failure.assert_not_called()
        service._warning_throttled.assert_called_once()

    def test_resolve_auto_inputs_uses_recent_cache_and_counts_hit(self):
        service = SimpleNamespace(
            auto_input_cache_seconds=120.0,
            auto_pv_poll_interval_seconds=2.0,
            auto_grid_poll_interval_seconds=2.0,
            auto_battery_poll_interval_seconds=10.0,
            auto_input_validation_poll_seconds=30.0,
            _auto_cached_inputs_used=False,
            _error_state={"cache_hits": 0},
            _last_pv_value=2300.0,
            _last_pv_at=98.0,
            _last_grid_value=-1700.0,
            _last_grid_at=97.0,
            _last_battery_soc_value=61.0,
            _last_battery_soc_at=90.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        pv_power, battery_soc, grid_power = controller.resolve_auto_inputs(
            {
                "captured_at": 100.0,
                "pv_power": None,
                "grid_power": None,
                "battery_soc": None,
            },
            100.0,
            True,
        )

        self.assertEqual(pv_power, 2300.0)
        self.assertEqual(battery_soc, 61.0)
        self.assertEqual(grid_power, -1700.0)
        self.assertTrue(service._auto_cached_inputs_used)
        self.assertEqual(service._error_state["cache_hits"], 1)

    def test_resolve_auto_inputs_rejects_stale_per_source_values_before_cache_fallback(self):
        service = SimpleNamespace(
            auto_input_cache_seconds=20.0,
            auto_pv_poll_interval_seconds=2.0,
            auto_grid_poll_interval_seconds=2.0,
            auto_battery_poll_interval_seconds=10.0,
            auto_input_validation_poll_seconds=30.0,
            _auto_cached_inputs_used=False,
            _error_state={"cache_hits": 0},
            _last_pv_value=2100.0,
            _last_pv_at=98.0,
            _last_grid_value=-1400.0,
            _last_grid_at=92.0,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        pv_power, battery_soc, grid_power = controller.resolve_auto_inputs(
            {
                "captured_at": 100.0,
                "pv_power": 2300.0,
                "pv_captured_at": 50.0,
                "grid_power": -1500.0,
                "grid_captured_at": 96.0,
                "battery_soc": 55.0,
                "battery_captured_at": 60.0,
            },
            100.0,
            True,
        )

        self.assertEqual(pv_power, 2100.0)
        self.assertIsNone(battery_soc)
        self.assertEqual(grid_power, -1500.0)
        self.assertTrue(service._auto_cached_inputs_used)
        self.assertEqual(service._error_state["cache_hits"], 1)

    def test_resolve_auto_inputs_does_not_reuse_equally_stale_cache(self):
        service = SimpleNamespace(
            auto_input_cache_seconds=20.0,
            auto_pv_poll_interval_seconds=2.0,
            auto_grid_poll_interval_seconds=2.0,
            auto_battery_poll_interval_seconds=10.0,
            auto_input_validation_poll_seconds=30.0,
            _auto_cached_inputs_used=False,
            _error_state={"cache_hits": 0},
            _last_pv_value=2100.0,
            _last_pv_at=90.0,
            _last_grid_value=None,
            _last_grid_at=None,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        pv_power, battery_soc, grid_power = controller.resolve_auto_inputs(
            {
                "captured_at": 100.0,
                "pv_power": 2300.0,
                "pv_captured_at": 50.0,
                "grid_power": None,
                "battery_soc": None,
            },
            100.0,
            True,
        )

        self.assertIsNone(pv_power)
        self.assertIsNone(battery_soc)
        self.assertIsNone(grid_power)
        self.assertFalse(service._auto_cached_inputs_used)
        self.assertEqual(service._error_state["cache_hits"], 0)

    def test_resolve_auto_inputs_rejects_future_source_timestamps_before_cache_fallback(self):
        service = SimpleNamespace(
            auto_input_cache_seconds=20.0,
            auto_pv_poll_interval_seconds=2.0,
            auto_grid_poll_interval_seconds=2.0,
            auto_battery_poll_interval_seconds=10.0,
            auto_input_validation_poll_seconds=30.0,
            _auto_cached_inputs_used=False,
            _error_state={"cache_hits": 0},
            _last_pv_value=2100.0,
            _last_pv_at=98.0,
            _last_grid_value=-1400.0,
            _last_grid_at=97.0,
            _last_battery_soc_value=61.0,
            _last_battery_soc_at=96.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        pv_power, battery_soc, grid_power = controller.resolve_auto_inputs(
            {
                "captured_at": 100.0,
                "pv_power": 2300.0,
                "pv_captured_at": 102.0,
                "grid_power": -1500.0,
                "grid_captured_at": 103.0,
                "battery_soc": 55.0,
                "battery_captured_at": 104.0,
            },
            100.0,
            True,
        )

        self.assertEqual(pv_power, 2100.0)
        self.assertEqual(battery_soc, 61.0)
        self.assertEqual(grid_power, -1400.0)
        self.assertTrue(service._auto_cached_inputs_used)
        self.assertEqual(service._error_state["cache_hits"], 1)

    def test_auto_input_source_max_age_prefers_source_poll_budget_over_validation_budget(self):
        service = SimpleNamespace(
            auto_pv_poll_interval_seconds=2.0,
            auto_battery_poll_interval_seconds=10.0,
            auto_input_validation_poll_seconds=30.0,
        )

        self.assertEqual(UpdateCycleController._auto_input_source_max_age_seconds(service, "auto_pv_poll_interval_seconds"), 4.0)
        self.assertEqual(
            UpdateCycleController._auto_input_source_max_age_seconds(service, "auto_battery_poll_interval_seconds"),
            20.0,
        )

    def test_resolve_pm_status_for_update_rejects_worker_snapshot_older_than_soft_fail_budget(self):
        service = SimpleNamespace(
            _last_pm_status={"output": True},
            _last_pm_status_at=95.0,
            _last_pm_status_confirmed=True,
            auto_shelly_soft_fail_seconds=10.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(
            controller.resolve_pm_status_for_update(
                service,
                {"pm_status": {"output": False}, "pm_confirmed": True, "pm_captured_at": 80.0},
                100.0,
            ),
            {"output": True, "_pm_confirmed": True},
        )
        self.assertEqual(service._last_pm_status, {"output": True})

    def test_resolve_pm_status_for_update_rejects_future_worker_snapshot(self):
        service = SimpleNamespace(
            _last_pm_status={"output": True},
            _last_pm_status_at=95.0,
            _last_pm_status_confirmed=True,
            auto_shelly_soft_fail_seconds=10.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(
            controller.resolve_pm_status_for_update(
                service,
                {"pm_status": {"output": False}, "pm_confirmed": True, "pm_captured_at": 102.5},
                100.0,
            ),
            {"output": True, "_pm_confirmed": True},
        )
        self.assertEqual(service._last_pm_status, {"output": True})

    def test_resolve_pm_status_for_update_rejects_future_cached_soft_fail_snapshot(self):
        service = SimpleNamespace(
            _last_pm_status={"output": True},
            _last_pm_status_at=102.5,
            _last_pm_status_confirmed=True,
            auto_shelly_soft_fail_seconds=10.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertIsNone(controller.resolve_pm_status_for_update(service, {}, 100.0))

    def test_resolve_pm_status_for_update_accepts_fresh_direct_snapshot_when_soft_fail_budget_is_zero(self):
        service = SimpleNamespace(
            _last_pm_status=None,
            _last_pm_status_at=None,
            _last_pm_status_confirmed=False,
            auto_shelly_soft_fail_seconds=0.0,
            _worker_poll_interval_seconds=1.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(
            controller.resolve_pm_status_for_update(
                service,
                {"pm_status": {"output": False}, "pm_confirmed": True, "pm_captured_at": 99.6},
                100.0,
            ),
            {"output": False, "_pm_confirmed": True},
        )
        self.assertEqual(service._last_pm_status, {"output": False, "_pm_confirmed": True})
        self.assertEqual(service._last_pm_status_at, 99.6)
        self.assertTrue(service._last_pm_status_confirmed)

    def test_resolve_pm_status_for_update_rejects_inconsistent_confirmed_worker_snapshot(self):
        service = SimpleNamespace(
            _last_pm_status={"output": True},
            _last_pm_status_at=95.0,
            _last_pm_status_confirmed=True,
            auto_shelly_soft_fail_seconds=10.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertEqual(
            controller.resolve_pm_status_for_update(
                service,
                {"captured_at": 100.0, "pm_status": {"apower": 1800.0}, "pm_confirmed": True, "pm_captured_at": 99.5},
                100.0,
            ),
            {"output": True, "_pm_confirmed": True},
        )
        self.assertEqual(service._last_pm_status, {"output": True})

    def test_update_offline_path_publishes_disconnected_state(self):
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=200.0),
            _state_summary=lambda: "state",
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={"pm_status": None}),
            _last_pm_status=None,
            _last_pm_status_at=None,
            _last_confirmed_pm_status=None,
            _last_confirmed_pm_status_at=None,
            auto_shelly_soft_fail_seconds=10.0,
            _last_voltage=230.0,
            virtual_startstop=1,
            phase="L1",
            voltage_mode="phase",
            _set_health=MagicMock(),
            _publish_live_measurements=MagicMock(return_value=True),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _publish_dbus_path=MagicMock(return_value=False),
            _bump_update_index=MagicMock(),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _last_health_reason="init",
            _last_health_code=0,
            charging_started_at=None,
            energy_at_start=0.0,
            last_status=0,
            virtual_mode=0,
            virtual_enable=1,
            _dbusservice={"/Ac/Power": 0.0},
            service_name="com.victronenergy.evcharger.http_60",
            last_update=0.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        result = controller.update()

        self.assertTrue(result)
        service._watchdog_recover.assert_called_once_with(200.0)
        service._publish_live_measurements.assert_called_once()
        service._set_health.assert_called_once_with("shelly-offline", cached=False)
        service._bump_update_index.assert_called_once_with(200.0)
        self.assertEqual(service.virtual_startstop, 0)
        self.assertEqual(service.last_update, 200.0)

    def test_publish_offline_update_uses_recent_confirmed_relay_state_only(self):
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=200.0),
            _last_voltage=230.0,
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=199.0,
            _worker_poll_interval_seconds=1.0,
            relay_sync_timeout_seconds=2.0,
            virtual_startstop=0,
            phase="L1",
            voltage_mode="phase",
            _set_health=MagicMock(),
            _publish_live_measurements=MagicMock(return_value=False),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _publish_dbus_path=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _last_health_reason="init",
            _last_health_code=0,
            charging_started_at=None,
            energy_at_start=0.0,
            last_status=0,
            virtual_mode=0,
            virtual_enable=1,
            _dbusservice={"/Ac/Power": 0.0},
            service_name="svc",
            last_update=0.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.publish_offline_update(200.0))
        self.assertEqual(service.virtual_startstop, 1)

        service._last_confirmed_pm_status_at = 195.0
        self.assertTrue(controller.publish_offline_update(200.0))
        self.assertEqual(service.virtual_startstop, 0)

    def test_publish_offline_update_rejects_future_confirmed_relay_timestamp(self):
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=200.0),
            _last_voltage=230.0,
            _last_confirmed_pm_status={"output": True},
            _last_confirmed_pm_status_at=202.5,
            _worker_poll_interval_seconds=1.0,
            relay_sync_timeout_seconds=2.0,
            virtual_startstop=0,
            phase="L1",
            voltage_mode="phase",
            _set_health=MagicMock(),
            _publish_live_measurements=MagicMock(return_value=False),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _publish_dbus_path=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _last_health_reason="init",
            _last_health_code=0,
            charging_started_at=None,
            energy_at_start=0.0,
            last_status=0,
            virtual_mode=0,
            virtual_enable=1,
            _dbusservice={"/Ac/Power": 0.0},
            service_name="svc",
            last_update=0.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.publish_offline_update(200.0))
        self.assertEqual(service.virtual_startstop, 0)

    def test_publish_online_update_prefers_backend_phase_distribution_metadata(self):
        service = SimpleNamespace(
            phase="L1",
            voltage_mode="phase",
            _publish_live_measurements=MagicMock(return_value=False),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.update_virtual_state = MagicMock(return_value=False)

        changed = controller.publish_online_update(
            {
                "output": True,
                "_phase_selection": "P1_P2",
                "_phase_powers_w": (1200.0, 1200.0, 0.0),
                "_phase_currents_a": (5.2, 5.2, 0.0),
            },
            2,
            12.5,
            True,
            2400.0,
            230.0,
            200.0,
        )

        self.assertFalse(changed)
        self.assertEqual(
            service._publish_live_measurements.call_args.args[3],
            {
                "L1": {"power": 1200.0, "voltage": 230.0, "current": 5.2},
                "L2": {"power": 1200.0, "voltage": 230.0, "current": 5.2},
                "L3": {"power": 0.0, "voltage": 230.0, "current": 0.0},
            },
        )

    def test_publish_online_update_prefers_fresh_native_charger_measurements(self):
        service = SimpleNamespace(
            phase="L1",
            voltage_mode="phase",
            _charger_backend=SimpleNamespace(),
            _last_charger_state_actual_current_amps=12.3,
            _last_charger_state_power_w=2830.0,
            _last_charger_state_energy_kwh=7.25,
            _last_charger_state_at=200.0,
            auto_shelly_soft_fail_seconds=10.0,
            _publish_live_measurements=MagicMock(return_value=False),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
        controller.update_virtual_state = MagicMock(return_value=False)

        changed = controller.publish_online_update(
            {
                "output": True,
                "apower": 1200.0,
                "current": 5.2,
                "aenergy": {"total": 1000.0},
            },
            2,
            1.0,
            True,
            1200.0,
            230.0,
            200.0,
        )

        self.assertFalse(changed)
        self.assertEqual(service._publish_live_measurements.call_args.args[0], 2830.0)
        self.assertAlmostEqual(service._publish_live_measurements.call_args.args[2], 12.3)
        controller.update_virtual_state.assert_called_once_with(2, 7.25, True)

    def test_publish_offline_update_uses_backend_phase_metadata_for_display(self):
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=200.0),
            _last_voltage=230.0,
            _last_confirmed_pm_status={
                "output": True,
                "_phase_selection": "P1",
                "_phase_powers_w": (0.0, 0.0, 0.0),
                "_phase_currents_a": (0.0, 0.0, 0.0),
            },
            _last_confirmed_pm_status_at=199.0,
            _worker_poll_interval_seconds=1.0,
            relay_sync_timeout_seconds=2.0,
            virtual_startstop=0,
            phase="L1",
            voltage_mode="phase",
            _set_health=MagicMock(),
            _publish_live_measurements=MagicMock(return_value=False),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _publish_dbus_path=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _last_health_reason="init",
            _last_health_code=0,
            charging_started_at=None,
            energy_at_start=0.0,
            last_status=0,
            virtual_mode=0,
            virtual_enable=1,
            _dbusservice={"/Ac/Power": 0.0},
            service_name="svc",
            last_update=0.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
        )

        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.publish_offline_update(200.0))
        self.assertEqual(
            service._publish_live_measurements.call_args.args[3],
            {
                "L1": {"power": 0.0, "voltage": 230.0, "current": 0.0},
                "L2": {"power": 0.0, "voltage": 230.0, "current": 0.0},
                "L3": {"power": 0.0, "voltage": 230.0, "current": 0.0},
            },
        )

    def test_cached_input_from_service_rejects_future_cached_timestamp(self):
        service = SimpleNamespace(_last_pv_value=2400.0, _last_pv_at=102.5)

        self.assertEqual(
            UpdateCycleController._cached_input_from_service(
                service,
                "_last_pv_value",
                "_last_pv_at",
                100.0,
                20.0,
            ),
            (None, False),
        )

    def test_update_cycle_helpers_cover_cached_pm_status_session_branches_and_logging(self):
        service = SimpleNamespace(
            charging_started_at=None,
            energy_at_start=1.5,
            virtual_mode=1,
            virtual_enable=1,
            phase="3P",
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _last_pm_status={"output": True},
            _last_pm_status_at=95.0,
            _last_pm_status_confirmed=True,
            auto_shelly_soft_fail_seconds=10.0,
            _last_auto_metrics={"surplus": 2500.0, "grid": -2200.0, "soc": 63.0},
            _last_health_reason="running",
            auto_audit_log=True,
            _queue_relay_command=MagicMock(),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=False,
            charging_threshold_watts=1500.0,
            idle_status=1,
            _time_now=MagicMock(return_value=123.0),
            _bump_update_index=MagicMock(),
            virtual_startstop=1,
            service_name="com.victronenergy.evcharger.http_60",
            _dbusservice={"/Ac/Power": 321.0},
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        charging_time, session_energy = controller.session_state_from_status(service, 1, 2.0, True, 100.0)
        self.assertEqual((charging_time, session_energy), (0, 0.5))

        self.assertEqual(
            controller.phase_energies_for_total(service, 6.0),
            {"L1": 2.0, "L2": 2.0, "L3": 2.0},
        )

        self.assertEqual(
            controller.resolve_pm_status_for_update(service, {"pm_status": None}, 100.0),
            {"output": True, "_pm_confirmed": True},
        )

        confirmed_pm_status = controller.resolve_pm_status_for_update(
            service,
            {"pm_status": {"output": False}, "pm_confirmed": True, "pm_captured_at": 101.0},
            101.0,
        )
        self.assertEqual(confirmed_pm_status, {"output": False, "_pm_confirmed": True})
        self.assertTrue(service._last_pm_status_confirmed)

        with patch("shelly_wallbox.update.controller.logging.info") as info_mock:
            controller.log_auto_relay_change(service, True)
            controller.sign_of_life()

        self.assertEqual(info_mock.call_count, 2)

        relay_on, power, current, confirmed = controller.apply_relay_decision(
            False,
            True,
            {"output": True, "apower": 1200.0, "current": 5.2, "_pm_confirmed": True},
            1200.0,
            5.2,
            123.0,
            True,
        )
        self.assertEqual((relay_on, power, current, confirmed), (False, 0.0, 0.0, False))
        service._publish_local_pm_status.assert_called_once_with(False, 123.0)

    def test_apply_relay_decision_and_update_cover_failure_and_warning_paths(self):
        service = SimpleNamespace(
            _last_auto_metrics={"surplus": None, "grid": None, "soc": None},
            _last_health_reason="init",
            auto_audit_log=False,
            auto_shelly_soft_fail_seconds=10.0,
            _queue_relay_command=MagicMock(side_effect=RuntimeError("boom")),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _state_summary=lambda: "state",
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={"pm_status": {"output": False}}),
            _last_pm_status=None,
            _last_pm_status_at=None,
            _last_pm_status_confirmed=False,
            _safe_float=lambda value, default=0.0: float(value) if value is not None else default,
            virtual_mode=1,
            phase="L1",
            voltage_mode="line",
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _auto_decide_relay=MagicMock(side_effect=RuntimeError("auto failed")),
            _publish_live_measurements=MagicMock(return_value=False),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _last_health_code=0,
            charging_started_at=None,
            energy_at_start=0.0,
            last_status=0,
            virtual_enable=1,
            _dbusservice={"/Ac/Power": 0.0},
            service_name="com.victronenergy.evcharger.http_60",
            last_update=0.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _last_voltage=230.0,
            virtual_startstop=0,
            charging_threshold_watts=1500.0,
            idle_status=1,
            _last_successful_update_at=None,
            _last_recovery_attempt_at=None,
            _error_state={"cache_hits": 0},
            _last_pv_value=None,
            _last_pv_at=None,
            _last_grid_value=None,
            _last_grid_at=None,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
            auto_input_cache_seconds=0.0,
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=False,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed = controller.apply_relay_decision(
            False,
            True,
            {"output": True, "apower": 1200.0, "current": 5.2, "_pm_confirmed": True},
            1200.0,
            5.2,
            100.0,
            False,
        )
        self.assertEqual((relay_on, power, current, confirmed), (True, 1200.0, 5.2, True))
        service._mark_failure.assert_called_once_with("shelly")
        service._warning_throttled.assert_called_once()

        with patch("shelly_wallbox.update.controller.logging.warning") as warning_mock:
            self.assertTrue(controller.update())
        warning_mock.assert_called_once()

    def test_derive_status_code_prefers_fresh_native_charger_enabled_readback(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_enabled=True,
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=1,
        )

        status = UpdateCycleController.derive_status_code(service, False, 0.0, False, 100.0)

        self.assertEqual(status, 1)
        self.assertEqual(service._last_status_source, "enabled-idle")
        self.assertEqual(service._last_charger_fault_active, 0)

    def test_derive_status_code_maps_fresh_native_charger_fault_to_disconnected(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="fault",
            _last_charger_state_fault="overcurrent error",
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=1,
        )

        status = UpdateCycleController.derive_status_code(service, True, 2000.0, True, 100.0)

        self.assertEqual(status, 0)
        self.assertEqual(service._last_status_source, "charger-fault")
        self.assertEqual(service._last_charger_fault_active, 1)

    def test_derive_status_code_maps_contactor_lockout_to_disconnected_fault_status(self):
        service = SimpleNamespace(
            _last_health_reason="contactor-lockout-open",
            charging_threshold_watts=1500.0,
            idle_status=1,
        )

        status = UpdateCycleController.derive_status_code(
            service,
            True,
            2000.0,
            True,
            health_reason="contactor-lockout-open",
            now=100.0,
        )

        self.assertEqual(status, 0)
        self.assertEqual(service._last_status_source, "contactor-lockout-open")

    def test_derive_status_code_prefers_contactor_lockout_over_fresh_native_charger_charging(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="charging",
            _last_charger_state_at=100.0,
            _last_health_reason="contactor-lockout-open",
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=1,
        )

        status = UpdateCycleController.derive_status_code(
            service,
            True,
            2000.0,
            True,
            health_reason="contactor-lockout-open",
            now=100.0,
        )

        self.assertEqual(status, 0)
        self.assertEqual(service._last_status_source, "contactor-lockout-open")

    def test_derive_status_code_maps_switch_feedback_mismatch_to_disconnected_fault_status(self):
        service = SimpleNamespace(
            _last_health_reason="contactor-feedback-mismatch",
            charging_threshold_watts=1500.0,
            idle_status=1,
        )

        status = UpdateCycleController.derive_status_code(
            service,
            True,
            2000.0,
            True,
            health_reason="contactor-feedback-mismatch",
            now=100.0,
        )

        self.assertEqual(status, 0)
        self.assertEqual(service._last_status_source, "contactor-feedback-fault")

    def test_derive_status_code_prefers_feedback_fault_over_fresh_native_charger_ready(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="ready",
            _last_charger_state_at=100.0,
            _last_health_reason="contactor-feedback-mismatch",
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=6,
        )

        status = UpdateCycleController.derive_status_code(
            service,
            True,
            0.0,
            True,
            health_reason="contactor-feedback-mismatch",
            now=100.0,
        )

        self.assertEqual(status, 0)
        self.assertEqual(service._last_status_source, "contactor-feedback-fault")

    def test_derive_status_code_maps_fresh_native_charger_charging_status_to_charging(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="charging",
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=6,
        )

        status = UpdateCycleController.derive_status_code(service, False, 0.0, False, 100.0)

        self.assertEqual(status, 2)
        self.assertEqual(service._last_status_source, "charger-status-charging")
        self.assertEqual(service._last_charger_fault_active, 0)

    def test_derive_status_code_keeps_native_charger_charging_truth_when_meter_power_is_zero(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="charging",
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=6,
        )

        status = UpdateCycleController.derive_status_code(service, True, 0.0, True, 100.0)

        self.assertEqual(status, 2)
        self.assertEqual(service._last_status_source, "charger-status-charging")

    def test_derive_status_code_maps_fresh_native_charger_ready_status_to_idle_status(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="ready",
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=6,
        )

        status = UpdateCycleController.derive_status_code(service, False, 0.0, False, 100.0)

        self.assertEqual(status, 6)
        self.assertEqual(service._last_status_source, "charger-status-ready")
        self.assertEqual(service._last_charger_fault_active, 0)

    def test_derive_status_code_maps_fresh_native_charger_paused_status_to_auto_waiting(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="paused",
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=1,
        )

        status = UpdateCycleController.derive_status_code(service, True, 0.0, True, 100.0)

        self.assertEqual(status, 4)
        self.assertEqual(service._last_status_source, "charger-status-waiting")
        self.assertEqual(service._last_charger_fault_active, 0)

    def test_derive_status_code_maps_fresh_native_charger_paused_status_to_manual_waiting(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="paused",
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=1,
        )

        status = UpdateCycleController.derive_status_code(service, True, 0.0, False, 100.0)

        self.assertEqual(status, 6)
        self.assertEqual(service._last_status_source, "charger-status-waiting")
        self.assertEqual(service._last_charger_fault_active, 0)

    def test_derive_status_code_maps_fresh_native_charger_completed_status_to_finished(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="completed",
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
            charging_threshold_watts=1500.0,
            idle_status=1,
        )

        status = UpdateCycleController.derive_status_code(service, True, 0.0, False, 100.0)

        self.assertEqual(status, 3)
        self.assertEqual(service._last_status_source, "charger-status-finished")
        self.assertEqual(service._last_charger_fault_active, 0)

    def test_session_state_from_status_prefers_fresh_native_charger_enabled_readback(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(),
            _last_charger_state_enabled=True,
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
            charging_started_at=90.0,
            energy_at_start=1.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        charging_time, session_energy = controller.session_state_from_status(service, 1, 2.0, False, 100.0)

        self.assertEqual((charging_time, session_energy), (10, 1.0))

    def test_apply_relay_decision_uses_native_charger_backend_when_available(self):
        charger_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            _last_auto_metrics={"surplus": None, "grid": None, "soc": None},
            _last_health_reason="init",
            auto_audit_log=False,
            auto_shelly_soft_fail_seconds=10.0,
            _charger_backend=charger_backend,
            _queue_relay_command=MagicMock(),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=False,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed = controller.apply_relay_decision(
            True,
            False,
            {"output": False, "_pm_confirmed": True},
            0.0,
            0.0,
            100.0,
            False,
        )

        self.assertEqual((relay_on, power, current, confirmed), (True, 0.0, 0.0, False))
        charger_backend.set_enabled.assert_called_once_with(True)
        service._queue_relay_command.assert_not_called()
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)

    def test_charger_health_override_detects_fault_like_readback(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(set_enabled=MagicMock()),
            _last_charger_state_status="error",
            _last_charger_state_fault="overcurrent fault",
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"charger-fault": 26}.get(reason, 99))

        self.assertEqual(controller.charger_health_override(service, 100.0), "charger-fault")

    def test_charger_health_override_ignores_benign_readback_text(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(set_enabled=MagicMock()),
            _last_charger_state_status="paused",
            _last_charger_state_fault="vehicle-sleeping",
            _last_charger_state_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"charger-fault": 26}.get(reason, 99))

        self.assertIsNone(controller.charger_health_override(service, 100.0))

    def test_charger_health_override_prefers_fresh_transport_issue(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(set_enabled=MagicMock()),
            _last_charger_state_status="charging",
            _last_charger_state_fault="",
            _last_charger_state_at=100.0,
            _last_charger_transport_reason="offline",
            _last_charger_transport_source="read",
            _last_charger_transport_detail="Modbus slave 1 did not respond",
            _last_charger_transport_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {
                "charger-fault": 26,
                "charger-transport-offline": 37,
            }.get(reason, 99),
        )

        self.assertEqual(controller.charger_health_override(service, 100.0), "charger-transport-offline")

    def test_charger_health_override_falls_back_to_active_retry_reason(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(set_enabled=MagicMock()),
            _last_charger_state_status="charging",
            _last_charger_state_fault="",
            _last_charger_state_at=100.0,
            _last_charger_transport_reason=None,
            _last_charger_transport_source=None,
            _last_charger_transport_detail=None,
            _last_charger_transport_at=None,
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=105.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {
                "charger-fault": 26,
                "charger-transport-offline": 37,
            }.get(reason, 99),
        )

        self.assertEqual(controller.charger_health_override(service, 100.0), "charger-transport-offline")

    def test_switch_feedback_health_override_detects_interlock_block(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=True,
            _last_switch_interlock_ok=False,
            _last_switch_feedback_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {"contactor-interlock": 28, "contactor-feedback-mismatch": 29}.get(reason, 99),
        )

        self.assertEqual(controller.switch_feedback_health_override(service, True, False, 100.0), "contactor-interlock")

    def test_switch_feedback_health_override_detects_feedback_mismatch(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=False,
            _last_switch_interlock_ok=True,
            _last_switch_feedback_at=100.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {"contactor-interlock": 28, "contactor-feedback-mismatch": 29}.get(reason, 99),
        )

        self.assertEqual(
            controller.switch_feedback_health_override(service, True, True, 100.0),
            "contactor-feedback-mismatch",
        )

    def test_switch_feedback_health_override_prefers_interlock_block_over_other_signals(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=True,
            _last_switch_interlock_ok=False,
            _last_switch_feedback_at=100.0,
            _contactor_suspected_open_since=90.0,
            _contactor_suspected_welded_since=91.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {"contactor-interlock": 28, "contactor-feedback-mismatch": 29}.get(reason, 99),
        )

        self.assertEqual(
            controller.switch_feedback_health_override(
                service,
                True,
                True,
                100.0,
                power=2300.0,
                current=10.0,
                pm_confirmed=True,
            ),
            "contactor-interlock",
        )
        self.assertIsNone(service._contactor_suspected_open_since)
        self.assertIsNone(service._contactor_suspected_welded_since)

    def test_switch_feedback_health_override_prefers_explicit_open_feedback_over_open_contactor_heuristic(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=False,
            _last_switch_interlock_ok=True,
            _last_switch_feedback_at=100.0,
            _contactor_suspected_open_since=90.0,
            _contactor_suspected_welded_since=None,
            _charger_backend=SimpleNamespace(),
            _last_charger_state_status="charging",
            _last_charger_state_at=100.0,
            charging_threshold_watts=100.0,
            min_current=6.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {"contactor-interlock": 28, "contactor-feedback-mismatch": 29}.get(reason, 99),
        )

        self.assertEqual(
            controller.switch_feedback_health_override(
                service,
                True,
                True,
                100.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            ),
            "contactor-feedback-mismatch",
        )
        self.assertIsNone(service._contactor_suspected_open_since)

    def test_switch_feedback_health_override_prefers_explicit_closed_feedback_over_welded_heuristic(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=True,
            _last_switch_interlock_ok=True,
            _last_switch_feedback_at=100.0,
            _contactor_suspected_open_since=None,
            _contactor_suspected_welded_since=90.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {"contactor-interlock": 28, "contactor-feedback-mismatch": 29}.get(reason, 99),
        )

        self.assertEqual(
            controller.switch_feedback_health_override(
                service,
                False,
                False,
                100.0,
                power=2300.0,
                current=10.0,
                pm_confirmed=True,
            ),
            "contactor-feedback-mismatch",
        )
        self.assertIsNone(service._contactor_suspected_welded_since)

    def test_switch_feedback_health_override_suspects_welded_contactor_without_feedback(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_switch_feedback_at=None,
            _contactor_suspected_open_since=None,
            _contactor_suspected_welded_since=None,
            _charger_backend=None,
            charging_threshold_watts=100.0,
            min_current=6.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {"contactor-suspected-welded": 31}.get(reason, 99),
        )

        self.assertIsNone(
            controller.switch_feedback_health_override(
                service,
                False,
                False,
                100.0,
                power=1200.0,
                current=5.2,
                pm_confirmed=True,
            )
        )
        self.assertEqual(service._contactor_suspected_welded_since, 100.0)
        self.assertEqual(
            controller.switch_feedback_health_override(
                service,
                False,
                False,
                111.0,
                power=1200.0,
                current=5.2,
                pm_confirmed=True,
            ),
            "contactor-suspected-welded",
        )

    def test_switch_feedback_health_override_suspects_open_contactor_from_charger_activity(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_switch_feedback_at=None,
            _contactor_suspected_open_since=None,
            _contactor_suspected_welded_since=None,
            _charger_backend=object(),
            _last_charger_state_at=100.0,
            _last_charger_state_status="charging",
            _last_charger_state_power_w=0.0,
            _last_charger_state_actual_current_amps=0.0,
            charging_threshold_watts=100.0,
            min_current=6.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {"contactor-suspected-open": 30}.get(reason, 99),
        )

        self.assertIsNone(
            controller.switch_feedback_health_override(
                service,
                True,
                True,
                100.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            )
        )
        self.assertEqual(service._contactor_suspected_open_since, 100.0)
        service._last_charger_state_at = 110.0
        self.assertEqual(
            controller.switch_feedback_health_override(
                service,
                True,
                True,
                110.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            ),
            "contactor-suspected-open",
        )

    def test_switch_feedback_health_override_latches_repeated_welded_contactor_faults(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_switch_feedback_at=None,
            _contactor_suspected_open_since=None,
            _contactor_suspected_welded_since=None,
            _contactor_fault_counts={},
            _contactor_fault_active_reason=None,
            _contactor_fault_active_since=None,
            _contactor_lockout_reason="",
            _contactor_lockout_source="",
            _contactor_lockout_at=None,
            _charger_backend=None,
            charging_threshold_watts=100.0,
            min_current=6.0,
            auto_shelly_soft_fail_seconds=10.0,
            auto_contactor_fault_latch_count=2,
            auto_contactor_fault_latch_seconds=120.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {
                "contactor-suspected-welded": 31,
                "contactor-lockout-welded": 33,
            }.get(reason, 99),
        )

        self.assertIsNone(
            controller.switch_feedback_health_override(
                service,
                False,
                False,
                100.0,
                power=1200.0,
                current=5.2,
                pm_confirmed=True,
            )
        )
        self.assertEqual(
            controller.switch_feedback_health_override(
                service,
                False,
                False,
                111.0,
                power=1200.0,
                current=5.2,
                pm_confirmed=True,
            ),
            "contactor-suspected-welded",
        )
        self.assertEqual(service._contactor_fault_counts, {"contactor-suspected-welded": 1})

        self.assertIsNone(
            controller.switch_feedback_health_override(
                service,
                False,
                False,
                112.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            )
        )
        self.assertIsNone(service._contactor_fault_active_reason)
        self.assertIsNone(
            controller.switch_feedback_health_override(
                service,
                False,
                False,
                113.0,
                power=1200.0,
                current=5.2,
                pm_confirmed=True,
            )
        )

        self.assertEqual(
            controller.switch_feedback_health_override(
                service,
                False,
                False,
                124.0,
                power=1200.0,
                current=5.2,
                pm_confirmed=True,
            ),
            "contactor-lockout-welded",
        )
        self.assertEqual(service._contactor_fault_counts, {"contactor-suspected-welded": 2})
        self.assertEqual(service._contactor_lockout_reason, "contactor-suspected-welded")
        self.assertEqual(service._contactor_lockout_source, "count-threshold")
        self.assertEqual(service._contactor_lockout_at, 124.0)

    def test_switch_feedback_health_override_latches_persistent_open_contactor_fault(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_switch_feedback_at=None,
            _contactor_suspected_open_since=None,
            _contactor_suspected_welded_since=None,
            _contactor_fault_counts={},
            _contactor_fault_active_reason=None,
            _contactor_fault_active_since=None,
            _contactor_lockout_reason="",
            _contactor_lockout_source="",
            _contactor_lockout_at=None,
            _charger_backend=object(),
            _last_charger_state_at=100.0,
            _last_charger_state_status="charging",
            _last_charger_state_power_w=0.0,
            _last_charger_state_actual_current_amps=0.0,
            charging_threshold_watts=100.0,
            min_current=6.0,
            auto_shelly_soft_fail_seconds=10.0,
            auto_contactor_fault_latch_count=3,
            auto_contactor_fault_latch_seconds=15.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {
                "contactor-suspected-open": 30,
                "contactor-lockout-open": 32,
            }.get(reason, 99),
        )

        self.assertIsNone(
            controller.switch_feedback_health_override(
                service,
                True,
                True,
                100.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            )
        )
        service._last_charger_state_at = 110.0
        self.assertEqual(
            controller.switch_feedback_health_override(
                service,
                True,
                True,
                110.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            ),
            "contactor-suspected-open",
        )
        self.assertEqual(service._contactor_fault_counts, {"contactor-suspected-open": 1})

        service._last_charger_state_at = 126.0
        self.assertEqual(
            controller.switch_feedback_health_override(
                service,
                True,
                True,
                126.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            ),
            "contactor-lockout-open",
        )
        self.assertEqual(service._contactor_lockout_reason, "contactor-suspected-open")
        self.assertEqual(service._contactor_lockout_source, "persistent")
        self.assertEqual(service._contactor_lockout_at, 126.0)

    def test_contactor_feedback_scenario_ready_but_no_power_does_not_false_positive_as_open_fault(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_switch_feedback_at=None,
            _contactor_suspected_open_since=None,
            _contactor_suspected_welded_since=None,
            _charger_backend=object(),
            _last_charger_state_at=100.0,
            _last_charger_state_status="ready",
            _last_charger_state_power_w=0.0,
            _last_charger_state_actual_current_amps=0.0,
            charging_threshold_watts=100.0,
            min_current=6.0,
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {"contactor-suspected-open": 30}.get(reason, 99),
        )

        self.assertIsNone(
            controller.switch_feedback_health_override(
                service,
                True,
                True,
                100.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            )
        )
        self.assertIsNone(service._contactor_suspected_open_since)

        service._last_charger_state_at = 110.0
        self.assertIsNone(
            controller.switch_feedback_health_override(
                service,
                True,
                True,
                110.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            )
        )
        self.assertIsNone(service._contactor_suspected_open_since)

    def test_contactor_feedback_scenario_stuck_welded_escalates_from_suspicion_to_lockout(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_switch_feedback_at=None,
            _contactor_suspected_open_since=None,
            _contactor_suspected_welded_since=None,
            _contactor_fault_counts={},
            _contactor_fault_active_reason=None,
            _contactor_fault_active_since=None,
            _contactor_lockout_reason="",
            _contactor_lockout_source="",
            _contactor_lockout_at=None,
            _charger_backend=None,
            charging_threshold_watts=100.0,
            min_current=6.0,
            auto_shelly_soft_fail_seconds=10.0,
            auto_contactor_fault_latch_count=2,
            auto_contactor_fault_latch_seconds=120.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {
                "contactor-suspected-welded": 31,
                "contactor-lockout-welded": 33,
            }.get(reason, 99),
        )

        self.assertIsNone(
            controller.switch_feedback_health_override(
                service,
                False,
                False,
                100.0,
                power=1200.0,
                current=5.2,
                pm_confirmed=True,
            )
        )
        self.assertEqual(service._contactor_suspected_welded_since, 100.0)

        self.assertEqual(
            controller.switch_feedback_health_override(
                service,
                False,
                False,
                111.0,
                power=1200.0,
                current=5.2,
                pm_confirmed=True,
            ),
            "contactor-suspected-welded",
        )
        self.assertEqual(service._contactor_fault_counts, {"contactor-suspected-welded": 1})

        self.assertIsNone(
            controller.switch_feedback_health_override(
                service,
                False,
                False,
                112.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            )
        )

        self.assertIsNone(
            controller.switch_feedback_health_override(
                service,
                False,
                False,
                113.0,
                power=1200.0,
                current=5.2,
                pm_confirmed=True,
            )
        )

        self.assertEqual(
            controller.switch_feedback_health_override(
                service,
                False,
                False,
                124.0,
                power=1200.0,
                current=5.2,
                pm_confirmed=True,
            ),
            "contactor-lockout-welded",
        )
        self.assertEqual(service._contactor_lockout_reason, "contactor-suspected-welded")
        self.assertEqual(service._contactor_lockout_source, "count-threshold")

    def test_contactor_feedback_scenario_stuck_open_escalates_from_suspicion_to_lockout(self):
        service = SimpleNamespace(
            _last_switch_feedback_closed=None,
            _last_switch_interlock_ok=None,
            _last_switch_feedback_at=None,
            _contactor_suspected_open_since=None,
            _contactor_suspected_welded_since=None,
            _contactor_fault_counts={},
            _contactor_fault_active_reason=None,
            _contactor_fault_active_since=None,
            _contactor_lockout_reason="",
            _contactor_lockout_source="",
            _contactor_lockout_at=None,
            _charger_backend=object(),
            _last_charger_state_at=100.0,
            _last_charger_state_status="charging",
            _last_charger_state_power_w=0.0,
            _last_charger_state_actual_current_amps=0.0,
            charging_threshold_watts=100.0,
            min_current=6.0,
            auto_shelly_soft_fail_seconds=10.0,
            auto_contactor_fault_latch_count=3,
            auto_contactor_fault_latch_seconds=15.0,
        )
        controller = UpdateCycleController(
            service,
            _phase_values,
            lambda reason: {
                "contactor-suspected-open": 30,
                "contactor-lockout-open": 32,
            }.get(reason, 99),
        )

        self.assertIsNone(
            controller.switch_feedback_health_override(
                service,
                True,
                True,
                100.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            )
        )
        self.assertEqual(service._contactor_suspected_open_since, 100.0)

        service._last_charger_state_at = 110.0
        self.assertEqual(
            controller.switch_feedback_health_override(
                service,
                True,
                True,
                110.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            ),
            "contactor-suspected-open",
        )
        self.assertEqual(service._contactor_fault_counts, {"contactor-suspected-open": 1})

        service._last_charger_state_at = 126.0
        self.assertEqual(
            controller.switch_feedback_health_override(
                service,
                True,
                True,
                126.0,
                power=0.0,
                current=0.0,
                pm_confirmed=True,
            ),
            "contactor-lockout-open",
        )
        self.assertEqual(service._contactor_lockout_reason, "contactor-suspected-open")
        self.assertEqual(service._contactor_lockout_source, "persistent")

    def test_apply_charger_current_target_prefers_stable_learned_current(self):
        charger_backend = SimpleNamespace(set_current=MagicMock())
        service = SimpleNamespace(
            _charger_backend=charger_backend,
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            virtual_set_current=16.0,
            min_current=6.0,
            max_current=16.0,
            learned_charge_power_state="stable",
            learned_charge_power_watts=2990.0,
            learned_charge_power_updated_at=95.0,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            phase="L1",
            voltage_mode="phase",
            auto_learn_charge_power_max_age_seconds=21600.0,
            _charger_target_current_amps=None,
            _charger_target_current_applied_at=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        applied = controller.apply_charger_current_target(service, True, 100.0, True)

        self.assertEqual(applied, 13.0)
        charger_backend.set_current.assert_called_once_with(13.0)
        self.assertEqual(service._charger_target_current_amps, 13.0)
        self.assertEqual(service._charger_target_current_applied_at, 100.0)

    def test_apply_charger_current_target_falls_back_to_virtual_current_and_skips_duplicate_write(self):
        charger_backend = SimpleNamespace(set_current=MagicMock())
        service = SimpleNamespace(
            _charger_backend=charger_backend,
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            virtual_set_current=11.0,
            min_current=6.0,
            max_current=16.0,
            learned_charge_power_state="learning",
            learned_charge_power_watts=2990.0,
            learned_charge_power_updated_at=95.0,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            phase="L1",
            voltage_mode="phase",
            auto_learn_charge_power_max_age_seconds=21600.0,
            _charger_target_current_amps=None,
            _charger_target_current_applied_at=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        first = controller.apply_charger_current_target(service, True, 100.0, True)
        second = controller.apply_charger_current_target(service, True, 101.0, True)

        self.assertEqual(first, 11.0)
        self.assertEqual(second, 11.0)
        charger_backend.set_current.assert_called_once_with(11.0)
        self.assertEqual(service._charger_target_current_amps, 11.0)

    def test_apply_charger_current_target_uses_scheduled_night_current_during_scheduled_night_charge(self):
        charger_backend = SimpleNamespace(set_current=MagicMock())
        service = SimpleNamespace(
            _charger_backend=charger_backend,
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            virtual_mode=2,
            virtual_set_current=9.0,
            min_current=6.0,
            max_current=16.0,
            auto_month_windows={4: ((7, 30), (19, 30))},
            auto_scheduled_enabled_days="Mon,Tue,Wed,Thu,Fri",
            auto_scheduled_night_start_delay_seconds=3600.0,
            auto_scheduled_latest_end_time="06:30",
            auto_scheduled_night_current_amps=13.0,
            learned_charge_power_state="stable",
            learned_charge_power_watts=2990.0,
            learned_charge_power_updated_at=95.0,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            phase="L1",
            voltage_mode="phase",
            auto_learn_charge_power_max_age_seconds=21600.0,
            _charger_target_current_amps=None,
            _charger_target_current_applied_at=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        applied = controller.apply_charger_current_target(
            service,
            True,
            datetime(2026, 4, 20, 21, 0).timestamp(),
            True,
        )

        self.assertEqual(applied, 13.0)
        charger_backend.set_current.assert_called_once_with(13.0)
        self.assertEqual(service._charger_target_current_amps, 13.0)

    def test_apply_charger_current_target_marks_charger_failure_when_current_write_raises(self):
        charger_backend = SimpleNamespace(
            set_current=MagicMock(side_effect=ModbusSlaveOfflineError("Modbus slave 1 did not respond"))
        )
        service = SimpleNamespace(
            _charger_backend=charger_backend,
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            _delay_source_retry=MagicMock(),
            virtual_set_current=11.0,
            min_current=6.0,
            max_current=16.0,
            learned_charge_power_state="unknown",
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            phase="L1",
            voltage_mode="phase",
            auto_dbus_backoff_base_seconds=5.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            _charger_target_current_amps=None,
            _charger_target_current_applied_at=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        applied = controller.apply_charger_current_target(service, True, 100.0, True)

        self.assertIsNone(applied)
        service._mark_failure.assert_called_once_with("charger")
        service._warning_throttled.assert_called_once()
        self.assertEqual(service._last_charger_transport_reason, "offline")
        self.assertEqual(service._charger_retry_reason, "offline")
        self.assertEqual(service._charger_retry_source, "current")
        self.assertEqual(service._charger_retry_until, 120.0)
        service._delay_source_retry.assert_called_once_with("charger", 100.0, 20.0)

    def test_apply_charger_current_target_skips_write_while_retry_backoff_is_active(self):
        charger_backend = SimpleNamespace(set_current=MagicMock())
        service = SimpleNamespace(
            _charger_backend=charger_backend,
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            virtual_set_current=11.0,
            min_current=6.0,
            max_current=16.0,
            learned_charge_power_state="unknown",
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            phase="L1",
            voltage_mode="phase",
            auto_learn_charge_power_max_age_seconds=21600.0,
            _charger_target_current_amps=9.0,
            _charger_target_current_applied_at=95.0,
            _charger_retry_reason="offline",
            _charger_retry_source="current",
            _charger_retry_until=105.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        applied = controller.apply_charger_current_target(service, True, 100.0, True)

        self.assertEqual(applied, 9.0)
        charger_backend.set_current.assert_not_called()

    def test_charger_current_scenario_offline_backoff_then_retry_after_window(self):
        charger_backend = SimpleNamespace(
            set_current=MagicMock(
                side_effect=[
                    ModbusSlaveOfflineError("Modbus slave 1 did not respond"),
                    None,
                ]
            )
        )
        service = SimpleNamespace(
            _charger_backend=charger_backend,
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            _delay_source_retry=MagicMock(),
            virtual_set_current=11.0,
            min_current=6.0,
            max_current=16.0,
            learned_charge_power_state="unknown",
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_phase="L1",
            learned_charge_power_voltage=230.0,
            phase="L1",
            voltage_mode="phase",
            auto_dbus_backoff_base_seconds=5.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            _charger_target_current_amps=None,
            _charger_target_current_applied_at=None,
            _charger_retry_reason=None,
            _charger_retry_source=None,
            _charger_retry_until=None,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        first = controller.apply_charger_current_target(service, True, 100.0, True)
        second = controller.apply_charger_current_target(service, True, 105.0, True)
        third = controller.apply_charger_current_target(service, True, 121.0, True)

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(third, 11.0)
        self.assertEqual(charger_backend.set_current.call_count, 2)
        charger_backend.set_current.assert_called_with(11.0)
        self.assertEqual(service._charger_target_current_amps, 11.0)
        self.assertEqual(service._charger_target_current_applied_at, 121.0)
        self.assertIsNone(service._charger_retry_reason)
        self.assertIsNone(service._charger_retry_source)
        self.assertIsNone(service._charger_retry_until)

    def test_apply_relay_decision_marks_charger_failure_when_native_backend_raises(self):
        charger_backend = SimpleNamespace(set_enabled=MagicMock(side_effect=RuntimeError("boom")))
        service = SimpleNamespace(
            _last_auto_metrics={"surplus": None, "grid": None, "soc": None},
            _last_health_reason="init",
            auto_audit_log=False,
            auto_shelly_soft_fail_seconds=10.0,
            _charger_backend=charger_backend,
            _queue_relay_command=MagicMock(),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=False,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed = controller.apply_relay_decision(
            True,
            False,
            {"output": False, "_pm_confirmed": True},
            1200.0,
            5.2,
            100.0,
            False,
        )
        self.assertEqual((relay_on, power, current, confirmed), (False, 1200.0, 5.2, True))
        service._mark_failure.assert_called_once_with("charger")
        service._warning_throttled.assert_called_once()
        service._queue_relay_command.assert_not_called()

    def test_apply_relay_decision_skips_native_enable_while_charger_retry_is_active(self):
        charger_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            _last_auto_metrics={"surplus": None, "grid": None, "soc": None},
            _last_health_reason="init",
            auto_audit_log=False,
            auto_shelly_soft_fail_seconds=10.0,
            _charger_backend=charger_backend,
            _queue_relay_command=MagicMock(),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=False,
            _charger_retry_reason="offline",
            _charger_retry_source="enable",
            _charger_retry_until=105.0,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed = controller.apply_relay_decision(
            True,
            False,
            {"output": False, "_pm_confirmed": True},
            1200.0,
            5.2,
            100.0,
            False,
        )

        self.assertEqual((relay_on, power, current, confirmed), (False, 1200.0, 5.2, True))
        charger_backend.set_enabled.assert_not_called()
        service._queue_relay_command.assert_not_called()
        service._mark_failure.assert_not_called()
        service._warning_throttled.assert_not_called()

    def test_apply_relay_decision_does_not_requeue_same_target_while_confirmation_is_pending(self):
        service = SimpleNamespace(
            _last_auto_metrics={"surplus": None, "grid": None, "soc": None},
            auto_audit_log=False,
            auto_shelly_soft_fail_seconds=10.0,
            _queue_relay_command=MagicMock(),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _relay_sync_expected_state=True,
            _relay_sync_requested_at=90.0,
            _relay_sync_deadline_at=95.0,
            _relay_sync_failure_reported=False,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed = controller.apply_relay_decision(
            True,
            False,
            {"output": False, "_pm_confirmed": True},
            1200.0,
            5.2,
            100.0,
            False,
        )

        self.assertEqual((relay_on, power, current, confirmed), (False, 1200.0, 5.2, True))

    def test_apply_relay_decision_keeps_in_flight_transition_when_placeholder_publish_fails(self):
        service = SimpleNamespace(
            _last_auto_metrics={"surplus": None, "grid": None, "soc": None},
            _last_health_reason="init",
            auto_audit_log=False,
            auto_shelly_soft_fail_seconds=10.0,
            relay_sync_timeout_seconds=2.0,
            _queue_relay_command=MagicMock(),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            _publish_local_pm_status=MagicMock(side_effect=RuntimeError("publish failed")),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        relay_on, power, current, confirmed = controller.apply_relay_decision(
            True,
            False,
            {"output": False, "apower": 0.0, "current": 0.0, "_pm_confirmed": True},
            0.0,
            0.0,
            100.0,
            False,
        )

        self.assertEqual((relay_on, power, current, confirmed), (True, 0.0, 0.0, False))
        service._queue_relay_command.assert_called_once_with(True, 100.0)
        service._warning_throttled.assert_called_once()

    def test_relay_sync_health_override_reports_mismatch_and_clears_tracking_after_timeout(self):
        service = SimpleNamespace(
            _relay_sync_expected_state=True,
            _relay_sync_requested_at=100.0,
            _relay_sync_deadline_at=104.0,
            _relay_sync_failure_reported=False,
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertIsNone(controller.relay_sync_health_override(True, False, 102.0))
        self.assertFalse(service._relay_sync_failure_reported)
        self.assertEqual(controller.relay_sync_health_override(False, True, 102.0), "command-mismatch")
        self.assertFalse(service._relay_sync_failure_reported)
        service._mark_failure.assert_not_called()

        self.assertEqual(controller.relay_sync_health_override(False, False, 105.0), "relay-sync-failed")
        service._mark_failure.assert_called_once_with("shelly")
        service._warning_throttled.assert_called_once()
        self.assertIsNone(service._relay_sync_expected_state)
        self.assertIsNone(service._relay_sync_requested_at)
        self.assertIsNone(service._relay_sync_deadline_at)
        self.assertFalse(service._relay_sync_failure_reported)

        service._mark_failure.reset_mock()
        service._warning_throttled.reset_mock()
        self.assertIsNone(controller.relay_sync_health_override(False, False, 106.0))
        service._mark_failure.assert_not_called()
        service._warning_throttled.assert_not_called()

        service._queue_relay_command = MagicMock()
        service._publish_local_pm_status = MagicMock()
        relay_on, power, current, confirmed = controller.apply_relay_decision(
            True,
            False,
            {"output": False, "_pm_confirmed": True},
            1200.0,
            5.2,
            106.5,
            False,
        )
        self.assertEqual((relay_on, power, current, confirmed), (True, 0.0, 0.0, False))
        service._queue_relay_command.assert_called_once_with(True, 106.5)
        service._publish_local_pm_status.assert_called_once_with(True, 106.5)
        service._mark_recovery.assert_not_called()

    def test_relay_sync_health_override_marks_recovery_after_confirmed_match(self):
        service = SimpleNamespace(
            _relay_sync_expected_state=True,
            _relay_sync_requested_at=100.0,
            _relay_sync_deadline_at=104.0,
            _relay_sync_failure_reported=True,
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertIsNone(controller.relay_sync_health_override(True, True, 103.0))

        service._mark_recovery.assert_called_once_with("shelly", "Shelly relay confirmation recovered")
        self.assertIsNone(service._relay_sync_expected_state)
        self.assertIsNone(service._relay_sync_requested_at)
        self.assertIsNone(service._relay_sync_deadline_at)
        self.assertFalse(service._relay_sync_failure_reported)

    def test_update_overrides_health_when_relay_confirmation_times_out(self):
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=105.0),
            _state_summary=lambda: "state",
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(
                return_value={
                    "pm_status": {
                        "output": False,
                        "apower": 0.0,
                        "voltage": 230.0,
                        "current": 0.0,
                        "aenergy": {"total": 1.0},
                    },
                    "pm_confirmed": True,
                }
            ),
            _last_pm_status=None,
            _last_pm_status_at=None,
            _last_pm_status_confirmed=False,
            _safe_float=lambda value, default=0.0: float(value) if value is not None else default,
            virtual_mode=1,
            phase="L1",
            voltage_mode="phase",
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _auto_decide_relay=MagicMock(return_value=True),
            _publish_live_measurements=MagicMock(return_value=False),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _set_health=MagicMock(),
            _last_health_reason="waiting",
            _last_health_code=0,
            charging_started_at=None,
            energy_at_start=0.0,
            last_status=0,
            virtual_enable=1,
            _dbusservice={"/Ac/Power": 0.0},
            service_name="com.victronenergy.evcharger.http_60",
            last_update=0.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _last_voltage=230.0,
            virtual_startstop=0,
            charging_threshold_watts=100.0,
            idle_status=1,
            _last_successful_update_at=None,
            _last_recovery_attempt_at=None,
            _error_state={"cache_hits": 0},
            _last_pv_value=None,
            _last_pv_at=None,
            _last_grid_value=None,
            _last_grid_at=None,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
            auto_input_cache_seconds=0.0,
            auto_shelly_soft_fail_seconds=10.0,
            auto_audit_log=False,
            _last_auto_metrics={"surplus": None, "grid": None, "soc": None},
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_state="unknown",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            learned_charge_power_phase=None,
            learned_charge_power_voltage=None,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=None,
            auto_learn_charge_power_enabled=False,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            max_current=16.0,
            _relay_sync_expected_state=True,
            _relay_sync_requested_at=100.0,
            _relay_sync_deadline_at=104.0,
            _relay_sync_failure_reported=False,
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _bump_update_index=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0, "relay-sync-failed": 25}.get(reason, 99))

        self.assertTrue(controller.update())

        service._set_health.assert_any_call("relay-sync-failed", cached=False)
        service._mark_failure.assert_called_once_with("shelly")
        service._warning_throttled.assert_called_once()

    def test_update_forces_charger_off_when_native_charger_fault_is_fresh(self):
        charger_backend = SimpleNamespace(
            set_enabled=MagicMock(),
            set_current=MagicMock(),
        )
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=105.0),
            _state_summary=lambda: "state",
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(
                return_value={
                    "pm_status": {
                        "output": True,
                        "apower": 1200.0,
                        "voltage": 230.0,
                        "current": 5.2,
                        "aenergy": {"total": 1.0},
                    },
                    "pm_confirmed": True,
                    "pm_captured_at": 105.0,
                }
            ),
            _last_pm_status=None,
            _last_pm_status_at=None,
            _last_pm_status_confirmed=False,
            _safe_float=lambda value, default=0.0: float(value) if value is not None else default,
            virtual_mode=1,
            phase="L1",
            voltage_mode="phase",
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _auto_decide_relay=MagicMock(return_value=True),
            _charger_backend=charger_backend,
            _last_charger_state_status="fault",
            _last_charger_state_fault="overcurrent error",
            _last_charger_state_at=105.0,
            _publish_live_measurements=MagicMock(return_value=False),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _set_health=MagicMock(),
            _last_health_reason="running",
            _last_health_code=5,
            charging_started_at=None,
            energy_at_start=0.0,
            last_status=0,
            virtual_enable=1,
            virtual_set_current=11.0,
            _dbusservice={"/Ac/Power": 0.0},
            service_name="com.victronenergy.evcharger.http_60",
            last_update=0.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _last_voltage=230.0,
            virtual_startstop=0,
            charging_threshold_watts=100.0,
            idle_status=1,
            _last_successful_update_at=None,
            _last_recovery_attempt_at=None,
            _error_state={"cache_hits": 0},
            _last_pv_value=None,
            _last_pv_at=None,
            _last_grid_value=None,
            _last_grid_at=None,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
            auto_input_cache_seconds=0.0,
            auto_shelly_soft_fail_seconds=10.0,
            auto_audit_log=False,
            _last_auto_metrics={"surplus": None, "grid": None, "soc": None},
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_state="unknown",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            learned_charge_power_phase=None,
            learned_charge_power_voltage=None,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=None,
            auto_learn_charge_power_enabled=False,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            min_current=6.0,
            max_current=16.0,
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=False,
            _charger_target_current_amps=None,
            _charger_target_current_applied_at=None,
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            _warning_throttled=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _bump_update_index=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0, "charger-fault": 26}.get(reason, 99))

        self.assertTrue(controller.update())

        charger_backend.set_current.assert_not_called()
        charger_backend.set_enabled.assert_called_once_with(False)
        service._set_health.assert_any_call("charger-fault", cached=False)
        service._warning_throttled.assert_called_once()
        self.assertEqual(service.last_status, 0)
        self.assertEqual(service._last_status_source, "charger-fault")
        self.assertEqual(service._last_charger_fault_active, 1)

    def test_update_saves_runtime_state_when_charge_power_learning_updates(self):
        service = SimpleNamespace(
            _time_now=MagicMock(return_value=100.0),
            _state_summary=lambda: "state",
            _watchdog_recover=MagicMock(),
            _ensure_auto_input_helper_process=MagicMock(),
            _refresh_auto_input_snapshot=MagicMock(),
            _get_worker_snapshot=MagicMock(
                return_value={
                    "pm_status": {"output": True, "apower": 1900.0, "voltage": 230.0, "current": 8.3, "aenergy": {"total": 1.0}},
                    "pm_confirmed": True,
                    "pm_captured_at": 100.0,
                }
            ),
            _last_pm_status=None,
            _last_pm_status_at=None,
            _last_pm_status_confirmed=False,
            _safe_float=lambda value, default=0.0: float(value) if value is not None else default,
            virtual_mode=1,
            phase="L1",
            voltage_mode="phase",
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            _auto_decide_relay=MagicMock(return_value=True),
            _publish_live_measurements=MagicMock(return_value=False),
            _publish_energy_time_measurements=MagicMock(return_value=False),
            _publish_config_paths=MagicMock(return_value=False),
            _publish_diagnostic_paths=MagicMock(return_value=False),
            _save_runtime_state=MagicMock(),
            _ensure_observability_state=MagicMock(),
            _last_health_reason="init",
            _last_health_code=0,
            charging_started_at=50.0,
            energy_at_start=0.0,
            last_status=0,
            virtual_enable=1,
            _dbusservice={"/Ac/Power": 0.0},
            service_name="com.victronenergy.evcharger.http_60",
            last_update=0.0,
            _dbus_publish_state={},
            _dbus_live_publish_interval_seconds=1.0,
            _dbus_slow_publish_interval_seconds=5.0,
            _last_voltage=230.0,
            virtual_startstop=1,
            charging_threshold_watts=100.0,
            idle_status=1,
            _last_successful_update_at=None,
            _last_recovery_attempt_at=None,
            _error_state={"cache_hits": 0},
            _last_pv_value=None,
            _last_pv_at=None,
            _last_grid_value=None,
            _last_grid_at=None,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
            auto_input_cache_seconds=0.0,
            auto_learn_charge_power_enabled=True,
            auto_learn_charge_power_start_delay_seconds=30.0,
            auto_learn_charge_power_window_seconds=180.0,
            auto_learn_charge_power_max_age_seconds=21600.0,
            auto_learn_charge_power_min_watts=500.0,
            auto_learn_charge_power_alpha=0.2,
            learned_charge_power_watts=None,
            learned_charge_power_updated_at=None,
            learned_charge_power_state="unknown",
            learned_charge_power_learning_since=None,
            learned_charge_power_sample_count=0,
            learned_charge_power_phase=None,
            learned_charge_power_voltage=None,
            learned_charge_power_signature_mismatch_sessions=0,
            learned_charge_power_signature_checked_session_started_at=None,
            _bump_update_index=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertTrue(controller.update())

        self.assertGreaterEqual(service._save_runtime_state.call_count, 1)
        self.assertEqual(service.learned_charge_power_watts, 1900.0)
        self.assertEqual(service.learned_charge_power_updated_at, 100.0)
