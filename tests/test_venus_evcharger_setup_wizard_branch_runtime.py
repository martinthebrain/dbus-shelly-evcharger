# SPDX-License-Identifier: GPL-3.0-or-later
from tests.wizard_branch_runtime_cases_cli import _WizardBranchRuntimeCliCases
from tests.wizard_branch_runtime_cases_common import unittest
from tests.wizard_branch_runtime_cases_core import _WizardBranchRuntimeCoreCases
from tests.wizard_branch_runtime_cases_import_policy import _WizardBranchRuntimeImportPolicyCases


class TestShellyWallboxWizardBranchRuntime(
    _WizardBranchRuntimeCoreCases,
    _WizardBranchRuntimeCliCases,
    _WizardBranchRuntimeImportPolicyCases,
    unittest.TestCase,
):
    pass


if __name__ == "__main__":
    unittest.main()
