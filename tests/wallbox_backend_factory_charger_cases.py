# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from shelly_wallbox.backend.factory import build_service_backends
from shelly_wallbox.backend.modbus_charger import ModbusChargerBackend
from shelly_wallbox.backend.simpleevse_charger import SimpleEvseChargerBackend
from shelly_wallbox.backend.smartevse_charger import SmartEvseChargerBackend
from shelly_wallbox.backend.template_charger import TemplateChargerBackend


class TestShellyWallboxBackendFactoryChargers(unittest.TestCase):
    def test_build_service_backends_supports_template_and_modbus_chargers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = Path(temp_dir) / "charger.ini"
            charger_path.write_text("[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n[EnableRequest]\nUrl=/charger/enable\n[CurrentRequest]\nUrl=/charger/current\n[PhaseRequest]\nUrl=/charger/phase\n", encoding="utf-8")
            service = SimpleNamespace(backend_mode="split", meter_backend_type="shelly_combined", switch_backend_type="shelly_combined", charger_backend_type="template_charger", meter_backend_config_path="", switch_backend_config_path="", charger_backend_config_path=str(charger_path), phase="L1", host="192.168.1.20", pm_component="Switch", pm_id=0, max_current=16.0, session=MagicMock())
            resolved = build_service_backends(service)
            self.assertIsInstance(resolved.charger, TemplateChargerBackend)
            charger_path.write_text("[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n[Transport]\nHost=192.168.1.40\nPort=502\nUnitId=7\n[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n", encoding="utf-8")
            service.charger_backend_type = "modbus_charger"
            resolved = build_service_backends(service)
            self.assertIsInstance(resolved.charger, ModbusChargerBackend)

    def test_build_service_backends_supports_simpleevse_and_smartevse_chargers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = Path(temp_dir) / "charger.ini"
            charger_path.write_text("[Adapter]\nType=simpleevse_charger\nTransport=tcp\n[Capabilities]\nSupportedPhaseSelections=P1_P2_P3\n[Transport]\nHost=192.168.1.50\nPort=502\nUnitId=1\n", encoding="utf-8")
            service = SimpleNamespace(backend_mode="split", meter_backend_type="none", switch_backend_type="none", charger_backend_type="simpleevse_charger", meter_backend_config_path="", switch_backend_config_path="", charger_backend_config_path=str(charger_path), phase="L1", host="192.168.1.20", pm_component="Switch", pm_id=0, max_current=16.0, session=MagicMock())
            resolved = build_service_backends(service)
            self.assertIsInstance(resolved.charger, SimpleEvseChargerBackend)
            charger_path.write_text("[Adapter]\nType=smartevse_charger\nTransport=tcp\n[Capabilities]\nSupportedPhaseSelections=P1_P2\n[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n", encoding="utf-8")
            service.charger_backend_type = "smartevse_charger"
            resolved = build_service_backends(service)
            self.assertIsInstance(resolved.charger, SmartEvseChargerBackend)

    def test_build_service_backends_split_mode_none_edges(self) -> None:
        service = SimpleNamespace(backend_mode="split", meter_backend_type="none", switch_backend_type="shelly_combined", charger_backend_type=None, meter_backend_config_path="", switch_backend_config_path="", charger_backend_config_path="", phase="L1", host="192.168.1.20", pm_component="Switch", pm_id=0, max_current=16.0, session=MagicMock())
        with self.assertRaisesRegex(ValueError, "MeterType=none requires a configured charger backend"):
            build_service_backends(service)
        service = SimpleNamespace(backend_mode="split", meter_backend_type="shelly_combined", switch_backend_type="none", charger_backend_type=None, meter_backend_config_path="", switch_backend_config_path="", charger_backend_config_path="", phase="L1", host="192.168.1.20", pm_component="Switch", pm_id=0, max_current=16.0, session=MagicMock())
        with self.assertRaisesRegex(ValueError, "SwitchType=none requires a configured charger backend"):
            build_service_backends(service)

    def test_build_service_backends_combined_mode_none_edges(self) -> None:
        service = SimpleNamespace(backend_mode="combined", meter_backend_type="none", switch_backend_type="shelly_combined", charger_backend_type=None, meter_backend_config_path="", switch_backend_config_path="", charger_backend_config_path="", phase="L1", host="192.168.1.20", pm_component="Switch", pm_id=0, max_current=16.0, session=MagicMock())
        with self.assertRaisesRegex(ValueError, "MeterType=none is only supported in split backend mode"):
            build_service_backends(service)
        service = SimpleNamespace(backend_mode="combined", meter_backend_type="shelly_combined", switch_backend_type="none", charger_backend_type=None, meter_backend_config_path="", switch_backend_config_path="", charger_backend_config_path="", phase="L1", host="192.168.1.20", pm_component="Switch", pm_id=0, max_current=16.0, session=MagicMock())
        with self.assertRaisesRegex(ValueError, "SwitchType=none is only supported in split backend mode"):
            build_service_backends(service)
