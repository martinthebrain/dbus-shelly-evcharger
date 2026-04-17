# SPDX-License-Identifier: GPL-3.0-or-later
from tests.wallbox_shelly_io_controller_cases_primary import TestShellyIoControllerPrimary
from tests.wallbox_shelly_io_controller_cases_secondary import TestShellyIoControllerSecondary
from tests.wallbox_shelly_io_controller_cases_tertiary import TestShellyIoControllerTertiary
from tests.wallbox_shelly_io_controller_cases_quaternary import TestShellyIoControllerQuaternary

__all__ = [
    "TestShellyIoControllerPrimary",
    "TestShellyIoControllerSecondary",
    "TestShellyIoControllerTertiary",
    "TestShellyIoControllerQuaternary",
]
