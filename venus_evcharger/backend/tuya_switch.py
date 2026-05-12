# SPDX-License-Identifier: GPL-3.0-or-later
"""Tuya-style HTTP/JSON switch backends."""

from __future__ import annotations

from dataclasses import replace

from .template_switch import TemplateSwitchBackend


class TuyaSwitchBackend(TemplateSwitchBackend):
    """Template-backed switch alias for Tuya-compatible local HTTP bridges."""


class TuyaContactorSwitchBackend(TuyaSwitchBackend):
    """Tuya switch backend treated as an external contactor by default."""

    def __init__(self, service: object, config_path: str = "") -> None:
        super().__init__(service, config_path=config_path)
        self.settings = replace(
            self.settings,
            switching_mode="contactor",
            max_direct_switch_power_w=None,
        )
