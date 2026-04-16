#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later

# Local updater used by the standalone GX bootstrap installer.
#
# Phase 1 deliberately keeps the update model simple and practical:
# - materialize or refresh a working repository tree under the target directory
# - preserve the user-edited Venus config when present
# - avoid shipping tests and development-only helpers to the GX device

set -eu

if [ "${1:-}" = "" ]; then
    echo "Usage: $0 <target-dir>" >&2
    exit 1
fi

TARGET_DIR="$1"
DEFAULT_REPO_SLUG="martinthebrain/dbus-shelly-evcharger"
DEFAULT_CHANNEL="main"
REPO_SLUG="${SHELLY_WALLBOX_REPO_SLUG:-$DEFAULT_REPO_SLUG}"
CHANNEL="${SHELLY_WALLBOX_CHANNEL:-$DEFAULT_CHANNEL}"
SOURCE_DIR_OVERRIDE="${SHELLY_WALLBOX_SOURCE_DIR:-}"
MANIFEST_SOURCE="${SHELLY_WALLBOX_MANIFEST_SOURCE:-}"
MANIFEST_SIG_SOURCE="${SHELLY_WALLBOX_MANIFEST_SIG_SOURCE:-${MANIFEST_SOURCE}.sig}"
BOOTSTRAP_PUBKEY_OVERRIDE="${SHELLY_WALLBOX_BOOTSTRAP_PUBKEY:-}"
REQUIRE_SIGNED_MANIFEST="${SHELLY_WALLBOX_REQUIRE_SIGNED_MANIFEST:-0}"
ARCHIVE_URL="${SHELLY_WALLBOX_ARCHIVE_URL:-https://codeload.github.com/${REPO_SLUG}/tar.gz/refs/heads/${CHANNEL}}"
STATE_DIR="${TARGET_DIR}/.bootstrap-state"
INSTALLED_BUNDLE_HASH_FILE="${STATE_DIR}/installed_bundle_sha256"
INSTALLED_VERSION_FILE="${STATE_DIR}/installed_version"
RELEASES_DIR="${TARGET_DIR}/releases"
CURRENT_LINK="${TARGET_DIR}/current"
PREVIOUS_LINK="${TARGET_DIR}/previous"

log() {
    printf '%s\n' "[updater] $*"
}

require_command() {
    command_name="$1"
    if ! command -v "$command_name" >/dev/null 2>&1; then
        log "Required command is missing: $command_name"
        exit 1
    fi
}

ensure_download_tool() {
    if command -v wget >/dev/null 2>&1 || command -v curl >/dev/null 2>&1; then
        return 0
    fi
    log "Neither wget nor curl is available"
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
    return 1
}

