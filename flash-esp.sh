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

cat <<EOF

Flashed. Reminder:
  - environment 'esp8266_gpio14' runs at 250000 baud; set BAUD=250000 in
    /etc/steamos-led-serial.conf to match.
  - all other environments use the default in platformio.ini.
EOF
