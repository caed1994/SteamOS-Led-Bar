#!/usr/bin/env bash
# Builds and flashes the LED client firmware with PlatformIO.
#
#   ./flash-esp.sh                       # ESP8266 on GPIO2 (recommended)
#   ./flash-esp.sh esp32dev              # ESP32
#   ./flash-esp.sh esp8266_gpio14        # ESP8266 keeping D5 wiring
#   ./flash-esp.sh nodemcuv2 /dev/ttyUSB0
#
# Do not run this from SteamOS Game Mode while the service owns the port:
#   sudo systemctl stop steamos-led-serial

set -euo pipefail

ENVIRONMENT="${1:-nodemcuv2}"
PORT="${2:-}"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/firmware/led-client"

if ! command -v pio >/dev/null 2>&1; then
    cat >&2 <<'EOF'
PlatformIO (pio) not found. Install it with:

    python3 -m pip install --user platformio
    export PATH="$HOME/.local/bin:$PATH"

On SteamOS, install it in your home directory - the rootfs is read-only.
EOF
    exit 1
fi

# Recent espressif32 platforms ship an esptool that imports intelhex, which is
# not always present in PlatformIO's bundled virtualenv. Without it the build
# dies at bootloader.bin with "No module named 'intelhex'".
ensure_esptool_deps() {
    local core_dir="${PLATFORMIO_CORE_DIR:-$HOME/.platformio}"
    local penv_python="$core_dir/penv/bin/python"

    [[ -x "$penv_python" ]] || return 0
    "$penv_python" -c 'import intelhex' >/dev/null 2>&1 && return 0

    echo "PlatformIO's esptool needs the 'intelhex' module; installing it..."
    if ! "$penv_python" -m pip install --quiet intelhex; then
        cat >&2 <<EOF

Could not install 'intelhex' automatically. Install it by hand:

    $penv_python -m pip install intelhex

EOF
        exit 1
    fi
}

ensure_esptool_deps

if systemctl is-active --quiet steamos-led-serial.service 2>/dev/null; then
    echo "Stopping steamos-led-serial so the serial port is free..."
    sudo systemctl stop steamos-led-serial.service
    RESTART_SERVICE=1
fi

ARGS=(run -e "$ENVIRONMENT" -t upload -d "$PROJECT_DIR")
[[ -n "$PORT" ]] && ARGS+=(--upload-port "$PORT")

echo "Building and flashing environment '$ENVIRONMENT'..."
pio "${ARGS[@]}"

if [[ "${RESTART_SERVICE:-0}" == "1" ]]; then
    echo "Restarting steamos-led-serial..."
    sudo systemctl start steamos-led-serial.service
fi

# Read the rate out of the firmware config instead of duplicating it here.
EXPECTED_BAUD="$(sed -n "/^\[env:$ENVIRONMENT\]/,/^\[/p" "$PROJECT_DIR/platformio.ini" \
    | sed -n 's/.*-D SERIAL_BAUD=\([0-9]\+\).*/\1/p' | tail -1)"
[[ -n "$EXPECTED_BAUD" ]] || EXPECTED_BAUD=230400

cat <<EOF

Flashed environment '$ENVIRONMENT' (serial link: $EXPECTED_BAUD baud).

Make sure /etc/steamos-led-serial.conf matches:

    BAUD=$EXPECTED_BAUD

then: sudo systemctl restart steamos-led-serial
EOF

if [[ -f /etc/steamos-led-serial.conf ]]; then
    CONFIGURED_BAUD="$(sed -n 's/^BAUD=\([0-9]\+\).*/\1/p' /etc/steamos-led-serial.conf | tail -1)"
    if [[ -n "$CONFIGURED_BAUD" && "$CONFIGURED_BAUD" != "$EXPECTED_BAUD" ]]; then
        printf '\n\033[1;33mwarning:\033[0m config says BAUD=%s but the firmware runs at %s.\n' \
            "$CONFIGURED_BAUD" "$EXPECTED_BAUD"
    fi
fi
