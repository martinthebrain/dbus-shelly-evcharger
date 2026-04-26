# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.control_api_http_cases_common import _FakeHandler
from venus_evcharger.control import ControlApiRateLimiter, ControlCommand, ControlResult, LocalControlApiHttpServer


class _ControlApiHttpTailCases:
    def test_events_endpoint_filters_recent_events_by_kind(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
            _control_api_state_token=MagicMock(return_value="state-5"),
            _control_api_event_bus=MagicMock(),
            _state_api_event_snapshot_payload=MagicMock(return_value={"summary": {"kind": "summary"}}),
        )
        service._control_api_event_bus.return_value.recent.return_value = [
            {"seq": 1, "api_version": "v1", "kind": "command", "timestamp": 1.0, "payload": {"detail": "cmd"}},
            {"seq": 2, "api_version": "v1", "kind": "state", "timestamp": 2.0, "payload": {"detail": "state"}},
        ]
        service._control_api_event_bus.return_value.wait_for_next.return_value = None
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/events?once=1&kind=command")

        server._handle_get(handler)

        import json

        lines = [json.loads(line) for line in handler.wfile.getvalue().decode("utf-8").splitlines()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["kind"], "command")

    def test_events_endpoint_waits_past_unmatched_events_until_matching_kind_arrives(self) -> None:
        event_bus = MagicMock()
        event_bus.recent.return_value = []
        event_bus.wait_for_next.side_effect = [
            {"seq": 3, "api_version": "v1", "kind": "state", "timestamp": 3.0, "payload": {"detail": "skip"}},
            {"seq": 4, "api_version": "v1", "kind": "command", "timestamp": 4.0, "payload": {"detail": "keep"}},
            None,
        ]
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
            _control_api_state_token=MagicMock(return_value="state-6"),
            _control_api_event_bus=MagicMock(return_value=event_bus),
            _state_api_event_snapshot_payload=MagicMock(return_value={"summary": {"kind": "summary"}}),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/events?after=2&timeout=0.02&kind=command&heartbeat=0")

        server._handle_get(handler)

        import json

        lines = [json.loads(line) for line in handler.wfile.getvalue().decode("utf-8").splitlines()]
        self.assertEqual(lines[0]["kind"], "command")
        self.assertEqual(lines[0]["resume_token"], "4")

    def test_write_live_events_returns_immediately_for_zero_timeout(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/events")
        event_bus = MagicMock()

        server._write_live_events(
            handler,
            event_bus,
            after_seq=0,
            timeout=0.0,
            heartbeat_interval=1.0,
            event_kinds=frozenset(),
            retry_ms=1000,
        )

        event_bus.wait_for_next.assert_not_called()

    def test_write_live_events_stops_without_heartbeat_and_wait_timeout_uses_remaining(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/events")
        event_bus = MagicMock()
        event_bus.wait_for_next.return_value = None

        server._write_live_events(
            handler,
            event_bus,
            after_seq=4,
            timeout=0.02,
            heartbeat_interval=0.0,
            event_kinds=frozenset(),
            retry_ms=1000,
        )

        event_bus.wait_for_next.assert_called_once()
        self.assertEqual(handler.wfile.getvalue(), b"")
        self.assertEqual(server._event_wait_timeout(0.75, 0.0), 0.75)

    def test_loopback_helpers_cover_common_and_invalid_hosts(self) -> None:
        handler = SimpleNamespace(client_address="bad-client")

        self.assertEqual(LocalControlApiHttpServer._client_host(handler), "127.0.0.1")
        self.assertTrue(LocalControlApiHttpServer._is_loopback_host("localhost"))
        self.assertFalse(LocalControlApiHttpServer._is_loopback_host("not-an-ip"))

    def test_query_helpers_fall_back_for_invalid_values(self) -> None:
        self.assertEqual(LocalControlApiHttpServer._query_int({"limit": ["bad"]}, "limit", 3), 3)
        self.assertEqual(LocalControlApiHttpServer._query_float({"timeout": ["bad"]}, "timeout", 1.5), 1.5)
        self.assertEqual(
            LocalControlApiHttpServer._query_event_kinds({"kind": [" command , invalid ", "state"]}),
            frozenset({"command", "state"}),
        )

    def test_request_state_tokens_ignores_empty_items_and_normalizes_unquoted_tokens(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler(
            "/v1/control/command",
            headers={
                "If-Match": ' ,W/"etag-1",plain-token',
                "X-State-Token": ",token-2,",
            },
        )

        self.assertEqual(
            server._request_state_tokens(handler),
            {"etag-1", "plain-token", "token-2"},
        )
        self.assertEqual(LocalControlApiHttpServer._normalized_token("plain-token"), "plain-token")

    def test_authorization_scope_prefers_highest_matching_token(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(
            service,
            host="127.0.0.1",
            port=8765,
            read_token="read-token",
            control_token="control-token",
            admin_token="admin-token",
            update_token="update-token",
        )

        self.assertEqual(server._authorization_scope(_FakeHandler("/v1/capabilities", authorization="Bearer update-token")), "update_admin")
        self.assertEqual(server._authorization_scope(_FakeHandler("/v1/capabilities", authorization="Bearer admin-token")), "control_admin")
        self.assertEqual(server._authorization_scope(_FakeHandler("/v1/capabilities", authorization="Bearer control-token")), "control_basic")
        self.assertEqual(server._authorization_scope(_FakeHandler("/v1/capabilities", authorization="Bearer read-token")), "read")

    def test_required_scope_for_command_payload_resolves_paths_and_falls_back_for_invalid_paths(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(
                side_effect=[
                    ControlCommand(name="set_mode", path="/Mode", value=1, source="http"),
                    ValueError("bad path"),
                ]
            ),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

        resolved_scope = server._required_scope_for_command_payload({"path": "/Mode", "value": 1})
        fallback_scope = server._required_scope_for_command_payload({"path": "/Unknown", "value": 1})

        self.assertEqual(resolved_scope, "control_basic")
        self.assertEqual(fallback_scope, "control_admin")

    def test_finer_scopes_gate_admin_and_update_commands(self) -> None:
        command = ControlCommand(name="set_auto_runtime_setting", path="/Auto/StartSurplusWatts", value=1800.0, source="http")
        result = ControlResult.applied_result(command)
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(return_value=command),
            _handle_control_command=MagicMock(return_value=result),
            _record_control_api_command_audit=MagicMock(),
        )
        server = LocalControlApiHttpServer(
            service,
            host="127.0.0.1",
            port=8765,
            control_token="control-token",
            admin_token="admin-token",
            update_token="update-token",
        )
        control_handler = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"set_auto_runtime_setting","path":"/Auto/StartSurplusWatts","value":1800.0}',
            authorization="Bearer control-token",
        )
        admin_handler = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"trigger_software_update","value":1}',
            authorization="Bearer admin-token",
        )
        update_handler = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"trigger_software_update","value":1}',
            authorization="Bearer update-token",
        )

        server._handle_post(control_handler)
        service._control_command_from_payload.return_value = ControlCommand(
            name="trigger_software_update",
            path="/Auto/SoftwareUpdateRun",
            value=1,
            source="http",
        )
        server._handle_post(admin_handler)
        server._handle_post(update_handler)

        self.assertEqual(control_handler.status_code, 403)
        self.assertEqual(control_handler.json_payload()["error"]["code"], "insufficient_scope")
        self.assertEqual(admin_handler.status_code, 403)
        self.assertEqual(admin_handler.json_payload()["error"]["code"], "insufficient_scope")
        self.assertEqual(update_handler.status_code, 200)

    def test_start_and_stop_support_unix_socket_mode(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, unix_socket_path="/tmp/control.sock")
        fake_server = MagicMock()
        fake_thread = MagicMock()

        with (
            patch.object(server, "_prepare_unix_socket_path") as prepare_socket,
            patch("venus_evcharger.control.http_api._ThreadingLocalControlUnixHttpServer", return_value=fake_server) as server_factory,
            patch("venus_evcharger.control.http_api.threading.Thread", return_value=fake_thread),
            patch("venus_evcharger.control.http_api.os.path.exists", return_value=True),
            patch("venus_evcharger.control.http_api.os.unlink") as unlink_mock,
        ):
            server.start()
            self.assertEqual(server.bound_unix_socket_path, "/tmp/control.sock")
            server.stop()

        prepare_socket.assert_called_once_with("/tmp/control.sock")
        server_factory.assert_called_once()
        unlink_mock.assert_called_once_with("/tmp/control.sock")

    def test_prepare_unix_socket_path_handles_missing_socket_existing_socket_and_non_socket(self) -> None:
        with (
            patch("venus_evcharger.control.http_api.os.path.exists", return_value=False),
            patch("venus_evcharger.control.http_api.os.unlink") as unlink_mock,
        ):
            LocalControlApiHttpServer._prepare_unix_socket_path("/tmp/missing.sock")
        unlink_mock.assert_not_called()

        with (
            patch("venus_evcharger.control.http_api.os.path.exists", return_value=True),
            patch("venus_evcharger.control.http_api.os.stat", return_value=SimpleNamespace(st_mode=0o140000)),
            patch("venus_evcharger.control.http_api.stat.S_ISSOCK", return_value=True),
            patch("venus_evcharger.control.http_api.os.unlink") as unlink_mock,
        ):
            LocalControlApiHttpServer._prepare_unix_socket_path("/tmp/existing.sock")
        unlink_mock.assert_called_once_with("/tmp/existing.sock")

        with (
            patch("venus_evcharger.control.http_api.os.path.exists", return_value=True),
            patch("venus_evcharger.control.http_api.os.stat", return_value=SimpleNamespace(st_mode=0o100644)),
            patch("venus_evcharger.control.http_api.stat.S_ISSOCK", return_value=False),
        ):
            with self.assertRaises(ValueError):
                LocalControlApiHttpServer._prepare_unix_socket_path("/tmp/not-a-socket")

    def test_get_and_post_unknown_paths_return_not_found(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        get_handler = _FakeHandler("/v1/control/unknown")
        post_handler = _FakeHandler("/v1/control/unknown", body=b"{}")

        server._handle_get(get_handler)
        server._handle_post(post_handler)

        self.assertEqual(get_handler.status_code, 404)
        self.assertEqual(post_handler.status_code, 404)
        self.assertEqual(get_handler.json_payload()["error"]["code"], "not_found")
        self.assertEqual(post_handler.json_payload()["error"]["code"], "not_found")
        self.assertIsNone(get_handler.json_payload()["command"])
        self.assertIsNone(post_handler.json_payload()["result"])

    def test_command_endpoint_rejects_invalid_json_payloads(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/control/command", body=b"{invalid")

        server._handle_post(handler)

        self.assertEqual(handler.status_code, 400)
        self.assertEqual(handler.json_payload()["error"]["code"], "invalid_json")
        service._control_command_from_payload.assert_not_called()

    def test_command_endpoint_rejects_payloads_that_fail_strict_command_schema_validation(self) -> None:
        record_audit = MagicMock()
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(side_effect=ValueError("Control command 'set_mode' requires an integer value.")),
            _handle_control_command=MagicMock(),
            _record_control_api_command_audit=record_audit,
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/control/command", body=b'{"name":"set_mode","value":"1"}')

        server._handle_post(handler)

        self.assertEqual(handler.status_code, 400)
        self.assertEqual(handler.json_payload()["error"]["code"], "validation_error")
        self.assertIn("requires an integer value", handler.json_payload()["error"]["message"])
        service._handle_control_command.assert_not_called()
        record_audit.assert_called_once()
        self.assertEqual(record_audit.call_args.kwargs["status_code"], 400)

    def test_read_json_payload_rejects_invalid_content_length_and_non_object_json(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        invalid_length_handler = _FakeHandler("/v1/control/command")
        invalid_length_handler.headers["Content-Length"] = "abc"
        list_handler = _FakeHandler("/v1/control/command", body=b"[]")

        self.assertIsNone(server._read_json_payload(invalid_length_handler))
        self.assertEqual(invalid_length_handler.status_code, 400)
        self.assertEqual(invalid_length_handler.json_payload()["error"]["code"], "invalid_content_length")
        self.assertIsNone(server._read_json_payload(list_handler))
        self.assertEqual(list_handler.status_code, 400)
        self.assertEqual(list_handler.json_payload()["error"]["code"], "invalid_payload")

    def test_write_command_result_rejects_value_errors_and_maps_statuses(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/control/command", body=b"{}")

        with patch.object(service, "_control_command_from_payload", side_effect=ValueError("bad payload")):
            server._write_command_result(handler, {})

        self.assertEqual(handler.status_code, 400)
        self.assertEqual(handler.json_payload()["detail"], "bad payload")
        self.assertEqual(handler.json_payload()["error"]["code"], "validation_error")
        self.assertIsNone(handler.json_payload()["command"])
        self.assertIsNone(handler.json_payload()["result"])

        command = ControlCommand(name="set_mode", path="/Mode", value=1, source="http")
        self.assertEqual(server._http_status_for_result(ControlResult.applied_result(command)), 200)
        self.assertEqual(server._http_status_for_result(ControlResult.accepted_in_flight_result(command)), 202)
        self.assertEqual(server._http_status_for_result(ControlResult.rejected_result(command)), 409)

    def test_rejected_command_response_contains_structured_error(self) -> None:
        command = ControlCommand(name="set_phase_selection", path="/PhaseSelection", value="P1_P2_P3", source="http")
        result = ControlResult.rejected_result(command, detail="Unsupported phase selection")
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(return_value=command),
            _handle_control_command=MagicMock(return_value=result),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/control/command", body=b'{"name": "set_phase_selection", "value": "P1_P2_P3"}')

        server._handle_post(handler)

        self.assertEqual(handler.status_code, 409)
        payload = handler.json_payload()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "unsupported_for_topology")
        self.assertTrue(payload["error"]["retryable"])
        self.assertEqual(payload["error"]["details"]["status"], "rejected")

    def test_command_endpoint_rejects_unknown_commands_with_semantic_error_code(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(side_effect=ValueError("Unsupported control command 'boom'.")),
            _handle_control_command=MagicMock(),
            _record_control_api_command_audit=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/control/command", body=b'{"name":"boom","value":1}')

        server._handle_post(handler)

        self.assertEqual(handler.status_code, 400)
        self.assertEqual(handler.json_payload()["error"]["code"], "unsupported_command")

    def test_payload_error_code_maps_supported_choice_failures_to_unsupported_command(self) -> None:
        self.assertEqual(
            LocalControlApiHttpServer._payload_error_code("Control command 'set_mode' requires one of: 0, 1, 2."),
            "unsupported_command",
        )

    def test_result_error_code_maps_semantic_rejections(self) -> None:
        topology_command = ControlCommand(name="set_phase_selection", path="/PhaseSelection", value="P1_P2_P3")
        update_command = ControlCommand(name="trigger_software_update", path="/Update/Run", value=1)
        mode_command = ControlCommand(name="set_mode", path="/Mode", value=1)
        health_command = ControlCommand(name="set_enable", path="/Enable", value=1)

        self.assertEqual(
            LocalControlApiHttpServer._result_error_code(
                ControlResult.rejected_result(topology_command, detail="Unsupported phase selection")
            ),
            "unsupported_for_topology",
        )
        self.assertEqual(
            LocalControlApiHttpServer._result_error_code(
                ControlResult.rejected_result(update_command, detail="Update already running")
            ),
            "update_in_progress",
        )
        self.assertEqual(
            LocalControlApiHttpServer._result_error_code(
                ControlResult.rejected_result(health_command, detail="Health fault lockout active")
            ),
            "blocked_by_health",
        )
        self.assertEqual(
            LocalControlApiHttpServer._result_error_code(
                ControlResult.rejected_result(mode_command, detail="Mode blocked while charging")
            ),
            "blocked_by_mode",
        )
        self.assertEqual(
            LocalControlApiHttpServer._result_error_code(
                ControlResult.accepted_in_flight_result(mode_command, detail="still busy")
            ),
            "conflict",
        )

    def test_rate_limiter_and_critical_cooldown_protect_control_endpoint(self) -> None:
        command = ControlCommand(name="trigger_software_update", path="/Auto/SoftwareUpdateRun", value=1, source="http")
        result = ControlResult.applied_result(command)
        rate_limiter = SimpleNamespace(
            allow_request=MagicMock(side_effect=[(False, 1.5), (True, 0.0)]),
            allow_command=MagicMock(return_value=(False, 2.25)),
        )
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(return_value=command),
            _handle_control_command=MagicMock(return_value=result),
            _record_control_api_command_audit=MagicMock(),
            _control_api_rate_limiter=lambda: rate_limiter,
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        rate_handler = _FakeHandler("/v1/control/command", body=b'{"name":"trigger_software_update","value":1}')
        cooldown_handler = _FakeHandler("/v1/control/command", body=b'{"name":"trigger_software_update","value":1}')

        server._handle_post(rate_handler)
        server._handle_post(cooldown_handler)

        self.assertEqual(rate_handler.status_code, 429)
        self.assertEqual(rate_handler.json_payload()["error"]["code"], "rate_limited")
        self.assertEqual(rate_handler.response_headers["Retry-After"], "2")
        self.assertEqual(cooldown_handler.status_code, 429)
        self.assertEqual(cooldown_handler.json_payload()["error"]["code"], "cooldown_active")
        self.assertEqual(cooldown_handler.response_headers["Retry-After"], "3")

    def test_rate_limiter_handles_window_retry_and_critical_cooldown_directly(self) -> None:
        limiter = ControlApiRateLimiter(max_requests=1, window_seconds=5.0, critical_cooldown_seconds=3.0)

        self.assertEqual(limiter.allow_request("local", now=10.0), (True, 0.0))
        allowed, retry_after = limiter.allow_request("local", now=12.0)
        self.assertFalse(allowed)
        self.assertEqual(retry_after, 3.0)
        self.assertEqual(limiter.allow_request("local", now=15.5), (True, 0.0))
        self.assertEqual(limiter.allow_command("local", "set_mode", now=20.0), (True, 0.0))
        self.assertEqual(limiter.allow_command("local", "trigger_software_update", now=20.0), (True, 0.0))
        critical_allowed, critical_retry = limiter.allow_command("local", "trigger_software_update", now=21.0)
        self.assertFalse(critical_allowed)
        self.assertEqual(critical_retry, 2.0)
