#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# Flashes the ESP firmware, giving it the serial port for the duration.
#
#   flash-firmware.sh <environment>
#
# This runs through pkexec, and one half of it needs root.
#
# The service opens the port exclusively and must stop, and that step needs
# root. PlatformIO must run as the owner of ~/.platformio.
#
# As root, PlatformIO does one of two things: it does not find pio, or it
# downloads some hundred megabytes of tools into a home directory that belongs
# to root. The second result stops each later run.
#
# This script thus stops the service, gives the flash back to the caller, and
# starts the service again. It does that also when the flash fails and when a
# person interrupts it.

set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="steamos-utility-center.service"
INI="$SOURCE_DIR/firmware/led-client/platformio.ini"

ENVIRONMENT="${1:-}"
[[ -n "$ENVIRONMENT" ]] \
    || { echo "usage: flash-firmware.sh <environment>" >&2; exit 2; }

# This checks each value that it can check with no change to the board and no
# change to the service. A wrong name thus costs nothing.
if ! grep -q "^\[env:$ENVIRONMENT\]" "$INI" 2>/dev/null; then
    echo "no firmware environment called '$ENVIRONMENT'. $INI has:" >&2
    sed -n 's/^\[env:\(.*\)\]$/  \1/p' "$INI" >&2
    exit 2
fi

# pkexec gives the caller in one variable. sudo gives it in another. A direct
# run means that the caller is already the correct person.
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
    # The installer is the correct answer and a pip line is not. The root
    # filesystem of SteamOS is read-only, so a pip install for the system
    # cannot write.
    #
    # "pip install --user" writes into a directory that the next system update
    # erases. The flash thus operates today and fails after an update, and
    # nobody connects that failure to this line.
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

# pkexec starts this script in the home directory of root, and the flash runs
# as the caller. That caller cannot return into /root.
#
# PlatformIO changes back to its start directory at its exit. A successful
# flash thus ended with "PermissionError: [Errno 13] Permission denied:
# '/root'" and an exit code that reported a full failure.
#
# This script thus changes to a directory that both users can reach.
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
