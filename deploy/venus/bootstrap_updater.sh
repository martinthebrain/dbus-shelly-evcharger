#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

# Local updater used by the standalone GX bootstrap installer.
#
# The updater can:
# - materialize or refresh a working repository tree under the target directory
# - preserve and additively merge the user-edited Venus config
# - validate the resulting config before activating a refreshed tree
# - keep versioned release directories and rollback pointers ready

set -eu

usage() {
    echo "Usage: $0 [--dry-run|--preview] <target-dir>" >&2
    exit 1
}

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ] || [ "${1:-}" = "--preview" ]; then
    DRY_RUN=1
    shift
fi

if [ "${1:-}" = "" ] || [ "${2:-}" != "" ]; then
    usage
fi

TARGET_DIR="$1"
DEFAULT_REPO_SLUG="martinthebrain/venus-evcharger-service"
DEFAULT_CHANNEL="main"
REPO_SLUG="${VENUS_EVCHARGER_REPO_SLUG:-$DEFAULT_REPO_SLUG}"
CHANNEL="${VENUS_EVCHARGER_CHANNEL:-$DEFAULT_CHANNEL}"
SOURCE_DIR_OVERRIDE="${VENUS_EVCHARGER_SOURCE_DIR:-}"
MANIFEST_SOURCE="${VENUS_EVCHARGER_MANIFEST_SOURCE:-}"
if [ -n "${VENUS_EVCHARGER_MANIFEST_SIG_SOURCE:-}" ]; then
    MANIFEST_SIG_SOURCE="$VENUS_EVCHARGER_MANIFEST_SIG_SOURCE"
elif [ -n "$MANIFEST_SOURCE" ]; then
    MANIFEST_SIG_SOURCE="${MANIFEST_SOURCE}.sig"
else
    MANIFEST_SIG_SOURCE=""
fi
BOOTSTRAP_PUBKEY_OVERRIDE="${VENUS_EVCHARGER_BOOTSTRAP_PUBKEY:-}"
REQUIRE_SIGNED_MANIFEST="${VENUS_EVCHARGER_REQUIRE_SIGNED_MANIFEST:-0}"
ARCHIVE_URL="${VENUS_EVCHARGER_ARCHIVE_URL:-https://codeload.github.com/${REPO_SLUG}/tar.gz/refs/heads/${CHANNEL}}"
STATE_DIR="${TARGET_DIR}/.bootstrap-state"
INSTALLED_BUNDLE_HASH_FILE="${STATE_DIR}/installed_bundle_sha256"
INSTALLED_VERSION_FILE="${STATE_DIR}/installed_version"
STATUS_FILE="${STATE_DIR}/update_status.json"
AUDIT_LOG_FILE="${STATE_DIR}/update_audit.log"
RELEASES_DIR="${TARGET_DIR}/releases"
CURRENT_LINK="${TARGET_DIR}/current"
PREVIOUS_LINK="${TARGET_DIR}/previous"
TMP_DIR=""

RUN_MODE="apply"
[ "$DRY_RUN" = "1" ] && RUN_MODE="dry-run"
RUN_RESULT="failed"
RUN_FAILURE_REASON=""
RUN_OLD_VERSION=""
RUN_NEW_VERSION=""
RUN_OLD_BUNDLE_SHA256=""
RUN_NEW_BUNDLE_SHA256=""
CURRENT_PRESERVED=0
CURRENT_ALREADY_MATCHED=0
PROMOTED_RELEASE=""
PROMOTION_ABORTED_REASON=""
ROLLBACK_REASON=""
VALIDATION_PASSED=0
CONFIG_VALIDATION_MODE="not-run"
CONFIG_MERGE_CHANGED=0
CONFIG_MERGE_COMMENT_PRESERVED=1
CONFIG_MERGE_SKIPPED_REASON=""
CONFIG_MERGE_BACKUP_PATH=""
CONFIG_MERGE_BACKUP_REQUIRED=0
CONFIG_SCHEMA_BEFORE=""
CONFIG_SCHEMA_TARGET=""
CONFIG_MERGE_ADDED_KEYS=""
CONFIG_MERGE_ADDED_SECTIONS=""
CONFIG_MIGRATIONS_APPLIED=""

export TARGET_DIR RUN_MODE RUN_RESULT RUN_FAILURE_REASON
export RUN_OLD_VERSION RUN_NEW_VERSION RUN_OLD_BUNDLE_SHA256 RUN_NEW_BUNDLE_SHA256
export CURRENT_PRESERVED CURRENT_ALREADY_MATCHED PROMOTED_RELEASE PROMOTION_ABORTED_REASON ROLLBACK_REASON
export VALIDATION_PASSED CONFIG_VALIDATION_MODE
export CONFIG_MERGE_CHANGED CONFIG_MERGE_COMMENT_PRESERVED CONFIG_MERGE_SKIPPED_REASON
export CONFIG_MERGE_BACKUP_PATH CONFIG_MERGE_BACKUP_REQUIRED CONFIG_SCHEMA_BEFORE CONFIG_SCHEMA_TARGET
export CONFIG_MERGE_ADDED_KEYS CONFIG_MERGE_ADDED_SECTIONS CONFIG_MIGRATIONS_APPLIED

log() {
    printf '%s\n' "[updater] $*" >&2
}

set_failure_reason_once() {
    if [ -z "$RUN_FAILURE_REASON" ]; then
        RUN_FAILURE_REASON="$1"
    fi
}

