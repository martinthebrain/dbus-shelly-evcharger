# Shelly Wallbox for Victron Venus OS

[![CI](https://github.com/martinthebrain/dbus-shelly-evcharger/actions/workflows/ci.yml/badge.svg)](https://github.com/martinthebrain/dbus-shelly-evcharger/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/martinthebrain/dbus-shelly-evcharger/graph/badge.svg)](https://codecov.io/gh/martinthebrain/dbus-shelly-evcharger)

`dbus-shelly-evcharger` brings EV charging setups into Victron Venus OS as a
full EV charger service with GUI tile, DBus integration, MQTT reachability,
Auto logic, Scheduled logic, backend abstraction, and GX-friendly deployment.

It fits simple relay-driven charging bricks, Shelly-based relay paths, native
charger integrations such as go-e, and modular topologies with external meter,
switch, charger, and phase-switch components.

## What This Repository Delivers

- Victron EV charger service on DBus for Venus OS and Cerbo GX
- Manual, Auto, and Scheduled/Plan charging modes
- DBus and MQTT control surface for day-to-day runtime settings
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

## Install On Cerbo GX / Venus OS

### Quick Start

1. Copy the repository to a writable path under `/data`, for example
   `/data/shellyWB`.
2. Edit `deploy/venus/config.shelly_wallbox.ini`.
3. Run:

   ```bash
   cd /data/shellyWB
   ./deploy/venus/install_shelly_wallbox.sh
   ```

4. Restart the service:

   ```bash
   svc -t /service/dbus-shelly-wallbox
   ```

5. Verify the service:

   ```bash
   svstat /service/dbus-shelly-wallbox
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
- supports bundle manifests and detached signatures
- keeps release directories under `releases/<version>/`
- advances `current/` after the new release is ready
- keeps `previous/` available for rollback

Useful bootstrap variables:

- `SHELLY_WALLBOX_TARGET_DIR`
- `SHELLY_WALLBOX_CHANNEL`
- `SHELLY_WALLBOX_SOURCE_DIR`
- `SHELLY_WALLBOX_MANIFEST_SOURCE`
- `SHELLY_WALLBOX_BOOTSTRAP_PUBKEY`
- `SHELLY_WALLBOX_REQUIRE_SIGNED_MANIFEST`

The full installation walkthrough lives in [INSTALL.md](INSTALL.md).
The bootstrap and updater flow is described in [UPDATE_FLOW.md](UPDATE_FLOW.md).

## Configuration Guide

The main deployment file is:

- `deploy/venus/config.shelly_wallbox.ini`

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
python3 -m shelly_wallbox.backend.probe validate-wallbox deploy/venus/config.shelly_wallbox.ini
```

Validate an individual adapter file:

```bash
python3 -m shelly_wallbox.backend.probe validate /data/etc/wallbox-meter.ini
python3 -m shelly_wallbox.backend.probe validate /data/etc/wallbox-switch.ini
python3 -m shelly_wallbox.backend.probe validate /data/etc/wallbox-charger.ini
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
