# SPDX-License-Identifier: GPL-3.0-or-later
"""Tuya-style HTTP/JSON meter backend."""

from __future__ import annotations

from .template_meter import TemplateMeterBackend


class TuyaMeterBackend(TemplateMeterBackend):
    """Template-backed meter alias for Tuya-compatible local HTTP bridges."""
