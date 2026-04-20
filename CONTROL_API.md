# Local Control API

The service exposes one small local HTTP API on top of the canonical
`Control API v1`.

This API is intentionally:

- local-first
- command-oriented
- versioned
- transport-thin

It does not introduce a second control model. The HTTP adapter builds the same
canonical `ControlCommand` objects used by DBus writes and returns the same
structured `ControlResult` semantics.

## Enable The API

Set these values in `deploy/venus/config.venus_evcharger.ini`:

```ini
ControlApiEnabled=1
ControlApiHost=127.0.0.1
ControlApiPort=8765
ControlApiAuthToken=
ControlApiReadToken=
ControlApiControlToken=
ControlApiLocalhostOnly=1
ControlApiUnixSocketPath=
```

Recommended defaults:

- keep `ControlApiLocalhostOnly=1`
- keep `ControlApiHost=127.0.0.1` unless you intentionally front it with another local proxy
- use `ControlApiReadToken` for read-only clients and `ControlApiControlToken` for writers
- use `ControlApiAuthToken` only when one shared token for both scopes is enough
- set `ControlApiUnixSocketPath` when a process-local unix socket is a better fit than TCP

## Machine-Readable Contract

- `GET /v1/openapi.json` returns the OpenAPI `3.1.0` document
- OpenAPI `3.1` component schemas are JSON Schema-compatible and are the normative machine-readable contract

## Endpoints

### Public endpoints

- `GET /v1/control/health`
- `GET /v1/openapi.json`

### Authenticated read endpoints

- `GET /v1/capabilities`
- `GET /v1/events`
- all `GET /v1/state/*`

### Authenticated control endpoint

- `POST /v1/control/command`

## Auth Contract

Header contract:

- `Authorization: Bearer <token>`

Scope rules:

- `ControlApiReadToken` grants read access
- `ControlApiControlToken` grants read and control access
- `ControlApiAuthToken` acts as one shared fallback token for both scopes when the scoped tokens are unset

Locality rules:

- when `ControlApiLocalhostOnly=1`, remote TCP clients are rejected
- unix-socket mode is always treated as local

## `GET /v1/capabilities`

Returns the stable capability view for the running setup, including:

- supported command names
- available command sources
- available modes
- supported phase selections
- backend topology
- auth behavior
- endpoint stability information

Notable fields:

- `command_names`
- `available_modes`
- `supported_phase_selections`
- `features`
- `topology`
- `versioning.stable_endpoints`
- `versioning.experimental_endpoints`

## `POST /v1/control/command`

Accepts one JSON object and returns one structured command/result payload.

Supported request styles:

1. Canonical command form

```json
{
  "name": "set_mode",
  "value": 1
}
```

2. Path/value form

```json
{
  "path": "/Mode",
  "value": 1
}
```

For runtime-setting commands, an explicit path is required:

```json
{
  "name": "set_auto_runtime_setting",
  "path": "/Auto/StartSurplusWatts",
  "value": 1700
}
```

### Request fields

- `name`
- `path`
- `value`
- `detail`
- `command_id`
- `idempotency_key`

### Tracking headers

- `X-Command-Id: <client command id>`
- `Idempotency-Key: <stable retry key>`

The HTTP adapter will generate a `command_id` when none is supplied.

## Response contract

Every command response uses this stable outer shape:

- `ok`
- `detail`
- `replayed`
- `command`
- `result`
- `error`

`command` includes:

- `name`
- `path`
- `value`
- `source`
- `detail`
- `command_id`
- `idempotency_key`

`result` includes:

- `command`
- `status`
- `accepted`
- `applied`
- `persisted`
- `reversible_failure`
- `external_side_effect_started`
- `detail`

`error` includes:

- `code`
- `message`
- `retryable`
- `details`

Stable `status` values:

- `applied`
- `accepted_in_flight`
- `rejected`

Stable error codes include:

- `invalid_json`
- `invalid_payload`
- `unauthorized`
- `insufficient_scope`
- `forbidden_remote_client`
- `command_rejected`
- `idempotency_conflict`
- `not_found`

HTTP status mapping:

- `200 OK` for `applied`
- `202 Accepted` for `accepted_in_flight`
- `409 Conflict` for rejected commands or idempotency conflicts
- `400 Bad Request` for malformed JSON or invalid command payloads
- `401 Unauthorized` for missing/invalid tokens
- `403 Forbidden` for wrong scope or rejected remote clients

## Idempotency and command tracking

For safe retries:

- provide one stable `Idempotency-Key`
- optionally provide your own `X-Command-Id`

Behavior:

- same `Idempotency-Key` + same payload returns the cached response with `replayed=true`
- same `Idempotency-Key` + different payload returns `409` with `code=idempotency_conflict`

## Event stream

`GET /v1/events` returns `application/x-ndjson`.

Each line is one event object with:

- `seq`
- `api_version`
- `kind`
- `timestamp`
- `payload`

Useful query parameters:

- `limit`
- `after`
- `timeout`
- `once`

Typical examples:

```bash
curl -s \
  -H 'Authorization: Bearer READ-TOKEN' \
  'http://127.0.0.1:8765/v1/events?once=1'
```

```bash
curl -N \
  -H 'Authorization: Bearer READ-TOKEN' \
  'http://127.0.0.1:8765/v1/events?limit=10&timeout=30'
```

## Curl examples

Read capabilities:

```bash
curl -s \
  -H 'Authorization: Bearer READ-TOKEN' \
  http://127.0.0.1:8765/v1/capabilities
```

Execute a command:

```bash
curl -s \
  -H 'Authorization: Bearer CONTROL-TOKEN' \
  -H 'Idempotency-Key: mode-1' \
  -H 'Content-Type: application/json' \
  -d '{"name":"set_mode","value":1}' \
  http://127.0.0.1:8765/v1/control/command
```

Use a unix socket:

```bash
curl --unix-socket /run/venus-evcharger-control.sock \
  -H 'Authorization: Bearer CONTROL-TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"name":"set_mode","value":1}' \
  http://localhost/v1/control/command
```

## Versioning

The formal versioning rules are documented in [API_VERSIONING.md](API_VERSIONING.md).
