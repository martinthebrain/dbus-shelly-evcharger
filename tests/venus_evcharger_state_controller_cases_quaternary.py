# SPDX-License-Identifier: GPL-3.0-or-later
from tests.venus_evcharger_state_controller_support import *


class TestServiceStateControllerQuaternary(ServiceStateControllerTestBase):
    def test_runtime_state_roundtrip_restores_ram_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/state.json"
            service = SimpleNamespace(
                runtime_state_path=path,
                virtual_mode=1,
                virtual_autostart=0,
                virtual_enable=1,
                virtual_startstop=0,
                manual_override_until=123.5,
                _auto_mode_cutover_pending=True,
                _ignore_min_offtime_once=True,
                learned_charge_power_watts=1980.0,
                learned_charge_power_updated_at=113.0,
                learned_charge_power_state="stable",
                learned_charge_power_learning_since=None,
                learned_charge_power_sample_count=4,
                learned_charge_power_phase="L1",
                learned_charge_power_voltage=229.4,
                learned_charge_power_signature_mismatch_sessions=1,
                learned_charge_power_signature_checked_session_started_at=80.0,
                supported_phase_selections=("P1", "P1_P2", "P1_P2_P3"),
                requested_phase_selection="P1_P2",
                active_phase_selection="P1_P2",
                _phase_switch_pending_selection="P1_P2_P3",
                _phase_switch_state="stabilizing",
                _phase_switch_requested_at=118.0,
                _phase_switch_stable_until=130.0,
                _phase_switch_resume_relay=True,
                _phase_switch_mismatch_counts={"P1_P2_P3": 2},
                _phase_switch_last_mismatch_selection="P1_P2_P3",
                _phase_switch_last_mismatch_at=119.0,
                _phase_switch_lockout_selection="P1_P2_P3",
                _phase_switch_lockout_reason="mismatch-threshold",
                _phase_switch_lockout_at=120.0,
                _phase_switch_lockout_until=180.0,
                _contactor_fault_counts={"contactor-suspected-open": 3},
                _contactor_fault_active_reason="contactor-suspected-open",
                _contactor_fault_active_since=121.0,
                _contactor_lockout_reason="contactor-suspected-open",
                _contactor_lockout_source="count-threshold",
                _contactor_lockout_at=122.0,
                relay_last_changed_at=111.0,
                relay_last_off_at=112.0,
                _runtime_state_serialized=None,
                _last_health_reason="init",
            )
            controller = ServiceStateController(service, self._normalize_mode)

            controller.save_runtime_state()

            with open(path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)

            self.assertEqual(saved["mode"], 1)
            self.assertEqual(saved["autostart"], 0)
            self.assertEqual(saved["auto_mode_cutover_pending"], 1)
            self.assertNotIn("ignore_min_offtime_once", saved)
            self.assertEqual(saved["learned_charge_power_watts"], 1980.0)
            self.assertEqual(saved["learned_charge_power_updated_at"], 113.0)
            self.assertEqual(saved["learned_charge_power_state"], "stable")
            self.assertEqual(saved["learned_charge_power_sample_count"], 4)
            self.assertEqual(saved["learned_charge_power_phase"], "L1")
            self.assertEqual(saved["learned_charge_power_voltage"], 229.4)
            self.assertEqual(saved["learned_charge_power_signature_mismatch_sessions"], 1)
            self.assertEqual(saved["learned_charge_power_signature_checked_session_started_at"], 80.0)
            self.assertEqual(saved["supported_phase_selections"], ["P1", "P1_P2", "P1_P2_P3"])
            self.assertEqual(saved["requested_phase_selection"], "P1_P2")
            self.assertEqual(saved["active_phase_selection"], "P1_P2")
            self.assertEqual(saved["phase_switch_pending_selection"], "P1_P2_P3")
            self.assertEqual(saved["phase_switch_state"], "stabilizing")
            self.assertEqual(saved["phase_switch_requested_at"], 118.0)
            self.assertEqual(saved["phase_switch_stable_until"], 130.0)
            self.assertEqual(saved["phase_switch_resume_relay"], 1)
            self.assertEqual(saved["phase_switch_mismatch_counts"], {"P1_P2_P3": 2})
            self.assertEqual(saved["phase_switch_last_mismatch_selection"], "P1_P2_P3")
            self.assertEqual(saved["phase_switch_last_mismatch_at"], 119.0)
            self.assertEqual(saved["phase_switch_lockout_selection"], "P1_P2_P3")
            self.assertEqual(saved["phase_switch_lockout_reason"], "mismatch-threshold")
            self.assertEqual(saved["phase_switch_lockout_at"], 120.0)
            self.assertEqual(saved["phase_switch_lockout_until"], 180.0)
            self.assertEqual(saved["contactor_fault_counts"], {"contactor-suspected-open": 3})
            self.assertEqual(saved["contactor_fault_active_reason"], "contactor-suspected-open")
            self.assertEqual(saved["contactor_fault_active_since"], 121.0)
            self.assertEqual(saved["contactor_lockout_reason"], "contactor-suspected-open")
            self.assertEqual(saved["contactor_lockout_source"], "count-threshold")
            self.assertEqual(saved["contactor_lockout_at"], 122.0)

            service.virtual_mode = 0
            service.virtual_autostart = 1
            service.virtual_enable = 0
            service.virtual_startstop = 1
            service.manual_override_until = 0.0
            service._auto_mode_cutover_pending = False
            service._ignore_min_offtime_once = False
            service.learned_charge_power_watts = None
            service.learned_charge_power_updated_at = None
            service.learned_charge_power_state = "unknown"
            service.learned_charge_power_learning_since = None
            service.learned_charge_power_sample_count = 0
            service.learned_charge_power_phase = None
            service.learned_charge_power_voltage = None
            service.learned_charge_power_signature_mismatch_sessions = 0
            service.learned_charge_power_signature_checked_session_started_at = None
            service.supported_phase_selections = ("P1",)
            service.requested_phase_selection = "P1"
            service.active_phase_selection = "P1"
            service._phase_switch_pending_selection = None
            service._phase_switch_state = None
            service._phase_switch_requested_at = None
            service._phase_switch_stable_until = None
            service._phase_switch_resume_relay = False
            service._phase_switch_mismatch_counts = {}
            service._phase_switch_last_mismatch_selection = None
            service._phase_switch_last_mismatch_at = None
            service._phase_switch_lockout_selection = None
            service._phase_switch_lockout_reason = ""
            service._phase_switch_lockout_at = None
            service._phase_switch_lockout_until = None
            service._contactor_fault_counts = {}
            service._contactor_fault_active_reason = None
            service._contactor_fault_active_since = None
            service._contactor_lockout_reason = ""
            service._contactor_lockout_source = ""
            service._contactor_lockout_at = None
            service.relay_last_changed_at = None
            service.relay_last_off_at = None

            controller.load_runtime_state()

            self.assertEqual(service.virtual_mode, 1)
            self.assertEqual(service.virtual_autostart, 0)
            self.assertEqual(service.virtual_enable, 1)
            self.assertEqual(service.virtual_startstop, 0)
            self.assertEqual(service.manual_override_until, 123.5)
            self.assertTrue(service._auto_mode_cutover_pending)
            self.assertFalse(service._ignore_min_offtime_once)
            self.assertEqual(service.learned_charge_power_watts, 1980.0)
            self.assertEqual(service.learned_charge_power_updated_at, 113.0)
            self.assertEqual(service.learned_charge_power_state, "stable")
            self.assertIsNone(service.learned_charge_power_learning_since)
            self.assertEqual(service.learned_charge_power_sample_count, 4)
            self.assertEqual(service.learned_charge_power_phase, "L1")
            self.assertEqual(service.learned_charge_power_voltage, 229.4)
            self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 1)
            self.assertEqual(service.learned_charge_power_signature_checked_session_started_at, 80.0)
            self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2", "P1_P2_P3"))
            self.assertEqual(service.requested_phase_selection, "P1_P2")
            self.assertEqual(service.active_phase_selection, "P1_P2")
            self.assertEqual(service._phase_switch_pending_selection, "P1_P2_P3")
            self.assertEqual(service._phase_switch_state, "stabilizing")
            self.assertEqual(service._phase_switch_requested_at, 118.0)
            self.assertEqual(service._phase_switch_stable_until, 130.0)
            self.assertTrue(service._phase_switch_resume_relay)
            self.assertEqual(service._phase_switch_mismatch_counts, {"P1_P2_P3": 2})
            self.assertEqual(service._phase_switch_last_mismatch_selection, "P1_P2_P3")
            self.assertEqual(service._phase_switch_last_mismatch_at, 119.0)
            self.assertEqual(service._phase_switch_lockout_selection, "P1_P2_P3")
            self.assertEqual(service._phase_switch_lockout_reason, "mismatch-threshold")
            self.assertEqual(service._phase_switch_lockout_at, 120.0)
            self.assertEqual(service._phase_switch_lockout_until, 180.0)
            self.assertEqual(service._contactor_fault_counts, {"contactor-suspected-open": 3})
            self.assertEqual(service._contactor_fault_active_reason, "contactor-suspected-open")
            self.assertEqual(service._contactor_fault_active_since, 121.0)
            self.assertEqual(service._contactor_lockout_reason, "contactor-suspected-open")
            self.assertEqual(service._contactor_lockout_source, "count-threshold")
            self.assertEqual(service._contactor_lockout_at, 122.0)
            self.assertEqual(service.relay_last_changed_at, 111.0)
            self.assertEqual(service.relay_last_off_at, 112.0)

    def test_load_runtime_state_normalizes_unknown_learned_power_state_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/state.json"
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "mode": 1,
                        "autostart": 1,
                        "enable": 1,
                        "startstop": 0,
                        "manual_override_until": 0.0,
                        "auto_mode_cutover_pending": 0,
                        "ignore_min_offtime_once": 0,
                        "learned_charge_power_watts": 1980.0,
                        "learned_charge_power_updated_at": 113.0,
                        "learned_charge_power_state": "nonsense",
                        "learned_charge_power_sample_count": 2,
                        "learned_charge_power_phase": "weird",
                        "supported_phase_selections": ["L2", "3P"],
                        "requested_phase_selection": "3P",
                        "active_phase_selection": "L2",
                    },
                    handle,
                )

            service = make_runtime_state_service(runtime_state_path=path)
            controller = ServiceStateController(service, self._normalize_mode)
            controller.load_runtime_state()

            self.assertFalse(service._ignore_min_offtime_once)
            self.assertEqual(service.learned_charge_power_state, "unknown")
            self.assertEqual(service.learned_charge_power_sample_count, 2)
            self.assertIsNone(service.learned_charge_power_phase)
            self.assertEqual(service.supported_phase_selections, ("P1", "P1_P2_P3"))
            self.assertEqual(service.requested_phase_selection, "P1_P2_P3")
            self.assertEqual(service.active_phase_selection, "P1")

    def test_load_runtime_state_discards_future_historical_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/state.json"
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "mode": 1,
                        "autostart": 1,
                        "enable": 1,
                        "startstop": 0,
                        "manual_override_until": 150.0,
                        "auto_mode_cutover_pending": 0,
                        "learned_charge_power_watts": 1980.0,
                        "learned_charge_power_updated_at": 120.0,
                        "learned_charge_power_learning_since": 121.0,
                        "learned_charge_power_signature_checked_session_started_at": 122.0,
                        "relay_last_changed_at": 123.0,
                        "relay_last_off_at": 124.0,
                    },
                    handle,
                )

            service = make_runtime_state_service(runtime_state_path=path)
            service._time_now = lambda: 100.0
            controller = ServiceStateController(service, self._normalize_mode)
            controller.load_runtime_state()

            self.assertEqual(service.manual_override_until, 150.0)
            self.assertIsNone(service.learned_charge_power_updated_at)
            self.assertIsNone(service.learned_charge_power_learning_since)
            self.assertIsNone(service.learned_charge_power_signature_checked_session_started_at)
            self.assertIsNone(service.relay_last_changed_at)
            self.assertIsNone(service.relay_last_off_at)

    def test_load_runtime_state_discards_negative_learned_power_and_voltage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/state.json"
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "mode": 1,
                        "autostart": 1,
                        "enable": 1,
                        "startstop": 0,
                        "learned_charge_power_watts": -1.0,
                        "learned_charge_power_updated_at": 100.0,
                        "learned_charge_power_state": "stable",
                        "learned_charge_power_voltage": -230.0,
                        "learned_charge_power_sample_count": -4,
                        "learned_charge_power_signature_mismatch_sessions": -2,
                    },
                    handle,
                )

            service = make_runtime_state_service(runtime_state_path=path)
            service._time_now = lambda: 100.0
            controller = ServiceStateController(service, self._normalize_mode)
            controller.load_runtime_state()

            self.assertIsNone(service.learned_charge_power_watts)
            self.assertIsNone(service.learned_charge_power_voltage)
            self.assertEqual(service.learned_charge_power_sample_count, 0)
            self.assertEqual(service.learned_charge_power_signature_mismatch_sessions, 0)

    def test_load_runtime_state_restart_during_scheduled_wait_rederives_waiting_snapshot_from_mode_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/state.json"
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "mode": 2,
                        "autostart": 1,
                        "enable": 1,
                        "startstop": 0,
                        "manual_override_until": 0.0,
                        "auto_mode_cutover_pending": 0,
                    },
                    handle,
                )

            service = make_runtime_state_service(runtime_state_path=path)
            service.auto_month_windows = {4: ((7, 30), (19, 30))}
            service.auto_scheduled_enabled_days = "Mon,Tue,Wed,Thu,Fri"
            service.auto_scheduled_night_start_delay_seconds = 3600.0
            service.auto_scheduled_latest_end_time = "06:30"
            controller = ServiceStateController(service, self._normalize_mode)
            controller.load_runtime_state()

            now = datetime(2026, 4, 20, 20, 15).timestamp()
            with patch(STATE_SUMMARY_TIME, return_value=now):
                summary = controller.state_summary()

            self.assertEqual(service.virtual_mode, 2)
            self.assertIn("scheduled_state=waiting-fallback", summary)
            self.assertIn("scheduled_reason=waiting-fallback-delay", summary)

    def test_load_runtime_state_restart_does_not_restore_transient_charger_retry_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/state.json"
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "mode": 1,
                        "autostart": 1,
                        "enable": 1,
                        "startstop": 0,
                        "manual_override_until": 0.0,
                        "auto_mode_cutover_pending": 0,
                        "charger_retry_reason": "offline",
                        "charger_retry_source": "current",
                        "charger_retry_until": 180.0,
                    },
                    handle,
                )

            service = make_runtime_state_service(runtime_state_path=path)
            controller = ServiceStateController(service, self._normalize_mode)
            controller.load_runtime_state()

            self.assertFalse(hasattr(service, "_charger_retry_reason"))
            self.assertFalse(hasattr(service, "_charger_retry_source"))
            self.assertFalse(hasattr(service, "_charger_retry_until"))

    def test_save_runtime_state_skips_empty_path_deduplicates_and_warns_on_failure(self) -> None:
        service = make_runtime_state_service()
        controller = ServiceStateController(service, self._normalize_mode)
        controller.save_runtime_state()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "state.json")
            service.runtime_state_path = path
            controller.save_runtime_state()
            first_serialized = service._runtime_state_serialized
            controller.save_runtime_state()
            self.assertEqual(service._runtime_state_serialized, first_serialized)

            with patch(STATE_RESTORE_WRITE, side_effect=RuntimeError("boom")):
                service._runtime_state_serialized = None
                with patch(STATE_RESTORE_LOG_WARNING) as warning_mock:
                    controller.save_runtime_state()
                warning_mock.assert_called_once()
