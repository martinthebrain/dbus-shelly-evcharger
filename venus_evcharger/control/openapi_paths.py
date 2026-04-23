# SPDX-License-Identifier: GPL-3.0-or-later
"""Path definitions for the local OpenAPI document."""

from __future__ import annotations

from typing import Any

from venus_evcharger.core.contracts import CONTROL_API_EVENT_KINDS, CONTROL_API_STATE_ENDPOINTS

from .openapi_helpers import (
    _array_schema,
    _boolean_schema,
    _etag_headers,
    _integer_schema,
    _json_response,
    _number_schema,
    _ref,
    _string_schema,
)
from .openapi_schemas import _tracking_headers


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


def _error_responses() -> dict[str, Any]:
    return {
        "400": _json_response("Malformed or invalid request payload.", "ControlCommandResponse"),
        "401": _json_response("Missing or invalid bearer token.", "ControlCommandResponse"),
        "403": _json_response("Request is authenticated incorrectly or not allowed from this client.", "ControlCommandResponse"),
        "404": _json_response("Unknown endpoint.", "ControlCommandResponse"),
        "409": _json_response("Command rejected after validation or idempotency conflict.", "ControlCommandResponse"),
        "429": _json_response("The local API temporarily throttled this request.", "ControlCommandResponse"),
    }


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


def _paths_spec() -> dict[str, Any]:
    return {
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
                    "200": {**_json_response("Command applied.", "ControlCommandResponse"), "headers": _etag_headers()},
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
    }