require_command() {
    command_name="$1"
    if ! command -v "$command_name" >/dev/null 2>&1; then
        log "Required command is missing: $command_name"
        set_failure_reason_once "missing-command:${command_name}"
        exit 1
    fi
}

ensure_download_tool() {
    if command -v wget >/dev/null 2>&1 || command -v curl >/dev/null 2>&1; then
        return 0
    fi
    log "Neither wget nor curl is available"
    set_failure_reason_once "missing-download-tool"
    exit 1
}

ensure_updater_prereqs() {
    require_command cp
    require_command rm
    require_command mv
    require_command ln
    require_command mkdir
    require_command chmod
    require_command awk
    require_command mktemp
    require_command tar
    require_command sha256sum
    require_command python3
    if [ -z "$SOURCE_DIR_OVERRIDE" ]; then
        ensure_download_tool
    fi
    if [ "$REQUIRE_SIGNED_MANIFEST" = "1" ]; then
        require_command openssl
    fi
}

download_to() {
    src="$1"
    dst="$2"
    if [ -f "$src" ]; then
        cp "$src" "$dst"
        return 0
    fi
    if command -v wget >/dev/null 2>&1; then
        wget -q -O "$dst" "$src"
        return 0
    fi
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$dst" "$src"
        return 0
    fi
    log "Neither wget nor curl is available to fetch $src"
    set_failure_reason_once "download-unavailable"
    return 1
}

require_source_layout() {
    src_dir="$1"
    [ -f "${src_dir}/venus_evcharger_service.py" ] || return 1
    [ -f "${src_dir}/venus_evcharger_auto_input_helper.py" ] || return 1
    [ -f "${src_dir}/deploy/venus/install_venus_evcharger_service.sh" ] || return 1
    [ -d "${src_dir}/venus_evcharger" ] || return 1
    return 0
}

write_builtin_pubkey() {
    destination="$1"
    cat > "$destination" <<'EOF'
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAsWdKDZgN3QdCJ9VXsbk6
xEwJ/8l92kxAyNgLWJ6QwgvusA8mTKEpYfYLoKszqVCiO8nH4O8/MYrOAXqpwfa9
er2lBaIiUhvbuzUKKlfz5iq7hJ7/G2jWvTizUpY1NtwT0LY2hm9xELfbzintKK9r
Gpd1QLxbJ2b7X4K1l+I/3DsoH59dbLUGP4yQgGH0x0vO3tgULKu/oVKp2bEjae9i
ukU9eZio9Yry5YsFwSnuqfiLO5frFXt8Jeikf24vQTGz5bjG1kjQTYDGVO/4WLPj
graKJ4MBJXTsEs4Gy7kcSRDMfc4CvziUx9he8FI34j/qT3eQ9A1Fi9Sfti3dCZB7
FwIDAQAB
-----END PUBLIC KEY-----
EOF
}

resolve_pubkey_path() {
    if [ -n "$BOOTSTRAP_PUBKEY_OVERRIDE" ] && [ -f "$BOOTSTRAP_PUBKEY_OVERRIDE" ]; then
        printf '%s\n' "$BOOTSTRAP_PUBKEY_OVERRIDE"
        return 0
    fi
    sibling_pubkey="$(cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)/bootstrap_manifest.pub"
    if [ -f "$sibling_pubkey" ]; then
        printf '%s\n' "$sibling_pubkey"
        return 0
    fi
    if [ -n "${TMP_DIR:-}" ]; then
        pubkey_path="${TMP_DIR}/bootstrap_manifest.pub"
        mkdir -p "$TMP_DIR"
    else
        pubkey_path="${STATE_DIR}/bootstrap_manifest.pub"
        mkdir -p "$STATE_DIR"
    fi
    write_builtin_pubkey "$pubkey_path"
    printf '%s\n' "$pubkey_path"
}

verify_signature() {
    manifest_path="$1"
    signature_path="$2"
    pubkey_path="$3"
    command -v openssl >/dev/null 2>&1 || return 1
    openssl dgst -sha256 -verify "$pubkey_path" -signature "$signature_path" "$manifest_path" >/dev/null 2>&1
}

json_field() {
    json_path="$1"
    field_name="$2"
    python3 - "$json_path" "$field_name" <<'PY'
import json
import sys

path, field = sys.argv[1], sys.argv[2]
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    sys.exit(1)
value = data.get(field, "")
if value is None:
    sys.exit(0)
if isinstance(value, bool):
    print("1" if value else "0")
elif isinstance(value, (str, int, float)):
    print(value)
PY
}

json_lines_field() {
    json_path="$1"
    field_name="$2"
    python3 - "$json_path" "$field_name" <<'PY'
import json
import sys

path, field = sys.argv[1], sys.argv[2]
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    sys.exit(1)
value = data.get(field, [])
if isinstance(value, list):
    for item in value:
        if item is None:
            continue
        print(str(item))
PY
}

manifest_field() {
    json_field "$1" "$2"
}

read_text_file() {
    file_path="$1"
    if [ -f "$file_path" ]; then
        awk 'NF { print; exit }' "$file_path"
    fi
}

normalize_version_value() {
    raw_value="$1"
    printf '%s\n' "$raw_value" | awk '
        NF {
            line=$0
            sub(/^[Vv]ersion:[[:space:]]*/, "", line)
            print line
            exit
        }
    '
}

read_tree_version() {
    root_dir="$1"
    raw_version=$(read_text_file "${root_dir}/version.txt" || true)
    normalize_version_value "$raw_version"
}

