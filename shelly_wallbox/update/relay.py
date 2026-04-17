# SPDX-License-Identifier: GPL-3.0-or-later
"""Composed relay/update-cycle helpers for the Shelly wallbox service."""

from __future__ import annotations

from shelly_wallbox.backend.models import normalize_phase_selection
from shelly_wallbox.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin
from shelly_wallbox.update.relay_charger_current import _RelayChargerCurrentMixin
from shelly_wallbox.update.relay_charger_health import _RelayChargerHealthMixin
from shelly_wallbox.update.relay_charger_readback import _RelayChargerReadbackMixin
from shelly_wallbox.update.relay_phase_decision import _RelayPhaseDecisionMixin
from shelly_wallbox.update.relay_phase_publish import _RelayPhasePublishMixin
from shelly_wallbox.update.relay_phase_switch_policy import _RelayPhaseSwitchPolicyMixin
from shelly_wallbox.update.relay_phase_switch_runtime import _RelayPhaseSwitchRuntimeMixin
from shelly_wallbox.update.relay_status_publish import _RelayStatusPublishMixin


class _UpdateCycleRelayMixin(
    _RelayPhaseDecisionMixin,
    _RelayPhaseSwitchPolicyMixin,
    _RelayChargerReadbackMixin,
    _RelayChargerHealthMixin,
    _RelayChargerCurrentMixin,
    _RelayPhasePublishMixin,
    _RelayPhaseSwitchRuntimeMixin,
    _RelayStatusPublishMixin,
    _ComposableControllerMixin,
):
    """Composed update-cycle relay helpers.

    The update-cycle logic is intentionally split into focused helper modules:
    phase target selection, phase-switch orchestration, charger health/current
    handling, relay confirmation, and outward status publishing.
    """

    PHASE_SWITCH_WAITING_STATE = "waiting-relay-off"
    PHASE_SWITCH_STABILIZING_STATE = "stabilizing"
    CHARGER_FAULT_HINT_TOKENS = frozenset(
        {"fault", "error", "failed", "failure", "alarm", "offline", "unavailable", "lockout", "tripped"}
    )
    CHARGER_STATUS_CHARGING_HINT_TOKENS = frozenset({"charging"})
    CHARGER_STATUS_READY_HINT_TOKENS = frozenset({"ready", "connected", "available", "idle"})
    CHARGER_STATUS_WAITING_HINT_TOKENS = frozenset({"paused", "waiting", "suspended", "sleeping"})
    CHARGER_STATUS_FINISHED_HINT_TOKENS = frozenset({"complete", "completed", "finished", "done"})


__all__ = ["_UpdateCycleRelayMixin", "normalize_phase_selection"]
