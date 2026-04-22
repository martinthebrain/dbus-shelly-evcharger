# Local State API

The local HTTP API also exposes a read/state surface beside the command
endpoint.

For the short architectural overview of Control vs State vs Events, see
[API_OVERVIEW.md](API_OVERVIEW.md).

For operator quick checks and target-system CLI examples, see
[API_OPERATOR_GUIDE.md](API_OPERATOR_GUIDE.md).
For local PC-based development and API/CLI iteration, see
[DEV_API_WORKFLOW.md](DEV_API_WORKFLOW.md).

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

Successful state and capability reads also expose the current local state token:

- `ETag`
- `X-State-Token`

Clients can pass that token back to `POST /v1/control/command` via `If-Match`
or `X-State-Token` to protect writes against stale state assumptions.

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
- `combined_battery_soc`
- `combined_battery_source_count`
- `combined_battery_online_source_count`
- `combined_battery_charge_power_w`
- `combined_battery_discharge_power_w`
- `combined_battery_net_power_w`
- `combined_battery_ac_power_w`
- `combined_battery_learning_profile_count`
- `combined_battery_observed_max_charge_power_w`
- `combined_battery_observed_max_discharge_power_w`

These combined battery fields are populated from the normalized multi-source
energy snapshot used by Auto mode. With `AutoEnergySources=...`, the service
can aggregate several battery or hybrid-inverter sources and expose the
combined result here for local tooling and troubleshooting.

### Combined battery state details

- `combined_battery_soc` is the aggregated SOC view used for operator-facing
  diagnostics
- `combined_battery_source_count` counts configured/readable energy sources in
  the current snapshot
- `combined_battery_online_source_count` counts sources that were online in the
  current snapshot
- `combined_battery_charge_power_w` sums currently observed charging power
- `combined_battery_discharge_power_w` sums currently observed discharge power
- `combined_battery_net_power_w` is the signed combined battery power
- `combined_battery_ac_power_w` sums configured AC-side power visibility
- `combined_battery_learning_profile_count` counts runtime learning profiles
  that currently contribute observed maxima
- `combined_battery_observed_max_charge_power_w` and
  `combined_battery_observed_max_discharge_power_w` summarize the runtime-only
  learned maxima across sources

`GET /v1/state/runtime` also exposes the lower-level aggregation payload,
including:

- `combined_battery_usable_capacity_wh`
- `combined_battery_valid_soc_source_count`
- `combined_battery_sources`
- `combined_battery_learning_profiles`

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

### `GET /v1/state/healthz`

Returns a tiny liveness-style payload for local supervision.

This endpoint is intentionally lightweight and is also available without auth
when the local listener is enabled. It is still subject to the service's local
binding rules.

Typical fields include:

- `alive`
- `service`
- `api_version`

### `GET /v1/state/version`

Returns the current service and API version identity for local tooling.

Typical fields include:

- `service_version`
- `api_version`
- `current_version`

### `GET /v1/state/build`

Returns build and product identity metadata for the running service.

Typical fields include:

- `product_name`
- `service_name`
- `connection_name`
- `hardware_version`
- `firmware_version`

### `GET /v1/state/contracts`

Returns links and endpoint references for the active API contract surface.

Typical fields include:

- `openapi_path`
- `capabilities_path`
- `control_api_doc`
- `state_api_doc`
- `versioning_doc`

## Event stream relationship

`GET /v1/events` complements the read endpoints:

- `state/*` is the current snapshot surface
- `events` is the incremental change surface
- `events?kind=...` can narrow the incremental surface to selected event kinds

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

Read healthz:

```bash
curl -s \
  http://127.0.0.1:8765/v1/state/healthz
```

Read version:

```bash
curl -s \
  -H 'Authorization: Bearer READ-TOKEN' \
  http://127.0.0.1:8765/v1/state/version
```

Use a unix socket:

```bash
curl --unix-socket /run/venus-evcharger-control.sock \
  -H 'Authorization: Bearer READ-TOKEN' \
  http://localhost/v1/state/summary
```

## Versioning

The formal versioning rules are documented in [API_VERSIONING.md](API_VERSIONING.md).
