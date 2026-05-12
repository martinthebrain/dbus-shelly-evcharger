# SPDX-License-Identifier: GPL-3.0-or-later
from tests.wizard_setup_cases_presets_support import *  # noqa: F401,F403
from tests.wizard_setup_cases_presets_part1 import __WizardSetupPresetCasesPart1
from tests.wizard_setup_cases_presets_part2 import __WizardSetupPresetCasesPart2

class _WizardSetupPresetCases(__WizardSetupPresetCasesPart1, __WizardSetupPresetCasesPart2):
    pass
