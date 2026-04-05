# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from dbus_shelly_wallbox_auto_controller import AutoDecisionController
from dbus_shelly_wallbox_auto_policy import AutoStopEwmaPolicy, AutoThresholdProfile, AutoPolicy, validate_auto_policy
from dbus_shelly_wallbox_auto_input_supervisor import AutoInputSupervisor
from dbus_shelly_wallbox_runtime_support import RuntimeSupportController
from dbus_shelly_wallbox_shared import write_text_atomically
from dbus_shelly_wallbox_shelly_io import ShellyIoController
from tests.wallbox_test_fixtures import make_auto_controller_service


class _SteppingClock:
    def __init__(self, start=0.0, step=0.001):
        self._value = float(start)
        self._step = float(step)
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            self._value += self._step
            return self._value


class _FastStopEvent:
    def __init__(self):
        self._flag = False

    def is_set(self):
        return self._flag

    def wait(self, _timeout):
        return self._flag

    def set(self):
        self._flag = True


class TestShellyWallboxStress(unittest.TestCase):
    @staticmethod
    def _stress_iters():
        return max(50, int(os.environ.get("SHELLY_STRESS_ITERS", "400")))

    @staticmethod
    def _stress_threads():
        return max(2, int(os.environ.get("SHELLY_STRESS_THREADS", "4")))

    @staticmethod
    def _make_auto_controller():
        def _health_code(reason):
            return {
                "grid-missing": 1,
                "inputs-missing": 2,
                "auto-start": 3,
                "battery-soc-missing": 4,
                "battery-soc-missing-allowed": 5,
                "waiting-grid": 6,
                "waiting": 7,
                "autostart-disabled": 8,
                "averaging": 9,
                "mode-transition": 10,
                "waiting-grid-recovery": 11,
                "waiting-surplus": 12,
                "waiting-soc": 13,
                "running": 14,
                "auto-stop": 15,
            }.get(reason, 99)

        def _mode_uses_auto_logic(mode):
            return int(mode) in (1, 2)

        service = make_auto_controller_service()
        controller = AutoDecisionController(service, _health_code, _mode_uses_auto_logic)
        service._clear_auto_samples = controller.clear_auto_samples
        service._set_health = controller.set_health
        service._get_available_surplus_watts = controller.get_available_surplus_watts
        service._add_auto_sample = controller.add_auto_sample
        service._average_auto_metric = controller.average_auto_metric
        service._is_within_auto_daytime_window = lambda: True
        return controller, service

    def test_runtime_support_worker_snapshot_survives_concurrent_access(self):
        service = SimpleNamespace(
            poll_interval_ms=1000,
            deviceinstance=60,
        )
        controller = RuntimeSupportController(service, lambda *_args, **_kwargs: 0, lambda *_args, **_kwargs: 0)
        service._ensure_worker_state = controller.ensure_worker_state
        controller.ensure_worker_state()

        start = threading.Event()
        errors = []
        error_lock = threading.Lock()
        iterations = self._stress_iters()
        thread_count = self._stress_threads()

        def _record_error(message):
            with error_lock:
                errors.append(message)

        def writer(writer_id):
            start.wait()
            try:
                for sequence in range(iterations):
                    controller.update_worker_snapshot(
                        captured_at=float(sequence),
                        pm_status={"writer": writer_id, "sequence": sequence},
                        pv_power=float(writer_id * iterations + sequence),
                    )
            except Exception as error:  # pylint: disable=broad-except
                _record_error(f"writer-{writer_id}: {error}")

        def reader(reader_id):
            start.wait()
            try:
                for _ in range(iterations):
                    snapshot = controller.get_worker_snapshot()
                    pm_status = snapshot.get("pm_status")
                    if pm_status is None:
                        continue
                    if set(pm_status) != {"writer", "sequence"}:
                        _record_error(f"reader-{reader_id}: partial pm_status {pm_status}")
                    if not isinstance(pm_status["writer"], int) or not isinstance(pm_status["sequence"], int):
                        _record_error(f"reader-{reader_id}: invalid pm_status {pm_status}")
            except Exception as error:  # pylint: disable=broad-except
                _record_error(f"reader-{reader_id}: {error}")

        threads = []
        for writer_id in range(thread_count):
            threads.append(threading.Thread(target=writer, args=(writer_id,), daemon=True))
        for reader_id in range(thread_count):
            threads.append(threading.Thread(target=reader, args=(reader_id,), daemon=True))

        for thread in threads:
            thread.start()
        start.set()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        final_snapshot = controller.get_worker_snapshot()
        self.assertIn("captured_at", final_snapshot)
        self.assertIn("pm_status", final_snapshot)

    def test_relay_queue_survives_concurrent_writers_and_consumers(self):
        service = SimpleNamespace(
            poll_interval_ms=1000,
            deviceinstance=60,
            _time_now=_SteppingClock(),
        )
        runtime = RuntimeSupportController(service, lambda *_args, **_kwargs: 0, lambda *_args, **_kwargs: 0)
        service._ensure_worker_state = runtime.ensure_worker_state
        runtime.ensure_worker_state()
        controller = ShellyIoController(service)

        start = threading.Event()
        errors = []
        error_lock = threading.Lock()
        iterations = self._stress_iters()
        thread_count = self._stress_threads()

        def _record_error(message):
            with error_lock:
                errors.append(message)

        def writer(offset):
            start.wait()
            try:
                for index in range(iterations):
                    controller.queue_relay_command(bool((index + offset) % 2))
            except Exception as error:  # pylint: disable=broad-except
                _record_error(f"writer-{offset}: {error}")

        def consumer(consumer_id):
            start.wait()
            try:
                for _ in range(iterations):
                    pending_state, pending_at = controller.peek_pending_relay_command()
                    if (pending_state is None) != (pending_at is None):
                        _record_error(
                            f"consumer-{consumer_id}: torn queue state "
                            f"pending_state={pending_state} pending_at={pending_at}"
                        )
                    if pending_state is not None:
                        controller.clear_pending_relay_command(pending_state)
            except Exception as error:  # pylint: disable=broad-except
                _record_error(f"consumer-{consumer_id}: {error}")

        threads = []
        for writer_id in range(thread_count):
            threads.append(threading.Thread(target=writer, args=(writer_id,), daemon=True))
        for consumer_id in range(thread_count):
            threads.append(threading.Thread(target=consumer, args=(consumer_id,), daemon=True))

        for thread in threads:
            thread.start()
        start.set()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        pending_state, pending_at = controller.peek_pending_relay_command()
        self.assertEqual(pending_state is None, pending_at is None)

    def test_io_worker_loop_survives_prolonged_fault_injection(self):
        iterations = self._stress_iters()
        stop_event = _FastStopEvent()
        read_counter = {"count": 0}

        def fetch_pm_status():
            read_counter["count"] += 1
            current = read_counter["count"]
            if current >= iterations:
                stop_event.set()
            if current % 5 == 0:
                raise RuntimeError("fault-injected Shelly read failure")
            return {"output": bool(current % 2), "apower": float(current)}

        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _worker_stop_event=stop_event,
            _worker_poll_interval_seconds=0.001,
            _time_now=_SteppingClock(),
            _update_worker_snapshot=MagicMock(),
            _worker_apply_pending_relay_command=MagicMock(),
            _worker_fetch_pm_status=fetch_pm_status,
            _mark_recovery=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
            virtual_mode=1,
            auto_shelly_soft_fail_seconds=0.001,
        )
        controller = ShellyIoController(service)

        controller.io_worker_loop()

        self.assertGreaterEqual(read_counter["count"], iterations)
        self.assertGreater(service._mark_failure.call_count, 0)
        self.assertGreater(service._mark_recovery.call_count, 0)
        self.assertGreater(service._update_worker_snapshot.call_count, 0)
        self.assertGreater(service._warning_throttled.call_count, 0)

    def test_auto_input_supervisor_handles_fault_injected_snapshot_stream(self):
        iterations = self._stress_iters()
        updates = []
        errors = []
        updates_lock = threading.Lock()
        errors_lock = threading.Lock()
        start = threading.Event()

        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = os.path.join(temp_dir, "auto-input.json")

            def load_json_file(path):
                with open(path, "r", encoding="utf-8") as handle:
                    return json.load(handle)

            def update_worker_snapshot(**fields):
                with updates_lock:
                    updates.append(dict(fields))

            service = SimpleNamespace(
                _ensure_worker_state=MagicMock(),
                auto_input_snapshot_path=snapshot_path,
                auto_input_helper_stale_seconds=5.0,
                auto_input_helper_restart_seconds=1.0,
                _time_now=lambda: 0.0,
                _stat_path=os.stat,
                _load_json_file=load_json_file,
                _warning_throttled=MagicMock(),
                _mode_uses_auto_logic=lambda mode: int(mode) in (1, 2),
                virtual_mode=1,
                _auto_input_snapshot_mtime_ns=None,
                _auto_input_snapshot_last_seen=None,
                _update_worker_snapshot=update_worker_snapshot,
            )
            controller = AutoInputSupervisor(service)

            def _record_error(message):
                with errors_lock:
                    errors.append(message)

            def writer():
                start.wait()
                try:
                    for index in range(iterations):
                        if index % 4 == 0:
                            payload = "{broken-json"
                        elif index % 4 == 1:
                            payload = json.dumps(["not-a-dict"])
                        elif index % 4 == 2:
                            payload = json.dumps(
                                {
                                    "captured_at": float(index),
                                    "heartbeat_at": float(index),
                                    "pv_captured_at": float(index),
                                    "pv_power": 1200.0,
                                    "battery_captured_at": float(index),
                                    "battery_soc": 55.0,
                                    "grid_captured_at": float(index),
                                    "grid_power": -800.0,
                                }
                            )
                        else:
                            payload = json.dumps(
                                {
                                    "captured_at": float(index - 100),
                                    "heartbeat_at": float(index - 100),
                                    "pv_captured_at": float(index - 100),
                                    "pv_power": 900.0,
                                    "battery_captured_at": float(index - 100),
                                    "battery_soc": 50.0,
                                    "grid_captured_at": float(index - 100),
                                    "grid_power": -500.0,
                                }
                            )
                        write_text_atomically(snapshot_path, payload)
                except Exception as error:  # pylint: disable=broad-except
                    _record_error(f"writer: {error}")

            def reader():
                start.wait()
                try:
                    for index in range(iterations):
                        controller.refresh_snapshot(now=float(index))
                except Exception as error:  # pylint: disable=broad-except
                    _record_error(f"reader: {error}")

            writer_thread = threading.Thread(target=writer, daemon=True)
            reader_thread = threading.Thread(target=reader, daemon=True)
            writer_thread.start()
            reader_thread.start()
            start.set()
            writer_thread.join()
            reader_thread.join()

            write_text_atomically(snapshot_path, "{broken-json")
            controller.refresh_snapshot(now=float(iterations + 0.25))

            write_text_atomically(snapshot_path, json.dumps(["not-a-dict"]))
            controller.refresh_snapshot(now=float(iterations + 0.5))

            write_text_atomically(
                snapshot_path,
                json.dumps(
                    {
                        "captured_at": float(iterations - 100),
                        "heartbeat_at": float(iterations - 100),
                        "pv_captured_at": float(iterations - 100),
                        "pv_power": 900.0,
                        "battery_captured_at": float(iterations - 100),
                        "battery_soc": 50.0,
                        "grid_captured_at": float(iterations - 100),
                        "grid_power": -500.0,
                    }
                ),
            )
            controller.refresh_snapshot(now=float(iterations + 10))

            write_text_atomically(
                snapshot_path,
                json.dumps(
                    {
                        "captured_at": float(iterations + 1),
                        "heartbeat_at": float(iterations + 1),
                        "pv_captured_at": float(iterations + 1),
                        "pv_power": 1200.0,
                        "battery_captured_at": float(iterations + 1),
                        "battery_soc": 55.0,
                        "grid_captured_at": float(iterations + 1),
                        "grid_power": -800.0,
                    }
                ),
            )
            controller.refresh_snapshot(now=float(iterations + 1))

        self.assertEqual(errors, [])
        self.assertGreater(len(updates), 0)
        self.assertGreater(service._warning_throttled.call_count, 0)
        self.assertTrue(any(update.get("pv_power") is None for update in updates))
        self.assertTrue(any(update.get("pv_power") == 1200.0 for update in updates))

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
            with patch("dbus_shelly_wallbox_auto_logic.time.time", return_value=current_time[0]):
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
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=lambda: current[0],
            _stop_auto_input_helper=MagicMock(side_effect=lambda force=False: None),
            _spawn_auto_input_helper=None,
            _warning_throttled=MagicMock(),
            _auto_input_helper_process=process,
            _auto_input_helper_last_start_at=80.0,
            _auto_input_helper_restart_requested_at=None,
            _auto_input_snapshot_last_seen=60.0,
            auto_input_helper_stale_seconds=15.0,
            auto_input_helper_restart_seconds=5.0,
        )
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
            with patch("dbus_shelly_wallbox_auto_logic.time.time", return_value=101.0 + index):
                result = controller.auto_decide_relay(False, 2600.0, 65.0, -2200.0)
            self.assertIsInstance(result, bool)
            self.assertLessEqual(service.auto_stop_surplus_watts, service.auto_start_surplus_watts)
            self.assertLessEqual(service.auto_high_soc_stop_surplus_watts, service.auto_high_soc_start_surplus_watts)
