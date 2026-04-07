#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
DEFAULT_VENV_MYPY="$HOME/.venvs/mypy/bin/mypy"

if command -v mypy >/dev/null 2>&1; then
    MYPY_BIN=$(command -v mypy)
elif [[ -x "${MYPY_BIN:-}" ]]; then
    MYPY_BIN="${MYPY_BIN}"
elif [[ -x "$DEFAULT_VENV_MYPY" ]]; then
    MYPY_BIN="$DEFAULT_VENV_MYPY"
else
    echo "mypy is not installed or not on PATH."
    echo "Install it in a venv, for example:"
    echo "  python3 -m venv ~/.venvs/mypy"
    echo "  ~/.venvs/mypy/bin/pip install mypy"
    exit 127
fi

cd "$SCRIPT_DIR"
"$MYPY_BIN" --config-file "$SCRIPT_DIR/mypy.ini"
