#!/usr/bin/env bash
# Removes the SteamOS LED bar USB-serial bridge.
#   sudo ./uninstall.sh [--purge]
# --purge also deletes /etc/steamos-led-serial.conf.

set -euo pipefail

INSTALL_DIR="/var/lib/steamos-led-serial"
CONFIG_PATH="/etc/steamos-led-serial.conf"
UNIT_PATH="/etc/systemd/system/steamos-led-serial.service"
UDEV_PATH="/etc/udev/rules.d/99-steamos-led-serial.rules"

PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1

[[ $EUID -eq 0 ]] || { echo "run as root: sudo ./uninstall.sh" >&2; exit 1; }

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
echo "The leds-valve-shim kernel module was not touched."
