# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

from shelly_wallbox.backend.probe import main, validate_backend_config, validate_wallbox_config
from shelly_wallbox.backend.modbus_transport import ModbusRequest


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _FakeSimpleEvseTransport:
    def __init__(self) -> None:
        self.holding_registers: dict[int, int] = {
            1000: 16,
            1001: 13,
            1002: 3,
            1004: 0,
            1006: 2,
            1007: 1,
        }

    def exchange(self, request: ModbusRequest, *, timeout_seconds: float) -> bytes:
        if request.function_code == 0x03:
            address = int.from_bytes(request.payload[0:2], "big")
            count = int.from_bytes(request.payload[2:4], "big")
            payload = b"".join(
                int(self.holding_registers.get(address + index, 0)).to_bytes(2, "big")
                for index in range(count)
            )
            return bytes((0x03, len(payload))) + payload
        raise AssertionError(f"Unexpected Modbus function code {request.function_code}")


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

    def test_validate_backend_config_accepts_switch_group_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write_config(
                temp_dir,
                "phase1-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n"
                "[StateRequest]\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/control\n",
            )
            switch_path = self._write_config(
                temp_dir,
                "switch.ini",
                "[Adapter]\nType=switch_group\n"
                "[Members]\nP1=phase1-switch.ini\n",
            )

            payload = validate_backend_config(switch_path)

            self.assertEqual(payload["type"], "switch_group")
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

    def test_validate_backend_config_accepts_goe_charger_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=goe_charger\nBaseUrl=http://goe.local\n",
            )

            payload = validate_backend_config(charger_path)

            self.assertEqual(payload["type"], "goe_charger")
            self.assertEqual(payload["roles"], ["charger"])

    def test_validate_backend_config_accepts_modbus_charger_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.40\nPort=502\nUnitId=7\n"
                "[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n",
            )

            payload = validate_backend_config(charger_path)

            self.assertEqual(payload["type"], "modbus_charger")
            self.assertEqual(payload["roles"], ["charger"])

    def test_validate_backend_config_accepts_simpleevse_charger_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.50\nPort=502\nUnitId=1\n",
            )

            payload = validate_backend_config(charger_path)

            self.assertEqual(payload["type"], "simpleevse_charger")
            self.assertEqual(payload["roles"], ["charger"])

    def test_probe_modbus_charger_prints_transport_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=modbus_charger\nProfile=generic\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.40\nPort=502\nUnitId=7\n"
                "[EnableWrite]\nRegisterType=coil\nAddress=20\nTrueValue=1\nFalseValue=0\n"
                "[CurrentWrite]\nRegisterType=holding\nAddress=30\nDataType=uint16\nScale=10\n",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(["probe-charger", charger_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "modbus_charger")
            self.assertEqual(payload["profile_name"], "generic")
            self.assertEqual(payload["transport_kind"], "tcp")
            self.assertEqual(payload["transport_unit_id"], 7)
            self.assertIsNone(payload["transport_device"])
            self.assertEqual(payload["transport_serial_port_owner"], "none")
            self.assertEqual(payload["transport_serial_retry_count"], 0)

    def test_probe_simpleevse_charger_prints_transport_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.50\nPort=502\nUnitId=1\n",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(["probe-charger", charger_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "simpleevse_charger")
            self.assertEqual(payload["profile_name"], "simpleevse")
            self.assertEqual(payload["transport_kind"], "tcp")
            self.assertEqual(payload["transport_unit_id"], 1)
            self.assertIsNone(payload["transport_device"])
            self.assertEqual(payload["transport_serial_port_owner"], "none")
            self.assertEqual(payload["transport_serial_retry_count"], 0)

    def test_probe_simpleevse_charger_prints_serial_owner_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=simpleevse_charger\nTransport=serial_rtu\n"
                "[Transport]\nDevice=/dev/ttyS7\nBaudrate=9600\nParity=N\nStopBits=1\nUnitId=1\n"
                "PortOwner=venus\nRetryCount=2\nRetryDelaySeconds=0.5\n",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(["probe-charger", charger_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "simpleevse_charger")
            self.assertEqual(payload["transport_kind"], "serial_rtu")
            self.assertEqual(payload["transport_device"], "/dev/ttyS7")
            self.assertEqual(payload["transport_serial_port_owner"], "venus_serial_starter")
            self.assertEqual(payload["transport_serial_retry_count"], 2)
            self.assertEqual(payload["transport_serial_retry_delay_seconds"], 0.5)

    def test_read_simpleevse_charger_returns_live_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=simpleevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.50\nPort=502\nUnitId=1\n",
            )

            stdout = io.StringIO()
            with (
                redirect_stdout(stdout),
                patch(
                    "shelly_wallbox.backend.simpleevse_charger.create_modbus_transport",
                    return_value=_FakeSimpleEvseTransport(),
                ),
            ):
                rc = main(["read-charger", charger_path])

            payload = json.loads(stdout.getvalue())
            charger_state = cast(dict[str, object], payload["charger_state"])
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "simpleevse_charger")
            self.assertEqual(charger_state["enabled"], True)
            self.assertEqual(charger_state["current_amps"], 16.0)
            self.assertEqual(charger_state["actual_current_amps"], 13.0)
            self.assertEqual(charger_state["status_text"], "charging")

    def test_validate_wallbox_config_accepts_meterless_split_with_charger(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[EnableRequest]\nUrl=/charger/enable\n"
                "[CurrentRequest]\nUrl=/charger/current\n",
            )
            wallbox_path = self._write_config(
                temp_dir,
                "config.ini",
                "[DEFAULT]\nHost=192.168.1.20\n"
                "[Backends]\nMode=split\nMeterType=none\nSwitchType=shelly_combined\n"
                f"ChargerType=template_charger\nChargerConfigPath={charger_path}\n",
            )

            payload = validate_wallbox_config(wallbox_path)
            selection = cast(dict[str, object], payload["selection"])

            self.assertEqual(selection["mode"], "split")
            self.assertEqual(selection["meter_type"], "none")
            self.assertEqual(selection["charger_type"], "template_charger")
            self.assertEqual(payload["resolved_roles"], {"meter": False, "switch": True, "charger": True})

    def test_validate_wallbox_config_accepts_simpleevse_plus_switch_group_topology(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            phase1_path = self._write_config(
                temp_dir,
                "phase1-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n"
                "[StateRequest]\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/control\n",
            )
            phase2_path = self._write_config(
                temp_dir,
                "phase2-switch.ini",
                "[Adapter]\nType=shelly_switch\nHost=192.168.1.61\n",
            )
            phase3_path = self._write_config(
                temp_dir,
                "phase3-switch.ini",
                "[Adapter]\nType=shelly_contactor_switch\nHost=192.168.1.62\n",
            )
            switch_path = self._write_config(
                temp_dir,
                "switch-group.ini",
                "[Adapter]\nType=switch_group\n"
                f"[Members]\nP1={Path(phase1_path).name}\nP2={Path(phase2_path).name}\nP3={Path(phase3_path).name}\n",
            )
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=simpleevse_charger\nTransport=serial_rtu\n"
                "[Transport]\nDevice=/dev/ttyUSB0\nBaudrate=9600\nParity=N\nStopBits=1\nUnitId=1\n",
            )
            wallbox_path = self._write_config(
                temp_dir,
                "config.ini",
                "[DEFAULT]\nHost=192.168.1.20\n"
                "[Backends]\nMode=split\nMeterType=none\nSwitchType=switch_group\n"
                f"SwitchConfigPath={switch_path}\n"
                "ChargerType=simpleevse_charger\n"
                f"ChargerConfigPath={charger_path}\n",
            )

            payload = validate_wallbox_config(wallbox_path)
            selection = cast(dict[str, object], payload["selection"])

            self.assertEqual(selection["mode"], "split")
            self.assertEqual(selection["meter_type"], "none")
            self.assertEqual(selection["switch_type"], "switch_group")
            self.assertEqual(selection["charger_type"], "simpleevse_charger")
            self.assertEqual(payload["resolved_roles"], {"meter": False, "switch": True, "charger": True})

    def test_validate_wallbox_config_rejects_invalid_meterless_split_without_charger(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            wallbox_path = self._write_config(
                temp_dir,
                "config.ini",
                "[DEFAULT]\nHost=192.168.1.20\n"
                "[Backends]\nMode=split\nMeterType=none\nSwitchType=shelly_combined\nChargerType=\n",
            )

            with self.assertRaisesRegex(ValueError, "MeterType=none requires a configured charger backend"):
                validate_wallbox_config(wallbox_path)

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

    def test_probe_switch_group_prints_phase_member_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write_config(
                temp_dir,
                "phase1-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n"
                "[StateRequest]\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/control\n",
            )
            self._write_config(
                temp_dir,
                "phase2-switch.ini",
                "[Adapter]\nType=shelly_switch\nHost=192.168.1.22\nComponent=Switch\nId=0\n",
            )
            self._write_config(
                temp_dir,
                "phase3-switch.ini",
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase3.local\n"
                "[StateRequest]\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nUrl=/control\n",
            )
            switch_path = self._write_config(
                temp_dir,
                "switch.ini",
                "[Adapter]\nType=switch_group\n"
                "[Members]\nP1=phase1-switch.ini\nP2=phase2-switch.ini\nP3=phase3-switch.ini\n",
            )
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"enabled": True}),
                _FakeResponse({"output": True}),
                _FakeResponse({"enabled": True}),
            ]

            stdout = io.StringIO()
            with patch("shelly_wallbox.backend.probe.requests.Session", return_value=session):
                with redirect_stdout(stdout):
                    rc = main(["probe-switch", switch_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "switch_group")
            self.assertEqual(
                payload["phase_switch_targets"],
                {"P1": ["P1"], "P1_P2": ["P1", "P2"], "P1_P2_P3": ["P1", "P2", "P3"]},
            )
            self.assertEqual(payload["phase_members"]["P2"]["backend_type"], "shelly_switch")
            self.assertEqual(payload["switch_state"]["phase_selection"], "P1_P2_P3")
            self.assertTrue(payload["switch_state"]["enabled"])

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

    def test_probe_goe_charger_prints_normalized_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=goe_charger\nBaseUrl=http://goe.local\n",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(["probe-charger", charger_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["type"], "goe_charger")
            self.assertEqual(payload["supported_phase_selections"], ["P1"])
            self.assertEqual(payload["state_url"], "http://goe.local/api/status")
            self.assertEqual(payload["enable_url"], "http://goe.local/api/set")
            self.assertEqual(payload["current_url"], "http://goe.local/api/set")
            self.assertIsNone(payload["phase_url"])

    def test_validate_wallbox_command_prints_normalized_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            charger_path = self._write_config(
                temp_dir,
                "charger.ini",
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[EnableRequest]\nUrl=/charger/enable\n"
                "[CurrentRequest]\nUrl=/charger/current\n",
            )
            wallbox_path = self._write_config(
                temp_dir,
                "config.ini",
                "[DEFAULT]\nHost=192.168.1.20\n"
                "[Backends]\nMode=split\nMeterType=none\nSwitchType=none\n"
                f"ChargerType=template_charger\nChargerConfigPath={charger_path}\n",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(["validate-wallbox", wallbox_path])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["selection"]["mode"], "split")
            self.assertEqual(payload["selection"]["meter_type"], "none")
            self.assertEqual(payload["selection"]["switch_type"], "none")
            self.assertEqual(payload["selection"]["charger_type"], "template_charger")
            self.assertEqual(payload["resolved_roles"], {"meter": False, "switch": False, "charger": True})
