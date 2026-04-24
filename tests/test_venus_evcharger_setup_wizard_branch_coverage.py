# SPDX-License-Identifier: GPL-3.0-or-later
from tests.wizard_branch_coverage_cases_common import unittest
from tests.wizard_branch_coverage_cases_guidance import _WizardBranchCoverageGuidanceCases
from tests.wizard_branch_coverage_cases_imports import _WizardBranchCoverageImportCases
from tests.wizard_branch_coverage_cases_output import _WizardBranchCoverageOutputCases


class TestShellyWallboxWizardBranchCoverage(
    _WizardBranchCoverageOutputCases,
    _WizardBranchCoverageGuidanceCases,
    _WizardBranchCoverageImportCases,
    unittest.TestCase,
):
    pass

if __name__ == "__main__":
    unittest.main()
