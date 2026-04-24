# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from tests.energy_connectors_cases_common import _EnergyConnectorsTestBase
from tests.energy_connectors_cases_helpers import _EnergyConnectorsHelperCases
from tests.energy_connectors_cases_http_opendtu import _EnergyConnectorsHttpOpenDtuCases
from tests.energy_connectors_cases_modbus_command import _EnergyConnectorsModbusCommandCases


class TestVenusEvchargerEnergyConnectors(
    _EnergyConnectorsHttpOpenDtuCases,
    _EnergyConnectorsModbusCommandCases,
    _EnergyConnectorsHelperCases,
    _EnergyConnectorsTestBase,
):
    pass


if __name__ == "__main__":
    unittest.main()
