# SPDX-License-Identifier: GPL-3.0-or-later
"""OpenAPI 3.1 description for the local Control and State API."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from venus_evcharger.control.service import ControlApiV1Service
from venus_evcharger.core.contracts import (
    CONTROL_API_ENDPOINTS,
    CONTROL_API_ERROR_CODES,
    CONTROL_API_EVENT_KINDS,
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


def _integer_schema(*, minimum: int | None = None, enum: Iterable[int] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer"}
    if minimum is not None:
        schema["minimum"] = minimum
    if enum is not None:
        schema["enum"] = sorted(int(item) for item in enum)
    return schema


def _number_schema(*, minimum: float | None = None, exclusive_minimum: float | None = None, maximum: float | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "number"}
    if minimum is not None:
        schema["minimum"] = minimum
    if exclusive_minimum is not None:
        schema["exclusiveMinimum"] = exclusive_minimum
    if maximum is not None:
        schema["maximum"] = maximum
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
        "content": {"application/json": {"schema": _ref(schema_name)}},
    }


def _etag_headers() -> dict[str, Any]:
    return {
        "ETag": {
            "description": "Current local state token for optimistic concurrency.",
            "schema": _string_schema(),
        },
        "X-State-Token": {
            "description": "Current local state token mirrored as a plain header value.",
            "schema": _string_schema(),
        },
    }


def _error_responses() -> dict[str, Any]:
    return {
        "400": _json_response("Malformed or invalid request payload.", "ControlCommandResponse"),
        "401": _json_response("Missing or invalid bearer token.", "ControlCommandResponse"),
        "403": _json_response("Request is authenticated incorrectly or not allowed from this client.", "ControlCommandResponse"),
        "404": _json_response("Unknown endpoint.", "ControlCommandResponse"),
        "409": _json_response("Command rejected after validation or idempotency conflict.", "ControlCommandResponse"),
        "429": _json_response("The local API temporarily throttled this request.", "ControlCommandResponse"),
    }


def _state_schema_name(path: str) -> str:
    return {
        "/v1/state/build": "StateBuild",
        "/v1/state/config-effective": "StateConfigEffective",
        "/v1/state/contracts": "StateContracts",
        "/v1/state/dbus-diagnostics": "StateDbusDiagnostics",
        "/v1/state/health": "StateHealth",
        "/v1/state/healthz": "StateHealthz",
        "/v1/state/operational": "StateOperational",
        "/v1/state/runtime": "StateRuntime",
        "/v1/state/summary": "StateSummary",
        "/v1/state/topology": "StateTopology",
        "/v1/state/update": "StateUpdate",
        "/v1/state/version": "StateVersion",
        "/v1/state/victron-bias-recommendation": "StateVictronBiasRecommendation",
    }[path]


def _state_paths() -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for path in sorted(CONTROL_API_STATE_ENDPOINTS):
        get_spec: dict[str, Any] = {
            "summary": f"Read {path.split('/')[-1]} state",
            "responses": {
                "200": {
                    **_json_response("Normalized state payload.", _state_schema_name(path)),
                    "headers": _etag_headers(),
                },
                "401": _json_response("Missing or invalid bearer token.", "ControlCommandResponse"),
                "403": _json_response("Client not permitted for this endpoint.", "ControlCommandResponse"),
                "404": _json_response("Unknown endpoint.", "ControlCommandResponse"),
            },
        }
        if path != "/v1/state/healthz":
            get_spec["security"] = [{"BearerAuth": []}]
        paths[path] = {"get": get_spec}
    return paths


def _tracking_properties() -> dict[str, Any]:
    return {
        "detail": _string_schema(),
        "command_id": _string_schema(),
        "idempotency_key": _string_schema(),
    }


def _tracking_headers() -> list[dict[str, Any]]:
    return [
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
    ]


def _const_schema(value: str) -> dict[str, Any]:
    return {"const": value, "type": "string"}


def _boolean_or_binary_integer_schema() -> dict[str, Any]:
    return {"oneOf": [{"type": "boolean"}, {"type": "integer", "enum": [0, 1]}]}


def _named_command_request_schema(name: str, value_schema: Mapping[str, Any], *, path_schema: Mapping[str, Any] | None = None) -> dict[str, Any]:
    properties = {"name": _const_schema(name), "value": dict(value_schema), **_tracking_properties()}
    required = ["name", "value"]
    if path_schema is not None:
        properties["path"] = dict(path_schema)
        required.append("path")
    return _object_schema(properties, required=required, additional_properties=False)


def _path_command_request_schema(path_schema: Mapping[str, Any], value_schema: Mapping[str, Any]) -> dict[str, Any]:
    return _object_schema(
        {"path": dict(path_schema), "value": dict(value_schema), **_tracking_properties()},
        required=("path", "value"),
        additional_properties=False,
    )


def _control_request_schemas() -> dict[str, Any]:
    direct_binary_path_names = {
        "/Auto/ContactorLockoutReset": "ResetContactorLockout",
        "/Auto/PhaseLockoutReset": "ResetPhaseLockout",
        "/Auto/SoftwareUpdateRun": "TriggerSoftwareUpdate",
        "/AutoStart": "SetAutoStart",
        "/Enable": "SetEnable",
        "/StartStop": "SetStartStop",
    }
    binary_schema = _boolean_or_binary_integer_schema()
    request_schemas: dict[str, Any] = {
        "SetModeCommandRequest": _named_command_request_schema(
            "set_mode",
            _integer_schema(enum=(0, 1, 2)),
            path_schema=_const_schema("/Mode"),
        ),
        "SetModePathRequest": _path_command_request_schema(_const_schema("/Mode"), _integer_schema(enum=(0, 1, 2))),
        "SetPhaseSelectionCommandRequest": _named_command_request_schema(
            "set_phase_selection",
            _string_schema(enum=sorted(ControlApiV1Service._KNOWN_PHASE_SELECTIONS)),
            path_schema=_const_schema("/PhaseSelection"),
        ),
        "SetPhaseSelectionPathRequest": _path_command_request_schema(
            _const_schema("/PhaseSelection"),
            _string_schema(enum=sorted(ControlApiV1Service._KNOWN_PHASE_SELECTIONS)),
        ),
        "SetCurrentSettingCommandRequest": _named_command_request_schema(
            "set_current_setting",
            _number_schema(minimum=0.0),
            path_schema={"type": "string", "enum": ["/SetCurrent"]},
        ),
        "SetCurrentSettingPathRequest": _path_command_request_schema(
            {"type": "string", "enum": ["/SetCurrent"]},
            _number_schema(minimum=0.0),
        ),
        "LegacyUnknownWriteCommandRequest": _named_command_request_schema(
            "legacy_unknown_write",
            {},
            path_schema=_string_schema(),
        ),
    }
    for path, schema_name in direct_binary_path_names.items():
        command_name = ControlApiV1Service._DIRECT_PATH_COMMANDS[path]
        request_schemas[f"{schema_name}CommandRequest"] = _named_command_request_schema(
            command_name,
            binary_schema,
            path_schema=_const_schema(path),
        )
        request_schemas[f"{schema_name}PathRequest"] = _path_command_request_schema(_const_schema(path), binary_schema)
    request_schemas["SetAutoRuntimeFloatCommandRequest"] = _named_command_request_schema(
        "set_auto_runtime_setting",
        _number_schema(minimum=0.0),
        path_schema={"type": "string", "enum": sorted(ControlApiV1Service._FLOAT_AUTO_RUNTIME_PATHS)},
    )
    request_schemas["SetAutoRuntimeStringCommandRequest"] = _named_command_request_schema(
        "set_auto_runtime_setting",
        _string_schema(),
        path_schema={"type": "string", "enum": sorted(ControlApiV1Service._STRING_AUTO_RUNTIME_PATHS)},
    )
    request_schemas["SetAutoRuntimeBinaryCommandRequest"] = _named_command_request_schema(
        "set_auto_runtime_setting",
        binary_schema,
        path_schema={"type": "string", "enum": sorted(ControlApiV1Service._BINARY_AUTO_RUNTIME_PATHS)},
    )
    request_schemas["SetAutoRuntimeIntegerCommandRequest"] = _named_command_request_schema(
        "set_auto_runtime_setting",
        _integer_schema(minimum=0),
        path_schema={"type": "string", "enum": sorted(ControlApiV1Service._INTEGER_AUTO_RUNTIME_PATHS)},
    )
    request_schemas["SetAutoRuntimeFloatPathRequest"] = _path_command_request_schema(
        {"type": "string", "enum": sorted(ControlApiV1Service._FLOAT_AUTO_RUNTIME_PATHS)},
        _number_schema(minimum=0.0),
    )
    request_schemas["SetAutoRuntimeStringPathRequest"] = _path_command_request_schema(
        {"type": "string", "enum": sorted(ControlApiV1Service._STRING_AUTO_RUNTIME_PATHS)},
        _string_schema(),
    )
    request_schemas["SetAutoRuntimeBinaryPathRequest"] = _path_command_request_schema(
        {"type": "string", "enum": sorted(ControlApiV1Service._BINARY_AUTO_RUNTIME_PATHS)},
        binary_schema,
    )
    request_schemas["SetAutoRuntimeIntegerPathRequest"] = _path_command_request_schema(
        {"type": "string", "enum": sorted(ControlApiV1Service._INTEGER_AUTO_RUNTIME_PATHS)},
        _integer_schema(minimum=0),
    )
    return request_schemas


def _control_command_request_schema_names() -> list[str]:
    return sorted(_control_request_schemas())


def _component_schemas() -> dict[str, Any]:
    generic_state = {"type": "object", "additionalProperties": True}
    schemas: dict[str, Any] = {
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
        "ControlCommandRequest": {
            "oneOf": [_ref(name) for name in _control_command_request_schema_names()],
        },
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
                "kind": _string_schema(enum=CONTROL_API_EVENT_KINDS, default="state"),
                "timestamp": _number_schema(minimum=0.0),
                "resume_token": _string_schema(default="0"),
                "payload": generic_state,
            },
            required=("seq", "api_version", "kind", "timestamp", "resume_token", "payload"),
        ),
    }
    for schema_name, default_kind in (
        ("StateBuild", "build"),
        ("StateConfigEffective", "config-effective"),
        ("StateContracts", "contracts"),
        ("StateDbusDiagnostics", "dbus-diagnostics"),
        ("StateHealth", "health"),
        ("StateHealthz", "healthz"),
        ("StateOperational", "operational"),
        ("StateRuntime", "runtime"),
        ("StateSummary", "summary"),
        ("StateTopology", "topology"),
        ("StateUpdate", "update"),
        ("StateVersion", "version"),
        ("StateVictronBiasRecommendation", "victron-bias-recommendation"),
    ):
        if default_kind == "summary":
            schemas[schema_name] = _object_schema(
                {
                    "ok": _boolean_schema(default=True),
                    "api_version": _string_schema(enum=CONTROL_API_VERSIONS, default="v1"),
                    "kind": _string_schema(enum=STATE_API_KINDS, default=default_kind),
                    "summary": _string_schema(),
                },
                required=("ok", "api_version", "kind", "summary"),
            )
            continue
        schemas[schema_name] = _object_schema(
            {
                "ok": _boolean_schema(default=True),
                "api_version": _string_schema(enum=CONTROL_API_VERSIONS, default="v1"),
                "kind": _string_schema(enum=STATE_API_KINDS, default=default_kind),
                "state": generic_state,
            },
            required=("ok", "api_version", "kind", "state"),
        )
    schemas.update(_control_request_schemas())
    return schemas


def build_control_api_openapi_spec() -> dict[str, Any]:
    """Return the stable OpenAPI 3.1 description for the local HTTP API."""
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Venus EV Charger Service Local API",
            "version": "v1",
            "description": "Local command, state, and event API for the Venus EV charger service.",
        },
        "servers": [
            {"url": "http://127.0.0.1:8765"},
            {"url": "http+unix://localhost"},
        ],
        "paths": {
            "/v1/control/health": {
                "get": {
                    "summary": "Read local API liveness and binding state",
                    "responses": {
                        "200": {
                            **_json_response("Local API health payload.", "ControlHealth"),
                            "headers": _etag_headers(),
                        },
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
                            "content": {"application/json": {"schema": {"type": "object", "additionalProperties": True}}},
                        },
                        "403": _json_response("Client not permitted for this endpoint.", "ControlCommandResponse"),
                    },
                }
            },
            "/v1/capabilities": {
                "get": {
                    "summary": "Read stable command, topology, auth, and endpoint capabilities",
                    "responses": {
                        "200": {
                            **_json_response("Capabilities payload.", "ControlCapabilities"),
                            "headers": _etag_headers(),
                        },
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
                    "parameters": _tracking_headers()
                    + [
                        {
                            "name": "If-Match",
                            "in": "header",
                            "required": False,
                            "schema": _string_schema(),
                            "description": "Optional optimistic concurrency token from a prior state ETag.",
                        },
                        {
                            "name": "X-State-Token",
                            "in": "header",
                            "required": False,
                            "schema": _string_schema(),
                            "description": "Optional plain state token alternative to If-Match.",
                        },
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": _ref("ControlCommandRequest")}},
                    },
                    "responses": {
                        "200": {
                            **_json_response("Command applied.", "ControlCommandResponse"),
                            "headers": _etag_headers(),
                        },
                        "202": {
                            **_json_response("Command accepted but still in flight.", "ControlCommandResponse"),
                            "headers": _etag_headers(),
                        },
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
                            "name": "resume",
                            "in": "query",
                            "required": False,
                            "schema": _integer_schema(minimum=0),
                            "description": "Resume the stream after a previously seen resume_token.",
                        },
                        {
                            "name": "timeout",
                            "in": "query",
                            "required": False,
                            "schema": _number_schema(minimum=0.0),
                            "description": "Maximum number of seconds to keep the stream open while waiting for new events.",
                        },
                        {
                            "name": "heartbeat",
                            "in": "query",
                            "required": False,
                            "schema": _number_schema(minimum=0.0),
                            "description": "Emit heartbeat events at this cadence while the stream is idle.",
                        },
                        {
                            "name": "kind",
                            "in": "query",
                            "required": False,
                            "schema": _array_schema(_string_schema(enum=CONTROL_API_EVENT_KINDS)),
                            "style": "form",
                            "explode": True,
                            "description": "Optionally filter stored events by kind, for example command or state.",
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
                            "headers": {
                                "X-Control-Api-Retry-Ms": {
                                    "description": "Recommended reconnect delay for stream clients in milliseconds.",
                                    "schema": _integer_schema(minimum=0),
                                }
                            },
                            "content": {"application/x-ndjson": {"schema": _ref("ControlEvent")}},
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
