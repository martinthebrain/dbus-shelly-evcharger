# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class TestUpdateCycleControllerQuaternary(UpdateCycleControllerTestBase):
    def test_update_cycle_helpers_cover_offline_inputs_and_relay_resolution_edges(self) -> None:
        service = SimpleNamespace(
            _last_confirmed_pm_status="bad",
            _last_confirmed_pm_status_at=100.0,
            relay_sync_timeout_seconds=3.0,
            virtual_mode=1,
            _auto_cached_inputs_used=True,
            _auto_decide_relay=MagicMock(return_value=True),
            _bump_update_index=MagicMock(),
            _time_now=MagicMock(return_value=123.0),
            _last_successful_update_at=None,
            _last_recovery_attempt_at=1.0,
            last_update=0.0,
            _warning_throttled=MagicMock(),
            auto_shelly_soft_fail_seconds=10.0,
            _last_charger_transport_source="source",
            _last_charger_transport_detail="detail",
            _last_charger_state_status="charging",
            _last_charger_state_fault=None,
            _last_switch_feedback_closed=True,
            _contactor_fault_counts={},
            _contactor_lockout_source="",
            _publish_companion_dbus_bridge=MagicMock(),
        )
        controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

        self.assertIsNone(controller._fresh_offline_pm_status(service, 101.0))
        self.assertEqual(controller._offline_power_state(), (0.0, 0.0, 0))
        self.assertEqual(controller.resolve_auto_inputs({}, 100.0, False), (None, None, None))
        self.assertFalse(service._auto_cached_inputs_used)

        controller.complete_update_cycle(service, False, 200.0, False, 0.0, 0.0, 0, None, None, None)
        service._bump_update_index.assert_not_called()
        self.assertEqual(service._last_successful_update_at, 123.0)

        controller.complete_update_cycle(service, True, 201.0, False, 0.0, 0.0, 0, None, None, None)
        service._bump_update_index.assert_called_once_with(201.0)
        self.assertEqual(service._publish_companion_dbus_bridge.call_count, 2)
        service._publish_companion_dbus_bridge.assert_called_with(123.0)

        with patch.object(controller, "orchestrate_pending_phase_switch", return_value=(True, 2300.0, 10.0, True, None)), patch.object(
            controller,
            "_blocking_switch_feedback_health",
            return_value="switch-feedback-mismatch",
        ), patch.object(controller, "_blocking_charger_health", return_value=None), patch.object(
            controller,
            "maybe_apply_auto_phase_selection",
            return_value=True,
        ), patch.object(controller, "apply_charger_current_target") as apply_target:
            result = controller._resolved_relay_decision({}, True, 2300.0, 230.0, 10.0, True, 100.0, True, 5000.0, 50.0, -1000.0)

        self.assertEqual(result, (True, 2300.0, 10.0, True, True, "switch-feedback-mismatch"))
        apply_target.assert_called_once_with(service, True, 100.0, True)

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

    def test_software_update_run_requires_restart_script(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / "install.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            service = self._software_update_service(temp_dir, _software_update_run_requested_at=50.0)
            controller = UpdateCycleController(service, _phase_values, lambda reason: {"init": 0}.get(reason, 99))

            started = controller._start_software_update_run(service, 120.0, "manual")

            self.assertFalse(started)
            self.assertEqual(service._software_update_state, "update-unavailable")
            self.assertEqual(service._software_update_detail, "restart script missing")
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
