# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from tests.wizard_setup_cases_cli import _WizardSetupCliCases
from tests.wizard_setup_cases_config import _WizardSetupConfigCases
from tests.wizard_setup_cases_config_validation import _WizardSetupConfigValidationCases
from tests.wizard_setup_cases_presets import _WizardSetupPresetCases


class TestShellyWallboxSetupWizard(
    _WizardSetupConfigCases,
    _WizardSetupConfigValidationCases,
    _WizardSetupPresetCases,
    _WizardSetupCliCases,
    unittest.TestCase,
):
    pass


if __name__ == "__main__":
    unittest.main()
