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

# The staged file comes from a directory that the desktop user can write, and
# this program runs as root.
#
# A symlink there would make this read a file that the user cannot read.
# install(1) would then copy, say, /etc/shadow into a file that everybody can
# read. So this refuses anything but a regular file, and it refuses one that
# belongs to somebody else.
#
# Nobody asked when the boot-time unit calls this with the record, and there is
# no user to compare against in that case. That one is permitted, and every
# other call must name a file of the user who made it.
#
# The same check is in apply-config.sh, apply-power.sh and apply-mounts.sh, and
# tests/test_ctl.py runs the three of them to keep them equal.
ASKED_BY="${PKEXEC_UID:-${SUDO_UID:-}}"
[[ ! -L "$STAGED" ]] || { echo "$STAGED is a symlink" >&2; exit 2; }
[[ -f "$STAGED" ]] || { echo "no such file: $STAGED" >&2; exit 2; }
STAGED_OWNER="$(stat -c %u "$STAGED" 2>/dev/null || echo -1)"
[[ -z "$ASKED_BY" || "$STAGED_OWNER" == "$ASKED_BY" ]] \
    || { echo "$STAGED does not belong to whoever asked" >&2; exit 2; }

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

# Clear the start counter before the restart.
#
# systemd counts five starts in ten seconds and then refuses the sixth:
#
#   Job for steamos-utility-center.service failed because start of the
#   service was attempted too often.
#
# That rule is for a service that crashes and starts again by itself. A person
# who moves a slider in Game Mode is not that: each step of the slider is a
# change, each change is a restart, and the service was dead after two
# seconds of moving one. The bar then stayed dark until somebody found
# `systemctl reset-failed` in a terminal.
#
# The limit stays for the case it is written for. This clears the counter for
# a change that a person asked for, which is the only kind that reaches here.
# It does nothing on a unit that is not failed, and it exits 0 either way.
systemctl reset-failed "$SERVICE" >/dev/null 2>&1 || true
systemctl restart "$SERVICE"
echo "Configuration applied and $SERVICE restarted."
