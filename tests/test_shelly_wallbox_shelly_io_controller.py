# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from shelly_wallbox.backend.modbus_transport import ModbusSlaveOfflineError
from shelly_wallbox.backend.shelly_io import (
    ShellyIoController,
    _phase_currents_for_selection,
    _phase_powers_for_selection,
    _single_phase_vector,
)
from shelly_wallbox.backend.smartevse_charger import SmartEvseChargerBackend
from shelly_wallbox.backend.models import ChargerState, MeterReading


class TestShellyIoController(unittest.TestCase):
    @staticmethod
    def _write_config(directory: str, content: str) -> str:
        path = Path(directory) / "smartevse-charger.ini"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_request_auth_kwargs_supports_digest_basic_and_no_auth(self):
        digest_service = SimpleNamespace(use_digest_auth=True, username="user", password="pass")
        basic_service = SimpleNamespace(use_digest_auth=False, username="user", password="pass")
        none_service = SimpleNamespace(use_digest_auth=False, username="", password="")

        digest_controller = ShellyIoController(digest_service)
        basic_controller = ShellyIoController(basic_service)
        none_controller = ShellyIoController(none_service)

        with patch("shelly_wallbox.backend.shelly_io_requests.HTTPDigestAuth", return_value="digest-auth") as digest_auth:
            self.assertEqual(digest_controller._request_auth_kwargs(), {"auth": "digest-auth"})
        digest_auth.assert_called_once_with("user", "pass")
        self.assertEqual(basic_controller._request_auth_kwargs(), {"auth": ("user", "pass")})
        self.assertEqual(none_controller._request_auth_kwargs(), {})

    def test_request_helpers_use_timeout_and_auth_kwargs(self):
        response = MagicMock()
        response.json.return_value = {"ok": True}
        session = MagicMock()
        session.get.return_value = response
        service = SimpleNamespace(
            session=session,
            shelly_request_timeout_seconds=1.5,
            use_digest_auth=False,
            username="",
            password="",
        )
        controller = ShellyIoController(service)

        self.assertEqual(controller.request("http://example.invalid"), {"ok": True})
        self.assertEqual(controller.request_with_session(session, "http://example.invalid/worker"), {"ok": True})

        session.get.assert_any_call(url="http://example.invalid", timeout=1.5)
        session.get.assert_any_call(url="http://example.invalid/worker", timeout=1.5)
        self.assertEqual(controller._request_kwargs("http://example.invalid"), {"url": "http://example.invalid", "timeout": 1.5})

    def test_rpc_call_encodes_bool_query_as_lowercase(self):
        service = SimpleNamespace(
            host="192.168.178.76",
            _request=MagicMock(return_value={"ok": True}),
        )

        controller = ShellyIoController(service)
        controller.rpc_call("Switch.Set", id=0, on=False)

        service._request.assert_called_once_with("http://192.168.178.76/rpc/Switch.Set?id=0&on=false")

    def test_rpc_url_without_params_uses_plain_endpoint(self):
        service = SimpleNamespace(host="192.168.178.76")
        controller = ShellyIoController(service)
        self.assertEqual(controller._rpc_url("Switch.GetStatus", None), "http://192.168.178.76/rpc/Switch.GetStatus")

    def test_rpc_call_with_session_and_component_helpers_use_expected_methods(self):
        service = SimpleNamespace(
            host="192.168.178.76",
            pm_component="Switch",
            pm_id=2,
            _request_with_session=MagicMock(return_value={"ok": True}),
            rpc_call=MagicMock(return_value={"ison": True}),
            _rpc_call_with_session=MagicMock(return_value={"apower": 1200.0}),
            _worker_session="worker-session",
        )
        controller = ShellyIoController(service)

        self.assertEqual(
            controller.rpc_call_with_session("session", "Switch.Set", id=2, on=True),
            {"ok": True},
        )
        controller.fetch_pm_status()
        controller.set_relay(False)
        controller.worker_fetch_pm_status()

        service._request_with_session.assert_called_once_with(
            "session",
            "http://192.168.178.76/rpc/Switch.Set?id=2&on=true",
        )
        service.rpc_call.assert_any_call("Switch.GetStatus", id=2)
        service.rpc_call.assert_any_call("Switch.Set", id=2, on=False)
        service._rpc_call_with_session.assert_called_once_with("worker-session", "Switch.GetStatus", id=2)

    def test_fetch_pm_status_uses_split_backends_and_prefers_switch_state(self):
        meter_backend = SimpleNamespace(
            read_meter=MagicMock(
                return_value=MeterReading(
                    relay_on=False,
                    power_w=2300.0,
                    voltage_v=230.0,
                    current_a=10.0,
                    energy_kwh=12.5,
                    phase_selection="P1",
                    phase_powers_w=(2300.0, 0.0, 0.0),
                    phase_currents_a=(10.0, 0.0, 0.0),
                )
            )
        )
        switch_backend = SimpleNamespace(
            read_switch_state=MagicMock(return_value=SimpleNamespace(enabled=True, phase_selection="P1_P2")),
            capabilities=MagicMock(return_value=SimpleNamespace(supported_phase_selections=("P1", "P1_P2"))),
            set_enabled=MagicMock(),
        )
        service = SimpleNamespace(
            _backend_selection=SimpleNamespace(mode="split"),
            _meter_backend=meter_backend,
            _switch_backend=switch_backend,
            rpc_call=MagicMock(),
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
        )

        controller = ShellyIoController(service)
        pm_status = controller.fetch_pm_status()

        self.assertEqual(
            pm_status,
            {
                "output": True,
                "apower": 2300.0,
                "current": 10.0,
                "voltage": 230.0,
                "aenergy": {"total": 12500.0},
                "_phase_selection": "P1",
                "_phase_powers_w": (2300.0, 0.0, 0.0),
                "_phase_currents_a": (10.0, 0.0, 0.0),
            },
        )
        meter_backend.read_meter.assert_called_once_with()
        switch_backend.read_switch_state.assert_called_once_with()
        switch_backend.capabilities.assert_called_once_with()
        self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2"))
        self.assertEqual(service.requested_phase_selection, "P1")
        self.assertEqual(service.active_phase_selection, "P1_P2")
        self.assertIsNone(getattr(service, "_last_switch_feedback_closed", None))
        self.assertIsNone(getattr(service, "_last_switch_interlock_ok", None))

    def test_fetch_pm_status_remembers_optional_switch_feedback_runtime_state(self):
        meter_backend = SimpleNamespace(
            read_meter=MagicMock(
                return_value=MeterReading(
                    relay_on=True,
                    power_w=1200.0,
                    voltage_v=230.0,
                    current_a=5.2,
                    energy_kwh=2.5,
                    phase_selection="P1",
                )
            )
        )
        switch_backend = SimpleNamespace(
            read_switch_state=MagicMock(
                return_value=SimpleNamespace(
                    enabled=True,
                    phase_selection="P1",
                    feedback_closed=False,
                    interlock_ok=True,
                )
            ),
            capabilities=MagicMock(return_value=SimpleNamespace(supported_phase_selections=("P1",))),
            set_enabled=MagicMock(),
        )
        service = SimpleNamespace(
            _backend_selection=SimpleNamespace(mode="split"),
            _meter_backend=meter_backend,
            _switch_backend=switch_backend,
            rpc_call=MagicMock(),
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            _time_now=lambda: 100.0,
        )

        controller = ShellyIoController(service)
        controller.fetch_pm_status()

        self.assertFalse(service._last_switch_feedback_closed)
        self.assertTrue(service._last_switch_interlock_ok)
        self.assertEqual(service._last_switch_feedback_at, 100.0)

    def test_queue_relay_command_warns_when_direct_switch_breaks_over_limit_load(self):
        switch_backend = SimpleNamespace(
            capabilities=MagicMock(
                return_value=SimpleNamespace(
                    switching_mode="direct",
                    max_direct_switch_power_w=1500.0,
                )
            )
        )
        service = SimpleNamespace(
            _backend_selection=SimpleNamespace(mode="split"),
            _switch_backend=switch_backend,
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _relay_command_lock=threading.Lock(),
            _pending_relay_state=None,
            _pending_relay_requested_at=None,
            relay_sync_timeout_seconds=2.0,
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=False,
            _last_pm_status={"apower": 1800.0},
            _last_pm_status_confirmed=True,
            _warning_throttled=MagicMock(),
            auto_shelly_soft_fail_seconds=30.0,
        )

        controller = ShellyIoController(service)
        controller.queue_relay_command(False)

        service._warning_throttled.assert_called_once()
        self.assertEqual(service._warning_throttled.call_args.args[0], "direct-switch-under-load")
        self.assertFalse(service._pending_relay_state)

    def test_queue_relay_command_skips_overload_warning_for_contactor_switching(self):
        switch_backend = SimpleNamespace(
            capabilities=MagicMock(
                return_value=SimpleNamespace(
                    switching_mode="contactor",
                    max_direct_switch_power_w=1500.0,
                )
            )
        )
        service = SimpleNamespace(
            _backend_selection=SimpleNamespace(mode="split"),
            _switch_backend=switch_backend,
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _relay_command_lock=threading.Lock(),
            _pending_relay_state=None,
            _pending_relay_requested_at=None,
            relay_sync_timeout_seconds=2.0,
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=False,
            _last_pm_status={"apower": 1800.0},
            _last_pm_status_confirmed=True,
            _warning_throttled=MagicMock(),
            auto_shelly_soft_fail_seconds=30.0,
        )

        controller = ShellyIoController(service)
        controller.queue_relay_command(False)

        service._warning_throttled.assert_not_called()
        self.assertFalse(service._pending_relay_state)

    def test_fetch_pm_status_preserves_legacy_single_phase_line_mapping_in_split_mode(self):
        meter_backend = SimpleNamespace(
            read_meter=MagicMock(
                return_value=MeterReading(
                    relay_on=True,
                    power_w=2300.0,
                    voltage_v=230.0,
                    current_a=10.0,
                    energy_kwh=12.5,
                    phase_selection="P1",
                    phase_powers_w=(0.0, 2300.0, 0.0),
                    phase_currents_a=(0.0, 10.0, 0.0),
                )
            )
        )
        switch_backend = SimpleNamespace(
            read_switch_state=MagicMock(return_value=SimpleNamespace(enabled=True)),
            capabilities=MagicMock(return_value=SimpleNamespace(supported_phase_selections=("P1",))),
            set_enabled=MagicMock(),
        )
        service = SimpleNamespace(
            _backend_selection=SimpleNamespace(mode="split"),
            _meter_backend=meter_backend,
            _switch_backend=switch_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
        )

        controller = ShellyIoController(service)
        pm_status = controller.fetch_pm_status()

        self.assertEqual(pm_status["_phase_powers_w"], (0.0, 2300.0, 0.0))
        self.assertEqual(pm_status["_phase_currents_a"], (0.0, 10.0, 0.0))
        self.assertEqual(service.active_phase_selection, "P1")

    def test_fetch_pm_status_split_falls_back_to_meter_relay_state_when_switch_read_fails(self):
        meter_backend = SimpleNamespace(
            read_meter=MagicMock(
                return_value=MeterReading(
                    relay_on=True,
                    power_w=1200.0,
                    voltage_v=230.0,
                    current_a=5.2,
                    energy_kwh=1.5,
                    phase_selection="P1",
                )
            )
        )
        switch_backend = SimpleNamespace(
            read_switch_state=MagicMock(side_effect=RuntimeError("switch down")),
            capabilities=MagicMock(return_value=SimpleNamespace(supported_phase_selections=("P1", "P1_P2_P3"))),
            set_enabled=MagicMock(),
        )
        service = SimpleNamespace(
            _backend_selection=SimpleNamespace(mode="split"),
            _meter_backend=meter_backend,
            _switch_backend=switch_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1_P2_P3",
            active_phase_selection="P1",
        )

        controller = ShellyIoController(service)
        pm_status = controller.worker_fetch_pm_status()

        self.assertTrue(pm_status["output"])
        self.assertEqual(pm_status["aenergy"]["total"], 1500.0)
        switch_backend.read_switch_state.assert_called_once_with()
        self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2_P3"))
        self.assertEqual(service.requested_phase_selection, "P1_P2_P3")
        self.assertEqual(service.active_phase_selection, "P1")

    def test_fetch_pm_status_split_without_meter_uses_fresh_charger_readback(self):
        switch_backend = SimpleNamespace(
            read_switch_state=MagicMock(
                return_value=SimpleNamespace(enabled=True, phase_selection="P1_P2_P3")
            ),
            capabilities=MagicMock(
                return_value=SimpleNamespace(supported_phase_selections=("P1", "P1_P2_P3"))
            ),
            set_enabled=MagicMock(),
        )
        charger_backend = SimpleNamespace(
            read_charger_state=MagicMock(
                return_value=SimpleNamespace(
                    enabled=False,
                    current_amps=15.5,
                    phase_selection="P1",
                    actual_current_amps=14.8,
                    power_w=10200.0,
                    energy_kwh=21.4,
                )
            )
        )
        service = SimpleNamespace(
            _backend_selection=SimpleNamespace(mode="split"),
            _meter_backend=None,
            _switch_backend=switch_backend,
            _charger_backend=charger_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            _time_now=MagicMock(return_value=100.0),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _mark_recovery=MagicMock(),
            virtual_mode=0,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_set_current=16.0,
            _last_voltage=230.0,
        )

        controller = ShellyIoController(service)
        pm_status = controller.fetch_pm_status()

        self.assertTrue(pm_status["output"])
        self.assertEqual(pm_status["apower"], 10200.0)
        self.assertEqual(pm_status["current"], 14.8)
        self.assertEqual(pm_status["voltage"], 230.0)
        self.assertEqual(pm_status["aenergy"]["total"], 21400.0)
        self.assertEqual(pm_status["_phase_selection"], "P1_P2_P3")
        self.assertEqual(pm_status["_phase_powers_w"], (3400.0, 3400.0, 3400.0))
        self.assertEqual(
            pm_status["_phase_currents_a"],
            (14.8 / 3.0, 14.8 / 3.0, 14.8 / 3.0),
        )
        charger_backend.read_charger_state.assert_called_once_with()
        switch_backend.read_switch_state.assert_called_once_with()
        self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2_P3"))
        self.assertEqual(service.active_phase_selection, "P1_P2_P3")

    def test_fetch_pm_status_split_without_meter_estimates_power_and_energy_from_fixed_phase_layout(self):
        charger_backend = SimpleNamespace(
            read_charger_state=MagicMock(
                return_value=SimpleNamespace(
                    enabled=True,
                    current_amps=15.0,
                    phase_selection="P1_P2_P3",
                    actual_current_amps=15.0,
                    power_w=None,
                    energy_kwh=None,
                    status_text="charging",
                )
            ),
            settings=SimpleNamespace(supported_phase_selections=("P1_P2_P3",)),
        )
        service = SimpleNamespace(
            _backend_selection=SimpleNamespace(mode="split"),
            _meter_backend=None,
            _switch_backend=None,
            _charger_backend=charger_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            _time_now=MagicMock(return_value=100.0),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _mark_recovery=MagicMock(),
            virtual_mode=0,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_set_current=16.0,
            _last_voltage=None,
        )

        controller = ShellyIoController(service)
        first_pm_status = controller.fetch_pm_status()

        self.assertEqual(first_pm_status["apower"], 10350.0)
        self.assertEqual(first_pm_status["current"], 15.0)
        self.assertEqual(first_pm_status["voltage"], 230.0)
        self.assertEqual(first_pm_status["aenergy"]["total"], 0.0)
        self.assertEqual(first_pm_status["_phase_selection"], "P1_P2_P3")
        self.assertEqual(service.supported_phase_selections, ("P1_P2_P3",))
        self.assertEqual(service.active_phase_selection, "P1_P2_P3")
        self.assertEqual(service._last_charger_estimate_source, "current-voltage-phase")
        self.assertEqual(service._last_charger_estimate_at, 100.0)

        service._time_now.return_value = 160.0
        second_pm_status = controller.fetch_pm_status()

        self.assertEqual(second_pm_status["apower"], 10350.0)
        self.assertEqual(second_pm_status["aenergy"]["total"], 172.5)

    def test_fetch_pm_status_split_without_meter_keeps_smartevse_connected_state_at_zero_estimate(self):
        charger_backend = SimpleNamespace(
            read_charger_state=MagicMock(
                return_value=SimpleNamespace(
                    enabled=True,
                    current_amps=16.0,
                    phase_selection="P1_P2_P3",
                    actual_current_amps=None,
                    power_w=None,
                    energy_kwh=None,
                    status_text="connected",
                )
            ),
            settings=SimpleNamespace(supported_phase_selections=("P1_P2_P3",)),
        )
        service = SimpleNamespace(
            _backend_selection=SimpleNamespace(mode="split"),
            _meter_backend=None,
            _switch_backend=None,
            _charger_backend=charger_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            _time_now=MagicMock(return_value=100.0),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _mark_recovery=MagicMock(),
            virtual_mode=0,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_set_current=16.0,
            _last_voltage=None,
        )

        controller = ShellyIoController(service)
        pm_status = controller.fetch_pm_status()

        self.assertEqual(pm_status["apower"], 0.0)
        self.assertEqual(pm_status["current"], 0.0)
        self.assertEqual(pm_status["voltage"], 230.0)
        self.assertEqual(pm_status["aenergy"]["total"], 0.0)

    def test_fetch_pm_status_split_without_meter_uses_recent_cached_charger_state(self):
        switch_backend = SimpleNamespace(
            read_switch_state=MagicMock(return_value=SimpleNamespace(enabled=True, phase_selection="P1")),
            capabilities=MagicMock(return_value=SimpleNamespace(supported_phase_selections=("P1",))),
            set_enabled=MagicMock(),
        )
        charger_backend = SimpleNamespace(read_charger_state=MagicMock(side_effect=RuntimeError("charger down")))
        service = SimpleNamespace(
            _backend_selection=SimpleNamespace(mode="split"),
            _meter_backend=None,
            _switch_backend=switch_backend,
            _charger_backend=charger_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            _time_now=MagicMock(return_value=100.0),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _mark_recovery=MagicMock(),
            _last_charger_state_enabled=True,
            _last_charger_state_current_amps=16.0,
            _last_charger_state_phase_selection="P1",
            _last_charger_state_actual_current_amps=15.1,
            _last_charger_state_power_w=3470.0,
            _last_charger_state_energy_kwh=8.75,
            _last_charger_state_at=95.0,
            _last_voltage=230.0,
        )

        controller = ShellyIoController(service)
        pm_status = controller.fetch_pm_status()

        self.assertTrue(pm_status["output"])
        self.assertEqual(pm_status["apower"], 3470.0)
        self.assertEqual(pm_status["current"], 15.1)
        self.assertEqual(pm_status["aenergy"]["total"], 8750.0)
        service._mark_failure.assert_called_once_with("charger")
        service._warning_throttled.assert_called_once()

    def test_set_relay_uses_split_switch_backend(self):
        switch_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            _backend_selection=SimpleNamespace(mode="split"),
            _switch_backend=switch_backend,
            rpc_call=MagicMock(),
        )

        controller = ShellyIoController(service)
        result = controller.set_relay(True)

        self.assertEqual(result, {"output": True})
        switch_backend.set_enabled.assert_called_once_with(True)
        service.rpc_call.assert_not_called()

    def test_set_relay_uses_split_charger_backend_when_no_switch_backend_exists(self):
        charger_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            _backend_selection=SimpleNamespace(mode="split"),
            _switch_backend=None,
            _charger_backend=charger_backend,
            rpc_call=MagicMock(),
        )

        controller = ShellyIoController(service)
        result = controller.set_relay(False)

        self.assertEqual(result, {"output": False})
        charger_backend.set_enabled.assert_called_once_with(False)
        service.rpc_call.assert_not_called()

    def test_set_phase_selection_updates_switch_and_charger_backends(self):
        switch_backend = SimpleNamespace(
            set_phase_selection=MagicMock(),
            capabilities=MagicMock(
                return_value=SimpleNamespace(
                    supported_phase_selections=("P1", "P1_P2"),
                    requires_charge_pause_for_phase_change=True,
                )
            ),
        )
        charger_backend = SimpleNamespace(set_phase_selection=MagicMock())
        service = SimpleNamespace(
            _switch_backend=switch_backend,
            _charger_backend=charger_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
        )

        controller = ShellyIoController(service)
        applied = controller.set_phase_selection("P1_P2")

        self.assertEqual(applied, "P1_P2")
        switch_backend.set_phase_selection.assert_called_once_with("P1_P2")
        charger_backend.set_phase_selection.assert_called_once_with("P1_P2")
        self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2"))
        self.assertEqual(service.requested_phase_selection, "P1_P2")
        self.assertEqual(service.active_phase_selection, "P1_P2")

    def test_set_phase_selection_skips_single_phase_charger_when_switch_handles_external_multi_phase(self):
        switch_backend = SimpleNamespace(
            set_phase_selection=MagicMock(),
            capabilities=MagicMock(
                return_value=SimpleNamespace(
                    supported_phase_selections=("P1", "P1_P2_P3"),
                    requires_charge_pause_for_phase_change=True,
                )
            ),
        )
        charger_backend = SimpleNamespace(
            set_phase_selection=MagicMock(),
            settings=SimpleNamespace(supported_phase_selections=("P1",)),
        )
        service = SimpleNamespace(
            _switch_backend=switch_backend,
            _charger_backend=charger_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
        )

        controller = ShellyIoController(service)
        applied = controller.set_phase_selection("P1_P2_P3")

        self.assertEqual(applied, "P1_P2_P3")
        switch_backend.set_phase_selection.assert_called_once_with("P1_P2_P3")
        charger_backend.set_phase_selection.assert_not_called()
        self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2_P3"))
        self.assertEqual(service.requested_phase_selection, "P1_P2_P3")
        self.assertEqual(service.active_phase_selection, "P1_P2_P3")

    def test_fetch_pm_status_syncs_native_charger_readback_into_runtime_state(self):
        charger_backend = SimpleNamespace(
            read_charger_state=MagicMock(
                return_value=SimpleNamespace(
                    enabled=False,
                    current_amps=12.5,
                    phase_selection="P1_P2",
                    actual_current_amps=12.3,
                    power_w=2830.0,
                    energy_kwh=7.25,
                )
            )
        )
        service = SimpleNamespace(
            host="192.168.178.76",
            pm_component="Switch",
            pm_id=0,
            rpc_call=MagicMock(
                return_value={
                    "output": True,
                    "apower": 1800.0,
                    "voltage": 230.0,
                    "current": 7.8,
                    "aenergy": {"total": 1000.0},
                }
            ),
            _charger_backend=charger_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            virtual_mode=0,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_set_current=16.0,
            _time_now=MagicMock(return_value=100.0),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _mark_recovery=MagicMock(),
        )

        controller = ShellyIoController(service)
        pm_status = controller.fetch_pm_status()

        self.assertTrue(pm_status["output"])
        charger_backend.read_charger_state.assert_called_once_with()
        self.assertEqual(service.virtual_enable, 0)
        self.assertEqual(service.virtual_startstop, 0)
        self.assertEqual(service.virtual_set_current, 12.5)
        self.assertEqual(service._charger_target_current_amps, 12.5)
        self.assertEqual(service._charger_target_current_applied_at, 100.0)
        self.assertFalse(service._last_charger_state_enabled)
        self.assertEqual(service._last_charger_state_current_amps, 12.5)
        self.assertEqual(service._last_charger_state_phase_selection, "P1_P2")
        self.assertEqual(service._last_charger_state_actual_current_amps, 12.3)
        self.assertEqual(service._last_charger_state_power_w, 2830.0)
        self.assertEqual(service._last_charger_state_energy_kwh, 7.25)
        self.assertEqual(service._last_charger_state_at, 100.0)
        self.assertIsNone(service._last_charger_transport_reason)
        self.assertIsNone(service._last_charger_transport_source)
        self.assertIsNone(service._last_charger_transport_detail)
        self.assertIsNone(service._last_charger_transport_at)
        self.assertIsNone(service._charger_retry_reason)
        self.assertIsNone(service._charger_retry_source)
        self.assertIsNone(service._charger_retry_until)
        self.assertEqual(service.active_phase_selection, "P1_P2")
        service._mark_recovery.assert_called_once_with("charger", "Charger state reads recovered")

    def test_fetch_pm_status_keeps_pm_flow_when_native_charger_readback_fails(self):
        service = SimpleNamespace(
            host="192.168.178.76",
            pm_component="Switch",
            pm_id=0,
            rpc_call=MagicMock(return_value={"output": False}),
            _charger_backend=SimpleNamespace(
                read_charger_state=MagicMock(
                    side_effect=ModbusSlaveOfflineError("Modbus slave 1 on /dev/ttyS7 did not respond")
                )
            ),
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _mark_recovery=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _source_retry_after={},
        )

        controller = ShellyIoController(service)
        pm_status = controller.fetch_pm_status()

        self.assertFalse(pm_status["output"])
        service._mark_failure.assert_called_once_with("charger")
        service._warning_throttled.assert_called_once()
        service._mark_recovery.assert_not_called()
        self.assertEqual(service._last_charger_transport_reason, "offline")
        self.assertEqual(service._last_charger_transport_source, "read")
        self.assertEqual(service._last_charger_transport_detail, "Modbus slave 1 on /dev/ttyS7 did not respond")
        self.assertEqual(service._last_charger_transport_at, 100.0)
        self.assertEqual(service._charger_retry_reason, "offline")
        self.assertEqual(service._charger_retry_source, "read")
        self.assertEqual(service._charger_retry_until, 120.0)
        self.assertEqual(service._source_retry_after["charger"], 120.0)

    def test_fetch_pm_status_keeps_pm_flow_when_smartevse_readback_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=smartevse_charger\nTransport=tcp\n"
                "[Transport]\nHost=192.168.1.60\nPort=502\nUnitId=1\n",
            )
            service = SimpleNamespace(
                host="192.168.178.76",
                pm_component="Switch",
                pm_id=0,
                rpc_call=MagicMock(return_value={"output": False}),
                supported_phase_selections=("P1",),
                requested_phase_selection="P1",
                active_phase_selection="P1",
                auto_shelly_soft_fail_seconds=10.0,
                _mark_failure=MagicMock(),
                _warning_throttled=MagicMock(),
                _mark_recovery=MagicMock(),
                _time_now=MagicMock(return_value=100.0),
                _source_retry_after={},
                shelly_request_timeout_seconds=2.0,
            )
            smartevse_backend = SmartEvseChargerBackend(service, config_path=config_path)
            service._charger_backend = smartevse_backend

            with patch(
                "shelly_wallbox.backend.smartevse_charger.create_modbus_transport",
                side_effect=ModbusSlaveOfflineError("Modbus slave 1 on /dev/ttyS7 did not respond"),
            ):
                controller = ShellyIoController(service)
                pm_status = controller.fetch_pm_status()

        self.assertFalse(pm_status["output"])
        service._mark_failure.assert_called_once_with("charger")
        service._warning_throttled.assert_called_once()
        service._mark_recovery.assert_not_called()
        self.assertEqual(service._last_charger_transport_reason, "offline")
        self.assertEqual(service._last_charger_transport_source, "read")
        self.assertEqual(service._last_charger_transport_detail, "Modbus slave 1 on /dev/ttyS7 did not respond")
        self.assertEqual(service._charger_retry_reason, "offline")
        self.assertEqual(service._charger_retry_source, "read")
        self.assertEqual(service._charger_retry_until, 120.0)
        self.assertEqual(service._source_retry_after["charger"], 120.0)

    def test_fetch_pm_status_skips_native_charger_readback_while_retry_backoff_is_active(self):
        charger_backend = SimpleNamespace(read_charger_state=MagicMock())
        service = SimpleNamespace(
            host="192.168.178.76",
            pm_component="Switch",
            pm_id=0,
            rpc_call=MagicMock(return_value={"output": False}),
            _charger_backend=charger_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _mark_recovery=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=110.0,
        )

        controller = ShellyIoController(service)
        pm_status = controller.fetch_pm_status()

        self.assertFalse(pm_status["output"])
        charger_backend.read_charger_state.assert_not_called()
        service._mark_failure.assert_not_called()

    def test_phase_selection_requires_pause_uses_switch_capabilities(self):
        switch_backend = SimpleNamespace(
            capabilities=MagicMock(
                return_value=SimpleNamespace(
                    supported_phase_selections=("P1", "P1_P2"),
                    requires_charge_pause_for_phase_change=True,
                )
            )
        )
        service = SimpleNamespace(
            _switch_backend=switch_backend,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
        )

        controller = ShellyIoController(service)

        self.assertTrue(controller.phase_selection_requires_pause())

    def test_build_and_publish_local_pm_status_fill_defaults(self):
        service = SimpleNamespace(
            _last_pm_status={"aenergy": "bad"},
            _last_voltage=231.0,
            _time_now=MagicMock(return_value=100.0),
            _update_worker_snapshot=MagicMock(),
            _last_pm_status_confirmed=True,
        )
        controller = ShellyIoController(service)
        service._build_local_pm_status = controller.build_local_pm_status

        pm_status = controller.build_local_pm_status(False)
        published = controller.publish_local_pm_status(True)

        self.assertEqual(pm_status["output"], False)
        self.assertEqual(pm_status["apower"], 0.0)
        self.assertEqual(pm_status["current"], 0.0)
        self.assertEqual(pm_status["voltage"], 231.0)
        self.assertEqual(pm_status["aenergy"]["total"], 0.0)
        self.assertEqual(published["output"], True)
        self.assertEqual(published["apower"], 0.0)
        self.assertEqual(published["current"], 0.0)
        self.assertEqual(service._last_pm_status_at, 100.0)
        self.assertFalse(service._last_pm_status_confirmed)
        service._update_worker_snapshot.assert_called_once_with(
            captured_at=100.0,
            pm_captured_at=100.0,
            pm_status=published,
            pm_confirmed=False,
        )

    def test_queue_peek_and_clear_pending_relay_command_use_worker_lock(self):
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _relay_command_lock=MagicMock(),
            _pending_relay_state=None,
            _pending_relay_requested_at=None,
            relay_sync_timeout_seconds=4.0,
            _relay_sync_expected_state=None,
            _relay_sync_requested_at=None,
            _relay_sync_deadline_at=None,
            _relay_sync_failure_reported=True,
        )
        service._relay_command_lock.__enter__ = MagicMock(return_value=None)
        service._relay_command_lock.__exit__ = MagicMock(return_value=None)
        controller = ShellyIoController(service)

        controller.queue_relay_command(True)
        self.assertEqual(controller.peek_pending_relay_command(), (True, 100.0))
        controller.clear_pending_relay_command(True)

        service._ensure_worker_state.assert_called()
        self.assertEqual(service._pending_relay_state, None)
        self.assertEqual(service._pending_relay_requested_at, None)
        self.assertEqual(service._relay_sync_expected_state, True)
        self.assertEqual(service._relay_sync_requested_at, 100.0)
        self.assertEqual(service._relay_sync_deadline_at, 104.0)
        self.assertFalse(service._relay_sync_failure_reported)

    def test_worker_apply_pending_relay_command_marks_success_and_clears_queue(self):
        service = SimpleNamespace(
            pm_id=0,
            _worker_session=MagicMock(),
            auto_shelly_soft_fail_seconds=10,
            _peek_pending_relay_command=MagicMock(return_value=(True, 90.0)),
            _rpc_call_with_session=MagicMock(return_value={"was_on": False}),
            _clear_pending_relay_command=MagicMock(),
            _mark_relay_changed=MagicMock(),
            _mark_recovery=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
        )

        controller = ShellyIoController(service)
        controller.worker_apply_pending_relay_command()

        service._rpc_call_with_session.assert_called_once_with(
            service._worker_session,
            "Switch.Set",
            id=0,
            on=True,
        )
        service._clear_pending_relay_command.assert_called_once_with(True)
        service._mark_relay_changed.assert_called_once_with(True, 100.0)
        service._mark_recovery.assert_called_once_with("shelly", "Shelly relay writes recovered")
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)

    def test_worker_apply_pending_relay_command_marks_failure_on_write_error(self):
        service = SimpleNamespace(
            pm_id=0,
            _worker_session=MagicMock(),
            auto_shelly_soft_fail_seconds=10,
            _peek_pending_relay_command=MagicMock(return_value=(True, 90.0)),
            _rpc_call_with_session=MagicMock(side_effect=RuntimeError("boom")),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
        )

        controller = ShellyIoController(service)
        controller.worker_apply_pending_relay_command()

        service._mark_failure.assert_called_once_with("shelly")
        service._warning_throttled.assert_called_once()

    def test_worker_apply_pending_relay_command_uses_split_switch_backend(self):
        switch_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            _backend_selection=SimpleNamespace(mode="split"),
            _switch_backend=switch_backend,
            _peek_pending_relay_command=MagicMock(return_value=(True, 90.0)),
            _clear_pending_relay_command=MagicMock(),
            _mark_relay_changed=MagicMock(),
            _mark_recovery=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _rpc_call_with_session=MagicMock(),
            _worker_session=MagicMock(),
            auto_shelly_soft_fail_seconds=10,
        )

        controller = ShellyIoController(service)
        controller.worker_apply_pending_relay_command()

        switch_backend.set_enabled.assert_called_once_with(True)
        service._rpc_call_with_session.assert_not_called()
        service._clear_pending_relay_command.assert_called_once_with(True)
        service._mark_relay_changed.assert_called_once_with(True, 100.0)
        service._mark_recovery.assert_called_once_with("shelly", "Shelly relay writes recovered")
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)

    def test_worker_apply_pending_relay_command_uses_split_charger_backend_without_switch(self):
        charger_backend = SimpleNamespace(set_enabled=MagicMock())
        service = SimpleNamespace(
            _backend_selection=SimpleNamespace(mode="split"),
            _switch_backend=None,
            _charger_backend=charger_backend,
            _peek_pending_relay_command=MagicMock(return_value=(True, 90.0)),
            _clear_pending_relay_command=MagicMock(),
            _mark_relay_changed=MagicMock(),
            _mark_recovery=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _rpc_call_with_session=MagicMock(),
            _worker_session=MagicMock(),
            auto_shelly_soft_fail_seconds=10,
        )

        controller = ShellyIoController(service)
        controller.worker_apply_pending_relay_command()

        charger_backend.set_enabled.assert_called_once_with(True)
        service._rpc_call_with_session.assert_not_called()
        service._clear_pending_relay_command.assert_called_once_with(True)
        service._mark_relay_changed.assert_called_once_with(True, 100.0)
        service._mark_recovery.assert_called_once_with("charger", "%s writes recovered", "charger backend")
        service._publish_local_pm_status.assert_called_once_with(True, 100.0)

    def test_io_worker_once_updates_snapshot_and_handles_read_failure(self):
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(side_effect=[100.0, 101.0]),
            _update_worker_snapshot=MagicMock(),
            _worker_apply_pending_relay_command=MagicMock(),
            _worker_fetch_pm_status=MagicMock(return_value={"output": True, "apower": 1200.0}),
            _mark_recovery=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _mode_uses_auto_logic=MagicMock(return_value=True),
            virtual_mode=1,
            auto_shelly_soft_fail_seconds=10,
        )

        controller = ShellyIoController(service)
        controller.io_worker_once()

        service._worker_apply_pending_relay_command.assert_called_once_with()
        service._mark_recovery.assert_called_once_with("shelly", "Shelly status reads recovered")
        self.assertEqual(service._update_worker_snapshot.call_count, 2)
        self.assertEqual(service._update_worker_snapshot.call_args_list[1].kwargs["pm_confirmed"], True)

        failing_service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _update_worker_snapshot=MagicMock(),
            _worker_apply_pending_relay_command=MagicMock(),
            _worker_fetch_pm_status=MagicMock(side_effect=RuntimeError("read failed")),
            _mark_recovery=MagicMock(),
            _mark_failure=MagicMock(),
            _warning_throttled=MagicMock(),
            _mode_uses_auto_logic=MagicMock(return_value=True),
            virtual_mode=1,
            auto_shelly_soft_fail_seconds=10,
        )
        controller = ShellyIoController(failing_service)
        controller.io_worker_once()
        failing_service._mark_failure.assert_called_once_with("shelly")
        failing_service._warning_throttled.assert_called_once()
        self.assertEqual(failing_service._update_worker_snapshot.call_count, 2)
        self.assertEqual(
            failing_service._update_worker_snapshot.call_args_list[1].kwargs,
            {
                "captured_at": 100.0,
                "auto_mode_active": True,
                "pm_status": None,
                "pm_captured_at": None,
                "pm_confirmed": False,
            },
        )

    def test_io_worker_loop_logs_cycle_failure_and_continues(self):
        stop_event = MagicMock()
        stop_event.is_set.side_effect = [False, False]
        stop_event.wait.side_effect = [False, True]
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _worker_stop_event=stop_event,
            _time_now=MagicMock(side_effect=[100.0, 100.2, 101.0, 101.1]),
            _worker_poll_interval_seconds=1.0,
            _warning_throttled=MagicMock(),
        )

        controller = ShellyIoController(service)
        controller.io_worker_once = MagicMock(side_effect=[RuntimeError("boom"), None])

        controller.io_worker_loop()

        service._ensure_worker_state.assert_called_once_with()
        self.assertEqual(controller.io_worker_once.call_count, 2)
        service._warning_throttled.assert_called_once()
        args = service._warning_throttled.call_args[0]
        self.assertEqual(args[0], "io-worker-cycle-failed")
        self.assertEqual(args[1], 1.0)
        self.assertEqual(args[2], "Background I/O worker cycle failed: %s")
        self.assertEqual(str(args[3]), "boom")

    def test_start_io_worker_spawns_thread_only_when_missing_and_always_checks_helper(self):
        alive_thread = MagicMock()
        alive_thread.is_alive.return_value = True
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _worker_thread=alive_thread,
            _ensure_auto_input_helper_process=MagicMock(),
        )

        controller = ShellyIoController(service)
        with patch("shelly_wallbox.backend.shelly_io_worker.threading.Thread") as thread_factory:
            controller.start_io_worker()
            thread_factory.assert_not_called()
        service._ensure_auto_input_helper_process.assert_called_once_with()

        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _worker_thread=None,
            _ensure_auto_input_helper_process=MagicMock(),
        )
        controller = ShellyIoController(service)
        thread = MagicMock()
        with patch("shelly_wallbox.backend.shelly_io_worker.threading.Thread", return_value=thread) as thread_factory:
            controller.start_io_worker()

        thread_factory.assert_called_once()
        thread.start.assert_called_once_with()
        service._ensure_auto_input_helper_process.assert_called_once_with()

    def test_helper_edges_cover_phase_vectors_runtime_cache_and_split_retry_paths(self):
        self.assertEqual(_single_phase_vector(9.0, "L2"), (0.0, 9.0, 0.0))
        self.assertEqual(_single_phase_vector(9.0, "L3"), (0.0, 0.0, 9.0))
        self.assertEqual(_phase_powers_for_selection(12.0, "P1_P2"), (6.0, 6.0, 0.0))
        self.assertIsNone(_phase_currents_for_selection(None, "P1"))
        self.assertEqual(_phase_currents_for_selection(12.0, "P1_P2"), (6.0, 6.0, 0.0))

        service = SimpleNamespace(
            use_digest_auth=False,
            username="user",
            password="pass",
            shelly_request_timeout_seconds=2.5,
            _backend_selection=SimpleNamespace(mode="combined"),
            _switch_backend=SimpleNamespace(capabilities=MagicMock(side_effect=RuntimeError("boom"))),
            supported_phase_selections=("P1", "P1_P2"),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            _last_pm_status_confirmed=False,
            _last_pm_status="bad",
            _last_voltage=400.0,
            voltage_mode="line",
            auto_shelly_soft_fail_seconds=15.0,
            _time_now=lambda: 100.0,
            _source_retry_after={},
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=120.0,
            _delay_source_retry=MagicMock(),
            _warning_throttled=MagicMock(),
            _mark_failure=MagicMock(),
            _mark_recovery=MagicMock(),
            virtual_mode=0,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_set_current=16.0,
            phase="L3",
        )
        controller = ShellyIoController(service)

        self.assertEqual(
            controller._request_kwargs("http://example.invalid"),
            {
                "url": "http://example.invalid",
                "timeout": 2.5,
                "auth": ("user", "pass"),
            },
        )
        self.assertIsNone(controller._split_meter_backend())
        self.assertIsNone(controller._phase_switch_capabilities())
        self.assertFalse(controller._charger_supports_phase_selection("P1"))
        self.assertIsNone(controller._current_confirmed_switch_load_power_w())
        self.assertIsNone(controller._direct_switch_warning_context(True))
        self.assertAlmostEqual(controller._estimated_phase_voltage_v("P1_P2"), 400.0 / (3.0**0.5))
        self.assertIsNone(controller._estimated_charger_power_w(None, "P1"))
        self.assertIsNone(
            controller._runtime_cached_charger_state(now=100.0, max_age_seconds=10.0)
        )
        self.assertIsNone(
            controller._cached_charger_state_timestamp(now=100.0, max_age_seconds=10.0)
        )
        self.assertEqual(
            controller._resolved_pm_charger_current(
                ChargerState(enabled=True, current_amps=12.0, phase_selection="P1", status_text="ready")
            ),
            0.0,
        )
        self.assertEqual(
            controller._resolved_pm_charger_current(
                ChargerState(enabled=False, current_amps=12.0, phase_selection="P1")
            ),
            0.0,
        )
        self.assertIsNone(
            controller._resolved_pm_charger_current(
                ChargerState(enabled=None, current_amps=None, phase_selection=None)
            )
        )
        self.assertEqual(
            controller._resolved_charger_current(
                ChargerState(enabled=None, current_amps=None, phase_selection=None, actual_current_amps=7.5)
            ),
            7.5,
        )
        self.assertEqual(
            controller._resolved_charger_current(
                ChargerState(enabled=None, current_amps=6.5, phase_selection="P1")
            ),
            6.5,
        )
        self.assertIsNone(
            controller._resolved_charger_current(
                ChargerState(enabled=None, current_amps=None, phase_selection=None)
            )
        )
        self.assertEqual(
            controller._pm_status_from_charger_state(
                ChargerState(enabled=None, current_amps=None, phase_selection=None),
                relay_on=None,
            ),
            {
                "apower": 0.0,
                "aenergy": {"total": 0.0},
                "_phase_selection": "P1",
                "voltage": 400.0,
                "_phase_powers_w": (0.0, 0.0, 0.0),
            },
        )
        self.assertEqual(controller._split_switch_supported_phase_selections(), ("P1", "P1_P2"))
        with self.assertRaisesRegex(ValueError, "Unsupported phase selection"):
            controller.set_phase_selection("P1_P2_P3")
        controller._remember_charger_retry("offline", "read", 100.0)
        service._delay_source_retry.assert_called_once_with("charger", 100.0, 20.0)
        controller._clear_charger_retry()
        self.assertEqual(service._source_retry_after["charger"], 0.0)
        self.assertIsNone(service._charger_retry_until)
        self.assertEqual(controller._relay_state_from_split_switch(True), True)
        self.assertIsNone(controller._runtime_cached_charger_state())

        service._last_pm_status_confirmed = True
        service._last_pm_status = "bad"
        self.assertIsNone(controller._current_confirmed_switch_load_power_w())
        service._last_pm_status = {"apower": 1000.0}
        switch_backend = SimpleNamespace(
            capabilities=MagicMock(
                return_value=SimpleNamespace(
                    switching_mode="direct",
                    max_direct_switch_power_w=1500.0,
                )
            )
        )
        service._switch_backend = switch_backend
        controller = ShellyIoController(service)
        self.assertIsNone(controller._direct_switch_warning_context(False))
        service._last_pm_status = {"apower": 2000.0}
        service._warning_throttled = "nope"
        controller._warn_if_direct_switching_under_load(False)
        self.assertEqual(
            controller._resolved_pm_charger_current(
                ChargerState(enabled=True, current_amps=12.0, phase_selection="P1")
            ),
            12.0,
        )
        service._last_charger_state_at = 95.0
        self.assertEqual(controller._cached_charger_state_timestamp(max_age_seconds=None), 95.0)
        self.assertIsNone(controller._cached_charger_state_timestamp(now=110.0, max_age_seconds=10.0))
        service._last_charger_state_enabled = None
        service._last_charger_state_current_amps = None
        service._last_charger_state_phase_selection = None
        self.assertIsNone(controller._runtime_cached_charger_state())

    def test_helper_edges_cover_runtime_sync_and_switch_state_fallbacks(self):
        service = SimpleNamespace(
            _backend_selection=SimpleNamespace(mode="split"),
            _switch_backend=SimpleNamespace(read_switch_state=MagicMock(side_effect=RuntimeError("switch down"))),
            _charger_backend=SimpleNamespace(settings=SimpleNamespace(supported_phase_selections=("P1_P2_P3",))),
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            _last_switch_feedback_closed=True,
            _last_switch_interlock_ok=True,
            _last_switch_feedback_at=50.0,
            _time_now=lambda: 100.0,
            _mode_uses_auto_logic=lambda mode: bool(mode),
            virtual_mode=0,
            virtual_enable=1,
            virtual_startstop=1,
            virtual_set_current=16.0,
            _source_retry_after={},
        )
        controller = ShellyIoController(service)

        controller._sync_virtual_enabled_state(
            ChargerState(enabled=None, current_amps=None, phase_selection=None),
            auto_mode_active=False,
        )
        controller._sync_virtual_current_target(
            ChargerState(enabled=None, current_amps=None, phase_selection=None),
            100.0,
        )
        controller._sync_runtime_phase_selection_from_charger(
            ChargerState(enabled=None, current_amps=None, phase_selection=None)
        )
        self.assertEqual(controller._safe_split_switch_state(), None)
        self.assertIsNone(service._last_switch_feedback_closed)
        self.assertIsNone(service._last_switch_interlock_ok)
        self.assertIsNone(service._last_switch_feedback_at)
        self.assertEqual(controller._relay_state_from_split_switch(False), False)
        self.assertEqual(controller._split_switch_supported_phase_selections(), ("P1",))

        service._switch_backend = SimpleNamespace(set_phase_selection=MagicMock())
        controller = ShellyIoController(service)
        controller._sync_runtime_phase_selection_from_charger(
            ChargerState(enabled=None, current_amps=None, phase_selection="P1_P2_P3")
        )
        self.assertEqual(service.active_phase_selection, "P1")

        service._switch_backend = None
        controller = ShellyIoController(service)
        self.assertEqual(controller._split_switch_supported_phase_selections(), ("P1_P2_P3",))
        service._switch_backend = SimpleNamespace(
            set_enabled=MagicMock(),
            read_switch_state=MagicMock(side_effect=RuntimeError("switch down")),
        )
        controller = ShellyIoController(service)
        self.assertEqual(controller._relay_state_from_split_switch(True), True)
        service._switch_backend = SimpleNamespace(
            set_enabled=MagicMock(),
            read_switch_state=MagicMock(return_value=SimpleNamespace(enabled=None)),
        )
        controller = ShellyIoController(service)
        self.assertEqual(controller._relay_state_from_split_switch(False), False)

    def test_build_local_pm_status_normalizes_numeric_energy_totals(self):
        service = SimpleNamespace(
            _last_pm_status={"aenergy": {"total": 12}},
            _last_voltage=230.0,
        )
        controller = ShellyIoController(service)

        pm_status = controller.build_local_pm_status(True)

        self.assertEqual(pm_status["aenergy"]["total"], 12.0)

    def test_worker_apply_pending_relay_command_returns_when_queue_is_empty(self):
        service = SimpleNamespace(
            _peek_pending_relay_command=MagicMock(return_value=(None, None)),
        )
        controller = ShellyIoController(service)

        controller.worker_apply_pending_relay_command()

        service._peek_pending_relay_command.assert_called_once_with()

    def test_read_split_pm_status_without_meter_requires_recent_charger_state(self):
        service = SimpleNamespace(
            _backend_selection=SimpleNamespace(mode="split"),
            _meter_backend=None,
            _switch_backend=None,
            _charger_backend=None,
            supported_phase_selections=("P1",),
            requested_phase_selection="P1",
            active_phase_selection="P1",
            auto_shelly_soft_fail_seconds=10.0,
            _time_now=lambda: 100.0,
        )
        controller = ShellyIoController(service)

        with self.assertRaisesRegex(RuntimeError, "requires fresh charger readback"):
            controller._read_split_pm_status_without_meter(None, ("P1",), None, 100.0)

    def test_worker_apply_pending_relay_command_skips_and_tracks_charger_transport_retry(self):
        charger_backend = SimpleNamespace(set_enabled=MagicMock(side_effect=ModbusSlaveOfflineError("offline")))
        service = SimpleNamespace(
            _backend_selection=SimpleNamespace(mode="split"),
            _switch_backend=None,
            _charger_backend=charger_backend,
            _peek_pending_relay_command=MagicMock(return_value=(True, 90.0)),
            _clear_pending_relay_command=MagicMock(),
            _mark_relay_changed=MagicMock(),
            _mark_recovery=MagicMock(),
            _publish_local_pm_status=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _rpc_call_with_session=MagicMock(),
            _worker_session=MagicMock(),
            auto_shelly_soft_fail_seconds=10.0,
            _warning_throttled=MagicMock(),
            _mark_failure=MagicMock(),
            _source_retry_after={},
            _charger_retry_reason="offline",
            _charger_retry_source="read",
            _charger_retry_until=110.0,
        )
        controller = ShellyIoController(service)

        controller.worker_apply_pending_relay_command()
        charger_backend.set_enabled.assert_not_called()

        service._charger_retry_until = None
        controller.worker_apply_pending_relay_command()
        charger_backend.set_enabled.assert_called_once_with(True)
        self.assertEqual(service._last_charger_transport_reason, "offline")
        self.assertEqual(service._last_charger_transport_source, "enable")
        self.assertEqual(service._charger_retry_reason, "offline")
        service._mark_failure.assert_called_once_with("charger")
