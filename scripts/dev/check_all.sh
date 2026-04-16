#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
REPO_DIR=$(realpath "$SCRIPT_DIR/../..")

cd "$REPO_DIR"

echo "[1/3] Syntax check"
python3 -m py_compile \
    dbus_shelly_wallbox.py \
    shelly_wallbox/bootstrap/controller.py \
    shelly_wallbox/core/common.py \
    shelly_wallbox/ports/__init__.py \
    shelly_wallbox/controllers/auto.py \
    shelly_wallbox/auto/workflow.py \
    shelly_wallbox/inputs/dbus.py \
    shelly_wallbox/runtime/support.py \
    shelly_wallbox/controllers/write.py \
    shelly_wallbox/update/controller.py

echo "[2/3] Unit tests"
python3 -m unittest discover -s tests -p 'test_*.py'

echo "[3/3] Type check"
bash "$SCRIPT_DIR/run_typecheck.sh"

echo "All checks passed."
