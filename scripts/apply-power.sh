#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# Installs the CPU power settings and puts them into effect.
#
#   apply-power.sh <staged-file>
#
# Split out of the control panel the same way apply-config.sh is, and for the
# same reason: the privileged step should be one short script that can be read
# in full rather than a command line assembled by a GUI.

set -euo pipefail

CONFIG_PATH="/etc/steamos-led-power.conf"
SERVICE="steamos-led-power.service"
INSTALL_DIR="/var/lib/steamos-led-serial"
APPLIER="$INSTALL_DIR/steamos-led-power"

STAGED="${1:-}"
[[ -n "$STAGED" ]] || { echo "usage: apply-power.sh <staged-file>" >&2; exit 2; }
[[ -f "$STAGED" ]] || { echo "no such file: $STAGED" >&2; exit 2; }

# Refuse a value this machine does not offer before it replaces a working
# file. The applier itself is what knows the answer - asking it here rather
# than repeating the rules means the check and the write cannot disagree.
if [[ -x "$APPLIER" ]]; then
    if ! rejection="$("$APPLIER" --apply --config "$STAGED" 2>&1)"; then
        echo "the new settings were rejected, keeping the old ones:" >&2
        echo "$rejection" >&2
        exit 1
    fi
    echo "$rejection"
fi

install -m 0644 "$STAGED" "$CONFIG_PATH"

# Enabled rather than started: the settings are already in effect - the applier
# above did that - and what the unit is for is putting them back after a
# reboot. Enabling it is idempotent, so pressing Apply twice is not an error.
if [[ -f "/etc/systemd/system/$SERVICE" ]]; then
    systemctl enable "$SERVICE" >/dev/null 2>&1 \
        || echo "could not enable $SERVICE - the settings will not survive a reboot" >&2
fi

echo "CPU settings applied and written to $CONFIG_PATH."
