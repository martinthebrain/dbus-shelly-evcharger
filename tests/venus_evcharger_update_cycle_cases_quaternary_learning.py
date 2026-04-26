# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_controller_support import *


class _UpdateCycleQuaternaryLearningCases:
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
