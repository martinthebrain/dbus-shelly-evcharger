# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock
import unittest

from shelly_wallbox.backend.factory import _resolved_meter_backend, _resolved_switch_backend, build_service_backends
from shelly_wallbox.backend.goe_charger import GoEChargerBackend
from shelly_wallbox.backend.models import BackendSelection
from shelly_wallbox.backend.registry import create_meter_backend
from shelly_wallbox.backend.shelly_combined import ShellyCombinedBackend
from shelly_wallbox.backend.shelly_contactor_switch import ShellyContactorSwitchBackend
from shelly_wallbox.backend.shelly_meter import ShellyMeterBackend
from shelly_wallbox.backend.shelly_switch import ShellySwitchBackend
from shelly_wallbox.backend.switch_group import SwitchGroupBackend
from shelly_wallbox.backend.template_meter import TemplateMeterBackend
from shelly_wallbox.backend.template_switch import TemplateSwitchBackend


class TestShellyWallboxBackendFactoryPrimary(unittest.TestCase):
    def test_private_resolvers_reject_none_backends_outside_split_mode(self) -> None:
        selection = BackendSelection(mode="combined", meter_type="none", switch_type="none", charger_type=None, meter_config_path=Path(""), switch_config_path=Path(""), charger_config_path=Path(""))
        with self.assertRaisesRegex(ValueError, "MeterType=none"):
            _resolved_meter_backend(selection, SimpleNamespace())
        with self.assertRaisesRegex(ValueError, "SwitchType=none"):
            _resolved_switch_backend(selection, SimpleNamespace())

    def test_registry_rejects_unsupported_meter_backend_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported meter backend"):
            create_meter_backend("unknown", SimpleNamespace(), "")

    def test_build_service_backends_uses_default_combined_selection(self) -> None:
        service = SimpleNamespace(phase="L1", pm_component="Switch", pm_id=0, max_current=16.0)
        resolved = build_service_backends(service)
        self.assertEqual(resolved.selection.mode, "combined")
        self.assertIsInstance(resolved.meter, ShellyCombinedBackend)
        self.assertIsInstance(resolved.switch, ShellyCombinedBackend)

    def test_build_service_backends_supports_split_meter_and_switch_backends(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = Path(temp_dir) / "meter.ini"
            switch_path = Path(temp_dir) / "switch.ini"
            meter_path.write_text("[Adapter]\nType=shelly_meter\nHost=192.168.1.20\n", encoding="utf-8")
            switch_path.write_text("[Adapter]\nType=shelly_switch\nHost=192.168.1.21\n[Capabilities]\nSupportedPhaseSelections=P1,P1_P2,P1_P2_P3\n[PhaseMap]\nP1=0\nP1_P2=0,1\nP1_P2_P3=0,1,2\n", encoding="utf-8")
            service = SimpleNamespace(backend_mode="split", meter_backend_type="shelly_meter", switch_backend_type="shelly_switch", charger_backend_type=None, meter_backend_config_path=str(meter_path), switch_backend_config_path=str(switch_path), charger_backend_config_path="", phase="L1", host="192.168.1.20", pm_component="Switch", pm_id=0, max_current=16.0, session=MagicMock())
            resolved = build_service_backends(service)
            self.assertIsInstance(resolved.meter, ShellyMeterBackend)
            self.assertIsInstance(resolved.switch, ShellySwitchBackend)

    def test_build_service_backends_supports_contactor_and_template_switches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            switch_path = Path(temp_dir) / "switch.ini"
            switch_path.write_text("[Adapter]\nType=shelly_contactor_switch\nHost=192.168.1.21\n", encoding="utf-8")
            service = SimpleNamespace(backend_mode="split", meter_backend_type="shelly_combined", switch_backend_type="shelly_contactor_switch", charger_backend_type=None, meter_backend_config_path="", switch_backend_config_path=str(switch_path), charger_backend_config_path="", phase="L1", host="192.168.1.20", pm_component="Switch", pm_id=0, max_current=16.0, session=MagicMock())
            resolved = build_service_backends(service)
            self.assertIsInstance(resolved.switch, ShellyContactorSwitchBackend)
            switch_path.write_text("[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n[StateRequest]\nUrl=/switch/state\n[StateResponse]\nEnabledPath=enabled\nPhaseSelectionPath=phase_selection\n[CommandRequest]\nUrl=/switch/control\n[PhaseRequest]\nUrl=/switch/phase\n", encoding="utf-8")
            service.switch_backend_type = "template_switch"
            resolved = build_service_backends(service)
            self.assertIsInstance(resolved.switch, TemplateSwitchBackend)

    def test_build_service_backends_supports_switch_group_and_template_meter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            p1_path = Path(temp_dir) / "phase1-switch.ini"
            p2_path = Path(temp_dir) / "phase2-switch.ini"
            switch_path = Path(temp_dir) / "switch.ini"
            p1_path.write_text("[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n[StateRequest]\nUrl=/state\n[StateResponse]\nEnabledPath=enabled\n[CommandRequest]\nMethod=POST\nUrl=/control\n", encoding="utf-8")
            p2_path.write_text("[Adapter]\nType=shelly_switch\nHost=192.168.1.21\n", encoding="utf-8")
            switch_path.write_text("[Adapter]\nType=switch_group\n[Members]\nP1=phase1-switch.ini\nP2=phase2-switch.ini\n", encoding="utf-8")
            meter_path = Path(temp_dir) / "meter.ini"
            meter_path.write_text("[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n[MeterRequest]\nUrl=/meter/state\n[MeterResponse]\nPowerPath=power_w\nEnergyKwhPath=energy_kwh\n", encoding="utf-8")
            service = SimpleNamespace(backend_mode="split", meter_backend_type="template_meter", switch_backend_type="switch_group", charger_backend_type=None, meter_backend_config_path=str(meter_path), switch_backend_config_path=str(switch_path), charger_backend_config_path="", phase="L1", host="192.168.1.20", pm_component="Switch", pm_id=0, max_current=16.0, session=MagicMock())
            resolved = build_service_backends(service)
            self.assertIsInstance(resolved.meter, TemplateMeterBackend)
            self.assertIsInstance(resolved.switch, SwitchGroupBackend)

    def test_build_service_backends_supports_goe_charger_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = Path(temp_dir) / "charger.ini"
            charger_path.write_text("[Adapter]\nType=goe_charger\nBaseUrl=http://goe.local\n", encoding="utf-8")
            service = SimpleNamespace(backend_mode="split", meter_backend_type="none", switch_backend_type="none", charger_backend_type="goe_charger", meter_backend_config_path="", switch_backend_config_path="", charger_backend_config_path=str(charger_path), phase="L1", host="192.168.1.20", pm_component="Switch", pm_id=0, max_current=16.0, session=MagicMock())
            resolved = build_service_backends(service)
            self.assertIsInstance(resolved.charger, GoEChargerBackend)
