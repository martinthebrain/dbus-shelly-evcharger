# SPDX-License-Identifier: GPL-3.0-or-later
import configparser
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from shelly_wallbox.bootstrap.wizard import WizardAnswers, configure_wallbox, default_template_path, main


class TestShellyWallboxSetupWizardExtensions(unittest.TestCase):
    def test_configure_wallbox_applies_policy_tuning_and_goe_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="native-charger",
                    host_input="goe.local",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input="goe.local",
                    device_instance=71,
                    phase="3P",
                    policy_mode="scheduled",
                    digest_auth=False,
                    username="",
                    password="",
                    charger_backend="goe_charger",
                    request_timeout_seconds=4.5,
                    auto_start_surplus_watts=2100.0,
                    auto_stop_surplus_watts=1650.0,
                    auto_min_soc=35.0,
                    auto_resume_soc=39.0,
                    scheduled_enabled_days="Mon,Wed,Fri",
                    scheduled_latest_end_time="07:15",
                    scheduled_night_current_amps=8.0,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            parser = configparser.ConfigParser()
            parser.read(config_path, encoding="utf-8")
            charger_text = (config_path.parent / "wizard-charger.ini").read_text(encoding="utf-8")
            self.assertEqual(parser["DEFAULT"]["AutoStartSurplusWatts"], "2100")
            self.assertEqual(parser["DEFAULT"]["AutoStopSurplusWatts"], "1650")
            self.assertEqual(parser["DEFAULT"]["AutoMinSoc"], "35")
            self.assertEqual(parser["DEFAULT"]["AutoResumeSoc"], "39")
            self.assertEqual(parser["DEFAULT"]["AutoScheduledEnabledDays"], "Mon,Wed,Fri")
            self.assertEqual(parser["DEFAULT"]["AutoScheduledLatestEndTime"], "07:15")
            self.assertEqual(parser["DEFAULT"]["AutoScheduledNightCurrentAmps"], "8")
            self.assertIn("RequestTimeoutSeconds=4.5\n", charger_text)
            self.assertEqual(result.answer_defaults["request_timeout_seconds"], 4.5)

    def test_configure_wallbox_keeps_switch_group_phase_layout_and_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            result = configure_wallbox(
                WizardAnswers(
                    profile="split-topology",
                    host_input="shared.local",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input="goe.local",
                    device_instance=72,
                    phase="3P",
                    policy_mode="manual",
                    digest_auth=False,
                    username="",
                    password="",
                    split_preset="goe-external-switch-group",
                    charger_backend="goe_charger",
                    switch_group_supported_phase_selections="P1,P1_P2_P3",
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            switch_group = (config_path.parent / "wizard-switch-group.ini").read_text(encoding="utf-8")
            self.assertIn("SupportedPhaseSelections=P1,P1_P2_P3\n", switch_group)
            self.assertTrue(any("1P -> 3P" in warning for warning in result.warnings))
            self.assertTrue(any("shared primary endpoint" in warning for warning in result.warnings))

    def test_main_resume_last_uses_previous_wizard_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            configure_wallbox(
                WizardAnswers(
                    profile="native-charger",
                    host_input="goe.local",
                    meter_host_input=None,
                    switch_host_input=None,
                    charger_host_input="goe.local",
                    device_instance=73,
                    phase="3P",
                    policy_mode="auto",
                    digest_auth=False,
                    username="wizard",
                    password="secret-value",
                    charger_backend="goe_charger",
                    request_timeout_seconds=3.5,
                ),
                config_path=config_path,
                template_path=default_template_path(),
                imported_from=None,
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = main(
                    [
                        "--non-interactive",
                        "--dry-run",
                        "--json",
                        "--config-path",
                        str(config_path),
                        "--resume-last",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertTrue(payload["imported_from"].endswith(".wizard-result.json"))
            self.assertEqual(payload["profile"], "native-charger")
            self.assertEqual(payload["charger_backend"], "goe_charger")
            self.assertEqual(payload["answer_defaults"]["password_present"], False)

    def test_main_probe_role_limits_live_check_to_selected_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.ini"
            stdout = io.StringIO()
            with (
                patch(
                    "shelly_wallbox.bootstrap.wizard._live_check_rendered_setup",
                    return_value={
                        "ok": True,
                        "checked_roles": ("charger",),
                        "roles": {
                            "meter": {"status": "skipped", "reason": "not requested"},
                            "switch": {"status": "skipped", "reason": "not requested"},
                            "charger": {"status": "ok", "payload": {"type": "goe_charger"}},
                        },
                    },
                ),
                redirect_stdout(stdout),
            ):
                rc = main(
                    [
                        "--non-interactive",
                        "--dry-run",
                        "--json",
                        "--config-path",
                        str(config_path),
                        "--profile",
                        "native-charger",
                        "--charger-backend",
                        "goe_charger",
                        "--host",
                        "goe.local",
                        "--probe-role",
                        "charger",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["live_check"]["checked_roles"], ["charger"])
            self.assertEqual(payload["live_check"]["roles"]["meter"]["status"], "skipped")
            self.assertEqual(payload["live_check"]["roles"]["charger"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
