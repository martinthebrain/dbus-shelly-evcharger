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
- Helper-process based DBus input polling for better runtime stability
- Audit logging for Auto-mode decisions
- Watchdog and recovery behavior for stale helper snapshots or Shelly faults
- Works out-of-the-box with Home Assistant via the Venus OS MQTT bridge. All
  parameters can be monitored and controlled remotely by
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

## Install on Cerbo GX / Venus OS

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

If you configure a native charger backend such as `template_charger`, the GUI
field `/SetCurrent` keeps showing the real configured/current charger target.
The learned-current display overlay is only used for relay-only setups where no
native current control exists.

For charger-native split setups, `MeterType=none` is now supported as long as a
`ChargerType` is configured. In that mode the service can synthesize its online
PM status from fresh charger readback instead of requiring a separate meter
backend.

`SwitchType=none` is also supported for charger-native split setups. In that
case enable/disable control is routed directly through the charger backend, so
no separate relay/switch adapter is required.

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
- `AutoPhasePreferLowestWhenIdle`

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
- This script is specifically designed for Shelly Gen4 models. Older generations (Gen1-3) are not compatible due to different API paths and lower relay power ratings (10A vs 16A).

## Disclaimer

This project is provided as-is for personal and experimental use.
It is not a certified electrical installation product.
Always ensure your setup complies with local regulations and
has been reviewed by a qualified electrician.
