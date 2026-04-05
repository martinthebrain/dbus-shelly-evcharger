#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

# Uninstaller for the Shelly wallbox service.
#
# This removes the runit service symlink, stops the currently running Python
# process, and cleans up the boot hooks that were added to /data/rc.local.

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
SERVICE_NAME="dbus-shelly-wallbox"
RC_LOCAL_FILE=/data/rc.local

# Remove the runit service registration if it exists.
if [ -L /service/$SERVICE_NAME ]; then
    rm /service/$SERVICE_NAME
fi

# Stop any still-running foreground/background wallbox main process.
pkill -f "$SCRIPT_DIR/dbus_shelly_wallbox.py" || true

STARTUP=$SCRIPT_DIR/install_shelly_wallbox.sh
BOOT_HELPER=$SCRIPT_DIR/boot_shelly_wallbox.sh
GENERIC_SHELLY_HELPER_CMD="$SCRIPT_DIR/disable_generic_shelly_once.py $SCRIPT_DIR/config.shelly_wallbox.ini >/dev/null 2>&1 &"

remove_rc_local_line() {
    line="$1"
    if [ ! -f "$RC_LOCAL_FILE" ]; then
        return
    fi
    tmp_file=$(mktemp)
    grep -vxF "$line" "$RC_LOCAL_FILE" > "$tmp_file" || true
    cat "$tmp_file" > "$RC_LOCAL_FILE"
    rm -f "$tmp_file"
}

# Remove both modern and older boot-hook variants.
remove_rc_local_line "$STARTUP"
remove_rc_local_line "$SCRIPT_DIR/install.sh"
remove_rc_local_line "$BOOT_HELPER"
remove_rc_local_line "$GENERIC_SHELLY_HELPER_CMD"
