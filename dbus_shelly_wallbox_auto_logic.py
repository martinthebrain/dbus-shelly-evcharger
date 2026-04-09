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

import time

from dbus_shelly_wallbox_auto_logic_decisions import _AutoDecisionDecisionMixin
from dbus_shelly_wallbox_auto_logic_gates import _AutoDecisionGatesMixin
from dbus_shelly_wallbox_auto_logic_samples import _AutoDecisionSamplesMixin
from dbus_shelly_wallbox_auto_logic_types import NO_RELAY_DECISION, RelayDecisionState

# Tests patch `dbus_shelly_wallbox_auto_logic.time.time` to cover default clock paths.
_TEST_PATCH_EXPORTS = (time,)

__all__ = ("AutoDecisionWorkflowMixin", "NO_RELAY_DECISION", "RelayDecisionState")


class AutoDecisionWorkflowMixin(
    _AutoDecisionSamplesMixin,
    _AutoDecisionGatesMixin,
    _AutoDecisionDecisionMixin,
):
    """Provide the detailed Auto-mode decision flow used by AutoDecisionController."""
