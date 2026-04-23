# SPDX-License-Identifier: GPL-3.0-or-later
"""Internal Auto-mode decision workflow helpers for the Venus EV charger service.

The public import path stays stable here while the actual decision helpers live
in smaller implementation modules.
"""

from __future__ import annotations

import logging

from venus_evcharger.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin

from .logic_gates_battery_balance import _AutoDecisionBatteryBalanceMixin
from .logic_gates_battery_learning import _AutoDecisionBatteryLearningMixin
from .logic_gates_metrics import _AutoDecisionMetricsMixin
from .logic_gates_runtime import _AutoDecisionRuntimeGatesMixin


class _AutoDecisionGatesMixin(
    _AutoDecisionRuntimeGatesMixin,
    _AutoDecisionMetricsMixin,
    _AutoDecisionBatteryBalanceMixin,
    _AutoDecisionBatteryLearningMixin,
    _ComposableControllerMixin,
):
    """Composed Auto decision helpers kept under the legacy module path."""


__all__ = ["_AutoDecisionGatesMixin", "logging"]
