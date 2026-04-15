# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from dbus_shelly_wallbox_backend_shelly_contactor_switch import ShellyContactorSwitchBackend
from dbus_shelly_wallbox_backend_shelly_switch import ShellySwitchBackend


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class TestShellyWallboxBackendSwitch(unittest.TestCase):
    @staticmethod
    def _service(session: object) -> SimpleNamespace:
        return SimpleNamespace(
            session=session,
            host="192.168.1.11",
            username="",
            password="",
            use_digest_auth=False,
            shelly_request_timeout_seconds=2.0,
            pm_component="Switch",
            pm_id=0,
            phase="L1",
            max_current=16.0,
            _last_voltage=230.0,
        )

    @staticmethod
    def _write_switch_config(directory: str) -> str:
        path = Path(directory) / "switch.ini"
        path.write_text(
            "[Adapter]\nType=shelly_switch\nHost=192.168.1.11\nComponent=Switch\nId=0\n"
            "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2,P1_P2_P3\n"
            "[PhaseMap]\nP1=0\nP1_P2=0,1\nP1_P2_P3=0,1,2\n",
            encoding="utf-8",
        )
        return str(path)

    def test_set_enabled_uses_phase_map_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_switch_config(temp_dir)
            session = MagicMock()
            session.get.return_value = _FakeResponse({})
            backend = ShellySwitchBackend(self._service(session), config_path=config_path)

            backend.set_phase_selection("P1_P2")
            backend.set_enabled(True)

            urls = [call.kwargs["url"] for call in session.get.call_args_list]
            self.assertEqual(
                urls,
                [
                    "http://192.168.1.11/rpc/Switch.Set?id=0&on=true",
                    "http://192.168.1.11/rpc/Switch.Set?id=1&on=true",
                    "http://192.168.1.11/rpc/Switch.Set?id=2&on=false",
                ],
            )

    def test_read_switch_state_infers_phase_selection_from_active_channels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_switch_config(temp_dir)
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"output": True}),
                _FakeResponse({"output": True}),
                _FakeResponse({"output": False}),
            ]
            backend = ShellySwitchBackend(self._service(session), config_path=config_path)

            state = backend.read_switch_state()

            self.assertTrue(state.enabled)
            self.assertEqual(state.phase_selection, "P1_P2")
            urls = [call.kwargs["url"] for call in session.get.call_args_list]
            self.assertEqual(
                urls,
                [
                    "http://192.168.1.11/rpc/Switch.GetStatus?id=0",
                    "http://192.168.1.11/rpc/Switch.GetStatus?id=1",
                    "http://192.168.1.11/rpc/Switch.GetStatus?id=2",
                ],
            )

    def test_contactor_mode_has_no_direct_switch_power_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "switch.ini"
            path.write_text(
                "[Adapter]\nType=shelly_switch\nHost=192.168.1.11\nComponent=Switch\nId=0\n"
                "[Capabilities]\nSwitchingMode=contactor\nSupportedPhaseSelections=P1\n",
                encoding="utf-8",
            )
            session = MagicMock()
            backend = ShellySwitchBackend(self._service(session), config_path=str(path))

            capabilities = backend.capabilities()

            self.assertEqual(capabilities.switching_mode, "contactor")
            self.assertIsNone(capabilities.max_direct_switch_power_w)

    def test_contactor_switch_backend_defaults_to_contactor_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "switch.ini"
            path.write_text(
                "[Adapter]\nType=shelly_contactor_switch\nHost=192.168.1.11\nComponent=Switch\nId=0\n",
                encoding="utf-8",
            )
            session = MagicMock()
            backend = ShellyContactorSwitchBackend(self._service(session), config_path=str(path))

            capabilities = backend.capabilities()

            self.assertEqual(capabilities.switching_mode, "contactor")
            self.assertIsNone(capabilities.max_direct_switch_power_w)
