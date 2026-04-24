# SPDX-License-Identifier: GPL-3.0-or-later
from tests.wizard_branch_runtime_cases_common import (
    Path,
    SimpleNamespace,
    _namespace,
    _result,
    io,
    patch,
    redirect_stdout,
    runpy,
    tempfile,
    wizard,
)


class _WizardBranchRuntimeCoreCases:
    def test_wizard_core_helpers_cover_live_checks_write_paths_and_main_guard(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing required key"):
            wizard._replace_assignment("Host=foo\n", "DeviceInstance", "60")
        self.assertEqual(wizard._append_backends("[Backends]\nX=1\n\n[Other]\nA=1\n", []), "[Other]\nA=1\n")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            main_path = temp_path / "config.ini"
            main_path.write_text("[DEFAULT]\nHost=demo\n", encoding="utf-8")

            selection = SimpleNamespace(
                meter_config_path=Path("meter.ini"),
                switch_config_path=None,
                charger_config_path=Path("charger.ini"),
            )
            (temp_path / "meter.ini").write_text("meter", encoding="utf-8")
            (temp_path / "charger.ini").write_text("charger", encoding="utf-8")
            with (
                patch("venus_evcharger.bootstrap.wizard.load_backend_selection", return_value=selection),
                patch("venus_evcharger.bootstrap.wizard.probe_meter_backend", return_value={"type": "meter"}) as meter_probe,
                patch("venus_evcharger.bootstrap.wizard.read_charger_backend", side_effect=RuntimeError("boom")),
            ):
                payload = wizard._live_connectivity_payload(main_path, ("meter", "charger"))

            self.assertFalse(payload["ok"])
            self.assertEqual(payload["roles"]["switch"]["status"], "skipped")
            self.assertEqual(payload["roles"]["charger"]["status"], "error")
            meter_probe.assert_called_once_with(str(temp_path / "meter.ini"))

            with (
                patch("venus_evcharger.bootstrap.wizard.load_backend_selection", return_value=selection),
                patch("venus_evcharger.bootstrap.wizard.probe_meter_backend", return_value={"type": "meter"}),
                patch("venus_evcharger.bootstrap.wizard.probe_switch_backend", return_value={"type": "switch"}),
                patch("venus_evcharger.bootstrap.wizard.read_charger_backend", return_value={"type": "charger"}),
            ):
                skipped_payload = wizard._live_connectivity_payload(main_path, None)
            self.assertEqual(skipped_payload["roles"]["switch"]["status"], "skipped")
            self.assertEqual(skipped_payload["roles"]["switch"]["reason"], "not configured")

            with patch(
                "venus_evcharger.bootstrap.wizard._live_connectivity_payload",
                side_effect=lambda path, roles: {"ok": path.exists() and (path.parent / "adapter.ini").exists(), "checked_roles": roles or (), "roles": {}},
            ):
                live_payload = wizard._live_check_rendered_setup(
                    "[DEFAULT]\nAdapter=adapter.ini\n",
                    {"adapter.ini": "[Adapter]\n"},
                    "config.ini",
                    ("meter",),
                )
            self.assertTrue(live_payload["ok"])

            config_path = temp_path / "written.ini"
            adapter_path = temp_path / "adapter.ini"
            config_path.write_text("old\n", encoding="utf-8")
            adapter_path.write_text("old\n", encoding="utf-8")
            backups = wizard._write_generated_files(config_path, "new\n", {"adapter.ini": "new\n"})
            self.assertEqual(len(backups), 2)

            self.assertFalse(wizard._non_interactive_write_allowed(_namespace(), tuple()))
            with self.assertRaisesRegex(ValueError, "Refusing to overwrite existing files"):
                wizard._non_interactive_write_allowed(_namespace(non_interactive=True), ("existing.ini",))
            self.assertTrue(wizard._non_interactive_write_allowed(_namespace(non_interactive=True, force=True), ("existing.ini",)))

            preview = _result()
            with patch("venus_evcharger.bootstrap.wizard._interactive_write_confirmed", return_value=False):
                with self.assertRaisesRegex(ValueError, "cancelled"):
                    wizard._confirm_write(_namespace(), preview, ("existing.ini",))
            with patch("venus_evcharger.bootstrap.wizard._interactive_write_confirmed", return_value=True):
                wizard._confirm_write(_namespace(), preview, ("existing.ini",))
            with (
                patch("venus_evcharger.bootstrap.wizard.prompt_yes_no", return_value=True),
                redirect_stdout(io.StringIO()),
            ):
                self.assertTrue(wizard._interactive_write_confirmed(preview, ("existing.ini",)))

            with patch("venus_evcharger.bootstrap.wizard.prompt_yes_no", return_value=True):
                self.assertTrue(wizard._resolve_live_check(_namespace()))
            self.assertFalse(wizard._resolve_live_check(_namespace(non_interactive=True)))

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as raised:
                with patch("sys.argv", ["wizard.py", "--non-interactive", "--dry-run", "--json", "--profile", "simple-relay", "--host", "192.168.1.44"]):
                    runpy.run_module("venus_evcharger.bootstrap.wizard", run_name="__main__")
        self.assertEqual(raised.exception.code, 0)
        self.assertIn('"profile": "simple-relay"', stdout.getvalue())

        error_stdout = io.StringIO()
        with redirect_stdout(error_stdout):
            rc = wizard.main(["--non-interactive", "--json"])
        self.assertEqual(rc, 2)
        self.assertIn('"error"', error_stdout.getvalue())
