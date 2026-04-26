# SPDX-License-Identifier: GPL-3.0-or-later
from tests.wizard_branch_coverage_cases_common import _result, wizard_cli_output


class _WizardBranchCoverageOutputCases:
    def test_result_text_covers_optional_sections_and_live_check_rendering(self) -> None:
        preview_text = wizard_cli_output.result_text(_result(dry_run=True))
        self.assertIn("Config preview for: /tmp/config.ini", preview_text)
        self.assertIn("Role endpoints:\n  - none", preview_text)
        self.assertIn("Live connectivity: not run", preview_text)

        persisted_text = wizard_cli_output.result_text(
            _result(
                imported_from="/tmp/source.ini",
                topology_preset="goe-external-switch-group",
                charger_backend="goe_charger",
                transport_kind="serial_rtu",
                role_hosts={"switch": "switch.local"},
                live_check={
                    "ok": False,
                    "roles": {
                        "switch": {"status": "error", "error": "boom"},
                        "charger": "ignored",
                    },
                },
                backup_files=("/tmp/config.ini.bak",),
                result_path="/tmp/config.ini.wizard-result.json",
                audit_path="/tmp/config.ini.wizard-audit.jsonl",
                topology_summary_path="/tmp/config.ini.wizard-topology.txt",
                warnings=("careful",),
            )
        )
        self.assertIn("Imported defaults: /tmp/source.ini", persisted_text)
        self.assertIn("Backup files:\n  - /tmp/config.ini.bak", persisted_text)
        self.assertIn("Warnings:\n  - careful", persisted_text)
        self.assertIn("Wizard result: /tmp/config.ini.wizard-result.json", persisted_text)
        self.assertIn("Live connectivity: check reported issues", persisted_text)
        self.assertIn("  - switch: error (boom)", persisted_text)
        self.assertNotIn("charger: ", persisted_text)
