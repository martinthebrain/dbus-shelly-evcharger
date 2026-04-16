# Shelly Wallbox for Victron Venus OS

[![CI](https://github.com/martinthebrain/dbus-shelly-evcharger/actions/workflows/ci.yml/badge.svg)](https://github.com/martinthebrain/dbus-shelly-evcharger/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/martinthebrain/dbus-shelly-evcharger/graph/badge.svg)](https://codecov.io/gh/martinthebrain/dbus-shelly-evcharger)

This project exposes a Shelly-controlled charging relay as a Victron EV charger
service on Venus OS / Cerbo GX.

It is meant for simple EV charging setups where a Shelly device switches the
charging power path of a portable EVSE ("charging brick", "Ladeziegel") and the
Cerbo should show and control that setup like a native EV charger tile.

## Requirements

This project is currently used with:
- `Venus OS v3.72`
- `Shelly 1PM Gen4`
- `Python 3` as shipped with Venus OS

Practical expectations:
- a Cerbo GX or another Venus OS device with writable `/data`
- a Shelly relay that exposes compatible RPC/HTTP state and switching endpoints
- a Victron DBus environment that provides the PV / grid / battery values you
  want to use for Auto mode

The wallbox codebase is written against modern Venus OS Python 3 environments.
Older Python-2-only Venus installations are not the target setup.

The codebase supports:
- Manual charging control from the Victron GUI
- Auto mode based on PV surplus, grid import, and battery SOC
- Scheduled/Plan mode (`/Mode = 2`) with Auto-by-day plus overnight full-charge fallback
- Helper-process based DBus input polling for better runtime stability
- Audit logging for Auto-mode decisions
- Watchdog and recovery behavior for stale helper snapshots or Shelly faults
- Works out-of-the-box with Home Assistant via the Venus OS MQTT bridge. All
  runtime control parameters can be monitored and controlled remotely by
  subscribing/publishing to the corresponding MQTT topics.

## Quick Start

1. Copy the wallbox files to your Cerbo, for example to `/data/shellyWB`.
2. Edit `deploy/venus/config.shelly_wallbox.ini` and replace the example values with your
   real Shelly host, device instance, phase, and optional DBus service names.
3. Install the service:

   ```bash
   cd /data/shellyWB
   ./deploy/venus/install_shelly_wallbox.sh
   ```

4. Restart the service:

   ```bash
   svc -t /service/dbus-shelly-wallbox
   ```

5. Verify that the service is up:

   ```bash
   svstat /service/dbus-shelly-wallbox
   dbus -y com.victronenergy.evcharger.http_60 /Connected GetValue
   ```

6. Watch Auto-mode diagnostics if needed:

   ```bash
   tail -f /var/volatile/log/dbus-shelly-wallbox/auto-reasons.log
   ```

## File Overview

The runtime surface is organized around a few stable entry points plus package
groups:
- `dbus_shelly_wallbox.py`: main service entry point and patch-friendly public module
- `shelly_wallbox/bootstrap/controller.py`: bootstrap and process wiring controller
- `shelly_wallbox/controllers/state.py`: state controller and runtime-state persistence
- `shelly_wallbox/auto/workflow.py` and `shelly_wallbox/update/controller.py`: packaged Auto and update workflow controllers
- `shelly_wallbox/app/`: package facades for the main/bootstrap entry points and bootstrap support helpers
- `shelly_wallbox/core/`: package facades for shared helpers, lightweight contracts, and mixin typing seams
- `shelly_wallbox/inputs/`: package slice for DBus input reads, helper supervision, and the helper-process subpackage under `shelly_wallbox/inputs/helper/`
- `shelly_wallbox/publish/`: package slice for DBus publish helpers
- `shelly_wallbox/backend/`: normalized meter, switch, and charger backends plus template adapters
- `shelly_wallbox/auto/` and `shelly_wallbox/update/`: packaged Auto-policy and update-cycle helpers
- `shelly_wallbox/bootstrap/`, `shelly_wallbox/service/`, and `shelly_wallbox/ports/`: service composition, bootstrap mixins, and typed controller/service seams
- `shelly_wallbox/controllers/` and `shelly_wallbox/runtime/`: packaged controllers plus the primary runtime/watchdog helpers
- `shelly_wallbox/backend/shelly_io.py`: Shelly I/O worker logic and backend integration seam
- `shelly_wallbox_auto_input_helper.py`: separate helper process for PV, grid, and battery DBus inputs; remains the patch-friendly entry anchor while helper internals live under `shelly_wallbox/inputs/helper/`
- `shelly_wallbox/backend/probe.py`: validator/probe tool for backend adapter configs, usually run as `python3 -m shelly_wallbox.backend.probe`
- `deploy/venus/config.shelly_wallbox.ini`: documented example configuration for Venus OS deployments
- `deploy/venus/service_shelly_wallbox/`: runit service scripts for Venus OS
- `deploy/venus/*.sh`: Venus deployment helpers
- `scripts/dev/`: local verification helpers such as `check_all.sh`, `run_typecheck.sh`, and `run_stress_tests.sh`
- `scripts/ops/`: operational helpers such as the Cerbo soak check

The temporary compatibility-wrapper layer is gone. The remaining flat
`dbus_shelly_wallbox_*.py` modules are now deliberate entry points, workflow
anchors, or test-friendly integration seams around the packaged source layout.

Files under `tests/` are for local verification on development systems and are
not required on the Cerbo.

## How It Works

At runtime the service is split into three main layers:

1. The main wallbox service publishes a Victron EV charger service on DBus and
   owns the visible charger state used by the GUI.
2. A Shelly I/O worker talks to the Shelly device and applies relay changes
   without blocking the main loop.
3. A separate Auto-input helper process reads PV, grid, and battery values from
   DBus and writes them into a compact JSON snapshot file.

This separation keeps the service more robust on real Cerbo systems:
- slow or flaky DBus reads do not block the main charger service
- transient Shelly communication issues are isolated and retried
- Auto mode can detect stale helper data and fall back safely

## Manual Mode

In Manual mode the Victron EV charger tile behaves like a direct on/off control
for the Shelly relay.

- `/Mode = 0` means Manual mode
- `/StartStop` directly requests relay on/off
- the service continuously reads the real Shelly relay state and updates the GUI

If the relay is switched externally, the service will notice the change on the
next update cycle and bring the virtual state back in sync.

## Auto Mode

In Auto mode the relay decision is made from:
- PV surplus
- grid import/export
- battery SOC
- optional daytime windows
- start/stop delays
- minimum runtime and minimum off-time
- grid recovery protection after missing grid data
- optional high-SOC threshold profile
- adaptive stop smoothing using EWMA

The Auto logic is intentionally conservative:
- hard stop reasons such as low battery SOC or high grid import are handled strictly
- low-surplus stop behavior can be softer and more delay-tolerant
- stale or missing helper data prevents unsafe Auto starts
- the service can automatically learn the real charging power of the connected portable EVSE, use it for adaptive Auto-mode thresholds, and mirror the derived rounded charging current in the Venus EV charger tile

### Auto Logic Overview

| Start conditions | Stop conditions |
| --- | --- |
| PV surplus at or above the active start threshold | Battery SOC below minimum SOC |
| Grid import below the allowed import threshold | Grid import at or above the stop threshold |
| Battery SOC at or above the resume threshold | Surplus below the active stop threshold |
| Auto mode enabled and not blocked by warmup/off-time/recovery guards | Missing or stale helper/grid input safety conditions |

Additional guards:
- start decisions are delayed by the configured start delay
- stop decisions are delayed by the configured stop delay or surplus-specific stop delay
- minimum runtime and minimum off-time prevent relay chatter
- after genuine grid-data loss, Auto start is blocked until the grid recovery timer has passed
- a high-SOC profile can switch to more permissive start/stop thresholds
- stop smoothing uses EWMA and can adapt to volatile weather conditions

## Scheduled / Plan Mode

Venus OS exposes a third EV charger mode besides Manual and Auto. This service
maps that mode to `/Mode = 2`.

`/Mode = 2` behaves like normal Auto mode during the configured daytime window.
Outside that window it can switch into a scheduled night fallback that is tuned
for "charge with PV during the day, but still be ready the next morning".

Scheduled mode v2 adds four policy inputs:

- `AutoScheduledEnabledDays`
  Target weekdays for the next morning, for example `Mon,Tue,Wed,Thu,Fri`
- `AutoScheduledNightStartDelaySeconds`
  How long to wait after the configured month-specific daytime end before night
  fallback may begin
- `AutoScheduledLatestEndTime`
  Morning cutoff for the fallback window, for example `06:30`
- `AutoScheduledNightCurrentAmps`
  Dedicated fallback current for night charging; `0` means `MaxCurrent`

The target-day logic is intentional: with `Mon,Tue,Wed,Thu,Fri`, a Sunday night
fallback may run so the vehicle is ready for Monday morning, while a Friday
night fallback does not run for Saturday morning.

When the current time falls into an active scheduled fallback window:

- charging may start even without PV surplus, battery SOC, or live helper input
- the relay is kept enabled overnight
- native charger-current control, when available, is driven to
  `AutoScheduledNightCurrentAmps` or `MaxCurrent` if that value is `0`
- once the next daytime window opens, or the configured latest end time is
  reached, the service falls back to normal Auto behavior again

This makes a common weekday strategy possible: use PV surplus while the sun is
up, then guarantee a full vehicle by morning.

Scheduled diagnostics are exposed on DBus and therefore also via the Venus MQTT
bridge:

- `/Auto/ScheduledState`
- `/Auto/ScheduledStateCode`
- `/Auto/ScheduledNightBoostActive`
- `/Auto/ScheduledTargetDay`
- `/Auto/ScheduledTargetDate`

## Configuration

The wallbox uses `deploy/venus/config.shelly_wallbox.ini`.

This file is written as a documented example template and contains only generic
placeholder values so it can be committed to GitHub safely.

Before deploying, replace at least:
- `Host`
- `DeviceInstance`
- `Phase`
- any explicit battery or PV service names if you pin them manually
- optional generic Shelly disable helper values

### Before First Start, Change These Settings

For most users, these are the important settings to review before the first
real deployment:

- `Host`
  Set this to the real IP address or hostname of your Shelly device.
- `DeviceInstance`
  Choose a free Victron device instance so the EV charger tile does not collide
  with another service on the GX device.
- `Phase`
  Set this to `L1`, `L2`, `L3`, or `3P` so the displayed power/current mapping
  matches your electrical installation.
- `DigestAuth`, `Username`, `Password`
  Only needed if your Shelly is protected by authentication.
- `AutoBatteryService`
  Replace the example value with your real battery DBus service if you pin it
  explicitly. If you prefer discovery, use the documented discovery settings
  instead.

Optional, depending on your setup:
- `DisableGenericShellyDevice`, `GenericShellyDisableIp`, `GenericShellyDisableMac`, `GenericShellyDisableChannel`
  Review these if another generic `dbus-shelly` integration on the Cerbo could
  otherwise fight for the same Shelly device.
- Auto-mode thresholds and SOC settings
  The example values are sensible defaults, but you should tune them to your
  charger power, PV size, and battery strategy.

The comments in the config explain each group of settings:
- service identity
- Shelly access
- backend / adapter selection
- startup behavior
- PV / battery / grid input selection
- Auto-mode thresholds
- high-SOC profile
- stop smoothing
- helper / watchdog behavior
- audit logging

### Runtime Overrides via DBus / MQTT

The service now keeps a small persistent runtime-override file separate from
the main deployment config:

- `RuntimeStatePath` stays RAM-only and restores volatile UI/runtime state
- `RuntimeOverridesPath` stores selected DBus-writable control values so they
  survive restarts and can be driven via the Venus MQTT bridge

This split is intentional:

- structural settings such as backend types, hosts, credentials, and adapter
  wiring stay in `config.shelly_wallbox.ini`
- day-to-day runtime controls can be changed safely through DBus/MQTT without
  rewriting the structural installation config

Current persistent runtime-override paths include:

- `/Mode`
- `/AutoStart`
- `/SetCurrent`
- `/MinCurrent`
- `/MaxCurrent`
- `/PhaseSelection`
- `/Auto/StartSurplusWatts`
- `/Auto/StopSurplusWatts`
- `/Auto/MinSoc`
- `/Auto/ResumeSoc`
- `/Auto/StartDelaySeconds`
- `/Auto/StopDelaySeconds`
- `/Auto/DbusBackoffBaseSeconds`
- `/Auto/DbusBackoffMaxSeconds`
- `/Auto/GridRecoveryStartSeconds`
- `/Auto/StopSurplusDelaySeconds`
- `/Auto/StopSurplusVolatilityLowWatts`
- `/Auto/StopSurplusVolatilityHighWatts`
- `/Auto/ReferenceChargePowerWatts`
- `/Auto/LearnChargePowerEnabled`
- `/Auto/LearnChargePowerMinWatts`
- `/Auto/LearnChargePowerAlpha`
- `/Auto/LearnChargePowerStartDelaySeconds`
- `/Auto/LearnChargePowerWindowSeconds`
- `/Auto/LearnChargePowerMaxAgeSeconds`
- `/Auto/PhaseSwitching`
- `/Auto/PhasePreferLowestWhenIdle`
- `/Auto/PhaseUpshiftDelaySeconds`
- `/Auto/PhaseDownshiftDelaySeconds`
- `/Auto/PhaseUpshiftHeadroomWatts`
- `/Auto/PhaseDownshiftMarginWatts`
- `/Auto/PhaseMismatchRetrySeconds`
- `/Auto/PhaseMismatchLockoutCount`
- `/Auto/PhaseMismatchLockoutSeconds`
- `/Auto/ScheduledEnabledDays`
- `/Auto/ScheduledFallbackDelaySeconds`
- `/Auto/ScheduledLatestEndTime`
- `/Auto/ScheduledNightCurrent`

The active override state is visible on DBus via:

- `/Auto/RuntimeOverridesActive`
- `/Auto/RuntimeOverridesPath`

## Install on Cerbo GX / Venus OS

### One-File Bootstrap Install

For a minimal first deployment on a GX device, the user can copy only
[`install.sh`](install.sh)
to any directory under `/data` and execute it there.

Phase 1 of that bootstrap flow does this:

- checks for a local `noUpdate` marker next to the bootstrap script
- ensures a local bootstrap updater exists and refreshes it when possible
- materializes or refreshes the actual wallbox codebase in
  `${SCRIPT_DIR}/dbus-shelly-wallbox` by default
- preserves an existing `deploy/venus/config.shelly_wallbox.ini`
- excludes development-only content such as `tests/`, `docs/`, and
  `scripts/dev/`
- calls the existing
  [`deploy/venus/install_shelly_wallbox.sh`](deploy/venus/install_shelly_wallbox.sh)
  from the refreshed codebase

Useful bootstrap overrides:

- `SHELLY_WALLBOX_TARGET_DIR`
  Choose a different repository target directory.
- `SHELLY_WALLBOX_CHANNEL`
  Select another branch/channel than the default `main`.
- `SHELLY_WALLBOX_SOURCE_DIR`
  Let the updater sync from a local source tree instead of downloading.
- `SHELLY_WALLBOX_UPDATER_SOURCE`
  Override where the bootstrap fetches the updater from.
- `SHELLY_WALLBOX_UPDATER_HASH_SOURCE`
  Override where the bootstrap fetches the updater hash from.

Phase 2 adds an optional manifest-driven bundle flow on top:

- `SHELLY_WALLBOX_MANIFEST_SOURCE`
  Let the bootstrap/updater consume one manifest with bundle and updater hashes.
- manifest-driven updates skip work when the installed bundle hash already
  matches
- `deploy/venus/bootstrap_updater.sh` preserves `noUpdate`, `update-channel`,
  and the local Venus config while refreshing the codebase
- `deploy/venus/build_bootstrap_bundle.sh` can generate a release-style
  `wallbox-bundle.tar.gz` plus `bootstrap_manifest.json` for publishing

Phase 3 promotes manifest-based updates into a staged release layout:

- versioned bundles are unpacked into `releases/<version>/`
- `current/` is updated to the prepared release only after extraction finishes
- the bootstrap prefers `current/deploy/venus/install_shelly_wallbox.sh` when it
  exists
- the root-level bootstrap state and local preserve files stay outside the
  versioned release tree

Phase 4 adds the missing trust anchor for manifests:

- `bootstrap_manifest.json` can be accompanied by `bootstrap_manifest.json.sig`
- bootstrap and updater verify that detached signature with `openssl`
- the public key can be supplied via `SHELLY_WALLBOX_BOOTSTRAP_PUBKEY`
- the repo also carries a default public key in
  [`deploy/venus/bootstrap_manifest.pub`](deploy/venus/bootstrap_manifest.pub)
- `SHELLY_WALLBOX_REQUIRE_SIGNED_MANIFEST=1` makes signed manifests mandatory

Phase 5 hardens the flow for GX-style field deployments:

- bootstrap and updater now fail early with explicit messages when required
  shell tools are missing
- bootstrap can roll back from `current/` to `previous/` if the newly selected
  release installer fails
- updater preserves the previous release target during manifest-based
  promotions, so that rollback has a concrete last-known-good release

### Required Files on the Cerbo

Copy the entire repository content to the Cerbo, except for the
`tests/` directory and development helpers (`mypy.ini`, `Makefile`,
`scripts/dev/*`). Use as directory for example:

```bash
/data/shellyWB
```

Then run:

```bash
cd /data/shellyWB
./deploy/venus/install_shelly_wallbox.sh
```

The installer:
- restores executable bits
- registers the runit service under `/service/dbus-shelly-wallbox`
- makes sure `rc.local` calls the lightweight boot helper after reboot

Useful commands on the Cerbo:

```bash
svstat /service/dbus-shelly-wallbox
svc -t /service/dbus-shelly-wallbox
tail -f /var/volatile/log/dbus-shelly-wallbox/current
tail -f /var/volatile/log/dbus-shelly-wallbox/auto-reasons.log
```

### Backend And Charger Diagnostics

When split backends or a native charger backend are configured, the service now
publishes a few extra diagnostic paths that make field troubleshooting much
easier:

- `/Auto/BackendMode`
- `/Auto/MeterBackend`
- `/Auto/SwitchBackend`
- `/Auto/ChargerBackend`
- `/Auto/ChargerWriteErrors`
- `/Auto/ChargerCurrentTarget`
- `/Auto/ChargerCurrentTargetAge`

These values show which backend combination is actually active, whether charger
writes are failing, and which current target was last pushed to a native
charger backend.

If you configure a native charger backend such as `template_charger` or
`goe_charger`, the GUI
field `/SetCurrent` keeps showing the real configured/current charger target.
The learned-current display overlay is only used for relay-only setups where no
native current control exists.

The first concrete native charger backend is now `goe_charger`. It talks to the
documented local go-e HTTP API and supports native enable/disable, current
setpoints, and charger-state readback including power, energy, and high-level
status/fault signals. Its current implementation intentionally leaves native
phase writes disabled until go-e publishes a stable public API key for direct
phase switching.

There is now also a generic `modbus_charger` foundation for EVSEs that speak
Modbus over RS485 or Ethernet. It is intentionally layered into:

- transport in [modbus_transport.py](/home/martin/Schreibtisch/cerbo300126/vomCerbo/data/dbus-opendtuAndi/github/dbus-shelly-evcharger/shelly_wallbox/backend/modbus_transport.py)
- raw register reads/writes in [modbus_client.py](/home/martin/Schreibtisch/cerbo300126/vomCerbo/data/dbus-opendtuAndi/github/dbus-shelly-evcharger/shelly_wallbox/backend/modbus_client.py)
- EVSE register schema in [modbus_profiles.py](/home/martin/Schreibtisch/cerbo300126/vomCerbo/data/dbus-opendtuAndi/github/dbus-shelly-evcharger/shelly_wallbox/backend/modbus_profiles.py)
- normal charger backend surface in [modbus_charger.py](/home/martin/Schreibtisch/cerbo300126/vomCerbo/data/dbus-opendtuAndi/github/dbus-shelly-evcharger/shelly_wallbox/backend/modbus_charger.py)

The first profile is `Profile=generic`, configured directly from Modbus
register sections. Transport can be `serial_rtu`, `tcp`, or `udp`. A USB-RS485
stick and a VE.Direct/TTL-to-RS485 path both fit under the same
`serial_rtu` transport by pointing `Transport.Device` at the exposed serial
character device.

Minimal example:

```ini
[Adapter]
Type=modbus_charger
Profile=generic
Transport=tcp

[Transport]
Host=192.168.1.40
Port=502
UnitId=7

[EnableWrite]
RegisterType=coil
Address=20
TrueValue=1
FalseValue=0

[CurrentWrite]
RegisterType=holding
Address=30
DataType=uint16
Scale=10
```

The first concrete Modbus EVSE backend on top of that foundation is now
`simpleevse_charger`. It targets the documented SimpleEVSE WB/DIN register map
directly and assumes the published defaults: register `1000` for configured
amps, `1001` for actual amps, `1004` for runtime enable/disable, `1006` for
EVSE state, and `1007` for status/fault flags. Native phase switching is not
exposed there, so `simpleevse_charger` stays single-phase on the charger side
and relies on a separate switch backend if you want external phase switching.
For fixed one-/two-/three-phase SimpleEVSE installs without native phase
feedback, `[Capabilities] SupportedPhaseSelections=` may contain exactly one
configured layout such as `P1_P2_P3`. When no separate meter backend exists,
Venus power/energy values are then estimated from charger current, voltage, and
that configured phase count; DBus marks this via `/Auto/ChargerEstimate*`.

Minimal example:

```ini
[Adapter]
Type=simpleevse_charger
Transport=serial_rtu

[Capabilities]
SupportedPhaseSelections=P1_P2_P3

[Transport]
Device=/dev/ttyUSB0
Baudrate=9600
Parity=N
StopBits=1
UnitId=1
```

There is now also a first SmartEVSE-family Modbus backend: `smartevse_charger`.
It follows the documented SmartEVSE-2 Modbus register map conservatively:

- register `0x0000` for EVSE state
- register `0x0001` for error bits
- register `0x0002` for configured charging current
- register `0x0005` for the access bit used as enable/disable control

This first implementation keeps native phase switching disabled and reports
only `P1` on the charger side, so mixed or external phase switching should
still be handled through a separate switch backend.
For fixed one-/two-/three-phase SmartEVSE installs without native phase
feedback, `[Capabilities] SupportedPhaseSelections=` may contain exactly one
configured layout such as `P1_P2_P3`. In meterless setups the service can then
estimate Venus power/energy from charger current, voltage, and that configured
phase count, again marked through `/Auto/ChargerEstimate*`.

Minimal example:

```ini
[Adapter]
Type=smartevse_charger
Transport=serial_rtu

[Capabilities]
SupportedPhaseSelections=P1_P2_P3

[Transport]
Device=/dev/ttyUSB0
Baudrate=9600
Parity=N
StopBits=1
UnitId=1
```

For charger-native split setups, `MeterType=none` is now supported as long as a
`ChargerType` is configured. In that mode the service can synthesize its online
PM status from fresh charger readback instead of requiring a separate meter
backend.

`SwitchType=none` is also supported for charger-native split setups. In that
case enable/disable control is routed directly through the charger backend, so
no separate relay/switch adapter is required.

To validate the full backend combination from the main wallbox config before a
real service start, use:

```bash
python3 -m shelly_wallbox.backend.probe validate-wallbox deploy/venus/config.shelly_wallbox.ini
```

For `template_switch` adapters, `[StateResponse]` can now optionally expose
two additional booleans:

- `FeedbackClosedPath`
- `InterlockOkPath`

This is useful for contactor setups with an auxiliary feedback contact or an
external interlock/freigabe signal. When those paths are configured, the
service publishes the extra diagnostics:

- `/Auto/SwitchFeedbackClosed`
- `/Auto/SwitchInterlockOk`
- `/Auto/SwitchFeedbackMismatch`
- `/Auto/LastSwitchFeedbackAge`

All template backends (`template_meter`, `template_switch`, `template_charger`)
also support optional HTTP auth fields in `[Adapter]`:

- `Username`
- `Password`
- `DigestAuth`
- `AuthHeaderName`
- `AuthHeaderValue`

This covers the most common cases without teaching the wallbox core any
vendor-specific protocol details:

- Basic auth via `Username` + `Password`
- Digest auth via `Username` + `Password` + `DigestAuth=1`
- bearer/token headers via `AuthHeaderName=Authorization` and
  `AuthHeaderValue=Bearer ...`

For native `shelly_contactor_switch` setups you can now model the same signals
without a template adapter by adding optional `[Feedback]` and `[Interlock]`
sections to the switch backend config. Each section supports:

- `Component`
- `Id`
- `ValuePath`
- `Invert`

Typical example: an auxiliary contact on `Input` channel `7` with `ValuePath=state`.
The service reads these Shelly RPC states directly and feeds them into the same
health, DBus, summary, and audit diagnostics.

Native Shelly meter and switch backends also accept an optional
`[Adapter] ShellyProfile=...` preset so common Shelly families no longer need a
small template adapter just to pick the right component namespace. The current
wallbox-focused presets are:

- `switch_1ch`
- `switch_1ch_with_pm`
- `switch_multi_or_plug`
- `switch_or_cover_profile`
- `pm1_meter_only`
- `em1_meter_single_or_dual`
- `em_3phase_profiled`

A compact configuration matrix for these presets lives in
[SHELLY_PROFILES.md](/home/martin/Schreibtisch/cerbo300126/vomCerbo/data/dbus-opendtuAndi/github/dbus-shelly-evcharger/SHELLY_PROFILES.md).

These presets currently cover the relevant local RPC namespaces for wallbox
setups:

- `Switch.GetStatus`-based relays and plugs
- `PM1.GetStatus` meters
- `EM1.GetStatus` single-/dual-channel energy meters
- `EM.GetStatus` three-phase meters

You can still override `Component=` and `Id=` manually when a preset needs a
slight variant for your installation.

If each switched phase is handled by its own dedicated child adapter, use
`switch_group`. It acts as a pure coordinator and maps concrete child switch
configs to logical phases:

```ini
[Adapter]
Type=switch_group

[Members]
P1=phase1-switch.ini
P2=phase2-switch.ini
P3=phase3-switch.ini
```

Each child config is just a normal standalone switch backend, so you can mix
different implementations freely, for example:

- `template_switch` for one Shelly generation or external adapter
- `shelly_switch` for a direct native Shelly RPC relay
- `shelly_contactor_switch` when one phase is driven through a contactor path

The group backend keeps phase ownership explicit and testable:

- `P1` always means the child assigned to phase 1
- `P1_P2` enables the `P1` and `P2` children together
- `P1_P2_P3` enables all three children together

Optional `[Capabilities] SupportedPhaseSelections=...` can restrict the exposed
logical layouts to a subset of the configured members.

Example child config:

```ini
[Adapter]
Type=template_switch
BaseUrl=http://phase1.local

[StateRequest]
Url=/state

[StateResponse]
EnabledPath=enabled

[CommandRequest]
Url=/control
```

Auto mode can now also switch between supported phase selections
conservatively. The first implementation is intentionally simple:

- it only runs in Auto mode
- it switches stepwise between neighboring phase layouts
- it uses delay plus surplus hysteresis before changing phase count
- when a backend requires it, the existing relay-off pause and stabilization
  sequence is reused instead of forcing a second switching path

The corresponding tuning knobs live in
[`deploy/venus/config.shelly_wallbox.ini`](/home/martin/Schreibtisch/cerbo300126/vomCerbo/data/dbus-opendtuAndi/github/dbus-shelly-evcharger/deploy/venus/config.shelly_wallbox.ini):

- `AutoPhaseSwitching`
- `AutoPhaseUpshiftDelaySeconds`
- `AutoPhaseDownshiftDelaySeconds`
- `AutoPhaseUpshiftHeadroomWatts`
- `AutoPhaseDownshiftMarginWatts`
- `AutoPhaseMismatchRetrySeconds`
- `AutoPhaseMismatchLockoutCount`
- `AutoPhaseMismatchLockoutSeconds`
- `AutoPhasePreferLowestWhenIdle`
- `AutoContactorFaultLatchCount`
- `AutoContactorFaultLatchSeconds`

Once the service starts observing phase transitions, the following diagnostics
also help when a backend or topology does not switch as requested:

- `/Auto/RecoveryActive`
- `/Auto/FaultActive`
- `/Auto/FaultReason`
- `/Auto/PhaseObserved`
- `/Auto/PhaseMismatchActive`
- `/Auto/PhaseLockoutActive`
- `/Auto/PhaseLockoutTarget`
- `/Auto/PhaseLockoutReason`
- `/Auto/PhaseSupportedConfigured`
- `/Auto/PhaseSupportedEffective`
- `/Auto/PhaseDegradedActive`
- `/Auto/ContactorFaultCount`
- `/Auto/ContactorLockoutActive`
- `/Auto/ContactorLockoutReason`
- `/Auto/ContactorLockoutSource`
- `/Auto/ContactorLockoutReset`
- `/Auto/PhaseLockoutReset`

`/Auto/PhaseSupportedConfigured` shows the configured phase layouts, while
`/Auto/PhaseSupportedEffective` shows the currently usable subset after an
active mismatch lockout degraded the runtime capability. `/Auto/PhaseDegradedActive`
turns on while such degradation is active.

Operators can acknowledge and clear the current mismatch/lockout state by
writing `1` to `/Auto/PhaseLockoutReset`. That immediately removes the current
phase lockout, restores the effective supported set to the configured one, and
lets Auto try phase changes again.

Latched contactor lockouts and explicit contactor-feedback mismatches now also
drive the outward EV charger status into the fault state (`/Status = 0`) with a
matching `/Auto/StatusSource`, so they are visible as real EVSE-side faults and
not only as Auto-health diagnostics.

## Troubleshooting

### Service does not start

- Check the service state with `svstat /service/dbus-shelly-wallbox` and run `./deploy/venus/install_shelly_wallbox.sh` again to restore symlinks and executable bits.
- Then start it in the foreground once with `python3 ./dbus_shelly_wallbox.py` to see the real traceback immediately.

### EV charger tile does not appear in the GX GUI

- First verify the DBus service exists: `dbus -y com.victronenergy.evcharger.http_60 /Connected GetValue`
  Replace `http_60` with your configured `DeviceInstance`, for example `http_61` if `DeviceInstance=61`.
- If DBus works but the tile is still missing, restart the GUI with `svc -t /service/gui`

### Native charger or backend behavior is unclear

- Check the service log and the Auto audit log first:
  - `tail -f /var/volatile/log/dbus-shelly-wallbox/current`
  - `tail -f /var/volatile/log/dbus-shelly-wallbox/auto-reasons.log`
- Inspect the backend-related DBus paths listed above.
- If you use template adapters, validate them before enabling the service:

  ```bash
  python3 -m shelly_wallbox.backend.probe validate /data/etc/wallbox-meter.ini
  python3 -m shelly_wallbox.backend.probe validate /data/etc/wallbox-switch.ini
  python3 -m shelly_wallbox.backend.probe validate /data/etc/wallbox-charger.ini
  ```

## Project Structure

The codebase is now split into small packages with a few deliberately stable
flat anchors:

- `dbus_shelly_wallbox.py`: intentionally stable top-level service entrypoint
- `shelly_wallbox/bootstrap/controller.py` and `shelly_wallbox/controllers/state.py`: packaged controller sources for bootstrap and state management
- `shelly_wallbox/auto/workflow.py` and `shelly_wallbox/update/controller.py`: packaged workflow controllers for Auto and update logic
- `shelly_wallbox/app/`: package facades for the patch-friendly main and bootstrap entrypoints
- `shelly_wallbox/core/`: package facades for common helpers, contracts, and split-mixin typing contracts
- `shelly_wallbox/inputs/`: package slice for DBus input discovery, PV/battery/grid reads, helper supervision, and the auto-input helper internals
- `shelly_wallbox/publish/`: package slice for throttled DBus publishing helpers
- `shelly_wallbox/backend/`: first package slice for normalized meter/switch/charger backends
- `shelly_wallbox/auto/`: package slice for Auto policy and split workflow helpers
- `shelly_wallbox/bootstrap/`: package slice for bootstrap config, runtime wiring, and DBus path registration mixins
- `shelly_wallbox/controllers/`: package slice for reusable service controllers while patch-sensitive state remains a facade
- `shelly_wallbox/ports/`: package slice for typed controller-to-service forwarding ports
- `shelly_wallbox/service/`: package slice for service mixins and lazy controller factories
- `shelly_wallbox/runtime/`: package slice for runtime/watchdog setup, health, and audit helpers
- `shelly_wallbox/update/`: package slice for learned-power, relay, and session update helpers
- `shelly_wallbox/backend/shelly_io.py`: Shelly/network I/O seam isolated from the main service loop
- `shelly_wallbox_auto_input_helper.py`: helper-process entry anchor; its internals now live under `shelly_wallbox/inputs/helper/`
- `shelly_wallbox/ops/disable_generic_shelly_once.py`: standalone one-shot helper for installations that also run a generic `dbus-shelly` service
- `deploy/venus/`: Venus OS deployment material, including config template, service directory, and install/boot helpers
- `scripts/dev/`: local verification scripts
- `scripts/ops/`: operational helpers
- `shelly_wallbox/controllers/write_snapshot.py`: helper seam around write-state rollback snapshots

This structure keeps the service easier to read and test than a single large
monolithic script.

## Local Development

Useful local commands:

```bash
./scripts/dev/check_all.sh
./scripts/dev/run_typecheck.sh
./scripts/dev/run_stress_tests.sh
make check
make typecheck
make stress
```

These helpers are for development on a workstation and are not required for
deployment on the Cerbo.

## Notes

- The wallbox integration does not implement real EV charging current control.
  It switches a Shelly relay and publishes a compatible EV charger tile for
  Victron GUI integration.
- The service uses runtime state in RAM to avoid unnecessary flash wear.
- The example config is sanitized for GitHub. Replace all example addresses and
  identifiers before real deployment.
- Gen4 remains the primary tested direct-switch path, but the native Shelly
  backends now also expose config-selectable RPC-family presets for additional
  modern Shelly switch and meter devices such as `PM1`, `EM1`, and `EM`.

## Disclaimer

This project is provided as-is for personal and experimental use.
It is not a certified electrical installation product.
Always ensure your setup complies with local regulations and
has been reviewed by a qualified electrician.
