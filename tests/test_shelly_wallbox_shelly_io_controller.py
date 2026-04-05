# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from dbus_shelly_wallbox_shelly_io import ShellyIoController


class TestShellyIoController(unittest.TestCase):
    def test_request_auth_kwargs_supports_digest_basic_and_no_auth(self):
        digest_service = SimpleNamespace(use_digest_auth=True, username="user", password="pass")
        basic_service = SimpleNamespace(use_digest_auth=False, username="user", password="pass")
        none_service = SimpleNamespace(use_digest_auth=False, username="", password="")

        digest_controller = ShellyIoController(digest_service)
        basic_controller = ShellyIoController(basic_service)
        none_controller = ShellyIoController(none_service)

        with patch("dbus_shelly_wallbox_shelly_io.HTTPDigestAuth", return_value="digest-auth") as digest_auth:
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

    def test_build_and_publish_local_pm_status_fill_defaults(self):
        service = SimpleNamespace(
            _last_pm_status={"aenergy": "bad"},
            _last_voltage=231.0,
            _time_now=MagicMock(return_value=100.0),
            _update_worker_snapshot=MagicMock(),
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
        self.assertEqual(service._last_pm_status_at, 100.0)
        service._update_worker_snapshot.assert_called_once()

    def test_queue_peek_and_clear_pending_relay_command_use_worker_lock(self):
        service = SimpleNamespace(
            _ensure_worker_state=MagicMock(),
            _time_now=MagicMock(return_value=100.0),
            _relay_command_lock=MagicMock(),
            _pending_relay_state=None,
            _pending_relay_requested_at=None,
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
        with patch("dbus_shelly_wallbox_shelly_io.threading.Thread") as thread_factory:
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
        with patch("dbus_shelly_wallbox_shelly_io.threading.Thread", return_value=thread) as thread_factory:
            controller.start_io_worker()

        thread_factory.assert_called_once()
        thread.start.assert_called_once_with()
        service._ensure_auto_input_helper_process.assert_called_once_with()
