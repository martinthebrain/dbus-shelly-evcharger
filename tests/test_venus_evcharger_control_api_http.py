# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from tests.control_api_http_cases_auth_events import _ControlApiHttpAuthEventsCases
from tests.control_api_http_cases_state import _ControlApiHttpStateCases
from tests.control_api_http_cases_storage_server import _ControlApiHttpStorageServerCases
from tests.control_api_http_cases_tail import _ControlApiHttpTailCases


class TestLocalControlApiHttpServer(
    _ControlApiHttpStorageServerCases,
    _ControlApiHttpStateCases,
    _ControlApiHttpAuthEventsCases,
    _ControlApiHttpTailCases,
    unittest.TestCase,
):
    pass