detect_current_version() {
    current_version=$(read_text_file "$INSTALLED_VERSION_FILE" || true)
    if [ -n "$current_version" ]; then
        normalize_version_value "$current_version"
        return 0
    fi
    active_dir=$(current_codebase_dir)
    read_tree_version "$active_dir" || true
}

detect_current_bundle_hash() {
    read_text_file "$INSTALLED_BUNDLE_HASH_FILE" || true
}

normalize_multiline_var() {
    input_value="$1"
    printf '%s' "$input_value" | awk 'NF {print}'
}

load_manifest() {
    manifest_path="$1"
    signature_path="${manifest_path}.sig"
    [ -n "$MANIFEST_SOURCE" ] || return 1
    download_to "$MANIFEST_SOURCE" "$manifest_path" || return 1
    pubkey_path=$(resolve_pubkey_path)
    if download_to "$MANIFEST_SIG_SOURCE" "$signature_path"; then
        if ! verify_signature "$manifest_path" "$signature_path" "$pubkey_path"; then
            log "Manifest signature verification failed"
            set_failure_reason_once "manifest-signature-verification-failed"
            return 1
        fi
    elif [ "$REQUIRE_SIGNED_MANIFEST" = "1" ]; then
        log "Signed manifest required but signature could not be fetched"
        set_failure_reason_once "manifest-signature-missing"
        return 1
    else
        return 1
    fi

    MANIFEST_BUNDLE_URL=$(manifest_field "$manifest_path" "bundle_url" || true)
    MANIFEST_BUNDLE_SHA256=$(manifest_field "$manifest_path" "bundle_sha256" || true)
    MANIFEST_VERSION=$(manifest_field "$manifest_path" "version" || true)
    MANIFEST_CHANNEL=$(manifest_field "$manifest_path" "channel" || true)

    [ -n "$MANIFEST_BUNDLE_URL" ] || return 1
    [ -n "$MANIFEST_BUNDLE_SHA256" ] || return 1
    return 0
}

current_codebase_dir() {
    if [ -L "$CURRENT_LINK" ] || [ -d "$CURRENT_LINK" ]; then
        printf '%s\n' "$CURRENT_LINK"
        return 0
    fi
    printf '%s\n' "$TARGET_DIR"
}

target_is_current_for_manifest() {
    [ -f "$INSTALLED_BUNDLE_HASH_FILE" ] || return 1
    [ -n "${MANIFEST_BUNDLE_SHA256:-}" ] || return 1
    current_hash=$(awk 'NF {print $1; exit}' "$INSTALLED_BUNDLE_HASH_FILE")
    [ "$current_hash" = "$MANIFEST_BUNDLE_SHA256" ] || return 1
    active_dir=$(current_codebase_dir)
    require_source_layout "$active_dir"
}

copy_item() {
    src_root="$1"
    dst_root="$2"
    rel_path="$3"
    src_path="${src_root}/${rel_path}"
    dst_path="${dst_root}/${rel_path}"

    if [ ! -e "$src_path" ]; then
        return 0
    fi

    rm -rf "$dst_path"
    mkdir -p "$(dirname "$dst_path")"
    if [ -d "$src_path" ]; then
        cp -R "$src_path" "$dst_path"
    else
        cp "$src_path" "$dst_path"
    fi
}

managed_layout_paths() {
    printf '%s\n' \
        install.sh \
        LICENSE \
        README.md \
        SHELLY_PROFILES.md \
        version.txt \
        venus_evcharger_service.py \
        venus_evcharger_auto_input_helper.py \
        deploy/venus \
        venus_evcharger \
        scripts/ops
}

copy_managed_layout_items() {
    src_dir="$1"
    dst_dir="$2"
    while IFS= read -r rel_path; do
        copy_item "$src_dir" "$dst_dir" "$rel_path"
    done <<EOF
$(managed_layout_paths)
EOF
}

preserve_local_config() {
    preserve_dir="$1"
    active_dir=$(current_codebase_dir)
    if [ -f "${active_dir}/deploy/venus/config.venus_evcharger.ini" ]; then
        mkdir -p "${preserve_dir}/deploy/venus"
        cp "${active_dir}/deploy/venus/config.venus_evcharger.ini" "${preserve_dir}/deploy/venus/config.venus_evcharger.ini"
    elif [ -f "${TARGET_DIR}/deploy/venus/config.venus_evcharger.ini" ]; then
        mkdir -p "${preserve_dir}/deploy/venus"
        cp "${TARGET_DIR}/deploy/venus/config.venus_evcharger.ini" "${preserve_dir}/deploy/venus/config.venus_evcharger.ini"
    fi
    if [ -f "${TARGET_DIR}/noUpdate" ]; then
        cp "${TARGET_DIR}/noUpdate" "${preserve_dir}/noUpdate"
    fi
    if [ -f "${TARGET_DIR}/update-channel" ]; then
        cp "${TARGET_DIR}/update-channel" "${preserve_dir}/update-channel"
    fi
}

restore_local_config() {
    preserve_dir="$1"
    destination_root="$2"
    if [ -f "${preserve_dir}/deploy/venus/config.venus_evcharger.ini" ]; then
        mkdir -p "${destination_root}/deploy/venus"
        cp "${preserve_dir}/deploy/venus/config.venus_evcharger.ini" "${destination_root}/deploy/venus/config.venus_evcharger.ini"
    fi
    if [ "$DRY_RUN" != "1" ]; then
        if [ -f "${preserve_dir}/noUpdate" ]; then
            cp "${preserve_dir}/noUpdate" "${TARGET_DIR}/noUpdate"
        fi
        if [ -f "${preserve_dir}/update-channel" ]; then
            cp "${preserve_dir}/update-channel" "${TARGET_DIR}/update-channel"
        fi
    fi
}

