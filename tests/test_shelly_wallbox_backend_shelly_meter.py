# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from shelly_wallbox.backend.shelly_meter import ShellyMeterBackend


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class TestShellyWallboxBackendShellyMeter(unittest.TestCase):
    @staticmethod
    def _service(session: object) -> SimpleNamespace:
        return SimpleNamespace(
            session=session,
            host="192.168.1.10",
            username="",
            password="",
            use_digest_auth=False,
            shelly_request_timeout_seconds=2.0,
            pm_component="Switch",
            pm_id=0,
            phase="L1",
            max_current=16.0,
            _last_voltage=230.0,
        )

    def test_shelly_meter_uses_pm1_profile_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "meter.ini"
            path.write_text(
                "[Adapter]\nType=shelly_meter\nHost=192.168.1.50\nShellyProfile=pm1_meter_only\n",
                encoding="utf-8",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "apower": 920.0,
                    "current": 4.0,
                    "voltage": 230.0,
                    "aenergy": {"total": 1234.0},
                }
            )

            backend = ShellyMeterBackend(self._service(session), config_path=str(path))
            reading = backend.read_meter()

            self.assertEqual(backend.settings.profile_name, "pm1_meter_only")
            self.assertEqual(backend.settings.component, "PM1")
            self.assertEqual(reading.power_w, 920.0)
            self.assertEqual(reading.energy_kwh, 1.234)
            self.assertEqual(
                [call.kwargs["url"] for call in session.get.call_args_list],
                ["http://192.168.1.50/rpc/PM1.GetStatus?id=0"],
            )

    def test_shelly_meter_normalizes_em1_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "meter.ini"
            path.write_text(
                "[Adapter]\nType=shelly_meter\nHost=192.168.1.51\nShellyProfile=em1_meter_single_or_dual\n",
                encoding="utf-8",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "act_power": 1234.0,
                    "current": 5.4,
                    "voltage": 228.0,
                    "total_act_energy": 6789.0,
                }
            )

            backend = ShellyMeterBackend(self._service(session), config_path=str(path))
            reading = backend.read_meter()

            self.assertEqual(backend.settings.component, "EM1")
            self.assertEqual(reading.power_w, 1234.0)
            self.assertEqual(reading.current_a, 5.4)
            self.assertEqual(reading.voltage_v, 228.0)
            self.assertEqual(reading.energy_kwh, 6.789)
            self.assertEqual(
                [call.kwargs["url"] for call in session.get.call_args_list],
                ["http://192.168.1.51/rpc/EM1.GetStatus?id=0"],
            )

    def test_shelly_meter_normalizes_em_three_phase_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "meter.ini"
            path.write_text(
                "[Adapter]\nType=shelly_meter\nHost=192.168.1.52\nShellyProfile=em_3phase_profiled\n"
                "[Phase]\nMeasuredPhaseSelection=P1_P2_P3\n",
                encoding="utf-8",
            )
            session = MagicMock()
            session.get.return_value = _FakeResponse(
                {
                    "a_act_power": 1100.0,
                    "b_act_power": 1200.0,
                    "c_act_power": 1150.0,
                    "a_current": 4.8,
                    "b_current": 5.1,
                    "c_current": 5.0,
                    "a_voltage": 229.0,
                    "b_voltage": 230.0,
                    "c_voltage": 231.0,
                    "a_total_act_energy": 1000.0,
                    "b_total_act_energy": 2000.0,
                    "c_total_act_energy": 3000.0,
                }
            )

            backend = ShellyMeterBackend(self._service(session), config_path=str(path))
            reading = backend.read_meter()

            self.assertEqual(backend.settings.component, "EM")
            self.assertIsNone(reading.relay_on)
            self.assertEqual(reading.phase_selection, "P1_P2_P3")
            self.assertEqual(reading.power_w, 3450.0)
            self.assertAlmostEqual(reading.current_a or 0.0, 14.9, places=6)
            self.assertEqual(reading.voltage_v, 230.0)
            self.assertEqual(reading.energy_kwh, 6.0)
            self.assertEqual(reading.phase_powers_w, (1100.0, 1200.0, 1150.0))
            self.assertEqual(reading.phase_currents_a, (4.8, 5.1, 5.0))
            self.assertEqual(
                [call.kwargs["url"] for call in session.get.call_args_list],
                ["http://192.168.1.52/rpc/EM.GetStatus?id=0"],
            )
