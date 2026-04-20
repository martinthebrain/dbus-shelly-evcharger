# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import io
import json
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.bootstrap import wizard, wizard_adapters, wizard_cli, wizard_cli_output
from venus_evcharger.bootstrap.wizard_charger_presets import (
    apply_charger_preset_backend,
    charger_preset_backend,
    preset_transport_port,
    preset_transport_unit_id,
    relevant_charger_presets,
    render_charger_preset_config,
)
from venus_evcharger.bootstrap.wizard_guidance import (
    apply_split_preset_backend,
    compatibility_warnings,
    default_backend,
    probe_roles,
    prompt_role_hosts,
    relevant_role_hosts,
    resolved_primary_host,
    role_prompt_intro,
    role_prompt_label,
)
from venus_evcharger.bootstrap.wizard_import import (
    ImportedWizardDefaults,
    _adapter_path,
    _native_profile_defaults,
    _profile_defaults,
    _profile_defaults_from_types,
    _request_timeout_seconds,
    _switch_group_host_value,
)
from venus_evcharger.bootstrap.wizard_models import WizardResult
from venus_evcharger.bootstrap.wizard_persistence import _topology_summary_text
from venus_evcharger.bootstrap.wizard_policy_guidance import policy_defaults, prompt_policy_defaults
from venus_evcharger.bootstrap.wizard_split_layouts import split_topology_files
from venus_evcharger.bootstrap.wizard_support import (
    base_url_from_input,
    default_transport_kind,
    host_from_input,
    transport_summary,
)
from venus_evcharger.bootstrap.wizard_transport_guidance import (
    preset_specific_defaults,
    prompt_preset_specific_defaults,
    prompt_transport_inputs,
)
from venus_evcharger.core.common_schedule import scheduled_mode_snapshot


def _imported_defaults(**overrides: object) -> ImportedWizardDefaults:
    values = {
        "imported_from": "",
        "profile": None,
        "host_input": None,
        "meter_host_input": None,
        "switch_host_input": None,
        "charger_host_input": None,
        "device_instance": None,
        "phase": None,
        "policy_mode": None,
        "digest_auth": None,
        "username": None,
        "password": None,
        "split_preset": None,
        "charger_backend": None,
        "charger_preset": None,
        "request_timeout_seconds": None,
        "switch_group_phase_layout": None,
        "auto_start_surplus_watts": None,
        "auto_stop_surplus_watts": None,
        "auto_min_soc": None,
        "auto_resume_soc": None,
        "scheduled_enabled_days": None,
        "scheduled_latest_end_time": None,
        "scheduled_night_current_amps": None,
        "transport_kind": None,
        "transport_host": None,
        "transport_port": None,
        "transport_device": None,
        "transport_unit_id": None,
    }
    values.update(overrides)
    return ImportedWizardDefaults(**values)


