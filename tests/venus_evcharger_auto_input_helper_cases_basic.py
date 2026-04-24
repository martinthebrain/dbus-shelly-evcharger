# SPDX-License-Identifier: GPL-3.0-or-later
from tests.auto_input_helper_basic_cases_common import AutoInputHelperTestCase, unittest
from tests.auto_input_helper_basic_cases_core import _AutoInputHelperBasicCoreCases
from tests.auto_input_helper_basic_cases_snapshot import _AutoInputHelperBasicSnapshotCases
from tests.auto_input_helper_basic_cases_subscriptions import _AutoInputHelperBasicSubscriptionCases


class TestShellyWallboxAutoInputHelperBasic(
    _AutoInputHelperBasicCoreCases,
    _AutoInputHelperBasicSnapshotCases,
    _AutoInputHelperBasicSubscriptionCases,
    AutoInputHelperTestCase,
):
    pass
