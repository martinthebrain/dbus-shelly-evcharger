# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from shelly_wallbox.backend.template_switch import TemplateSwitchBackend


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class TestShellyWallboxBackendTemplateSwitch(unittest.TestCase):
    @staticmethod
    def _service(session: object) -> SimpleNamespace:
        return SimpleNamespace(
            session=session,
            requested_phase_selection="P1",
            shelly_request_timeout_seconds=2.0,
        )

    @staticmethod
    def _write_config(directory: str, content: str) -> str:
        path = Path(directory) / "template-switch.ini"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_read_switch_state_uses_normalized_state_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2_P3\n"
                "[StateRequest]\nMethod=GET\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=data.enabled\nPhaseSelectionPath=data.phase_selection\n"
                "[CommandRequest]\nMethod=POST\nUrl=/switch/control\n"
                "[PhaseRequest]\nMethod=PUT\nUrl=/switch/phase\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {"data": {"enabled": True, "phase_selection": "P1_P2_P3"}}
            )
            backend = TemplateSwitchBackend(self._service(session), config_path=config_path)

            state = backend.read_switch_state()

            self.assertTrue(state.enabled)
            self.assertEqual(state.phase_selection, "P1_P2_P3")
            session.get.assert_called_once_with(
                url="http://adapter.local/switch/state",
                timeout=2.0,
            )

    def test_read_switch_state_exposes_optional_feedback_and_interlock_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1\nSwitchingMode=contactor\n"
                "[StateRequest]\nMethod=GET\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=data.enabled\nFeedbackClosedPath=data.feedback_closed\nInterlockOkPath=data.interlock_ok\n"
                "[CommandRequest]\nMethod=POST\nUrl=/switch/control\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {"data": {"enabled": True, "feedback_closed": False, "interlock_ok": True}}
            )
            backend = TemplateSwitchBackend(self._service(session), config_path=config_path)

            state = backend.read_switch_state()

            self.assertTrue(state.enabled)
            self.assertEqual(state.phase_selection, "P1")
            self.assertFalse(state.feedback_closed)
            self.assertTrue(state.interlock_ok)

    def test_read_switch_state_supports_digest_auth_and_custom_header(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "Username=user\nPassword=secret\nDigestAuth=1\n"
                "AuthHeaderName=Authorization\nAuthHeaderValue=Bearer token\n"
                "[Capabilities]\nSupportedPhaseSelections=P1\n"
                "[StateRequest]\nMethod=GET\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=data.enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/switch/control\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"data": {"enabled": True}})
            with patch("shelly_wallbox.backend.template_support.HTTPDigestAuth", return_value="digest-auth") as digest_auth:
                backend = TemplateSwitchBackend(self._service(session), config_path=config_path)

                state = backend.read_switch_state()

            self.assertTrue(state.enabled)
            digest_auth.assert_called_once_with("user", "secret")
            session.get.assert_called_once_with(
                url="http://adapter.local/switch/state",
                timeout=2.0,
                auth="digest-auth",
                headers={"Authorization": "Bearer token"},
            )

    def test_set_enabled_posts_rendered_json_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/switch/control\n"
                'JsonTemplate={"enabled": $enabled_json, "phase_selection": "$phase_selection"}\n',
            )
            session = MagicMock()
            session.post.return_value = _FakeResponse({})
            backend = TemplateSwitchBackend(self._service(session), config_path=config_path)

            backend.set_enabled(True)

            session.post.assert_called_once_with(
                url="http://adapter.local/switch/control",
                timeout=2.0,
                json={"enabled": True, "phase_selection": "P1"},
            )

    def test_set_phase_selection_posts_when_phase_request_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/switch/control\n"
                "[PhaseRequest]\nMethod=PUT\nUrl=/switch/phase\n"
                'JsonTemplate={"phase_selection": "$phase_selection"}\n',
            )
            session = MagicMock()
            session.put.return_value = _FakeResponse({})
            backend = TemplateSwitchBackend(self._service(session), config_path=config_path)

            backend.set_phase_selection("P1_P2")

            session.put.assert_called_once_with(
                url="http://adapter.local/switch/phase",
                timeout=2.0,
                json={"phase_selection": "P1_P2"},
            )

    def test_multi_phase_template_switch_requires_phase_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_switch\nBaseUrl=http://adapter.local\n"
                "[Capabilities]\nSupportedPhaseSelections=P1,P1_P2\n"
                "[StateRequest]\nUrl=/switch/state\n"
                "[StateResponse]\nEnabledPath=enabled\n"
                "[CommandRequest]\nMethod=POST\nUrl=/switch/control\n",
            )

            with self.assertRaises(ValueError):
                TemplateSwitchBackend(self._service(MagicMock()), config_path=config_path)
