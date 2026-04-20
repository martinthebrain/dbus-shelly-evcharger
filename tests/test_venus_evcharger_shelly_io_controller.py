# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_shelly_io_controller_cases_primary import TestShellyIoControllerPrimary
from tests.venus_evcharger_shelly_io_controller_cases_secondary import TestShellyIoControllerSecondary
from tests.venus_evcharger_shelly_io_controller_cases_tertiary import TestShellyIoControllerTertiary
from tests.venus_evcharger_shelly_io_controller_cases_quaternary import TestShellyIoControllerQuaternary

__all__ = [
    "TestShellyIoControllerPrimary",
    "TestShellyIoControllerSecondary",
    "TestShellyIoControllerTertiary",
    "TestShellyIoControllerQuaternary",
]
