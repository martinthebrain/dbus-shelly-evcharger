# SPDX-License-Identifier: GPL-3.0-or-later
"""Package facade for the legacy runtime-health implementation."""

from dbus_shelly_wallbox_runtime_health import *  # noqa: F401,F403
from dbus_shelly_wallbox_runtime_health import _RuntimeSupportHealthMixin

__all__ = ["_RuntimeSupportHealthMixin"]
