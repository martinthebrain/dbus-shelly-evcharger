# Update Flow

This guide explains how the GX bootstrap and updater flow works, what it
updates, and how `noUpdate` changes that behavior.

## Goal

The update flow is designed for a simple first deployment on a GX device:

- place a small bootstrap script under `/data`
- run it there
- let it materialize or refresh the wallbox tree
- let it start the regular Venus installer from the refreshed tree

This keeps the first install lightweight while still supporting repeatable
updates and rollback-ready release directories.

The running service uses the same bootstrap entrypoint for remote availability
checks, DBus-triggered refresh runs, and the delayed post-boot update pass.

## Main Pieces

### `install.sh`

The bootstrap entrypoint.

Responsibilities:

- determine the local working directory
- check for `noUpdate`
- ensure a local updater is available
- refresh the codebase when updates are enabled
- run the regular Venus installer from the resulting tree

The service calls this same script when a user triggers an update through DBus
or MQTT.

### `deploy/venus/bootstrap_updater.sh`

The updater that fetches or refreshes the wallbox tree.

Responsibilities:

- read update inputs from local sources or manifests
- refresh the target tree
- preserve local deployment files
- prepare versioned release directories
- keep rollback targets available

### `deploy/venus/install_shelly_wallbox.sh`

The regular Venus installer.

Responsibilities:

- restore executable bits
- register the runit service
- refresh the service wiring
- complete the local deployment

## What Gets Updated

The updater refreshes the wallbox codebase in the selected target directory.

That includes:

- Python service code
- deployment scripts
- runit service files
- documentation
- backend and policy code

The service checks for newer releases once per week. The default flow prefers
the bootstrap manifest and falls back to `version.txt` when needed.

When `noUpdate` is absent, the service also schedules one update run about one
hour after a GX reboot.

The updater also supports staged release layouts such as:

- `releases/<version>/`
- `current/`
- `previous/`

## What Stays Local

The updater keeps the local deployment shape intact where it matters for GX
operation.

Typical preserved items include:

- the local wallbox config
- channel markers such as `update-channel`
- the release pointers used for rollback

## Manifest-Based Updates

The bootstrap can consume a manifest-driven update flow.

Typical inputs:

- bundle location
- bundle hash
- updater location
- updater hash
- detached manifest signature

This allows one small bootstrap script to bring in a full prepared release.

## Signed Manifests

The update flow supports detached signatures for manifests through `openssl`.

Useful inputs:

- `SHELLY_WALLBOX_MANIFEST_SOURCE`
- `SHELLY_WALLBOX_BOOTSTRAP_PUBKEY`
- `SHELLY_WALLBOX_REQUIRE_SIGNED_MANIFEST`

This makes it possible to require a signed manifest before a refresh is
accepted.

## Release Layout And Rollback

The updater can prepare a versioned release directory first and then move the
runtime pointer to the new release.

Typical layout:

- `releases/1.0.0/`
- `releases/1.1.0/`
- `current/`
- `previous/`

If the freshly selected installer fails, the bootstrap can start the installer
from `previous/` and restore the last known good release path.

## `noUpdate`

Place a file named `noUpdate` next to the bootstrap script to freeze the local
installation at its current code state.

When `noUpdate` is present:

- the bootstrap skips the updater phase
- the runtime service reports update blocking on DBus
- manual DBus and MQTT update requests are rejected
- the local code tree is used as-is
- the regular Venus installer still runs from the local tree

This is useful when:

- a system should stay on a pinned code version
- field testing should continue without refreshes
- you want to inspect or patch the local tree manually before allowing updates

## Typical Update Variables

- `SHELLY_WALLBOX_TARGET_DIR`
- `SHELLY_WALLBOX_CHANNEL`
- `SHELLY_WALLBOX_SOURCE_DIR`
- `SHELLY_WALLBOX_UPDATER_SOURCE`
- `SHELLY_WALLBOX_UPDATER_HASH_SOURCE`
- `SHELLY_WALLBOX_MANIFEST_SOURCE`
- `SHELLY_WALLBOX_BOOTSTRAP_PUBKEY`
- `SHELLY_WALLBOX_REQUIRE_SIGNED_MANIFEST`

## Practical Examples

### Regular bootstrap run

```bash
cd /data/bootstrap-wallbox
./install.sh
```

### Freeze updates with `noUpdate`

```bash
cd /data/bootstrap-wallbox
touch noUpdate
./install.sh
```

### Use a local source tree

```bash
SHELLY_WALLBOX_SOURCE_DIR=/data/src/dbus-shelly-evcharger ./install.sh
```

### Trigger an update through DBus or MQTT

Write `1` to:

- `/Auto/SoftwareUpdateRun`

Useful companion paths:

- `/Auto/SoftwareUpdateAvailable`
- `/Auto/SoftwareUpdateState`
- `/Auto/SoftwareUpdateStateCode`
- `/Auto/SoftwareUpdateDetail`
- `/Auto/SoftwareUpdateCurrentVersion`
- `/Auto/SoftwareUpdateAvailableVersion`
- `/Auto/SoftwareUpdateNoUpdateActive`

`/Auto/SoftwareUpdateState` uses a fixed outward vocabulary. A particularly
useful value is `available-blocked`, which means the service found a newer
release and the local `noUpdate` marker currently blocks installation.

`installed` means the update run completed successfully and the service
initiated the restart handoff. The next service instance starts again from
`idle`, so `installed` is a transient completion state rather than a statement
that the currently running process is already the new version.

## Where To Read Next

- [INSTALL.md](INSTALL.md)
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- [DIAGNOSTICS.md](DIAGNOSTICS.md)
