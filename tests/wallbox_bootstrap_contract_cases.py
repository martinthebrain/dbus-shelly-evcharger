# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from shelly_wallbox.core.contracts import (
    BOOTSTRAP_UPDATE_MODES,
    BOOTSTRAP_UPDATE_RESULTS,
    normalized_bootstrap_string_list,
    normalized_bootstrap_update_mode,
    normalized_bootstrap_update_result,
    normalized_bootstrap_update_status_fields,
)


class TestShellyWallboxBootstrapContracts(unittest.TestCase):
    def test_bootstrap_contract_helpers_normalize_mode_result_and_lists(self) -> None:
        self.assertEqual(BOOTSTRAP_UPDATE_MODES, frozenset({"apply", "dry-run"}))
        self.assertEqual(BOOTSTRAP_UPDATE_RESULTS, frozenset({"success", "failed", "preview"}))
        self.assertEqual(normalized_bootstrap_update_mode(" dry-run "), "dry-run")
        self.assertEqual(normalized_bootstrap_update_mode("unknown"), "apply")
        self.assertEqual(normalized_bootstrap_update_result("preview", mode="apply"), "failed")
        self.assertEqual(normalized_bootstrap_update_result("unknown", mode="dry-run"), "preview")
        self.assertEqual(normalized_bootstrap_string_list([" one ", "", None, "two"]), ["one", "two"])

    def test_bootstrap_status_contract_enforces_safe_preview_and_failure_shape(self) -> None:
        preview = normalized_bootstrap_update_status_fields(
            {
                "mode": "dry-run",
                "result": "success",
                "failure_reason": "ignore-me",
                "config_merge_changed": 1,
                "config_merge_backup_required": 1,
                "config_merge_backup_path": "/tmp/should-not-exist",
                "config_merge_added_keys": [" DEFAULT.Host ", "", None],
                "config_validation_passed": 1,
            }
        )
        self.assertEqual(preview["mode"], "dry-run")
        self.assertEqual(preview["result"], "preview")
        self.assertEqual(preview["failure_reason"], "")
        self.assertEqual(preview["config_merge_backup_path"], "")
        self.assertEqual(preview["config_merge_added_keys"], ["DEFAULT.Host"])

        failed = normalized_bootstrap_update_status_fields(
            {
                "mode": "apply",
                "result": "preview",
                "failure_reason": "config-validation-failed",
                "current_preserved": 0,
                "promotion_aborted_reason": "should-clear",
                "config_merge_changed": 0,
                "config_merge_backup_required": 1,
                "config_merge_backup_path": "/tmp/stale-backup",
                "config_validation_passed": 0,
            }
        )
        self.assertEqual(failed["mode"], "apply")
        self.assertEqual(failed["result"], "failed")
        self.assertEqual(failed["failure_reason"], "config-validation-failed")
        self.assertEqual(failed["promotion_aborted_reason"], "")
        self.assertEqual(failed["config_merge_backup_path"], "")
        self.assertFalse(failed["config_validation_passed"])

