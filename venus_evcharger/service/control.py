# SPDX-License-Identifier: GPL-3.0-or-later
"""Control API mixin for the Venus EV charger service."""

from __future__ import annotations

from venus_evcharger.control import LocalControlApiHttpServer

from .control_runtime import _ControlApiRuntimeMixin
from .control_state_config import _ControlApiStateConfigMixin
from .control_state_core import _ControlApiStateCoreMixin
from .control_state_meta import _ControlApiStateMetaMixin
from .control_state_operational import _ControlApiStateOperationalMixin
from .control_state_victron import _ControlApiStateVictronMixin
from .factory import ServiceControllerFactoryMixin

__all__ = ["ControlApiMixin", "LocalControlApiHttpServer"]


class ControlApiMixin(
    _ControlApiStateCoreMixin,
    _ControlApiStateOperationalMixin,
    _ControlApiStateVictronMixin,
    _ControlApiStateConfigMixin,
    _ControlApiStateMetaMixin,
    _ControlApiRuntimeMixin,
    ServiceControllerFactoryMixin,
):
    """Expose canonical command building and optional local HTTP control transport."""