require_source_layout() {
    src_dir="$1"
    [ -f "${src_dir}/dbus_shelly_wallbox.py" ] || return 1
    [ -f "${src_dir}/shelly_wallbox_auto_input_helper.py" ] || return 1
    [ -f "${src_dir}/deploy/venus/install_shelly_wallbox.sh" ] || return 1
    [ -d "${src_dir}/shelly_wallbox" ] || return 1
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
    pubkey_path="${STATE_DIR}/bootstrap_manifest.pub"
    mkdir -p "$STATE_DIR"
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

manifest_field() {
    manifest_path="$1"
    field_name="$2"
    python3 - "$manifest_path" "$field_name" <<'PY'
import json
import sys

path, field = sys.argv[1], sys.argv[2]
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    sys.exit(1)
value = data.get(field, "")
if isinstance(value, (str, int, float)):
    print(value)
PY
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
            return 1
        fi
    elif [ "$REQUIRE_SIGNED_MANIFEST" = "1" ]; then
        log "Signed manifest required but signature could not be fetched"
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

preserve_local_config() {
    preserve_dir="$1"
    active_dir=$(current_codebase_dir)
    if [ -f "${active_dir}/deploy/venus/config.shelly_wallbox.ini" ]; then
        mkdir -p "${preserve_dir}/deploy/venus"
        cp "${active_dir}/deploy/venus/config.shelly_wallbox.ini" "${preserve_dir}/deploy/venus/config.shelly_wallbox.ini"
    elif [ -f "${TARGET_DIR}/deploy/venus/config.shelly_wallbox.ini" ]; then
        mkdir -p "${preserve_dir}/deploy/venus"
        cp "${TARGET_DIR}/deploy/venus/config.shelly_wallbox.ini" "${preserve_dir}/deploy/venus/config.shelly_wallbox.ini"
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
    if [ -f "${preserve_dir}/deploy/venus/config.shelly_wallbox.ini" ]; then
        mkdir -p "${destination_root}/deploy/venus"
        cp "${preserve_dir}/deploy/venus/config.shelly_wallbox.ini" "${destination_root}/deploy/venus/config.shelly_wallbox.ini"
    fi
    if [ -f "${preserve_dir}/noUpdate" ]; then
        cp "${preserve_dir}/noUpdate" "${TARGET_DIR}/noUpdate"
    fi
    if [ -f "${preserve_dir}/update-channel" ]; then
        cp "${preserve_dir}/update-channel" "${TARGET_DIR}/update-channel"
    fi
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

    for rel_path in \
        install.sh \
        LICENSE \
        README.md \
        SHELLY_PROFILES.md \
        version.txt \
        dbus_shelly_wallbox.py \
        shelly_wallbox_auto_input_helper.py \
        deploy/venus \
        shelly_wallbox \
        scripts/ops
    do
        copy_item "$src_dir" "$destination_root" "$rel_path"
    done

    restore_local_config "$preserve_dir" "$destination_root"
    rm -rf "$preserve_dir"
    cleanup_unwanted_paths "$destination_root"

    chmod 755 "${destination_root}/install.sh" 2>/dev/null || true
    chmod 755 "${destination_root}/dbus_shelly_wallbox.py" 2>/dev/null || true
    chmod 755 "${destination_root}/shelly_wallbox_auto_input_helper.py" 2>/dev/null || true
    chmod 755 "${destination_root}/deploy/venus/install_shelly_wallbox.sh" 2>/dev/null || true
    chmod 755 "${destination_root}/deploy/venus/boot_shelly_wallbox.sh" 2>/dev/null || true
    chmod 744 "${destination_root}/deploy/venus/restart_shelly_wallbox.sh" 2>/dev/null || true
    chmod 744 "${destination_root}/deploy/venus/uninstall_shelly_wallbox.sh" 2>/dev/null || true
    chmod 755 "${destination_root}/deploy/venus/service_shelly_wallbox/run" 2>/dev/null || true
    chmod 755 "${destination_root}/deploy/venus/service_shelly_wallbox/log/run" 2>/dev/null || true
}

promote_release_layout() {
    src_dir="$1"
    release_version="${MANIFEST_VERSION:-bundle}"
    mkdir -p "$RELEASES_DIR"
    final_release_dir="${RELEASES_DIR}/${release_version}"
    staging_release_dir="${RELEASES_DIR}/.${release_version}.staging.$$"
    rm -rf "$staging_release_dir"
    write_managed_layout "$src_dir" "$staging_release_dir"
    rm -rf "$final_release_dir"
    mv "$staging_release_dir" "$final_release_dir"
    if [ -L "$CURRENT_LINK" ] && command -v readlink >/dev/null 2>&1; then
        current_target=$(readlink "$CURRENT_LINK" 2>/dev/null || true)
        if [ -n "$current_target" ]; then
            ln -sfn "$current_target" "$PREVIOUS_LINK"
        fi
    fi
    ln -sfn "$final_release_dir" "$CURRENT_LINK"
}

record_install_state() {
    mkdir -p "$STATE_DIR"
    if [ -n "${MANIFEST_BUNDLE_SHA256:-}" ]; then
        printf '%s\n' "$MANIFEST_BUNDLE_SHA256" > "$INSTALLED_BUNDLE_HASH_FILE"
    fi
    if [ -n "${MANIFEST_VERSION:-}" ]; then
        printf '%s\n' "$MANIFEST_VERSION" > "$INSTALLED_VERSION_FILE"
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
        exit 1
    fi
    mkdir -p "$extract_dir"
    tar -xzf "$archive_path" -C "$extract_dir"
    SOURCE_DIR="$extract_dir"
    if ! require_source_layout "$SOURCE_DIR"; then
        SOURCE_DIR=$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)
        if [ -z "$SOURCE_DIR" ] || ! require_source_layout "$SOURCE_DIR"; then
            log "Downloaded code bundle is incomplete"
            exit 1
        fi
    fi
}

main() {
    tmp_dir=$(mktemp -d)
    cleanup_tmp() {
        rm -rf "$tmp_dir"
    }
    trap cleanup_tmp EXIT

    ensure_updater_prereqs

    if [ -n "$SOURCE_DIR_OVERRIDE" ]; then
        SOURCE_DIR="$SOURCE_DIR_OVERRIDE"
        if ! require_source_layout "$SOURCE_DIR"; then
            log "Local source directory is incomplete: $SOURCE_DIR"
            exit 1
        fi
    elif load_manifest "${tmp_dir}/bootstrap_manifest.json"; then
        if target_is_current_for_manifest; then
            log "Target already matches manifest${MANIFEST_VERSION:+ version $MANIFEST_VERSION}; skipping refresh"
            exit 0
        fi
        archive_path="${tmp_dir}/bundle.tar.gz"
        extract_dir="${tmp_dir}/extract"
        materialize_source_from_bundle "$archive_path" "$extract_dir"
    else
        archive_path="${tmp_dir}/bundle.tar.gz"
        extract_dir="${tmp_dir}/extract"
        mkdir -p "$extract_dir"
        log "Downloading code bundle for ${REPO_SLUG}:${CHANNEL}"
        download_to "$ARCHIVE_URL" "$archive_path"
        tar -xzf "$archive_path" -C "$extract_dir"
        SOURCE_DIR=$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)
        if [ -z "$SOURCE_DIR" ] || ! require_source_layout "$SOURCE_DIR"; then
            log "Downloaded code bundle is incomplete"
            exit 1
        fi
    fi

    if [ -n "${MANIFEST_VERSION:-}" ]; then
        promote_release_layout "$SOURCE_DIR"
    else
        write_managed_layout "$SOURCE_DIR"
    fi
    record_install_state
    log "Codebase refreshed in $TARGET_DIR"
}

main
