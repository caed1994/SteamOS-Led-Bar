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
POWER_UNIT_PATH="/etc/systemd/system/steamos-led-power.service"
POWER_CONFIG_PATH="/etc/steamos-led-power.conf"
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

# --- the read-only rootfs ---------------------------------------------------
#
# Before the first thing is removed, not before the kernel module. The suspend
# hook lives under /usr/lib/systemd, and `rm -f` on a locked rootfs does not
# quietly do nothing - it fails with "Read-only file system", which under
# set -e ended the uninstall three steps in: the udev rule gone, the service
# files, the command link and the configuration all still there, and nothing
# said about it. Shared with install.sh so the two cannot drift again.
unlock_rootfs || true

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

# --- what the installer put in that user's home -----------------------------
#
# Not under any root path, so none of the rm -f lines below reach it - and all
# of it was written by this project rather than by whoever is uninstalling.

remove_menu_entry() {
    watcher_user_dirs || return 0

    local applications="$WATCHER_HOME/$PANEL_ENTRY_DIR"
    local removed=0
    if [[ -f "$applications/$PANEL_ENTRY" ]]; then
        rm -f "$applications/$PANEL_ENTRY"
        removed=1
    fi

    # Every size rather than the one this clone's icon happens to be: the
    # installer files it under the width it reads out of the PNG, so an older
    # install may well have left one somewhere else.
    local icon
    for icon in "$WATCHER_HOME"/$PANEL_ICON_GLOB; do
        [[ -f "$icon" ]] || continue
        rm -f "$icon"
        removed=1
    done

    [[ $removed -eq 1 ]] || return 0
    echo "Removed the control panel's menu entry and icon for $WATCHER_USER."
    # Or the menu goes on offering it, and launching something that is no
    # longer there, until the cache is rebuilt by itself.
    refresh_desktop_caches "$applications"
}

# The two lines the installer appends to the user's .bashrc so "pio" is a
# command they can type. PlatformIO itself stays: it is somebody else's
# program, it may well be used for other things, and it is a few hundred
# megabytes nobody should have to fetch again. The lines naming this installer
# are ours to take back, and the summary says how to put them back by hand.
remove_platformio_path() {
    watcher_user_dirs || return 0

    local profile="$WATCHER_HOME/.bashrc"
    [[ -f "$profile" ]] || return 0
    grep -qF "$PLATFORMIO_PATH_MARK" "$profile" 2>/dev/null || return 0

    # By exact match, and as the user: anything looser would edit a line
    # somebody put there themselves, and rewriting it as root would leave
    # their own .bashrc owned by root. Written back through the same inode,
    # so its ownership and mode are whatever they already were.
    if runuser -u "$WATCHER_USER" -- bash -c '
            profile="$1"; scratch="$profile.steamos-led.$$"
            grep -vxF "$2" "$profile" | grep -vxF "$3" > "$scratch" || true
            cat "$scratch" > "$profile" && rm -f "$scratch"
        ' _ "$profile" "$PLATFORMIO_PATH_NOTE" "$PLATFORMIO_PATH_LINE"; then
        echo "Took the PlatformIO PATH line back out of $profile."
        PLATFORMIO_NOTE=1
    else
        echo "Could not edit $profile - remove this line by hand:" >&2
        echo "    $PLATFORMIO_PATH_LINE" >&2
    fi
}

PLATFORMIO_NOTE=0
remove_menu_entry
remove_platformio_path

# Stopping the service blanks the strip before the process exits.
systemctl disable --now steamos-led-serial.service 2>/dev/null || true
rm -f "$UNIT_PATH"

# The CPU settings. Disabling stops it being reapplied at the next boot; what
# is set right now is left as it is, because putting the governor back would
# mean knowing what it was before this was ever installed, and nothing here
# recorded that. It is a setting, not a change to be undone.
systemctl disable steamos-led-power.service 2>/dev/null || true
rm -f "$POWER_UNIT_PATH"
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
if [[ -L "$POWER_COMMAND_LINK" \
      && "$(readlink "$POWER_COMMAND_LINK")" == "$INSTALL_DIR/steamos-led-power" ]]; then
    rm -f "$POWER_COMMAND_LINK"
fi

rm -rf "${INSTALL_DIR:?}"

[[ $PURGE -eq 1 ]] && rm -f "$CONFIG_PATH" "$POWER_CONFIG_PATH"
echo "Removed."

# --- kernel module ---------------------------------------------------------
#
# The rootfs was unlocked at the top of this run and stays that way until the
# exit trap puts it back - see unlock_rootfs.

if [[ $REMOVE_MODULE -eq 1 ]]; then
    if lsmod | grep -q "^${MODULE_NAME//-/_}"; then
        rmmod "$MODULE_NAME" 2>/dev/null \
            || modprobe -r "$MODULE_NAME" 2>/dev/null || true
        echo "Module unloaded."
    fi
    rm -f "$MODULE_PATH" "$MODULES_LOAD"
    depmod "$RELEASE" || true
    echo "Module removed from $MODULE_PATH."
fi

# --- what is still here, and why -------------------------------------------
#
# Said rather than left to be discovered. Everything above is this project's
# own; everything below is either somebody else's, or yours, or a switch that
# other things may be hanging off - so it is reported instead of removed.

echo
echo "Left in place:"
if [[ $PURGE -eq 0 ]]; then
    echo "  $CONFIG_PATH"
    echo "  $POWER_CONFIG_PATH"
    echo "      your settings - --purge deletes it"
fi
if [[ $REMOVE_MODULE -eq 0 ]]; then
    echo "  the $MODULE_NAME kernel module"
    echo "      --remove-module unloads and removes it"
else
    echo "  /usr/lib/depmod.d/10-updates.conf"
    echo "      it only sets the general search order for updates/, and other"
    echo "      modules may rely on it"
fi
echo "  $SOURCE_DIR"
echo "      the clone, which is yours - delete it if you like"
if [[ -n "${WATCHER_USER:-}" ]] && linger_is_on "$WATCHER_USER"; then
    # Turned on by the installer if it was not already on, and not turned off
    # here: it is a per-user switch that anything else in that account may be
    # relying on by now, and taking it away would stop those too.
    echo "  lingering for $WATCHER_USER, so their services survive Game Mode"
    echo "      sudo loginctl disable-linger $WATCHER_USER"
fi
if [[ -n "${WATCHER_HOME:-}" && -d "$WATCHER_HOME/.platformio" ]]; then
    echo "  $WATCHER_HOME/.platformio"
    echo "      PlatformIO, which builds the ESP firmware. It is not ours,"
    echo "      and other projects may be using it."
    if [[ $PLATFORMIO_NOTE -eq 1 ]]; then
        echo "      To go on typing \"pio\", put this back in ~/.bashrc:"
        echo "        $PLATFORMIO_PATH_LINE"
    fi
fi
