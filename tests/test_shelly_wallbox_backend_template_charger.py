# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from shelly_wallbox.backend.template_charger import (
    TemplateChargerBackend,
    TemplateChargerSettings,
    _TemplateChargerCachedState,
    load_template_charger_settings,
)
from shelly_wallbox.backend.template_support import TemplateAuthSettings


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

    def test_set_enabled_supports_custom_auth_header(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "AuthHeaderName=Authorization\nAuthHeaderValue=Bearer token\n"
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
                headers={"Authorization": "Bearer token"},
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

    def test_template_charger_helper_edges_cover_timeouts_state_fallbacks_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\nRequestTimeoutSeconds=0\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable\n"
                "[CurrentRequest]\nMethod=POST\nUrl=/charger/current\n",
            )
            settings = load_template_charger_settings(self._service(MagicMock()), config_path)
            self.assertEqual(settings.timeout_seconds, 2.0)

        self.assertIsNone(TemplateChargerBackend._enabled_state(None))
        self.assertFalse(TemplateChargerBackend._enabled_state(0))
        self.assertTrue(TemplateChargerBackend._enabled_state(1))
        self.assertTrue(TemplateChargerBackend._enabled_state("enabled"))
        self.assertFalse(TemplateChargerBackend._enabled_state("disabled"))
        self.assertIsNone(TemplateChargerBackend._enabled_state("maybe"))
        self.assertIsNone(TemplateChargerBackend._optional_text(None))
        self.assertIsNone(TemplateChargerBackend._optional_text("  "))
        self.assertIsNone(TemplateChargerBackend._payload_float({}, None))

        backend = TemplateChargerBackend.__new__(TemplateChargerBackend)
        backend.settings = TemplateChargerSettings(
            base_url="http://charger.local",
            auth_settings=TemplateAuthSettings("", "", False, None, None),
            timeout_seconds=2.0,
            supported_phase_selections=("P1",),
            state_method="GET",
            state_url=None,
            state_enabled_path=None,
            state_current_path=None,
            state_phase_selection_path=None,
            state_actual_current_path=None,
            state_power_watts_path=None,
            state_energy_kwh_path=None,
            state_status_path=None,
            state_fault_path=None,
            enable_method="POST",
            enable_url="/enable",
            enable_json_template=None,
            current_method="POST",
            current_url="/current",
            current_json_template=None,
            phase_method="POST",
            phase_url=None,
            phase_json_template=None,
        )
        cached = _TemplateChargerCachedState(enabled=True, current_amps=10.0, phase_selection="P1")
        self.assertEqual(backend._payload_phase_selection({}, cached), "P1")
        self.assertIsNone(backend._payload_text({}, None))

        with self.assertRaisesRegex(ValueError, "Unsupported charger current"):
            backend.set_current(-1.0)
        with self.assertRaisesRegex(ValueError, "Unsupported phase selection"):
            backend.set_phase_selection("P1_P2")
        backend.set_phase_selection("P1")

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable\n"
                "[CurrentRequest]\nMethod=POST\nUrl=/charger/current\n"
                "[PhaseRequest]\nMethod=POST\nUrl=/charger/phase\n",
            )
            backend = TemplateChargerBackend(
                SimpleNamespace(session=MagicMock(), shelly_request_timeout_seconds=2.0, requested_phase_selection="P1_P2_P3"),
                config_path=config_path,
            )
            self.assertEqual(backend._phase_selection_cache, "P1")

    def test_template_charger_requires_enable_and_current_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_enable = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[EnableRequest]\nMethod=POST\n"
                "[CurrentRequest]\nMethod=POST\nUrl=/charger/current\n",
            )
            with self.assertRaisesRegex(ValueError, r"requires \[EnableRequest\] Url"):
                TemplateChargerBackend(self._service(MagicMock()), config_path=missing_enable)

        with tempfile.TemporaryDirectory() as temp_dir:
            missing_current = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_charger\nBaseUrl=http://adapter.local\n"
                "[EnableRequest]\nMethod=POST\nUrl=/charger/enable\n"
                "[CurrentRequest]\nMethod=POST\n",
            )
            with self.assertRaisesRegex(ValueError, r"requires \[CurrentRequest\] Url"):
                TemplateChargerBackend(self._service(MagicMock()), config_path=missing_current)
