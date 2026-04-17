# SPDX-License-Identifier: GPL-3.0-or-later
from tests.wallbox_shelly_io_controller_support import *


class TestShellyIoControllerQuaternary(ShellyIoControllerTestBase):
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

    def test_helper_edges_cover_runtime_now_split_optionals_and_worker_fallbacks(self):
        service = SimpleNamespace(
            _time_now=lambda: True,
            _source_retry_after=None,
            voltage_mode="phase",
            virtual_enable=0,
            virtual_startstop=7,
            _mode_uses_auto_logic=lambda mode: bool(mode),
            _warning_throttled=MagicMock(),
            _mark_failure=MagicMock(),
            auto_shelly_soft_fail_seconds=10.0,
        )
        controller = ShellyIoController(service)

        self.assertEqual(controller._runtime_now(), 0.0)
        controller._schedule_charger_retry_backoff(service, 100.0, 20.0)
        self.assertEqual(controller._phase_voltage_for_selection("P1_P2", 400.0), 400.0)
        controller._sync_virtual_enabled_state(
            ChargerState(enabled=True, current_amps=None, phase_selection=None),
            auto_mode_active=True,
        )
        self.assertEqual(service.virtual_enable, 1)
        self.assertEqual(service.virtual_startstop, 7)

        minimal_pm = controller._pm_status_from_meter_reading(
            MeterReading(
                relay_on=None,
                power_w=0.0,
                voltage_v=None,
                current_a=None,
                energy_kwh=1.5,
                phase_selection="P1",
                phase_powers_w=None,
                phase_currents_a=None,
            )
        )
        self.assertEqual(minimal_pm, {"apower": 0.0, "aenergy": {"total": 1500.0}, "_phase_selection": "P1"})

        self.assertEqual(controller._normalized_energy_payload({"total": "bad"}), {"total": 0.0})

        service._ensure_worker_state = MagicMock()
        service._relay_command_lock = threading.Lock()
        service._pending_relay_state = False
        service._pending_relay_requested_at = 99.0
        controller.clear_pending_relay_command(True)
        self.assertFalse(service._pending_relay_state)
        self.assertEqual(service._pending_relay_requested_at, 99.0)

        controller._handle_pending_relay_command_error(service, "charger", "charger backend", 100.0, RuntimeError("boom"))
        self.assertFalse(hasattr(service, "_last_charger_transport_reason"))
        service._mark_failure.assert_called_once_with("charger")
        service._warning_throttled.assert_called_once()

    def test_helper_edges_cover_io_worker_loop_zero_iteration_and_non_numeric_runtime_time(self):
        stop_event = SimpleNamespace(is_set=MagicMock(return_value=True))
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _worker_stop_event=stop_event,
            _time_now=lambda: "bad",
            _worker_poll_interval_seconds=0.2,
        )
        controller = ShellyIoController(service)

        controller.io_worker_loop()

        service._ensure_worker_state.assert_called_once_with()
