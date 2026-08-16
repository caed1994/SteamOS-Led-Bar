#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# Removes the SteamOS LED bar USB-serial bridge.
#   sudo ./uninstall.sh [--purge] [--remove-module]
# --purge          also deletes /etc/steamos-led-serial.conf
# --remove-module  also unloads and removes the leds-valve-shim kernel module

set -euo pipefail

INSTALL_DIR="/var/lib/steamos-led-serial"
CONFIG_PATH="/etc/steamos-led-serial.conf"
UNIT_PATH="/etc/systemd/system/steamos-led-serial.service"
UDEV_PATH="/etc/udev/rules.d/99-steamos-led-serial.rules"
SLEEP_HOOK_PATH="/usr/lib/systemd/system-sleep/steamos-led-serial"

MODULE_NAME="leds-valve-shim"
RELEASE="$(uname -r)"
MODULE_PATH="/usr/lib/modules/$RELEASE/updates/${MODULE_NAME}.ko"
MODULES_LOAD="/etc/modules-load.d/steamos-led-bar.conf"

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/user-unit.sh
source "$SOURCE_DIR/scripts/user-unit.sh"

PURGE=0
REMOVE_MODULE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --purge) PURGE=1; shift ;;
        --remove-module) REMOVE_MODULE=1; shift ;;
        -h|--help) sed -n '2,5p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

[[ $EUID -eq 0 ]] || { echo "run as root: sudo ./uninstall.sh" >&2; exit 1; }

# --- the units that run in the desktop session ------------------------------

remove_user_units() {
    watcher_user_dirs || return 0

    local unit removed=0
    # Every unit this project ever installs, not only the ones it installs
    # today: walking the list is what stops an older install's file from being
    # left behind in somebody's ~/.config with nothing to remove it.
    for unit in "${WATCHER_UNITS[@]}"; do
        [[ -f "$WATCHER_DIR/$unit" ]] || continue
        user_systemctl stop "$unit" || true
        rm -f "$WATCHER_DIR/$unit" "$WATCHER_DIR/$WATCHER_WANTS/$unit"
        removed=1
    done
    [[ $removed -eq 1 ]] || return 0

    user_systemctl daemon-reload || true
    echo "Removed the desktop-session services for $WATCHER_USER."
}

remove_user_units

# Stopping the service blanks the strip before the process exits.
systemctl disable --now steamos-led-serial.service 2>/dev/null || true
rm -f "$UNIT_PATH"
systemctl daemon-reload

rm -f "$UDEV_PATH"
rm -f "$SLEEP_HOOK_PATH"
udevadm control --reload >/dev/null 2>&1 || true

# Only if it is still ours. Somebody who put their own steamos-led-serial
# there is entitled to keep it, and a link pointing somewhere else is not
# something this script installed.
if [[ -L "$COMMAND_LINK" \
      && "$(readlink "$COMMAND_LINK")" == "$INSTALL_DIR/steamos-led-serial" ]]; then
    rm -f "$COMMAND_LINK"
fi

rm -rf "${INSTALL_DIR:?}"

if [[ $PURGE -eq 1 ]]; then
    rm -f "$CONFIG_PATH"
    echo "Removed, including $CONFIG_PATH."
else
    echo "Removed. Kept $CONFIG_PATH (use --purge to delete it)."
fi

if [[ $REMOVE_MODULE -eq 0 ]]; then
    echo "The $MODULE_NAME kernel module was left in place (--remove-module removes it)."
    exit 0
fi

# --- kernel module ---------------------------------------------------------

ROOTFS_WAS_READONLY=0
restore_readonly() {
    if [[ $ROOTFS_WAS_READONLY -eq 1 ]] && command -v steamos-readonly >/dev/null 2>&1; then
        steamos-readonly enable || true
    fi
}
trap restore_readonly EXIT

if command -v steamos-readonly >/dev/null 2>&1; then
    if steamos-readonly status 2>/dev/null | grep -qi enabled; then
        echo "Disabling the read-only rootfs to remove the module..."
        steamos-readonly disable
        ROOTFS_WAS_READONLY=1
    fi
fi

if lsmod | grep -q "^${MODULE_NAME//-/_}"; then
    rmmod "$MODULE_NAME" 2>/dev/null || modprobe -r "$MODULE_NAME" 2>/dev/null || true
    echo "Module unloaded."
fi

rm -f "$MODULE_PATH" "$MODULES_LOAD"
depmod "$RELEASE" || true
echo "Module removed from $MODULE_PATH."
# /usr/lib/depmod.d/10-updates.conf is left alone: it only sets the general
# search order for updates/ and other modules may rely on it.
