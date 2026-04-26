# SPDX-License-Identifier: GPL-3.0-or-later
"""Small schema-building helpers for the local OpenAPI document."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


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


def _number_schema(
    *,
    minimum: float | None = None,
    exclusive_minimum: float | None = None,
    maximum: float | None = None,
) -> dict[str, Any]:
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


def _const_schema(value: str) -> dict[str, Any]:
    return {"const": value, "type": "string"}


def _boolean_or_binary_integer_schema() -> dict[str, Any]:
    return {"oneOf": [{"type": "boolean"}, {"type": "integer", "enum": [0, 1]}]}
