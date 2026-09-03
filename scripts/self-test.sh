#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# Runs the strip self-test, giving it the serial port for the duration.
#
#   self-test.sh [seconds]
#
# The service opens the port exclusively. It must thus stop and start again
# afterwards. That includes a test that fails and a test that a person
# interrupts.

set -euo pipefail

INSTALL_DIR="/var/lib/steamos-utility-center"
SERVICE="steamos-utility-center.service"
SECONDS_TO_RUN="${1:-12}"

[[ -x "$INSTALL_DIR/steamos-utility-center" ]] \
    || { echo "not installed: $INSTALL_DIR/steamos-utility-center" >&2; exit 2; }

RESTART=0
if systemctl is-active --quiet "$SERVICE"; then
    RESTART=1
    systemctl stop "$SERVICE"
fi

restore() {
    [[ $RESTART -eq 1 ]] && systemctl start "$SERVICE" || true
}
trap restore EXIT

"$INSTALL_DIR/steamos-utility-center" --self-test "$SECONDS_TO_RUN"
