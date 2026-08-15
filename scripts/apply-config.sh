#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# Installs a prepared configuration file and restarts the service.
#
#   apply-config.sh <staged-file>
#
# Split out of the control panel so the privileged step is one short script
# that can be read in full, rather than a shell command line assembled by a
# GUI. Run through pkexec; the panel itself stays unprivileged.

set -euo pipefail

CONFIG_PATH="/etc/steamos-led-serial.conf"
SERVICE="steamos-led-serial.service"

STAGED="${1:-}"
[[ -n "$STAGED" ]] || { echo "usage: apply-config.sh <staged-file>" >&2; exit 2; }
[[ -f "$STAGED" ]] || { echo "no such file: $STAGED" >&2; exit 2; }

# Refuse anything the service itself would refuse, before it replaces a
# working file - a rejected config would leave the service dead on restart.
INSTALL_DIR="/var/lib/steamos-led-serial"
if [[ -x "$INSTALL_DIR/steamos-led-serial" ]]; then
    # Ask the service itself whether it would accept the file. Anything else
    # would be a second copy of the validation rules, and getting that subtly
    # wrong means overwriting a working config with one the service refuses -
    # leaving it dead on restart.
    if ! rejection="$("$INSTALL_DIR/steamos-led-serial" --config "$STAGED" \
                      --check-config 2>&1 1>/dev/null)"; then
        echo "the new configuration was rejected, keeping the old one:" >&2
        echo "$rejection" >&2
        exit 1
    fi
fi

install -m 0644 "$STAGED" "$CONFIG_PATH"
systemctl restart "$SERVICE"
echo "Configuration applied and $SERVICE restarted."
