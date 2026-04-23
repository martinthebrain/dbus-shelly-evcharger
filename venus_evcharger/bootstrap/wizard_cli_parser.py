# SPDX-License-Identifier: GPL-3.0-or-later
"""Argument parser builder for the setup wizard CLI."""

from __future__ import annotations

import argparse

from venus_evcharger.bootstrap.wizard_charger_presets import CHARGER_PRESET_VALUES
from venus_evcharger.bootstrap.wizard_models import WizardTransportKind
from venus_evcharger.bootstrap.wizard_support import (
    NATIVE_CHARGER_VALUES,
    POLICY_VALUES,
    PROFILE_VALUES,
    SPLIT_PRESET_VALUES,
    TRANSPORT_VALUES,
)
from venus_evcharger.bootstrap.wizard_transport_guidance import SWITCH_GROUP_PHASE_LAYOUT_VALUES


def build_parser(default_config_path: str, default_template_path: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Optional setup wizard for the Venus EV charger config.")
    parser.add_argument("--config-path", default=default_config_path)
    parser.add_argument("--template-path", default=default_template_path)
    parser.add_argument("--profile", choices=PROFILE_VALUES)
    parser.add_argument("--split-preset", choices=SPLIT_PRESET_VALUES)
    parser.add_argument("--charger-backend", choices=NATIVE_CHARGER_VALUES)
    parser.add_argument("--charger-preset", choices=CHARGER_PRESET_VALUES)
    parser.add_argument("--host")
    parser.add_argument("--meter-host")
    parser.add_argument("--switch-host")
    parser.add_argument("--charger-host")
    parser.add_argument("--device-instance", type=int)
    parser.add_argument("--phase", choices=("L1", "L2", "L3", "3P", "1P"))
    parser.add_argument("--policy-mode", choices=POLICY_VALUES)
    parser.add_argument("--transport", choices=TRANSPORT_VALUES)
    parser.add_argument("--transport-host")
    parser.add_argument("--transport-port", type=int)
    parser.add_argument("--transport-device")
    parser.add_argument("--transport-unit-id", type=int)
    parser.add_argument("--digest-auth", action="store_true")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--import-config")
    parser.add_argument("--resume-last", action="store_true")
    parser.add_argument("--clone-current", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--live-check", action="store_true")
    parser.add_argument("--probe-role", dest="probe_roles", action="append", choices=("meter", "switch", "charger"))
    parser.add_argument("--request-timeout-seconds", type=float)
    parser.add_argument("--switch-group-phase-layout", choices=SWITCH_GROUP_PHASE_LAYOUT_VALUES)
    parser.add_argument("--auto-start-surplus-watts", type=float)
    parser.add_argument("--auto-stop-surplus-watts", type=float)
    parser.add_argument("--auto-min-soc", type=float)
    parser.add_argument("--auto-resume-soc", type=float)
    parser.add_argument("--scheduled-enabled-days")
    parser.add_argument("--scheduled-latest-end-time")
    parser.add_argument("--scheduled-night-current-amps", type=float)
    parser.add_argument("--energy-recommendation-prefix", action="append")
    parser.add_argument("--huawei-recommendation-prefix", action="append")
    parser.add_argument("--energy-default-usable-capacity-wh", type=float)
    parser.add_argument("--huawei-usable-capacity-wh", type=float)
    parser.add_argument("--energy-usable-capacity-wh", action="append")
    parser.add_argument("--apply-energy-merge", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    return parser
