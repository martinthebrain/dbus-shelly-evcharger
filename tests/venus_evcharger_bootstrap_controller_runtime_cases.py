# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_bootstrap_controller_support import (
    MagicMock,
    ServiceBootstrapController,
    ServiceBootstrapControllerTestCase,
    SimpleNamespace,
    _FakeDbusService,
    patch,
)


class TestServiceBootstrapControllerRuntime(ServiceBootstrapControllerTestCase):
    def test_initialize_virtual_state_uses_config_defaults(self):
        service = SimpleNamespace(
            config={
                "DEFAULT": {
                    "Mode": "1",
                    "AutoStart": "0",
                    "StartStop": "1",
                    "Enable": "0",
                    "SetCurrent": "12.5",
                    "PhaseSelection": "P1_P2",
                }
            },
            max_current=16.0,
            _switch_backend=SimpleNamespace(
                capabilities=MagicMock(return_value=SimpleNamespace(supported_phase_selections=("P1", "P1_P2")))
            ),
        )
        controller = self._controller(service)

        controller.initialize_virtual_state()

        self.assertEqual(service.virtual_mode, 1)
        self.assertEqual(service.virtual_autostart, 0)
        self.assertEqual(service.virtual_startstop, 1)
        self.assertEqual(service.virtual_enable, 0)
        self.assertEqual(service.virtual_set_current, 12.5)
        self.assertEqual(list(service.auto_samples), [])
        self.assertIsNone(service.learned_charge_power_watts)
        self.assertIsNone(service.learned_charge_power_updated_at)
        self.assertEqual(service.learned_charge_power_state, "unknown")
        self.assertIsNone(service.learned_charge_power_learning_since)
        self.assertEqual(service.learned_charge_power_sample_count, 0)
        self.assertIsNone(service.learned_charge_power_phase)
        self.assertIsNone(service.learned_charge_power_voltage)
        self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 0)
        self.assertIsNone(service.learned_charge_power_signature_checked_session_started_at)
        self.assertIsNone(service.relay_last_changed_at)
        self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2"))
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service.active_phase_selection, "P1_P2")
        self.assertFalse(service._auto_mode_cutover_pending)

    def test_initialize_virtual_state_falls_back_when_phase_selection_is_not_supported(self):
        service = SimpleNamespace(
            config={
                "DEFAULT": {
                    "PhaseSelection": "P1_P2_P3",
                }
            },
            max_current=16.0,
            _switch_backend=SimpleNamespace(
                capabilities=MagicMock(return_value=SimpleNamespace(supported_phase_selections=("P1", "P1_P2")))
            ),
        )
        controller = self._controller(service)

        controller.initialize_virtual_state()

        self.assertEqual(service.requested_phase_selection, "P1")
        self.assertEqual(service.active_phase_selection, "P1")

    def test_switch_backend_supported_phase_selections_falls_back_after_capability_error(self):
        service = SimpleNamespace(
            _switch_backend=SimpleNamespace(capabilities=MagicMock(side_effect=RuntimeError("boom"))),
        )
        controller = self._controller(service)

        self.assertEqual(controller._switch_backend_supported_phase_selections(service), ("P1",))

    def test_restore_runtime_state_sets_manual_startup_target_only_outside_auto_mode(self):
        manual_service = SimpleNamespace(
            virtual_mode=0,
            virtual_enable=0,
            virtual_startstop=1,
            _load_runtime_state=MagicMock(),
            _init_worker_state=MagicMock(),
        )
        auto_service = SimpleNamespace(
            virtual_mode=1,
            virtual_enable=1,
            virtual_startstop=1,
            _load_runtime_state=MagicMock(),
            _init_worker_state=MagicMock(),
        )

        self._controller(manual_service).restore_runtime_state()
        self._controller(auto_service).restore_runtime_state()

        self.assertTrue(manual_service._startup_manual_target)
        self.assertIsNone(auto_service._startup_manual_target)
        manual_service._load_runtime_state.assert_called_once_with()
        manual_service._init_worker_state.assert_called_once_with()

    def test_apply_device_metadata_prefers_custom_override_and_defaults(self):
        service = SimpleNamespace(
            config={"DEFAULT": {"ProductName": "Configured Product"}},
            custom_name_override="My Wallbox",
            host="192.168.1.20",
            host_configured=True,
        )
        controller = self._controller(service)
        controller.fetch_device_info_with_fallback = MagicMock(return_value={})

        controller.apply_device_metadata()

        self.assertEqual(service.product_name, "Configured Product")
        self.assertEqual(service.custom_name, "My Wallbox")
        self.assertEqual(service.serial, "192168120")
        self.assertEqual(service.firmware_version, "1.0")
        self.assertEqual(service.hardware_version, "Shelly 1PM Gen4")

    def test_apply_device_metadata_uses_device_info_when_available(self):
        service = SimpleNamespace(
            config={"DEFAULT": {}},
            custom_name_override="",
            host="192.168.1.20",
            host_configured=True,
        )
        controller = self._controller(service)
        controller.fetch_device_info_with_fallback = MagicMock(
            return_value={
                "name": "Shelly Garage",
                "mac": "ABCDEF",
                "fw_id": "fw-123",
                "model": "Shelly Plus",
            }
        )

        controller.apply_device_metadata()

        self.assertEqual(service.product_name, "Venus EV Charger Service")
        self.assertEqual(service.custom_name, "Shelly Garage")
        self.assertEqual(service.serial, "ABCDEF")
        self.assertEqual(service.firmware_version, "fw-123")
        self.assertEqual(service.hardware_version, "Shelly Plus")

    def test_apply_device_metadata_skips_device_lookup_when_host_is_not_configured(self):
        service = SimpleNamespace(
            config={"DEFAULT": {"ProductName": "Configured Product"}},
            custom_name_override="",
            host="",
            host_configured=False,
            deviceinstance=60,
        )
        controller = self._controller(service)
        controller.fetch_device_info_with_fallback = MagicMock(return_value={"name": "should-not-be-used"})

        controller.apply_device_metadata()

        controller.fetch_device_info_with_fallback.assert_not_called()
        self.assertEqual(service.product_name, "Configured Product")
        self.assertEqual(service.custom_name, "Venus EV Charger Service")
        self.assertEqual(service.serial, "unconfigured-60")
        self.assertEqual(service.firmware_version, "1.0")
        self.assertEqual(service.hardware_version, "Not configured")

    def test_start_runtime_loops_starts_worker_and_schedules_timers(self):
        gobject_module = MagicMock()
        service = SimpleNamespace(
            _start_io_worker=MagicMock(),
            _start_control_api_server=MagicMock(),
            _start_companion_dbus_bridge=MagicMock(),
            host_configured=True,
            runtime_state_path="/run/state.json",
            _state_summary=MagicMock(return_value="mode=1"),
            poll_interval_ms=1000,
            sign_of_life_minutes=10,
            _update=MagicMock(),
            _sign_of_life=MagicMock(),
        )
        controller = ServiceBootstrapController(
            service,
            normalize_phase_func=lambda value: value,
            normalize_mode_func=lambda value: int(value),
            mode_uses_auto_logic_func=lambda mode: int(mode) in (1, 2),
            month_window_func=lambda *_args, **_kwargs: ((8, 0), (18, 0)),
            age_seconds_func=lambda *_args, **_kwargs: 0,
            health_code_func=lambda reason: {"init": 0}.get(reason, 99),
            phase_values_func=lambda *_args, **_kwargs: {},
            read_version_func=lambda _name: "1.0",
            gobject_module=gobject_module,
            script_path="/tmp/venus_evcharger_service.py",
            formatters={"kwh": None, "a": None, "w": None, "v": None, "status": None},
        )

        controller.start_runtime_loops()

        service._start_io_worker.assert_called_once_with()
        service._start_control_api_server.assert_called_once_with()
        service._start_companion_dbus_bridge.assert_called_once_with()
        gobject_module.timeout_add.assert_any_call(1000, service._update)
        gobject_module.timeout_add.assert_any_call(600000, service._sign_of_life)

    def test_start_runtime_loops_skips_companion_bridge_when_hook_is_not_callable(self):
        gobject_module = MagicMock()
        service = SimpleNamespace(
            _start_io_worker=MagicMock(),
            _start_control_api_server=MagicMock(),
            _start_companion_dbus_bridge=None,
            host_configured=True,
            runtime_state_path="/run/state.json",
            _state_summary=MagicMock(return_value="mode=1"),
            poll_interval_ms=1000,
            sign_of_life_minutes=10,
            _update=MagicMock(),
            _sign_of_life=MagicMock(),
        )
        controller = ServiceBootstrapController(
            service,
            normalize_phase_func=lambda value: value,
            normalize_mode_func=lambda value: int(value),
            mode_uses_auto_logic_func=lambda mode: int(mode) in (1, 2),
            month_window_func=lambda *_args, **_kwargs: ((8, 0), (18, 0)),
            age_seconds_func=lambda *_args, **_kwargs: 0,
            health_code_func=lambda reason: {"init": 0}.get(reason, 99),
            phase_values_func=lambda *_args, **_kwargs: {},
            read_version_func=lambda _name: "1.0",
            gobject_module=gobject_module,
            script_path="/tmp/venus_evcharger_service.py",
            formatters={"kwh": None, "a": None, "w": None, "v": None, "status": None},
        )

        controller.start_runtime_loops()

        service._start_io_worker.assert_called_once_with()
        service._start_control_api_server.assert_called_once_with()
        gobject_module.timeout_add.assert_any_call(1000, service._update)

    def test_start_runtime_loops_skips_worker_when_host_is_not_configured(self):
        gobject_module = MagicMock()
        service = SimpleNamespace(
            _start_io_worker=MagicMock(),
            _start_control_api_server=MagicMock(),
            _start_companion_dbus_bridge=MagicMock(),
            host_configured=False,
            runtime_state_path="/run/state.json",
            _state_summary=MagicMock(return_value="mode=0"),
            poll_interval_ms=1000,
            sign_of_life_minutes=10,
            _update=MagicMock(),
            _sign_of_life=MagicMock(),
        )
        controller = ServiceBootstrapController(
            service,
            normalize_phase_func=lambda value: value,
            normalize_mode_func=lambda value: int(value),
            mode_uses_auto_logic_func=lambda mode: int(mode) in (1, 2),
            month_window_func=lambda *_args, **_kwargs: ((8, 0), (18, 0)),
            age_seconds_func=lambda *_args, **_kwargs: 0,
            health_code_func=lambda reason: {"init": 0, "not-configured": 41}.get(reason, 99),
            phase_values_func=lambda *_args, **_kwargs: {},
            read_version_func=lambda _name: "1.0",
            gobject_module=gobject_module,
            script_path="/tmp/venus_evcharger_service.py",
            formatters={"kwh": None, "a": None, "w": None, "v": None, "status": None},
        )

        controller.start_runtime_loops()

        service._start_io_worker.assert_not_called()
        service._start_control_api_server.assert_called_once_with()
        service._start_companion_dbus_bridge.assert_called_once_with()
        gobject_module.timeout_add.assert_any_call(1000, service._update)
        gobject_module.timeout_add.assert_any_call(600000, service._sign_of_life)

    def test_initialize_dbus_service_uses_device_instance_in_name(self):
        service = SimpleNamespace(service_name="com.victronenergy.evcharger", deviceinstance=60)
        controller = self._controller(service)

        with patch("venus_evcharger.bootstrap.controller.VeDbusService", return_value="dbus-service") as factory:
            controller.initialize_dbus_service()

        factory.assert_called_once_with("com.victronenergy.evcharger.http_60", register=False)
        self.assertEqual(service._dbusservice, "dbus-service")

    def test_publish_dbus_service_registers_initialized_dbus_shell(self):
        dbus_service = MagicMock()
        service = SimpleNamespace(_dbusservice=dbus_service)
        controller = self._controller(service)

        controller.publish_dbus_service()

        dbus_service.register.assert_called_once_with()

    def test_publish_dbus_service_tolerates_name_exists_when_current_process_already_owns_name(self):
        class NameExistsException(Exception):
            pass

        dbus_service = MagicMock()
        dbus_service.register.side_effect = NameExistsException("exists")
        dbus_service.name = "com.victronenergy.evcharger.http_60"
        service = SimpleNamespace(_dbusservice=dbus_service)
        controller = self._controller(service)
        controller._dbus_service_owned_by_current_process = MagicMock(return_value=True)

        controller.publish_dbus_service()

        controller._dbus_service_owned_by_current_process.assert_called_once_with(dbus_service)

    def test_publish_dbus_service_reraises_name_exists_for_foreign_owner(self):
        class NameExistsException(Exception):
            pass

        dbus_service = MagicMock()
        dbus_service.register.side_effect = NameExistsException("exists")
        service = SimpleNamespace(_dbusservice=dbus_service)
        controller = self._controller(service)
        controller._dbus_service_owned_by_current_process = MagicMock(return_value=False)

        with self.assertRaises(NameExistsException):
            controller.publish_dbus_service()

    def test_dbus_service_owned_by_current_process_checks_bus_owner_pid(self):
        bus_proxy = MagicMock()
        bus_proxy.GetNameOwner.return_value = ":1.42"
        bus_proxy.GetConnectionUnixProcessID.return_value = 1234
        dbus_conn = MagicMock()
        dbus_conn.get_object.return_value = bus_proxy
        dbus_service = SimpleNamespace(_dbusconn=dbus_conn, name="com.victronenergy.evcharger.http_60")
        controller = self._controller(SimpleNamespace())

        with patch("venus_evcharger.bootstrap.controller.os.getpid", return_value=1234):
            self.assertTrue(controller._dbus_service_owned_by_current_process(dbus_service))

        bus_proxy.GetNameOwner.assert_called_once_with(
            "com.victronenergy.evcharger.http_60",
            dbus_interface="org.freedesktop.DBus",
        )
        bus_proxy.GetConnectionUnixProcessID.assert_called_once_with(
            ":1.42",
            dbus_interface="org.freedesktop.DBus",
        )

    def test_dbus_service_owned_by_current_process_returns_false_without_connection(self):
        controller = self._controller(SimpleNamespace())
        dbus_service = SimpleNamespace(name="com.victronenergy.evcharger.http_60")

        self.assertFalse(controller._dbus_service_owned_by_current_process(dbus_service))

    def test_register_paths_logs_and_reraises_add_path_failures(self):
        class _BrokenDbusService(_FakeDbusService):
            def add_path(self, path, value, **kwargs):
                if path == "/Mode":
                    raise RuntimeError("boom")
                return super().add_path(path, value, **kwargs)

        service = SimpleNamespace(
            _dbusservice=_BrokenDbusService(),
            connection_name="Shelly RPC",
            deviceinstance=60,
            product_name="Venus EV Charger Service",
            custom_name="Wallbox",
            firmware_version="1.0",
            hardware_version="Shelly 1PM Gen4",
            serial="ABC123",
            position=1,
            min_current=6.0,
            max_current=16.0,
            virtual_set_current=16.0,
            virtual_autostart=1,
            virtual_mode=0,
            virtual_startstop=1,
            virtual_enable=1,
            _last_health_reason="init",
            _last_health_code=0,
            _handle_write=MagicMock(),
        )
        controller = self._controller(service)

        with patch("venus_evcharger.bootstrap.controller.logging.error") as error_mock:
            with self.assertRaises(RuntimeError):
                controller.register_paths()

        error_mock.assert_called_once()
