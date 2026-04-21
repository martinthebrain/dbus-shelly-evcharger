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
ControlApiAdminToken=
ControlApiUpdateToken=
ControlApiLocalhostOnly=1
ControlApiUnixSocketPath=
ControlApiAuditPath=/run/dbus-venus-evcharger-control-audit-60.jsonl
ControlApiIdempotencyPath=/run/dbus-venus-evcharger-idempotency-60.json
ControlApiRateLimitMaxRequests=30
ControlApiRateLimitWindowSeconds=5
ControlApiCriticalCooldownSeconds=2
```

Recommended defaults:

- keep `ControlApiLocalhostOnly=1`
- keep `ControlApiHost=127.0.0.1` unless you intentionally front it with another local proxy
- use `ControlApiReadToken` for read-only clients and `ControlApiControlToken` for writers
- use `ControlApiAdminToken` and `ControlApiUpdateToken` only when you want stricter local separation for advanced control and update operations
- use `ControlApiAuthToken` only when one shared token for both scopes is enough
- prefer `ControlApiUnixSocketPath` when a process-local unix socket is a better fit than TCP
- keep API audit and idempotency paths on `/run/...` or `/tmp/...`
- use the rate-limit settings as a local abuse guard, not as a remote multi-tenant quota system
- treat the unix socket as the preferred local automation transport when your client supports it

## Runtime-Only Policy

The API is intentionally designed not to wear flash storage.

- command audit data is kept in memory and may optionally be mirrored only to `/run/...` or `/tmp/...`
- idempotency replay state is kept in memory and may optionally be mirrored only to `/run/...` or `/tmp/...`
- non-runtime paths for these API metadata files are ignored for persistence on purpose

This keeps API control metadata runtime-only and avoids writing it to flash-backed storage.

## Machine-Readable Contract

- `GET /v1/openapi.json` returns the OpenAPI `3.1.0` document
- OpenAPI `3.1` component schemas are JSON Schema-compatible and are the normative machine-readable contract

## Endpoints

### Public endpoints

- `GET /v1/control/health`
- `GET /v1/state/healthz`
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
- `ControlApiAdminToken` grants read, basic control, and admin-only control access
- `ControlApiUpdateToken` grants read, control, admin, and update-trigger access
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
- `If-Match: "<state-token>"`
- `X-State-Token: <state-token>`

The HTTP adapter will generate a `command_id` when none is supplied.

## Optimistic concurrency

Clients can protect control writes against stale assumptions about current local
state.

Recommended flow:

- read one state or capabilities endpoint
- capture `ETag` or `X-State-Token`
- send that token back in `If-Match` or `X-State-Token`

Behavior:

- matching token allows the write to proceed
- stale token returns `409` with `code=conflict`
- `If-Match: *` explicitly skips the concurrency check

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
- `validation_error`
- `unauthorized`
- `insufficient_scope`
- `forbidden_remote_client`
- `unsupported_command`
- `unsupported_for_topology`
- `blocked_by_health`
- `blocked_by_mode`
- `update_in_progress`
- `command_rejected`
- `idempotency_conflict`
- `rate_limited`
- `cooldown_active`
- `not_found`

HTTP status mapping:

- `200 OK` for `applied`
- `202 Accepted` for `accepted_in_flight`
- `409 Conflict` for rejected commands or idempotency conflicts
- `429 Too Many Requests` for local rate limits or command cooldowns
- `400 Bad Request` for malformed JSON or invalid command payloads
- `401 Unauthorized` for missing/invalid tokens
- `403 Forbidden` for wrong scope or rejected remote clients

Successful capability, state, and command responses also expose the current
local state token in:

- `ETag`
- `X-State-Token`

## Idempotency and command tracking

For safe retries:

- provide one stable `Idempotency-Key`
- optionally provide your own `X-Command-Id`

Behavior:

- same `Idempotency-Key` + same payload returns the cached response with `replayed=true`
- same `Idempotency-Key` + different payload returns `409` with `code=idempotency_conflict`

The replay cache is runtime-only:

- it stays in memory and can optionally be mirrored under `/run/...` or `/tmp/...`
- it is not intended to survive a device reboot
- it should not be pointed at flash-backed storage

## Request schema strictness

`POST /v1/control/command` is now described in OpenAPI as an explicit per-command
`oneOf` contract instead of one generic loose payload.

That means the machine-readable contract now publishes, per command:

- allowed fields
- required fields
- expected types
- value ranges or enums where applicable

Examples:

- `set_mode` accepts only `0`, `1`, or `2`
- `set_phase_selection` accepts only known phase-selection values
- `set_current_setting` requires a non-negative numeric value
- runtime-setting payloads are split into float, integer, string, and binary forms

Runtime validation in the service follows the same stricter rules, so unclear
payloads are rejected early with `validation_error`.

## Local throttling

The HTTP adapter includes small local abuse guards for control traffic:

- `ControlApiRateLimitMaxRequests`
- `ControlApiRateLimitWindowSeconds`
- `ControlApiCriticalCooldownSeconds`

These are intentionally:

- local-only
- lightweight
- runtime-only

They exist to prevent command storms or overly aggressive retry loops from a
local client. They are not intended as a remote quota or multi-tenant traffic
policy.

When throttling is active, the API returns:

- `429 Too Many Requests`
- `code=rate_limited` for general request bursts
- `code=cooldown_active` for short cool-down protection on critical commands
- `Retry-After` when a sensible retry delay is known

## Event stream

`GET /v1/events` returns `application/x-ndjson`.

Each line is one event object with:

- `seq`
- `api_version`
- `kind`
- `timestamp`
- `resume_token`
- `payload`

Useful query parameters:

- `limit`
- `after`
- `resume`
- `heartbeat`
- `timeout`
- `once`
- `kind`

`kind` may be repeated or comma-separated to filter the stream to specific
event types, for example:

- `kind=command`
- `kind=state`
- `kind=command&kind=state`
- `kind=command,state`

Reconnect hints:

- the stream response includes `X-Control-Api-Retry-Ms`
- heartbeat payloads include `retry_hint_ms`
- heartbeat payloads include `resume_hint`

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
