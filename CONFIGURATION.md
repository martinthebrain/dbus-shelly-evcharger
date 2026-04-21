# Configuration

This guide explains how to shape a wallbox installation from
`deploy/venus/config.venus_evcharger.ini`.

The most useful way to read the config is by decision area:

1. service identity
2. hardware topology
3. charger control style
4. Auto policy
5. Scheduled policy
6. runtime control and persistence
7. optional local HTTP control API

## Core Deployment Values

These are the first values to review in a fresh install:

- `Host`
- `DeviceInstance`
- `Phase`
- `Username`
- `Password`
- `DigestAuth`

Then review the pinned DBus selectors when you prefer explicit services for:

- battery
- PV
- grid

## Topology Decisions

The main topology choice is whether the installation behaves like one combined
wallbox path or like a composed set of meter, switch, charger, and phase
components.

### Combined

Use `combined` when one backend represents the visible charging path.

Typical fit:

- Shelly relay path with directly visible power
- one device representing the wallbox behavior as a whole

### Split

Use `split` when the installation is composed from separate roles:

- `MeterType=...`
- `SwitchType=...`
- `ChargerType=...`

Typical fit:

- separate meter and relay devices
- charger-native setups
- charger plus external phase switching
- multi-device installations with explicit feedback and interlock inputs

## Backend Selection Patterns

### Relay + meter path

Typical shape:

```ini
[Backends]
Mode=split
MeterType=shelly_meter
SwitchType=shelly_switch
```

### Charger-native path

Typical shape:

```ini
[Backends]
Mode=split
MeterType=none
SwitchType=none
ChargerType=goe_charger
```

### Charger + external phase switch

Typical shape:

```ini
[Backends]
Mode=split
MeterType=none
SwitchType=switch_group
ChargerType=simpleevse_charger
```

For backend-specific examples, see:

- [CHARGER_BACKENDS.md](CHARGER_BACKENDS.md)
- [SHELLY_PROFILES.md](SHELLY_PROFILES.md)

## Phase Configuration

The visible phase layout is shaped by:

- wallbox-level `Phase`
- backend capabilities
- optional phase-switching policy
- optional feedback and observed-phase diagnostics

Relevant runtime-tunable paths:

- `/PhaseSelection`
- `/Auto/PhaseSwitching`
- `/Auto/PhasePreferLowestWhenIdle`
- `/Auto/PhaseUpshiftDelaySeconds`
- `/Auto/PhaseDownshiftDelaySeconds`
- `/Auto/PhaseUpshiftHeadroomWatts`
- `/Auto/PhaseDownshiftMarginWatts`

## Auto Policy

Auto policy is built from:

- surplus start/stop thresholds
- SOC thresholds
- start/stop delays
- minimum runtime and off-time
- grid recovery timing
- learned charging power
- stop smoothing
- optional high-SOC profile
- optional automatic phase switching

Useful primary controls:

- `AutoStartSurplusWatts`
- `AutoStopSurplusWatts`
- `AutoMinSoc`
- `AutoResumeSoc`
- `AutoStartDelaySeconds`
- `AutoStopDelaySeconds`
- `AutoGridRecoveryStartSeconds`
- `AutoReferenceChargePowerWatts`

Useful advanced controls:

- learned-power window and alpha values
- stop-volatility thresholds
- phase upshift/downshift timing
- phase mismatch and lockout tuning

## Scheduled / Plan Policy

Scheduled mode uses the same base policy as Auto and adds target-day night
boost windows.

Main settings:

- `AutoScheduledEnabledDays`
- `AutoScheduledFallbackDelaySeconds`
- `AutoScheduledLatestEndTime`
- `AutoScheduledNightCurrentAmps`

These are also available through DBus and MQTT as runtime overrides.

## Runtime Overrides

Selected runtime values can be changed through DBus and MQTT and are stored in
the runtime override file.

The default `RuntimeOverridesPath` uses `/run/...`, so the active values stay
available during runtime and service restarts, and the base config takes over
again after a GX reboot.

The runtime layer is a strong fit for:

- charging mode
- current and phase selection
- Auto thresholds and delays
- learned-power tuning
- phase policy
- Scheduled policy

Operational metadata:

- `/Auto/RuntimeOverridesActive`
- `/Auto/RuntimeOverridesPath`

## Local HTTP Control API

An optional process-local HTTP control surface can be enabled when another
local service or script should drive the charger without going through DBus
directly.

Main config values:

- `ControlApiEnabled`
- `ControlApiHost`
- `ControlApiPort`
- `ControlApiAuthToken`
- `ControlApiReadToken`
- `ControlApiControlToken`
- `ControlApiAdminToken`
- `ControlApiUpdateToken`
- `ControlApiLocalhostOnly`
- `ControlApiUnixSocketPath`
- `ControlApiAuditPath`
- `ControlApiIdempotencyPath`
- `ControlApiRateLimitMaxRequests`
- `ControlApiRateLimitWindowSeconds`
- `ControlApiCriticalCooldownSeconds`

Recommended shape:

- keep `ControlApiLocalhostOnly=1`
- keep `ControlApiHost=127.0.0.1`
- prefer `ControlApiUnixSocketPath` for process-local automation when feasible
- prefer split `ControlApiReadToken` and `ControlApiControlToken` for local clients with different scopes
- add `ControlApiAdminToken` and `ControlApiUpdateToken` only when you want stricter local separation for advanced local control
- use `ControlApiAuthToken` only when one shared token is enough
- keep `ControlApiAuditPath` and `ControlApiIdempotencyPath` on `/run/...` or `/tmp/...`
- keep the API rate-limit window small and local; this is meant as abuse protection, not remote multi-tenant throttling
- do not point API audit or idempotency files at flash-backed storage
- treat this as a command surface, not a general telemetry interface

The formal JSON contract, the OpenAPI `3.1` document, HTTP status mapping, and
`curl` examples live in [CONTROL_API.md](CONTROL_API.md).

The same listener also exposes `GET /v1/capabilities` for stable topology and
command discovery, `GET /v1/events` for NDJSON event streaming, and the
state endpoints:

- `GET /v1/state/healthz`
- `GET /v1/state/version`
- `GET /v1/state/build`
- `GET /v1/state/contracts`
- `GET /v1/state/summary`
- `GET /v1/state/runtime`
- `GET /v1/state/operational`
- `GET /v1/state/dbus-diagnostics`
- `GET /v1/state/topology`
- `GET /v1/state/update`
- `GET /v1/state/config-effective`
- `GET /v1/state/health`

For read-only local inspection on the same listener, see [STATE_API.md](STATE_API.md).
For stability rules inside `v1`, see [API_VERSIONING.md](API_VERSIONING.md).

## Validation Before Start

Validate the full wallbox config:

```bash
python3 -m venus_evcharger.backend.probe validate-wallbox deploy/venus/config.venus_evcharger.ini
```

Validate adapter files individually:

```bash
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-meter.ini
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-switch.ini
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-charger.ini
```

## Live Inspection

For runtime diagnostics, see:

- [DIAGNOSTICS.md](DIAGNOSTICS.md)
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- [STATE_MODEL.md](STATE_MODEL.md)
