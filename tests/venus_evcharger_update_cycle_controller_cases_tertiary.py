# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class TestUpdateCycleControllerTertiary(UpdateCycleControllerTestBase):
    def test_phase_switch_waiting_and_stabilizing_helpers_cover_remaining_branches(self):
        service = _auto_phase_service(
            _phase_switch_pending_selection="P1_P2",
            _phase_switch_state="waiting-relay-off",
            _phase_switch_requested_at=98.0,
            _phase_switch_resume_relay=True,
            _save_runtime_state=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        service._peek_pending_relay_command.return_value = (True, 99.0)
        self.assertFalse(controller._phase_switch_waiting_ready(service, False, True, 100.0))
        self.assertEqual(
            controller._orchestrate_waiting_phase_switch(service, "P1_P2", False, 10.0, 2.0, True, 100.0, False),
            (False, 10.0, 2.0, True, False),
        )

        service._peek_pending_relay_command.return_value = (None, None)
        service._apply_phase_selection = MagicMock(side_effect=RuntimeError("apply failed"))
        result = controller._orchestrate_waiting_phase_switch(service, "P1_P2", False, 11.0, 3.0, True, 101.0, False)
        self.assertEqual(result[-1], None)
        self.assertEqual(service.requested_phase_selection, "P1")
        service._mark_failure.assert_called()
        service._warning_throttled.assert_called()

        service._phase_switch_state = "stabilizing"
        service._phase_switch_pending_selection = "P1_P2"
        service._phase_switch_stable_until = 120.0
        self.assertEqual(
            controller._orchestrate_stabilizing_phase_switch(service, "P1_P2", {}, False, 0.0, 0.0, False, 110.0, False),
            (False, 0.0, 0.0, False, False),
        )

        service._phase_switch_stable_until = 100.0
        service._phase_switch_lockout_selection = "P1_P2"
        with patch.object(controller, "_resume_after_phase_switch_pause", return_value=(True, 0.0, 0.0, False)) as resume_mock:
            result = controller._orchestrate_stabilizing_phase_switch(
                service,
                "P1_P2",
                {"_phase_selection": "P1_P2"},
                False,
                0.0,
                0.0,
                False,
                121.0,
                False,
            )
        self.assertEqual(result, (True, 0.0, 0.0, False, None))
        resume_mock.assert_called_once()
        self.assertIsNone(service._phase_switch_lockout_selection)

    def test_relay_decision_failure_records_charger_transport_retry(self):
        service = SimpleNamespace(
            _charger_backend=SimpleNamespace(set_enabled=MagicMock()),
            _last_charger_transport_reason=None,
            _last_charger_transport_source=None,
            _last_charger_transport_detail=None,
            _last_charger_transport_at=None,
            _charger_retry_reason=None,
            _charger_retry_source=None,
            _source_retry_after={},
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        UpdateCycleController._handle_relay_decision_failure(service, ModbusSlaveOfflineError("offline"))

        self.assertEqual(service._last_charger_transport_reason, "offline")
        self.assertEqual(service._last_charger_transport_source, "enable")
        self.assertEqual(service._charger_retry_reason, "offline")
        self.assertEqual(service._charger_retry_source, "enable")
        self.assertIn("charger", service._source_retry_after)

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

            with patch("venus_evcharger.update.controller.requests.get", return_value=response) as mock_get:
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

    def test_software_update_helper_methods_cover_text_and_state_branches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bootstrap_state = Path(temp_dir) / ".bootstrap-state"
            bootstrap_state.mkdir(parents=True, exist_ok=True)
            (bootstrap_state / "installed_version").write_text("2.0.0\nextra\n", encoding="utf-8")
            (bootstrap_state / "installed_bundle_sha256").write_text("abc123  payload\n", encoding="utf-8")
            service = self._software_update_service(temp_dir)

            self.assertEqual(UpdateCycleController._read_text_file(""), "")
            self.assertEqual(UpdateCycleController._read_text_file(Path(temp_dir) / "missing.txt"), "")
            self.assertEqual(UpdateCycleController._local_software_update_version(service), "2.0.0")
            self.assertEqual(UpdateCycleController._local_installed_bundle_hash(service), "abc123")

            service._software_update_available = False
            service._software_update_last_check_at = None
            self.assertEqual(UpdateCycleController._software_update_state_for_no_update_block(service), "idle")
            service._software_update_last_check_at = 100.0
            self.assertEqual(UpdateCycleController._software_update_state_for_no_update_block(service), "up-to-date")
            service._software_update_available = True
            self.assertEqual(UpdateCycleController._software_update_state_for_no_update_block(service), "available-blocked")

            UpdateCycleController._set_software_update_state(
                service,
                "available",
                detail="detail",
                available=True,
                available_version="2.0.1",
                last_result="success",
            )
            self.assertEqual(service._software_update_state, "available")
            self.assertEqual(service._software_update_detail, "detail")
            self.assertTrue(service._software_update_available)
            self.assertEqual(service._software_update_available_version, "2.0.1")
            self.assertEqual(service._software_update_last_result, "success")

    def test_software_update_check_covers_version_source_and_failure_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "version.txt").write_text("1.2.3\n", encoding="utf-8")
            service = self._software_update_service(
                temp_dir,
                software_update_manifest_source="",
                software_update_version_source="https://example.invalid/version.txt",
            )
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
            response = MagicMock()
            response.raise_for_status.return_value = None
            response.text = "1.2.4\n"

            with patch("venus_evcharger.update.controller.requests.get", return_value=response):
                controller._run_software_update_check(service, 100.0)

            self.assertEqual(service._software_update_state, "available")
            self.assertEqual(service._software_update_available_version, "1.2.4")
            self.assertEqual(service._software_update_detail, "version-file")

            with patch("venus_evcharger.update.controller.requests.get", side_effect=RuntimeError("network down")):
                controller._run_software_update_check(service, 120.0)

            self.assertEqual(service._software_update_state, "check-failed")
            self.assertEqual(service._software_update_detail, "network down")
            self.assertFalse(service._software_update_available)

    def test_software_update_check_uses_manifest_version_without_bundle_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "version.txt").write_text("1.2.3\n", encoding="utf-8")
            service = self._software_update_service(temp_dir)
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))
            response = MagicMock()
            response.raise_for_status.return_value = None
            response.json.return_value = {"version": "1.2.4"}

            with patch("venus_evcharger.update.controller.requests.get", return_value=response):
                controller._run_software_update_check(service, 100.0)

            self.assertTrue(service._software_update_available)
            self.assertEqual(service._software_update_available_version, "1.2.4")

    def test_software_update_manifest_and_log_handle_helpers_cover_remaining_edges(self) -> None:
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = ["not-a-dict"]

        with patch("venus_evcharger.update.controller.requests.get", return_value=response):
            self.assertEqual(
                UpdateCycleController._software_update_manifest_result(
                    "https://example.invalid/bootstrap_manifest.json",
                    "1.2.3",
                    "",
                ),
                ("", False, ""),
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            relative_log_path = str(Path(temp_dir) / "software-update.log")
            log_handle = UpdateCycleController._software_update_log_handle(relative_log_path)
            log_handle.close()

        UpdateCycleController._close_open_log_handle(None)
        service = self._software_update_service("/tmp")
        service._software_update_process_log_handle = None
        UpdateCycleController._close_software_update_log_handle(service)
        with patch.object(UpdateCycleController, "_software_update_no_update_active", return_value=True):
            self.assertEqual(
                UpdateCycleController._software_update_availability_state(service, True),
                "available-blocked",
            )

    def test_software_update_run_and_poll_cover_process_lifecycle_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "install.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            restart_dir = repo_root / "deploy" / "venus"
            restart_dir.mkdir(parents=True, exist_ok=True)
            (restart_dir / "restart_venus_evcharger_service.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            service = self._software_update_service(temp_dir)
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

            active_process = MagicMock()
            service._software_update_process = active_process
            self.assertFalse(controller._start_software_update_run(service, 100.0, "manual"))
            self.assertIsNone(service._software_update_run_requested_at)

            service._software_update_process = None
            service.software_update_install_script = str(repo_root / "missing-install.sh")
            self.assertFalse(controller._start_software_update_run(service, 100.0, "manual"))
            self.assertEqual(service._software_update_state, "update-unavailable")
            self.assertEqual(service._software_update_detail, "install.sh missing")

            service.software_update_install_script = str(repo_root / "install.sh")
            fake_process = MagicMock()
            with patch("venus_evcharger.update.controller.subprocess.Popen", return_value=fake_process) as popen_mock:
                self.assertTrue(controller._start_software_update_run(service, 130.0, "manual"))

            popen_mock.assert_called_once()
            self.assertIs(service._software_update_process, fake_process)
            self.assertEqual(service._software_update_state, "running")
            self.assertEqual(service._software_update_detail, "manual")
            self.assertEqual(service._software_update_last_run_at, 130.0)
            log_handle = service._software_update_process_log_handle

            service._software_update_process = fake_process
            fake_process.poll.return_value = None
            controller._poll_software_update_process(service)
            self.assertIs(service._software_update_process, fake_process)

            fake_process.poll.return_value = 0
            controller._poll_software_update_process(service)
            self.assertIsNone(service._software_update_process)
            self.assertEqual(service._software_update_state, "installed")

            failing_process = MagicMock()
            failing_process.poll.return_value = 9
            failing_log = MagicMock()
            service._software_update_process = failing_process
            service._software_update_process_log_handle = failing_log
            controller._poll_software_update_process(service)
            failing_log.close.assert_called_once_with()
            self.assertEqual(service._software_update_state, "install-failed")
            self.assertEqual(service._software_update_detail, "exit 9")
            if log_handle is not None and hasattr(log_handle, "close"):
                log_handle.close()

    def test_software_update_run_and_housekeeping_cover_failure_and_due_check_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "install.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            restart_dir = repo_root / "deploy" / "venus"
            restart_dir.mkdir(parents=True, exist_ok=True)
            (restart_dir / "restart_venus_evcharger_service.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            service = self._software_update_service(temp_dir, _software_update_next_check_at=100.0)
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

            with patch("venus_evcharger.update.controller.subprocess.Popen", side_effect=RuntimeError("spawn failed")):
                self.assertFalse(controller._start_software_update_run(service, 120.0, "manual"))

            self.assertEqual(service._software_update_state, "install-failed")
            self.assertEqual(service._software_update_detail, "spawn failed")

            with patch.object(UpdateCycleController, "_run_software_update_check") as check_mock:
                controller._software_update_housekeeping(service, 120.0)
            check_mock.assert_called_once_with(service, 120.0)

            process = MagicMock()
            process.poll.return_value = 1
            failing_log = MagicMock()
            failing_log.close.side_effect = OSError("close failed")
            service._software_update_process = process
            service._software_update_process_log_handle = failing_log
            controller._poll_software_update_process(service)
            self.assertEqual(service._software_update_state, "install-failed")

    def test_software_update_run_failure_tolerates_log_close_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "install.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            restart_dir = repo_root / "deploy" / "venus"
            restart_dir.mkdir(parents=True, exist_ok=True)
            (restart_dir / "restart_venus_evcharger_service.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            service = self._software_update_service(temp_dir)
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

            fake_log = MagicMock()
            fake_log.close.side_effect = OSError("close failed")
            with patch("builtins.open", return_value=fake_log), patch(
                "venus_evcharger.update.controller.subprocess.Popen",
                side_effect=RuntimeError("spawn failed"),
            ):
                self.assertFalse(controller._start_software_update_run(service, 120.0, "manual"))

            self.assertEqual(service._software_update_state, "install-failed")

    def test_update_cycle_health_helpers_cover_blocking_reason_variants(self) -> None:
        service = SimpleNamespace(
            auto_shelly_soft_fail_seconds=10.0,
            _warning_throttled=MagicMock(),
            _last_charger_transport_source="charger",
            _last_charger_transport_detail="timeout",
            _last_charger_state_status="charging",
            _last_charger_state_fault="fault",
            _last_switch_interlock_ok=False,
            _contactor_fault_counts={
                "contactor-suspected-open": 2,
                "contactor-suspected-welded": 3,
            },
            _contactor_lockout_source="feedback",
            _last_switch_feedback_closed=True,
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        with patch.object(controller, "charger_health_override", return_value="charger-transport-timeout"):
            self.assertEqual(controller._blocking_charger_health(True, False, 100.0), "charger-transport-timeout")
        with patch.object(controller, "charger_health_override", return_value="charger-fault"):
            self.assertEqual(controller._blocking_charger_health(True, False, 100.0), "charger-fault")
        with patch.object(controller, "charger_health_override", return_value=None):
            self.assertIsNone(controller._blocking_charger_health(True, False, 100.0))

        for reason in (
            "contactor-interlock",
            "contactor-suspected-open",
            "contactor-suspected-welded",
            "contactor-lockout-open",
            "contactor-lockout-welded",
            "switch-feedback-mismatch",
        ):
            with self.subTest(reason=reason):
                with patch.object(controller, "switch_feedback_health_override", return_value=reason):
                    self.assertEqual(
                        controller._blocking_switch_feedback_health(True, True, 2300.0, 10.0, True, 100.0),
                        reason,
                    )

        with patch.object(controller, "switch_feedback_health_override", return_value=None):
            self.assertIsNone(controller._blocking_switch_feedback_health(True, True, 2300.0, 10.0, True, 100.0))

        self.assertTrue(controller._desired_relay_target(service, False, True, None, None, None))
        service._auto_decide_relay = MagicMock(return_value=False)
        self.assertFalse(controller._desired_relay_target(service, True, None, None, None, None))
