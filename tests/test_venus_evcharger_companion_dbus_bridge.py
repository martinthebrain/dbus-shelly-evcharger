# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from tests.companion_dbus_bridge_cases_core import _CompanionDbusBridgeCoreCases
from tests.companion_dbus_bridge_cases_grid import _CompanionDbusBridgeGridCases


class TestEnergyCompanionDbusBridge(
    _CompanionDbusBridgeCoreCases,
    _CompanionDbusBridgeGridCases,
    unittest.TestCase,
):
    pass
