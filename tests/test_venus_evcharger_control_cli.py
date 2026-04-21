# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
import runpy
import sys
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from venus_evcharger.control import cli
from venus_evcharger.control.client import ControlApiClientResponse

from tests.venus_evcharger_control_test_support import started_control_api_server


class _FakeClientForCliMainGuard:
    def __init__(self, *args: object, **kwargs: object) -> None:
        return None

    def state(self, name: str) -> ControlApiClientResponse:
        return ControlApiClientResponse(status=200, headers={}, body=json.dumps({"kind": name, "ok": True}))


class TestVenusEvchargerControlCli(unittest.TestCase):
    def test_parse_cli_value_covers_common_scalar_shapes(self) -> None:
        self.assertTrue(cli._parse_cli_value("true"))
        self.assertFalse(cli._parse_cli_value("off"))
        self.assertEqual(cli._parse_cli_value("12"), 12)
        self.assertEqual(cli._parse_cli_value("12.5"), 12.5)
        self.assertEqual(cli._parse_cli_value('"hello"'), "hello")
        self.assertEqual(cli._parse_cli_value("raw-text"), "raw-text")

    def test_command_payload_and_compact_helpers_cover_optional_branches(self) -> None:
        namespace = SimpleNamespace(name="set-current-setting", value="12.5", path="/SetCurrent", detail="manual")
        payload = cli._command_payload(namespace)
        self.assertEqual(
            payload,
            {"name": "set_current_setting", "path": "/SetCurrent", "value": 12.5, "detail": "manual"},
        )

        compact_stdout = io.StringIO()
        with redirect_stdout(compact_stdout):
            cli._write_json({"ok": True}, compact=True)
        self.assertEqual(compact_stdout.getvalue(), '{"ok":true}\n')

        stream_stdout = io.StringIO()
        with redirect_stdout(stream_stdout):
            rc = cli._write_event_response(
                ControlApiClientResponse(status=200, headers={}, body='{"kind":"command"}'),
                compact=True,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(stream_stdout.getvalue(), '{"kind":"command"}\n')

        empty_stdout = io.StringIO()
        with redirect_stdout(empty_stdout):
            cli._write_stream_body("")
        self.assertEqual(empty_stdout.getvalue(), "")

    def test_run_capabilities_and_unknown_dispatch_paths_are_covered(self) -> None:
        with patch.object(cli, "_client") as client_factory:
            client_factory.return_value.capabilities.return_value = ControlApiClientResponse(
                status=200,
                headers={},
                body='{"ok":true,"kind":"capabilities"}',
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                rc = cli._run_capabilities(SimpleNamespace(compact=False))
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(stdout.getvalue())["kind"], "capabilities")

        fake_parser = SimpleNamespace(parse_args=lambda _argv=None: SimpleNamespace(subcommand="unknown"))
        with patch.object(cli, "build_parser", return_value=fake_parser):
            with self.assertRaises(SystemExit):
                cli.main([])

    def test_cli_module_main_guard_runs(self) -> None:
        stdout = io.StringIO()
        argv = sys.argv[:]
        sys.argv = ["venus_evcharger/control/cli.py", "--token", "read-token", "state", "summary"]
        try:
            with (
                patch("venus_evcharger.control.client.LocalControlApiClient", _FakeClientForCliMainGuard),
                redirect_stdout(stdout),
                self.assertRaises(SystemExit) as exit_info,
            ):
                runpy.run_path("venus_evcharger/control/cli.py", run_name="__main__")
        finally:
            sys.argv = argv
        self.assertEqual(exit_info.exception.code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["kind"], "summary")

    def test_state_command_and_events_work_against_live_server(self) -> None:
        with started_control_api_server() as (_service, server):
            base_args = [
                "--url",
                f"http://{server.bound_host}:{server.bound_port}",
            ]

            state_stdout = io.StringIO()
            with redirect_stdout(state_stdout):
                rc = cli.main([*base_args, "--token", "read-token", "state", "summary"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(state_stdout.getvalue())["kind"], "summary")

            command_stdout = io.StringIO()
            with redirect_stdout(command_stdout):
                rc = cli.main([*base_args, "--token", "control-token", "command", "set-mode", "1"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(command_stdout.getvalue())["result"]["status"], "applied")

            events_stdout = io.StringIO()
            with redirect_stdout(events_stdout):
                rc = cli.main(
                    [
                        *base_args,
                        "--token",
                        "read-token",
                        "events",
                        "--kind",
                        "command",
                        "--once",
                    ]
                )
            self.assertEqual(rc, 0)
            events = json.loads(events_stdout.getvalue())
            self.assertTrue(events)
            self.assertEqual(events[-1]["kind"], "command")


if __name__ == "__main__":
    unittest.main()
