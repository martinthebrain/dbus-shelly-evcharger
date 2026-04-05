# SPDX-License-Identifier: GPL-3.0-or-later
"""Compatibility facade for the wallbox service mixin modules."""

from dbus_shelly_wallbox_service_auto import DbusAutoLogicMixin
from dbus_shelly_wallbox_service_factory import ServiceControllerFactoryMixin
from dbus_shelly_wallbox_service_runtime import RuntimeHelperMixin
from dbus_shelly_wallbox_service_state_publish import StatePublishMixin
from dbus_shelly_wallbox_service_update import UpdateCycleMixin

__all__ = [
    "DbusAutoLogicMixin",
    "RuntimeHelperMixin",
    "ServiceControllerFactoryMixin",
    "StatePublishMixin",
    "UpdateCycleMixin",
]
