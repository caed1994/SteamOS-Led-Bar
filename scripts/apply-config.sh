#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# Installs a prepared configuration file and restarts the service.
#
#   apply-config.sh <staged-file>
#
# This is separate from the control panel, so that the step with root is one
# short script that a person can read. It is not a command line that a GUI
# builds. It runs through pkexec, and the panel itself has no rights.

set -euo pipefail

CONFIG_PATH="/etc/steamos-utility-center.conf"
SERVICE="steamos-utility-center.service"

STAGED="${1:-}"
[[ -n "$STAGED" ]] || { echo "usage: apply-config.sh <staged-file>" >&2; exit 2; }
[[ -f "$STAGED" ]] || { echo "no such file: $STAGED" >&2; exit 2; }

# Refuse each file that the service refuses, before this replaces a file that
# operates. A file that the service refuses stops the service at its next
# start.
INSTALL_DIR="/var/lib/steamos-utility-center"
if [[ -x "$INSTALL_DIR/steamos-utility-center" ]]; then
    # Ask the service whether it accepts the file. Each other method is a second
    # copy of the validation rules.
    #
    # A small mistake in that copy replaces a configuration that operates with one
    # that the service refuses. The service then does not start.
    if ! rejection="$("$INSTALL_DIR/steamos-utility-center" --config "$STAGED" \
                      --check-config 2>&1 1>/dev/null)"; then
        echo "the new configuration was rejected, keeping the old one:" >&2
        echo "$rejection" >&2
        exit 1
    fi
fi

install -m 0644 "$STAGED" "$CONFIG_PATH"
systemctl restart "$SERVICE"
echo "Configuration applied and $SERVICE restarted."
