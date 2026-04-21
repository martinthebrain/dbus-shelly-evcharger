# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
import threading
import time
import unittest
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from venus_evcharger.control import (
    ControlApiAuditTrail,
    ControlApiEventBus,
    ControlApiIdempotencyStore,
    ControlApiRateLimiter,
    ControlCommand,
    ControlResult,
    LocalControlApiHttpServer,
)


class _FakeHandler:
    def __init__(
        self,
        path: str,
        *,
        body: bytes = b"{}",
        authorization: str = "",
        client_host: str = "127.0.0.1",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.path = path
        self.headers = {
            "Content-Length": str(len(body)),
            "Authorization": authorization,
        }
        if headers:
            self.headers.update(headers)
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status_code: int | None = None
        self.response_headers: dict[str, str] = {}
        self.client_address = (client_host, 12345)

    def send_response(self, status: int) -> None:
        self.status_code = status

    def send_header(self, key: str, value: str) -> None:
        self.response_headers[key] = value

    def end_headers(self) -> None:
        return None

    def json_payload(self) -> dict:
        return json.loads(self.wfile.getvalue().decode("utf-8"))


class TestLocalControlApiHttpServer(unittest.TestCase):
    def test_audit_trail_keeps_recent_entries_and_only_mirrors_runtime_paths(self) -> None:
        with patch("venus_evcharger.control.audit.open", create=True) as open_mock:
            trail = ControlApiAuditTrail(history_limit=2, path="/data/not-allowed.jsonl")
            trail.append({"command": {"name": "set_mode"}})
        open_mock.assert_not_called()

        with patch("venus_evcharger.control.audit.open", create=True) as open_mock:
            trail = ControlApiAuditTrail(history_limit=2, path="/run/control-audit.jsonl")
            first = trail.append({"command": {"name": "set_mode"}})
            second = trail.append({"command": {"name": "set_auto_start"}})
            third = trail.append({"command": {"name": "set_enable"}})

        self.assertEqual(first["seq"], 1)
        self.assertEqual(third["seq"], 3)
        self.assertEqual(trail.count(), 2)
        self.assertEqual(trail.path, "/run/control-audit.jsonl")
        self.assertEqual([entry["command"]["name"] for entry in trail.recent(limit=5)], ["set_auto_start", "set_enable"])
        open_mock.assert_called()

    def test_audit_trail_normalizes_non_mapping_payloads(self) -> None:
        trail = ControlApiAuditTrail(history_limit=2, path="/run/control-audit.jsonl")

        entry = trail.append({"command": "bad", "result": None, "error": object()})

        self.assertEqual(entry["command"], {})
        self.assertEqual(entry["result"], {})
        self.assertEqual(entry["error"], {})

    def test_idempotency_store_persists_only_to_runtime_paths_and_survives_restart(self) -> None:
        with patch("venus_evcharger.control.idempotency.open", create=True) as open_mock:
            store = ControlApiIdempotencyStore(history_limit=2, path="/data/not-allowed.json")
            store.put("idem-1", "fp", 200, {"ok": True})
        open_mock.assert_not_called()

        with TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/idempotency.json"
            first = ControlApiIdempotencyStore(history_limit=2, path=path)
            first.put("idem-1", "fp-1", 200, {"ok": True})
            first.put("idem-2", "fp-2", 202, {"ok": False})
            first.put("idem-3", "fp-3", 204, {"ok": "newest"})
            second = ControlApiIdempotencyStore(history_limit=2, path=path)

        self.assertEqual(second.count(), 2)
        self.assertEqual(second.path, path)
        self.assertIsNone(second.get("idem-1"))
        self.assertEqual(second.get("idem-2"), ("fp-2", 202, {"ok": False}))
        self.assertEqual(second.get("idem-3"), ("fp-3", 204, {"ok": "newest"}))

    def test_idempotency_store_ignores_invalid_runtime_payloads(self) -> None:
        with TemporaryDirectory() as tmpdir:
            invalid_json_path = f"{tmpdir}/invalid.json"
            with open(invalid_json_path, "w", encoding="utf-8") as handle:
                handle.write("{not-json")
            invalid_json_store = ControlApiIdempotencyStore(history_limit=2, path=invalid_json_path)

            invalid_entries_path = f"{tmpdir}/entries.json"
            with open(invalid_entries_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "bad-non-dict": "x",
                        "bad-empty-fingerprint": {"fingerprint": "", "status": 200, "response": {}},
                        "bad-response": {"fingerprint": "fp", "status": 200, "response": "x"},
                        "good": {"fingerprint": "fp-good", "status": 201, "response": {"ok": True}},
                    },
                    handle,
                )
            invalid_entries_store = ControlApiIdempotencyStore(history_limit=2, path=invalid_entries_path)

        self.assertEqual(invalid_json_store.count(), 0)
        self.assertEqual(invalid_entries_store.count(), 1)
        self.assertEqual(invalid_entries_store.get("good"), ("fp-good", 201, {"ok": True}))

    def test_idempotency_store_trims_loaded_runtime_entries_to_history_limit(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/entries.json"
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "idem-1": {"fingerprint": "fp-1", "status": 200, "response": {"ok": 1}},
                        "idem-2": {"fingerprint": "fp-2", "status": 201, "response": {"ok": 2}},
                        "idem-3": {"fingerprint": "fp-3", "status": 202, "response": {"ok": 3}},
                    },
                    handle,
                )
            store = ControlApiIdempotencyStore(history_limit=2, path=path)

        self.assertEqual(store.count(), 2)
        self.assertIsNone(store.get("idem-1"))
        self.assertEqual(store.get("idem-2"), ("fp-2", 201, {"ok": 2}))
        self.assertEqual(store.get("idem-3"), ("fp-3", 202, {"ok": 3}))

    def test_start_initializes_server_and_background_thread(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

        fake_server = MagicMock()
        fake_server.server_address = ("127.0.0.1", 8765)
        fake_thread = MagicMock()

        with (
            patch("venus_evcharger.control.http_api._ThreadingLocalControlHttpServer", return_value=fake_server) as server_factory,
            patch("venus_evcharger.control.http_api.threading.Thread", return_value=fake_thread) as thread_factory,
        ):
            server.start()
            self.assertEqual(server.bound_host, "127.0.0.1")
            self.assertEqual(server.bound_port, 8765)
            server.stop()

        server_factory.assert_called_once()
        thread_factory.assert_called_once()
        fake_thread.start.assert_called_once_with()
        fake_thread.join.assert_called_once_with(timeout=1.0)

    def test_start_is_noop_when_server_is_already_running(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        server._server = MagicMock()

        with patch("venus_evcharger.control.http_api._ThreadingLocalControlHttpServer") as server_factory:
            server.start()

        server_factory.assert_not_called()

    def test_stop_handles_missing_server_and_missing_thread(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        server.stop()

        fake_server = MagicMock()
        server._server = fake_server
        server._thread = None

        server.stop()

        fake_server.shutdown.assert_called_once_with()
        fake_server.server_close.assert_called_once_with()

    def test_health_endpoint_reports_bound_local_server(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        server.bound_host = "127.0.0.1"
        server.bound_port = 8765
        handler = _FakeHandler("/v1/control/health")

        server._handle_get(handler)
        payload = handler.json_payload()

        self.assertEqual(handler.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["api_version"], "v1")
        self.assertEqual(payload["transport"], "http")
        self.assertEqual(payload["listen_port"], 8765)

    def test_openapi_endpoint_returns_machine_readable_spec_without_auth(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, auth_token="secret-token")
        handler = _FakeHandler("/v1/openapi.json")

        server._handle_get(handler)
        payload = handler.json_payload()

        self.assertEqual(handler.status_code, 200)
        self.assertEqual(payload["openapi"], "3.1.0")
        self.assertIn("/v1/control/command", payload["paths"])
        self.assertIn("ControlCommandResponse", payload["components"]["schemas"])

    def test_event_bus_publish_recent_and_wait_cover_immediate_and_timeout_paths(self) -> None:
        bus = ControlApiEventBus(history_limit=2)

        first = bus.publish("command", {"detail": "one"})
        second = bus.publish("state", {"detail": "two"})

        self.assertEqual(bus.recent(limit=0), [])
        self.assertEqual(bus.recent(limit=5, after_seq=first["seq"])[0]["seq"], second["seq"])
        self.assertEqual(bus.wait_for_next(after_seq=0, timeout=0.0)["seq"], first["seq"])
        self.assertIsNone(bus.wait_for_next(after_seq=99, timeout=0.0))

    def test_event_bus_wait_for_next_returns_event_after_wait(self) -> None:
        bus = ControlApiEventBus(history_limit=2)

        def _publish_later() -> None:
            time.sleep(0.01)
            bus.publish("state", {"detail": "later"})

        thread = threading.Thread(target=_publish_later)
        thread.start()
        try:
            event = bus.wait_for_next(after_seq=0, timeout=0.2)
        finally:
            thread.join(timeout=1.0)

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["kind"], "state")

    def test_capabilities_and_state_get_endpoints_return_payloads(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
            _control_api_state_token=MagicMock(return_value="state-1"),
            _control_api_capabilities_payload=MagicMock(
                return_value={
                    "ok": True,
                    "api_version": "v1",
                    "transport": "http",
                    "auth_required": False,
                    "command_names": ["set_mode"],
                    "command_sources": ["http"],
                    "state_endpoints": ["/v1/state/summary"],
                    "endpoints": ["/v1/capabilities"],
                    "supported_phase_selections": ["P1"],
                    "features": {"state_reads": True},
                    "topology": {"backend_mode": "combined"},
                }
            ),
            _state_api_dbus_diagnostics_payload=MagicMock(
                return_value={"ok": True, "api_version": "v1", "kind": "dbus-diagnostics", "state": {"/Auto/State": "idle"}}
            ),
            _state_api_healthz_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "healthz", "state": {"alive": True}}),
            _state_api_version_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "version", "state": {"service_version": "1.2.3"}}),
            _state_api_build_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "build", "state": {"firmware_version": "FW"}}),
            _state_api_contracts_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "contracts", "state": {"openapi_endpoint": "/v1/openapi.json"}}),
            _state_api_summary_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "summary", "summary": "x"}),
            _state_api_runtime_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "runtime", "state": {"mode": 1}}),
            _state_api_operational_payload=MagicMock(
                return_value={"ok": True, "api_version": "v1", "kind": "operational", "state": {"mode": 1}}
            ),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

        capabilities_handler = _FakeHandler("/v1/capabilities")
        diagnostics_handler = _FakeHandler("/v1/state/dbus-diagnostics")
        healthz_handler = _FakeHandler("/v1/state/healthz")
        version_handler = _FakeHandler("/v1/state/version")
        build_handler = _FakeHandler("/v1/state/build")
        contracts_handler = _FakeHandler("/v1/state/contracts")
        summary_handler = _FakeHandler("/v1/state/summary")
        runtime_handler = _FakeHandler("/v1/state/runtime")
        operational_handler = _FakeHandler("/v1/state/operational")

        server._handle_get(capabilities_handler)
        server._handle_get(diagnostics_handler)
        server._handle_get(healthz_handler)
        server._handle_get(version_handler)
        server._handle_get(build_handler)
        server._handle_get(contracts_handler)
        server._handle_get(summary_handler)
        server._handle_get(runtime_handler)
        server._handle_get(operational_handler)

        self.assertEqual(capabilities_handler.status_code, 200)
        self.assertEqual(capabilities_handler.response_headers["ETag"], '"state-1"')
        self.assertEqual(capabilities_handler.response_headers["X-State-Token"], "state-1")
        self.assertIn("set_mode", capabilities_handler.json_payload()["command_names"])
        self.assertEqual(diagnostics_handler.status_code, 200)
        self.assertEqual(diagnostics_handler.json_payload()["kind"], "dbus-diagnostics")
        self.assertEqual(healthz_handler.status_code, 200)
        self.assertEqual(healthz_handler.json_payload()["kind"], "healthz")
        self.assertEqual(version_handler.json_payload()["kind"], "version")
        self.assertEqual(build_handler.json_payload()["kind"], "build")
        self.assertEqual(contracts_handler.json_payload()["kind"], "contracts")
        self.assertEqual(summary_handler.status_code, 200)
        self.assertEqual(summary_handler.json_payload()["kind"], "summary")
        self.assertEqual(runtime_handler.json_payload()["kind"], "runtime")
        self.assertEqual(operational_handler.json_payload()["kind"], "operational")

    def test_execute_payload_preserves_existing_tracking_when_service_returns_it(self) -> None:
        command = ControlCommand(
            name="set_mode",
            path="/Mode",
            value=1,
            source="http",
            command_id="cmd-1",
            idempotency_key="idem-1",
        )
        result = ControlResult.applied_result(command)
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(return_value=command),
            _handle_control_command=MagicMock(return_value=result),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

        executed_command, _executed_result = server.execute_payload(
            {"name": "set_mode", "value": 1, "command_id": "cmd-1", "idempotency_key": "idem-1"}
        )

        self.assertEqual(executed_command.command_id, "cmd-1")
        self.assertEqual(executed_command.idempotency_key, "idem-1")

    def test_tracked_command_keeps_matching_tracking_metadata_unchanged(self) -> None:
        command = ControlCommand(
            name="set_mode",
            path="/Mode",
            value=1,
            source="http",
            command_id="cmd-1",
            idempotency_key="idem-1",
        )

        tracked = LocalControlApiHttpServer._tracked_command(
            {"command_id": "cmd-1", "idempotency_key": "idem-1"},
            command,
        )

        self.assertIs(tracked, command)

    def test_execute_payload_injects_tracking_when_service_returns_untracked_command(self) -> None:
        command = ControlCommand(name="set_mode", path="/Mode", value=1, source="http")
        result = ControlResult.applied_result(command)
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(return_value=command),
            _handle_control_command=MagicMock(return_value=result),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

        executed_command, _executed_result = server.execute_payload(
            {"name": "set_mode", "value": 1, "command_id": "cmd-9", "idempotency_key": "idem-9"}
        )

        self.assertEqual(executed_command.command_id, "cmd-9")
        self.assertEqual(executed_command.idempotency_key, "idem-9")

    def test_command_endpoint_rejects_stale_if_match_state_token(self) -> None:
        command = ControlCommand(name="set_mode", path="/Mode", value=1, source="http")
        result = ControlResult.applied_result(command)
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(return_value=command),
            _handle_control_command=MagicMock(return_value=result),
            _record_control_api_command_audit=MagicMock(),
            _control_api_state_token=MagicMock(return_value="fresh-state"),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"set_mode","value":1}',
            headers={"If-Match": '"stale-state"'},
        )

        server._handle_post(handler)

        payload = handler.json_payload()
        self.assertEqual(handler.status_code, 409)
        self.assertEqual(payload["error"]["code"], "conflict")
        self.assertEqual(payload["error"]["details"]["current"], "fresh-state")
        self.assertEqual(handler.response_headers["ETag"], '"fresh-state"')
        service._handle_control_command.assert_not_called()

    def test_command_endpoint_accepts_matching_if_match_state_token(self) -> None:
        command = ControlCommand(name="set_mode", path="/Mode", value=1, source="http")
        result = ControlResult.applied_result(command)
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(return_value=command),
            _handle_control_command=MagicMock(return_value=result),
            _record_control_api_command_audit=MagicMock(),
            _control_api_state_token=MagicMock(return_value="match-state"),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"set_mode","value":1}',
            headers={"If-Match": 'W/"match-state"'},
        )

        server._handle_post(handler)

        self.assertEqual(handler.status_code, 200)
        self.assertEqual(handler.response_headers["ETag"], '"match-state"')

    def test_health_payload_uses_configured_host_and_auth_flag_before_bind(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.5", port=9000, auth_token="token")

        payload = server.health_payload()

        self.assertEqual(payload["listen_host"], "127.0.0.5")
        self.assertEqual(payload["listen_port"], 9000)
        self.assertTrue(payload["auth_required"])

    def test_capabilities_payload_normalizes_service_result(self) -> None:
        service = SimpleNamespace(
            _control_api_capabilities_payload=MagicMock(
                return_value={
                    "ok": 1,
                    "api_version": "v1",
                    "transport": "http",
                    "auth_required": 1,
                    "command_names": ["set_mode"],
                    "command_sources": ["http"],
                    "state_endpoints": ["/v1/state/summary"],
                    "endpoints": ["/v1/capabilities"],
                    "supported_phase_selections": ["P1"],
                    "features": {"state_reads": 1},
                    "topology": {"backend_mode": "combined"},
                }
            ),
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, auth_token="token")

        payload = server.capabilities_payload()

        self.assertTrue(payload["auth_required"])
        self.assertEqual(payload["command_names"], ["set_mode"])
        self.assertEqual(payload["topology"]["backend_mode"], "combined")

    def test_bound_host_port_falls_back_for_non_tuple_server_address(self) -> None:
        fake_server = SimpleNamespace(server_address="unix")

        host, port = LocalControlApiHttpServer._bound_host_port(fake_server)

        self.assertEqual(host, "")
        self.assertEqual(port, 0)

    def test_command_endpoint_executes_payload_through_service_hooks(self) -> None:
        command = ControlCommand(name="set_mode", path="/Mode", value=1, source="http")
        result = ControlResult.applied_result(command)
        record_audit = MagicMock()
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(return_value=command),
            _handle_control_command=MagicMock(return_value=result),
            _record_control_api_command_audit=record_audit,
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler(
            "/v1/control/command",
            body=b'{"name": "set_mode", "value": 1}',
        )

        server._handle_post(handler)
        payload = handler.json_payload()

        self.assertEqual(handler.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"]["name"], "set_mode")
        self.assertTrue(payload["command"]["command_id"])
        self.assertEqual(payload["result"]["status"], "applied")
        self.assertIsNone(payload["error"])
        sent_payload = service._control_command_from_payload.call_args.args[0]
        self.assertEqual(sent_payload["name"], "set_mode")
        self.assertEqual(sent_payload["value"], 1)
        self.assertTrue(sent_payload["command_id"])
        self.assertEqual(sent_payload["idempotency_key"], "")
        self.assertEqual(service._control_command_from_payload.call_args.kwargs, {"source": "http"})
        handled_command = service._handle_control_command.call_args.args[0]
        self.assertEqual(handled_command.name, "set_mode")
        self.assertEqual(handled_command.path, "/Mode")
        self.assertEqual(handled_command.value, 1)
        self.assertEqual(handled_command.source, "http")
        self.assertTrue(handled_command.command_id)
        record_audit.assert_called_once()
        self.assertEqual(record_audit.call_args.kwargs["status_code"], 200)

    def test_handler_class_routes_get_post_and_logs_messages(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler_class = server._handler_class()
        handler = object.__new__(handler_class)

        with (
            patch.object(server, "_handle_get") as handle_get,
            patch.object(server, "_handle_post") as handle_post,
            patch("venus_evcharger.control.http_api.logging.debug") as debug_mock,
        ):
            handler_class.do_GET(handler)
            handler_class.do_POST(handler)
            handler_class.log_message(handler, "hello %s", "world")

        handle_get.assert_called_once_with(handler)
        handle_post.assert_called_once_with(handler)
        debug_mock.assert_called_once_with("Control API HTTP: " + "hello %s", "world")

    def test_command_endpoint_enforces_bearer_token_when_configured(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, auth_token="secret-token")
        handler = _FakeHandler(
            "/v1/control/command",
            body=b'{"name": "set_mode", "value": 1}',
        )

        server._handle_post(handler)

        self.assertEqual(handler.status_code, 401)
        service._control_command_from_payload.assert_not_called()

    def test_state_and_capabilities_endpoints_enforce_bearer_token_but_health_and_openapi_stay_open(self) -> None:
        service = SimpleNamespace(
            _control_api_capabilities_payload=MagicMock(
                return_value={
                    "ok": True,
                    "api_version": "v1",
                    "transport": "http",
                    "auth_required": True,
                    "command_names": ["set_mode"],
                    "command_sources": ["http"],
                    "state_endpoints": ["/v1/state/summary"],
                    "endpoints": ["/v1/capabilities"],
                    "supported_phase_selections": ["P1"],
                    "features": {"state_reads": True},
                    "topology": {"backend_mode": "combined"},
                }
            ),
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
            _state_api_dbus_diagnostics_payload=MagicMock(
                return_value={"ok": True, "api_version": "v1", "kind": "dbus-diagnostics", "state": {}}
            ),
            _state_api_summary_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "summary", "summary": "x"}),
            _state_api_runtime_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "runtime", "state": {}}),
            _state_api_operational_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "operational", "state": {}}),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, auth_token="secret-token")

        capabilities_handler = _FakeHandler("/v1/capabilities")
        state_handler = _FakeHandler("/v1/state/summary")
        health_handler = _FakeHandler("/v1/control/health")
        openapi_handler = _FakeHandler("/v1/openapi.json")

        server._handle_get(capabilities_handler)
        server._handle_get(state_handler)
        server._handle_get(health_handler)
        server._handle_get(openapi_handler)

        self.assertEqual(capabilities_handler.status_code, 401)
        self.assertEqual(state_handler.status_code, 401)
        self.assertEqual(health_handler.status_code, 200)
        self.assertEqual(openapi_handler.status_code, 200)
        self.assertEqual(state_handler.json_payload()["error"]["code"], "unauthorized")

    def test_events_endpoint_enforces_bearer_token_when_configured(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, auth_token="secret-token")
        handler = _FakeHandler("/v1/events")

        server._handle_get(handler)

        self.assertEqual(handler.status_code, 401)
        self.assertEqual(handler.json_payload()["error"]["code"], "unauthorized")

    def test_additional_state_endpoints_return_payloads(self) -> None:
        service = SimpleNamespace(
            _control_api_capabilities_payload=MagicMock(),
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
            _state_api_config_effective_payload=MagicMock(
                return_value={"ok": True, "api_version": "v1", "kind": "config-effective", "state": {"host": "charger.local"}}
            ),
            _state_api_dbus_diagnostics_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "dbus-diagnostics", "state": {}}),
            _state_api_health_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "health", "state": {"health_code": 0}}),
            _state_api_operational_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "operational", "state": {}}),
            _state_api_runtime_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "runtime", "state": {}}),
            _state_api_summary_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "summary", "summary": "x"}),
            _state_api_topology_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "topology", "state": {"backend_mode": "split"}}),
            _state_api_update_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "update", "state": {"available": False}}),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)

        handlers = {
            "config": _FakeHandler("/v1/state/config-effective"),
            "health": _FakeHandler("/v1/state/health"),
            "topology": _FakeHandler("/v1/state/topology"),
            "update": _FakeHandler("/v1/state/update"),
        }
        for handler in handlers.values():
            server._handle_get(handler)

        self.assertEqual(handlers["config"].json_payload()["kind"], "config-effective")
        self.assertEqual(handlers["health"].json_payload()["kind"], "health")
        self.assertEqual(handlers["topology"].json_payload()["state"]["backend_mode"], "split")
        self.assertEqual(handlers["update"].json_payload()["kind"], "update")

    def test_read_token_allows_gets_but_not_control_post(self) -> None:
        service = SimpleNamespace(
            _control_api_capabilities_payload=MagicMock(
                return_value={
                    "ok": True,
                    "api_version": "v1",
                    "transport": "http",
                    "auth_required": True,
                    "read_auth_required": True,
                    "control_auth_required": True,
                    "auth_scopes": ["read", "control_basic", "control_admin", "update_admin"],
                    "command_names": ["set_mode"],
                    "command_scope_requirements": {"set_mode": "control_basic"},
                    "command_sources": ["http"],
                    "state_endpoints": ["/v1/state/summary"],
                    "endpoints": ["/v1/capabilities"],
                    "available_modes": [0, 1, 2],
                    "supported_phase_selections": ["P1"],
                    "features": {"state_reads": True},
                    "topology": {"backend_mode": "combined"},
                    "versioning": {"stable_endpoints": ["/v1/capabilities"], "experimental_endpoints": ["/v1/events"]},
                }
            ),
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
            _state_api_config_effective_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "config-effective", "state": {}}),
            _state_api_dbus_diagnostics_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "dbus-diagnostics", "state": {}}),
            _state_api_health_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "health", "state": {}}),
            _state_api_operational_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "operational", "state": {}}),
            _state_api_runtime_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "runtime", "state": {}}),
            _state_api_summary_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "summary", "summary": "x"}),
            _state_api_topology_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "topology", "state": {}}),
            _state_api_update_payload=MagicMock(return_value={"ok": True, "api_version": "v1", "kind": "update", "state": {}}),
        )
        server = LocalControlApiHttpServer(
            service,
            host="127.0.0.1",
            port=8765,
            read_token="read-token",
            control_token="control-token",
        )
        read_get = _FakeHandler("/v1/state/summary", authorization="Bearer read-token")
        read_post = _FakeHandler("/v1/control/command", body=b'{"name":"set_mode","value":1}', authorization="Bearer read-token")

        server._handle_get(read_get)
        server._handle_post(read_post)

        self.assertEqual(read_get.status_code, 200)
        self.assertEqual(read_post.status_code, 403)
        self.assertEqual(read_post.json_payload()["error"]["code"], "insufficient_scope")

    def test_localhost_only_rejects_remote_clients(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, localhost_only=True)
        handler = _FakeHandler("/v1/control/health", client_host="192.168.1.10")

        server._handle_get(handler)

        self.assertEqual(handler.status_code, 403)
        self.assertEqual(handler.json_payload()["error"]["code"], "forbidden_remote_client")

    def test_control_post_rejects_remote_clients_and_locality_can_be_disabled(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
        )
        localhost_only_server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, localhost_only=True)
        remote_post = _FakeHandler("/v1/control/command", body=b'{"name":"set_mode","value":1}', client_host="192.168.1.10")

        localhost_only_server._handle_post(remote_post)

        self.assertEqual(remote_post.status_code, 403)
        self.assertIsNone(LocalControlApiHttpServer(service, host="127.0.0.1", port=8765, localhost_only=False)._locality_error(remote_post))

    def test_idempotency_key_replays_matching_payload_and_rejects_conflicts(self) -> None:
        command = ControlCommand(name="set_mode", path="/Mode", value=1, source="http")
        result = ControlResult.applied_result(command)
        record_audit = MagicMock()
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(return_value=command),
            _handle_control_command=MagicMock(return_value=result),
            _record_control_api_command_audit=record_audit,
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        first = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"set_mode","value":1}',
            headers={"Idempotency-Key": "idem-1"},
        )
        replay = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"set_mode","value":1}',
            headers={"Idempotency-Key": "idem-1"},
        )
        conflict = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"set_mode","value":2}',
            headers={"Idempotency-Key": "idem-1"},
        )

        server._handle_post(first)
        server._handle_post(replay)
        server._handle_post(conflict)

        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json_payload()["replayed"])
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json_payload()["replayed"])
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json_payload()["error"]["code"], "idempotency_conflict")
        service._handle_control_command.assert_called_once()
        self.assertEqual(record_audit.call_count, 3)
        self.assertTrue(record_audit.call_args_list[1].kwargs["replayed"])
        self.assertEqual(record_audit.call_args_list[2].kwargs["status_code"], 409)

    def test_idempotency_key_replays_after_server_restart_when_runtime_store_is_shared(self) -> None:
        command = ControlCommand(name="set_mode", path="/Mode", value=1, source="http")
        result = ControlResult.applied_result(command)
        with TemporaryDirectory() as tmpdir:
            store = ControlApiIdempotencyStore(path=f"{tmpdir}/idempotency.json")
            service = SimpleNamespace(
                _control_command_from_payload=MagicMock(return_value=command),
                _handle_control_command=MagicMock(return_value=result),
                _control_api_idempotency_store=lambda: store,
                _record_control_api_command_audit=MagicMock(),
            )
            first_server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
            second_server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
            first = _FakeHandler(
                "/v1/control/command",
                body=b'{"name":"set_mode","value":1}',
                headers={"Idempotency-Key": "idem-restart"},
            )
            replay = _FakeHandler(
                "/v1/control/command",
                body=b'{"name":"set_mode","value":1}',
                headers={"Idempotency-Key": "idem-restart"},
            )

            first_server._handle_post(first)
            second_server._handle_post(replay)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json_payload()["replayed"])
        service._handle_control_command.assert_called_once()

    def test_replayed_idempotent_response_publishes_command_event_when_supported(self) -> None:
        command = ControlCommand(name="set_mode", path="/Mode", value=1, source="http")
        result = ControlResult.applied_result(command)
        publish_event = MagicMock()
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(return_value=command),
            _handle_control_command=MagicMock(return_value=result),
            _publish_control_api_command_event=publish_event,
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        first = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"set_mode","value":1}',
            headers={"Idempotency-Key": "idem-2"},
        )
        replay = _FakeHandler(
            "/v1/control/command",
            body=b'{"name":"set_mode","value":1}',
            headers={"Idempotency-Key": "idem-2"},
        )

        server._handle_post(first)
        server._handle_post(replay)

        publish_event.assert_called_once()
        self.assertTrue(replay.json_payload()["replayed"])

    def test_events_endpoint_streams_snapshot_and_recent_events(self) -> None:
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
            _control_api_state_token=MagicMock(return_value="state-2"),
            _control_api_event_bus=MagicMock(),
            _state_api_event_snapshot_payload=MagicMock(return_value={"summary": {"kind": "summary"}}),
        )
        service._control_api_event_bus.return_value.recent.return_value = [
            {"seq": 1, "api_version": "v1", "kind": "command", "timestamp": 1.0, "payload": {"detail": "ok"}}
        ]
        service._control_api_event_bus.return_value.wait_for_next.return_value = None
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/events?once=1")

        server._handle_get(handler)

        lines = [json.loads(line) for line in handler.wfile.getvalue().decode("utf-8").splitlines()]
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(handler.response_headers["X-Control-Api-Retry-Ms"], "1000")
        self.assertEqual(lines[0]["kind"], "snapshot")
        self.assertEqual(lines[0]["resume_token"], "0")
        self.assertEqual(lines[0]["payload"]["state_token"], "state-2")
        self.assertEqual(lines[1]["kind"], "command")
        self.assertEqual(lines[1]["resume_token"], "1")

    def test_events_endpoint_supports_live_follow_and_query_fallbacks(self) -> None:
        event_bus = MagicMock()
        event_bus.recent.return_value = []
        returned_event = False

        def _wait_for_next(**_kwargs: object) -> dict[str, object] | None:
            nonlocal returned_event
            if returned_event:
                return None
            returned_event = True
            return {"seq": 2, "api_version": "v1", "kind": "state", "timestamp": 2.0, "payload": {"detail": "later"}}

        event_bus.wait_for_next.side_effect = _wait_for_next
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
            _control_api_state_token=MagicMock(return_value="state-3"),
            _control_api_event_bus=MagicMock(return_value=event_bus),
            _state_api_event_snapshot_payload=MagicMock(return_value={"summary": {"kind": "summary"}}),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/events?after=7&resume=9&limit=bad&timeout=bad&heartbeat=bad&once=0")

        server._handle_get(handler)

        lines = [json.loads(line) for line in handler.wfile.getvalue().decode("utf-8").splitlines()]
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(lines[0]["kind"], "state")
        self.assertEqual(lines[0]["resume_token"], "2")
        event_bus.recent.assert_called_once_with(limit=20, after_seq=9)
        event_bus.wait_for_next.assert_called()

    def test_events_endpoint_emits_heartbeat_when_live_follow_is_idle(self) -> None:
        event_bus = MagicMock()
        event_bus.recent.return_value = []
        event_bus.wait_for_next.return_value = None
        service = SimpleNamespace(
            _control_command_from_payload=MagicMock(),
            _handle_control_command=MagicMock(),
            _control_api_state_token=MagicMock(return_value="state-4"),
            _control_api_event_bus=MagicMock(return_value=event_bus),
            _state_api_event_snapshot_payload=MagicMock(return_value={"summary": {"kind": "summary"}}),
        )
        server = LocalControlApiHttpServer(service, host="127.0.0.1", port=8765)
        handler = _FakeHandler("/v1/events?after=4&timeout=0.02&heartbeat=0.01")

        server._handle_get(handler)

        lines = [json.loads(line) for line in handler.wfile.getvalue().decode("utf-8").splitlines()]
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(lines[0]["kind"], "heartbeat")
        self.assertEqual(lines[0]["resume_token"], "4")
        self.assertTrue(lines[0]["payload"]["alive"])
        self.assertEqual(lines[0]["payload"]["retry_hint_ms"], 250)
        self.assertEqual(lines[0]["payload"]["resume_hint"], "4")

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
