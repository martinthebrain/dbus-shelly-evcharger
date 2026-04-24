# SPDX-License-Identifier: GPL-3.0-or-later
from tests.energy_probe_cases_cli import _EnergyProbeCliCases
from tests.energy_probe_cases_common import _EnergyProbeBase
from tests.energy_probe_cases_detect import _EnergyProbeDetectCases
from tests.energy_probe_cases_validate import _EnergyProbeValidateCases


class TestVenusEvchargerEnergyProbe(
    _EnergyProbeDetectCases,
    _EnergyProbeValidateCases,
    _EnergyProbeCliCases,
    _EnergyProbeBase,
):
    pass
