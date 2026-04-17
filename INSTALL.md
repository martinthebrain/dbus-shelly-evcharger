# Install

This guide covers the usual installation paths for Venus OS and Cerbo GX.

## Prerequisites

- GX device or other Venus OS system with writable `/data`
- Python 3 as shipped with current Venus OS versions
- network reachability to the selected meter, switch, charger, or Shelly device
- a free `DeviceInstance` for the EV charger service

## Standard Install

1. Copy the repository to a writable path under `/data`, for example:

   ```bash
   /data/shellyWB
   ```

2. Edit:

   ```bash
   deploy/venus/config.shelly_wallbox.ini
   ```

3. Set the core deployment values:

   - `Host`
   - `DeviceInstance`
   - `Phase`
   - `Username`, `Password`, `DigestAuth` when required
   - battery / PV / grid DBus service selectors when you pin them manually

4. Run the installer:

   ```bash
   cd /data/shellyWB
   ./deploy/venus/install_shelly_wallbox.sh
   ```

5. Restart the service:

   ```bash
   svc -t /service/dbus-shelly-wallbox
   ```

6. Verify the service:

   ```bash
   svstat /service/dbus-shelly-wallbox
   dbus -y com.victronenergy.evcharger.http_60 /Connected GetValue
   ```

## One-File Bootstrap Install

`install.sh` supports a small bootstrap flow for GX deployments.

Typical flow:

1. copy `install.sh` into a directory under `/data`
2. run it there
3. let it materialize the working tree
4. let it call the regular Venus installer from the refreshed tree

Bootstrap highlights:

- refreshes the local updater
- supports manifest-based updates
- supports detached-signature verification
- keeps release directories under `releases/<version>/`
- advances `current/` after the target release is ready
- keeps `previous/` available for rollback

Useful variables:

- `SHELLY_WALLBOX_TARGET_DIR`
- `SHELLY_WALLBOX_CHANNEL`
- `SHELLY_WALLBOX_SOURCE_DIR`
- `SHELLY_WALLBOX_MANIFEST_SOURCE`
- `SHELLY_WALLBOX_BOOTSTRAP_PUBKEY`
- `SHELLY_WALLBOX_REQUIRE_SIGNED_MANIFEST`

The full bootstrap and updater behavior, including `noUpdate`, is documented in
[UPDATE_FLOW.md](UPDATE_FLOW.md).

## Configuration Patterns

### Combined relay + meter

Good fit for a single backend that represents the visible wallbox path.

### Split relay + meter

Good fit when metering and switching come from different devices.

### Native charger

Good fit when the charger handles enable and current directly.

### Native charger + external phase switch

Good fit when current control and phase layout live in different devices.

Further backend examples live in [CHARGER_BACKENDS.md](CHARGER_BACKENDS.md) and
[SHELLY_PROFILES.md](SHELLY_PROFILES.md).

## Validation Before First Start

Validate the full wallbox configuration:

```bash
python3 -m shelly_wallbox.backend.probe validate-wallbox deploy/venus/config.shelly_wallbox.ini
```

Validate adapter files individually:

```bash
python3 -m shelly_wallbox.backend.probe validate /data/etc/wallbox-meter.ini
python3 -m shelly_wallbox.backend.probe validate /data/etc/wallbox-switch.ini
python3 -m shelly_wallbox.backend.probe validate /data/etc/wallbox-charger.ini
```

## After Installation

Useful commands:

```bash
svstat /service/dbus-shelly-wallbox
svc -t /service/dbus-shelly-wallbox
tail -f /var/volatile/log/dbus-shelly-wallbox/current
tail -f /var/volatile/log/dbus-shelly-wallbox/auto-reasons.log
```

Live diagnostics and DBus paths are collected in
[DIAGNOSTICS.md](DIAGNOSTICS.md).
