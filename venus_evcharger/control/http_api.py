# SPDX-License-Identifier: GPL-3.0-or-later
"""Local stdlib HTTP adapter for Control API v1."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import socketserver
import stat
import threading
import time
import uuid
from dataclasses import asdict, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping, cast
from urllib.parse import parse_qs, urlsplit

from venus_evcharger.control.models import ControlCommand, ControlResult
from venus_evcharger.control.openapi import build_control_api_openapi_spec
from venus_evcharger.core.contracts import (
    normalized_control_api_capabilities_fields,
    normalized_control_api_command_response_fields,
    normalized_control_api_error_fields,
    normalized_control_api_event_fields,
    normalized_control_api_health_fields,
    normalized_control_command_fields,
    normalized_control_result_fields,
)


class _ThreadingLocalControlHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _ThreadingLocalControlUnixHttpServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class LocalControlApiHttpServer:
    """Expose one tiny local HTTP surface for Control API v1."""

    _STATE_GET_ENDPOINTS = frozenset(
        {
            "/v1/state/config-effective",
            "/v1/state/dbus-diagnostics",
            "/v1/state/health",
            "/v1/state/operational",
            "/v1/state/runtime",
            "/v1/state/summary",
            "/v1/state/topology",
            "/v1/state/update",
        }
    )
    _LOCALITY_FORBIDDEN = (HTTPStatus.FORBIDDEN, "forbidden_remote_client", "Remote clients are not allowed for this API.")
    _UNAUTHORIZED_ERROR = (HTTPStatus.UNAUTHORIZED, "unauthorized", "Unauthorized.")
    _INSUFFICIENT_SCOPE_ERROR = (
        HTTPStatus.FORBIDDEN,
        "insufficient_scope",
        "A control token is required for this endpoint.",
    )

    def __init__(
        self,
        service: Any,
        *,
        host: str,
        port: int,
        auth_token: str = "",
        read_token: str = "",
        control_token: str = "",
        localhost_only: bool = True,
        unix_socket_path: str = "",
    ) -> None:
        self._service = service
        self._host = host
        self._port = int(port)
        self._auth_token = auth_token.strip()
        self._read_token = read_token.strip()
        self._control_token = control_token.strip()
        self._localhost_only = bool(localhost_only)
        self._unix_socket_path = unix_socket_path.strip()
        self._server: _ThreadingLocalControlHttpServer | _ThreadingLocalControlUnixHttpServer | None = None
        self._thread: threading.Thread | None = None
        self._idempotency_cache: dict[str, tuple[str, int, dict[str, Any], ControlCommand | None, ControlResult | None]] = {}
        self.bound_host = ""
        self.bound_port = 0
        self.bound_unix_socket_path = ""

    @staticmethod
    def _bound_host_port(
        server: _ThreadingLocalControlHttpServer | _ThreadingLocalControlUnixHttpServer,
    ) -> tuple[str, int]:
        address = server.server_address
        if isinstance(address, tuple) and len(address) >= 2:
            return str(address[0]), int(address[1])
        return "", 0

    def start(self) -> None:
        """Start the background HTTP server once."""
        if self._server is not None:
            return
        server = self._build_server()
        self._server = server
        if self._unix_socket_path:
            self.bound_unix_socket_path = self._unix_socket_path
            self.bound_host = ""
            self.bound_port = 0
            listen_target = f"unix://{self.bound_unix_socket_path}"
        else:
            self.bound_host, self.bound_port = self._bound_host_port(server)
            self.bound_unix_socket_path = ""
            listen_target = f"http://{self.bound_host}:{self.bound_port}"
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="venus-evcharger-control-api",
            daemon=True,
        )
        self._thread.start()
        logging.info("Started local Control API v1 on %s", listen_target)

    def stop(self) -> None:
        """Stop the background HTTP server when one is running."""
        server = self._server
        thread = self._thread
        socket_path = self.bound_unix_socket_path
        self._server = None
        self._thread = None
        self.bound_host = ""
        self.bound_port = 0
        self.bound_unix_socket_path = ""
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=1.0)
        if socket_path and os.path.exists(socket_path):
            os.unlink(socket_path)

    def health_payload(self) -> dict[str, Any]:
        """Return one stable health payload for the local HTTP adapter."""
        read_auth_required = bool(self._effective_read_token())
        control_auth_required = bool(self._effective_control_token())
        return normalized_control_api_health_fields(
            {
                "ok": True,
                "api_version": "v1",
                "transport": "http",
                "listen_host": self.bound_host or self._host,
                "listen_port": int(self.bound_port or self._port),
                "auth_required": bool(read_auth_required or control_auth_required),
                "read_auth_required": read_auth_required,
                "control_auth_required": control_auth_required,
                "localhost_only": self._localhost_only,
                "unix_socket_path": self.bound_unix_socket_path or self._unix_socket_path,
            }
        )

    def capabilities_payload(self) -> dict[str, Any]:
        """Return one stable capabilities payload for the local HTTP adapter."""
        payload = self._service._control_api_capabilities_payload()
        return normalized_control_api_capabilities_fields(payload)

    @staticmethod
    def openapi_payload() -> dict[str, Any]:
        """Return the OpenAPI 3.1 description for this local HTTP surface."""
        return build_control_api_openapi_spec()

    def execute_payload(self, payload: dict[str, Any]) -> tuple[ControlCommand, ControlResult]:
        """Build and execute one Control API command from one JSON payload."""
        command = self._service._control_command_from_payload(payload, source="http")
        if not command.command_id or command.idempotency_key != str(payload.get("idempotency_key", "")).strip():
            command = replace(
                command,
                command_id=str(payload.get("command_id", "")).strip(),
                idempotency_key=str(payload.get("idempotency_key", "")).strip(),
            )
        result = self._service._handle_control_command(command)
        return command, result

    def _build_server(self) -> _ThreadingLocalControlHttpServer | _ThreadingLocalControlUnixHttpServer:
        if not self._unix_socket_path:
            return _ThreadingLocalControlHttpServer((self._host, self._port), self._handler_class())
        self._prepare_unix_socket_path(self._unix_socket_path)
        return _ThreadingLocalControlUnixHttpServer(self._unix_socket_path, self._handler_class())

    @staticmethod
    def _prepare_unix_socket_path(path: str) -> None:
        if not os.path.exists(path):
            return
        mode = os.stat(path).st_mode
        if not stat.S_ISSOCK(mode):
            raise ValueError(f"Control API unix socket path already exists and is not a socket: {path}")
        os.unlink(path)

    def _state_payload(self, path: str) -> dict[str, Any]:
        payload_getter_names: dict[str, str] = {
            "/v1/state/config-effective": "_state_api_config_effective_payload",
            "/v1/state/dbus-diagnostics": "_state_api_dbus_diagnostics_payload",
            "/v1/state/health": "_state_api_health_payload",
            "/v1/state/operational": "_state_api_operational_payload",
            "/v1/state/runtime": "_state_api_runtime_payload",
            "/v1/state/summary": "_state_api_summary_payload",
            "/v1/state/topology": "_state_api_topology_payload",
            "/v1/state/update": "_state_api_update_payload",
        }
        getter = cast(Callable[[], Any], getattr(self._service, payload_getter_names[path]))
        return cast(dict[str, Any], getter())

    def _public_get_payload(self, path: str) -> dict[str, Any] | None:
        if path == "/v1/control/health":
            return self.health_payload()
        if path == "/v1/openapi.json":
            return self.openapi_payload()
        return None

    def _authorized_get_payload(self, path: str) -> dict[str, Any] | None:
        if path == "/v1/capabilities":
            return self.capabilities_payload()
        if path in self._STATE_GET_ENDPOINTS:
            return self._state_payload(path)
        return None

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                owner._handle_get(self)

            def do_POST(self) -> None:  # noqa: N802
                owner._handle_post(self)

            def log_message(self, format: str, *args: Any) -> None:
                logging.debug("Control API HTTP: " + format, *args)

        return _Handler

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        path, params = self._parsed_request_target(handler.path)
        error = self._locality_error(handler)
        if error is not None:
            self._write_error(handler, *error)
            return
        public_payload = self._public_get_payload(path)
        if public_payload is not None:
            self._write_json(handler, HTTPStatus.OK, public_payload)
            return
        if path == "/v1/events":
            self._handle_events_get(handler, params)
            return
        authorized_payload = self._authorized_get_payload(path)
        if authorized_payload is not None:
            self._handle_authorized_get(handler, authorized_payload)
            return
        self._write_error(handler, HTTPStatus.NOT_FOUND, "not_found", "Not found.")

    def _handle_events_get(self, handler: BaseHTTPRequestHandler, params: dict[str, list[str]]) -> None:
        auth_error = self._auth_error(handler, required_scope="read")
        if auth_error is not None:
            self._write_error(handler, *auth_error)
            return
        self._write_event_stream(handler, params)

    def _handle_authorized_get(self, handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> None:
        auth_error = self._auth_error(handler, required_scope="read")
        if auth_error is not None:
            self._write_error(handler, *auth_error)
            return
        self._write_json(handler, HTTPStatus.OK, payload)

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        path, _params = self._parsed_request_target(handler.path)
        if path != "/v1/control/command":
            self._write_error(handler, HTTPStatus.NOT_FOUND, "not_found", "Not found.")
            return
        access_error = self._locality_error(handler)
        if access_error is not None:
            self._write_error(handler, *access_error)
            return
        auth_error = self._auth_error(handler, required_scope="control")
        if auth_error is not None:
            self._write_error(handler, *auth_error)
            return
        payload = self._read_json_payload(handler)
        if isinstance(payload, dict):
            self._write_command_result(handler, payload)

    @staticmethod
    def _parsed_request_target(target: str) -> tuple[str, dict[str, list[str]]]:
        parts = urlsplit(target)
        return parts.path, parse_qs(parts.query, keep_blank_values=True)

    def _locality_error(self, handler: BaseHTTPRequestHandler) -> tuple[HTTPStatus, str, str] | None:
        if not self._localhost_only or self._unix_socket_path:
            return None
        host = self._client_host(handler)
        return None if self._is_loopback_host(host) else self._LOCALITY_FORBIDDEN

    @staticmethod
    def _client_host(handler: BaseHTTPRequestHandler) -> str:
        client_address = getattr(handler, "client_address", ("127.0.0.1", 0))
        if isinstance(client_address, tuple) and client_address:
            return str(client_address[0])
        return "127.0.0.1"

    @staticmethod
    def _is_loopback_host(host: str) -> bool:
        if host in {"localhost", "::1"}:
            return True
        try:
            return bool(ipaddress.ip_address(host).is_loopback)
        except ValueError:
            return False

    def _auth_error(self, handler: BaseHTTPRequestHandler, *, required_scope: str) -> tuple[HTTPStatus, str, str] | None:
        scope = self._authorization_scope(handler)
        if self._scope_satisfies_requirement(scope, required_scope):
            return None
        if scope == "read" and required_scope == "control":
            return self._INSUFFICIENT_SCOPE_ERROR
        return self._UNAUTHORIZED_ERROR

    @staticmethod
    def _scope_satisfies_requirement(scope: str | None, required_scope: str) -> bool:
        if required_scope == "read":
            return scope in {"read", "control"}
        return scope == "control"

    def _authorization_scope(self, handler: BaseHTTPRequestHandler) -> str | None:
        read_token = self._effective_read_token()
        control_token = self._effective_control_token()
        if not read_token and not control_token:
            return "control"
        header = handler.headers.get("Authorization", "").strip()
        if self._matches_bearer_token(header, control_token):
            return "control"
        if self._matches_bearer_token(header, read_token):
            return "read"
        return None

    @staticmethod
    def _matches_bearer_token(header: str, token: str) -> bool:
        return bool(token) and header == f"Bearer {token}"

    def _effective_read_token(self) -> str:
        return self._read_token or self._control_token or self._auth_token

    def _effective_control_token(self) -> str:
        return self._control_token or self._auth_token

    def _read_json_payload(self, handler: BaseHTTPRequestHandler) -> dict[str, Any] | None:
        try:
            content_length = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            self._write_error(handler, HTTPStatus.BAD_REQUEST, "invalid_content_length", "Invalid Content-Length.")
            return None
        try:
            raw_payload = handler.rfile.read(max(0, content_length))
            parsed = json.loads(raw_payload.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._write_error(handler, HTTPStatus.BAD_REQUEST, "invalid_json", "Invalid JSON body.")
            return None
        if not isinstance(parsed, dict):
            self._write_error(handler, HTTPStatus.BAD_REQUEST, "invalid_payload", "JSON body must be an object.")
            return None
        return parsed

    def _write_command_result(self, handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> None:
        tracked_payload = self._tracked_payload(handler, payload)
        replay = self._replayed_response(tracked_payload)
        if replay is not None:
            self._write_json(handler, replay[0], replay[1])
            return
        try:
            command, result = self.execute_payload(tracked_payload)
        except ValueError as error:
            self._write_error(handler, HTTPStatus.BAD_REQUEST, "invalid_payload", str(error))
            return
        status = self._http_status_for_result(result)
        response_payload = self._command_response_payload(command, result, replayed=False)
        self._cache_idempotent_response(tracked_payload, status, response_payload, command, result)
        self._write_json(handler, status, response_payload)

    def _tracked_payload(self, handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> dict[str, Any]:
        tracked = dict(payload)
        command_id = str(tracked.get("command_id", "")).strip() or handler.headers.get("X-Command-Id", "").strip()
        idempotency_key = str(tracked.get("idempotency_key", "")).strip() or handler.headers.get("Idempotency-Key", "").strip()
        tracked["command_id"] = command_id or uuid.uuid4().hex
        tracked["idempotency_key"] = idempotency_key
        return tracked

    def _replayed_response(self, payload: dict[str, Any]) -> tuple[HTTPStatus, dict[str, Any]] | None:
        idempotency_key = str(payload.get("idempotency_key", "")).strip()
        if not idempotency_key:
            return None
        cached = self._cached_idempotent_response(idempotency_key)
        if cached is None:
            return None
        fingerprint, status, response_payload, command, result = cached
        if fingerprint != self._idempotency_fingerprint(payload):
            return self._idempotency_conflict_response(idempotency_key)
        replayed_payload = self._replayed_payload(response_payload)
        self._publish_replayed_command_event(command, result)
        return (HTTPStatus(status), replayed_payload)

    def _cached_idempotent_response(
        self,
        idempotency_key: str,
    ) -> tuple[str, int, dict[str, Any], ControlCommand | None, ControlResult | None] | None:
        return self._idempotency_cache.get(idempotency_key)

    @staticmethod
    def _idempotency_conflict_response(idempotency_key: str) -> tuple[HTTPStatus, dict[str, Any]]:
        message = "Idempotency-Key was already used for a different payload."
        return (
            HTTPStatus.CONFLICT,
            normalized_control_api_command_response_fields(
                {
                    "ok": False,
                    "detail": message,
                    "error": {
                        "code": "idempotency_conflict",
                        "message": message,
                        "retryable": False,
                        "details": {"idempotency_key": idempotency_key},
                    },
                }
            ),
        )

    @staticmethod
    def _replayed_payload(response_payload: dict[str, Any]) -> dict[str, Any]:
        return normalized_control_api_command_response_fields({**response_payload, "replayed": True})

    def _publish_replayed_command_event(
        self,
        command: ControlCommand | None,
        result: ControlResult | None,
    ) -> None:
        publish_event = getattr(self._service, "_publish_control_api_command_event", None)
        if not callable(publish_event) or command is None or result is None:
            return
        publish_event(command, result, replayed=True)

    def _cache_idempotent_response(
        self,
        payload: dict[str, Any],
        status: HTTPStatus,
        response_payload: dict[str, Any],
        command: ControlCommand,
        result: ControlResult,
    ) -> None:
        idempotency_key = str(payload.get("idempotency_key", "")).strip()
        if not idempotency_key:
            return
        self._idempotency_cache[idempotency_key] = (
            self._idempotency_fingerprint(payload),
            int(status),
            dict(response_payload),
            command,
            result,
        )

    @staticmethod
    def _idempotency_fingerprint(payload: dict[str, Any]) -> str:
        comparable = {
            key: value
            for key, value in payload.items()
            if key not in {"command_id", "idempotency_key"}
        }
        return json.dumps(comparable, sort_keys=True, separators=(",", ":"), default=str)

    def _write_event_stream(self, handler: BaseHTTPRequestHandler, params: dict[str, list[str]]) -> None:
        event_bus = self._service._control_api_event_bus()
        limit = self._query_int(params, "limit", 20)
        after_seq = self._query_int(params, "after", 0)
        timeout = self._query_float(params, "timeout", 5.0)
        once = self._query_bool(params, "once", False)
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "application/x-ndjson")
        handler.send_header("Cache-Control", "no-cache")
        handler.end_headers()
        last_seq = self._write_initial_event_snapshot(handler, after_seq)
        last_seq = self._write_recent_events(handler, event_bus, limit=limit, after_seq=last_seq)
        if not once:
            self._write_live_events(handler, event_bus, after_seq=last_seq, timeout=timeout)

    def _write_initial_event_snapshot(self, handler: BaseHTTPRequestHandler, after_seq: int) -> int:
        if after_seq > 0:
            return after_seq
        self._write_event_line(
            handler,
            normalized_control_api_event_fields(
                {
                    "seq": 0,
                    "api_version": "v1",
                    "kind": "snapshot",
                    "timestamp": time.time(),
                    "payload": self._service._state_api_event_snapshot_payload(),
                }
            ),
        )
        return after_seq

    def _write_recent_events(
        self,
        handler: BaseHTTPRequestHandler,
        event_bus: Any,
        *,
        limit: int,
        after_seq: int,
    ) -> int:
        last_seq = after_seq
        for event in event_bus.recent(limit=limit, after_seq=after_seq):
            self._write_event_line(handler, event)
            last_seq = max(last_seq, int(event["seq"]))
        return last_seq

    def _write_live_events(
        self,
        handler: BaseHTTPRequestHandler,
        event_bus: Any,
        *,
        after_seq: int,
        timeout: float,
    ) -> None:
        deadline = time.time() + max(0.0, timeout)
        last_seq = after_seq
        while time.time() < deadline:
            event = event_bus.wait_for_next(after_seq=last_seq, timeout=max(0.0, deadline - time.time()))
            if event is None:
                return
            self._write_event_line(handler, event)
            last_seq = max(last_seq, int(event["seq"]))

    @staticmethod
    def _query_int(params: dict[str, list[str]], key: str, default: int) -> int:
        try:
            return max(0, int(params.get(key, [str(default)])[0]))
        except ValueError:
            return default

    @staticmethod
    def _query_float(params: dict[str, list[str]], key: str, default: float) -> float:
        try:
            return max(0.0, float(params.get(key, [str(default)])[0]))
        except ValueError:
            return default

    @staticmethod
    def _query_bool(params: dict[str, list[str]], key: str, default: bool) -> bool:
        raw = params.get(key, ["1" if default else "0"])[0].strip().lower()
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _write_event_line(handler: BaseHTTPRequestHandler, event: Mapping[str, Any]) -> None:
        handler.wfile.write((json.dumps(dict(event), sort_keys=True) + "\n").encode("utf-8"))
        handler.wfile.flush()

    def _command_response_payload(self, command: ControlCommand, result: ControlResult, *, replayed: bool) -> dict[str, Any]:
        error_payload = None
        if not result.accepted:
            error_payload = normalized_control_api_error_fields(
                {
                    "code": "command_rejected" if self._http_status_for_result(result) == HTTPStatus.CONFLICT else "conflict",
                    "message": result.detail or "Command rejected.",
                    "retryable": result.reversible_failure,
                    "details": {
                        "status": result.status,
                        "path": result.command.path,
                        "command_id": result.command.command_id,
                        "idempotency_key": result.command.idempotency_key,
                    },
                }
            )
        return normalized_control_api_command_response_fields(
            {
                "ok": bool(result.accepted),
                "detail": result.detail,
                "replayed": replayed,
                "command": self._command_payload(command),
                "result": self._result_payload(result),
                "error": error_payload,
            }
        )

    @staticmethod
    def _http_status_for_result(result: ControlResult) -> HTTPStatus:
        if result.status == "applied":
            return HTTPStatus.OK
        if result.status == "accepted_in_flight":
            return HTTPStatus.ACCEPTED
        return HTTPStatus.CONFLICT

    @staticmethod
    def _command_payload(command: ControlCommand) -> dict[str, Any]:
        return normalized_control_command_fields(asdict(command), default_source="http")

    @staticmethod
    def _result_payload(result: ControlResult) -> dict[str, Any]:
        return normalized_control_result_fields(asdict(result))

    @staticmethod
    def _write_error(
        handler: BaseHTTPRequestHandler,
        status: HTTPStatus,
        code: str,
        message: str,
    ) -> None:
        payload = normalized_control_api_command_response_fields(
            {
                "ok": False,
                "detail": message,
                "error": {
                    "code": code,
                    "message": message,
                    "retryable": False,
                    "details": {},
                },
            }
        )
        LocalControlApiHttpServer._write_json(handler, status, payload)

    @staticmethod
    def _write_json(
        handler: BaseHTTPRequestHandler,
        status: HTTPStatus,
        payload: Mapping[str, Any],
    ) -> None:
        raw = json.dumps(dict(payload), sort_keys=True).encode("utf-8")
        handler.send_response(int(status))
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)
