# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_write_controller_support import *
from venus_evcharger.control import ControlApiV1Service, ControlCommand


class TestControlApiV1(DbusWriteControllerTestBase):
    def test_command_for_dbus_write_maps_canonical_commands(self) -> None:
        api = ControlApiV1Service(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=DbusWriteController.AUTO_RUNTIME_SETTING_PATHS,
        )

        mode_command = api.command_for_dbus_write("/Mode", 1)
        current_command = api.command_for_dbus_write("/SetCurrent", 12.5)
        auto_command = api.command_for_dbus_write("/Auto/StartSurplusWatts", 1800.0)
        unknown_command = api.command_for_dbus_write("/UnknownPath", 1)

        self.assertEqual(mode_command.name, "set_mode")
        self.assertEqual(current_command.name, "set_current_setting")
        self.assertEqual(auto_command.name, "set_auto_runtime_setting")
        self.assertEqual(unknown_command.name, "legacy_unknown_write")
        self.assertEqual(unknown_command.source, "dbus")

    def test_command_for_write_preserves_transport_source(self) -> None:
        api = ControlApiV1Service(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=DbusWriteController.AUTO_RUNTIME_SETTING_PATHS,
        )

        command = api.command_for_write("/Mode", 1, source="mqtt")

        self.assertEqual(command.name, "set_mode")
        self.assertEqual(command.source, "mqtt")

    def test_command_from_payload_accepts_canonical_name_and_default_path(self) -> None:
        api = ControlApiV1Service(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=DbusWriteController.AUTO_RUNTIME_SETTING_PATHS,
        )

        command = api.command_from_payload(
            {"name": "set_mode", "value": 1, "command_id": "cmd-1", "idempotency_key": "idem-1"},
            source="http",
        )

        self.assertEqual(command.name, "set_mode")
        self.assertEqual(command.path, "/Mode")
        self.assertEqual(command.source, "http")
        self.assertEqual(command.command_id, "cmd-1")
        self.assertEqual(command.idempotency_key, "idem-1")

    def test_command_from_payload_requires_explicit_path_for_runtime_setting_commands(self) -> None:
        api = ControlApiV1Service(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=DbusWriteController.AUTO_RUNTIME_SETTING_PATHS,
        )

        with self.assertRaisesRegex(ValueError, "requires an explicit 'path'"):
            api.command_from_payload({"name": "set_current_setting", "value": 12.5}, source="http")

    def test_command_from_payload_rejects_unknown_command_names(self) -> None:
        api = ControlApiV1Service(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=DbusWriteController.AUTO_RUNTIME_SETTING_PATHS,
        )

        with self.assertRaisesRegex(ValueError, "Unsupported control command"):
            api.command_from_payload({"name": "set_everything_on", "value": 1}, source="http")

    def test_command_from_payload_accepts_explicit_path_and_rejects_missing_shape(self) -> None:
        api = ControlApiV1Service(
            current_setting_paths=DbusWriteController.CURRENT_SETTING_PATHS,
            auto_runtime_setting_paths=DbusWriteController.AUTO_RUNTIME_SETTING_PATHS,
        )

        command = api.command_from_payload(
            {"name": "set_current_setting", "path": "/SetCurrent", "value": 10.0},
            source="http",
        )

        self.assertEqual(command.path, "/SetCurrent")
        path_command = api.command_from_payload({"path": "/Mode", "value": 1}, source="http")
        self.assertEqual(path_command.name, "set_mode")
        with self.assertRaisesRegex(ValueError, "must include either 'name' or 'path'"):
            api.command_from_payload({}, source="http")

    def test_build_control_command_from_payload_delegates_to_control_api(self) -> None:
        service = SimpleNamespace(
            virtual_autostart=0,
            _dbusservice={"/AutoStart": 0},
            _time_now=MagicMock(return_value=10.0),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
            _save_runtime_overrides=MagicMock(),
        )
        controller = DbusWriteController(WriteControllerPort(service))

        command = controller.build_control_command_from_payload({"name": "set_mode", "value": 1}, source="http")

        self.assertEqual(command.name, "set_mode")
        self.assertEqual(command.path, "/Mode")
        self.assertEqual(command.source, "http")

    def test_handle_control_command_returns_applied_result(self) -> None:
        service = SimpleNamespace(
            virtual_autostart=0,
            _dbusservice={"/AutoStart": 0},
            _time_now=MagicMock(return_value=10.0),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(),
            _save_runtime_overrides=MagicMock(),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)
        controller = DbusWriteController(WriteControllerPort(service))
        command = ControlCommand(name="set_auto_start", path="/AutoStart", value=1)

        result = controller.handle_control_command(command)

        self.assertTrue(result.accepted)
        self.assertTrue(result.applied)
        self.assertTrue(result.persisted)
        self.assertEqual(result.status, "applied")
        self.assertFalse(result.external_side_effect_started)
        self.assertEqual(service.virtual_autostart, 1)
        self.assertEqual(service._dbusservice["/AutoStart"], 1)
        service._save_runtime_state.assert_called_once()
        service._save_runtime_overrides.assert_called_once()

    def test_handle_control_command_returns_rejected_result_for_reversible_failures(self) -> None:
        service = SimpleNamespace(
            virtual_autostart=0,
            _dbusservice={"/AutoStart": 0},
            _time_now=MagicMock(return_value=10.0),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(side_effect=RuntimeError("save failed")),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)
        controller = DbusWriteController(WriteControllerPort(service))
        command = ControlCommand(name="set_auto_start", path="/AutoStart", value=1)

        result = controller.handle_control_command(command)

        self.assertFalse(result.accepted)
        self.assertFalse(result.applied)
        self.assertFalse(result.persisted)
        self.assertTrue(result.reversible_failure)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.detail, "save failed")
        self.assertEqual(service.virtual_autostart, 0)
        self.assertEqual(service._dbusservice["/AutoStart"], 0)

    def test_handle_control_command_returns_in_flight_result_after_external_side_effects(self) -> None:
        backend = SimpleNamespace(set_current=MagicMock())
        service = SimpleNamespace(
            virtual_set_current=6.0,
            _charger_backend=backend,
            _dbusservice={"/SetCurrent": 6.0},
            _time_now=MagicMock(return_value=10.0),
            _publish_dbus_path=MagicMock(),
            _state_summary=self._state_summary,
            _save_runtime_state=MagicMock(side_effect=RuntimeError("save failed")),
        )
        service._publish_dbus_path.side_effect = self._publish_side_effect(service)
        controller = DbusWriteController(WriteControllerPort(service))
        command = ControlCommand(name="set_current_setting", path="/SetCurrent", value=12.5)

        result = controller.handle_control_command(command)

        self.assertTrue(result.accepted)
        self.assertFalse(result.applied)
        self.assertFalse(result.persisted)
        self.assertEqual(result.status, "accepted_in_flight")
        self.assertTrue(result.external_side_effect_started)
        self.assertEqual(result.detail, "save failed")
        backend.set_current.assert_called_once_with(12.5)
        self.assertEqual(service.virtual_set_current, 12.5)
        self.assertEqual(service._dbusservice["/SetCurrent"], 12.5)
