# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared support for the wallbox Auto input helper test split."""

import json
import os
import runpy
import sys
import tempfile
import unittest
from types import ModuleType
from unittest.mock import MagicMock, patch

sys.modules["dbus"] = MagicMock()

import venus_evcharger_auto_input_helper  # noqa: E402
from venus_evcharger_auto_input_helper import AutoInputHelper, _as_bool  # noqa: E402


class AutoInputHelperTestCase(unittest.TestCase):
    def _make_helper(self):
        helper = AutoInputHelper.__new__(AutoInputHelper)
        helper.config = {}
        helper._warning_state = {}
        helper._source_retry_after = {}
        helper._system_bus = None
        helper._dbus_list_backoff_until = 0.0
        helper._dbus_list_failures = 0
        helper._resolved_auto_pv_services = []
        helper._auto_pv_last_scan = 0.0
        helper._resolved_auto_battery_service = None
        helper._auto_battery_last_scan = 0.0
        helper._last_payload = None
        helper._last_snapshot_state = AutoInputHelper._empty_snapshot()
        helper._next_source_poll_at = {"pv": 0.0, "battery": 0.0, "grid": 0.0}
        helper.poll_interval_seconds = 1.0
        helper.auto_pv_poll_interval_seconds = 2.0
        helper.auto_grid_poll_interval_seconds = 2.0
        helper.auto_battery_poll_interval_seconds = 10.0
        helper.auto_dbus_backoff_base_seconds = 5.0
        helper.auto_dbus_backoff_max_seconds = 60.0
        helper.auto_pv_service = ""
        helper.auto_pv_service_prefix = "com.victronenergy.pvinverter"
        helper.auto_pv_path = "/Ac/Power"
        helper.auto_pv_max_services = 10
        helper.auto_pv_scan_interval_seconds = 60.0
        helper.auto_use_dc_pv = True
        helper.auto_dc_pv_service = "com.victronenergy.system"
        helper.auto_dc_pv_path = "/Dc/Pv/Power"
        helper.auto_battery_service = "com.victronenergy.battery.socketcan_can1"
        helper.auto_battery_soc_path = "/Soc"
        helper.auto_battery_service_prefix = "com.victronenergy.battery"
        helper.auto_battery_scan_interval_seconds = 60.0
        helper.auto_grid_service = "com.victronenergy.system"
        helper.auto_grid_l1_path = "/Ac/Grid/L1/Power"
        helper.auto_grid_l2_path = "/Ac/Grid/L2/Power"
        helper.auto_grid_l3_path = "/Ac/Grid/L3/Power"
        helper.auto_grid_require_all_phases = True
        helper.dbus_method_timeout_seconds = 1.0
        return helper


__all__ = [
    "AutoInputHelper",
    "AutoInputHelperTestCase",
    "MagicMock",
    "ModuleType",
    "_as_bool",
    "json",
    "os",
    "patch",
    "runpy",
    "venus_evcharger_auto_input_helper",
    "sys",
    "tempfile",
    "unittest",
]
