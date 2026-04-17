# SPDX-License-Identifier: GPL-3.0-or-later
import os
import threading
import unittest

from shelly_wallbox.auto.policy import AutoStopEwmaPolicy, AutoThresholdProfile, AutoPolicy, validate_auto_policy
from shelly_wallbox.controllers.auto import AutoDecisionController
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


class StressTestCaseBase(unittest.TestCase):
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


__all__ = [
    "AutoPolicy",
    "AutoStopEwmaPolicy",
    "AutoThresholdProfile",
    "StressTestCaseBase",
    "_FastStopEvent",
    "_SteppingClock",
    "validate_auto_policy",
]
