# SPDX-License-Identifier: GPL-3.0-or-later
from tests.test_venus_evcharger_state_contracts_support import *  # noqa: F401,F403

class _TestVenusEvchargerStateContractsPart3:
    def test_operational_decision_contract_rejects_unsafe_or_unknown_values(self) -> None:
        decision = normalized_state_api_operational_decision_fields(
            {
                "reason": "",
                "state": "odd",
                "state_code": "bad",
                "relay_intent": "unexpected",
                "surplus_watts": float("nan"),
                "grid_watts": "not-a-number",
                "profile": None,
            }
        )

        self.assertEqual(decision["reason"], "na")
        self.assertEqual(decision["state"], "idle")
        self.assertEqual(decision["state_code"], 0)
        self.assertEqual(decision["relay_intent"], -1)
        self.assertIsNone(decision["surplus_watts"])
        self.assertIsNone(decision["grid_watts"])
        self.assertEqual(decision["profile"], "")
        self.assertEqual(normalized_state_api_operational_decision_fields({"relay_intent": True})["relay_intent"], 1)

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

