# SPDX-License-Identifier: GPL-3.0-or-later
from tests.auto_input_helper_sources_cases_common import AutoInputHelperTestCase, unittest
from tests.auto_input_helper_sources_cases_dbus import _AutoInputHelperSourcesDbusCases
from tests.auto_input_helper_sources_cases_energy import _AutoInputHelperSourcesEnergyCases
from tests.auto_input_helper_sources_cases_runtime import _AutoInputHelperSourcesRuntimeCases


class TestShellyWallboxAutoInputHelperSources(
    _AutoInputHelperSourcesDbusCases,
    _AutoInputHelperSourcesEnergyCases,
    _AutoInputHelperSourcesRuntimeCases,
    AutoInputHelperTestCase,
):
    pass
