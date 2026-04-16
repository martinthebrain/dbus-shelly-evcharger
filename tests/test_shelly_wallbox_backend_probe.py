# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from shelly_wallbox.backend.probe import main, validate_backend_config


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class TestShellyWallboxBackendProbe(unittest.TestCase):
    @staticmethod
    def _write_config(directory: str, filename: str, content: str) -> str:
        path = Path(directory) / filename
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_validate_backend_config_accepts_meter_and_switch_types(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = self._write_config(
                temp_dir,
                "meter.ini",
                "[Adapter]\nType=shelly_meter\nHost=192.168.1.10\n",
            )

            payload = validate_backend_config(meter_path)

            self.assertEqual(payload["type"], "shelly_meter")
            self.assertEqual(payload["roles"], ["meter"])

    def test_validate_backend_config_accepts_contactor_switch_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            switch_path = self._write_config(
                temp_dir,
                "switch.ini",
                "[Adapter]\nType=shelly_contactor_switch\nHost=192.168.1.11\n",
            )

            payload = validate_backend_config(switch_path)

            self.assertEqual(payload["type"], "shelly_contactor_switch")
            self.assertEqual(payload["roles"], ["switch"])

    def test_validate_backend_config_accepts_template_switch_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            switch_path = self._write_config(
                temp_dir,
                "switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/switch/control\n",
            )

            payload = validate_backend_config(switch_path)

            self.assertEqual(payload["type"], "template_switch")
            self.assertEqual(payload["roles"], ["switch"])

    def test_validate_backend_config_accepts_template_meter_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = self._write_config(
                temp_dir,
                "meter.ini",
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "[MeterRequest]\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=power_w\n",
            )

            payload = validate_backend_config(meter_path)

            self.assertEqual(payload["type"], "template_meter")
            self.assertEqual(payload["roles"], ["meter"])

    def test_validate_backend_config_accepts_template_charger_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[EnableRequest]\nUrl=/charger/enable\n"
                "[CurrentRequest]\nUrl=/charger/current\n",
            )

            payload = validate_backend_config(charger_path)

            self.assertEqual(payload["type"], "template_charger")
            self.assertEqual(payload["roles"], ["charger"])

    def test_probe_meter_command_prints_normalized_meter_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = self._write_config(
                temp_dir,
                "meter.ini",
                "[Adapter]\nType=shelly_meter\nHost=192.168.1.10\nComponent=Switch\nId=0\n"
                "[Phase]\nMeasuredPhaseSelection=P1_P2_P3\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "output": True,
                    "apower": 3450.0,
                    "current": 15.0,
                    "voltage": 230.0,
                    "aenergy": {"total": 6789.0},
                }
            )

            stdout = io.StringIO()
            with patch("shelly_wallbox.backend.probe.requests.Session", return_value=session):
                with redirect_stdout(stdout):
                    rc = main(["probe-meter", meter_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "shelly_meter")
            self.assertEqual(payload["meter"]["phase_selection"], "P1_P2_P3")
            self.assertEqual(payload["meter"]["phase_powers_w"], [1150.0, 1150.0, 1150.0])

    def test_probe_template_meter_prints_normalized_meter_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            meter_path = self._write_config(
                temp_dir,
                "meter.ini",
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "[Phase]\nMeasuredPhaseSelection=P1\n"
                "[MeterRequest]\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=data.power_w\nCurrentPath=data.current_a\n"
                "VoltagePath=data.voltage_v\nEnergyKwhPath=data.energy_kwh\n"
                "PhaseSelectionPath=data.phase_selection\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "data": {
                        "power_w": 3450.0,
                        "current_a": 15.0,
                        "voltage_v": 230.0,
                        "energy_kwh": 6.789,
                        "phase_selection": "P1_P2_P3",
                    }
                }
            )

            stdout = io.StringIO()
            with patch("shelly_wallbox.backend.probe.requests.Session", return_value=session):
                with redirect_stdout(stdout):
                    rc = main(["probe-meter", meter_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "template_meter")
            self.assertEqual(payload["meter"]["phase_selection"], "P1_P2_P3")
            self.assertEqual(payload["meter"]["phase_powers_w"], [1150.0, 1150.0, 1150.0])

    def test_probe_switch_command_prints_capabilities_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            switch_path = self._write_config(
                temp_dir,
                "switch.ini",
                "[Adapter]\nType=shelly_switch\nHost=192.168.1.11\nComponent=Switch\nId=1\n"
                "[Capabilities]\nSwitchingMode=contactor\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "RequiresChargePauseForPhaseChange=1\n"
                "[PhaseMap]\nP1=1\nP1_P2_P3=1,2,3\n",
            )
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"output": False}),
                _FakeResponse({"output": False}),
                _FakeResponse({"output": False}),
            ]

            stdout = io.StringIO()
            with patch("shelly_wallbox.backend.probe.requests.Session", return_value=session):
                with redirect_stdout(stdout):
                    rc = main(["probe-switch", switch_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "shelly_switch")
            self.assertEqual(payload["capabilities"]["switching_mode"], "contactor")
            self.assertEqual(payload["capabilities"]["supported_phase_selections"], ["P1", "P1_P2_P3"])
            self.assertEqual(payload["phase_switch_targets"], {"P1": [1], "P1_P2_P3": [1, 2, 3]})
            self.assertFalse(payload["switch_state"]["enabled"])

    def test_probe_contactor_switch_defaults_to_contactor_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            switch_path = self._write_config(
                temp_dir,
                "switch.ini",
                "[Adapter]\nType=shelly_contactor_switch\nHost=192.168.1.11\nComponent=Switch\nId=1\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"output": False})

            stdout = io.StringIO()
            with patch("shelly_wallbox.backend.probe.requests.Session", return_value=session):
                with redirect_stdout(stdout):
                    rc = main(["probe-switch", switch_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "shelly_contactor_switch")
            self.assertEqual(payload["capabilities"]["switching_mode"], "contactor")
            self.assertIsNone(payload["capabilities"]["max_direct_switch_power_w"])

    def test_probe_switch_prints_native_feedback_and_interlock_readback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            switch_path = self._write_config(
                temp_dir,
                "switch.ini",
                "[Adapter]\nType=shelly_contactor_switch\nHost=192.168.1.11\nComponent=Switch\nId=1\n"
                "[Feedback]\nComponent=Input\nId=7\nValuePath=state\n"
                "[Interlock]\nComponent=Input\nId=8\nValuePath=state\nInvert=1\n",
            )
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"output": True}),
                _FakeResponse({"state": True}),
                _FakeResponse({"state": False}),
            ]

            stdout = io.StringIO()
            with patch("shelly_wallbox.backend.probe.requests.Session", return_value=session):
                with redirect_stdout(stdout):
                    rc = main(["probe-switch", switch_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["switch_state"]["feedback_closed"], True)
            self.assertEqual(payload["switch_state"]["interlock_ok"], True)
            self.assertEqual(payload["feedback_readback"]["component"], "Input")
            self.assertEqual(payload["feedback_readback"]["device_id"], 7)
            self.assertEqual(payload["feedback_readback"]["value_path"], "state")
            self.assertEqual(payload["interlock_readback"]["component"], "Input")
            self.assertEqual(payload["interlock_readback"]["device_id"], 8)
            self.assertEqual(payload["interlock_readback"]["invert"], True)

    def test_probe_template_switch_prints_normalized_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            switch_path = self._write_config(
                temp_dir,
                "switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=data.enabled\nPhaseSelectionPath=data.phase_selection\n"
                "[CommandRequest]\nUrl=/switch/control\n"
                "[PhaseRequest]\nUrl=/switch/phase\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {"data": {"enabled": True, "phase_selection": "P1_P2_P3"}}
            )

            stdout = io.StringIO()
            with patch("shelly_wallbox.backend.probe.requests.Session", return_value=session):
                with redirect_stdout(stdout):
                    rc = main(["probe-switch", switch_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "template_switch")
            self.assertEqual(payload["capabilities"]["supported_phase_selections"], ["P1", "P1_P2_P3"])
            self.assertEqual(payload["switch_state"]["phase_selection"], "P1_P2_P3")
            self.assertTrue(payload["switch_state"]["enabled"])

    def test_probe_template_charger_prints_normalized_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "[StateRequest]\nUrl=/charger/state\n"
                "[StateResponse]\nActualCurrentPath=data.actual_current\n"
                "PowerWattsPath=data.power_w\nEnergyKwhPath=data.energy_kwh\n"
                "StatusPath=data.status\nFaultPath=data.fault\n"
                "[EnableRequest]\nUrl=/charger/enable\n"
                "[CurrentRequest]\nUrl=/charger/current\n"
                "[PhaseRequest]\nUrl=/charger/phase\n",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(["probe-charger", charger_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "template_charger")
            self.assertEqual(payload["supported_phase_selections"], ["P1", "P1_P2_P3"])
            self.assertEqual(payload["state_url"], "http://adapter.local/charger/state")
            self.assertEqual(payload["state_actual_current_path"], "data.actual_current")
            self.assertEqual(payload["state_power_watts_path"], "data.power_w")
            self.assertEqual(payload["state_energy_kwh_path"], "data.energy_kwh")
            self.assertEqual(payload["state_status_path"], "data.status")
            self.assertEqual(payload["state_fault_path"], "data.fault")
            self.assertEqual(payload["enable_url"], "http://adapter.local/charger/enable")
            self.assertEqual(payload["current_url"], "http://adapter.local/charger/current")
            self.assertEqual(payload["phase_url"], "http://adapter.local/charger/phase")