apply_merge_result() {
    result_path="$1"
    CONFIG_MERGE_CHANGED=$(json_field "$result_path" "changed" || printf '0')
    CONFIG_MERGE_COMMENT_PRESERVED=$(json_field "$result_path" "comment_preserved" || printf '1')
    CONFIG_MERGE_SKIPPED_REASON=$(json_field "$result_path" "skipped_reason" || true)
    CONFIG_MERGE_BACKUP_PATH=$(json_field "$result_path" "backup_path" || true)
    CONFIG_MERGE_BACKUP_REQUIRED=$(json_field "$result_path" "backup_required" || printf '0')
    CONFIG_SCHEMA_BEFORE=$(json_field "$result_path" "schema_before" || true)
    CONFIG_SCHEMA_TARGET=$(json_field "$result_path" "schema_target" || true)
    CONFIG_MERGE_ADDED_KEYS=$(normalize_multiline_var "$(json_lines_field "$result_path" "added_keys" || true)")
    CONFIG_MERGE_ADDED_SECTIONS=$(normalize_multiline_var "$(json_lines_field "$result_path" "added_sections" || true)")
    CONFIG_MIGRATIONS_APPLIED=$(normalize_multiline_var "$(json_lines_field "$result_path" "migrations_applied" || true)")
}

