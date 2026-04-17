# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from shelly_wallbox.auto.policy import (
    AutoLearnChargePowerPolicy,
    AutoPolicy,
    AutoStopEwmaPolicy,
    AutoThresholdProfile,
)
from shelly_wallbox.controllers.state import ServiceStateController

STATE_SUMMARY_TIME = "shelly_wallbox.controllers.state_summary.time.time"
STATE_RUNTIME_PARSER_READ = "shelly_wallbox.controllers.state_runtime._CasePreservingConfigParser.read"
STATE_RUNTIME_WRITE = "shelly_wallbox.controllers.state_runtime.write_text_atomically"
STATE_RUNTIME_LOG_WARNING = "shelly_wallbox.controllers.state_runtime.logging.warning"
STATE_RESTORE_WRITE = "shelly_wallbox.controllers.state_restore.write_text_atomically"
STATE_RESTORE_LOG_WARNING = "shelly_wallbox.controllers.state_restore.logging.warning"
from tests.wallbox_test_fixtures import make_runtime_state_service, make_state_validation_service



class ServiceStateControllerTestBase(unittest.TestCase):
    @staticmethod
    def _normalize_mode(value: object) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, (int, float, str)):
            return int(value)
        return 0
