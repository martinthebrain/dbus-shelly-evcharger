# SPDX-License-Identifier: GPL-3.0-or-later
"""Internal Auto-mode decision workflow helpers for the Shelly wallbox service.

The Auto controller keeps the policy readable by splitting the decision tree
into many small helper methods. The high-level behavior is:
- gather fresh PV, grid, and battery inputs
- smooth the relevant values
- check hard safety gates first
- evaluate start/stop conditions
- return the desired relay state plus a diagnostic health reason
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import math
import time
from datetime import datetime
from typing import Any, Deque

from dbus_shelly_wallbox_auto_policy import AutoPolicy, validate_auto_policy
from dbus_shelly_wallbox_common import _auto_state_code, _derive_auto_state

AutoSample = tuple[float, float, float]
AutoDecision = bool | object
MonthWindow = tuple[tuple[int, int], tuple[int, int]]


from dbus_shelly_wallbox_auto_logic_decisions import _AutoDecisionDecisionMixin
from dbus_shelly_wallbox_auto_logic_gates import _AutoDecisionGatesMixin
from dbus_shelly_wallbox_auto_logic_samples import _AutoDecisionSamplesMixin


class AutoDecisionWorkflowMixin(
    _AutoDecisionSamplesMixin,
    _AutoDecisionGatesMixin,
    _AutoDecisionDecisionMixin,
):
    """Provide the detailed Auto-mode decision flow used by AutoDecisionController."""
