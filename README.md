# Venus OS EV Charger Service

[![CI](https://github.com/martinthebrain/venus-evcharger-service/actions/workflows/ci.yml/badge.svg)](https://github.com/martinthebrain/venus-evcharger-service/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/martinthebrain/venus-evcharger-service/graph/badge.svg)](https://codecov.io/gh/martinthebrain/venus-evcharger-service)

`venus-evcharger-service` exposes an EV charging setup as a Victron Venus OS EV
charger service with DBus integration, GUI tile support, Auto logic, Scheduled
logic, and GX-friendly deployment.

It is meant for real-world setups that are not a single fixed wallbox:
portable EVSEs behind relays, Shelly-based switching, native chargers such as
go-e, split meter/switch/charger topologies, and external energy sources such
as hybrid inverters.

## What You Get

- EV charger service on DBus for Venus OS / Cerbo GX
- Manual, Auto, and Scheduled charging modes
- DBus and MQTT runtime control
- optional local HTTP Control API
- Shelly, HTTP template, Modbus, and native charger backend families
- optional external phase switching
- diagnostics for runtime, faults, retries, recovery, scheduling, and topology
- installer, updater, bootstrap, and setup wizard for `/data` deployments

## Typical Setups

| Setup | Main idea |
| --- | --- |
| Shelly relay + charging brick | Victron switches the charging path of a portable EVSE |
| Native charger | Charger handles enable/current directly |
| Native charger + external phase switch | Charger controls current, external relays control phases |
| Multi-device wallbox | Meter, switch, charger, and feedback come from different devices |
| External energy sources | Battery / hybrid inverter data augments Auto mode |

## Charging Modes

### Manual

`/Mode = 0`

- direct start/stop from the Victron EV charger tile
- outward state mirrors the real relay or charger state

### Auto

`/Mode = 1`

Auto mode can combine:

- PV surplus
- grid import/export
- battery SOC
- optional aggregated external energy sources
- start/stop delays
- minimum runtime and off-time
- optional high-SOC profile
- learned charge-power behavior
- optional automatic phase switching

### Scheduled

`/Mode = 2`

Scheduled mode reuses Auto policy and adds daytime / night-boost behavior for
selected target days.

## Quick Start

1. Copy the repository to a writable path under `/data`, for example
   `/data/venus-evcharger-service`.
2. Edit [deploy/venus/config.venus_evcharger.ini](deploy/venus/config.venus_evcharger.ini).
3. Install the service:

   ```bash
   cd /data/venus-evcharger-service
   ./deploy/venus/install_venus_evcharger_service.sh
   ```

4. Optional for first setup or reconfiguration:

   ```bash
   ./deploy/venus/configure_venus_evcharger_service.sh
   ```

5. Restart the service:

   ```bash
   svc -t /service/dbus-venus-evcharger
   ```

6. Verify the service:

   ```bash
   svstat /service/dbus-venus-evcharger
   dbus -y com.victronenergy.evcharger.http_60 /Connected GetValue
   ```

## External Energy Sources

The service can combine additional battery-like or hybrid-inverter-like energy
sources for Auto mode. Supported connector families include:

- `dbus`
- `template_http`
- `modbus`
- `command_json`
- `opendtu_http`

Named profiles are available for generic sources and for Huawei family
variants. OpenDTU can also be read directly through the service via
`opendtu-pvinverter`, without a separate OpenDTU-to-DBus bridge. For
configuration details and examples, see
[CONFIGURATION.md](CONFIGURATION.md). For the Huawei-specific operator guide,
see [HUAWEI_INTEGRATION.md](HUAWEI_INTEGRATION.md).

## Control Surfaces

- DBus: primary Venus OS integration surface
- MQTT: mirrors the published runtime paths through the Venus MQTT bridge
- HTTP: optional local Control API for process-local automation

The stable control contract lives in [CONTROL_API.md](CONTROL_API.md). The
stable read/state contract lives in [STATE_API.md](STATE_API.md).

<!-- BEGIN:README_LOCAL_HTTP_CONTROL_API_GETTING_STARTED -->
Quick start:

- `python3 ./venus_evchargerctl.py --token READ-TOKEN health`
- `python3 ./venus_evchargerctl.py --token READ-TOKEN doctor`
- `python3 ./venus_evchargerctl.py --token READ-TOKEN capabilities`
- `python3 ./venus_evchargerctl.py --token READ-TOKEN state summary`
- `python3 ./venus_evchargerctl.py --token CONTROL-TOKEN safe-write set-mode 1`
- `python3 ./venus_evchargerctl.py --unix-socket /run/venus-evcharger-control.sock --token READ-TOKEN watch --kind command --once`

For direct HTTP usage, `curl` snippets, optimistic concurrency with `If-Match`,
and a small Python example, see [CONTROL_API.md](CONTROL_API.md).
For a short local developer runbook on a normal PC, see
[DEV_API_WORKFLOW.md](DEV_API_WORKFLOW.md).
<!-- END:README_LOCAL_HTTP_CONTROL_API_GETTING_STARTED -->

## Backends

Main backend groups:

- Shelly relay / meter / switch backends
- template adapters for custom HTTP-based integrations
- native charger backends such as `goe_charger`, `simpleevse_charger`,
  `smartevse_charger`, and `modbus_charger`
- `switch_group` for external phase-switch coordination

Backend-specific notes and starter mappings live in
[CHARGER_BACKENDS.md](CHARGER_BACKENDS.md) and
[SHELLY_PROFILES.md](SHELLY_PROFILES.md).

## Where To Look Next

- [INSTALL.md](INSTALL.md): installation walkthrough
- [CONFIGURATION.md](CONFIGURATION.md): main config guide
- [CONTROL_API.md](CONTROL_API.md): HTTP control API
- [STATE_API.md](STATE_API.md): stable outward state contract
- [HUAWEI_INTEGRATION.md](HUAWEI_INTEGRATION.md): Huawei setup guide
- [DIAGNOSTICS.md](DIAGNOSTICS.md): operations and diagnostics
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md): field debugging
- [UPDATE_FLOW.md](UPDATE_FLOW.md): bootstrap and updater flow
- [CONTRIBUTING.md](CONTRIBUTING.md): contributor guidance

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
