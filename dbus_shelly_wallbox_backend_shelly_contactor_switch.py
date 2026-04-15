# SPDX-License-Identifier: GPL-3.0-or-later
"""Preset Shelly switch backend for relay-to-contactor installations."""

from __future__ import annotations

from dbus_shelly_wallbox_backend_shelly_switch import ShellySwitchBackend


class ShellyContactorSwitchBackend(ShellySwitchBackend):
    """Shelly switch backend with ``contactor`` as the default switching mode."""

    def __init__(self, service: object, config_path: str = "") -> None:
        super().__init__(
            service,
            config_path=config_path,
            default_switching_mode="contactor",
        )
