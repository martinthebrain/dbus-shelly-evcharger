# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from dbus_shelly_wallbox_backend_template_charger import TemplateChargerBackend


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class TestShellyWallboxBackendTemplateCharger(unittest.TestCase):
    @staticmethod
    def _service(session: object) -> SimpleNamespace:
        return SimpleNamespace(
            session=session,
            shelly_request_timeout_seconds=2.0,
        )

    @staticmethod
    def _write_config(directory: str, content: str) -> str:
        path = Path(directory) / "template-charger.ini"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_set_enabled_posts_rendered_enable_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable\n"
                '[CurrentRequest]\nMethod=POST\nUrl=/charger/current\nJsonTemplate={"amps": $amps}\n',
            )
            session = MagicMock()
            session.post.return_value = _FakeResponse({})
            backend = TemplateChargerBackend(self._service(session), config_path=config_path)

            backend.set_enabled(True)

            session.post.assert_called_once_with(
                url="http://adapter.local/charger/enable",
                timeout=2.0,
                json={"enabled": True},
            )

    def test_set_current_posts_rendered_current_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable\n"
                '[CurrentRequest]\nMethod=PATCH\nUrl=/charger/current\nJsonTemplate={"amps": $amps}\n',
            )
            session = MagicMock()
            session.patch.return_value = _FakeResponse({})
            backend = TemplateChargerBackend(self._service(session), config_path=config_path)

            backend.set_current(13.5)

            session.patch.assert_called_once_with(
                url="http://adapter.local/charger/current",
                timeout=2.0,
                json={"amps": 13.5},
            )

    def test_set_phase_selection_posts_when_phase_request_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable\n"
                '[CurrentRequest]\nMethod=POST\nUrl=/charger/current\nJsonTemplate={"amps": $amps}\n'
                '[PhaseRequest]\nMethod=PUT\nUrl=/charger/phase\n'
                'JsonTemplate={"phase_selection": "$phase_selection"}\n',
            )
            session = MagicMock()
            session.put.return_value = _FakeResponse({})
            backend = TemplateChargerBackend(self._service(session), config_path=config_path)

            backend.set_phase_selection("P1_P2_P3")

            session.put.assert_called_once_with(
                url="http://adapter.local/charger/phase",
                timeout=2.0,
                json={"phase_selection": "P1_P2_P3"},
            )

    def test_read_charger_state_reads_normalized_response_when_state_request_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "[StateRequest]\nMethod=GET\nUrl=/charger/state\n"
                "[StateResponse]\nEnabledPath=data.enabled\nCurrentPath=data.current\n"
                "PhaseSelectionPath=data.phase_selection\nActualCurrentPath=data.actual_current\n"
                "PowerWattsPath=data.power_w\nEnergyKwhPath=data.energy_kwh\n"
                "StatusPath=data.status\nFaultPath=data.fault\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable\n"
                '[CurrentRequest]\nMethod=POST\nUrl=/charger/current\nJsonTemplate={"amps": $amps}\n'
                '[PhaseRequest]\nMethod=PUT\nUrl=/charger/phase\n'
                'JsonTemplate={"phase_selection": "$phase_selection"}\n',
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "data": {
                        "enabled": True,
                        "current": 13.5,
                        "phase_selection": "P1_P2_P3",
                        "actual_current": 12.8,
                        "power_w": 2950.0,
                        "energy_kwh": 7.125,
                        "status": "charging",
                        "fault": "none",
                    }
                }
            )
            backend = TemplateChargerBackend(self._service(session), config_path=config_path)

            state = backend.read_charger_state()

            session.get.assert_called_once_with(
                url="http://adapter.local/charger/state",
                timeout=2.0,
            )
            self.assertTrue(state.enabled)
            self.assertEqual(state.current_amps, 13.5)
            self.assertEqual(state.phase_selection, "P1_P2_P3")
            self.assertEqual(state.actual_current_amps, 12.8)
            self.assertEqual(state.power_w, 2950.0)
            self.assertEqual(state.energy_kwh, 7.125)
            self.assertEqual(state.status_text, "charging")
            self.assertEqual(state.fault_text, "none")

    def test_read_charger_state_falls_back_to_command_cache_without_state_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable\n"
                '[CurrentRequest]\nMethod=POST\nUrl=/charger/current\nJsonTemplate={"amps": $amps}\n'
                '[PhaseRequest]\nMethod=PUT\nUrl=/charger/phase\n'
                'JsonTemplate={"phase_selection": "$phase_selection"}\n',
            )
            session = MagicMock()
            session.post.return_value = _FakeResponse({})
            session.put.return_value = _FakeResponse({})
            backend = TemplateChargerBackend(self._service(session), config_path=config_path)

            backend.set_enabled(True)
            backend.set_current(11.0)
            backend.set_phase_selection("P1_P2")
            state = backend.read_charger_state()

            self.assertTrue(state.enabled)
            self.assertEqual(state.current_amps, 11.0)
            self.assertEqual(state.phase_selection, "P1_P2")

    def test_multi_phase_template_charger_requires_phase_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable\n"
                '[CurrentRequest]\nMethod=POST\nUrl=/charger/current\nJsonTemplate={"amps": $amps}\n',
            )

            with self.assertRaises(ValueError):
                TemplateChargerBackend(self._service(MagicMock()), config_path=config_path)
