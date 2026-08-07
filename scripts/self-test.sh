#!/usr/bin/env bash
# Runs the strip self-test, giving it the serial port for the duration.
#
#   self-test.sh [seconds]
#
# The service holds the port exclusively, so it has to step aside and come
# back afterwards - including when the test fails or is interrupted.

set -euo pipefail

INSTALL_DIR="/var/lib/steamos-led-serial"
SERVICE="steamos-led-serial.service"
SECONDS_TO_RUN="${1:-12}"

[[ -x "$INSTALL_DIR/steamos-led-serial" ]] \
    || { echo "not installed: $INSTALL_DIR/steamos-led-serial" >&2; exit 2; }

RESTART=0
if systemctl is-active --quiet "$SERVICE"; then
    RESTART=1
    systemctl stop "$SERVICE"
fi

restore() {
    [[ $RESTART -eq 1 ]] && systemctl start "$SERVICE" || true
}
trap restore EXIT

"$INSTALL_DIR/steamos-led-serial" --self-test "$SECONDS_TO_RUN"
