# API Operator Guide

This page is the short operational checklist for the local HTTP Control API and
its CLI on Venus OS / Cerbo GX.

Use it when you want quick answers to:

- is the API alive at all?
- which commands and state endpoints does this setup support?
- is auth working?
- can I write one safe command with `If-Match`?
- how do I use the unix socket locally?
- how do I tail the event stream?

## Which CLI entrypoint should I use?

There are two supported operator entrypoints:

- repository-local:
  `python3 ./venus_evchargerctl.py ...`
- target-system wrapper after install:
  `./deploy/venus/venus_evchargerctl.sh ...`

Recommended practice:

- use `venus_evchargerctl.py` while developing or testing inside the repo
- use `deploy/venus/venus_evchargerctl.sh` on the installed GX target

Both entrypoints talk to the same local API contract.

## CLI exit codes

The CLI uses a deliberately small exit-code contract:

- `0`: request succeeded and the API returned a `2xx` response
- `1`: request reached the API but was rejected or failed, for example `401`,
  `403`, `409`, or `429`
- `2`: CLI usage error, such as invalid arguments or an unknown subcommand

This makes it easy to use the CLI from shell scripts and local automation.

## Quick health checks

Read API liveness without a token:

```bash
./deploy/venus/venus_evchargerctl.sh health
```

Expected shape:

- `ok: true`
- local bind metadata such as host, port, or unix socket path

If that fails:

- check whether the service is running:
  `svstat /service/dbus-venus-evcharger`
- check the wallbox main process:
  `pgrep -af venus_evcharger_service.py`

## Read capabilities

This is the best first authenticated read:

```bash
./deploy/venus/venus_evchargerctl.sh --token READ-TOKEN capabilities
```

Look at:

- `command_names`
- `supported_phase_selections`
- `features`
- `topology`
- `versioning`

If this fails with `401` or `403`:

- verify the token
- verify whether you are using the read token or a stronger token
- verify that `ControlApiLocalhostOnly=1` is not rejecting your TCP client path

## Read normalized state

Typical reads:

```bash
./deploy/venus/venus_evchargerctl.sh --token READ-TOKEN state summary
./deploy/venus/venus_evchargerctl.sh --token READ-TOKEN state operational
./deploy/venus/venus_evchargerctl.sh --token READ-TOKEN state topology
./deploy/venus/venus_evchargerctl.sh --token READ-TOKEN state health
```

Use these first before digging into lower-level DBus details.

## Safe write with optimistic concurrency

Read the current state token:

```bash
STATE_TOKEN="$(
  ./deploy/venus/venus_evchargerctl.sh --token READ-TOKEN state health \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state", {}).get("state_token", ""))'
)"
```

Send a command only if local state did not move in between:

```bash
./deploy/venus/venus_evchargerctl.sh \
  --token CONTROL-TOKEN \
  command set-mode 1 \
  --if-match "$STATE_TOKEN"
```

If the API returns `409`, re-read state and retry intentionally.

## Unix socket usage

When the target is configured with `ControlApiUnixSocketPath`, prefer it for
strictly local automation:

```bash
./deploy/venus/venus_evchargerctl.sh \
  --unix-socket /run/venus-evcharger-control.sock \
  --token READ-TOKEN \
  state summary
```

For direct HTTP debugging:

```bash
curl --unix-socket /run/venus-evcharger-control.sock \
  -H 'Authorization: Bearer READ-TOKEN' \
  http://localhost/v1/capabilities
```

## Event stream

Read one immediate snapshot of recent events:

```bash
./deploy/venus/venus_evchargerctl.sh \
  --token READ-TOKEN \
  events --once
```

Read only command events:

```bash
./deploy/venus/venus_evchargerctl.sh \
  --token READ-TOKEN \
  events --kind command --once
```

Follow the stream over the unix socket:

```bash
./deploy/venus/venus_evchargerctl.sh \
  --unix-socket /run/venus-evcharger-control.sock \
  --token READ-TOKEN \
  --compact \
  events --kind state --heartbeat 1.0
```

## Common failure patterns

### `401 unauthorized`

- wrong token
- missing `Authorization: Bearer <token>`

### `403 forbidden_remote_client`

- `ControlApiLocalhostOnly=1`
- TCP client did not appear local enough for policy
- prefer the unix socket if possible

### `403 insufficient_scope`

- read token used for a control command
- control token used for an admin or update command

### `409 conflict`

- stale `If-Match` / state token
- re-read current state and retry intentionally

### `409 command_rejected`

- command was structurally valid but not accepted in current runtime context
- inspect `error.code`, `detail`, and state payloads

### `429 rate_limited` or `429 cooldown_active`

- local client is sending commands too quickly
- slow down retries and repeated writes

## Best next diagnostics

If a write behaves unexpectedly, inspect in this order:

1. `health`
2. `capabilities`
3. `state health`
4. `state operational`
5. `state topology`
6. `events --kind command --once`

## Related docs

- [CONTROL_API.md](CONTROL_API.md)
- [STATE_API.md](STATE_API.md)
- [API_OVERVIEW.md](API_OVERVIEW.md)
- [API_VERSIONING.md](API_VERSIONING.md)
- [INSTALL.md](INSTALL.md)
