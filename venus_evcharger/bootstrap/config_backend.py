# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined"
from __future__ import annotations

from venus_evcharger.backend.config import load_backend_selection
from venus_evcharger.core.split_mixins import ComposableControllerMixin as _ComposableControllerMixin


class _ServiceBootstrapBackendConfigMixin(_ComposableControllerMixin):
    def _load_backend_config(self) -> None:
        """Load normalized meter/switch/charger backend selection.

        Backend selection is normalized early because many later config
        decisions depend on the topology shape. A charger-native setup and a
        relay-driven setup expose different valid combinations of meter,
        switch, and charger roles.
        """
        svc = self.service
        selection = load_backend_selection(svc.config)
        svc.backend_mode = selection.mode
        svc.meter_backend_type = selection.meter_type
        svc.switch_backend_type = selection.switch_type
        svc.charger_backend_type = selection.charger_type
        svc.meter_backend_config_path = selection.meter_config_path
        svc.switch_backend_config_path = selection.switch_config_path
        svc.charger_backend_config_path = selection.charger_config_path
