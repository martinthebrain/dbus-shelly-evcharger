# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from shelly_wallbox.backend.goe_charger import GoEChargerBackend


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class TestShellyWallboxBackendGoECharger(unittest.TestCase):
    @staticmethod
    def _service(session: object) -> SimpleNamespace:
        return SimpleNamespace(
            session=session,
            shelly_request_timeout_seconds=2.0,
        )

    @staticmethod
    def _write_config(directory: str, content: str) -> str:
        path = Path(directory) / "goe-charger.ini"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_set_enabled_forces_goe_force_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=goe_charger\nBaseUrl=http://goe.local\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({})
            backend = GoEChargerBackend(self._service(session), config_path=config_path)

            backend.set_enabled(True)

            session.get.assert_called_once_with(
                url="http://goe.local/api/set",
                timeout=2.0,
                params={"frc": "2"},
            )

    def test_set_enabled_supports_custom_auth_header(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=goe_charger\nBaseUrl=http://goe.local\n"
                "AuthHeaderName=Authorization\nAuthHeaderValue=Bearer token\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({})
            backend = GoEChargerBackend(self._service(session), config_path=config_path)

            backend.set_enabled(False)

            session.get.assert_called_once_with(
                url="http://goe.local/api/set",
                timeout=2.0,
                params={"frc": "1"},
                headers={"Authorization": "Bearer token"},
            )

    def test_set_current_rounds_to_whole_ampere(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=goe_charger\nBaseUrl=http://goe.local\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({})
            backend = GoEChargerBackend(self._service(session), config_path=config_path)

            backend.set_current(12.6)

            session.get.assert_called_once_with(
                url="http://goe.local/api/set",
                timeout=2.0,
                params={"amp": "13"},
            )

    def test_read_charger_state_maps_goe_status_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=goe_charger\nBaseUrl=http://goe.local\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "alw": True,
                    "amp": 16,
                    "car": 2,
                    "err": 13,
                    "eto": 12500,
                    "pnp": 3,
                    "nrg": [230, 230, 230, 230, 60, 60, 60, 14, 14, 14, 0, 414],
                }
            )
            backend = GoEChargerBackend(self._service(session), config_path=config_path)

            state = backend.read_charger_state()

            session.get.assert_called_once_with(
                url="http://goe.local/api/status",
                timeout=2.0,
                params={"filter": "alw,amp,acu,car,err,eto,nrg,pnp"},
            )
            self.assertTrue(state.enabled)
            self.assertEqual(state.current_amps, 16.0)
            self.assertEqual(state.phase_selection, "P1_P2_P3")
            self.assertEqual(state.actual_current_amps, 6.0)
            self.assertEqual(state.power_w, 4140.0)
            self.assertEqual(state.energy_kwh, 12.5)
            self.assertEqual(state.status_text, "charging")
            self.assertEqual(state.fault_text, "error-overtemp")

    def test_set_phase_selection_rejects_changes_without_documented_support(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=goe_charger\nBaseUrl=http://goe.local\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"pnp": 3})
            backend = GoEChargerBackend(self._service(session), config_path=config_path)
            backend.read_charger_state()

            with self.assertRaisesRegex(ValueError, "does not support documented native phase switching"):
                backend.set_phase_selection("P1")
