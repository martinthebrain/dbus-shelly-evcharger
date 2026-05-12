# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_wizard_inventory_helpers_support import *  # noqa: F401,F403
from tests.test_wizard_inventory_helpers_part1 import _WizardInventoryHelperTestsPart1
from tests.test_wizard_inventory_helpers_part2 import _WizardInventoryHelperTestsPart2
from tests.test_wizard_inventory_helpers_part3 import _WizardInventoryHelperTestsPart3

class WizardInventoryHelperTests(_WizardInventoryHelperTestsPart1, _WizardInventoryHelperTestsPart2, _WizardInventoryHelperTestsPart3, unittest.TestCase):
    pass

def parser_to_text(parser: configparser.ConfigParser) -> str:
    buffer = io.StringIO()
    parser.write(buffer)
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()
