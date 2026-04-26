# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from unittest.mock import patch

from venus_evcharger.energy import (
    EnergySourceSnapshot,
    EnergyLearningProfile,
    available_energy_source_profiles,
    aggregate_energy_sources,
    derive_discharge_balance_metrics,
    derive_discharge_control_metrics,
    derive_energy_forecast,
    EnergySourceDefinition,
    energy_source_profile_details,
    energy_source_profile_probe_plan,
    load_energy_source_settings,
    resolve_energy_source_profile,
    summarize_energy_learning_profiles,
    update_energy_learning_profiles,
)


class _EnergyAggregateTestBase(unittest.TestCase):
    pass
