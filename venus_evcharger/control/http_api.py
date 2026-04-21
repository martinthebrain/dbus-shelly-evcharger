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

from venus_evcharger.control.idempotency import ControlApiIdempotencyStore
from venus_evcharger.control.models import ControlCommand, ControlResult
from venus_evcharger.control.openapi import build_control_api_openapi_spec
from venus_evcharger.control.rate_limit import ControlApiRateLimiter
from venus_evcharger.core.contracts import (
    CONTROL_API_EVENT_KINDS,
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
            "/v1/state/build",
            "/v1/state/config-effective",
            "/v1/state/contracts",
            "/v1/state/dbus-diagnostics",
            "/v1/state/health",
            "/v1/state/healthz",
            "/v1/state/operational",
            "/v1/state/runtime",
            "/v1/state/summary",
            "/v1/state/topology",
            "/v1/state/update",
            "/v1/state/version",
        }
    )
    _LOCALITY_FORBIDDEN = (HTTPStatus.FORBIDDEN, "forbidden_remote_client", "Remote clients are not allowed for this API.")
    _UNAUTHORIZED_ERROR = (HTTPStatus.UNAUTHORIZED, "unauthorized", "Unauthorized.")
    _INSUFFICIENT_SCOPE_ERROR = (
        HTTPStatus.FORBIDDEN,
        "insufficient_scope",
        "The supplied token does not grant the required scope for this endpoint.",
    )
    _RETRY_HEADER = "X-Control-Api-Retry-Ms"
    _STATE_TOKEN_HEADER = "X-State-Token"
    _COMMAND_SCOPE_REQUIREMENTS: dict[str, str] = {
        "legacy_unknown_write": "control_admin",
        "reset_contactor_lockout": "control_admin",
        "reset_phase_lockout": "control_admin",
        "set_auto_runtime_setting": "control_admin",
        "set_auto_start": "control_basic",
        "set_current_setting": "control_basic",
        "set_enable": "control_basic",
        "set_mode": "control_basic",
        "set_phase_selection": "control_basic",
        "set_start_stop": "control_basic",
        "trigger_software_update": "update_admin",
    }
    _SCOPE_ORDER: dict[str, int] = {
        "read": 0,
        "control_basic": 1,
        "control_admin": 2,
        "update_admin": 3,
    }

    def __init__(
        self,
        service: Any,
        *,
        host: str,
        port: int,
        auth_token: str = "",
        read_token: str = "",
        control_token: str = "",
        admin_token: str = "",
        update_token: str = "",
        localhost_only: bool = True,
        unix_socket_path: str = "",
    ) -> None:
        self._service = service
        self._host = host
        self._port = int(port)
        self._auth_token = auth_token.strip()
        self._read_token = read_token.strip()
        self._control_token = control_token.strip()
        self._admin_token = admin_token.strip()
        self._update_token = update_token.strip()
        self._localhost_only = bool(localhost_only)
        self._unix_socket_path = unix_socket_path.strip()
        self._server: _ThreadingLocalControlHttpServer | _ThreadingLocalControlUnixHttpServer | None = None
        self._thread: threading.Thread | None = None
        self._fallback_idempotency_store = ControlApiIdempotencyStore()
        self._fallback_rate_limiter = ControlApiRateLimiter()
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
            "/v1/state/build": "_state_api_build_payload",
            "/v1/state/config-effective": "_state_api_config_effective_payload",
            "/v1/state/contracts": "_state_api_contracts_payload",
            "/v1/state/dbus-diagnostics": "_state_api_dbus_diagnostics_payload",
            "/v1/state/health": "_state_api_health_payload",
            "/v1/state/healthz": "_state_api_healthz_payload",
            "/v1/state/operational": "_state_api_operational_payload",
            "/v1/state/runtime": "_state_api_runtime_payload",
            "/v1/state/summary": "_state_api_summary_payload",
            "/v1/state/topology": "_state_api_topology_payload",
            "/v1/state/update": "_state_api_update_payload",
            "/v1/state/version": "_state_api_version_payload",
        }
        getter = cast(Callable[[], Any], getattr(self._service, payload_getter_names[path]))
        return cast(dict[str, Any], getter())

    def _public_get_payload(self, path: str) -> dict[str, Any] | None:
        if path == "/v1/control/health":
            return self.health_payload()
        if path == "/v1/state/healthz":
            return self._state_payload(path)
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
            self._write_json(handler, HTTPStatus.OK, public_payload, extra_headers=self._state_token_headers())
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
        self._write_json(handler, HTTPStatus.OK, payload, extra_headers=self._state_token_headers())

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        path, _params = self._parsed_request_target(handler.path)
        payload = self._validated_command_post_payload(handler, path)
        if payload is not None:
            self._write_command_result(handler, payload)

    def _validated_command_post_payload(
        self,
        handler: BaseHTTPRequestHandler,
        path: str,
    ) -> dict[str, Any] | None:
        if self._write_post_access_error(handler, path):
            return None
        payload = self._command_post_payload(handler)
        if payload is None:
            return None
        if self._write_command_post_auth_error(handler, payload):
            return None
        if self._write_optimistic_concurrency_error(handler):
            return None
        return payload

    def _command_post_payload(self, handler: BaseHTTPRequestHandler) -> dict[str, Any] | None:
        payload = self._read_json_payload(handler)
        if isinstance(payload, dict):
            return payload
        return None

    def _write_post_target_error(self, handler: BaseHTTPRequestHandler, path: str) -> bool:
        post_target_error = self._post_target_error(path)
        if post_target_error is None:
            return False
        self._write_error(handler, *post_target_error)
        return True

    def _write_post_access_error(self, handler: BaseHTTPRequestHandler, path: str) -> bool:
        return self._write_post_target_error(handler, path) or self._write_locality_error(handler)

    def _write_locality_error(self, handler: BaseHTTPRequestHandler) -> bool:
        access_error = self._locality_error(handler)
        if access_error is None:
            return False
        self._write_error(handler, *access_error)
        return True

    def _write_command_post_auth_error(
        self,
        handler: BaseHTTPRequestHandler,
        payload: dict[str, Any],
    ) -> bool:
        auth_error = self._command_post_auth_error(handler, payload)
        if auth_error is None:
            return False
        self._write_error(handler, *auth_error)
        return True

    def _write_optimistic_concurrency_error(self, handler: BaseHTTPRequestHandler) -> bool:
        concurrency_error = self._optimistic_concurrency_error(handler)
        if concurrency_error is None:
            return False
        status, response_payload, headers = concurrency_error
        self._write_json(handler, status, response_payload, extra_headers=headers)
        return True

    @staticmethod
    def _post_target_error(path: str) -> tuple[HTTPStatus, str, str] | None:
        if path == "/v1/control/command":
            return None
        return HTTPStatus.NOT_FOUND, "not_found", "Not found."

    def _command_post_auth_error(
        self,
        handler: BaseHTTPRequestHandler,
        payload: dict[str, Any],
    ) -> tuple[HTTPStatus, str, str] | None:
        return self._auth_error(
            handler,
            required_scope=self._required_scope_for_command_payload(payload),
        )

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
        if scope is not None and required_scope != "read":
            return self._INSUFFICIENT_SCOPE_ERROR
        return self._UNAUTHORIZED_ERROR

    @staticmethod
    def _scope_satisfies_requirement(scope: str | None, required_scope: str) -> bool:
        if scope is None:
            return False
        return LocalControlApiHttpServer._SCOPE_ORDER.get(scope, -1) >= LocalControlApiHttpServer._SCOPE_ORDER.get(
            required_scope,
            999,
        )

    def _authorization_scope(self, handler: BaseHTTPRequestHandler) -> str | None:
        scope_tokens = self._scope_tokens()
        if not any(token for _scope_name, token in scope_tokens):
            return "update_admin"
        header = handler.headers.get("Authorization", "").strip()
        for scope_name, token in scope_tokens:
            if self._matches_bearer_token(header, token):
                return scope_name
        return None

    def _scope_tokens(self) -> tuple[tuple[str, str], ...]:
        return (
            ("update_admin", self._effective_update_token()),
            ("control_admin", self._effective_admin_token()),
            ("control_basic", self._effective_control_token()),
            ("read", self._effective_read_token()),
        )

    @staticmethod
    def _matches_bearer_token(header: str, token: str) -> bool:
        return bool(token) and header == f"Bearer {token}"

    def _effective_read_token(self) -> str:
        return self._read_token or self._control_token or self._admin_token or self._update_token or self._auth_token

    def _effective_control_token(self) -> str:
        return self._control_token or self._auth_token

    def _effective_admin_token(self) -> str:
        return self._admin_token or self._control_token or self._auth_token

    def _effective_update_token(self) -> str:
        return self._update_token or self._admin_token or self._control_token or self._auth_token

    def _required_scope_for_command_payload(self, payload: dict[str, Any]) -> str:
        command_name = str(payload.get("name", "")).strip()
        if not command_name and "path" in payload:
            try:
                command_name = self._service._control_command_from_payload(
                    {**payload, "command_id": "", "idempotency_key": ""},
                    source="http",
                ).name
            except ValueError:
                return "control_admin"
        return self._COMMAND_SCOPE_REQUIREMENTS.get(command_name, "control_admin")

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
        client_host = self._client_host(handler)
        replay = self._replayed_response(tracked_payload)
        if replay is not None:
            self._record_command_audit(
                command=replay[1].get("command"),
                result=replay[1].get("result"),
                error=replay[1].get("error"),
                replayed=True,
                scope="control",
                client_host=client_host,
                status_code=int(replay[0]),
            )
            self._write_json(handler, replay[0], replay[1], extra_headers=self._state_token_headers())
            return
        try:
            command = self._service._control_command_from_payload(tracked_payload, source="http")
            command = self._tracked_command(tracked_payload, command)
        except ValueError as error:
            error_message = str(error)
            response_payload = self._error_response_payload(self._payload_error_code(error_message), error_message)
            self._record_command_audit(
                command=tracked_payload,
                result=None,
                error=cast(dict[str, Any] | None, response_payload.get("error")),
                replayed=False,
                scope="control",
                client_host=client_host,
                status_code=int(HTTPStatus.BAD_REQUEST),
            )
            self._write_json(handler, HTTPStatus.BAD_REQUEST, response_payload, extra_headers=self._state_token_headers())
            return
        rate_limit_error = self._rate_limit_error(client_host, command.name)
        if rate_limit_error is not None:
            status, response_payload, headers = rate_limit_error
            self._record_command_audit(
                command=command,
                result=None,
                error=cast(dict[str, Any] | None, response_payload.get("error")),
                replayed=False,
                scope="control",
                client_host=client_host,
                status_code=int(status),
            )
            self._write_json(handler, status, response_payload, extra_headers={**self._state_token_headers(), **headers})
            return
        result = self._service._handle_control_command(command)
        status = self._http_status_for_result(result)
        response_payload = self._command_response_payload(command, result, replayed=False)
        self._cache_idempotent_response(tracked_payload, status, response_payload, command, result)
        self._record_command_audit(
            command=command,
            result=result,
            error=cast(dict[str, Any] | None, response_payload.get("error")),
            replayed=False,
            scope="control",
            client_host=client_host,
            status_code=int(status),
        )
        self._write_json(handler, status, response_payload, extra_headers=self._state_token_headers())

    @staticmethod
    def _tracked_command(payload: dict[str, Any], command: ControlCommand) -> ControlCommand:
        if not command.command_id or command.idempotency_key != str(payload.get("idempotency_key", "")).strip():
            return replace(
                command,
                command_id=str(payload.get("command_id", "")).strip(),
                idempotency_key=str(payload.get("idempotency_key", "")).strip(),
            )
        return command

    @staticmethod
    def _payload_error_code(message: str) -> str:
        lowered = message.lower()
        if "unsupported control command" in lowered or "unsupported control path" in lowered:
            return "unsupported_command"
        if "does not support path" in lowered or "requires one of:" in lowered:
            return "unsupported_command"
        return "validation_error"

    def _rate_limit_error(
        self,
        client_host: str,
        command_name: str,
    ) -> tuple[HTTPStatus, dict[str, Any], dict[str, str]] | None:
        client_key = client_host if client_host else "local"
        request_allowed, retry_after = self._rate_limiter().allow_request(client_key)
        if not request_allowed:
            return self._throttled_response(
                "rate_limited",
                "Too many control requests in a short time window.",
                retry_after,
            )
        command_allowed, retry_after = self._rate_limiter().allow_command(client_key, command_name)
        if command_allowed:
            return None
        return self._throttled_response(
            "cooldown_active",
            f"Command '{command_name}' is temporarily cooling down.",
            retry_after,
        )

    def _rate_limiter(self) -> ControlApiRateLimiter:
        rate_limiter_factory = getattr(self._service, "_control_api_rate_limiter", None)
        if callable(rate_limiter_factory):
            return cast(ControlApiRateLimiter, rate_limiter_factory())
        return self._fallback_rate_limiter

    @staticmethod
    def _throttled_response(
        code: str,
        message: str,
        retry_after: float,
    ) -> tuple[HTTPStatus, dict[str, Any], dict[str, str]]:
        retry_seconds = max(1, int(retry_after) if retry_after.is_integer() else int(retry_after) + 1)
        payload = normalized_control_api_command_response_fields(
            {
                "ok": False,
                "detail": message,
                "error": {
                    "code": code,
                    "message": message,
                    "retryable": True,
                    "details": {"retry_after_seconds": retry_after},
                },
            }
        )
        return HTTPStatus.TOO_MANY_REQUESTS, payload, {"Retry-After": str(retry_seconds)}

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
    ) -> tuple[str, int, dict[str, Any], dict[str, Any] | None, dict[str, Any] | None] | None:
        cached = self._idempotency_store().get(idempotency_key)
        if cached is None:
            return None
        fingerprint, status, response_payload = cached
        command = response_payload.get("command")
        result = response_payload.get("result")
        return fingerprint, status, response_payload, command if isinstance(command, dict) else None, result if isinstance(result, dict) else None

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
        command: Any,
        result: Any,
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
        persisted_response = dict(response_payload)
        persisted_response["command"] = self._command_payload(command)
        persisted_response["result"] = self._result_payload(result)
        self._idempotency_store().put(
            idempotency_key,
            self._idempotency_fingerprint(payload),
            int(status),
            persisted_response,
        )

    def _idempotency_store(self) -> ControlApiIdempotencyStore:
        store_factory = getattr(self._service, "_control_api_idempotency_store", None)
        if callable(store_factory):
            return cast(ControlApiIdempotencyStore, store_factory())
        return self._fallback_idempotency_store

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
        after_seq = max(self._query_int(params, "after", 0), self._query_int(params, "resume", 0))
        timeout = self._query_float(params, "timeout", 5.0)
        heartbeat_interval = self._query_float(params, "heartbeat", 1.0)
        event_kinds = self._query_event_kinds(params)
        retry_ms = self._recommended_retry_ms(heartbeat_interval)
        once = self._query_bool(params, "once", False)
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "application/x-ndjson")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header(self._RETRY_HEADER, str(retry_ms))
        handler.end_headers()
        last_seq = self._write_initial_event_snapshot(handler, after_seq, event_kinds, retry_ms)
        last_seq = self._write_recent_events(handler, event_bus, limit=limit, after_seq=last_seq, event_kinds=event_kinds)
        if not once:
            self._write_live_events(
                handler,
                event_bus,
                after_seq=last_seq,
                timeout=timeout,
                heartbeat_interval=heartbeat_interval,
                event_kinds=event_kinds,
                retry_ms=retry_ms,
            )

    def _write_initial_event_snapshot(
        self,
        handler: BaseHTTPRequestHandler,
        after_seq: int,
        event_kinds: frozenset[str],
        retry_ms: int,
    ) -> int:
        if after_seq > 0:
            return after_seq
        if event_kinds and "snapshot" not in event_kinds:
            return after_seq
        self._write_event_line(
            handler,
            normalized_control_api_event_fields(
                {
                    "seq": 0,
                    "api_version": "v1",
                    "kind": "snapshot",
                    "timestamp": time.time(),
                    "payload": {
                        **self._service._state_api_event_snapshot_payload(),
                        "state_token": self._state_token(),
                        "retry_hint_ms": retry_ms,
                    },
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
        event_kinds: frozenset[str],
    ) -> int:
        last_seq = after_seq
        for event in event_bus.recent(limit=limit, after_seq=after_seq):
            if not self._event_matches_kinds(event, event_kinds):
                last_seq = max(last_seq, int(event["seq"]))
                continue
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
        heartbeat_interval: float,
        event_kinds: frozenset[str],
        retry_ms: int,
    ) -> None:
        deadline = time.time() + max(0.0, timeout)
        last_seq = after_seq
        while time.time() < deadline:
            remaining = max(0.0, deadline - time.time())
            event, last_seq = self._wait_for_matching_event(
                event_bus,
                after_seq=last_seq,
                timeout=self._event_wait_timeout(remaining, heartbeat_interval),
                event_kinds=event_kinds,
            )
            if event is None:
                if self._should_end_live_stream(remaining, heartbeat_interval):
                    return
                self._write_event_line(handler, self._heartbeat_event(last_seq, retry_ms))
                continue
            self._write_event_line(handler, event)
            last_seq = max(last_seq, int(event["seq"]))

    def _wait_for_matching_event(
        self,
        event_bus: Any,
        *,
        after_seq: int,
        timeout: float,
        event_kinds: frozenset[str],
    ) -> tuple[dict[str, Any] | None, int]:
        deadline = time.time() + max(0.0, timeout)
        current_after_seq = after_seq
        while True:
            remaining = max(0.0, deadline - time.time())
            event = event_bus.wait_for_next(after_seq=current_after_seq, timeout=remaining)
            if event is None:
                return None, current_after_seq
            current_after_seq = max(current_after_seq, int(event["seq"]))
            if self._event_matches_kinds(event, event_kinds):
                return event, current_after_seq

    @staticmethod
    def _event_wait_timeout(remaining: float, heartbeat_interval: float) -> float:
        if heartbeat_interval <= 0.0:
            return remaining
        return min(remaining, heartbeat_interval)

    @staticmethod
    def _should_end_live_stream(remaining: float, heartbeat_interval: float) -> bool:
        return heartbeat_interval <= 0.0 or remaining <= 0.0

    @staticmethod
    def _heartbeat_event(after_seq: int, retry_ms: int) -> dict[str, Any]:
        return normalized_control_api_event_fields(
            {
                "seq": after_seq,
                "api_version": "v1",
                "kind": "heartbeat",
                "timestamp": time.time(),
                "resume_token": str(after_seq),
                "payload": {
                    "alive": True,
                    "retry_hint_ms": retry_ms,
                    "resume_hint": str(after_seq),
                },
            }
        )

    @staticmethod
    def _query_event_kinds(params: dict[str, list[str]]) -> frozenset[str]:
        kinds: set[str] = set()
        for raw_value in params.get("kind", []):
            for item in raw_value.split(","):
                normalized = item.strip().lower()
                if normalized in CONTROL_API_EVENT_KINDS:
                    kinds.add(normalized)
        return frozenset(kinds)

    @staticmethod
    def _event_matches_kinds(event: Mapping[str, Any], event_kinds: frozenset[str]) -> bool:
        if not event_kinds:
            return True
        return str(event.get("kind", "")).strip().lower() in event_kinds

    @staticmethod
    def _recommended_retry_ms(heartbeat_interval: float) -> int:
        interval = heartbeat_interval if heartbeat_interval > 0.0 else 1.0
        return max(250, int(interval * 1000))

    def _optimistic_concurrency_error(
        self,
        handler: BaseHTTPRequestHandler,
    ) -> tuple[HTTPStatus, dict[str, Any], dict[str, str]] | None:
        expected_tokens = self._request_state_tokens(handler)
        if not expected_tokens or "*" in expected_tokens:
            return None
        current_token = self._state_token()
        if current_token in expected_tokens:
            return None
        message = "If-Match state token does not match the current local service state."
        payload = normalized_control_api_command_response_fields(
            {
                "ok": False,
                "detail": message,
                "error": {
                    "code": "conflict",
                    "message": message,
                    "retryable": True,
                    "details": {
                        "expected": sorted(expected_tokens),
                        "current": current_token,
                    },
                },
            }
        )
        return HTTPStatus.CONFLICT, payload, self._state_token_headers()

    def _request_state_tokens(self, handler: BaseHTTPRequestHandler) -> set[str]:
        tokens: set[str] = set()
        header_values = [
            handler.headers.get("If-Match", "").strip(),
            handler.headers.get(self._STATE_TOKEN_HEADER, "").strip(),
        ]
        for raw_value in header_values:
            if not raw_value:
                continue
            for item in raw_value.split(","):
                normalized = self._normalized_token(item)
                if normalized:
                    tokens.add(normalized)
        return tokens

    @staticmethod
    def _normalized_token(raw_value: str) -> str:
        token = raw_value.strip()
        if token.startswith("W/"):
            token = token[2:].strip()
        if len(token) >= 2 and token[0] == token[-1] == '"':
            token = token[1:-1].strip()
        return token

    def _state_token(self) -> str:
        state_token_factory = getattr(self._service, "_control_api_state_token", None)
        if callable(state_token_factory):
            return str(state_token_factory()).strip()
        return ""

    def _state_token_headers(self) -> dict[str, str]:
        token = self._state_token()
        if not token:
            return {}
        return {"ETag": f'"{token}"', self._STATE_TOKEN_HEADER: token}

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
        normalized_event = normalized_control_api_event_fields(event)
        handler.wfile.write((json.dumps(normalized_event, sort_keys=True) + "\n").encode("utf-8"))
        handler.wfile.flush()

    def _command_response_payload(self, command: ControlCommand, result: ControlResult, *, replayed: bool) -> dict[str, Any]:
        error_payload = None
        if not result.accepted:
            error_payload = normalized_control_api_error_fields(
                {
                    "code": self._result_error_code(result),
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
    def _result_error_code(result: ControlResult) -> str:
        detail = str(result.detail).strip().lower()
        semantic_checks = (
            (LocalControlApiHttpServer._is_topology_error, "unsupported_for_topology"),
            (LocalControlApiHttpServer._is_update_progress_error, "update_in_progress"),
            (LocalControlApiHttpServer._is_health_error, "blocked_by_health"),
            (LocalControlApiHttpServer._is_mode_block_error, "blocked_by_mode"),
        )
        for predicate, error_code in semantic_checks:
            if predicate(result, detail):
                return error_code
        return "command_rejected" if result.status == "rejected" else "conflict"

    @staticmethod
    def _is_topology_error(result: ControlResult, detail: str) -> bool:
        return result.command.name == "set_phase_selection" and "unsupported" in detail

    @staticmethod
    def _is_update_progress_error(_result: ControlResult, detail: str) -> bool:
        return "update" in detail and any(token in detail for token in ("progress", "running", "busy", "already"))

    @staticmethod
    def _is_health_error(_result: ControlResult, detail: str) -> bool:
        return any(token in detail for token in ("health", "fault", "lockout", "recovery"))

    @staticmethod
    def _is_mode_block_error(_result: ControlResult, detail: str) -> bool:
        return "mode" in detail and any(token in detail for token in ("blocked", "cannot", "while", "unsupported"))

    @staticmethod
    def _error_response_payload(code: str, message: str) -> dict[str, Any]:
        return normalized_control_api_command_response_fields(
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

    def _record_command_audit(
        self,
        *,
        command: Any,
        result: Any,
        error: dict[str, Any] | None,
        replayed: bool,
        scope: str,
        client_host: str,
        status_code: int,
    ) -> None:
        record_audit = getattr(self._service, "_record_control_api_command_audit", None)
        if not callable(record_audit):
            return
        record_audit(
            command=command,
            result=result,
            error=error,
            replayed=replayed,
            scope=scope,
            client_host=client_host,
            status_code=status_code,
            transport="http",
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
        payload = LocalControlApiHttpServer._error_response_payload(code, message)
        LocalControlApiHttpServer._write_json(handler, status, payload)

    @staticmethod
    def _write_json(
        handler: BaseHTTPRequestHandler,
        status: HTTPStatus,
        payload: Mapping[str, Any],
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        raw = json.dumps(dict(payload), sort_keys=True).encode("utf-8")
        handler.send_response(int(status))
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        for key, value in dict(extra_headers or {}).items():
            handler.send_header(str(key), str(value))
        handler.end_headers()
        handler.wfile.write(raw)