def _namespace(**overrides: object) -> argparse.Namespace:
    values = {
        "profile": None,
        "split_preset": None,
        "charger_backend": None,
        "charger_preset": None,
        "host": None,
        "meter_host": None,
        "switch_host": None,
        "charger_host": None,
        "device_instance": None,
        "phase": None,
        "policy_mode": None,
        "transport": None,
        "transport_host": None,
        "transport_port": None,
        "transport_device": None,
        "transport_unit_id": None,
        "digest_auth": False,
        "username": None,
        "password": None,
        "import_config": None,
        "resume_last": False,
        "clone_current": False,
        "yes": False,
        "force": False,
        "dry_run": False,
        "json": False,
        "live_check": False,
        "probe_roles": None,
        "request_timeout_seconds": None,
        "switch_group_phase_layout": None,
        "auto_start_surplus_watts": None,
        "auto_stop_surplus_watts": None,
        "auto_min_soc": None,
        "auto_resume_soc": None,
        "scheduled_enabled_days": None,
        "scheduled_latest_end_time": None,
        "scheduled_night_current_amps": None,
        "non_interactive": False,
        "config_path": "/tmp/config.ini",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _result(**overrides: object) -> WizardResult:
    values = {
        "created_at": "2026-04-20T02:53:57",
        "config_path": "/tmp/config.ini",
        "imported_from": None,
        "profile": "simple-relay",
        "policy_mode": "manual",
        "split_preset": None,
        "charger_backend": None,
        "charger_preset": None,
        "transport_kind": None,
        "role_hosts": {},
        "validation": {"resolved_roles": {"meter": False}},
        "live_check": None,
        "generated_files": ("config.ini",),
        "backup_files": tuple(),
        "result_path": None,
        "audit_path": None,
        "topology_summary_path": None,
        "manual_review": ("Auth",),
        "dry_run": False,
        "warnings": tuple(),
        "answer_defaults": {},
    }
    values.update(overrides)
    return WizardResult(**values)


class TestShellyWallboxWizardBranchCoverage(unittest.TestCase):
    def test_result_text_covers_optional_sections_and_live_check_rendering(self) -> None:
        preview_text = wizard_cli_output.result_text(_result(dry_run=True))
        self.assertIn("Config preview for: /tmp/config.ini", preview_text)
        self.assertIn("Role hosts:\n  - none", preview_text)
        self.assertIn("Live connectivity: not run", preview_text)

        persisted_text = wizard_cli_output.result_text(
            _result(
                imported_from="/tmp/source.ini",
                split_preset="goe-external-switch-group",
                charger_backend="goe_charger",
                transport_kind="serial_rtu",
                role_hosts={"switch": "switch.local"},
                live_check={
                    "ok": False,
                    "roles": {
                        "switch": {"status": "error", "error": "boom"},
                        "charger": "ignored",
                    },
                },
                backup_files=("/tmp/config.ini.bak",),
                result_path="/tmp/config.ini.wizard-result.json",
                audit_path="/tmp/config.ini.wizard-audit.jsonl",
                topology_summary_path="/tmp/config.ini.wizard-topology.txt",
                warnings=("careful",),
            )
        )
        self.assertIn("Imported defaults: /tmp/source.ini", persisted_text)
        self.assertIn("Backup files:\n  - /tmp/config.ini.bak", persisted_text)
        self.assertIn("Warnings:\n  - careful", persisted_text)
        self.assertIn("Wizard result: /tmp/config.ini.wizard-result.json", persisted_text)
        self.assertIn("Live connectivity: check reported issues", persisted_text)
        self.assertIn("  - switch: error (boom)", persisted_text)
        self.assertNotIn("charger: ", persisted_text)

    def test_wizard_support_validates_hosts_and_transport_summary(self) -> None:
        self.assertEqual(host_from_input("http://charger.local/status"), "charger.local")
        self.assertEqual(base_url_from_input("charger.local/"), "http://charger.local")
        self.assertEqual(default_transport_kind("modbus_charger"), "tcp")
        self.assertEqual(transport_summary("modbus_charger", "tcp"), "tcp")
        self.assertIsNone(transport_summary("goe_charger", "serial_rtu"))
        with self.assertRaisesRegex(ValueError, "Invalid host input"):
            host_from_input("http://")
        with self.assertRaisesRegex(ValueError, "Host must not be empty"):
            host_from_input("   ")
        with self.assertRaisesRegex(ValueError, "Host must not be empty"):
            base_url_from_input("   ")

    def test_wizard_guidance_helpers_cover_role_defaults_and_warnings(self) -> None:
        imported = _imported_defaults(charger_backend="template_charger")
        namespace = _namespace(switch_host="switch.explicit")
        prompted: list[tuple[str, str]] = []

        def prompt_text(label: str, default: str) -> str:
            prompted.append((label, default))
            return f"prompted-{default}"

        meter_host, switch_host, charger_host = prompt_role_hosts(
            namespace,
            imported,
            "split-topology",
            "template-stack",
            "shared.local",
            prompt_text=prompt_text,
        )
        self.assertEqual(relevant_role_hosts("advanced-manual", None), ())
        self.assertEqual(relevant_role_hosts("native-charger-phase-switch", None), ("charger", "switch"))
        self.assertIn("separate adapter roles", role_prompt_intro("split-topology", "template-stack") or "")
        self.assertEqual(role_prompt_intro("native-charger", None), "This preset only needs the charger endpoint.")
        self.assertEqual(role_prompt_label("switch", "template-stack"), "Switch endpoint (host or full BaseUrl)")
        self.assertEqual(
            role_prompt_label("switch", "goe-external-switch-group"),
            "External phase-switch endpoint (host or full BaseUrl)",
        )
        self.assertEqual((meter_host, switch_host, charger_host), ("prompted-shared.local", "switch.explicit", "prompted-shared.local"))
        self.assertEqual(
            resolved_primary_host(_namespace(), _imported_defaults(), None, None, None),
            "192.168.1.50",
        )
        self.assertEqual(default_backend("native-charger", imported), "template_charger")
        self.assertEqual(apply_split_preset_backend("shelly-meter-goe", "template_charger"), "goe_charger")
        self.assertEqual(
            apply_split_preset_backend("shelly-meter-modbus-charger", "template_charger", "abb-terra-ac-modbus"),
            "modbus_charger",
        )
        self.assertEqual(charger_preset_backend("abb-terra-ac-modbus"), "modbus_charger")
        self.assertEqual(apply_charger_preset_backend("cfos-power-brain-modbus", "template_charger"), "modbus_charger")
        self.assertEqual(
            relevant_charger_presets("modbus_charger"),
            ("abb-terra-ac-modbus", "cfos-power-brain-modbus", "openwb-modbus-secondary"),
        )
        self.assertEqual(relevant_charger_presets("goe_charger"), ())
        self.assertEqual(
            compatibility_warnings(
                profile="split-topology",
                split_preset="goe-external-switch-group",
                charger_backend="goe_charger",
                primary_host_input="shared.local",
                role_hosts={"switch": "shared.local"},
                transport_kind="serial_rtu",
                transport_host="shared.local",
                switch_group_supported_phase_selections="P1,P1_P2_P3",
                charger_preset="cfos-power-brain-modbus",
            ),
            (
                "This switch_group preset is using the shared primary endpoint for the external phase switch; verify that the switch adapter is really colocated there.",
                "The go-e preset fell back to the shared primary endpoint; set an explicit charger endpoint if the charger lives elsewhere.",
                "The cFos preset only writes charging_enable, charging_cur_limit, and relay_select regularly; avoid adding periodic writes to other cFos registers because they persist to flash.",
                "The external phase switch is configured for 1P -> 3P switching only; make sure a 2-phase step is intentionally unavailable.",
            ),
        )
        self.assertIn(
            "Multiple topology roles resolve to the same shared endpoint",
            compatibility_warnings(
                profile="split-topology",
                split_preset="template-stack",
                charger_backend="modbus_charger",
                primary_host_input="shared.local",
                role_hosts={"meter": "shared.local", "switch": "shared.local"},
                transport_kind="tcp",
                transport_host="shared.local",
                switch_group_supported_phase_selections="P1,P1_P2,P1_P2_P3",
            )[0],
        )
        self.assertEqual(probe_roles(_namespace(probe_roles=["meter", "charger"])), ("meter", "charger"))
        self.assertIsNone(probe_roles(_namespace()))

    def test_transport_guidance_covers_serial_tcp_and_prompted_defaults(self) -> None:
        imported = _imported_defaults(transport_kind="tcp", transport_host="10.0.0.2", transport_port=1502, transport_unit_id=7)

        tcp_transport = prompt_transport_inputs(
            "modbus_charger",
            "abb-terra-ac-modbus",
            "10.0.0.1",
            imported,
            prompt_choice=lambda *_args: "tcp",
            prompt_text=lambda label, default: {"Modbus TCP host": "10.0.0.8", "Modbus TCP port": "2502", "Modbus unit id": "9"}[label],
        )
        serial_transport = prompt_transport_inputs(
            "simpleevse_charger",
            None,
            "serial.local",
            _imported_defaults(),
            prompt_choice=lambda *_args: "serial_rtu",
            prompt_text=lambda label, default: {"Serial device": "/dev/ttyUSB9", "Modbus unit id": "4"}[label],
        )
        self.assertEqual(tcp_transport, ("tcp", "10.0.0.8", 2502, "/dev/ttyUSB0", 9))
        self.assertEqual(serial_transport, ("serial_rtu", "serial.local", 502, "/dev/ttyUSB9", 4))
        self.assertEqual(preset_transport_port("cfos-power-brain-modbus", "tcp"), 4701)
        self.assertEqual(preset_transport_port("openwb-modbus-secondary", "tcp"), 1502)
        self.assertIsNone(preset_transport_port("cfos-power-brain-modbus", "serial_rtu"))
        self.assertEqual(preset_transport_unit_id("abb-terra-ac-modbus"), 1)

        timeout, phase_layout = preset_specific_defaults(
            _namespace(),
            _imported_defaults(),
            backend="template_charger",
            split_preset=None,
            charger_preset=None,
        )
        self.assertIsNone(timeout)
        self.assertEqual(phase_layout, "P1,P1_P2,P1_P2_P3")

        prompted_timeout, prompted_phase_layout = prompt_preset_specific_defaults(
            _namespace(),
            _imported_defaults(request_timeout_seconds=5.0),
            profile="split-topology",
            backend="goe_charger",
            split_preset="goe-external-switch-group",
            charger_preset=None,
            prompt_choice=lambda *_args: "P1,P1_P2_P3",
            prompt_text=lambda label, default: "6.5" if "timeout" in label else default,
        )
        self.assertEqual(prompted_timeout, 6.5)
        self.assertEqual(prompted_phase_layout, "P1,P1_P2_P3")

    def test_policy_guidance_covers_manual_and_scheduled_branches(self) -> None:
        namespace = _namespace(auto_start_surplus_watts=2000.0)
        manual_defaults = policy_defaults("manual", _imported_defaults(), namespace)
        self.assertEqual(manual_defaults, (2000.0, None, None, None, None, None, None))

        scheduled_defaults = policy_defaults(
            "scheduled",
            _imported_defaults(
                auto_start_surplus_watts=1900.0,
                auto_stop_surplus_watts=1400.0,
                auto_min_soc=31.0,
                auto_resume_soc=34.0,
                scheduled_enabled_days="Sat,Sun",
                scheduled_latest_end_time="08:00",
                scheduled_night_current_amps=6.0,
            ),
            _namespace(),
        )
        self.assertEqual(scheduled_defaults, (1900.0, 1400.0, 31.0, 34.0, "Sat,Sun", "08:00", 6.0))

        prompted_defaults = prompt_policy_defaults(
            "scheduled",
            _imported_defaults(),
            _namespace(),
            prompt_text=lambda label, default: {
                "Auto start surplus watts": "2100",
                "Auto stop surplus watts": "1500",
                "Battery minimum SOC for Auto": "35",
                "Battery resume SOC for Auto": "39",
                "Scheduled weekdays": "Mon,Wed",
                "Scheduled latest end time (HH:MM)": "07:45",
                "Scheduled fallback night current amps": "8",
            }[label],
        )
        self.assertEqual(prompted_defaults, (2100.0, 1500.0, 35.0, 39.0, "Mon,Wed", "07:45", 8.0))

    def test_import_helpers_cover_adapter_paths_profiles_and_timeouts(self) -> None:
        self.assertEqual(_profile_defaults(None), ("simple-relay", None, None))
        self.assertEqual(_profile_defaults_from_types("", "", ""), (None, None, None))
        self.assertEqual(_profile_defaults_from_types("template_meter", "custom_switch", "",), ("advanced-manual", None, None))
        self.assertEqual(_native_profile_defaults("none", "none", "goe_charger", "goe_charger"), ("native-charger", None, "goe_charger"))
        self.assertEqual(
            _native_profile_defaults("none", "switch_group", "simpleevse_charger", "simpleevse_charger"),
            ("native-charger-phase-switch", None, "simpleevse_charger"),
        )
        self.assertIsNone(_native_profile_defaults("shelly_meter", "none", "goe_charger", "goe_charger"))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "config.ini"
            config_path.write_text("[DEFAULT]\n", encoding="utf-8")
            adapter_path = temp_path / "switch-group.ini"
            adapter_path.write_text("[DEFAULT]\nBaseUrl=http://fallback.local\n", encoding="utf-8")
            self.assertIsNone(_adapter_path(config_path, None, "SwitchConfigPath"))
            self.assertIsNone(_adapter_path(config_path, {"SwitchConfigPath": "missing.ini"}, "SwitchConfigPath"))
            self.assertEqual(
                _switch_group_host_value(adapter_path),
                "http://fallback.local",
            )

            charger_path = temp_path / "charger.ini"
            charger_path.write_text("[Adapter]\nType=goe_charger\nRequestTimeoutSeconds=4.25\n", encoding="utf-8")
            parser = argparse.Namespace(get=lambda key, default=None: None)
            backends = SimpleNamespace(get=lambda key, default=None: "charger.ini" if key == "ChargerConfigPath" else default)
            self.assertEqual(_request_timeout_seconds(config_path, backends, "goe_charger"), 4.25)
            self.assertIsNone(_request_timeout_seconds(config_path, backends, "template_charger"))

    def test_persistence_and_split_layout_helpers_cover_optional_paths(self) -> None:
        summary = _topology_summary_text(
            {
                "config_path": "/tmp/config.ini",
                "profile": "simple-relay",
                "split_preset": None,
                "charger_backend": None,
                "charger_preset": None,
                "policy_mode": "manual",
                "transport_kind": None,
                "role_hosts": {},
                "validation": {"resolved_roles": {"meter": False}},
                "live_check": {"ok": True},
                "warnings": ["careful"],
            }
        )
        self.assertIn("resolved_roles: {'meter': False}", summary)
        self.assertIn("live_check_ok: True", summary)
        self.assertIn("warnings:\n  - careful", summary)

        backend_lines, files, role_hosts = split_topology_files(
            split_preset="shelly-io-template-charger",
            role_hosts={"meter": "meter.local", "switch": "switch.local", "charger": "charger.local"},
            meter_base_url="http://meter.local",
            switch_base_url="http://switch.local",
            charger_base_url="http://charger.local",
            charger_preset=None,
            request_timeout_seconds=None,
            switch_group_supported_phase_selections="P1,P1_P2,P1_P2_P3",
            transport_kind="serial_rtu",
            transport_host="charger.local",
            transport_port=502,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=1,
        )
        self.assertIn("ChargerType=template_charger", backend_lines)
        self.assertIn("Type=template_charger", files["wizard-charger.ini"])
        self.assertEqual(role_hosts["meter"], "meter.local")
        self.assertIn("Type=template_charger", wizard_adapters.native_charger_config(
            "template_charger",
            "http://charger.local",
            charger_preset=None,
            request_timeout_seconds=None,
            transport_kind="serial_rtu",
            transport_host="charger.local",
            transport_port=502,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=1,
        ))
        self.assertIn("Transport=serial_rtu", wizard_adapters.native_charger_config(
            "simpleevse_charger",
            "http://charger.local",
            charger_preset=None,
            request_timeout_seconds=None,
            transport_kind="serial_rtu",
            transport_host="charger.local",
            transport_port=502,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=1,
        ))
        abb_config = render_charger_preset_config(
            "abb-terra-ac-modbus",
            transport_kind="tcp",
            transport_host="abb.local",
            transport_port=502,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=1,
        )
        self.assertIn("Preset=abb-terra-ac-modbus", abb_config)
        self.assertIn("Address=16645", abb_config)
        cfos_config = render_charger_preset_config(
            "cfos-power-brain-modbus",
            transport_kind="tcp",
            transport_host="cfos.local",
            transport_port=4701,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=1,
        )
        self.assertIn("Preset=cfos-power-brain-modbus", cfos_config)
        self.assertIn("Map=P1:1,P1_P2_P3:0", cfos_config)
        openwb_config = render_charger_preset_config(
            "openwb-modbus-secondary",
            transport_kind="tcp",
            transport_host="openwb.local",
            transport_port=1502,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=1,
        )
        self.assertIn("Preset=openwb-modbus-secondary", openwb_config)
        self.assertIn("EnableUsesCurrentWrite=1", openwb_config)
        self.assertIn("Address=10171", openwb_config)
        self.assertIn(
            "Device=/dev/ttyUSB7",
            render_charger_preset_config(
                "openwb-modbus-secondary",
                transport_kind="serial_rtu",
                transport_host="unused.local",
                transport_port=1502,
                transport_device="/dev/ttyUSB7",
                transport_unit_id=3,
            ),
        )
        with self.assertRaisesRegex(ValueError, "Unsupported charger preset"):
            render_charger_preset_config(
                "invalid-preset",
                transport_kind="tcp",
                transport_host="invalid.local",
                transport_port=1234,
                transport_device="/dev/ttyUSB0",
                transport_unit_id=1,
            )
        self.assertIn("Type=modbus_charger", wizard_adapters.native_charger_config(
            "modbus_charger",
            "",
            charger_preset=None,
            request_timeout_seconds=None,
            transport_kind="tcp",
            transport_host="modbus.local",
            transport_port=502,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=1,
        ))
        self.assertIn("RequestTimeoutSeconds=3.5", wizard_adapters.native_charger_config(
            "smart_custom",
            "http://charger.local",
            charger_preset=None,
            request_timeout_seconds=3.5,
            transport_kind="serial_rtu",
            transport_host="charger.local",
            transport_port=502,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=1,
        ))

        modbus_backend_lines, modbus_files, _ = split_topology_files(
            split_preset="shelly-meter-modbus-charger",
            role_hosts={"meter": "meter.local"},
            meter_base_url="http://meter.local",
            switch_base_url="http://switch.local",
            charger_base_url="http://charger.local",
            charger_preset=None,
            request_timeout_seconds=None,
            switch_group_supported_phase_selections="P1,P1_P2,P1_P2_P3",
            transport_kind="tcp",
            transport_host="modbus.local",
            transport_port=502,
            transport_device="/dev/ttyUSB0",
            transport_unit_id=1,
        )
        self.assertIn("ChargerType=modbus_charger", modbus_backend_lines)
        self.assertIn("Profile=generic", modbus_files["wizard-charger.ini"])

if __name__ == "__main__":
    unittest.main()
