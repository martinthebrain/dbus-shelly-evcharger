# Venus OS EV Charger Service

[![CI](https://github.com/martinthebrain/venus-evcharger-service/actions/workflows/ci.yml/badge.svg)](https://github.com/martinthebrain/venus-evcharger-service/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/martinthebrain/venus-evcharger-service/graph/badge.svg)](https://codecov.io/gh/martinthebrain/venus-evcharger-service)

`venus-evcharger-service` brings EV charging setups into Victron Venus OS as a
full EV charger service with GUI tile, DBus integration, MQTT reachability,
Auto logic, Scheduled logic, backend abstraction, and GX-friendly deployment.

It fits simple relay-driven charging bricks, Shelly-based relay paths, native
charger integrations such as go-e, and modular topologies with external meter,
switch, charger, and phase-switch components.

## What This Repository Delivers

- Victron EV charger service on DBus for Venus OS and Cerbo GX
- Manual, Auto, and Scheduled/Plan charging modes
- DBus and MQTT control surface for day-to-day runtime settings
- Optional local HTTP Control API v1 for process-local automation
- Native Shelly backends plus configurable HTTP and Modbus adapters
- Native charger integrations for go-e, SimpleEVSE, and SmartEVSE
- External phase-switch coordination through switch backends and `switch_group`
- Runtime diagnostics for status, faults, retry, recovery, scheduled state,
  phase state, contactor state, and backend composition
- GX-focused bootstrap and updater flow for field deployment under `/data`
- Broad automated test coverage across runtime behavior, invariants, topology
  conflicts, config-space validation, and outward-state publishing

## Typical Setups

| Setup | Main idea | Typical backend shape |
| --- | --- | --- |
| Shelly relay + charging brick | Victron controls a relay path that powers a portable EVSE | `combined` or `split relay+meter` |
| Native charger | Charger handles enable/current directly | `split` with `ChargerType=...` |
| Native charger + external phase switch | Charger handles current, external relays handle phase layout | `ChargerType=...` + `SwitchType=switch_group` |
| Multi-device wallbox | Meter, switch, charger, and feedback come from different devices | `split` |
| RS485 / Modbus EVSE | Charger control via Modbus RTU or TCP | `modbus_charger`, `simpleevse_charger`, `smartevse_charger` |

## Charging Modes

### Manual

`/Mode = 0`

- Direct start/stop control from the Victron EV charger tile
- Real relay or charger state is mirrored back into the GUI

### Auto

`/Mode = 1`

Auto mode combines:

- PV surplus
- grid import/export
- battery SOC
- daytime windows
- start/stop delays
- minimum runtime and minimum off-time
- recovery timers after missing grid data
- optional high-SOC profile
- learned charging power and adaptive smoothing
- optional automatic phase switching

The service publishes rich Auto diagnostics on DBus, including status source,
fault state, recovery state, retry state, scheduled state, phase state,
contactor state, and backend state.

### Scheduled / Plan

`/Mode = 2`

Scheduled mode runs Auto policy during the configured daytime window and adds a
night-boost window for selected target days.

Key controls:

- `/Auto/ScheduledEnabledDays`
- `/Auto/ScheduledFallbackDelaySeconds`
- `/Auto/ScheduledLatestEndTime`
- `/Auto/ScheduledNightCurrent`

Key diagnostics:

- `/Auto/ScheduledState`
- `/Auto/ScheduledStateCode`
- `/Auto/ScheduledReason`
- `/Auto/ScheduledReasonCode`
- `/Auto/ScheduledNightBoostActive`
- `/Auto/ScheduledTargetDay`
- `/Auto/ScheduledTargetDate`
- `/Auto/ScheduledFallbackStart`
- `/Auto/ScheduledBoostUntil`

## Supported Backend Families

### Native Shelly

- `shelly_combined`
- `shelly_meter`
- `shelly_switch`
- `shelly_contactor_switch`

Shelly profile presets are documented in
[SHELLY_PROFILES.md](SHELLY_PROFILES.md).

### Template Adapters

- `template_meter`
- `template_switch`
- `template_charger`

These adapters map HTTP endpoints and JSON paths into the normalized wallbox
surface. They are useful for vendor APIs, small helper services, and mixed
installations.

### Native Charger Backends

| Backend | Transport | Delivered functions |
| --- | --- | --- |
| `goe_charger` | HTTP | enable, disable, current, status, power, energy |
| `simpleevse_charger` | Modbus | enable, disable, current, status, fault |
| `smartevse_charger` | Modbus | enable, disable, current, status, fault |
| `modbus_charger` | Modbus | generic profile-driven charger mapping |

Backend examples, transport notes, charger-native topologies, and Modbus
starter configs live in [CHARGER_BACKENDS.md](CHARGER_BACKENDS.md).

### External Phase Switching

- `switch_group`

`switch_group` maps child switch configs to logical phases such as `P1`,
`P1_P2`, and `P1_P2_P3`. This is a strong fit for contactor banks, mixed
hardware, and chargers whose current control and phase switching live in
different devices.

## Runtime Control Through DBus And MQTT

The Venus MQTT bridge exposes the same runtime paths that the service publishes
on DBus, so the wallbox can be steered locally in the GX UI and remotely in
Home Assistant or other MQTT clients.

Runtime override paths cover these groups:

- Core charging:
  `/Mode`, `/AutoStart`, `/SetCurrent`, `/MinCurrent`, `/MaxCurrent`,
  `/PhaseSelection`
- Auto thresholds and timing:
  `/Auto/StartSurplusWatts`, `/Auto/StopSurplusWatts`, `/Auto/MinSoc`,
  `/Auto/ResumeSoc`, `/Auto/StartDelaySeconds`, `/Auto/StopDelaySeconds`,
  `/Auto/DbusBackoffBaseSeconds`, `/Auto/DbusBackoffMaxSeconds`,
  `/Auto/GridRecoveryStartSeconds`, `/Auto/StopSurplusDelaySeconds`
- Learned-power and smoothing:
  `/Auto/StopSurplusVolatilityLowWatts`,
  `/Auto/StopSurplusVolatilityHighWatts`,
  `/Auto/ReferenceChargePowerWatts`, `/Auto/LearnChargePowerEnabled`,
  `/Auto/LearnChargePowerMinWatts`, `/Auto/LearnChargePowerAlpha`,
  `/Auto/LearnChargePowerStartDelaySeconds`,
  `/Auto/LearnChargePowerWindowSeconds`,
  `/Auto/LearnChargePowerMaxAgeSeconds`
- Phase policy:
  `/Auto/PhaseSwitching`, `/Auto/PhasePreferLowestWhenIdle`,
  `/Auto/PhaseUpshiftDelaySeconds`, `/Auto/PhaseDownshiftDelaySeconds`,
  `/Auto/PhaseUpshiftHeadroomWatts`, `/Auto/PhaseDownshiftMarginWatts`,
  `/Auto/PhaseMismatchRetrySeconds`, `/Auto/PhaseMismatchLockoutCount`,
  `/Auto/PhaseMismatchLockoutSeconds`
- Scheduled policy:
  `/Auto/ScheduledEnabledDays`, `/Auto/ScheduledFallbackDelaySeconds`,
  `/Auto/ScheduledLatestEndTime`, `/Auto/ScheduledNightCurrent`
- Software update control:
  `/Auto/SoftwareUpdateRun`
- Software update visibility:
  `/Auto/SoftwareUpdateAvailable`, `/Auto/SoftwareUpdateState`,
  `/Auto/SoftwareUpdateStateCode`, `/Auto/SoftwareUpdateDetail`,
  `/Auto/SoftwareUpdateCurrentVersion`, `/Auto/SoftwareUpdateAvailableVersion`,
  `/Auto/SoftwareUpdateNoUpdateActive`

Operational metadata:

- `/Auto/RuntimeOverridesActive`
- `/Auto/RuntimeOverridesPath`

The default `RuntimeOverridesPath` uses `/run/...`, so DBus and MQTT control
values stay in RAM during runtime and fall back to the base config after a GX
reboot.

Software update cadence:

- remote availability check once per week
- one delayed auto-refresh run about one hour after a GX reboot
- manual refresh run through DBus or MQTT when `noUpdate` is absent
- fixed outward update states including `available-blocked` for
  "update exists and local policy blocks installation"

## Local HTTP Control API

For process-local automation, the service can also expose a small versioned
HTTP control surface on top of the same canonical command handling used by
DBus writes.

Reference docs:

- [API_OVERVIEW.md](API_OVERVIEW.md)
- [DEV_API_WORKFLOW.md](DEV_API_WORKFLOW.md)
- [API_OPERATOR_GUIDE.md](API_OPERATOR_GUIDE.md)
- [CONTROL_API.md](CONTROL_API.md)
- [STATE_API.md](STATE_API.md)
- [API_VERSIONING.md](API_VERSIONING.md)
- [examples/control_api_client.py](examples/control_api_client.py)

Config keys:

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
- `ControlApiRateLimitMaxRequests`
- `ControlApiRateLimitWindowSeconds`
- `ControlApiCriticalCooldownSeconds`

Endpoints:

- `GET /v1/openapi.json`
- `GET /v1/capabilities`
- `GET /v1/control/health`
- `GET /v1/state/healthz`
- `POST /v1/control/command`
- `GET /v1/events`
- `GET /v1/state/version`
- `GET /v1/state/build`
- `GET /v1/state/contracts`
- `GET /v1/state/config-effective`
- `GET /v1/state/summary`
- `GET /v1/state/runtime`
- `GET /v1/state/operational`
- `GET /v1/state/dbus-diagnostics`
- `GET /v1/state/topology`
- `GET /v1/state/update`
- `GET /v1/state/health`

The full request/response contract, auth rules, idempotency behavior, event
stream, `curl` examples, Python example, and CLI snippets live in
[CONTROL_API.md](CONTROL_API.md).

<!-- BEGIN:README_LOCAL_HTTP_CONTROL_API_GETTING_STARTED -->
Quick start:

- `python3 ./venus_evchargerctl.py --token READ-TOKEN health`
- `python3 ./venus_evchargerctl.py --token READ-TOKEN capabilities`
- `python3 ./venus_evchargerctl.py --token READ-TOKEN state summary`
- `python3 ./venus_evchargerctl.py --token CONTROL-TOKEN command set-mode 1`
- `python3 ./venus_evchargerctl.py --unix-socket /run/venus-evcharger-control.sock --token READ-TOKEN events --kind command --once`

For direct HTTP usage, `curl` snippets, optimistic concurrency with `If-Match`,
and a small Python example, see [CONTROL_API.md](CONTROL_API.md).
For a short local developer runbook on a normal PC, see
[DEV_API_WORKFLOW.md](DEV_API_WORKFLOW.md).
<!-- END:README_LOCAL_HTTP_CONTROL_API_GETTING_STARTED -->

On the installed Venus/GX target, use the deploy wrapper:

- `./deploy/venus/venus_evchargerctl.sh health`
- `./deploy/venus/venus_evchargerctl.sh --token READ-TOKEN state summary`
- `./deploy/venus/venus_evchargerctl.sh --unix-socket /run/venus-evcharger-control.sock --token CONTROL-TOKEN command set-mode 1`

API audit and idempotency metadata are intentionally runtime-only. Keep their
paths on `/run/...` or `/tmp/...`; they are not meant for flash-backed storage.
For process-local automation, prefer a unix socket over TCP when practical.

`venus_evchargerctl` exit codes:

- `0` success with `2xx` API response
- `1` request reached the API but failed or was rejected
- `2` CLI usage error

For read-only local inspection, the same listener also exposes:

- `GET /v1/state/summary`
- `GET /v1/state/runtime`
- `GET /v1/state/operational`
- `GET /v1/state/dbus-diagnostics`
- `GET /v1/state/topology`
- `GET /v1/state/update`
- `GET /v1/state/version`
- `GET /v1/state/build`
- `GET /v1/state/contracts`
- `GET /v1/state/config-effective`
- `GET /v1/state/health`

The stable state/read contract lives in [STATE_API.md](STATE_API.md). Formal
stability rules for `v1` live in [API_VERSIONING.md](API_VERSIONING.md).

## Install On Cerbo GX / Venus OS

### Quick Start

1. Copy the repository to a writable path under `/data`, for example
   `/data/venus-evcharger-service`.
2. Edit `deploy/venus/config.venus_evcharger.ini`.
3. Run:

   ```bash
   cd /data/venus-evcharger-service
   ./deploy/venus/install_venus_evcharger_service.sh
   ```

   For guided first-time setup or an intentional reconfiguration, run the
   optional wizard before restarting the service:

   ```bash
   ./deploy/venus/configure_venus_evcharger_service.sh
   ```

   The wizard also supports non-interactive presets, import/clone defaults from
   an existing config or the last wizard result, dry-run JSON previews,
   guarded `--force` overwrites, separate `--meter-host` / `--switch-host` /
   `--charger-host` role inputs, hidden password prompts for interactive auth
   setup, optional `--live-check` probing or targeted `--probe-role`
   adapter checks, guided Auto/Scheduled starter values, preset compatibility
   warnings, and writes a small result/audit trail plus topology summary
   beside the config for later review.

   Split-topology presets now cover documented starter layouts such as:
   `template-stack`, `shelly-meter-goe`, `goe-external-switch-group`,
   `shelly-meter-goe-switch-group`, `shelly-io-modbus-charger`, and
   `shelly-meter-modbus-switch-group`. Native Modbus charger presets now also
   cover device-specific mappings for `abb-terra-ac-modbus`,
   `cfos-power-brain-modbus`, and `openwb-modbus-secondary`.

4. Restart the service:

   ```bash
   svc -t /service/dbus-venus-evcharger
   ```

5. Verify the service:

   ```bash
   svstat /service/dbus-venus-evcharger
   dbus -y com.victronenergy.evcharger.http_60 /Connected GetValue
   ```

### One-File Bootstrap

`install.sh` supports a small-footprint bootstrap flow for GX devices. Place it
under `/data`, run it there, and let it materialize the working tree plus the
regular Venus installer.

Bootstrap highlights:

- refreshes the local updater
- populates the wallbox tree under a target directory
- preserves the local wallbox config
- additively merges newly shipped template keys into the preserved config
  without overwriting existing local values
- keeps existing config comments and layout where possible, and writes a
  timestamped backup before a merge rewrite
- validates the merged wallbox config before activating a refreshed tree
- records updater status and audit metadata under `.bootstrap-state/`
- supports `--dry-run` preview output for careful field updates
- supports bundle manifests and detached signatures
- keeps release directories under `releases/<version>/`
- advances `current/` after the new release is ready
- keeps `previous/` available for rollback

Useful bootstrap variables:

- `VENUS_EVCHARGER_TARGET_DIR`
- `VENUS_EVCHARGER_CHANNEL`
- `VENUS_EVCHARGER_SOURCE_DIR`
- `VENUS_EVCHARGER_MANIFEST_SOURCE`
- `VENUS_EVCHARGER_BOOTSTRAP_PUBKEY`
- `VENUS_EVCHARGER_REQUIRE_SIGNED_MANIFEST`

The full installation walkthrough lives in [INSTALL.md](INSTALL.md).
The bootstrap and updater flow is described in [UPDATE_FLOW.md](UPDATE_FLOW.md).

## Configuration Guide

The main deployment file is:

- `deploy/venus/config.venus_evcharger.ini`

The full configuration guide lives in [CONFIGURATION.md](CONFIGURATION.md).

Start with these settings:

- `Host`
- `DeviceInstance`
- `Phase`
- `DigestAuth`, `Username`, `Password` when your device uses auth
- DBus service selectors for battery, PV, and grid when you pin them manually

Then shape the installation in three layers:

1. **Service identity and DBus mapping**
2. **Backend selection**
3. **Policy tuning for Auto and Scheduled**

### Backend Selection Patterns

| Pattern | Core settings |
| --- | --- |
| Combined relay + meter | `Mode=combined`, `Type=shelly_combined` or another combined backend |
| Split relay + meter | `Mode=split`, `MeterType=...`, `SwitchType=...` |
| Native charger | `Mode=split`, `ChargerType=...` |
| Native charger + external phase switch | `Mode=split`, `ChargerType=...`, `SwitchType=switch_group` |

### Probe And Validation

Validate a full wallbox config before deployment:

```bash
python3 -m venus_evcharger.backend.probe validate-wallbox deploy/venus/config.venus_evcharger.ini
```

Validate an individual adapter file:

```bash
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-meter.ini
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-switch.ini
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-charger.ini
```

## Diagnostics And Operations

Operations and live DBus diagnostics are collected in
[DIAGNOSTICS.md](DIAGNOSTICS.md).

For common field issues and quick checks, see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## Design Spec And Contracts

The compact state and priority spec lives in [STATE_MODEL.md](STATE_MODEL.md).

It defines:

- outward truth priority
- authoritative sources
- persistence rules
- topology rules
- safeguard expectations for future changes

Runtime-near contracts back the spec at the publish edge, so outward state
fields stay coherent as the feature set grows.

## Development

Helpful local commands:

```bash
./scripts/dev/check_all.sh
./scripts/dev/run_typecheck.sh
./scripts/dev/run_stress_tests.sh
make check
make typecheck
make stress
```

The codebase is organized around a stable service entrypoint, clear package
boundaries for auto/update/backend/bootstrap concerns, and a growing set of
tests around invariants, topologies, and outward-state contracts. That makes it
straightforward to extend hardware support, policy layers, and diagnostics while
keeping the visible behavior crisp.

Contributor guidance lives in [CONTRIBUTING.md](CONTRIBUTING.md).
