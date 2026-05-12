#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small Venus OS/Cerbo live-testbed helper.

The default mode is intentionally CI-safe: it returns deterministic DBus-like
service snapshots that model common EV-charger scenarios. On a real Venus OS
device, ``probe-real`` performs read-only DBus checks for Cerbo relay paths.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from typing import Any, Sequence


@dataclass(frozen=True, slots=True)
class SimulatedDbusValue:
    service: str
    path: str
    value: object


SIMULATED_SCENARIOS: dict[str, tuple[SimulatedDbusValue, ...]] = {
    "pv-surplus": (
        SimulatedDbusValue("com.victronenergy.system", "/Dc/Battery/Soc", 74.0),
        SimulatedDbusValue("com.victronenergy.system", "/Ac/Grid/L1/Power", -420.0),
        SimulatedDbusValue("com.victronenergy.pvinverter.http_48", "/Ac/Power", 2680.0),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Mode", 1),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/StartStop", 1),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Auto/DecisionReason", "pv-surplus"),
    ),
    "night-fallback": (
        SimulatedDbusValue("com.victronenergy.system", "/Dc/Battery/Soc", 63.0),
        SimulatedDbusValue("com.victronenergy.system", "/Ac/Grid/L1/Power", 1800.0),
        SimulatedDbusValue("com.victronenergy.pvinverter.http_48", "/Ac/Power", 0.0),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Mode", 2),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Auto/ScheduledState", "night-boost"),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Auto/DecisionReason", "scheduled-night-charge"),
    ),
    "unplug-replug": (
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Mode", 2),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/StartStop", 1),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Ac/Power", 0.0),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Session/Energy", 0.0),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Auto/DecisionReason", "vehicle-not-charging"),
        SimulatedDbusValue("com.victronenergy.evcharger.http_60", "/Auto/DecisionRelayIntent", 1),
    ),
}

CERBO_READ_ONLY_PROBES = (
    ("com.victronenergy.platform", "/Relay/0/State"),
    ("com.victronenergy.platform", "/Relay/1/State"),
    ("com.victronenergy.settings", "/Settings/Relay/0/Function"),
    ("com.victronenergy.settings", "/Settings/Relay/1/Function"),
)


def simulated_payload(scenario: str) -> dict[str, Any]:
    values = SIMULATED_SCENARIOS[scenario]
    return {
        "ok": True,
        "kind": "venus-cerbo-testbed",
        "mode": "simulate",
        "scenario": scenario,
        "services": [asdict(value) for value in values],
        "expectations": scenario_expectations(scenario),
    }


def scenario_expectations(scenario: str) -> dict[str, Any]:
    return {
        "pv-surplus": {
            "mode": 1,
            "should_charge_when_thresholds_allow": True,
            "primary_reason": "pv-surplus",
        },
        "night-fallback": {
            "mode": 2,
            "should_charge_after_day_window": True,
            "primary_reason": "scheduled-night-charge",
        },
        "unplug-replug": {
            "session_energy_should_reset": True,
            "gui_should_remain_writable": True,
            "mode_should_remain": 2,
        },
    }[scenario]


def probe_real_cerbo(timeout: float) -> dict[str, Any]:
    dbus_binary = shutil.which("dbus")
    if not dbus_binary:
        return {
            "ok": False,
            "kind": "venus-cerbo-testbed",
            "mode": "probe-real",
            "skipped": True,
            "reason": "dbus CLI not found; run this on Venus OS or install the dbus tool",
            "probes": [],
        }
    probes = [_read_dbus_value(dbus_binary, service, path, timeout) for service, path in CERBO_READ_ONLY_PROBES]
    return {
        "ok": all(probe["ok"] or probe["skipped"] for probe in probes),
        "kind": "venus-cerbo-testbed",
        "mode": "probe-real",
        "skipped": False,
        "probes": probes,
    }


def _read_dbus_value(dbus_binary: str, service: str, path: str, timeout: float) -> dict[str, Any]:
    command = [dbus_binary, "-y", service, path, "GetValue"]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"service": service, "path": path, "ok": False, "skipped": False, "error": "timeout"}
    output = completed.stdout.strip()
    error = completed.stderr.strip()
    return {
        "service": service,
        "path": path,
        "ok": completed.returncode == 0,
        "skipped": completed.returncode != 0 and "ServiceUnknown" in error,
        "value": output,
        "error": error,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Venus OS/Cerbo EV charger live-testbed helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    simulate = subparsers.add_parser("simulate", help="Print one deterministic DBus-like test scenario.")
    simulate.add_argument("scenario", choices=tuple(sorted(SIMULATED_SCENARIOS)))

    probe = subparsers.add_parser("probe-real", help="Run read-only Cerbo relay DBus probes on Venus OS.")
    probe.add_argument("--timeout", type=float, default=3.0, help="Per-probe timeout in seconds.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    namespace = build_parser().parse_args(list(argv) if argv is not None else None)
    if namespace.command == "simulate":
        payload = simulated_payload(namespace.scenario)
    else:
        payload = probe_real_cerbo(namespace.timeout)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("ok") or payload.get("skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
