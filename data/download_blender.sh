#!/bin/bash
#
# Download a pinned, PORTABLE Blender (no install, no root) used by the SMPL-X
# exporter (ma_export) to produce rigged FBX / Alembic / BVH / USD. Extracts a
# self-contained Blender whose bundled Python is fully isolated from the `mamma`
# conda env — it cannot disturb numpy / sam2 / sam3 pins.
#
# We pin a 4.5 LTS build on purpose: it is exactly the line the SMPL-X Blender
# add-on targets (blender_version_min = 4.5.0), so the add-on runs reliably
# (newer Blender, e.g. 5.x, drifts and can break the add-on's registration).
# 4.5 is LTS, supported until July 2027.
#
# Public download (no credentials). Idempotent.
#
# Usage:
#   bash data/download_blender.sh
#   bash data/download_blender.sh --output /scratch/data
#   bash data/download_blender.sh --version 4.5.10
#   bash data/download_blender.sh --help
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR"
BLENDER_VERSION="4.5.10"   # pinned 4.5 LTS patch; bump here to update

usage() {
    echo "Usage: bash data/download_blender.sh [OPTIONS]"
    echo ""
    echo "Downloads a portable Blender ${BLENDER_VERSION} (4.5 LTS) into"
    echo "<output>/blender/ for the SMPL-X exporter. No install, no root."
    echo ""
    echo "Options:"
    echo "  --output DIR     Output directory (default: <repo>/data)"
    echo "  --version X.Y.Z  Blender version to fetch (default: ${BLENDER_VERSION}; must be >= 4.5.0)"
    echo "  -h, --help       Show this help message"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output)  OUTPUT_DIR="$2"; shift 2 ;;
        --version) BLENDER_VERSION="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1 (use --help for usage)" >&2; exit 1 ;;
    esac
done

MAJOR_MINOR="${BLENDER_VERSION%.*}"   # 4.5.10 -> 4.5
DEST_DIR="${OUTPUT_DIR}/blender"
BASE_URL="https://download.blender.org/release/Blender${MAJOR_MINOR}"

# ---- OS / arch detection -> archive name + extractor --------------------
os="$(uname -s)"; arch="$(uname -m)"
case "$os" in
    Linux)
        case "$arch" in
            x86_64|amd64) PLAT="linux-x64"; EXT="tar.xz" ;;
            aarch64|arm64) echo "No official portable Blender for linux-arm64; install Blender >= 4.5 manually." >&2; exit 1 ;;
            *) echo "Unsupported Linux arch: $arch" >&2; exit 1 ;;
        esac ;;
    Darwin)
        case "$arch" in
            arm64) PLAT="macos-arm64"; EXT="dmg" ;;
            x86_64) PLAT="macos-x64"; EXT="dmg" ;;
            *) echo "Unsupported macOS arch: $arch" >&2; exit 1 ;;
        esac ;;
    MINGW*|MSYS*|CYGWIN*) PLAT="windows-x64"; EXT="zip" ;;
    *) echo "Unsupported OS: $os — install Blender >= 4.5 manually and set MAMMA_BLENDER_BIN." >&2; exit 1 ;;
esac

ARCHIVE="blender-${BLENDER_VERSION}-${PLAT}.${EXT}"
URL="${BASE_URL}/${ARCHIVE}"
EXTRACTED="${DEST_DIR}/blender-${BLENDER_VERSION}-${PLAT}"
ARCHIVE_PATH="${DEST_DIR}/${ARCHIVE}"

# ---- locate the blender binary inside an extracted tree -----------------
blender_bin_in() {
    case "$os" in
        Darwin) echo "$1/Blender.app/Contents/MacOS/Blender" ;;
        MINGW*|MSYS*|CYGWIN*) echo "$1/blender.exe" ;;
        *) echo "$1/blender" ;;
    esac
}

if [[ -x "$(blender_bin_in "$EXTRACTED")" ]]; then
    echo "  [skip] portable Blender ${BLENDER_VERSION} already at ${EXTRACTED}"
    echo "Done! Downloaded: 0, Failed: 0"
    exit 0
fi

mkdir -p "$DEST_DIR"
echo ""
echo "Download:   ${URL}"
echo "Output:     ${EXTRACTED}"
echo ""

if ! wget "$URL" -O "$ARCHIVE_PATH" --continue --show-progress 2>&1; then
    rm -f "$ARCHIVE_PATH"
    echo "  [FAIL] could not download ${ARCHIVE} (check the version exists at ${BASE_URL}/)" >&2
    exit 1
fi

case "$EXT" in
    tar.xz) tar -xJf "$ARCHIVE_PATH" -C "$DEST_DIR" ;;
    zip)    unzip -q -o "$ARCHIVE_PATH" -d "$DEST_DIR" ;;
    dmg)
        MNT="$(mktemp -d)"
        hdiutil attach -nobrowse -mountpoint "$MNT" "$ARCHIVE_PATH" >/dev/null
        mkdir -p "$EXTRACTED"
        cp -R "$MNT/Blender.app" "$EXTRACTED/"
        hdiutil detach "$MNT" >/dev/null ;;
esac

BIN="$(blender_bin_in "$EXTRACTED")"
if [[ ! -x "$BIN" ]]; then
    echo "  [FAIL] blender binary not found at ${BIN} after extraction" >&2
    exit 1
fi
rm -f "$ARCHIVE_PATH"

echo "  [ok] portable Blender -> ${BIN}"
echo "      (ma_export auto-detects it here; or set MAMMA_BLENDER_BIN=${BIN})"
echo ""
echo "Done! Downloaded: 1, Failed: 0"
