# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from venus_evcharger.core.contracts import (
    STATE_API_KINDS,
    STATE_API_VERSIONS,
    normalized_state_api_config_effective_fields,
    normalized_state_api_health_fields,
    normalized_state_api_kind,
    normalized_state_api_operational_fields,
    normalized_state_api_operational_state_fields,
    normalized_state_api_runtime_fields,
    normalized_state_api_summary_fields,
    normalized_state_api_topology_fields,
    normalized_state_api_update_fields,
    normalized_state_api_version,
)


class TestVenusEvchargerStateContracts(unittest.TestCase):
    def test_state_contract_constants_and_version_kind_normalizers_are_stable(self) -> None:
        self.assertEqual(STATE_API_VERSIONS, frozenset({"v1"}))
        self.assertEqual(
            STATE_API_KINDS,
            frozenset(
                {
                    "summary",
                    "runtime",
                    "operational",
                    "dbus-diagnostics",
                    "topology",
                    "update",
                    "config-effective",
                    "health",
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

    def test_operational_state_contract_normalizes_known_fields(self) -> None:
        state = normalized_state_api_operational_state_fields(
            {
                "mode": "2",
                "enable": 1,
                "startstop": 0,
                "autostart": 1,
                "active_phase_selection": " P1_P2 ",
                "requested_phase_selection": "",
                "backend_mode": " split ",
                "meter_backend": " template_meter ",
                "switch_backend": "",
                "charger_backend": None,
                "auto_state": " charging ",
                "auto_state_code": 999,
                "fault_active": 0,
                "fault_reason": "",
                "software_update_state": "available",
                "software_update_available": 1,
                "software_update_no_update_active": 1,
                "runtime_overrides_active": 1,
                "runtime_overrides_path": " /run/x.ini ",
            }
        )
        self.assertEqual(state["mode"], 2)
        self.assertEqual(state["enable"], 1)
        self.assertEqual(state["requested_phase_selection"], "P1")
        self.assertEqual(state["backend_mode"], "split")
        self.assertEqual(state["switch_backend"], "na")
        self.assertEqual(state["charger_backend"], "na")
        self.assertEqual(state["auto_state"], "charging")
        self.assertEqual(state["auto_state_code"], 3)
        self.assertEqual(state["fault_reason"], "na")
        self.assertEqual(state["software_update_state"], "available-blocked")
        self.assertEqual(state["software_update_state_code"], 4)
        self.assertEqual(state["runtime_overrides_path"], "/run/x.ini")

    def test_operational_envelope_wraps_normalized_state(self) -> None:
        payload = normalized_state_api_operational_fields(
            {
                "ok": 1,
                "api_version": " v1 ",
                "kind": "operational",
                "state": {"mode": 1, "auto_state": "idle"},
            }
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["api_version"], "v1")
        self.assertEqual(payload["kind"], "operational")
        self.assertEqual(payload["state"]["mode"], 1)
        self.assertEqual(payload["state"]["auto_state"], "idle")

    def test_dbus_diagnostics_envelope_normalizes_mapping_keys(self) -> None:
        from venus_evcharger.core.contracts import normalized_state_api_dbus_diagnostics_fields

        payload = normalized_state_api_dbus_diagnostics_fields(
            {
                "ok": 1,
                "api_version": "v1",
                "kind": "dbus-diagnostics",
                "state": {
                    "/Auto/State": "charging",
                    123: 456,
                },
            }
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["kind"], "dbus-diagnostics")
        self.assertEqual(payload["state"]["/Auto/State"], "charging")
        self.assertEqual(payload["state"]["123"], 456)

    def test_dbus_diagnostics_envelope_falls_back_to_empty_state_for_non_mapping(self) -> None:
        from venus_evcharger.core.contracts import normalized_state_api_dbus_diagnostics_fields

        payload = normalized_state_api_dbus_diagnostics_fields(
            {
                "ok": 1,
                "api_version": "v1",
                "kind": "dbus-diagnostics",
                "state": "not-a-mapping",
            }
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["kind"], "dbus-diagnostics")
        self.assertEqual(payload["state"], {})

    def test_additional_state_envelopes_normalize_generic_mappings(self) -> None:
        topology = normalized_state_api_topology_fields(
            {
                "ok": 1,
                "api_version": "v1",
                "kind": "topology",
                "state": {"backend_mode": "split", "available_modes": [0, 1, 2]},
            }
        )
        update = normalized_state_api_update_fields(
            {
                "ok": 1,
                "api_version": "v1",
                "kind": "update",
                "state": {
                    "available": 1,
                    "no_update_active": 0,
                    "last_check_at": "12.5",
                },
            }
        )
        config_effective = normalized_state_api_config_effective_fields(
            {
                "ok": 1,
                "api_version": "v1",
                "kind": "config-effective",
                "state": {"host": "charger.local"},
            }
        )
        health = normalized_state_api_health_fields(
            {
                "ok": 1,
                "api_version": "v1",
                "kind": "health",
                "state": {
                    "health_code": "3",
                    "fault_active": 1,
                    "listen_port": "8765",
                    "update_stale": 0,
                },
            }
        )

        self.assertEqual(topology["kind"], "topology")
        self.assertEqual(topology["state"]["backend_mode"], "split")
        self.assertTrue(update["state"]["available"])
        self.assertAlmostEqual(update["state"]["last_check_at"], 12.5)
        self.assertEqual(config_effective["state"]["host"], "charger.local")
        self.assertEqual(health["state"]["health_code"], 3)
        self.assertTrue(health["state"]["fault_active"])
        self.assertEqual(health["state"]["listen_port"], 8765)
        self.assertFalse(health["state"]["update_stale"])

    def test_update_and_health_envelopes_handle_empty_state_without_extra_keys(self) -> None:
        update = normalized_state_api_update_fields(
            {
                "ok": 1,
                "api_version": "v1",
                "kind": "update",
                "state": {},
            }
        )
        health = normalized_state_api_health_fields(
            {
                "ok": 1,
                "api_version": "v1",
                "kind": "health",
                "state": {},
            }
        )

        self.assertEqual(update["state"], {})
        self.assertEqual(health["state"], {})
