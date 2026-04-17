# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

from shelly_wallbox.backend.factory import build_service_backends
from shelly_wallbox.backend.goe_charger import GoEChargerBackend
from shelly_wallbox.backend.modbus_charger import ModbusChargerBackend
from shelly_wallbox.backend.shelly_combined import ShellyCombinedBackend
from shelly_wallbox.backend.shelly_contactor_switch import ShellyContactorSwitchBackend
from shelly_wallbox.backend.shelly_meter import ShellyMeterBackend
from shelly_wallbox.backend.smartevse_charger import SmartEvseChargerBackend
from shelly_wallbox.backend.simpleevse_charger import SimpleEvseChargerBackend
from shelly_wallbox.backend.shelly_switch import ShellySwitchBackend
from shelly_wallbox.backend.switch_group import SwitchGroupBackend
from shelly_wallbox.backend.template_charger import TemplateChargerBackend
from shelly_wallbox.backend.template_meter import TemplateMeterBackend
from shelly_wallbox.backend.template_switch import TemplateSwitchBackend


class TestShellyWallboxBackendFactory(unittest.TestCase):
    def test_build_service_backends_uses_default_combined_selection(self) -> None:
        service = SimpleNamespace(
            phase="L1",
            pm_component="Switch",
            pm_id=0,
            max_current=16.0,
        )

        resolved = build_service_backends(service)

        self.assertEqual(resolved.selection.mode, "combined")
        self.assertEqual(resolved.selection.meter_type, "shelly_combined")
        self.assertEqual(resolved.selection.switch_type, "shelly_combined")
        self.assertIsInstance(resolved.meter, ShellyCombinedBackend)
        self.assertIsInstance(resolved.switch, ShellyCombinedBackend)
        self.assertIsNone(resolved.charger)

    def test_default_combined_selection_uses_distinct_instances_per_role(self) -> None:
        service = SimpleNamespace(
            phase="L1",
            pm_component="Switch",
            pm_id=0,
            max_current=16.0,
        )

        resolved = build_service_backends(service)

        self.assertIsInstance(resolved.meter, ShellyCombinedBackend)
        self.assertIsInstance(resolved.switch, ShellyCombinedBackend)
        self.assertIsNot(resolved.meter, resolved.switch)

    def test_combined_backend_reads_normalized_meter_and_switch_state(self) -> None:
        service = SimpleNamespace(
            phase="L2",
            max_current=16.0,
            _last_voltage=230.0,
            fetch_pm_status=MagicMock(
                return_value={
                    "output": True,
                    "apower": 2300.0,
                    "current": 10.0,
                    "voltage": 230.0,
                    "aenergy": {"total": 12500.0},
                }
            ),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_pm_status={"output": True},
            set_relay=MagicMock(),
        )

        resolved = build_service_backends(service)
        self.assertIsNotNone(resolved.meter)
        self.assertIsNotNone(resolved.switch)
        meter_backend = cast(ShellyCombinedBackend, resolved.meter)
        switch_backend = cast(ShellyCombinedBackend, resolved.switch)
        meter_reading = meter_backend.read_meter()
        switch_state = switch_backend.read_switch_state()
        switch_caps = switch_backend.capabilities()

        self.assertTrue(meter_reading.relay_on)
        self.assertEqual(meter_reading.power_w, 2300.0)
        self.assertEqual(meter_reading.energy_kwh, 12.5)
        self.assertEqual(meter_reading.phase_selection, "P1")
        self.assertEqual(meter_reading.phase_powers_w, (0.0, 2300.0, 0.0))
        self.assertEqual(meter_reading.phase_currents_a, (0.0, 10.0, 0.0))
        self.assertTrue(switch_state.enabled)
        self.assertEqual(switch_state.phase_selection, "P1")
        self.assertEqual(switch_caps.switching_mode, "direct")
        self.assertEqual(switch_caps.supported_phase_selections, ("P1",))

    def test_combined_backend_rejects_multi_phase_switch_requests(self) -> None:
        service = SimpleNamespace(
            phase="3P",
            max_current=16.0,
            _last_voltage=400.0,
            fetch_pm_status=MagicMock(return_value={}),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_pm_status=None,
            set_relay=MagicMock(),
        )

        resolved = build_service_backends(service)
        self.assertIsNotNone(resolved.switch)
        switch_backend = cast(ShellyCombinedBackend, resolved.switch)

        with self.assertRaises(ValueError):
            switch_backend.set_phase_selection("P1_P2_P3")

    def test_build_service_backends_supports_split_meter_and_switch_backends(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = Path(temp_dir) / "meter.ini"
            switch_path = Path(temp_dir) / "switch.ini"
            meter_path.write_text("[Adapter]\nType=shelly_meter\nHost=192.168.1.20\n", encoding="utf-8")
            switch_path.write_text(
                "[Adapter]\nType=shelly_switch\nHost=192.168.1.21\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2,P1_P2_P3\n"
                "[PhaseMap]\nP1=0\nP1_P2=0,1\nP1_P2_P3=0,1,2\n",
                encoding="utf-8",
            )
            service = SimpleNamespace(
                backend_mode="split",
                meter_backend_type="shelly_meter",
                switch_backend_type="shelly_switch",
                charger_backend_type=None,
                meter_backend_config_path=str(meter_path),
                switch_backend_config_path=str(switch_path),
                charger_backend_config_path="",
                phase="L1",
                host="192.168.1.20",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=MagicMock(),
            )

            resolved = build_service_backends(service)

            self.assertEqual(resolved.selection.mode, "split")
            self.assertEqual(resolved.selection.meter_type, "shelly_meter")
            self.assertEqual(resolved.selection.switch_type, "shelly_switch")
            self.assertIsInstance(resolved.meter, ShellyMeterBackend)
            self.assertIsInstance(resolved.switch, ShellySwitchBackend)
            resolved_meter = cast(ShellyMeterBackend, resolved.meter)
            resolved_switch = cast(ShellySwitchBackend, resolved.switch)
            self.assertEqual(resolved_meter.config_path, str(meter_path))
            self.assertEqual(resolved_switch.config_path, str(switch_path))
            self.assertEqual(
                resolved_switch.settings.phase_switch_targets,
                {
                    "P1": (0,),
                    "P1_P2": (0, 1),
                    "P1_P2_P3": (0, 1, 2),
                },
            )

    def test_build_service_backends_supports_contactor_switch_preset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            switch_path = Path(temp_dir) / "switch.ini"
            switch_path.write_text(
                "[Adapter]\nType=shelly_contactor_switch\nHost=192.168.1.21\n",
                encoding="utf-8",
            )
            service = SimpleNamespace(
                backend_mode="split",
                meter_backend_type="shelly_combined",
                switch_backend_type="shelly_contactor_switch",
                charger_backend_type=None,
                meter_backend_config_path="",
                switch_backend_config_path=str(switch_path),
                charger_backend_config_path="",
                phase="L1",
                host="192.168.1.20",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=MagicMock(),
            )

            resolved = build_service_backends(service)

            self.assertIsInstance(resolved.switch, ShellyContactorSwitchBackend)
            self.assertEqual(resolved.selection.switch_type, "shelly_contactor_switch")
            resolved_switch = cast(ShellyContactorSwitchBackend, resolved.switch)
            self.assertEqual(resolved_switch.capabilities().switching_mode, "contactor")
            self.assertIsNone(resolved_switch.capabilities().max_direct_switch_power_w)

    def test_build_service_backends_supports_template_switch_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            switch_path = Path(temp_dir) / "switch.ini"
            switch_path.write_text(
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\nPhaseSelectionPath=phase_selection\n"
                "[CommandRequest]\nUrl=/switch/control\n"
                "[PhaseRequest]\nUrl=/switch/phase\n",
                encoding="utf-8",
            )
            service = SimpleNamespace(
                backend_mode="split",
                meter_backend_type="shelly_combined",
                switch_backend_type="template_switch",
                charger_backend_type=None,
                meter_backend_config_path="",
                switch_backend_config_path=str(switch_path),
                charger_backend_config_path="",
                phase="L1",
                host="192.168.1.20",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=MagicMock(),
            )

            resolved = build_service_backends(service)

            self.assertIsInstance(resolved.switch, TemplateSwitchBackend)
            self.assertEqual(resolved.selection.switch_type, "template_switch")
            resolved_switch = cast(TemplateSwitchBackend, resolved.switch)
            self.assertEqual(resolved_switch.config_path, str(switch_path))
            self.assertEqual(
                resolved_switch.capabilities().supported_phase_selections,
                ("P1", "P1_P2_P3"),
            )

    def test_build_service_backends_supports_switch_group_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            p1_path = Path(temp_dir) / "phase1-switch.ini"
            p2_path = Path(temp_dir) / "phase2-switch.ini"
            switch_path = Path(temp_dir) / "switch.ini"
            p1_path.write_text(
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n"
                "[StateRequest]\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/control\n",
                encoding="utf-8",
            )
            p2_path.write_text(
                "[Adapter]\nType=shelly_switch\nHost=192.168.1.21\n",
                encoding="utf-8",
            )
            switch_path.write_text(
                "[Adapter]\nType=switch_group\n"
                "[Members]\nP1=phase1-switch.ini\nP2=phase2-switch.ini\n",
                encoding="utf-8",
            )
            service = SimpleNamespace(
                backend_mode="split",
                meter_backend_type="shelly_combined",
                switch_backend_type="switch_group",
                charger_backend_type=None,
                meter_backend_config_path="",
                switch_backend_config_path=str(switch_path),
                charger_backend_config_path="",
                phase="L1",
                host="192.168.1.20",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=MagicMock(),
            )

            resolved = build_service_backends(service)

            self.assertIsInstance(resolved.switch, SwitchGroupBackend)
            self.assertEqual(resolved.selection.switch_type, "switch_group")
            resolved_switch = cast(SwitchGroupBackend, resolved.switch)
            self.assertEqual(
                resolved_switch.settings.phase_switch_targets,
                {
                    "P1": ("P1",),
                    "P1_P2": ("P1", "P2"),
                },
            )
            self.assertEqual(
                resolved_switch.settings.phase_members["P1"].backend_type,
                "template_switch",
            )
            self.assertEqual(
                resolved_switch.settings.phase_members["P2"].backend_type,
                "shelly_switch",
            )

    def test_build_service_backends_supports_template_meter_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = Path(temp_dir) / "meter.ini"
            meter_path.write_text(
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "[MeterRequest]\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=power_w\nEnergyKwhPath=energy_kwh\n",
                encoding="utf-8",
            )
            service = SimpleNamespace(
                backend_mode="split",
                meter_backend_type="template_meter",
                switch_backend_type="shelly_combined",
                charger_backend_type=None,
                meter_backend_config_path=str(meter_path),
                switch_backend_config_path="",
                charger_backend_config_path="",
                phase="L1",
                host="192.168.1.20",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=MagicMock(),
            )

            resolved = build_service_backends(service)

            self.assertIsInstance(resolved.meter, TemplateMeterBackend)
            self.assertEqual(resolved.selection.meter_type, "template_meter")
            resolved_meter = cast(TemplateMeterBackend, resolved.meter)
            self.assertEqual(resolved_meter.config_path, str(meter_path))
            self.assertEqual(resolved_meter.settings.power_path, "power_w")

    def test_build_service_backends_supports_template_charger_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = Path(temp_dir) / "charger.ini"
            charger_path.write_text(
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "[EnableRequest]\nUrl=/charger/enable\n"
                "[CurrentRequest]\nUrl=/charger/current\n"
                "[PhaseRequest]\nUrl=/charger/phase\n",
                encoding="utf-8",
            )
            service = SimpleNamespace(
                backend_mode="split",
                meter_backend_type="shelly_combined",
                switch_backend_type="shelly_combined",
                charger_backend_type="template_charger",
                meter_backend_config_path="",
                switch_backend_config_path="",
                charger_backend_config_path=str(charger_path),
                phase="L1",
                host="192.168.1.20",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=MagicMock(),
            )

            resolved = build_service_backends(service)

            self.assertIsInstance(resolved.charger, TemplateChargerBackend)
            self.assertEqual(resolved.selection.charger_type, "template_charger")
            resolved_charger = cast(TemplateChargerBackend, resolved.charger)
            self.assertEqual(resolved_charger.config_path, str(charger_path))
            self.assertEqual(
                resolved_charger.settings.supported_phase_selections,
                ("P1", "P1_P2_P3"),
            )

    def test_build_service_backends_supports_goe_charger_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = Path(temp_dir) / "charger.ini"
            charger_path.write_text(
                "[Adapter]\nType=goe_charger\nBaseUrl=http://goe.local\n",
                encoding="utf-8",
            )
            service = SimpleNamespace(
                backend_mode="split",
                meter_backend_type="none",
                switch_backend_type="none",
                charger_backend_type="goe_charger",
                meter_backend_config_path="",
                switch_backend_config_path="",
                charger_backend_config_path=str(charger_path),
                phase="L1",
                host="192.168.1.20",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=MagicMock(),
            )

            resolved = build_service_backends(service)

            self.assertIsNone(resolved.meter)
            self.assertIsNone(resolved.switch)
            self.assertIsInstance(resolved.charger, GoEChargerBackend)
            resolved_charger = cast(GoEChargerBackend, resolved.charger)
            self.assertEqual(resolved.selection.charger_type, "goe_charger")
            self.assertEqual(resolved_charger.config_path, str(charger_path))
            self.assertEqual(resolved_charger.settings.supported_phase_selections, ("P1",))

    def test_build_service_backends_supports_modbus_charger_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = Path(temp_dir) / "charger.ini"
            charger_path.write_text(
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.40\nPort=502\nUnitId=7\n"
                "[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n",
                encoding="utf-8",
            )
            service = SimpleNamespace(
                backend_mode="split",
                meter_backend_type="none",
                switch_backend_type="none",
                charger_backend_type="modbus_charger",
                meter_backend_config_path="",
                switch_backend_config_path="",
                charger_backend_config_path=str(charger_path),
                phase="L1",
                host="192.168.1.20",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=MagicMock(),
            )

            resolved = build_service_backends(service)

            self.assertIsNone(resolved.meter)
            self.assertIsNone(resolved.switch)
            self.assertIsInstance(resolved.charger, ModbusChargerBackend)
            resolved_charger = cast(ModbusChargerBackend, resolved.charger)
            self.assertEqual(resolved.selection.charger_type, "modbus_charger")
            self.assertEqual(resolved_charger.config_path, str(charger_path))
            self.assertEqual(resolved_charger.settings.transport_settings.transport_kind, "tcp")

    def test_build_service_backends_supports_simpleevse_charger_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = Path(temp_dir) / "charger.ini"
            charger_path.write_text(
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Capabilities]\nSupportedPhaseSelections=P1_P2_P3\n"
                "[Transport]\nHost=192.168.1.50\nPort=502\nUnitId=1\n",
                encoding="utf-8",
            )
            service = SimpleNamespace(
                backend_mode="split",
                meter_backend_type="none",
                switch_backend_type="none",
                charger_backend_type="simpleevse_charger",
                meter_backend_config_path="",
                switch_backend_config_path="",
                charger_backend_config_path=str(charger_path),
                phase="L1",
                host="192.168.1.20",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=MagicMock(),
            )

            resolved = build_service_backends(service)

            self.assertIsNone(resolved.meter)
            self.assertIsNone(resolved.switch)
            self.assertIsInstance(resolved.charger, SimpleEvseChargerBackend)
            resolved_charger = cast(SimpleEvseChargerBackend, resolved.charger)
            self.assertEqual(resolved.selection.charger_type, "simpleevse_charger")
            self.assertEqual(resolved_charger.config_path, str(charger_path))
            self.assertEqual(resolved_charger.settings.transport_settings.transport_kind, "tcp")
            self.assertEqual(resolved_charger.settings.supported_phase_selections, ("P1_P2_P3",))

    def test_build_service_backends_supports_smartevse_charger_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = Path(temp_dir) / "charger.ini"
            charger_path.write_text(
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Capabilities]\nSupportedPhaseSelections=P1_P2\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
                encoding="utf-8",
            )
            service = SimpleNamespace(
                backend_mode="split",
                meter_backend_type="none",
                switch_backend_type="none",
                charger_backend_type="smartevse_charger",
                meter_backend_config_path="",
                switch_backend_config_path="",
                charger_backend_config_path=str(charger_path),
                phase="L1",
                host="192.168.1.20",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=MagicMock(),
            )

            resolved = build_service_backends(service)

            self.assertIsNone(resolved.meter)
            self.assertIsNone(resolved.switch)
            self.assertIsInstance(resolved.charger, SmartEvseChargerBackend)
            resolved_charger = cast(SmartEvseChargerBackend, resolved.charger)
            self.assertEqual(resolved.selection.charger_type, "smartevse_charger")
            self.assertEqual(resolved_charger.config_path, str(charger_path))
            self.assertEqual(resolved_charger.settings.transport_settings.transport_kind, "tcp")
            self.assertEqual(resolved_charger.settings.supported_phase_selections, ("P1_P2",))

    def test_build_service_backends_supports_split_charger_without_meter_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            switch_path = Path(temp_dir) / "switch.ini"
            charger_path = Path(temp_dir) / "charger.ini"
            switch_path.write_text(
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/switch/control\n",
                encoding="utf-8",
            )
            charger_path.write_text(
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[EnableRequest]\nUrl=/charger/enable\n"
                "[CurrentRequest]\nUrl=/charger/current\n",
                encoding="utf-8",
            )
            service = SimpleNamespace(
                backend_mode="split",
                meter_backend_type="none",
                switch_backend_type="template_switch",
                charger_backend_type="template_charger",
                meter_backend_config_path="",
                switch_backend_config_path=str(switch_path),
                charger_backend_config_path=str(charger_path),
                phase="L1",
                host="192.168.1.20",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=MagicMock(),
            )

            resolved = build_service_backends(service)

            self.assertEqual(resolved.selection.meter_type, "none")
            self.assertIsNone(resolved.meter)
            self.assertIsInstance(resolved.switch, TemplateSwitchBackend)
            self.assertIsInstance(resolved.charger, TemplateChargerBackend)

    def test_build_service_backends_rejects_meterless_split_mode_without_charger(self) -> None:
        service = SimpleNamespace(
            backend_mode="split",
            meter_backend_type="none",
            switch_backend_type="shelly_combined",
            charger_backend_type=None,
            meter_backend_config_path="",
            switch_backend_config_path="",
            charger_backend_config_path="",
            phase="L1",
            host="192.168.1.20",
            pm_component="Switch",
            pm_id=0,
            max_current=16.0,
            session=MagicMock(),
        )

        with self.assertRaisesRegex(ValueError, "MeterType=none requires a configured charger backend"):
            build_service_backends(service)

    def test_build_service_backends_supports_switchless_split_charger_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = Path(temp_dir) / "charger.ini"
            charger_path.write_text(
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[EnableRequest]\nUrl=/charger/enable\n"
                "[CurrentRequest]\nUrl=/charger/current\n"
                "[PhaseRequest]\nUrl=/charger/phase\n",
                encoding="utf-8",
            )
            service = SimpleNamespace(
                backend_mode="split",
                meter_backend_type="none",
                switch_backend_type="none",
                charger_backend_type="template_charger",
                meter_backend_config_path="",
                switch_backend_config_path="",
                charger_backend_config_path=str(charger_path),
                phase="L1",
                host="192.168.1.20",
                pm_component="Switch",
                pm_id=0,
                max_current=16.0,
                session=MagicMock(),
            )

            resolved = build_service_backends(service)

            self.assertEqual(resolved.selection.switch_type, "none")
            self.assertIsNone(resolved.meter)
            self.assertIsNone(resolved.switch)
            self.assertIsInstance(resolved.charger, TemplateChargerBackend)

    def test_build_service_backends_rejects_switchless_split_mode_without_charger(self) -> None:
        service = SimpleNamespace(
            backend_mode="split",
            meter_backend_type="shelly_combined",
            switch_backend_type="none",
            charger_backend_type=None,
            meter_backend_config_path="",
            switch_backend_config_path="",
            charger_backend_config_path="",
            phase="L1",
            host="192.168.1.20",
            pm_component="Switch",
            pm_id=0,
            max_current=16.0,
            session=MagicMock(),
        )

        with self.assertRaisesRegex(ValueError, "SwitchType=none requires a configured charger backend"):
            build_service_backends(service)

    def test_build_service_backends_rejects_meterless_combined_mode(self) -> None:
        service = SimpleNamespace(
            backend_mode="combined",
            meter_backend_type="none",
            switch_backend_type="shelly_combined",
            charger_backend_type=None,
            meter_backend_config_path="",
            switch_backend_config_path="",
            charger_backend_config_path="",
            phase="L1",
            host="192.168.1.20",
            pm_component="Switch",
            pm_id=0,
            max_current=16.0,
            session=MagicMock(),
        )

        with self.assertRaisesRegex(ValueError, "MeterType=none is only supported in split backend mode"):
            build_service_backends(service)

    def test_build_service_backends_rejects_switchless_combined_mode(self) -> None:
        service = SimpleNamespace(
            backend_mode="combined",
            meter_backend_type="shelly_combined",
            switch_backend_type="none",
            charger_backend_type=None,
            meter_backend_config_path="",
            switch_backend_config_path="",
            charger_backend_config_path="",
            phase="L1",
            host="192.168.1.20",
            pm_component="Switch",
            pm_id=0,
            max_current=16.0,
            session=MagicMock(),
        )

        with self.assertRaisesRegex(ValueError, "SwitchType=none is only supported in split backend mode"):
            build_service_backends(service)
