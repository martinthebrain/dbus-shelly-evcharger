# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from shelly_wallbox.backend.modbus_transport import ModbusSlaveOfflineError
from shelly_wallbox.backend.shelly_io import (
    ShellyIoController,
    _phase_currents_for_selection,
    _phase_powers_for_selection,
    _single_phase_vector,
)
from shelly_wallbox.backend.smartevse_charger import SmartEvseChargerBackend
from shelly_wallbox.backend.models import ChargerState, MeterReading

__all__ = [
    "ChargerState",
    "MagicMock",
    "MeterReading",
    "ModbusSlaveOfflineError",
    "Path",
    "ShellyIoController",
    "ShellyIoControllerTestBase",
    "SimpleNamespace",
    "SmartEvseChargerBackend",
    "_phase_currents_for_selection",
    "_phase_powers_for_selection",
    "_single_phase_vector",
    "patch",
    "tempfile",
    "threading",
]



class ShellyIoControllerTestBase(unittest.TestCase):
    @staticmethod
    def _write_config(directory: str, content: str) -> str:
        path = Path(directory) / "smartevse-charger.ini"
        path.write_text(content, encoding="utf-8")
        return str(path)
