# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for volatile Auto decision tracking."""

from __future__ import annotations

from typing import Any


def clear_auto_decision_tracking(svc: Any) -> bool:
    """Clear transient Auto start/stop timers and return whether state changed."""
    changed = False
    for attribute_name in (
        "auto_start_condition_since",
        "auto_stop_condition_since",
        "auto_stop_condition_reason",
    ):
        if hasattr(svc, attribute_name) and getattr(svc, attribute_name) is not None:
            setattr(svc, attribute_name, None)
            changed = True
    return changed
