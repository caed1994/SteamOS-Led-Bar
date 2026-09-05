#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# Builds and flashes the LED client firmware with PlatformIO.
#
#   ./flash-esp.sh                       # ESP8266 on GPIO2, the default
#   ./flash-esp.sh esp32dev              # ESP32
#   ./flash-esp.sh esp8266_gpio14        # ESP8266 keeping D5 wiring
#   ./flash-esp.sh nodemcuv2 /dev/ttyUSB0
#
# Do not run this from SteamOS Game Mode while the service owns the port:
#   sudo systemctl stop steamos-utility-center

set -euo pipefail

ENVIRONMENT="${1:-nodemcuv2}"
PORT="${2:-}"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/firmware/led-client"

if ! command -v pio >/dev/null 2>&1; then
    # Look where the installers put it before giving up: the standalone one
    # leaves the PATH to your profile, which a non-login shell has not read.
    for candidate in "$HOME/.platformio/penv/bin" "$HOME/.local/bin"; do
        if [[ -x "$candidate/pio" ]]; then
            PATH="$candidate:$PATH"
            break
        fi
    done
fi

if ! command -v pio >/dev/null 2>&1; then
    cat >&2 <<'EOF'
PlatformIO (pio) not found. On SteamOS it has to live in your home directory:
the rootfs is read-only, so pip cannot write to it, and "pip install --user"
lands somewhere the next system update resets.

    curl -fsSL -o get-platformio.py \
      https://raw.githubusercontent.com/platformio/platformio-core-installer/master/get-platformio.py
    python3 get-platformio.py
    echo 'export PATH="$HOME/.platformio/penv/bin:$PATH"' >> ~/.bashrc

Or let the installer do it: sudo ./install.sh --flash nodemcuv2
EOF
    exit 1
fi

# Recent espressif32 platforms ship an esptool that imports intelhex, which is
# not always present in PlatformIO's bundled virtualenv. Without it the build
# dies at bootloader.bin with "No module named 'intelhex'".
#
# That virtualenv does not always have a pip either, and then the one line
# that installed the module printed "No module named pip" and stopped. A
# virtualenv made with --without-pip has none, and PlatformIO needs none after
# its own installation.
#
# So there are three ways here, and each one is tried in turn. The last of
# them writes into that virtualenv with a pip from somewhere else, which works
# because intelhex is pure Python: where it was built does not matter.
ensure_esptool_deps() {
    local core_dir="${PLATFORMIO_CORE_DIR:-$HOME/.platformio}"
    local penv_python="$core_dir/penv/bin/python"
    local log site other

    [[ -x "$penv_python" ]] || return 0
    "$penv_python" -c 'import intelhex' >/dev/null 2>&1 && return 0

    echo "PlatformIO's esptool needs the 'intelhex' module; installing it..."
    log="$(mktemp)"

    # 1. The pip of that virtualenv, when it has one.
    if "$penv_python" -m pip install --quiet intelhex >>"$log" 2>&1; then
        rm -f "$log"
        return 0
    fi

    # 2. Give it one. ensurepip is in the standard library and it carries the
    #    wheel of pip with it, so this step needs no network of its own.
    if "$penv_python" -m ensurepip --default-pip >>"$log" 2>&1 \
            && "$penv_python" -m pip install --quiet intelhex >>"$log" 2>&1; then
        rm -f "$log"
        return 0
    fi

    # 3. A pip from another Python, writing into that virtualenv. purelib is
    #    the directory of its own modules, so nothing here guesses a path.
    site="$("$penv_python" -c \
        'import sysconfig; print(sysconfig.get_paths()["purelib"])' \
        2>>"$log" || true)"
    if [[ -n "$site" ]]; then
        for other in python3 python; do
            command -v "$other" >/dev/null 2>&1 || continue
            "$other" -m pip install --quiet --target "$site" intelhex \
                >>"$log" 2>&1 || continue
            if "$penv_python" -c 'import intelhex' >/dev/null 2>&1; then
                rm -f "$log"
                return 0
            fi
        done
    fi

    cat >&2 <<EOF

Could not install 'intelhex'. These three were tried:

    $penv_python -m pip install intelhex
    $penv_python -m ensurepip --default-pip
    python3 -m pip install --target $site intelhex

and each one said:

$(cat "$log")

The board still has the firmware it had.
EOF
    rm -f "$log"
    exit 1
}

ensure_esptool_deps

# The panel asks for this before it stops the service. The modules that a
# build needs are a question with no serial port and no root in it, so a
# machine that cannot answer it must not pay for a stopped service first.
# See scripts/flash-firmware.sh.
if [[ "${SUC_PREPARE_ONLY:-0}" == "1" ]]; then
    exit 0
fi

if systemctl is-active --quiet steamos-utility-center.service 2>/dev/null; then
    echo "Stopping steamos-utility-center so the serial port is free..."
    sudo systemctl stop steamos-utility-center.service
    RESTART_SERVICE=1
fi

ARGS=(run -e "$ENVIRONMENT" -t upload -d "$PROJECT_DIR")
[[ -n "$PORT" ]] && ARGS+=(--upload-port "$PORT")

echo "Building and flashing environment '$ENVIRONMENT'..."
pio "${ARGS[@]}"

if [[ "${RESTART_SERVICE:-0}" == "1" ]]; then
    echo "Restarting steamos-utility-center..."
    sudo systemctl start steamos-utility-center.service
fi

# Read the rate out of the firmware config instead of duplicating it here.
EXPECTED_BAUD="$(sed -n "/^\[env:$ENVIRONMENT\]/,/^\[/p" "$PROJECT_DIR/platformio.ini" \
    | sed -n 's/.*-D SERIAL_BAUD=\([0-9]\+\).*/\1/p' | tail -1)"
[[ -n "$EXPECTED_BAUD" ]] || EXPECTED_BAUD=230400

cat <<EOF

Flashed environment '$ENVIRONMENT' (serial link: $EXPECTED_BAUD baud).

Make sure /etc/steamos-utility-center.conf matches:

    BAUD=$EXPECTED_BAUD

then: sudo systemctl restart steamos-utility-center
EOF

if [[ -f /etc/steamos-utility-center.conf ]]; then
    CONFIGURED_BAUD="$(sed -n 's/^BAUD=\([0-9]\+\).*/\1/p' /etc/steamos-utility-center.conf | tail -1)"
    if [[ -n "$CONFIGURED_BAUD" && "$CONFIGURED_BAUD" != "$EXPECTED_BAUD" ]]; then
        printf '\n\033[1;33mwarning:\033[0m config says BAUD=%s but the firmware runs at %s.\n' \
            "$CONFIGURED_BAUD" "$EXPECTED_BAUD"
    fi
fi
