# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from shelly_wallbox.backend.template_meter import (
    TemplateMeterBackend,
    TemplateMeterSettings,
    _meter_scalar_values,
    _phase_vector,
    _resolved_phase_vector,
)
from shelly_wallbox.backend.template_support import TemplateAuthSettings


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class TestShellyWallboxBackendTemplateMeter(unittest.TestCase):
    @staticmethod
    def _service(session: object) -> SimpleNamespace:
        return SimpleNamespace(
            session=session,
            phase="L2",
            shelly_request_timeout_seconds=2.0,
        )

    @staticmethod
    def _write_config(directory: str, content: str) -> str:
        path = Path(directory) / "template-meter.ini"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_read_meter_uses_normalized_paths_and_derives_phase_vectors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "[Phase]\nMeasuredPhaseSelection=P1\n"
                "[MeterRequest]\nMethod=GET\nUrl=/meter/state\n"
                "[MeterResponse]\nRelayEnabledPath=data.enabled\nPowerPath=data.power_w\n"
                "VoltagePath=data.voltage_v\nCurrentPath=data.current_a\n"
                "EnergyKwhPath=data.energy_kwh\nPhaseSelectionPath=data.phase_selection\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "data": {
                        "enabled": True,
                        "power_w": 3450.0,
                        "voltage_v": 230.0,
                        "current_a": 15.0,
                        "energy_kwh": 6.789,
                        "phase_selection": "P1_P2_P3",
                    }
                }
            )
            backend = TemplateMeterBackend(self._service(session), config_path=config_path)

            reading = backend.read_meter()

            self.assertTrue(reading.relay_on)
            self.assertEqual(reading.power_w, 3450.0)
            self.assertEqual(reading.voltage_v, 230.0)
            self.assertEqual(reading.current_a, 15.0)
            self.assertEqual(reading.energy_kwh, 6.789)
            self.assertEqual(reading.phase_selection, "P1_P2_P3")
            self.assertEqual(reading.phase_powers_w, (1150.0, 1150.0, 1150.0))
            self.assertEqual(reading.phase_currents_a, (5.0, 5.0, 5.0))

    def test_read_meter_accepts_explicit_phase_vectors_and_wh_energy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "[Phase]\nMeasuredPhaseSelection=P1\n"
                "[MeterRequest]\nMethod=GET\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=power\nEnergyWhPath=energy_wh\n"
                "PhasePowersPath=phase_powers\nPhaseCurrentsPath=phase_currents\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "power": 2300.0,
                    "energy_wh": 12500.0,
                    "phase_powers": [0.0, 2300.0, 0.0],
                    "phase_currents": [0.0, 10.0, 0.0],
                }
            )
            backend = TemplateMeterBackend(self._service(session), config_path=config_path)

            reading = backend.read_meter()

            self.assertIsNone(reading.relay_on)
            self.assertEqual(reading.energy_kwh, 12.5)
            self.assertEqual(reading.phase_selection, "P1")
            self.assertEqual(reading.phase_powers_w, (0.0, 2300.0, 0.0))
            self.assertEqual(reading.phase_currents_a, (0.0, 10.0, 0.0))

    def test_read_meter_supports_basic_auth_from_adapter_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "Username=user\nPassword=secret\n"
                "[MeterRequest]\nMethod=GET\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=power_w\n",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse({"power_w": 1234.0})
            backend = TemplateMeterBackend(self._service(session), config_path=config_path)

            reading = backend.read_meter()

            self.assertEqual(reading.power_w, 1234.0)
            session.get.assert_called_once_with(
                url="http://adapter.local/meter/state",
                timeout=2.0,
                auth=("user", "secret"),
            )

    def test_template_meter_requires_request_url_and_power_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "[MeterRequest]\nMethod=GET\n"
                "[MeterResponse]\nPowerPath=\n",
            )

            with self.assertRaises(ValueError):
                TemplateMeterBackend(self._service(MagicMock()), config_path=config_path)

    def test_template_meter_requires_complete_auth_header_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "AuthHeaderName=Authorization\n"
                "[MeterRequest]\nMethod=GET\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=power_w\n",
            )

            with self.assertRaises(ValueError):
                TemplateMeterBackend(self._service(MagicMock()), config_path=config_path)

    def test_template_meter_helper_edges_cover_invalid_vectors_and_enabled_parsing(self) -> None:
        self.assertIsNone(_phase_vector([1.0, 2.0]))
        self.assertIsNone(_resolved_phase_vector((1.0, None, 3.0)))
        self.assertIsNone(TemplateMeterBackend._enabled_state(None))
        self.assertFalse(TemplateMeterBackend._enabled_state(0))
        self.assertTrue(TemplateMeterBackend._enabled_state(1))
        self.assertTrue(TemplateMeterBackend._enabled_state("enabled"))
        self.assertFalse(TemplateMeterBackend._enabled_state("disabled"))
        self.assertIsNone(TemplateMeterBackend._enabled_state("maybe"))

        settings = TemplateMeterSettings(
            base_url="http://meter.local",
            auth_settings=TemplateAuthSettings("", "", False, None, None),
            timeout_seconds=2.0,
            meter_method="GET",
            meter_url="/meter",
            relay_enabled_path=None,
            power_path="power_w",
            voltage_path=None,
            current_path=None,
            energy_kwh_path=None,
            energy_wh_path=None,
            phase_selection="P1",
            phase_selection_path=None,
            phase_powers_path=None,
            phase_currents_path=None,
        )
        with self.assertRaisesRegex(ValueError, "Invalid meter power value"):
            _meter_scalar_values({"power_w": None}, settings)

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "[Adapter]\nType=template_meter\nBaseUrl=http://adapter.local\n"
                "[MeterRequest]\nMethod=GET\nUrl=/meter/state\n"
                "[MeterResponse]\nPowerPath=\n",
            )
            with self.assertRaisesRegex(ValueError, r"requires \[MeterResponse\] PowerPath"):
                TemplateMeterBackend(self._service(MagicMock()), config_path=config_path)
