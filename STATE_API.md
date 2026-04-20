# Local State API

The local HTTP API also exposes a read/state surface beside the command
endpoint.

This API is intentionally:

- local-first
- versioned
- read-oriented
- sourced from existing service state, not a second model

It reuses the same listener configured through:

```ini
ControlApiEnabled=1
ControlApiHost=127.0.0.1
ControlApiPort=8765
ControlApiReadToken=
ControlApiControlToken=
ControlApiLocalhostOnly=1
ControlApiUnixSocketPath=
```

Read access requires:

- `Authorization: Bearer <read-or-control-token>` when read auth is configured
- a local client when `ControlApiLocalhostOnly=1`

## Endpoints

### `GET /v1/state/summary`

Returns the compact state-summary string already used by logs and recovery
paths.

### `GET /v1/state/runtime`

Returns the current runtime-state snapshot used for runtime persistence.

### `GET /v1/state/operational`

Returns a normalized operator-facing snapshot for automation and monitoring.

Stable fields in `state` include:

- `mode`
- `enable`
- `startstop`
- `autostart`
- `active_phase_selection`
- `requested_phase_selection`
- `backend_mode`
- `meter_backend`
- `switch_backend`
- `charger_backend`
- `auto_state`
- `auto_state_code`
- `fault_active`
- `fault_reason`
- `software_update_state`
- `software_update_state_code`
- `software_update_available`
- `software_update_no_update_active`
- `runtime_overrides_active`
- `runtime_overrides_path`

### `GET /v1/state/dbus-diagnostics`

Returns the compact outward diagnostics set that the service also publishes on
DBus. The returned `state` object uses DBus path names as stable keys.

### `GET /v1/state/topology`

Returns the currently effective topology view, including backend roles,
supported phase selections, available modes, and service identity.

### `GET /v1/state/update`

Returns the current software-update view, including:

- current version
- available version
- availability flag
- update state
- detail
- last check/run timestamps
- next scheduled check
- queued run time

### `GET /v1/state/config-effective`

Returns a safe effective-config subset for local tooling. This endpoint is
intentionally curated and excludes secrets such as passwords and tokens.

Typical fields include:

- device identity
- runtime paths
- control API binding settings
- backend selection
- selected scheduled/Auto policy basics

### `GET /v1/state/health`

Returns a compact local health view, including:

- health reason and code
- fault state
- runtime-override activity
- control API binding status
- update staleness and recovery timestamps

## Event stream relationship

`GET /v1/events` complements the read endpoints:

- `state/*` is the current snapshot surface
- `events` is the incremental change surface

Clients that need a quick current picture should start with one or more
`state/*` reads or use `/v1/events?once=1` to receive a snapshot event plus
recent events.

## Curl examples

Read one operational snapshot:

```bash
curl -s \
  -H 'Authorization: Bearer READ-TOKEN' \
  http://127.0.0.1:8765/v1/state/operational
```

Read topology:

```bash
curl -s \
  -H 'Authorization: Bearer READ-TOKEN' \
  http://127.0.0.1:8765/v1/state/topology
```

Read update state:

```bash
curl -s \
  -H 'Authorization: Bearer READ-TOKEN' \
  http://127.0.0.1:8765/v1/state/update
```

Read effective config:

```bash
curl -s \
  -H 'Authorization: Bearer READ-TOKEN' \
  http://127.0.0.1:8765/v1/state/config-effective
```

Read health:

```bash
curl -s \
  -H 'Authorization: Bearer READ-TOKEN' \
  http://127.0.0.1:8765/v1/state/health
```

Use a unix socket:

```bash
curl --unix-socket /run/venus-evcharger-control.sock \
  -H 'Authorization: Bearer READ-TOKEN' \
  http://localhost/v1/state/summary
```

## Versioning

The formal versioning rules are documented in [API_VERSIONING.md](API_VERSIONING.md).
