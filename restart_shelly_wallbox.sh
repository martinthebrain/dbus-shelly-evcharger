#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

# Convenience helper for development or manual recovery.
# It only stops the current wallbox main process from this directory; runit will
# normally bring it back if the service is still registered.

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
pkill -f "$SCRIPT_DIR/dbus_shelly_wallbox.py" || true
