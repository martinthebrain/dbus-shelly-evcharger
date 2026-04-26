#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small Shelly RPC emulator for local network smoke tests."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


@dataclass
class MockShellyState:
    relay_on: bool = False
    apower: float = 0.0
    current: float = 0.0
    voltage: float = 230.0
    total_energy_wh: float = 0.0
    name: str = "Mock Shelly Relay"
    mac: str = "AABBCCDDEEFF"
    fw_id: str = "mock-fw-1.0.0"
    model: str = "Shelly 1PM Gen4"
    pm_component: str = "Switch"
    pm_id: int = 0
    fault_mode: str = "none"
    fault_seconds: float = 0.0

    def device_info_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "mac": self.mac,
            "fw_id": self.fw_id,
            "model": self.model,
        }

    def switch_status_payload(self) -> dict[str, object]:
        return {
            "id": int(self.pm_id),
            "source": "mock-shelly-rpc",
            "output": bool(self.relay_on),
            "apower": float(self.apower),
            "current": float(self.current),
            "voltage": float(self.voltage),
            "aenergy": {"total": float(self.total_energy_wh)},
        }


def _first(values: dict[str, list[str]], key: str, default: str = "") -> str:
    return values.get(key, [default])[0]


def _parse_bool(value: str, default: bool = False) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_float(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _parse_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


class MockShellyRpcHandler(BaseHTTPRequestHandler):
    server: "MockShellyRpcServer"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query, keep_blank_values=True)
        state = self.server.state
        if parsed.path == "/__admin/reset":
            self._handle_admin_reset()
            return
        if parsed.path == "/__admin/status":
            self._send_json(200, {"state": asdict(state)})
            return
        if parsed.path == "/__admin/fault":
            self._handle_admin_fault(params)
            return
        if parsed.path == "/__admin/state":
            self._handle_admin_state(params)
            return
        if self._handle_fault_mode(state):
            return
        if parsed.path == "/rpc/Shelly.GetDeviceInfo":
            self._send_json(200, state.device_info_payload())
            return
        if parsed.path == f"/rpc/{state.pm_component}.GetStatus":
            self._handle_switch_get_status(params)
            return
        if parsed.path == "/rpc/Switch.Set":
            self._handle_switch_set(params)
            return
        self._send_json(404, {"error": "not-found", "path": parsed.path})

    def log_message(self, fmt: str, *args: object) -> None:
        self.server.log.append(fmt % args)

    def _handle_fault_mode(self, state: MockShellyState) -> bool:
        if state.fault_mode == "timeout":
            time.sleep(max(0.0, float(state.fault_seconds)))
            return False
        if state.fault_mode == "http500":
            self._send_json(500, {"error": "mock-http500"})
            return True
        if state.fault_mode == "badjson":
            payload = b"{bad-json"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return True
        return False

    def _handle_switch_get_status(self, params: dict[str, list[str]]) -> None:
        state = self.server.state
        requested_id = _parse_int(_first(params, "id", str(state.pm_id)), state.pm_id)
        if requested_id != state.pm_id:
            self._send_json(404, {"error": "unknown-switch-id", "id": requested_id})
            return
        self._send_json(200, state.switch_status_payload())

    def _handle_switch_set(self, params: dict[str, list[str]]) -> None:
        state = self.server.state
        requested_id = _parse_int(_first(params, "id", str(state.pm_id)), state.pm_id)
        if requested_id != state.pm_id:
            self._send_json(404, {"error": "unknown-switch-id", "id": requested_id})
            return
        requested_on = _parse_bool(_first(params, "on", "false"))
        state.relay_on = requested_on
        if not requested_on:
            state.apower = 0.0
            state.current = 0.0
        self._send_json(200, {"was_on": bool(state.relay_on), "output": bool(state.relay_on)})

    def _handle_admin_state(self, params: dict[str, list[str]]) -> None:
        state = self.server.state
        if "relay" in params:
            state.relay_on = _parse_bool(_first(params, "relay"), state.relay_on)
        if "apower" in params:
            state.apower = _parse_float(_first(params, "apower"), state.apower)
        if "current" in params:
            state.current = _parse_float(_first(params, "current"), state.current)
        if "voltage" in params:
            state.voltage = _parse_float(_first(params, "voltage"), state.voltage)
        if "total_energy_wh" in params:
            state.total_energy_wh = _parse_float(_first(params, "total_energy_wh"), state.total_energy_wh)
        if "name" in params:
            state.name = _first(params, "name", state.name)
        if "model" in params:
            state.model = _first(params, "model", state.model)
        self._send_json(200, {"state": asdict(state)})

    def _handle_admin_fault(self, params: dict[str, list[str]]) -> None:
        state = self.server.state
        state.fault_mode = _first(params, "mode", "none").strip().lower() or "none"
        state.fault_seconds = _parse_float(_first(params, "seconds", "0"), 0.0)
        self._send_json(200, {"fault_mode": state.fault_mode, "fault_seconds": state.fault_seconds})

    def _handle_admin_reset(self) -> None:
        self.server.state = self.server.initial_state()
        self._send_json(200, {"state": asdict(self.server.state)})

    def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MockShellyRpcServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], state: MockShellyState) -> None:
        super().__init__(server_address, MockShellyRpcHandler)
        self._initial_state = state
        self.state = self.initial_state()
        self.log: list[str] = []

    def initial_state(self) -> MockShellyState:
        return MockShellyState(**asdict(self._initial_state))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Small Shelly RPC emulator for Venus EV charger smoke tests.")
    parser.add_argument("--bind", default="127.0.0.1", help="Bind host for the emulator server.")
    parser.add_argument("--port", type=int, default=8080, help="TCP port to listen on.")
    parser.add_argument("--name", default="Mock Shelly Relay", help="Device name returned by Shelly.GetDeviceInfo.")
    parser.add_argument("--mac", default="AABBCCDDEEFF", help="MAC returned by Shelly.GetDeviceInfo.")
    parser.add_argument("--fw-id", default="mock-fw-1.0.0", help="Firmware id returned by Shelly.GetDeviceInfo.")
    parser.add_argument("--model", default="Shelly 1PM Gen4", help="Model returned by Shelly.GetDeviceInfo.")
    parser.add_argument("--component", default="Switch", help="RPC component used for GetStatus.")
    parser.add_argument("--device-id", type=int, default=0, help="Component device id accepted by the emulator.")
    parser.add_argument("--relay-on", action="store_true", help="Start with relay output enabled.")
    parser.add_argument("--apower", type=float, default=0.0, help="Initial active power in watts.")
    parser.add_argument("--current", type=float, default=0.0, help="Initial current in amps.")
    parser.add_argument("--voltage", type=float, default=230.0, help="Initial voltage in volts.")
    parser.add_argument("--total-energy-wh", type=float, default=0.0, help="Initial total energy counter in Wh.")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    state = MockShellyState(
        relay_on=bool(args.relay_on),
        apower=float(args.apower),
        current=float(args.current),
        voltage=float(args.voltage),
        total_energy_wh=float(args.total_energy_wh),
        name=str(args.name),
        mac=str(args.mac),
        fw_id=str(args.fw_id),
        model=str(args.model),
        pm_component=str(args.component),
        pm_id=int(args.device_id),
    )
    server = MockShellyRpcServer((str(args.bind), int(args.port)), state)
    print(
        f"Mock Shelly RPC listening on http://{args.bind}:{args.port} "
        f"(Host value for config: {args.bind}:{args.port})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping mock Shelly RPC server", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
