#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Entrypoint for the external Venus EV charger forensic observer."""

from __future__ import annotations

import argparse
import os

from venus_evcharger.ops.forensic_observer import observer_loop


def main() -> None:
    parser = argparse.ArgumentParser(description="Observe EV charger health and persist incident bundles to removable storage.")
    parser.add_argument("config_path", help="Path to config.venus_evcharger.ini")
    parser.add_argument("--start-delay", type=float, default=float(os.environ.get("VENUS_EVCHARGER_OBSERVER_START_DELAY", "180")))
    parser.add_argument("--interval", type=float, default=float(os.environ.get("VENUS_EVCHARGER_OBSERVER_INTERVAL", "30")))
    parser.add_argument("--cooldown", type=float, default=float(os.environ.get("VENUS_EVCHARGER_OBSERVER_COOLDOWN", "900")))
    args = parser.parse_args()
    observer_loop(args.config_path, start_delay=args.start_delay, interval=args.interval, incident_cooldown=args.cooldown)


if __name__ == "__main__":
    main()
