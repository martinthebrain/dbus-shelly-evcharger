# SPDX-License-Identifier: GPL-3.0-or-later
"""Small local CLI for the Control and State API."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from venus_evcharger.control.client import LocalControlApiClient


def _parse_cli_value(raw_value: str) -> Any:
    normalized = raw_value.strip()
    lowered = normalized.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    try:
        return json.loads(normalized)
    except json.JSONDecodeError:
        return raw_value


def _normalized_command_name(raw_name: str) -> str:
    return raw_name.strip().replace("-", "_")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Small local client for the Venus EV charger Control API.")
    parser.add_argument("--url", default="http://127.0.0.1:8765", help="Base URL for the local HTTP API.")
    parser.add_argument("--unix-socket", default="", help="Optional unix socket path to use instead of TCP.")
    parser.add_argument("--token", default="", help="Bearer token for read/control access.")
    parser.add_argument("--timeout", type=float, default=5.0, help="Request timeout in seconds.")
    parser.add_argument("--compact", action="store_true", help="Print compact JSON instead of pretty JSON.")

    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    state_parser = subparsers.add_parser("state", help="Read one normalized state payload.")
    state_parser.add_argument(
        "state_name",
        choices=(
            "summary",
            "runtime",
            "operational",
            "dbus-diagnostics",
            "topology",
            "update",
            "health",
            "healthz",
            "version",
            "build",
            "contracts",
            "config-effective",
        ),
    )

    capabilities_parser = subparsers.add_parser("capabilities", help="Read the capabilities payload.")
    capabilities_parser.set_defaults(state_name="")

    subparsers.add_parser("health", help="Read the local API health payload.")
    subparsers.add_parser("openapi", help="Read the OpenAPI 3.1 description.")

    command_parser = subparsers.add_parser("command", help="Send one canonical control command.")
    command_parser.add_argument("name", help="Canonical command name, for example set-mode.")
    command_parser.add_argument("value", help="Command value. JSON scalars such as 1, 12.5, true are accepted.")
    command_parser.add_argument("--path", default="", help="Explicit write path when the command family requires it.")
    command_parser.add_argument("--detail", default="", help="Optional detail string carried with the command.")
    command_parser.add_argument("--command-id", default="", help="Optional client-supplied command id.")
    command_parser.add_argument("--idempotency-key", default="", help="Optional replay-safe idempotency key.")
    command_parser.add_argument("--if-match", default="", help="Optional optimistic concurrency token.")

    events_parser = subparsers.add_parser("events", help="Read one NDJSON event stream snapshot.")
    events_parser.add_argument("--limit", type=int, default=20)
    events_parser.add_argument("--after", type=int, default=None)
    events_parser.add_argument("--resume", type=int, default=None)
    events_parser.add_argument("--timeout", type=float, default=2.0)
    events_parser.add_argument("--heartbeat", type=float, default=0.5)
    events_parser.add_argument("--kind", action="append", default=[], help="Optional event kind filter.")
    events_parser.add_argument("--once", action="store_true")

    return parser


def _client(namespace: argparse.Namespace) -> LocalControlApiClient:
    return LocalControlApiClient(
        base_url=namespace.url,
        unix_socket_path=namespace.unix_socket,
        bearer_token=namespace.token,
        timeout=namespace.timeout,
    )


def _write_json(value: Any, *, compact: bool) -> None:
    if compact:
        sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
        return
    sys.stdout.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _run_state(namespace: argparse.Namespace) -> int:
    response = _client(namespace).state(namespace.state_name)
    _write_json(response.json(), compact=namespace.compact)
    return 0 if 200 <= response.status < 300 else 1


def _run_capabilities(namespace: argparse.Namespace) -> int:
    response = _client(namespace).capabilities()
    _write_json(response.json(), compact=namespace.compact)
    return 0 if 200 <= response.status < 300 else 1


def _run_health(namespace: argparse.Namespace) -> int:
    response = _client(namespace).health()
    _write_json(response.json(), compact=namespace.compact)
    return 0 if 200 <= response.status < 300 else 1


def _run_openapi(namespace: argparse.Namespace) -> int:
    response = _client(namespace).openapi()
    _write_json(response.json(), compact=namespace.compact)
    return 0 if 200 <= response.status < 300 else 1


def _command_payload(namespace: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": _normalized_command_name(namespace.name),
        "value": _parse_cli_value(namespace.value),
    }
    if namespace.path:
        payload["path"] = namespace.path
    if namespace.detail:
        payload["detail"] = namespace.detail
    return payload


def _run_command(namespace: argparse.Namespace) -> int:
    response = _client(namespace).command(
        _command_payload(namespace),
        idempotency_key=namespace.idempotency_key,
        command_id=namespace.command_id,
        if_match=namespace.if_match,
    )
    _write_json(response.json(), compact=namespace.compact)
    return 0 if 200 <= response.status < 300 else 1


def _event_request_kwargs(namespace: argparse.Namespace) -> dict[str, Any]:
    return {
        "limit": namespace.limit,
        "after": namespace.after,
        "resume": namespace.resume,
        "timeout": namespace.timeout,
        "heartbeat": namespace.heartbeat,
        "kinds": tuple(namespace.kind),
        "once": namespace.once,
    }


def _run_events(namespace: argparse.Namespace) -> int:
    response = _client(namespace).events(**_event_request_kwargs(namespace))
    return _write_event_response(response, compact=namespace.compact)


def _write_event_response(response: Any, *, compact: bool) -> int:
    if compact:
        _write_stream_body(response.body)
    else:
        _write_json(response.ndjson(), compact=False)
    return 0 if 200 <= response.status < 300 else 1


def _write_stream_body(body: str) -> None:
    sys.stdout.write(body)
    if body and not body.endswith("\n"):
        sys.stdout.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    namespace = parser.parse_args(list(argv) if argv is not None else None)
    handlers = {
        "state": _run_state,
        "capabilities": _run_capabilities,
        "health": _run_health,
        "openapi": _run_openapi,
        "command": _run_command,
        "events": _run_events,
    }
    handler = handlers.get(namespace.subcommand)
    if handler is None:
        raise SystemExit(2)
    return handler(namespace)


if __name__ == "__main__":
    raise SystemExit(main())
