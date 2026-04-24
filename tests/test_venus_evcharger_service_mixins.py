# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from tests.service_mixins_cases_control import _ServiceMixinsControlCases
from tests.service_mixins_cases_runtime_update import _ServiceMixinsRuntimeUpdateCases


class TestShellyWallboxServiceMixins(
    _ServiceMixinsRuntimeUpdateCases,
    _ServiceMixinsControlCases,
    unittest.TestCase,
):
    pass
