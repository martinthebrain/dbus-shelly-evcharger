#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

# Installer for the Shelly wallbox service on Venus OS / Cerbo GX.
#
# The installer does four main things:
# 1. validate that the wallbox config exists
# 2. restore executable bits on Python and shell entrypoints
# 3. register the runit service symlink under /service
# 4. make sure rc.local calls the lightweight boot helper on every reboot
#
# It is safe to run this script repeatedly after updates.

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
SERVICE_NAME="dbus-shelly-wallbox"
SERVICE_DIR="$SCRIPT_DIR/service_shelly_wallbox"
GENERIC_SHELLY_HELPER="$SCRIPT_DIR/disable_generic_shelly_once.py"
BOOT_HELPER="$SCRIPT_DIR/boot_shelly_wallbox.sh"
RC_LOCAL_FILE=/data/rc.local

mkdir -p "$SERVICE_DIR/log"

# The service is intentionally config-driven. Fail early if the config file is
# missing so the user notices a broken deployment immediately.
if [ ! -f "$SCRIPT_DIR/config.shelly_wallbox.ini" ]; then
    echo "config.shelly_wallbox.ini file not found."
    exit 1
fi

# Restore execute bits for all directly launched entrypoints.
chmod a+x "$SCRIPT_DIR/dbus_shelly_wallbox.py"
chmod 755 "$SCRIPT_DIR/dbus_shelly_wallbox.py"

if [ -f "$GENERIC_SHELLY_HELPER" ]; then
    chmod a+x "$GENERIC_SHELLY_HELPER"
    chmod 755 "$GENERIC_SHELLY_HELPER"
fi

if [ -f "$SCRIPT_DIR/shelly_wallbox_auto_input_helper.py" ]; then
    chmod a+x "$SCRIPT_DIR/shelly_wallbox_auto_input_helper.py"
    chmod 755 "$SCRIPT_DIR/shelly_wallbox_auto_input_helper.py"
fi

if [ -f "$SCRIPT_DIR/restart_shelly_wallbox.sh" ]; then
    chmod a+x "$SCRIPT_DIR/restart_shelly_wallbox.sh"
    chmod 744 "$SCRIPT_DIR/restart_shelly_wallbox.sh"
fi

if [ -f "$BOOT_HELPER" ]; then
    chmod a+x "$BOOT_HELPER"
    chmod 755 "$BOOT_HELPER"
fi

if [ -f "$SCRIPT_DIR/uninstall_shelly_wallbox.sh" ]; then
    chmod a+x "$SCRIPT_DIR/uninstall_shelly_wallbox.sh"
    chmod 744 "$SCRIPT_DIR/uninstall_shelly_wallbox.sh"
fi

if [ -f "$SCRIPT_DIR/cerbo_soak_check.sh" ]; then
    chmod a+x "$SCRIPT_DIR/cerbo_soak_check.sh"
    chmod 744 "$SCRIPT_DIR/cerbo_soak_check.sh"
fi

chmod a+x "$SERVICE_DIR/run"
chmod 755 "$SERVICE_DIR/run"
chmod a+x "$SERVICE_DIR/log/run"
chmod 755 "$SERVICE_DIR/log/run"

# Register or update the runit service symlink.
ln -sfn "$SERVICE_DIR" "/service/$SERVICE_NAME"

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

# Create rc.local if the target system does not yet have one.
if [ ! -f "$RC_LOCAL_FILE" ]; then
    touch "$RC_LOCAL_FILE"
    chmod 755 "$RC_LOCAL_FILE"
    echo "#!/bin/bash" >> "$RC_LOCAL_FILE"
    echo >> "$RC_LOCAL_FILE"
fi

remove_rc_local_line "$SCRIPT_DIR/install_shelly_wallbox.sh"
remove_rc_local_line "$SCRIPT_DIR/install.sh"

# Keep rc.local lean: call the dedicated boot helper, not the full installer.
STARTUP="$BOOT_HELPER"
grep -qxF "$STARTUP" "$RC_LOCAL_FILE" || echo "$STARTUP" >> "$RC_LOCAL_FILE"

# Remove an obsolete direct generic helper line if one exists. The boot helper
# handles that logic now.
GENERIC_SHELLY_HELPER_CMD="$GENERIC_SHELLY_HELPER $SCRIPT_DIR/config.shelly_wallbox.ini >/dev/null 2>&1 &"
remove_rc_local_line "$GENERIC_SHELLY_HELPER_CMD"
