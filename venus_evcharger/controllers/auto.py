# SPDX-License-Identifier: GPL-3.0-or-later
"""Public Auto-mode controller facade for the Venus EV charger service."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from venus_evcharger.auto.workflow import AutoDecisionWorkflowMixin
from venus_evcharger.auto.logic_types import NO_RELAY_DECISION


class AutoDecisionController(AutoDecisionWorkflowMixin):
    """Thin public facade for the internal Auto-mode decision workflow."""

    _NO_DECISION = NO_RELAY_DECISION

    def __init__(
        self,
        service: Any,
        health_code_func: Callable[[str], int],
        mode_uses_auto_logic_func: Callable[[Any], bool],
    ) -> None:
        self.service = service
        if hasattr(service, "bind_controller"):
            service.bind_controller(self)
        self._health_code = health_code_func
        self._mode_uses_auto_logic = mode_uses_auto_logic_func
