# SPDX-License-Identifier: GPL-3.0-or-later
"""OpenAPI 3.1 description for the local Control and State API."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from venus_evcharger.core.contracts import (
    CONTROL_API_ENDPOINTS,
    CONTROL_API_ERROR_CODES,
    CONTROL_API_EXPERIMENTAL_ENDPOINTS,
    CONTROL_API_STATE_ENDPOINTS,
    CONTROL_API_STABLE_ENDPOINTS,
    CONTROL_API_TRANSPORTS,
    CONTROL_API_VERSIONS,
    CONTROL_COMMAND_NAMES,
    CONTROL_COMMAND_SOURCES,
    CONTROL_COMMAND_STATUSES,
    STATE_API_KINDS,
)

def _string_schema(*, enum: Iterable[str] | None = None, default: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string"}
    if enum is not None:
        schema["enum"] = sorted(str(item) for item in enum)
    if default is not None:
        schema["default"] = default
    return schema


def _boolean_schema(*, default: bool | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "boolean"}
    if default is not None:
        schema["default"] = default
    return schema


def _integer_schema(*, minimum: int | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer"}
    if minimum is not None:
        schema["minimum"] = minimum
    return schema


def _number_schema(*, minimum: float | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "number"}
    if minimum is not None:
        schema["minimum"] = minimum
    return schema


def _array_schema(items: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": dict(items)}


def _object_schema(
    properties: Mapping[str, Any],
    *,
    required: Iterable[str] = (),
    additional_properties: bool | Mapping[str, Any] = False,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": dict(properties),
        "additionalProperties": additional_properties,
    }
    required_fields = list(required)
    if required_fields:
        schema["required"] = required_fields
    return schema


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


def _json_response(description: str, schema_name: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": _ref(schema_name),
            }
        },
    }


def _error_responses() -> dict[str, Any]:
    return {
        "400": _json_response("Malformed or invalid request payload.", "ControlCommandResponse"),
        "401": _json_response("Missing or invalid bearer token.", "ControlCommandResponse"),
        "403": _json_response("Request is authenticated incorrectly or not allowed from this client.", "ControlCommandResponse"),
        "404": _json_response("Unknown endpoint.", "ControlCommandResponse"),
        "409": _json_response("Command rejected after validation or idempotency conflict.", "ControlCommandResponse"),
    }


def _state_schema_name(path: str) -> str:
    return {
        "/v1/state/config-effective": "StateConfigEffective",
        "/v1/state/dbus-diagnostics": "StateDbusDiagnostics",
        "/v1/state/health": "StateHealth",
        "/v1/state/operational": "StateOperational",
        "/v1/state/runtime": "StateRuntime",
        "/v1/state/summary": "StateSummary",
        "/v1/state/topology": "StateTopology",
        "/v1/state/update": "StateUpdate",
    }[path]


def _state_paths() -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for path in sorted(CONTROL_API_STATE_ENDPOINTS):
        paths[path] = {
            "get": {
                "summary": f"Read {path.split('/')[-1]} state",
                "responses": {
                    "200": _json_response("Normalized state payload.", _state_schema_name(path)),
                    "401": _json_response("Missing or invalid bearer token.", "ControlCommandResponse"),
                    "403": _json_response("Client not permitted for this endpoint.", "ControlCommandResponse"),
                    "404": _json_response("Unknown endpoint.", "ControlCommandResponse"),
                },
                "security": [{"BearerAuth": []}],
            }
        }
    return paths


def _component_schemas() -> dict[str, Any]:
    generic_state = {"type": "object", "additionalProperties": True}
    return {
        "ControlHealth": _object_schema(
            {
                "ok": _boolean_schema(default=True),
                "api_version": _string_schema(enum=CONTROL_API_VERSIONS, default="v1"),
                "transport": _string_schema(enum=CONTROL_API_TRANSPORTS, default="http"),
                "listen_host": _string_schema(default="127.0.0.1"),
                "listen_port": _integer_schema(minimum=0),
                "auth_required": _boolean_schema(default=False),
                "read_auth_required": _boolean_schema(default=False),
                "control_auth_required": _boolean_schema(default=False),
                "localhost_only": _boolean_schema(default=True),
                "unix_socket_path": _string_schema(default=""),
            },
            required=(
                "ok",
                "api_version",
                "transport",
                "listen_host",
                "listen_port",
                "auth_required",
                "read_auth_required",
                "control_auth_required",
                "localhost_only",
                "unix_socket_path",
            ),
        ),
        "ControlError": _object_schema(
            {
                "code": _string_schema(enum=CONTROL_API_ERROR_CODES, default="bad_request"),
                "message": _string_schema(),
                "retryable": _boolean_schema(default=False),
                "details": {"type": "object", "additionalProperties": True},
            },
            required=("code", "message", "retryable", "details"),
            additional_properties=False,
        ),
        "ControlCommand": _object_schema(
            {
                "name": _string_schema(enum=CONTROL_COMMAND_NAMES, default="legacy_unknown_write"),
                "path": _string_schema(),
                "value": {},
                "source": _string_schema(enum=CONTROL_COMMAND_SOURCES, default="http"),
                "detail": _string_schema(),
                "command_id": _string_schema(),
                "idempotency_key": _string_schema(),
            },
            required=("name", "path", "value", "source", "detail", "command_id", "idempotency_key"),
        ),
        "ControlResult": _object_schema(
            {
                "command": _ref("ControlCommand"),
                "status": _string_schema(enum=CONTROL_COMMAND_STATUSES, default="rejected"),
                "accepted": _boolean_schema(default=False),
                "applied": _boolean_schema(default=False),
                "persisted": _boolean_schema(default=False),
                "reversible_failure": _boolean_schema(default=True),
                "external_side_effect_started": _boolean_schema(default=False),
                "detail": _string_schema(),
            },
            required=(
                "command",
                "status",
                "accepted",
                "applied",
                "persisted",
                "reversible_failure",
                "external_side_effect_started",
                "detail",
            ),
        ),
        "ControlCommandRequest": _object_schema(
            {
                "name": _string_schema(enum=CONTROL_COMMAND_NAMES, default="legacy_unknown_write"),
                "path": _string_schema(),
                "value": {},
                "detail": _string_schema(),
                "command_id": _string_schema(),
                "idempotency_key": _string_schema(),
            },
            additional_properties=False,
        ),
        "ControlCommandResponse": _object_schema(
            {
                "ok": _boolean_schema(default=False),
                "detail": _string_schema(),
                "replayed": _boolean_schema(default=False),
                "command": {"oneOf": [_ref("ControlCommand"), {"type": "null"}]},
                "result": {"oneOf": [_ref("ControlResult"), {"type": "null"}]},
                "error": {"oneOf": [_ref("ControlError"), {"type": "null"}]},
            },
            required=("ok", "detail", "replayed", "command", "result", "error"),
        ),
        "ControlVersioning": _object_schema(
            {
                "stable_endpoints": _array_schema(_string_schema(enum=CONTROL_API_STABLE_ENDPOINTS)),
                "experimental_endpoints": _array_schema(_string_schema(enum=CONTROL_API_EXPERIMENTAL_ENDPOINTS)),
                "breaking_change_policy": _string_schema(),
            },
            required=("stable_endpoints", "experimental_endpoints", "breaking_change_policy"),
        ),
        "ControlCapabilities": _object_schema(
            {
                "ok": _boolean_schema(default=True),
                "api_version": _string_schema(enum=CONTROL_API_VERSIONS, default="v1"),
                "transport": _string_schema(enum=CONTROL_API_TRANSPORTS, default="http"),
                "auth_required": _boolean_schema(default=False),
                "read_auth_required": _boolean_schema(default=False),
                "control_auth_required": _boolean_schema(default=False),
                "localhost_only": _boolean_schema(default=True),
                "unix_socket_path": _string_schema(default=""),
                "auth_header": _string_schema(default="Authorization: Bearer <token>"),
                "command_names": _array_schema(_string_schema(enum=CONTROL_COMMAND_NAMES)),
                "command_sources": _array_schema(_string_schema(enum=CONTROL_COMMAND_SOURCES)),
                "state_endpoints": _array_schema(_string_schema(enum=CONTROL_API_STATE_ENDPOINTS)),
                "endpoints": _array_schema(_string_schema(enum=CONTROL_API_ENDPOINTS)),
                "available_modes": _array_schema(_integer_schema(minimum=0)),
                "supported_phase_selections": _array_schema(_string_schema()),
                "features": {"type": "object", "additionalProperties": {"type": "boolean"}},
                "topology": {"type": "object", "additionalProperties": {"type": "string"}},
                "versioning": _ref("ControlVersioning"),
            },
            required=(
                "ok",
                "api_version",
                "transport",
                "auth_required",
                "read_auth_required",
                "control_auth_required",
                "localhost_only",
                "unix_socket_path",
                "auth_header",
                "command_names",
                "command_sources",
                "state_endpoints",
                "endpoints",
                "available_modes",
                "supported_phase_selections",
                "features",
                "topology",
                "versioning",
            ),
        ),
        "ControlEvent": _object_schema(
            {
                "seq": _integer_schema(minimum=0),
                "api_version": _string_schema(enum=CONTROL_API_VERSIONS, default="v1"),
                "kind": _string_schema(enum=("snapshot", "command", "state"), default="state"),
                "timestamp": _number_schema(minimum=0.0),
                "payload": generic_state,
            },
            required=("seq", "api_version", "kind", "timestamp", "payload"),
        ),
        "StateSummary": _object_schema(
            {
                "ok": _boolean_schema(default=True),
                "api_version": _string_schema(enum=CONTROL_API_VERSIONS, default="v1"),
                "kind": _string_schema(enum=STATE_API_KINDS, default="summary"),
                "summary": _string_schema(),
            },
            required=("ok", "api_version", "kind", "summary"),
        ),
        "StateRuntime": _object_schema(
            {
                "ok": _boolean_schema(default=True),
                "api_version": _string_schema(enum=CONTROL_API_VERSIONS, default="v1"),
                "kind": _string_schema(enum=STATE_API_KINDS, default="runtime"),
                "state": generic_state,
            },
            required=("ok", "api_version", "kind", "state"),
        ),
        "StateOperational": _object_schema(
            {
                "ok": _boolean_schema(default=True),
                "api_version": _string_schema(enum=CONTROL_API_VERSIONS, default="v1"),
                "kind": _string_schema(enum=STATE_API_KINDS, default="operational"),
                "state": generic_state,
            },
            required=("ok", "api_version", "kind", "state"),
        ),
        "StateDbusDiagnostics": _object_schema(
            {
                "ok": _boolean_schema(default=True),
                "api_version": _string_schema(enum=CONTROL_API_VERSIONS, default="v1"),
                "kind": _string_schema(enum=STATE_API_KINDS, default="dbus-diagnostics"),
                "state": generic_state,
            },
            required=("ok", "api_version", "kind", "state"),
        ),
        "StateTopology": _object_schema(
            {
                "ok": _boolean_schema(default=True),
                "api_version": _string_schema(enum=CONTROL_API_VERSIONS, default="v1"),
                "kind": _string_schema(enum=STATE_API_KINDS, default="topology"),
                "state": generic_state,
            },
            required=("ok", "api_version", "kind", "state"),
        ),
        "StateUpdate": _object_schema(
            {
                "ok": _boolean_schema(default=True),
                "api_version": _string_schema(enum=CONTROL_API_VERSIONS, default="v1"),
                "kind": _string_schema(enum=STATE_API_KINDS, default="update"),
                "state": generic_state,
            },
            required=("ok", "api_version", "kind", "state"),
        ),
        "StateConfigEffective": _object_schema(
            {
                "ok": _boolean_schema(default=True),
                "api_version": _string_schema(enum=CONTROL_API_VERSIONS, default="v1"),
                "kind": _string_schema(enum=STATE_API_KINDS, default="config-effective"),
                "state": generic_state,
            },
            required=("ok", "api_version", "kind", "state"),
        ),
        "StateHealth": _object_schema(
            {
                "ok": _boolean_schema(default=True),
                "api_version": _string_schema(enum=CONTROL_API_VERSIONS, default="v1"),
                "kind": _string_schema(enum=STATE_API_KINDS, default="health"),
                "state": generic_state,
            },
            required=("ok", "api_version", "kind", "state"),
        ),
    }


def build_control_api_openapi_spec() -> dict[str, Any]:
    """Return the stable OpenAPI 3.1 description for the local HTTP API."""
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Venus EV Charger Service Local API",
            "version": "v1",
            "description": "Local command, state, and event API for the Venus EV charger service.",
        },
        "servers": [{"url": "http://127.0.0.1:8765"}],
        "paths": {
            "/v1/control/health": {
                "get": {
                    "summary": "Read local API liveness and binding state",
                    "responses": {
                        "200": _json_response("Local API health payload.", "ControlHealth"),
                        "403": _json_response("Client not permitted for this endpoint.", "ControlCommandResponse"),
                        "404": _json_response("Unknown endpoint.", "ControlCommandResponse"),
                    },
                }
            },
            "/v1/openapi.json": {
                "get": {
                    "summary": "Read the OpenAPI 3.1 description for this local API",
                    "responses": {
                        "200": {
                            "description": "OpenAPI description document.",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True},
                                }
                            },
                        },
                        "403": _json_response("Client not permitted for this endpoint.", "ControlCommandResponse"),
                    },
                }
            },
            "/v1/capabilities": {
                "get": {
                    "summary": "Read stable command, topology, auth, and endpoint capabilities",
                    "responses": {
                        "200": _json_response("Capabilities payload.", "ControlCapabilities"),
                        "401": _json_response("Missing or invalid bearer token.", "ControlCommandResponse"),
                        "403": _json_response("Client not permitted for this endpoint.", "ControlCommandResponse"),
                        "404": _json_response("Unknown endpoint.", "ControlCommandResponse"),
                    },
                    "security": [{"BearerAuth": []}],
                }
            },
            "/v1/control/command": {
                "post": {
                    "summary": "Execute one canonical control command",
                    "parameters": [
                        {
                            "name": "Idempotency-Key",
                            "in": "header",
                            "required": False,
                            "schema": _string_schema(),
                            "description": "Optional replay-safe key for command deduplication.",
                        },
                        {
                            "name": "X-Command-Id",
                            "in": "header",
                            "required": False,
                            "schema": _string_schema(),
                            "description": "Optional client-supplied command identifier.",
                        },
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": _ref("ControlCommandRequest"),
                            }
                        },
                    },
                    "responses": {
                        "200": _json_response("Command applied.", "ControlCommandResponse"),
                        "202": _json_response("Command accepted but still in flight.", "ControlCommandResponse"),
                        **_error_responses(),
                    },
                    "security": [{"BearerAuth": []}],
                }
            },
            "/v1/events": {
                "get": {
                    "summary": "Stream local API events as NDJSON",
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "required": False,
                            "schema": _integer_schema(minimum=0),
                            "description": "Number of recent events to include before waiting for newer ones.",
                        },
                        {
                            "name": "after",
                            "in": "query",
                            "required": False,
                            "schema": _integer_schema(minimum=0),
                            "description": "Only emit events with a sequence number greater than this value.",
                        },
                        {
                            "name": "timeout",
                            "in": "query",
                            "required": False,
                            "schema": _number_schema(minimum=0.0),
                            "description": "Maximum number of seconds to keep the stream open while waiting for new events.",
                        },
                        {
                            "name": "once",
                            "in": "query",
                            "required": False,
                            "schema": _boolean_schema(default=False),
                            "description": "When true, return snapshot plus recent events and close immediately.",
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "NDJSON event stream. Each line is one ControlEvent object.",
                            "content": {
                                "application/x-ndjson": {
                                    "schema": _ref("ControlEvent"),
                                }
                            },
                        },
                        "401": _json_response("Missing or invalid bearer token.", "ControlCommandResponse"),
                        "403": _json_response("Client not permitted for this endpoint.", "ControlCommandResponse"),
                    },
                    "security": [{"BearerAuth": []}],
                }
            },
            **_state_paths(),
        },
        "components": {
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "opaque-token",
                }
            },
            "schemas": _component_schemas(),
        },
    }
