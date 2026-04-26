# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json


class _FakeHandler:
    def __init__(
        self,
        path: str,
        *,
        body: bytes = b"{}",
        authorization: str = "",
        client_host: str = "127.0.0.1",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.path = path
        self.headers = {
            "Content-Length": str(len(body)),
            "Authorization": authorization,
        }
        if headers:
            self.headers.update(headers)
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status_code: int | None = None
        self.response_headers: dict[str, str] = {}
        self.client_address = (client_host, 12345)

    def send_response(self, status: int) -> None:
        self.status_code = status

    def send_header(self, key: str, value: str) -> None:
        self.response_headers[key] = value

    def end_headers(self) -> None:
        return None

    def json_payload(self) -> dict:
        return json.loads(self.wfile.getvalue().decode("utf-8"))
