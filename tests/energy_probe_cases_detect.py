# SPDX-License-Identifier: GPL-3.0-or-later
from tests.energy_probe_cases_common import (
    _EnergyProbeBase,
    _ProbeTransport,
    detect_modbus_energy_source,
    io,
    json,
    main,
    patch,
    redirect_stdout,
    tempfile,
)


class _EnergyProbeDetectCases(_EnergyProbeBase):
    def test_detect_modbus_energy_source_uses_huawei_candidates_until_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "energy.ini",
                "[Adapter]\nType=modbus\n"
                "[Transport]\nRequestTimeoutSeconds=2.0\n"
                "[SocRead]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=0.1\n",
            )

            def fake_transport(settings: object) -> object:
                port = getattr(settings, "port")
                if port != 502:
                    raise TimeoutError("tcp timeout")
                return _ProbeTransport(expected_port=502, expected_unit_id=1, value=523)

            with patch("venus_evcharger.energy.probe.create_modbus_transport", side_effect=fake_transport):
                payload = detect_modbus_energy_source(config_path, profile_name="huawei_ma_native_ap")

        self.assertEqual(payload["profile_name"], "huawei_ma_native_ap")
        self.assertEqual(payload["probe_field"]["section"], "SocRead")
        self.assertEqual(payload["detected"]["host"], "192.168.200.1")
        self.assertEqual(payload["detected"]["port"], 502)
        self.assertEqual(payload["detected"]["unit_id"], 1)
        self.assertEqual(payload["detected"]["scaled_value"], 52.300000000000004)
        self.assertEqual(payload["attempts"][0]["port"], 6607)
        self.assertFalse(payload["attempts"][0]["ok"])
        self.assertEqual(payload["attempts"][0]["reason"], "timeout")

    def test_detect_modbus_energy_source_prefers_cli_override_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "energy.ini",
                "[Adapter]\nType=modbus\n"
                "[Transport]\nHost=10.0.0.8\nPort=1502\nUnitId=7\nRequestTimeoutSeconds=2.0\n"
                "[BatteryPowerRead]\nRegisterType=holding\nAddress=10\nDataType=int16\nScale=1\n",
            )

            def fake_transport(settings: object) -> object:
                self.assertEqual(getattr(settings, "host"), "10.0.0.15")
                self.assertEqual(getattr(settings, "port"), 6607)
                self.assertEqual(getattr(settings, "unit_id"), 3)
                return _ProbeTransport(expected_port=6607, expected_unit_id=3, value=42)

            with patch("venus_evcharger.energy.probe.create_modbus_transport", side_effect=fake_transport):
                payload = detect_modbus_energy_source(
                    config_path,
                    profile_name="huawei_ma_native_lan",
                    host="10.0.0.15",
                    port=6607,
                    unit_id=3,
                )

        self.assertEqual(payload["detected"]["host"], "10.0.0.15")
        self.assertEqual(payload["detected"]["port"], 6607)
        self.assertEqual(payload["detected"]["unit_id"], 3)
        self.assertEqual(payload["attempts"], [payload["detected"]])

    def test_main_detect_modbus_energy_prints_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self._write_config(
                temp_dir,
                "energy.ini",
                "[Adapter]\nType=modbus\n"
                "[Transport]\nRequestTimeoutSeconds=2.0\n"
                "[SocRead]\nRegisterType=holding\nAddress=10\nDataType=uint16\nScale=0.1\n",
            )
            stdout = io.StringIO()

            def fake_transport(settings: object) -> object:
                return _ProbeTransport(expected_port=getattr(settings, "port"), expected_unit_id=0, value=481)

            with patch("venus_evcharger.energy.probe.create_modbus_transport", side_effect=fake_transport):
                with redirect_stdout(stdout):
                    rc = main(
                        [
                            "detect-modbus-energy",
                            config_path,
                            "--profile",
                            "huawei_smartlogger_modbus_tcp",
                            "--host",
                            "10.0.0.20",
                            "--unit-id",
                            "0",
                        ]
                    )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual(payload["profile_details"]["platform"], "smartlogger")
        self.assertEqual(payload["detected"]["unit_id"], 0)
