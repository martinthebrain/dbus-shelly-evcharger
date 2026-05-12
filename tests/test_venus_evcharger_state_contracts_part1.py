# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_venus_evcharger_state_contracts_support import *  # noqa: F401,F403

class _TestVenusEvchargerStateContractsPart1:
    def test_state_contract_constants_and_version_kind_normalizers_are_stable(self) -> None:
        self.assertEqual(STATE_API_VERSIONS, frozenset({"v1"}))
        self.assertEqual(
            STATE_API_KINDS,
            frozenset(
                {
                    "automation",
                    "build",
                    "contracts",
                    "healthz",
                    "summary",
                    "runtime",
                    "victron-bias-recommendation",
                    "operational",
                    "dbus-diagnostics",
                    "topology",
                    "update",
                    "config-effective",
                    "health",
                    "version",
                }
            ),
        )
        self.assertEqual(normalized_state_api_version(" v1 "), "v1")
        self.assertEqual(normalized_state_api_version("v2"), "v1")
        self.assertEqual(normalized_state_api_kind(" SUMMARY "), "summary")
        self.assertEqual(normalized_state_api_kind("other", default="runtime"), "runtime")

    def test_state_api_summary_and_runtime_fields_normalize_outer_shape(self) -> None:
        summary = normalized_state_api_summary_fields(
            {
                "ok": 1,
                "api_version": " v1 ",
                "kind": "summary",
                "summary": "  mode=1  ",
            }
        )
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["api_version"], "v1")
        self.assertEqual(summary["kind"], "summary")
        self.assertEqual(summary["summary"], "mode=1")

        runtime = normalized_state_api_runtime_fields(
            {
                "ok": True,
                "api_version": "v1",
                "kind": "runtime",
                "state": {"mode": 1, "autostart": 1},
            }
        )
        self.assertTrue(runtime["ok"])
        self.assertEqual(runtime["kind"], "runtime")
        self.assertEqual(runtime["state"], {"mode": 1, "autostart": 1})
        self.assertEqual(normalized_state_api_runtime_fields({"state": []})["state"], {})


