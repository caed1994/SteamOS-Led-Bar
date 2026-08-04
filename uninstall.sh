#!/usr/bin/env bash
# Removes the SteamOS LED bar USB-serial bridge.
#   sudo ./uninstall.sh [--purge] [--remove-module]
# --purge          also deletes /etc/steamos-led-serial.conf
# --remove-module  also unloads and removes the leds-valve-shim kernel module

set -euo pipefail

INSTALL_DIR="/var/lib/steamos-led-serial"
CONFIG_PATH="/etc/steamos-led-serial.conf"
UNIT_PATH="/etc/systemd/system/steamos-led-serial.service"
UDEV_PATH="/etc/udev/rules.d/99-steamos-led-serial.rules"

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

# --- achievement watcher (a user service) ----------------------------------

remove_achievement_watcher() {
    watcher_user_dirs || return 0
    [[ -f "$WATCHER_DIR/$WATCHER_UNIT" ]] || return 0

    user_systemctl stop "$WATCHER_UNIT" || true
    rm -f "$WATCHER_DIR/$WATCHER_UNIT" \
          "$WATCHER_DIR/$WATCHER_WANTS/$WATCHER_UNIT"
    user_systemctl daemon-reload || true
    echo "Removed the achievement watcher for $WATCHER_USER."
}

remove_achievement_watcher

# Stopping the service blanks the strip before the process exits.
systemctl disable --now steamos-led-serial.service 2>/dev/null || true
rm -f "$UNIT_PATH"
systemctl daemon-reload

rm -f "$UDEV_PATH"
udevadm control --reload >/dev/null 2>&1 || true

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
