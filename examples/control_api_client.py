# SPDX-License-Identifier: GPL-3.0-or-later
"""Small stdlib example for the local Control and State API."""

from __future__ import annotations

import json
import os

from venus_evcharger.control.client import LocalControlApiClient


def main() -> int:
    client = LocalControlApiClient(
        base_url=os.environ.get("VENUS_EVCHARGER_API_URL", "http://127.0.0.1:8765"),
        unix_socket_path=os.environ.get("VENUS_EVCHARGER_API_UNIX_SOCKET", ""),
        bearer_token=os.environ.get("VENUS_EVCHARGER_API_TOKEN", ""),
    )

    summary = client.state("summary").json()
    print("summary:")
    print(json.dumps(summary, indent=2, sort_keys=True))

    state_token = client.state("health").headers.get("X-State-Token", "")
    command = client.command(
        {"name": "set_mode", "value": 1},
        command_id="example-set-mode",
        idempotency_key="example-set-mode-1",
        if_match=state_token,
    ).json()
    print("\ncommand:")
    print(json.dumps(command, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
