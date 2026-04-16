# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from shelly_wallbox.backend.shelly_contactor_switch import ShellyContactorSwitchBackend
from shelly_wallbox.backend.shelly_switch import ShellySwitchBackend
from shelly_wallbox.backend.switch_group import SwitchGroupBackend


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

    @staticmethod
    def _write_switch_group_config(directory: str) -> str:
        p1_path = Path(directory) / "phase1-switch.ini"
        p2_path = Path(directory) / "phase2-switch.ini"
        p3_path = Path(directory) / "phase3-switch.ini"
        p1_path.write_text(
            "[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n"
            "[StateRequest]\nMethod=GET\nUrl=/state\n"
            "[StateResponse]\nEnabledPath=enabled\n"
            "[CommandRequest]\nMethod=POST\nUrl=/control\n"
            'JsonTemplate={"enabled": $enabled_json}\n',
            encoding="utf-8",
        )
        p2_path.write_text(
            "[Adapter]\nType=shelly_switch\nHost=192.168.1.22\nComponent=Switch\nId=0\n",
            encoding="utf-8",
        )
        p3_path.write_text(
            "[Adapter]\nType=template_switch\nBaseUrl=http://phase3.local\n"
            "[StateRequest]\nMethod=GET\nUrl=/state\n"
            "[StateResponse]\nEnabledPath=enabled\n"
            "[CommandRequest]\nMethod=POST\nUrl=/control\n"
            'JsonTemplate={"enabled": $enabled_json}\n',
            encoding="utf-8",
        )
        path = Path(directory) / "switch-group.ini"
        path.write_text(
            "[Adapter]\nType=switch_group\n"
            "[Members]\nP1=phase1-switch.ini\nP2=phase2-switch.ini\nP3=phase3-switch.ini\n",
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

    def test_shelly_switch_uses_profile_defaults_for_single_channel_switches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "switch.ini"
            path.write_text(
                "[Adapter]\nType=shelly_switch\nHost=192.168.1.33\nShellyProfile=switch_1ch_with_pm\n",
                encoding="utf-8",
            )
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"output": True}),
                _FakeResponse({}),
            ]
            backend = ShellySwitchBackend(self._service(session), config_path=str(path))

            state = backend.read_switch_state()
            backend.set_enabled(False)

            self.assertEqual(backend.settings.profile_name, "switch_1ch_with_pm")
            self.assertEqual(backend.settings.component, "Switch")
            self.assertTrue(state.enabled)
            self.assertEqual(
                [call.kwargs["url"] for call in session.get.call_args_list],
                [
                    "http://192.168.1.33/rpc/Switch.GetStatus?id=0",
                    "http://192.168.1.33/rpc/Switch.Set?id=0&on=false",
                ],
            )

    def test_switch_group_set_enabled_coordinates_mixed_child_backends(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_switch_group_config(temp_dir)
            session = MagicMock()
            session.get.return_value = _FakeResponse({})
            session.post.return_value = _FakeResponse({})
            backend = SwitchGroupBackend(self._service(session), config_path=config_path)

            backend.set_phase_selection("P1_P2")
            backend.set_enabled(True)

            self.assertEqual(
                [call.kwargs for call in session.post.call_args_list],
                [
                    {
                        "url": "http://phase1.local/control",
                        "timeout": 2.0,
                        "json": {"enabled": True},
                    },
                    {
                        "url": "http://phase3.local/control",
                        "timeout": 2.0,
                        "json": {"enabled": False},
                    },
                ],
            )
            self.assertEqual(
                [call.kwargs["url"] for call in session.get.call_args_list],
                [
                    "http://192.168.1.22/rpc/Switch.Set?id=0&on=true",
                ],
            )

    def test_switch_group_state_infers_phase_selection_from_child_states(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_switch_group_config(temp_dir)
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"enabled": True}),
                _FakeResponse({"output": True}),
                _FakeResponse({"enabled": False}),
            ]
            backend = SwitchGroupBackend(self._service(session), config_path=config_path)

            state = backend.read_switch_state()

            self.assertTrue(state.enabled)
            self.assertEqual(state.phase_selection, "P1_P2")
            self.assertEqual(
                [call.kwargs["url"] for call in session.get.call_args_list],
                [
                    "http://phase1.local/state",
                    "http://192.168.1.22/rpc/Switch.GetStatus?id=0",
                    "http://phase3.local/state",
                ],
            )

    def test_switch_group_aggregates_explicit_feedback_and_interlock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            p1_path = Path(temp_dir) / "phase1-switch.ini"
            p2_path = Path(temp_dir) / "phase2-switch.ini"
            path = Path(temp_dir) / "switch-group.ini"
            p1_path.write_text(
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase1.local\n"
                "[StateRequest]\nMethod=GET\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=data.enabled\nFeedbackClosedPath=data.feedback_closed\nInterlockOkPath=data.interlock_ok\n"
                "[CommandRequest]\nMethod=POST\nUrl=/control\n",
                encoding="utf-8",
            )
            p2_path.write_text(
                "[Adapter]\nType=template_switch\nBaseUrl=http://phase2.local\n"
                "[StateRequest]\nMethod=GET\nUrl=/state\n"
                "[StateResponse]\nEnabledPath=data.enabled\nFeedbackClosedPath=data.feedback_closed\nInterlockOkPath=data.interlock_ok\n"
                "[CommandRequest]\nMethod=POST\nUrl=/control\n",
                encoding="utf-8",
            )
            path.write_text(
                "[Adapter]\nType=switch_group\n"
                "[Members]\nP1=phase1-switch.ini\nP2=phase2-switch.ini\n",
                encoding="utf-8",
            )
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"data": {"enabled": True, "feedback_closed": True, "interlock_ok": True}}),
                _FakeResponse({"data": {"enabled": False, "feedback_closed": False, "interlock_ok": True}}),
            ]
            backend = SwitchGroupBackend(self._service(session), config_path=str(path))

            state = backend.read_switch_state()

            self.assertTrue(state.enabled)
            self.assertEqual(state.phase_selection, "P1")
            self.assertTrue(state.feedback_closed)
            self.assertTrue(state.interlock_ok)
            self.assertEqual(
                [call.kwargs["url"] for call in session.get.call_args_list],
                [
                    "http://phase1.local/state",
                    "http://phase2.local/state",
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

    def test_read_switch_state_exposes_optional_shelly_feedback_and_interlock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "switch.ini"
            path.write_text(
                "[Adapter]\nType=shelly_contactor_switch\nHost=192.168.1.11\nComponent=Switch\nId=0\n"
                "[Feedback]\nComponent=Input\nId=7\nValuePath=state\n"
                "[Interlock]\nComponent=Input\nId=8\nValuePath=state\nInvert=1\n",
                encoding="utf-8",
            )
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"output": True}),
                _FakeResponse({"state": True}),
                _FakeResponse({"state": False}),
            ]
            backend = ShellyContactorSwitchBackend(self._service(session), config_path=str(path))

            state = backend.read_switch_state()

            self.assertTrue(state.enabled)
            self.assertEqual(state.phase_selection, "P1")
            self.assertTrue(state.feedback_closed)
            self.assertTrue(state.interlock_ok)
            urls = [call.kwargs["url"] for call in session.get.call_args_list]
            self.assertEqual(
                urls,
                [
                    "http://192.168.1.11/rpc/Switch.GetStatus?id=0",
                    "http://192.168.1.11/rpc/Input.GetStatus?id=7",
                    "http://192.168.1.11/rpc/Input.GetStatus?id=8",
                ],
            )
