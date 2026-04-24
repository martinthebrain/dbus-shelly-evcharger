# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_update_cycle_cases_quaternary_learning import _UpdateCycleQuaternaryLearningCases
from tests.venus_evcharger_update_cycle_cases_quaternary_runtime import _UpdateCycleQuaternaryRuntimeCases
from tests.venus_evcharger_update_cycle_cases_quaternary_victron_adaptive import (
    _UpdateCycleQuaternaryVictronAdaptiveCases,
)
from tests.venus_evcharger_update_cycle_cases_quaternary_victron_core import (
    _UpdateCycleQuaternaryVictronCoreCases,
)
from tests.venus_evcharger_update_cycle_controller_support import UpdateCycleControllerTestBase


class TestUpdateCycleControllerQuaternary(
    _UpdateCycleQuaternaryVictronCoreCases,
    _UpdateCycleQuaternaryVictronAdaptiveCases,
    _UpdateCycleQuaternaryRuntimeCases,
    _UpdateCycleQuaternaryLearningCases,
    UpdateCycleControllerTestBase,
):
    pass
