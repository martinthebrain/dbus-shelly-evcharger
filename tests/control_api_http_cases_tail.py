# SPDX-License-Identifier: GPL-3.0-or-later
from tests.control_api_http_cases_tail_support import *  # noqa: F401,F403
from tests.control_api_http_cases_tail_part1 import __ControlApiHttpTailCasesPart1
from tests.control_api_http_cases_tail_part2 import __ControlApiHttpTailCasesPart2

class _ControlApiHttpTailCases(__ControlApiHttpTailCasesPart1, __ControlApiHttpTailCasesPart2):
    pass
