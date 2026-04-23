# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="attr-defined,no-any-return"
# pyright: reportAttributeAccessIssue=false, reportReturnType=false
"""Victron ESS balance-bias learning helpers."""

from __future__ import annotations

from .victron_ess_balance_learning_profiles import _UpdateCycleVictronEssBalanceLearningProfilesMixin
from .victron_ess_balance_learning_telemetry import _UpdateCycleVictronEssBalanceLearningTelemetryMixin


class _UpdateCycleVictronEssBalanceLearningMixin(
    _UpdateCycleVictronEssBalanceLearningTelemetryMixin,
    _UpdateCycleVictronEssBalanceLearningProfilesMixin,
):
    """Composed Victron ESS balance-bias learning helpers."""

