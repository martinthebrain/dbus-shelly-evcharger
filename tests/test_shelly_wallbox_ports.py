# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from dbus_shelly_wallbox_ports import AutoDecisionPort, DbusInputPort, UpdateCyclePort, WriteControllerPort


class TestWallboxPorts(unittest.TestCase):
    def test_base_ports_raise_for_unknown_attrs_and_missing_controller_bindings(self):
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=0,
            virtual_enable=1,
            virtual_set_current=16.0,
            min_current=6.0,
            max_current=16.0,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            manual_override_until=0.0,
            auto_manual_override_seconds=300.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
        )

        write_port = WriteControllerPort(service)
        with self.assertRaises(AttributeError):
            _ = write_port.unknown_attr
        with self.assertRaises(AttributeError):
            write_port.unknown_attr = 1

        input_service = SimpleNamespace(
            auto_pv_service="",
            auto_pv_service_prefix="com.victronenergy.pvinverter",
            _resolved_auto_pv_services=[],
            _auto_pv_last_scan=0.0,
            auto_pv_scan_interval_seconds=60.0,
            auto_pv_max_services=2,
            auto_pv_path="/Ac/Power",
            auto_use_dc_pv=False,
            auto_dc_pv_service="com.victronenergy.system",
            auto_dc_pv_path="/Dc/Pv/Power",
            _last_pv_missing_warning=None,
            auto_battery_service="",
            auto_battery_service_prefix="com.victronenergy.battery",
            auto_battery_soc_path="/Soc",
            _resolved_auto_battery_service=None,
            _auto_battery_last_scan=0.0,
            auto_battery_scan_interval_seconds=60.0,
            auto_grid_l1_path="/Ac/Grid/L1/Power",
            auto_grid_l2_path="/Ac/Grid/L2/Power",
            auto_grid_l3_path="/Ac/Grid/L3/Power",
            auto_grid_require_all_phases=True,
            auto_grid_service="com.victronenergy.system",
            _dbus_list_backoff_until=0.0,
            _dbus_list_failures=0,
            auto_dbus_backoff_base_seconds=5.0,
            auto_dbus_backoff_max_seconds=60.0,
            dbus_method_timeout_seconds=1.0,
            _last_dbus_ok_at=None,
            _source_retry_ready=MagicMock(return_value=True),
            _mark_recovery=MagicMock(),
            _mark_failure=MagicMock(),
            _delay_source_retry=MagicMock(),
            _warning_throttled=MagicMock(),
            _get_system_bus=MagicMock(),
            _reset_system_bus=MagicMock(),
        )
        input_port = DbusInputPort(input_service)
        with self.assertRaises(AttributeError):
            input_port.list_dbus_services()

    def test_port_allowed_methods_exist_on_service_class(self):
        source_text = "\n".join(
            Path(file_name).read_text(encoding="utf-8")
            for file_name in (
                "dbus_shelly_wallbox.py",
                "dbus_shelly_wallbox_service_auto.py",
                "dbus_shelly_wallbox_service_runtime.py",
                "dbus_shelly_wallbox_service_state_publish.py",
                "dbus_shelly_wallbox_service_update.py",
            )
        )
        for method_name in sorted(UpdateCyclePort._ALLOWED_METHODS):
            with self.subTest(method_name=method_name):
                self.assertIn(f"def {method_name}(", source_text)

    def test_port_declared_attrs_are_referenced_in_service_code(self):
        source_text = "\n".join(
            Path(file_name).read_text(encoding="utf-8")
            for file_name in (
                "dbus_shelly_wallbox.py",
                "dbus_shelly_wallbox_auto_logic.py",
                "dbus_shelly_wallbox_auto_policy.py",
                "dbus_shelly_wallbox_bootstrap.py",
                "dbus_shelly_wallbox_runtime_support.py",
                "dbus_shelly_wallbox_service_auto.py",
                "dbus_shelly_wallbox_service_factory.py",
                "dbus_shelly_wallbox_service_runtime.py",
                "dbus_shelly_wallbox_service_state_publish.py",
                "dbus_shelly_wallbox_service_update.py",
                "dbus_shelly_wallbox_state.py",
                "dbus_shelly_wallbox_update_cycle.py",
            )
        )
        declared_attrs = set()
        for port_class in (WriteControllerPort, DbusInputPort, UpdateCyclePort, AutoDecisionPort):
            declared_attrs.update(port_class._ALLOWED_ATTRS)
            declared_attrs.update(port_class._MUTABLE_ATTRS)
        for attr_name in sorted(declared_attrs):
            with self.subTest(attr_name=attr_name):
                self.assertIn(attr_name, source_text)

    def test_write_controller_port_forwards_state_and_methods(self):
        service = SimpleNamespace(
            virtual_mode=0,
            virtual_autostart=1,
            virtual_startstop=0,
            virtual_enable=1,
            virtual_set_current=16.0,
            min_current=6.0,
            max_current=16.0,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            manual_override_until=0.0,
            auto_manual_override_seconds=300.0,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _clear_auto_samples=MagicMock(),
            _queue_relay_command=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _get_worker_snapshot=MagicMock(return_value={"pm_status": None}),
            _update_worker_snapshot=MagicMock(),
            _publish_dbus_path=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _normalize_mode=MagicMock(return_value=1),
            _mode_uses_auto_logic=MagicMock(return_value=True),
            _state_summary=MagicMock(return_value="state"),
            _save_runtime_state=MagicMock(),
        )

        port = WriteControllerPort(service)
        port.virtual_mode = 2
        self.assertEqual(service.virtual_mode, 2)
        self.assertEqual(port.auto_manual_override_seconds, 300.0)
        self.assertFalse(port.auto_mode_cutover_pending)
        self.assertFalse(port.ignore_min_offtime_once)
        port.queue_relay_command(True, 100.0)
        service._queue_relay_command.assert_called_once_with(True, 100.0)
        self.assertEqual(port.normalize_mode("1"), 1)
        self.assertEqual(port._normalize_mode("1"), 1)

    def test_dbus_input_port_uses_service_override_before_controller(self):
        service = SimpleNamespace(
            auto_pv_service="",
            auto_pv_service_prefix="com.victronenergy.pvinverter",
            _resolved_auto_pv_services=[],
            _auto_pv_last_scan=0.0,
            auto_pv_scan_interval_seconds=60.0,
            auto_pv_max_services=2,
            auto_pv_path="/Ac/Power",
            auto_use_dc_pv=False,
            auto_dc_pv_service="com.victronenergy.system",
            auto_dc_pv_path="/Dc/Pv/Power",
            _last_pv_missing_warning=None,
            auto_battery_service="",
            auto_battery_service_prefix="com.victronenergy.battery",
            auto_battery_soc_path="/Soc",
            _resolved_auto_battery_service=None,
            _auto_battery_last_scan=0.0,
            auto_battery_scan_interval_seconds=60.0,
            auto_grid_l1_path="/Ac/Grid/L1/Power",
            auto_grid_l2_path="/Ac/Grid/L2/Power",
            auto_grid_l3_path="/Ac/Grid/L3/Power",
            auto_grid_require_all_phases=True,
            auto_grid_service="com.victronenergy.system",
            _dbus_list_backoff_until=0.0,
            _dbus_list_failures=0,
            auto_dbus_backoff_base_seconds=5.0,
            auto_dbus_backoff_max_seconds=60.0,
            dbus_method_timeout_seconds=1.0,
            _last_dbus_ok_at=None,
            _source_retry_ready=MagicMock(return_value=True),
            _mark_recovery=MagicMock(),
            _mark_failure=MagicMock(),
            _delay_source_retry=MagicMock(),
            _warning_throttled=MagicMock(),
            _get_system_bus=MagicMock(),
            _reset_system_bus=MagicMock(),
            _get_dbus_value=MagicMock(return_value=42.0),
        )

        port = DbusInputPort(service)

        class DummyController:
            def get_dbus_value(self, *_args, **_kwargs):
                raise AssertionError("service override should win")

        port.bind_controller(DummyController())
        self.assertEqual(port.get_dbus_value("svc", "/Path"), 42.0)
        self.assertEqual(port._get_dbus_value("svc", "/Path"), 42.0)
        self.assertEqual(service._get_dbus_value.call_count, 2)

    def test_auto_decision_port_falls_back_to_bound_controller(self):
        service = SimpleNamespace(
            auto_samples=[],
            auto_average_window_seconds=60.0,
            relay_last_changed_at=None,
            relay_last_off_at=None,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            _last_health_reason="init",
            _last_health_code=0,
            auto_min_runtime_seconds=0.0,
            auto_min_offtime_seconds=0.0,
            _last_grid_at=None,
            auto_grid_missing_stop_seconds=60.0,
            virtual_mode=1,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _last_battery_allow_warning=None,
            auto_allow_without_battery_soc=False,
            auto_battery_scan_interval_seconds=60.0,
            auto_resume_soc=50.0,
            auto_min_soc=30.0,
            auto_stop_delay_seconds=30.0,
            auto_stop_grid_import_watts=200.0,
            auto_night_lock_stop=False,
            _last_auto_metrics={},
            started_at=0.0,
            auto_startup_warmup_seconds=0.0,
            manual_override_until=0.0,
            virtual_autostart=1,
            auto_start_delay_seconds=30.0,
            auto_start_max_grid_import_watts=50.0,
            auto_start_surplus_watts=2000.0,
            auto_stop_surplus_watts=1500.0,
            _auto_cached_inputs_used=False,
            virtual_enable=1,
            auto_daytime_only=False,
            auto_month_windows={},
            _save_runtime_state=MagicMock(),
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
        )

        port = AutoDecisionPort(service)

        class DummyController:
            def clear_auto_samples(self):
                return "cleared"

            def is_within_auto_daytime_window(self):
                return True

        port.bind_controller(DummyController())
        self.assertEqual(port.clear_auto_samples(), "cleared")
        self.assertEqual(port._clear_auto_samples(), "cleared")
        self.assertTrue(port.is_within_auto_daytime_window())

    def test_auto_decision_port_forwards_audit_and_pending_helpers(self):
        service = SimpleNamespace(
            auto_samples=[],
            auto_average_window_seconds=60.0,
            relay_last_changed_at=None,
            relay_last_off_at=None,
            auto_start_condition_since=None,
            auto_stop_condition_since=None,
            _last_health_reason="init",
            _last_health_code=0,
            auto_min_runtime_seconds=0.0,
            auto_min_offtime_seconds=0.0,
            _last_grid_at=None,
            auto_grid_missing_stop_seconds=60.0,
            virtual_mode=1,
            _auto_mode_cutover_pending=False,
            _ignore_min_offtime_once=False,
            _last_battery_allow_warning=None,
            auto_allow_without_battery_soc=False,
            auto_battery_scan_interval_seconds=60.0,
            auto_resume_soc=50.0,
            auto_min_soc=30.0,
            auto_stop_delay_seconds=30.0,
            auto_stop_grid_import_watts=200.0,
            auto_night_lock_stop=False,
            _last_auto_metrics={},
            started_at=0.0,
            auto_startup_warmup_seconds=0.0,
            manual_override_until=0.0,
            virtual_autostart=1,
            auto_start_delay_seconds=30.0,
            auto_start_max_grid_import_watts=50.0,
            auto_start_surplus_watts=2000.0,
            auto_stop_surplus_watts=1500.0,
            _auto_cached_inputs_used=False,
            virtual_enable=1,
            virtual_startstop=0,
            auto_daytime_only=False,
            auto_month_windows={},
            auto_audit_log=True,
            _save_runtime_state=MagicMock(return_value="saved"),
            _write_auto_audit_event=MagicMock(return_value="audited"),
            _peek_pending_relay_command=MagicMock(return_value=(True, 123.0)),
        )

        port = AutoDecisionPort(service)
        self.assertEqual(port.save_runtime_state(), "saved")
        self.assertEqual(port.write_auto_audit_event("running", False), "audited")
        self.assertEqual(port.peek_pending_relay_command(), (True, 123.0))
        service._write_auto_audit_event.assert_called_once_with("running", False)

    def test_update_cycle_port_forwards_mutable_runtime_fields(self):
        service = SimpleNamespace(
            _startup_manual_target=None,
            virtual_mode=1,
            auto_shelly_soft_fail_seconds=10.0,
            _last_health_reason="init",
            _last_health_code=0,
            charging_started_at=None,
            energy_at_start=0.0,
            virtual_startstop=0,
            virtual_enable=1,
            phase="L1",
            voltage_mode="phase",
            last_status=0,
            _last_pm_status=None,
            _last_pm_status_at=None,
            _last_voltage=None,
            auto_input_cache_seconds=120.0,
            _auto_cached_inputs_used=False,
            _error_state={"cache_hits": 0},
            _last_pv_value=None,
            _last_pv_at=None,
            _last_grid_value=None,
            _last_grid_at=None,
            _last_battery_soc_value=None,
            _last_battery_soc_at=None,
            auto_audit_log=False,
            _last_auto_metrics={},
            charging_threshold_watts=100.0,
            idle_status=6,
            _last_successful_update_at=None,
            _last_recovery_attempt_at=None,
            last_update=0.0,
            service_name="svc",
            _dbusservice={"/Ac/Power": 0.0},
            _mode_uses_auto_logic=MagicMock(return_value=True),
        )

        port = UpdateCyclePort(service)
        port.last_status = 2
        self.assertEqual(service.last_status, 2)
        self.assertTrue(port._mode_uses_auto_logic(1))
