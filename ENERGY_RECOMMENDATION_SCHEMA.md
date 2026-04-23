# Energy Recommendation Schema

This document defines the versioned bundle format for external energy-source
recommendations.

## Purpose

Recommendation bundles are small handoff artifacts between:

- a vendor-specific probe or validation command
- the setup wizard
- future vendor integrations that want to suggest `AutoEnergySources=` entries

The goal is to keep that handoff explicit and versioned instead of relying on
implicit file contents.

## Bundle Files

For one bundle prefix `<prefix>`, the standard file set is:

- `<prefix>.manifest.json`
- `<prefix>.ini`
- `<prefix>.wizard.txt`
- `<prefix>.summary.txt`

The wizard still accepts older bundles that only contain the three text files,
but new bundle writers should always emit the manifest.

## Manifest Schema

Current schema:

- `schema_type`: required, must be `energy-recommendation-bundle`
- `schema_version`: required, must be `1`
- `source_id`: required, target `AutoEnergySource.<id>` name
- `profile`: required, recommended source profile
- `config_path`: required, recommended config path for the source adapter file
- `files`: required object with:
  - `config_snippet`
  - `wizard_hint`
  - `summary`

Example:

```json
{
  "schema_type": "energy-recommendation-bundle",
  "schema_version": 1,
  "source_id": "hybrid_ext",
  "profile": "huawei_mb_sdongle",
  "config_path": "/data/etc/huawei-mb-modbus.ini",
  "files": {
    "config_snippet": "/data/tmp/huawei-rec.ini",
    "wizard_hint": "/data/tmp/huawei-rec.wizard.txt",
    "summary": "/data/tmp/huawei-rec.summary.txt"
  }
}
```

## Text File Roles

- `.ini`
  Copy-pasteable config block for the main wallbox config
- `.wizard.txt`
  Short operator-facing hint block for wizard or ticket output
- `.summary.txt`
  One compact summary string

## Structured Source Expectations

The `.ini` file should contain one `AutoEnergySource.<source_id>.*` block. The
wizard currently extracts these fields when present:

- `Profile`
- `ConfigPath`
- `Host`
- `Port`
- `UnitId`
- optional `UsableCapacityWh`

## Versioning Rule

- additive, backward-compatible bundle changes should increment the manifest
  contents while keeping `schema_version = 1`
- incompatible changes must use a new schema version
- readers must reject unknown schema versions instead of guessing
