#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

cd "$SCRIPT_DIR"

echo "[1/3] Syntax check"
python3 -m py_compile \
    dbus_shelly_wallbox.py \
    dbus_shelly_wallbox_bootstrap.py \
    dbus_shelly_wallbox_common.py \
    dbus_shelly_wallbox_ports.py \
    dbus_shelly_wallbox_auto_controller.py \
    dbus_shelly_wallbox_auto_logic.py \
    dbus_shelly_wallbox_dbus_inputs.py \
    dbus_shelly_wallbox_runtime_support.py \
    dbus_shelly_wallbox_write_controller.py \
    dbus_shelly_wallbox_update_cycle.py

echo "[2/3] Unit tests"
python3 -m unittest

echo "[3/3] Type check"
"$SCRIPT_DIR/run_typecheck.sh"

echo "All checks passed."
