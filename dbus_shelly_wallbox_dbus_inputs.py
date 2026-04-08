# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared DBus input-reading helpers for the Shelly wallbox service."""

from __future__ import annotations

import logging
import time
from typing import Any

import dbus
from dbus_shelly_wallbox_shared import (
    coerce_dbus_numeric,
    configured_grid_paths,
    discovery_cache_valid,
    first_matching_prefixed_service,
    grid_values_complete_enough,
    prefixed_service_names,
    should_assume_zero_pv,
    sum_dbus_numeric,
)
from dbus_shelly_wallbox_dbus_inputs_pv import _DbusInputPvMixin
from dbus_shelly_wallbox_dbus_inputs_storage import _DbusInputStorageMixin


class DbusInputController(_DbusInputPvMixin, _DbusInputStorageMixin):
    """Encapsulate PV, battery, and grid DBus discovery/reads for the main service."""

    def __init__(self, port: Any) -> None:
        self.port = port
        self.service = port
        if hasattr(port, "bind_controller"):
            port.bind_controller(self)
