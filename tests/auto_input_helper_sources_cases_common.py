# SPDX-License-Identifier: GPL-3.0-or-later
from typing import Any, cast

from tests.venus_evcharger_auto_input_helper_support import (
    AutoInputHelperTestCase,
    MagicMock,
    patch,
    venus_evcharger_auto_input_helper,
    sys,
    unittest,
)
from venus_evcharger.energy import EnergyLearningProfile, EnergySourceDefinition, EnergySourceSnapshot
