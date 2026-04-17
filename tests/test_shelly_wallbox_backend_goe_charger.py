# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from shelly_wallbox.backend.goe_charger import (
    GoEChargerBackend,
    _goe_auth,
    _goe_fault_text,
    _goe_headers,
    _goe_nrg_values,
    _goe_optional_bool,
    _goe_payload,
    _goe_phase_selection,
    _goe_rounded_current_setting,
    load_goe_charger_settings,
)
from shelly_wallbox.backend.template_support import TemplateAuthSettings


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

    def test_goe_helpers_cover_optional_auth_payload_and_bool_paths(self) -> None:
        digest_settings = TemplateAuthSettings("user", "secret", True, None, None)
        self.assertIsNotNone(_goe_auth(digest_settings))
        self.assertEqual(
            _goe_auth(TemplateAuthSettings("user", "secret", False, None, None)),
            ("user", "secret"),
        )
        self.assertIsNone(_goe_auth(TemplateAuthSettings("", "", False, None, None)))
        self.assertEqual(
            _goe_headers(TemplateAuthSettings("", "", False, "X-Test", "value")),
            {"X-Test": "value"},
        )
        self.assertIsNone(_goe_headers(TemplateAuthSettings("", "", False, None, "value")))
        self.assertEqual(_goe_payload("bad"), {})
        self.assertEqual(_goe_payload({"data": {"ok": 1}}), {"ok": 1})
        self.assertEqual(_goe_payload({"ok": 2}), {"ok": 2})
        self.assertTrue(_goe_optional_bool(True))
        self.assertTrue(_goe_optional_bool(1))
        self.assertTrue(_goe_optional_bool("true"))
        self.assertFalse(_goe_optional_bool("off"))
        self.assertIsNone(_goe_optional_bool(None))
        self.assertIsNone(_goe_optional_bool("maybe"))
        self.assertEqual(_goe_phase_selection({}, "P1_P2"), "P1_P2")
        self.assertEqual(_goe_phase_selection({"pnp": 2}, "P1"), "P1_P2")
        self.assertEqual(_goe_phase_selection({"pnp": 1}, "P1_P2_P3"), "P1")
        self.assertIsNone(_goe_nrg_values({"nrg": "bad"}))
        self.assertIsNone(_goe_nrg_values({"nrg": [230, "bad"]}))
        self.assertEqual(_goe_fault_text({"car": 0}), "error")
        self.assertEqual(_goe_fault_text({"car": 5}), "error")
        self.assertIsNone(_goe_fault_text({"car": 1, "err": 0}))
        self.assertEqual(_goe_fault_text({"err": 99}), "error-99")

    def test_goe_settings_and_backend_cover_error_and_request_branches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_base_url = self._write_config(temp_dir, "[Adapter]\nType=goe_charger\n")
            with self.assertRaisesRegex(ValueError, "requires Adapter.BaseUrl"):
                load_goe_charger_settings(self._service(MagicMock()), missing_base_url)

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=goe_charger\nBaseUrl=http://goe.local\nRequestTimeoutSeconds=0\n"
                "Username=user\nPassword=secret\nUseDigestAuth=1\n",
            )
            settings = load_goe_charger_settings(self._service(MagicMock()), config_path)
            self.assertEqual(settings.timeout_seconds, 2.0)

            session = MagicMock()
            session.get.return_value = _FakeResponse({"amp": False})
            backend = GoEChargerBackend(self._service(session), config_path=config_path)
            kwargs = backend._request_kwargs("http://goe.local/api/set", params={"amp": "16"})
            self.assertEqual(kwargs["params"], {"amp": "16"})
            self.assertIn("auth", kwargs)
            with self.assertRaisesRegex(RuntimeError, "rejected amp=16"):
                backend._set_value("amp", 16)

            session.get.return_value = _FakeResponse({"amp": "rejected"})
            with self.assertRaisesRegex(RuntimeError, "rejected amp=16"):
                backend._set_value("amp", 16)

            with self.assertRaisesRegex(ValueError, "Unsupported charger current '0'"):
                backend.set_current(0)
            with self.assertRaisesRegex(ValueError, "expected 6..32 A"):
                _goe_rounded_current_setting(40)

            backend._observed_phase_selection = "P1"
            backend.set_phase_selection("P1")

    def test_request_kwargs_omits_optional_params_and_auth_when_not_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=goe_charger\nBaseUrl=http://goe.local\n",
            )
            backend = GoEChargerBackend(self._service(MagicMock()), config_path=config_path)

            self.assertEqual(
                backend._request_kwargs("http://goe.local/api/status"),
                {"url": "http://goe.local/api/status", "timeout": 2.0},
            )
