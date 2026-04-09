# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared DBus input-reading helpers for the Shelly wallbox service."""

from __future__ import annotations

import time
from typing import Any

import dbus
from dbus_shelly_wallbox_dbus_inputs_pv import _DbusInputPvMixin
from dbus_shelly_wallbox_dbus_inputs_storage import _DbusInputStorageMixin

_TEST_PATCH_EXPORTS = (dbus, time)


class DbusInputController(_DbusInputPvMixin, _DbusInputStorageMixin):
    """Encapsulate PV, battery, and grid DBus discovery/reads for the main service."""

    def __init__(self, port: Any) -> None:
        self.port = port
        self.service = port
        if hasattr(port, "bind_controller"):
            port.bind_controller(self)
