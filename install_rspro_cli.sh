#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REVISION=8aa4dcd2973ae2a0f961afa61e0ecbcb4e3a92dc
SOURCE_URL="${RSPRO_SOURCE_URL:-https://github.com/Cymaphore/libsigrok.git}"
ROOT_DIR="${RSPRO_ROOT:-${HOME}/.local/share/rspro-sigrok}"
DEST_DIR="${RSPRO_BIN_DIR:-${HOME}/.local/bin}"

CLI_ONLY=0
if (( $# )); then
    if (( $# == 1 )) && [[ "$1" == --help || "$1" == -h ]]; then
        cat <<'HELP'
Usage: ./install_rspro_cli.sh [--help] [--cli-only]
Build pinned, patched libsigrok and install the rspro CLI.
Install Arch/Omarchy dependencies listed in README.md first.
--cli-only updates the CLI and dashboard without rebuilding libsigrok.

Environment:
  RSPRO_ROOT        Build/install root (default: ~/.local/share/rspro-sigrok)
  RSPRO_BIN_DIR     CLI destination (default: ~/.local/bin)
  RSPRO_JOBS        Parallel build jobs (default: 2)
  RSPRO_SOURCE_URL  Source mirror containing the pinned libsigrok commit
HELP
        exit 0
    fi
    if (( $# == 1 )) && [[ "$1" == --cli-only ]]; then
        CLI_ONLY=1
    else
        echo "ERROR: Unknown installer arguments. See --help." >&2
        exit 2
    fi
fi

[[ "$ROOT_DIR" == /* && "$DEST_DIR" == /* ]] || {
    echo "ERROR: RSPRO_ROOT and RSPRO_BIN_DIR must be absolute paths." >&2
    exit 2
}
JOBS="${RSPRO_JOBS:-2}"
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || { echo "ERROR: Invalid RSPRO_JOBS." >&2; exit 2; }
for tool in python3 stdbuf bluetoothctl install; do
    command -v "$tool" >/dev/null || { echo "ERROR: Missing $tool; see README.md." >&2; exit 1; }
done
python3 -c 'import curses, sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11 or newer is required.")'

install_cli() {
    mkdir -p "$DEST_DIR"
    install -m 0755 "$SRC_DIR/rspro" "$DEST_DIR/rspro"
    install -m 0644 "$SRC_DIR/rspro_dashboard.py" "$DEST_DIR/rspro_dashboard.py"
    "$DEST_DIR/rspro" --help >/dev/null
    "$DEST_DIR/rspro" dashboard --help >/dev/null
    echo "Installed $DEST_DIR/rspro and terminal dashboard."
}

if (( CLI_ONLY )); then
    install_cli
    exit 0
fi

for tool in git make gcc pkg-config autoreconf libtoolize install sigrok-cli; do
    command -v "$tool" >/dev/null || { echo "ERROR: Missing $tool; see README.md." >&2; exit 1; }
done
pkg-config --exists glib-2.0 bluez || {
    echo "ERROR: GLib and BlueZ development files are required; see README.md." >&2
    exit 1
}
mkdir -p "$ROOT_DIR/builds"
BUILD_DIR="$(mktemp -d "$ROOT_DIR/builds/libsigrok.XXXXXX")"
PREFIX="$ROOT_DIR/prefix"
echo "Building libsigrok $REVISION in $BUILD_DIR"
# A fresh checkout preserves any existing local source and debugging logs.
git clone --no-checkout "$SOURCE_URL" "$BUILD_DIR/source"
git -C "$BUILD_DIR/source" checkout --detach "$REVISION"
git -C "$BUILD_DIR/source" apply --check "$SRC_DIR/patches/libsigrok-appa-s3-timing.patch"
git -C "$BUILD_DIR/source" apply "$SRC_DIR/patches/libsigrok-appa-s3-timing.patch"
cd "$BUILD_DIR/source"
./autogen.sh
./configure --prefix="$PREFIX" --with-libbluez --disable-bindings
make -j "$JOBS"
make install
LIBDIR="$PREFIX/lib"
[[ -d "$PREFIX/lib64" ]] && LIBDIR="$PREFIX/lib64"
# Verify runtime loading and driver availability without connecting to a meter.
LD_LIBRARY_PATH="$LIBDIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    sigrok-cli --version > "$BUILD_DIR/runtime-version.txt" 2>&1
if ! grep -q 'rt: 0.6.0-git-8aa4dcd2/' "$BUILD_DIR/runtime-version.txt"; then
    echo "ERROR: sigrok-cli did not load the pinned library; inspect $BUILD_DIR/runtime-version.txt." >&2
    exit 1
fi
LD_LIBRARY_PATH="$LIBDIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    sigrok-cli --list-supported > "$BUILD_DIR/supported.txt" 2>&1
if ! grep -q 'appa-dmm' "$BUILD_DIR/supported.txt"; then
    echo "ERROR: Built library does not expose appa-dmm; inspect $BUILD_DIR." >&2
    exit 1
fi
install_cli
echo "Installed $DEST_DIR/rspro; runtime verification saved in $BUILD_DIR."
case ":$PATH:" in
    *":$DEST_DIR:"*) ;;
    *) echo "Add $DEST_DIR to your PATH." ;;
esac
echo "Start with: RSPRO_DEVICE=AA:BB:CC:DD:EE:FF rspro"
if [[ "$ROOT_DIR" != "$HOME/.local/share/rspro-sigrok" ]]; then
    echo "Use RSPRO_ROOT=$ROOT_DIR when running rspro."
fi