merge_local_config_template() {
    source_root="$1"
    destination_root="$2"
    template_path="${source_root}/deploy/venus/config.venus_evcharger.ini"
    local_path="${destination_root}/deploy/venus/config.venus_evcharger.ini"
    [ -f "$template_path" ] || return 0
    [ -f "$local_path" ] || return 0

    merge_result_path="${TMP_DIR}/config-merge-result.json"
    if python3 - "$local_path" "$template_path" "$merge_result_path" "$RUN_MODE" <<'PY'
import configparser
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime


SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(?:[#;].*)?$")


class CaseConfigParser(configparser.ConfigParser):
    optionxform = str


def read_config(path: str) -> CaseConfigParser | None:
    parser = CaseConfigParser(interpolation=None)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error):
        return None
    return parser


def schema_version(config: CaseConfigParser) -> int:
    raw_value = config.defaults().get("ConfigSchemaVersion", "").strip()
    if not raw_value:
        return 0
    try:
        return int(raw_value)
    except ValueError:
        return 0


def apply_schema_migrations(config: CaseConfigParser, source_version: int, target_version: int) -> list[str]:
    migrations: list[str] = []
    current_version = source_version
    while current_version < target_version:
        next_version = current_version + 1
        # Explicit migration hook for future renamed or semantic config changes.
        current_version = next_version
    return migrations


def section_ranges(lines: list[str]) -> tuple[int | None, dict[str, tuple[int, int]]]:
    section_positions: list[tuple[str, int]] = []
    first_named_section: int | None = None
    for index, line in enumerate(lines):
        match = SECTION_RE.match(line)
        if not match:
            continue
        section_name = match.group(1).strip()
        if section_name == "DEFAULT":
            continue
        if first_named_section is None:
            first_named_section = index
        section_positions.append((section_name, index))

    ranges: dict[str, tuple[int, int]] = {}
    for index, (section_name, start) in enumerate(section_positions):
        if index + 1 < len(section_positions):
            end = section_positions[index + 1][1]
        else:
            end = len(lines)
        ranges[section_name] = (start, end)
    return first_named_section, ranges


def write_atomically(path: str, content: str) -> None:
    directory = os.path.dirname(path) or "."
    fd, temp_path = tempfile.mkstemp(prefix=".config-merge-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def build_backup_path(path: str) -> str:
    directory = os.path.dirname(path) or "."
    base_name = os.path.basename(path)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = os.path.join(directory, f"{base_name}.bak-{timestamp}")
    suffix = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base_name}.bak-{timestamp}-{suffix}")
        suffix += 1
    return candidate


def merge_with_layout(
    local_path: str,
    template_path: str,
    mode: str,
) -> dict[str, object]:
    result: dict[str, object] = {
        "changed": False,
        "comment_preserved": True,
        "skipped_reason": "",
        "backup_path": "",
        "backup_required": False,
        "added_keys": [],
        "added_sections": [],
        "migrations_applied": [],
        "schema_before": "",
        "schema_target": "",
    }

    local_config = read_config(local_path)
    template_config = read_config(template_path)
    if template_config is None:
        result["skipped_reason"] = "malformed-template-config"
        raise SystemExit(json.dumps(result))
    if local_config is None:
        result["skipped_reason"] = "malformed-local-config"
        return result

    local_schema = schema_version(local_config)
    template_schema = schema_version(template_config)
    migrations = apply_schema_migrations(local_config, local_schema, template_schema)
    result["migrations_applied"] = migrations
    result["schema_before"] = str(local_schema)
    result["schema_target"] = str(template_schema)

    missing_defaults: list[tuple[str, str]] = []
    for key, value in template_config.defaults().items():
        if key not in local_config.defaults():
            missing_defaults.append((key, value))
            result["added_keys"].append(f"DEFAULT.{key}")

    missing_section_keys: dict[str, list[tuple[str, str]]] = {}
    for section_name, section_values in template_config._sections.items():
        if not local_config.has_section(section_name):
            result["added_sections"].append(section_name)
            result["added_keys"].extend(f"{section_name}.{key}" for key in section_values.keys())
            continue
        section_missing: list[tuple[str, str]] = []
        local_section = local_config[section_name]
        for key, value in section_values.items():
            if key not in local_section:
                section_missing.append((key, value))
                result["added_keys"].append(f"{section_name}.{key}")
        if section_missing:
            missing_section_keys[section_name] = section_missing

    if not result["added_keys"] and not result["added_sections"] and not migrations:
        return result

    with open(local_path, "r", encoding="utf-8") as handle:
        local_text = handle.read()
    with open(template_path, "r", encoding="utf-8") as handle:
        template_text = handle.read()

    local_lines = local_text.splitlines()
    template_lines = template_text.splitlines()
    has_trailing_newline = local_text.endswith("\n")
    first_named_section, local_ranges = section_ranges(local_lines)
    _, template_ranges = section_ranges(template_lines)

    insertions: list[tuple[int, list[str]]] = []

    if missing_defaults:
        insert_at = first_named_section if first_named_section is not None else len(local_lines)
        snippet: list[str] = []
        if insert_at > 0 and local_lines[insert_at - 1].strip():
            snippet.append("")
        snippet.append("# Added from refreshed template")
        for key, value in missing_defaults:
            snippet.append(f"{key}={value}")
        if insert_at < len(local_lines) and local_lines[insert_at].strip():
            snippet.append("")
        insertions.append((insert_at, snippet))

    for section_name, missing_pairs in missing_section_keys.items():
        _, end = local_ranges[section_name]
        snippet = []
        if end > 0 and local_lines[end - 1].strip():
            snippet.append("")
        snippet.append("# Added from refreshed template")
        for key, value in missing_pairs:
            snippet.append(f"{key}={value}")
        if end < len(local_lines) and local_lines[end].strip():
            snippet.append("")
        insertions.append((end, snippet))

    if result["added_sections"]:
        appended_sections: list[str] = []
        if local_lines and local_lines[-1].strip():
            appended_sections.append("")
        for section_name in result["added_sections"]:
            start, end = template_ranges[section_name]
            section_block = template_lines[start:end]
            while section_block and not section_block[-1].strip():
                section_block = section_block[:-1]
            appended_sections.extend(section_block)
            appended_sections.append("")
        while appended_sections and not appended_sections[-1].strip():
            appended_sections.pop()
        insertions.append((len(local_lines), appended_sections))

    merged_lines = list(local_lines)
    for insert_at, snippet in sorted(insertions, key=lambda item: item[0], reverse=True):
        if snippet:
            merged_lines[insert_at:insert_at] = snippet

    merged_text = "\n".join(merged_lines)
    if has_trailing_newline or merged_lines:
        merged_text += "\n"

    if merged_text == local_text:
        return result

    result["changed"] = True
    result["backup_required"] = True
    if mode != "dry-run":
        backup_path = build_backup_path(local_path)
        shutil.copy2(local_path, backup_path)
        write_atomically(local_path, merged_text)
        result["backup_path"] = backup_path
    return result


local_path, template_path, result_path, mode = sys.argv[1:5]
try:
    result = merge_with_layout(local_path, template_path, mode)
except SystemExit as exc:
    payload = exc.code
    if isinstance(payload, str) and payload.startswith("{"):
        with open(result_path, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
        sys.exit(2)
    raise

with open(result_path, "w", encoding="utf-8") as handle:
    json.dump(result, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
    then
        merge_status=0
    else
        merge_status=$?
    fi
    apply_merge_result "$merge_result_path"

    if [ "$merge_status" -eq 2 ] && [ "$CONFIG_MERGE_SKIPPED_REASON" = "malformed-template-config" ]; then
        log "Refreshed template config is malformed"
        set_failure_reason_once "malformed-template-config"
        return 1
    fi

    if [ "$CONFIG_MERGE_CHANGED" = "1" ]; then
        if [ "$DRY_RUN" = "1" ]; then
            log "Previewed additive config merge with preserved comments/layout"
        else
            log "Merged missing config keys from the refreshed template"
        fi
    elif [ -n "$CONFIG_MERGE_SKIPPED_REASON" ]; then
        log "Skipping additive config merge; reason: $CONFIG_MERGE_SKIPPED_REASON"
    else
        log "Preserved local config already contains the refreshed template keys"
    fi
}

validate_wallbox_config() {
    destination_root="$1"
    config_path="${destination_root}/deploy/venus/config.venus_evcharger.ini"
    [ -f "$config_path" ] || return 0
    if [ -f "${destination_root}/venus_evcharger/backend/probe.py" ]; then
        if (
            cd "$destination_root" &&
            python3 -m venus_evcharger.backend.probe validate-wallbox "$config_path" >/dev/null
        ); then
            VALIDATION_PASSED=1
            CONFIG_VALIDATION_MODE="probe"
            log "Validated merged wallbox config"
            return 0
        fi
        VALIDATION_PASSED=0
        CONFIG_VALIDATION_MODE="probe"
        PROMOTION_ABORTED_REASON="${PROMOTION_ABORTED_REASON:-config-validation-failed}"
        set_failure_reason_once "config-validation-failed"
        log "Wallbox config validation failed for $config_path"
        return 1
    fi
    if python3 - "$config_path" <<'PY'
import configparser
import sys

config_path = sys.argv[1]
parser = configparser.ConfigParser()
try:
    with open(config_path, "r", encoding="utf-8") as handle:
        parser.read_file(handle)
except (OSError, configparser.Error):
    raise SystemExit(1)
if "DEFAULT" not in parser or not parser["DEFAULT"].get("Host", "").strip():
    raise SystemExit(1)
PY
    then
        VALIDATION_PASSED=1
        CONFIG_VALIDATION_MODE="fallback"
        log "Validated merged wallbox config with the bootstrap fallback validator"
        return 0
    fi
    VALIDATION_PASSED=0
    CONFIG_VALIDATION_MODE="fallback"
    PROMOTION_ABORTED_REASON="${PROMOTION_ABORTED_REASON:-config-validation-failed}"
    set_failure_reason_once "config-validation-failed"
    log "Wallbox config validation failed for $config_path"
    return 1
}

cleanup_unwanted_paths() {
    cleanup_root="$1"
    rm -rf "${cleanup_root}/tests"
    rm -rf "${cleanup_root}/docs"
    rm -rf "${cleanup_root}/.github"
    rm -rf "${cleanup_root}/scripts/dev"
    rm -rf "${cleanup_root}/__pycache__"
    rm -f "${cleanup_root}/Makefile"
    rm -f "${cleanup_root}/mypy.ini"
    rm -f "${cleanup_root}/mypy_strict.ini"
    rm -f "${cleanup_root}/pyrightconfig.json"
}

write_managed_layout() {
    src_dir="$1"
    destination_root="${2:-$TARGET_DIR}"
    mkdir -p "$destination_root"

    preserve_dir=$(mktemp -d)
    preserve_local_config "$preserve_dir"

    copy_managed_layout_items "$src_dir" "$destination_root"

    restore_local_config "$preserve_dir" "$destination_root"
    if ! merge_local_config_template "$src_dir" "$destination_root"; then
        rm -rf "$preserve_dir"
        return 1
    fi
    rm -rf "$preserve_dir"
    cleanup_unwanted_paths "$destination_root"
    if ! validate_wallbox_config "$destination_root"; then
        return 1
    fi

    chmod 755 "${destination_root}/install.sh" 2>/dev/null || true
    chmod 755 "${destination_root}/venus_evcharger_service.py" 2>/dev/null || true
    chmod 755 "${destination_root}/venus_evcharger_auto_input_helper.py" 2>/dev/null || true
    chmod 755 "${destination_root}/deploy/venus/install_venus_evcharger_service.sh" 2>/dev/null || true
    chmod 755 "${destination_root}/deploy/venus/boot_venus_evcharger_service.sh" 2>/dev/null || true
    chmod 744 "${destination_root}/deploy/venus/restart_venus_evcharger_service.sh" 2>/dev/null || true
    chmod 744 "${destination_root}/deploy/venus/uninstall_venus_evcharger_service.sh" 2>/dev/null || true
    chmod 755 "${destination_root}/deploy/venus/service_venus_evcharger/run" 2>/dev/null || true
    chmod 755 "${destination_root}/deploy/venus/service_venus_evcharger/log/run" 2>/dev/null || true
}

promote_target_layout() {
    staged_root="$1"
    mkdir -p "$TARGET_DIR"
    copy_managed_layout_items "$staged_root" "$TARGET_DIR"
    cleanup_unwanted_paths "$TARGET_DIR"
}

promote_release_layout() {
    src_dir="$1"
    release_version="${MANIFEST_VERSION:-bundle}"
    mkdir -p "$RELEASES_DIR"
    final_release_dir="${RELEASES_DIR}/${release_version}"
    staging_release_dir="${RELEASES_DIR}/.${release_version}.staging.$$"
    rm -rf "$staging_release_dir"
    if ! write_managed_layout "$src_dir" "$staging_release_dir"; then
        CURRENT_PRESERVED=1
        rm -rf "$staging_release_dir"
        return 1
    fi
    rm -rf "$final_release_dir"
    mv "$staging_release_dir" "$final_release_dir"
    if [ -n "$CONFIG_MERGE_BACKUP_PATH" ]; then
        CONFIG_MERGE_BACKUP_PATH="${CONFIG_MERGE_BACKUP_PATH/$staging_release_dir/$final_release_dir}"
    fi
    if [ -L "$CURRENT_LINK" ] && command -v readlink >/dev/null 2>&1; then
        current_target=$(readlink "$CURRENT_LINK" 2>/dev/null || true)
        if [ -n "$current_target" ]; then
            ln -sfn "$current_target" "$PREVIOUS_LINK"
        fi
    fi
    ln -sfn "$final_release_dir" "$CURRENT_LINK"
    PROMOTED_RELEASE="$final_release_dir"
}

record_install_state() {
    mkdir -p "$STATE_DIR"
    if [ -n "${MANIFEST_BUNDLE_SHA256:-}" ]; then
        printf '%s\n' "$MANIFEST_BUNDLE_SHA256" > "$INSTALLED_BUNDLE_HASH_FILE"
    fi
    if [ -n "${MANIFEST_VERSION:-}" ]; then
        printf '%s\n' "$MANIFEST_VERSION" > "$INSTALLED_VERSION_FILE"
    elif [ -n "$RUN_NEW_VERSION" ]; then
        printf '%s\n' "$(normalize_version_value "$RUN_NEW_VERSION")" > "$INSTALLED_VERSION_FILE"
    fi
}

materialize_source_from_bundle() {
    archive_path="$1"
    extract_dir="$2"
    log "Downloading code bundle${MANIFEST_VERSION:+ version $MANIFEST_VERSION}"
    download_to "$MANIFEST_BUNDLE_URL" "$archive_path"
    archive_hash=$(sha256sum "$archive_path" | awk '{print $1}')
    if [ "$archive_hash" != "$MANIFEST_BUNDLE_SHA256" ]; then
        log "Bundle hash mismatch for $MANIFEST_BUNDLE_URL"
        set_failure_reason_once "bundle-hash-mismatch"
        exit 1
    fi
    mkdir -p "$extract_dir"
    tar -xzf "$archive_path" -C "$extract_dir"
    SOURCE_DIR="$extract_dir"
    if ! require_source_layout "$SOURCE_DIR"; then
        SOURCE_DIR=$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)
        if [ -z "$SOURCE_DIR" ] || ! require_source_layout "$SOURCE_DIR"; then
            log "Downloaded code bundle is incomplete"
            set_failure_reason_once "incomplete-downloaded-bundle"
            exit 1
        fi
    fi
}

write_update_status() {
    mkdir -p "$STATE_DIR"
    CONFIG_MERGE_ADDED_KEYS="$CONFIG_MERGE_ADDED_KEYS" \
    CONFIG_MERGE_ADDED_SECTIONS="$CONFIG_MERGE_ADDED_SECTIONS" \
    CONFIG_MIGRATIONS_APPLIED="$CONFIG_MIGRATIONS_APPLIED" \
    python3 - "$STATUS_FILE" "$AUDIT_LOG_FILE" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone


def split_lines(name: str) -> list[str]:
    value = os.environ.get(name, "")
    return [item for item in value.splitlines() if item]


status_path, audit_path = sys.argv[1:3]
payload = {
    "timestamp_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "mode": os.environ.get("RUN_MODE", ""),
    "result": os.environ.get("RUN_RESULT", ""),
    "failure_reason": os.environ.get("RUN_FAILURE_REASON", ""),
    "target_dir": os.environ.get("TARGET_DIR", ""),
    "old_version": os.environ.get("RUN_OLD_VERSION", ""),
    "new_version": os.environ.get("RUN_NEW_VERSION", ""),
    "old_bundle_sha256": os.environ.get("RUN_OLD_BUNDLE_SHA256", ""),
    "new_bundle_sha256": os.environ.get("RUN_NEW_BUNDLE_SHA256", ""),
    "current_preserved": os.environ.get("CURRENT_PRESERVED", "0") == "1",
    "already_current": os.environ.get("CURRENT_ALREADY_MATCHED", "0") == "1",
    "promoted_release": os.environ.get("PROMOTED_RELEASE", ""),
    "promotion_aborted_reason": os.environ.get("PROMOTION_ABORTED_REASON", ""),
    "rollback_reason": os.environ.get("ROLLBACK_REASON", ""),
    "config_merge_changed": os.environ.get("CONFIG_MERGE_CHANGED", "0") == "1",
    "config_merge_comment_preserved": os.environ.get("CONFIG_MERGE_COMMENT_PRESERVED", "1") == "1",
    "config_merge_skipped_reason": os.environ.get("CONFIG_MERGE_SKIPPED_REASON", ""),
    "config_merge_backup_path": os.environ.get("CONFIG_MERGE_BACKUP_PATH", ""),
    "config_merge_backup_required": os.environ.get("CONFIG_MERGE_BACKUP_REQUIRED", "0") == "1",
    "config_merge_added_keys": split_lines("CONFIG_MERGE_ADDED_KEYS"),
    "config_merge_added_sections": split_lines("CONFIG_MERGE_ADDED_SECTIONS"),
    "config_schema_before": os.environ.get("CONFIG_SCHEMA_BEFORE", ""),
    "config_schema_target": os.environ.get("CONFIG_SCHEMA_TARGET", ""),
    "config_migrations_applied": split_lines("CONFIG_MIGRATIONS_APPLIED"),
    "config_validation_passed": os.environ.get("VALIDATION_PASSED", "0") == "1",
    "config_validation_mode": os.environ.get("CONFIG_VALIDATION_MODE", ""),
}

with open(status_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")

with open(audit_path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(payload, sort_keys=True))
    handle.write("\n")
PY
}

print_preview_summary() {
    CONFIG_MERGE_ADDED_KEYS="$CONFIG_MERGE_ADDED_KEYS" \
    CONFIG_MERGE_ADDED_SECTIONS="$CONFIG_MERGE_ADDED_SECTIONS" \
    CONFIG_MIGRATIONS_APPLIED="$CONFIG_MIGRATIONS_APPLIED" \
    python3 - <<'PY'
import json
import os
from datetime import datetime, timezone


def split_lines(name: str) -> list[str]:
    value = os.environ.get(name, "")
    return [item for item in value.splitlines() if item]


payload = {
    "timestamp_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "mode": os.environ.get("RUN_MODE", ""),
    "result": os.environ.get("RUN_RESULT", ""),
    "failure_reason": os.environ.get("RUN_FAILURE_REASON", ""),
    "target_dir": os.environ.get("TARGET_DIR", ""),
    "old_version": os.environ.get("RUN_OLD_VERSION", ""),
    "new_version": os.environ.get("RUN_NEW_VERSION", ""),
    "old_bundle_sha256": os.environ.get("RUN_OLD_BUNDLE_SHA256", ""),
    "new_bundle_sha256": os.environ.get("RUN_NEW_BUNDLE_SHA256", ""),
    "already_current": os.environ.get("CURRENT_ALREADY_MATCHED", "0") == "1",
    "config_merge_changed": os.environ.get("CONFIG_MERGE_CHANGED", "0") == "1",
    "config_merge_comment_preserved": os.environ.get("CONFIG_MERGE_COMMENT_PRESERVED", "1") == "1",
    "config_merge_skipped_reason": os.environ.get("CONFIG_MERGE_SKIPPED_REASON", ""),
    "config_merge_backup_required": os.environ.get("CONFIG_MERGE_BACKUP_REQUIRED", "0") == "1",
    "config_merge_added_keys": split_lines("CONFIG_MERGE_ADDED_KEYS"),
    "config_merge_added_sections": split_lines("CONFIG_MERGE_ADDED_SECTIONS"),
    "config_schema_before": os.environ.get("CONFIG_SCHEMA_BEFORE", ""),
    "config_schema_target": os.environ.get("CONFIG_SCHEMA_TARGET", ""),
    "config_migrations_applied": split_lines("CONFIG_MIGRATIONS_APPLIED"),
    "config_validation_passed": os.environ.get("VALIDATION_PASSED", "0") == "1",
    "config_validation_mode": os.environ.get("CONFIG_VALIDATION_MODE", ""),
}
print(json.dumps(payload, sort_keys=True))
PY
}

finalize_run() {
    status=$?
    set +e
    if [ -n "${TMP_DIR:-}" ] && [ -d "$TMP_DIR" ]; then
        rm -rf "$TMP_DIR"
    fi
    if [ "${DRY_RUN:-0}" != "1" ] && [ -n "${TARGET_DIR:-}" ]; then
        write_update_status
    fi
    return "$status"
}

trap finalize_run EXIT

main() {
    TMP_DIR=$(mktemp -d)
    ensure_updater_prereqs

    RUN_OLD_VERSION=$(detect_current_version || true)
    RUN_OLD_BUNDLE_SHA256=$(detect_current_bundle_hash || true)

    if [ -n "$SOURCE_DIR_OVERRIDE" ]; then
        SOURCE_DIR="$SOURCE_DIR_OVERRIDE"
        if ! require_source_layout "$SOURCE_DIR"; then
            log "Local source directory is incomplete: $SOURCE_DIR"
            set_failure_reason_once "incomplete-local-source"
            exit 1
        fi
    elif load_manifest "${TMP_DIR}/bootstrap_manifest.json"; then
        RUN_NEW_BUNDLE_SHA256="${MANIFEST_BUNDLE_SHA256:-}"
        if target_is_current_for_manifest; then
            CURRENT_ALREADY_MATCHED=1
            RUN_NEW_VERSION="${MANIFEST_VERSION:-$RUN_OLD_VERSION}"
            VALIDATION_PASSED=1
            CONFIG_VALIDATION_MODE="current-state"
            RUN_RESULT="success"
            log "Target already matches manifest${MANIFEST_VERSION:+ version $MANIFEST_VERSION}; skipping refresh"
            if [ "$DRY_RUN" = "1" ]; then
                RUN_RESULT="preview"
                print_preview_summary
            fi
            exit 0
        fi
        archive_path="${TMP_DIR}/bundle.tar.gz"
        extract_dir="${TMP_DIR}/extract"
        materialize_source_from_bundle "$archive_path" "$extract_dir"
    else
        archive_path="${TMP_DIR}/bundle.tar.gz"
        extract_dir="${TMP_DIR}/extract"
        mkdir -p "$extract_dir"
        log "Downloading code bundle for ${REPO_SLUG}:${CHANNEL}"
        download_to "$ARCHIVE_URL" "$archive_path"
        tar -xzf "$archive_path" -C "$extract_dir"
        SOURCE_DIR=$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)
        if [ -z "$SOURCE_DIR" ] || ! require_source_layout "$SOURCE_DIR"; then
            log "Downloaded code bundle is incomplete"
            set_failure_reason_once "incomplete-downloaded-bundle"
            exit 1
        fi
    fi

    RUN_NEW_VERSION="${MANIFEST_VERSION:-$(read_tree_version "$SOURCE_DIR" || true)}"

    if [ "$DRY_RUN" = "1" ]; then
        preview_root="${TMP_DIR}/preview"
        if ! write_managed_layout "$SOURCE_DIR" "$preview_root"; then
            RUN_RESULT="failed"
            print_preview_summary
            exit 1
        fi
        RUN_RESULT="preview"
        print_preview_summary
        exit 0
    fi

    if [ -n "${MANIFEST_VERSION:-}" ]; then
        if ! promote_release_layout "$SOURCE_DIR"; then
            exit 1
        fi
    else
        direct_staging_root="${TMP_DIR}/target-layout"
        if ! write_managed_layout "$SOURCE_DIR" "$direct_staging_root"; then
            exit 1
        fi
        promote_target_layout "$direct_staging_root"
        if [ -n "$CONFIG_MERGE_BACKUP_PATH" ]; then
            CONFIG_MERGE_BACKUP_PATH="${CONFIG_MERGE_BACKUP_PATH/$direct_staging_root/$TARGET_DIR}"
        fi
    fi

    record_install_state
    RUN_RESULT="success"
    log "Codebase refreshed in $TARGET_DIR"
}

main
