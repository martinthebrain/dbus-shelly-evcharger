# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import io
import os
import pathlib
import runpy
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import venus_evchargerctl
from venus_evcharger.control import cli


class _FakeClientResponse:
    def __init__(self, *, payload: dict, headers: dict[str, str] | None = None) -> None:
        self._payload = payload
        self.headers = headers or {}

    def json(self) -> dict:
        return dict(self._payload)


class _FakeClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.calls: list[tuple[str, object]] = []

    def state(self, name: str) -> _FakeClientResponse:
        self.calls.append(("state", name))
        if name == "health":
            return _FakeClientResponse(payload={"kind": "health"}, headers={"X-State-Token": "rev-1"})
        return _FakeClientResponse(payload={"kind": name, "ok": True})

    def command(self, payload: dict, **kwargs: object) -> _FakeClientResponse:
        self.calls.append(("command", payload))
        return _FakeClientResponse(payload={"ok": True, "result": {"status": "applied"}})


class TestVenusEvchargerControlExamples(unittest.TestCase):
    def test_entrypoint_wrapper_reexports_cli_main(self) -> None:
        self.assertIs(venus_evchargerctl.main, cli.main)

    def test_example_script_runs_with_fake_client(self) -> None:
        example_path = pathlib.Path("examples/control_api_client.py")
        spec = importlib.util.spec_from_file_location("control_api_client_example", example_path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)

        stdout = io.StringIO()
        with (
            patch.object(module, "LocalControlApiClient", _FakeClient),
            patch.dict(os.environ, {}, clear=False),
            redirect_stdout(stdout),
        ):
            rc = module.main()

        self.assertEqual(rc, 0)
        output = stdout.getvalue()
        self.assertIn("summary:", output)
        self.assertIn("command:", output)

    def test_example_script_main_guard_runs(self) -> None:
        with (
            patch("venus_evcharger.control.client.LocalControlApiClient", _FakeClient),
            self.assertRaises(SystemExit) as exit_info,
        ):
            runpy.run_path("examples/control_api_client.py", run_name="__main__")
        self.assertEqual(exit_info.exception.code, 0)

    def test_cli_entrypoint_main_guard_runs(self) -> None:
        with (
            patch("venus_evcharger.control.cli.main", return_value=0) as main_mock,
            self.assertRaises(SystemExit) as exit_info,
        ):
            runpy.run_path("venus_evchargerctl.py", run_name="__main__")
        self.assertEqual(exit_info.exception.code, 0)
        main_mock.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
