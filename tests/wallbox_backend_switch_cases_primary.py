# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace
from typing import Any, cast

from shelly_wallbox.backend.shelly_combined import ShellyCombinedBackend
from shelly_wallbox.backend.shelly_switch import ShellySwitchBackend
from shelly_wallbox.backend.switch_group import SwitchGroupBackend
from tests.wallbox_backend_switch_support import SwitchBackendTestCaseBase, _FakeResponse, MagicMock, tempfile


class TestShellyWallboxBackendSwitchPrimary(SwitchBackendTestCaseBase):
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

    def test_shelly_switch_rejects_unsupported_phase_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_switch_config(temp_dir)
            backend = ShellySwitchBackend(self._service(MagicMock()), config_path=config_path)

            with self.assertRaisesRegex(ValueError, "Unsupported phase selection"):
                backend.set_phase_selection(cast(Any, "P9"))

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

    def test_shelly_switch_returns_last_selected_phase_when_no_channel_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_switch_config(temp_dir)
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"output": False}),
                _FakeResponse({"output": False}),
                _FakeResponse({"output": False}),
            ]
            backend = ShellySwitchBackend(self._service(session), config_path=config_path)
            backend.set_phase_selection("P1_P2")

            state = backend.read_switch_state()

            self.assertFalse(state.enabled)
            self.assertEqual(state.phase_selection, "P1_P2")

    def test_combined_backend_phase_distribution_and_pending_switch_state(self) -> None:
        service = SimpleNamespace(
            phase="L3",
            max_current=16.0,
            _last_voltage=230.0,
            fetch_pm_status=MagicMock(return_value={"output": False}),
            _peek_pending_relay_command=MagicMock(return_value=(1, 101.0)),
            _last_pm_status={"output": False},
            set_relay=MagicMock(),
        )
        backend = ShellyCombinedBackend(service)

        self.assertEqual(ShellyCombinedBackend._distributed_phase_value(9.0, "3P"), (3.0, 3.0, 3.0))
        self.assertEqual(ShellyCombinedBackend._distributed_phase_value(9.0, "L2"), (0.0, 9.0, 0.0))
        self.assertEqual(ShellyCombinedBackend._distributed_phase_value(9.0, "L3"), (0.0, 0.0, 9.0))
        self.assertTrue(backend.read_switch_state().enabled)
        backend.set_enabled(True)
        service.set_relay.assert_called_once_with(True)

    def test_combined_backend_covers_meter_capability_and_guard_paths(self) -> None:
        service = SimpleNamespace(
            phase=None,
            max_current=None,
            _last_voltage=None,
            fetch_pm_status=MagicMock(return_value={"aenergy": {}, "apower": "bad", "voltage": "bad"}),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_pm_status=object(),
            set_relay=MagicMock(),
        )
        backend = ShellyCombinedBackend(service)

        self.assertEqual(ShellyCombinedBackend._phase_selection_for_service(None), "P1")
        self.assertEqual(ShellyCombinedBackend._phase_powers(9.0, None), (9.0, 0.0, 0.0))
        self.assertIsNone(ShellyCombinedBackend._phase_currents(None, "L1"))

        meter = backend.read_meter()
        self.assertIsNone(meter.relay_on)
        self.assertEqual(meter.power_w, 0.0)
        self.assertEqual(meter.energy_kwh, 0.0)
        self.assertEqual(meter.phase_selection, "P1")

        capabilities = backend.capabilities()
        self.assertIsNone(capabilities.max_direct_switch_power_w)
        self.assertFalse(backend.read_switch_state().enabled)

        backend.set_phase_selection("P1")
        with self.assertRaisesRegex(ValueError, "single-phase"):
            backend.set_phase_selection("P1_P2")

    def test_combined_backend_covers_positive_current_and_direct_power_limit(self) -> None:
        service = SimpleNamespace(
            phase="3P",
            max_current=16.0,
            _last_voltage=230.0,
            fetch_pm_status=MagicMock(
                return_value={
                    "output": True,
                    "apower": 6900.0,
                    "current": 12.0,
                    "voltage": 230.0,
                    "aenergy": {"total": 1200.0},
                }
            ),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
            _last_pm_status={"output": True},
            set_relay=MagicMock(),
        )
        backend = ShellyCombinedBackend(service)

        self.assertEqual(ShellyCombinedBackend._phase_currents(12.0, "3P"), (4.0, 4.0, 4.0))
        self.assertEqual(backend.read_meter().phase_currents_a, (4.0, 4.0, 4.0))
        self.assertEqual(backend.capabilities().max_direct_switch_power_w, 3680.0)

    def test_shelly_switch_returns_selected_phase_when_active_channels_do_not_match_any_map(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_switch_config(temp_dir)
            session = MagicMock()
            session.get.side_effect = [
                _FakeResponse({"output": False}),
                _FakeResponse({"output": False}),
                _FakeResponse({"output": True}),
            ]
            backend = ShellySwitchBackend(self._service(session), config_path=config_path)
            backend.set_phase_selection("P1_P2")

            state = backend.read_switch_state()

            self.assertTrue(state.enabled)
            self.assertEqual(state.phase_selection, "P1_P2")

    def test_shelly_switch_uses_profile_defaults_for_single_channel_switches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write_switch_config(temp_dir).replace("switch.ini", "profile-switch.ini")
            from pathlib import Path
            Path(path).write_text(
                "[Adapter]\nType=shelly_switch\nHost=192.168.1.33\nShellyProfile=switch_1ch_with_pm\n",
                encoding="utf-8",
            )
            session = MagicMock()
            session.get.side_effect = [_FakeResponse({"output": True}), _FakeResponse({})]
            backend = ShellySwitchBackend(self._service(session), config_path=str(path))

            state = backend.read_switch_state()
            backend.set_enabled(False)

            self.assertEqual(backend.settings.profile_name, "switch_1ch_with_pm")
            self.assertTrue(state.enabled)

    def test_switch_group_set_enabled_coordinates_mixed_child_backends(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_switch_group_config(temp_dir)
            session = MagicMock()
            session.get.return_value = _FakeResponse({})
            session.post.return_value = _FakeResponse({})
            backend = SwitchGroupBackend(self._service(session), config_path=config_path)

            backend.set_phase_selection("P1_P2")
            backend.set_enabled(True)

            self.assertEqual(len(session.post.call_args_list), 2)
            self.assertEqual(
                [call.kwargs["url"] for call in session.get.call_args_list],
                ["http://192.168.1.22/rpc/Switch.Set?id=0&on=true"],
            )
