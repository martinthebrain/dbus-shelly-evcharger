# SPDX-License-Identifier: GPL-3.0-or-later
"""Tasmota-style HTTP/JSON meter backend."""

from __future__ import annotations

from .template_meter import TemplateMeterBackend


class TasmotaMeterBackend(TemplateMeterBackend):
    """Template-backed meter alias for Tasmota HTTP/JSON devices."""
