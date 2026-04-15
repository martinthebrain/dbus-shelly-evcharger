# SPDX-License-Identifier: GPL-3.0-or-later
"""Package facade for the legacy runtime-setup implementation."""

from dbus_shelly_wallbox_runtime_setup import *  # noqa: F401,F403
from dbus_shelly_wallbox_runtime_setup import _RuntimeSupportSetupMixin

__all__ = ["_RuntimeSupportSetupMixin"]
