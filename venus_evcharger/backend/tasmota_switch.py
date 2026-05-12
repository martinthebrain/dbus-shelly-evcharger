# SPDX-License-Identifier: GPL-3.0-or-later
"""Tasmota-style HTTP/JSON switch backends."""

from __future__ import annotations

from dataclasses import replace

from .template_switch import TemplateSwitchBackend


class TasmotaSwitchBackend(TemplateSwitchBackend):
    """Template-backed switch alias for Tasmota HTTP/JSON devices."""


class TasmotaContactorSwitchBackend(TasmotaSwitchBackend):
    """Tasmota switch backend treated as an external contactor by default."""

    def __init__(self, service: object, config_path: str = "") -> None:
        super().__init__(service, config_path=config_path)
        self.settings = replace(
            self.settings,
            switching_mode="contactor",
            max_direct_switch_power_w=None,
        )
