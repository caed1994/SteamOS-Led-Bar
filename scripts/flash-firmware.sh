#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# Flashes the ESP firmware, giving it the serial port for the duration.
#
#   flash-firmware.sh <environment>
#
# Run through pkexec, but only half of this wants to be root: the service
# holds the port exclusively and has to step aside, which is privileged, while
# PlatformIO must run as whoever owns ~/.platformio - as root it would either
# not find pio at all or download a few hundred megabytes of toolchain into a
# root-owned home that breaks every later run. So it stops the service, hands
# the flashing back to the caller, and puts the service back afterwards -
# including when the flash fails or is interrupted.

set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="steamos-utility-center.service"
INI="$SOURCE_DIR/firmware/led-client/platformio.ini"

ENVIRONMENT="${1:-}"
[[ -n "$ENVIRONMENT" ]] \
    || { echo "usage: flash-firmware.sh <environment>" >&2; exit 2; }

# Everything that can be checked without touching the board or the service is
# checked first: being told a wrong name should cost nothing at all.
if ! grep -q "^\[env:$ENVIRONMENT\]" "$INI" 2>/dev/null; then
    echo "no firmware environment called '$ENVIRONMENT'. $INI has:" >&2
    sed -n 's/^\[env:\(.*\)\]$/  \1/p' "$INI" >&2
    exit 2
fi

# pkexec says who asked; sudo says it differently; running it directly means
# the caller is already the right person.
TARGET_UID="${PKEXEC_UID:-}"
if [[ -z "$TARGET_UID" && -n "${SUDO_UID:-}" ]]; then
    TARGET_UID="$SUDO_UID"
fi
if [[ -z "$TARGET_UID" ]]; then
    TARGET_UID="$(id -u)"
fi
TARGET_USER="$(id -nu "$TARGET_UID" 2>/dev/null || true)"
TARGET_HOME="$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6)"
if [[ -z "$TARGET_USER" || -z "$TARGET_HOME" ]]; then
    echo "cannot tell which user to flash as (uid $TARGET_UID)." >&2
    exit 1
fi

find_pio() {
    local candidate
    candidate="$(runuser -l "$TARGET_USER" -c 'command -v pio' 2>/dev/null \
                 | tail -1)"
    if [[ -n "$candidate" && -x "$candidate" ]]; then
        printf '%s' "$candidate"
        return 0
    fi
    for candidate in "$TARGET_HOME/.platformio/penv/bin/pio" \
                     "$TARGET_HOME/.local/bin/pio"; do
        [[ -x "$candidate" ]] && { printf '%s' "$candidate"; return 0; }
    done
    return 1
}

if ! PIO="$(find_pio)"; then
    # The installer is the answer rather than a pip line: on SteamOS the
    # rootfs is read-only, so a system-wide pip install cannot write at all,
    # and "pip install --user" lands in a directory the next system update
    # resets - which is a flash that works today and stops working after an
    # update, for no reason anybody would connect back to here.
    echo "PlatformIO (pio) not found for $TARGET_USER. Install it with:" >&2
    echo "    sudo $SOURCE_DIR/install.sh" >&2
    echo "which offers it, or by hand with PlatformIO's own installer:" >&2
    echo "    curl -fsSL -O \\" >&2
    echo "      https://raw.githubusercontent.com/platformio/platformio-core-installer/master/get-platformio.py" >&2
    echo "    python3 get-platformio.py" >&2
    echo "Nothing was changed; the board still has the firmware it had." >&2
    exit 1
fi

RESTART=0
if systemctl is-active --quiet "$SERVICE"; then
    RESTART=1
    echo "Stopping $SERVICE so the port is free ..."
    systemctl stop "$SERVICE"
fi

restore() {
    if [[ $RESTART -eq 1 ]]; then
        echo "Starting $SERVICE again ..."
        systemctl start "$SERVICE" || true
    fi
}
trap restore EXIT

# pkexec starts us in root's home, and the flashing runs as the caller - who
# cannot get back into /root afterwards. PlatformIO restores the directory it
# started in on the way out, so it ended a successful flash with
# "PermissionError: [Errno 13] Permission denied: '/root'" and an exit code
# that said the whole thing had failed. Stand somewhere they can both reach.
cd "$SOURCE_DIR"

echo "Flashing '$ENVIRONMENT' as $TARGET_USER ($PIO)"
if [[ "$(id -u)" == "$TARGET_UID" ]]; then
    env "PATH=$(dirname "$PIO"):$PATH" \
        bash "$SOURCE_DIR/flash-esp.sh" "$ENVIRONMENT"
else
    runuser -u "$TARGET_USER" -- env \
        "HOME=$TARGET_HOME" \
        "PATH=$(dirname "$PIO"):$PATH" \
        bash "$SOURCE_DIR/flash-esp.sh" "$ENVIRONMENT"
fi
