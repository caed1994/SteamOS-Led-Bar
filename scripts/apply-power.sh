#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# Installs the CPU power settings and puts them into effect.
#
#   apply-power.sh <staged-file>
#
# This is separate from the control panel, as apply-config.sh is, and for the
# same reason. The step with root must be one short script that a person can
# read. It must not be a command line that a GUI builds.

set -euo pipefail

CONFIG_PATH="/etc/steamos-utility-center-power.conf"
SERVICE="steamos-utility-center-power.service"
INSTALL_DIR="/var/lib/steamos-utility-center"
APPLIER="$INSTALL_DIR/steamos-utility-center-power"

STAGED="${1:-}"
[[ -n "$STAGED" ]] || { echo "usage: apply-power.sh <staged-file>" >&2; exit 2; }
[[ -f "$STAGED" ]] || { echo "no such file: $STAGED" >&2; exit 2; }

# Refuse a value that this machine does not offer, before this replaces a file
# that operates.
#
# The program that applies the value knows the answer. A question to that
# program keeps the check and the write equal. A second copy of the rules does
# not.
if [[ -x "$APPLIER" ]]; then
    if ! rejection="$("$APPLIER" --apply --config "$STAGED" 2>&1)"; then
        echo "the new settings were rejected, keeping the old ones:" >&2
        echo "$rejection" >&2
        exit 1
    fi
    echo "$rejection"
fi

install -m 0644 "$STAGED" "$CONFIG_PATH"

# This enables the unit and does not start it. The settings are already in
# effect: the program above applied them. The purpose of the unit is to apply
# them again after a reboot.
#
# To enable a unit two times has the same result as one time, so two presses of
# Apply are not an error.
if [[ -f "/etc/systemd/system/$SERVICE" ]]; then
    systemctl enable "$SERVICE" >/dev/null 2>&1 \
        || echo "could not enable $SERVICE - the settings will not survive a reboot" >&2
fi

echo "CPU settings applied and written to $CONFIG_PATH."
