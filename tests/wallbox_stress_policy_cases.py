# SPDX-License-Identifier: GPL-3.0-or-later
from unittest.mock import MagicMock, patch

from shelly_wallbox.inputs.supervisor import AutoInputSupervisor
from tests.wallbox_stress_support import (
    AutoPolicy,
    AutoStopEwmaPolicy,
    AutoThresholdProfile,
    StressTestCaseBase,
    validate_auto_policy,
)


class TestShellyWallboxStressPolicy(StressTestCaseBase):
    def test_auto_controller_survives_inconsistent_dbus_value_sequences(self):
        controller, service = self._make_auto_controller()
        service.auto_start_delay_seconds = 0.0
        service.auto_startup_warmup_seconds = 0.0
        service.started_at = 0.0

        scenarios = [
            {"relay_on": False, "pv": 2200.0, "soc": 55.0, "grid": -2100.0, "grid_at": 100.0},
            {"relay_on": False, "pv": None, "soc": 55.0, "grid": -2100.0, "grid_at": 101.0},
            {"relay_on": False, "pv": 0.0, "soc": 55.0, "grid": -2500.0, "grid_at": 102.0},
            {"relay_on": False, "pv": 2300.0, "soc": None, "grid": -2100.0, "grid_at": 103.0},
            {"relay_on": False, "pv": 2300.0, "soc": 55.0, "grid": None, "grid_at": 0.0},
            {"relay_on": True, "pv": 300.0, "soc": 44.0, "grid": 350.0, "grid_at": 105.0},
            {"relay_on": True, "pv": None, "soc": 55.0, "grid": 50.0, "grid_at": 80.0},
        ]

        results = []
        health_reasons = []
        current_time = [100.0]

        for scenario in scenarios:
            service._last_grid_at = scenario["grid_at"]
            current_time[0] += 1.0
            with patch("shelly_wallbox.auto.workflow.time.time", return_value=current_time[0]):
                result = controller.auto_decide_relay(
                    scenario["relay_on"],
                    scenario["pv"],
                    scenario["soc"],
                    scenario["grid"],
                )
            results.append(result)
            health_reasons.append(service._last_health_reason)

        self.assertTrue(all(isinstance(result, bool) for result in results))
        self.assertIn("grid-missing", health_reasons)
        self.assertTrue(
            set(health_reasons).issubset(
                {
                    "init",
                    "running",
                    "waiting-surplus",
                    "battery-soc-missing",
                    "grid-missing",
                    "inputs-missing",
                    "auto-start",
                    "waiting-grid-recovery",
                    "auto-stop",
                }
            )
        )

    def test_helper_supervisor_survives_process_state_flapping(self):
        current = [100.0]
        process = MagicMock()
        process.pid = 4321
        process.poll.side_effect = [None, None, 1]
        service = type(
            "Service",
            (),
            {
                "_ensure_worker_state": MagicMock(),
                "_time_now": lambda _self: current[0],
                "_stop_auto_input_helper": MagicMock(side_effect=lambda force=False: None),
                "_spawn_auto_input_helper": None,
                "_warning_throttled": MagicMock(),
                "_auto_input_helper_process": process,
                "_auto_input_helper_last_start_at": 80.0,
                "_auto_input_helper_restart_requested_at": None,
                "_auto_input_snapshot_last_seen": 60.0,
                "auto_input_helper_stale_seconds": 15.0,
                "auto_input_helper_restart_seconds": 5.0,
            },
        )()
        service._spawn_auto_input_helper = MagicMock(
            side_effect=lambda now=None: setattr(service, "_auto_input_helper_process", process)
        )
        controller = AutoInputSupervisor(service)

        controller.ensure_helper_process(now=current[0])
        self.assertEqual(service._stop_auto_input_helper.call_count, 1)
        self.assertEqual(service._auto_input_helper_restart_requested_at, 100.0)

        current[0] = 101.0
        controller.ensure_helper_process(now=current[0])
        self.assertEqual(service._stop_auto_input_helper.call_count, 1)

        current[0] = 106.0
        controller.ensure_helper_process(now=current[0])
        self.assertEqual(service._spawn_auto_input_helper.call_count, 1)

    def test_auto_policy_matrix_clamps_and_remains_decidable(self):
        policy_variants = [
            AutoPolicy(
                normal_profile=AutoThresholdProfile(1850.0, 1350.0),
                high_soc_profile=AutoThresholdProfile(1650.0, 800.0),
                high_soc_threshold=50.0,
                high_soc_release_threshold=45.0,
                min_soc=40.0,
                resume_soc=50.0,
                start_max_grid_import_watts=50.0,
                stop_grid_import_watts=300.0,
                grid_recovery_start_seconds=0.0,
                stop_surplus_delay_seconds=0.0,
                ewma=AutoStopEwmaPolicy(0.35, 0.55, 0.15, 150.0, 400.0),
            ),
            AutoPolicy(
                normal_profile=AutoThresholdProfile(1600.0, 1600.0),
                high_soc_profile=AutoThresholdProfile(1200.0, 1200.0),
                high_soc_threshold=55.0,
                high_soc_release_threshold=55.0,
                min_soc=45.0,
                resume_soc=45.0,
                start_max_grid_import_watts=0.0,
                stop_grid_import_watts=0.0,
                grid_recovery_start_seconds=30.0,
                stop_surplus_delay_seconds=120.0,
                ewma=AutoStopEwmaPolicy(1.0, 1.0, 0.05, 0.0, 0.0),
            ),
            AutoPolicy(
                normal_profile=AutoThresholdProfile(2000.0, 1500.0),
                high_soc_profile=AutoThresholdProfile(1700.0, 900.0),
                high_soc_threshold=80.0,
                high_soc_release_threshold=60.0,
                min_soc=20.0,
                resume_soc=25.0,
                start_max_grid_import_watts=150.0,
                stop_grid_import_watts=500.0,
                grid_recovery_start_seconds=5.0,
                stop_surplus_delay_seconds=10.0,
                ewma=AutoStopEwmaPolicy(0.2, 0.4, 0.1, 50.0, 250.0),
            ),
        ]

        for index, policy in enumerate(policy_variants):
            controller, service = self._make_auto_controller()
            validated_policy = validate_auto_policy(policy)
            validated_policy.apply_to_service(service)
            service.auto_start_delay_seconds = 0.0
            service.auto_startup_warmup_seconds = 0.0
            service.started_at = 0.0
            service._last_grid_at = 100.0
            with patch("shelly_wallbox.auto.workflow.time.time", return_value=101.0 + index):
                result = controller.auto_decide_relay(False, 2600.0, 65.0, -2200.0)
            self.assertIsInstance(result, bool)
            self.assertLessEqual(service.auto_stop_surplus_watts, service.auto_start_surplus_watts)
            self.assertLessEqual(service.auto_high_soc_stop_surplus_watts, service.auto_high_soc_start_surplus_watts)
