# Shelly Wallbox for Victron Venus OS

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

## Quick Start

1. Copy the wallbox files to your Cerbo, for example to `/data/shellyWB`.
2. Edit `config.shelly_wallbox.ini` and replace the example values with your
   real Shelly host, device instance, phase, and optional DBus service names.
3. Install the service:

   ```bash
   cd /data/shellyWB
   ./install_shelly_wallbox.sh
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

## What This Repository Contains

This repository is intended to be published and deployed as a wallbox-only
codebase.

The important runtime files are:
- `dbus_shelly_wallbox.py`: main service entry point
- `dbus_shelly_wallbox_bootstrap.py`: config loading, DBus path setup, service bootstrap
- `dbus_shelly_wallbox_auto_logic.py`: Auto-mode decision logic
- `dbus_shelly_wallbox_auto_policy.py`: structured Auto-mode thresholds and smoothing policy
- `dbus_shelly_wallbox_update_cycle.py`: main update loop logic
- `dbus_shelly_wallbox_write_controller.py`: Victron GUI write handling
- `dbus_shelly_wallbox_runtime_support.py`: runtime caches, watchdog, audit log helpers
- `dbus_shelly_wallbox_shelly_io.py`: Shelly HTTP access and relay worker logic
- `shelly_wallbox_auto_input_helper.py`: separate helper process for PV, grid, and battery DBus inputs
- `config.shelly_wallbox.ini`: documented example configuration
- `service_shelly_wallbox/`: runit service scripts for Venus OS
- `boot_shelly_wallbox.sh`, `install_shelly_wallbox.sh`, `restart_shelly_wallbox.sh`, `uninstall_shelly_wallbox.sh`: deployment helpers

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

The wallbox uses `config.shelly_wallbox.ini`.

This file is written as a documented example template and contains only generic
placeholder values so it can be committed to GitHub safely.

Before deploying, replace at least:
- `Host`
- `DeviceInstance`
- `Phase`
- any explicit battery or PV service names if you pin them manually
- optional generic Shelly disable helper values

The comments in the config explain each group of settings:
- service identity
- Shelly access
- startup behavior
- PV / battery / grid input selection
- Auto-mode thresholds
- high-SOC profile
- stop smoothing
- helper / watchdog behavior
- audit logging

## Install on Cerbo GX / Venus OS

### Required Files on the Cerbo

For a real deployment on Venus OS / Cerbo GX, these files and folders are the
important ones to copy:

- `dbus_shelly_wallbox.py`
- `dbus_shelly_wallbox_auto_controller.py`
- `dbus_shelly_wallbox_auto_input_supervisor.py`
- `dbus_shelly_wallbox_auto_logic.py`
- `dbus_shelly_wallbox_auto_policy.py`
- `dbus_shelly_wallbox_bootstrap.py`
- `dbus_shelly_wallbox_common.py`
- `dbus_shelly_wallbox_dbus_inputs.py`
- `dbus_shelly_wallbox_ports.py`
- `dbus_shelly_wallbox_publisher.py`
- `dbus_shelly_wallbox_runtime_support.py`
- `dbus_shelly_wallbox_service_auto.py`
- `dbus_shelly_wallbox_service_bindings.py`
- `dbus_shelly_wallbox_service_factory.py`
- `dbus_shelly_wallbox_service_runtime.py`
- `dbus_shelly_wallbox_service_state_publish.py`
- `dbus_shelly_wallbox_service_update.py`
- `dbus_shelly_wallbox_shared.py`
- `dbus_shelly_wallbox_shelly_io.py`
- `dbus_shelly_wallbox_state.py`
- `dbus_shelly_wallbox_update_cycle.py`
- `dbus_shelly_wallbox_write_controller.py`
- `shelly_wallbox_auto_input_helper.py`
- `disable_generic_shelly_once.py`
- `config.shelly_wallbox.ini`
- `version.txt`
- `boot_shelly_wallbox.sh`
- `install_shelly_wallbox.sh`
- `restart_shelly_wallbox.sh`
- `uninstall_shelly_wallbox.sh`
- `service_shelly_wallbox/`

Optional but useful on the Cerbo:
- `cerbo_soak_check.sh`

Not required on the Cerbo for runtime operation:
- `tests/`
- `mypy.ini`
- `run_typecheck.sh`
- `run_stress_tests.sh`
- `check_all.sh`
- `Makefile`

Copy the wallbox runtime files to a directory on the Cerbo, for example:

```bash
/data/shellyWB
```

Then run:

```bash
cd /data/shellyWB
./install_shelly_wallbox.sh
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

## Troubleshooting

### Service does not start

- Check the service state with `svstat /service/dbus-shelly-wallbox` and run `./install_shelly_wallbox.sh` again to restore symlinks and executable bits.
- Then start it in the foreground once with `python3 ./dbus_shelly_wallbox.py` to see the real traceback immediately.

### EV charger tile does not appear in the GX GUI

- First verify the DBus service exists: `dbus -y com.victronenergy.evcharger.http_60 /Connected GetValue`
  Replace `http_60` with your configured `DeviceInstance`, for example `http_61` if `DeviceInstance=61`.
- If DBus works but the tile is still missing, restart the GUI with `svc -t /service/gui`

## Project Structure

The codebase is split into small modules on purpose:

- `dbus_shelly_wallbox_*controller.py`: controller entry points for a single responsibility
- `dbus_shelly_wallbox_service_*.py`: mixins used by the service class
- `dbus_shelly_wallbox_ports.py`: small typed forwarding ports from controllers to the service object
- `dbus_shelly_wallbox_common.py` and `dbus_shelly_wallbox_shared.py`: common helper functions
- `dbus_shelly_wallbox_auto_policy.py`: policy dataclasses used across bootstrap, validation, and runtime logic

This structure keeps the service easier to read and test than a single large
monolithic script.

## Local Development

Useful local commands:

```bash
./check_all.sh
./run_typecheck.sh
./run_stress_tests.sh
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

## Disclaimer

This project is provided as-is for personal and experimental use.
It is not a certified electrical installation product. 
Always ensure your setup complies with local regulations and 
has been reviewed by a qualified electrician.
