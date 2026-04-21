# SPDX-License-Identifier: GPL-3.0-or-later
import json
import unittest
from unittest.mock import MagicMock

from venus_evcharger.control.client import ControlApiClientResponse, LocalControlApiClient


class _FakeHttpResponse:
    def __init__(self, status: int, body: str, headers: list[tuple[str, str]] | None = None) -> None:
        self.status = status
        self._body = body.encode("utf-8")
        self._headers = headers or []

    def read(self) -> bytes:
        return self._body

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self._headers)


class TestVenusEvchargerControlClient(unittest.TestCase):
    def test_json_and_ndjson_helpers_cover_success_and_failure_paths(self) -> None:
        response = ControlApiClientResponse(status=200, headers={}, body='{"ok":true}')
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(
            ControlApiClientResponse(status=200, headers={}, body='{"a":1}\n\n{"b":2}\n').ndjson(),
            [{"a": 1}, {"b": 2}],
        )

        with self.assertRaises(ValueError):
            ControlApiClientResponse(status=200, headers={}, body='["not-an-object"]').json()

        with self.assertRaises(ValueError):
            ControlApiClientResponse(status=200, headers={}, body='["not-an-object"]\n').ndjson()

    def test_request_target_and_headers_cover_query_and_auth_branches(self) -> None:
        client = LocalControlApiClient(base_url="http://127.0.0.1:8765", bearer_token="token")

        self.assertEqual(
            client._request_target("/v1/events", query={"kind": ["command", "state"], "once": 1}),
            "/v1/events?kind=command&kind=state&once=1",
        )
        self.assertEqual(
            client._request_headers({"X-Test": "1"}, json_payload={"ok": True}),
            {
                "Accept": "application/json",
                "Authorization": "Bearer token",
                "Content-Type": "application/json",
                "X-Test": "1",
            },
        )
        self.assertEqual(LocalControlApiClient._request_body({"ok": True}), '{"ok":true}')
        self.assertIsNone(LocalControlApiClient._request_body(None))

    def test_state_passthrough_and_headers_without_bearer_token(self) -> None:
        client = LocalControlApiClient(base_url="http://127.0.0.1:8765", bearer_token="")
        client.get = MagicMock(return_value=ControlApiClientResponse(status=200, headers={}, body='{"ok":true}'))  # type: ignore[method-assign]

        response = client.state("/v1/state/runtime")

        self.assertEqual(response.status, 200)
        client.get.assert_called_once_with("/v1/state/runtime", headers=None)
        self.assertEqual(client._request_headers(None, json_payload=None), {"Accept": "application/json"})

    def test_health_and_openapi_delegate_to_get(self) -> None:
        client = LocalControlApiClient(base_url="http://127.0.0.1:8765", bearer_token="")
        client.get = MagicMock(return_value=ControlApiClientResponse(status=200, headers={}, body='{"ok":true}'))  # type: ignore[method-assign]

        health_response = client.health()
        openapi_response = client.openapi()

        self.assertEqual(health_response.status, 200)
        self.assertEqual(openapi_response.status, 200)
        self.assertEqual(
            client.get.call_args_list,
            [
                unittest.mock.call("/v1/control/health", headers=None),
                unittest.mock.call("/v1/openapi.json", headers=None),
            ],
        )

    def test_request_uses_connection_and_returns_normalized_response(self) -> None:
        client = LocalControlApiClient(base_url="http://127.0.0.1:8765", bearer_token="token")
        fake_connection = MagicMock()
        fake_connection.getresponse.return_value = _FakeHttpResponse(
            200,
            json.dumps({"ok": True}),
            headers=[("X-State-Token", "rev-1")],
        )
        client._connection = MagicMock(return_value=fake_connection)  # type: ignore[method-assign]

        response = client.command(
            {"name": "set_mode", "value": 1},
            idempotency_key="idem-1",
            command_id="cmd-1",
            if_match="rev-0",
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["X-State-Token"], "rev-1")
        fake_connection.request.assert_called_once_with(
            "POST",
            "/v1/control/command",
            body='{"name":"set_mode","value":1}',
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer token",
                "Content-Type": "application/json",
                "Idempotency-Key": "idem-1",
                "X-Command-Id": "cmd-1",
                "If-Match": "rev-0",
            },
        )
        fake_connection.close.assert_called_once_with()

    def test_connection_chooses_unix_socket_when_configured(self) -> None:
        client = LocalControlApiClient(unix_socket_path="/tmp/control.sock")

        connection = client._connection()

        self.assertEqual(connection._unix_socket_path, "/tmp/control.sock")  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
