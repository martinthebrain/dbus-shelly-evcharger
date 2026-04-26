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
        from venus_evcharger.bootstrap import wizard_render
        from venus_evcharger.bootstrap import wizard_runtime

        with self.assertRaisesRegex(ValueError, "missing required key"):
            wizard._replace_assignment("Host=foo\n", "DeviceInstance", "60")
        self.assertEqual(wizard._append_backends("[Backends]\nX=1\n\n[Other]\nA=1\n", []), "[Other]\nA=1\n")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            main_path = temp_path / "config.ini"
            main_path.write_text("[DEFAULT]\nHost=demo\n", encoding="utf-8")

            runtime = SimpleNamespace(
                meter_config_path=Path("meter.ini"),
                switch_config_path=None,
                charger_config_path=Path("charger.ini"),
                backend_mode="split",
            )
            (temp_path / "meter.ini").write_text("meter", encoding="utf-8")
            (temp_path / "charger.ini").write_text("charger", encoding="utf-8")
            with (
                patch(
                    "venus_evcharger.bootstrap.wizard.build_service_backends",
                    return_value=SimpleNamespace(runtime=runtime, meter=None, switch=None, charger=None),
                ),
                patch("venus_evcharger.bootstrap.wizard.probe_meter_backend", return_value={"type": "meter"}) as meter_probe,
                patch("venus_evcharger.bootstrap.wizard.read_charger_backend", side_effect=RuntimeError("boom")),
            ):
                payload = wizard._live_connectivity_payload(main_path, ("meter", "charger"))

            self.assertFalse(payload["ok"])
            self.assertEqual(payload["roles"]["switch"]["status"], "skipped")
            self.assertEqual(payload["roles"]["charger"]["status"], "error")
            meter_probe.assert_called_once_with(str(temp_path / "meter.ini"))

            with (
                patch(
                    "venus_evcharger.bootstrap.wizard.build_service_backends",
                    return_value=SimpleNamespace(runtime=runtime, meter=None, switch=None, charger=None),
                ),
                patch("venus_evcharger.bootstrap.wizard.probe_meter_backend", return_value={"type": "meter"}),
                patch("venus_evcharger.bootstrap.wizard.probe_switch_backend", return_value={"type": "switch"}),
                patch("venus_evcharger.bootstrap.wizard.read_charger_backend", return_value={"type": "charger"}),
            ):
                skipped_payload = wizard._live_connectivity_payload(main_path, None)
            self.assertEqual(skipped_payload["roles"]["switch"]["status"], "skipped")
            self.assertEqual(skipped_payload["roles"]["switch"]["reason"], "not configured")

            combined_runtime = SimpleNamespace(
                backend_mode="combined",
                meter_config_path=None,
                switch_config_path=None,
                charger_config_path=None,
            )
            combined_backends = SimpleNamespace(
                runtime=combined_runtime,
                meter=SimpleNamespace(read_meter=lambda: {"power_w": 1234.0}),
                switch=SimpleNamespace(
                    capabilities=lambda: {"switching_mode": "direct"},
                    read_switch_state=lambda: {"enabled": True},
                ),
                charger=None,
            )
            with (
                patch("venus_evcharger.bootstrap.wizard.build_service_backends", return_value=combined_backends),
            ):
                combined_payload = wizard._live_connectivity_payload(main_path, None)

            self.assertTrue(combined_payload["ok"])
            self.assertEqual(combined_payload["roles"]["meter"]["status"], "ok")
            self.assertEqual(combined_payload["roles"]["meter"]["payload"]["type"], "shelly_meter")
            self.assertEqual(combined_payload["roles"]["switch"]["status"], "ok")
            self.assertEqual(combined_payload["roles"]["switch"]["payload"]["switch_state"]["enabled"], True)
            self.assertEqual(combined_payload["roles"]["charger"]["reason"], "not configured")

            combined_backends_missing_meter = SimpleNamespace(
                runtime=combined_runtime,
                meter=None,
                switch=combined_backends.switch,
                charger=None,
            )
            with patch("venus_evcharger.bootstrap.wizard.build_service_backends", return_value=combined_backends_missing_meter):
                missing_combined_payload = wizard._live_connectivity_payload(main_path, ("meter",))
            self.assertEqual(missing_combined_payload["roles"]["meter"]["reason"], "not configured")

            combined_backends_charger = SimpleNamespace(
                runtime=combined_runtime,
                meter=combined_backends.meter,
                switch=combined_backends.switch,
                charger=SimpleNamespace(read_charger_state=lambda: {"online": True}),
            )
            combined_role = wizard_runtime._combined_role_payload(
                "charger",
                combined_backends_charger.charger,
                main_path,
                "goe_charger",
            )
            self.assertEqual(combined_role["charger_state"]["online"], True)

            error_switch_backends = SimpleNamespace(
                runtime=combined_runtime,
                meter=combined_backends.meter,
                switch=SimpleNamespace(
                    capabilities=lambda: (_ for _ in ()).throw(RuntimeError("capabilities boom")),
                    read_switch_state=lambda: {"enabled": True},
                ),
                charger=None,
            )
            with patch("venus_evcharger.bootstrap.wizard.build_service_backends", return_value=error_switch_backends):
                error_payload = wizard._live_connectivity_payload(main_path, ("switch",))
            self.assertFalse(error_payload["ok"])
            self.assertEqual(error_payload["roles"]["switch"]["status"], "error")

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
            with patch(
                "venus_evcharger.bootstrap.wizard_runtime._live_connectivity_payload",
                side_effect=lambda path, roles: {"ok": path.exists() and (path.parent / "adapter.ini").exists(), "checked_roles": roles or (), "roles": {}},
            ):
                runtime_live_payload = wizard_runtime._live_check_rendered_setup(
                    "[DEFAULT]\nAdapter=adapter.ini\n",
                    {"adapter.ini": "[Adapter]\n"},
                    "config.ini",
                    ("meter",),
                )
            self.assertTrue(runtime_live_payload["ok"])
            with patch(
                "venus_evcharger.bootstrap.wizard_runtime._live_connectivity_payload_with_hooks",
                return_value={"ok": True, "checked_roles": ("meter",), "roles": {}},
            ) as runtime_hooks:
                direct_runtime_payload = wizard_runtime._live_connectivity_payload(main_path, ("meter",))
            self.assertTrue(direct_runtime_payload["ok"])
            runtime_hooks.assert_called_once()

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
            with (
                patch("venus_evcharger.bootstrap.wizard_runtime.prompt_yes_no", return_value=True),
                redirect_stdout(io.StringIO()),
            ):
                self.assertTrue(wizard_runtime._interactive_write_confirmed(preview, ("existing.ini",)))

            with patch("venus_evcharger.bootstrap.wizard.prompt_yes_no", return_value=True):
                self.assertTrue(wizard._resolve_live_check(_namespace()))
            self.assertFalse(wizard._resolve_live_check(_namespace(non_interactive=True)))
            self.assertTrue(wizard._skip_write_confirmation(_namespace(dry_run=True), ("existing.ini",)))
            self.assertTrue(wizard._skip_write_confirmation(_namespace(non_interactive=True, force=True), ("existing.ini",)))
            self.assertTrue(wizard._skip_write_confirmation(_namespace(yes=True), tuple()))
            self.assertFalse(wizard._skip_write_confirmation(_namespace(), tuple()))
            self.assertFalse(wizard_runtime._non_interactive_write_allowed(_namespace(), tuple()))
            with self.assertRaisesRegex(ValueError, "Refusing to overwrite existing files"):
                wizard_runtime._non_interactive_write_allowed(_namespace(non_interactive=True), ("existing.ini",))
            self.assertTrue(wizard_runtime._non_interactive_write_allowed(_namespace(non_interactive=True, force=True), ("existing.ini",)))
            self.assertTrue(wizard_runtime._skip_write_confirmation(_namespace(dry_run=True), ("existing.ini",)))
            self.assertTrue(wizard_runtime._skip_write_confirmation(_namespace(non_interactive=True, force=True), ("existing.ini",)))
            self.assertTrue(wizard_runtime._skip_write_confirmation(_namespace(yes=True), tuple()))
            self.assertFalse(wizard_runtime._skip_write_confirmation(_namespace(), tuple()))
            with (
                patch("venus_evcharger.bootstrap.wizard.prompt_yes_no", return_value=True),
                redirect_stdout(io.StringIO()),
            ):
                self.assertTrue(wizard._interactive_write_confirmed(preview, tuple()))
            with (
                patch("venus_evcharger.bootstrap.wizard_runtime.prompt_yes_no", return_value=True),
                redirect_stdout(io.StringIO()),
            ):
                self.assertTrue(wizard_runtime._interactive_write_confirmed(preview, tuple()))
            wizard._confirm_write(_namespace(dry_run=True), preview, ("existing.ini",))
            wizard_runtime._confirm_write(_namespace(dry_run=True), preview, ("existing.ini",))
            with patch("venus_evcharger.bootstrap.wizard_runtime._interactive_write_confirmed", return_value=True):
                wizard_runtime._confirm_write(_namespace(), preview, ("existing.ini",))
            with patch("venus_evcharger.bootstrap.wizard_runtime._interactive_write_confirmed", return_value=False):
                with self.assertRaisesRegex(ValueError, "cancelled"):
                    wizard_runtime._confirm_write(_namespace(), preview, ("existing.ini",))

            topology_answers = wizard.WizardAnswers(
                profile="multi_adapter_topology",
                host_input="adapter.local",
                meter_host_input=None,
                switch_host_input="switch.local",
                charger_host_input="charger.local",
                device_instance=60,
                phase="3P",
                policy_mode="manual",
                digest_auth=False,
                username="",
                password="",
                topology_preset="goe-external-switch-group",
                charger_backend="goe_charger",
                transport_kind="serial_rtu",
                transport_host="adapter.local",
                transport_port=502,
                transport_device="/dev/ttyUSB0",
                transport_unit_id=1,
            )
            topology_config = wizard_render.build_wizard_topology_config(
                topology_answers,
            )
            rendered_files = wizard_render.render_adapter_files_from_topology(
                topology_config,
                topology_answers,
                {"charger": "charger.local", "switch": "switch.local"},
            )
            self.assertIn("Type=goe_charger", rendered_files["wizard-charger.ini"])
            self.assertIn("BaseUrl=http://charger.local", rendered_files["wizard-charger.ini"])
            self.assertIn("Type=switch_group", rendered_files["wizard-switch-group.ini"])
            self.assertEqual(
                wizard_render.render_legacy_backends_from_topology(
                    topology_config,
                    rendered_files,
                ),
                [
                    "Mode=split",
                    "MeterType=none",
                    "SwitchType=switch_group",
                    "SwitchConfigPath=wizard-switch-group.ini",
                    "ChargerType=goe_charger",
                    "ChargerConfigPath=wizard-charger.ini",
                ],
            )

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as raised:
                with patch("sys.argv", ["wizard.py", "--non-interactive", "--dry-run", "--json", "--profile", "simple_relay", "--host", "192.168.1.44"]):
                    runpy.run_module("venus_evcharger.bootstrap.wizard", run_name="__main__")
        self.assertEqual(raised.exception.code, 0)
        self.assertIn('"profile": "simple_relay"', stdout.getvalue())

        error_stdout = io.StringIO()
        with redirect_stdout(error_stdout):
            rc = wizard.main(["--non-interactive", "--json"])
        self.assertEqual(rc, 2)
        self.assertIn('"error"', error_stdout.getvalue())
