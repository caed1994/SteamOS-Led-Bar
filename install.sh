#!/usr/bin/env bash
# Installer for the SteamOS LED bar USB-serial bridge.
#
#   sudo ./install.sh                 interactive
#   sudo ./install.sh --leds 60 --port /dev/steamos-led-esp --yes
#
# Everything lands in /var/lib so it survives SteamOS system updates, which
# reset the read-only rootfs.

set -euo pipefail

INSTALL_DIR="/var/lib/steamos-led-serial"
CONFIG_PATH="/etc/steamos-led-serial.conf"
UNIT_PATH="/etc/systemd/system/steamos-led-serial.service"
UDEV_PATH="/etc/udev/rules.d/99-steamos-led-serial.rules"
SHIM_DEVICE="/dev/valve-leds-shim"

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

LED_COUNT=""
SERIAL_PORT=""
BAUD=""
ASSUME_YES=0

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m warning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m error:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    cat <<'EOF'

Options:
  --leds N        number of LEDs on the strip (default: 17)
  --port PATH     serial device, or "auto" (default: auto)
  --baud RATE     serial baud rate (default: 460800)
  -y, --yes       accept defaults, no prompts
  -h, --help      this text
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --leds) LED_COUNT="${2:-}"; shift 2 ;;
        --port) SERIAL_PORT="${2:-}"; shift 2 ;;
        --baud) BAUD="${2:-}"; shift 2 ;;
        -y|--yes) ASSUME_YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
done

[[ $EUID -eq 0 ]] || die "run as root: sudo ./install.sh"
command -v python3 >/dev/null || die "python3 not found"
[[ -f "$SOURCE_DIR/server/steamos-led-serial" ]] \
    || die "run this script from inside the cloned repository"

# --- gather settings -------------------------------------------------------

ask() {  # ask <prompt> <default>
    local answer
    if [[ $ASSUME_YES -eq 1 ]]; then
        printf '%s' "$2"
        return
    fi
    read -r -p "$1 [$2]: " answer </dev/tty || answer=""
    printf '%s' "${answer:-$2}"
}

if [[ -z "$LED_COUNT" ]]; then
    LED_COUNT="$(ask 'Number of LEDs on the strip' 17)"
fi
[[ "$LED_COUNT" =~ ^[0-9]+$ ]] && (( LED_COUNT >= 1 && LED_COUNT <= 1024 )) \
    || die "LED count must be a number between 1 and 1024"

if [[ -z "$SERIAL_PORT" ]]; then
    say "Detected USB serial devices:"
    if ! python3 "$SOURCE_DIR/server/steamos-led-serial" --list-ports 2>/dev/null; then
        echo "   (none - plug the ESP in, or set the port later in $CONFIG_PATH)"
    fi
    SERIAL_PORT="$(ask 'Serial port ("auto" picks the first ESP adapter)' auto)"
fi

[[ -n "$BAUD" ]] || BAUD="$(ask 'Baud rate (all shipped firmware uses 230400)' 230400)"
[[ "$BAUD" =~ ^[0-9]+$ ]] || die "baud rate must be a number"

# --- install ---------------------------------------------------------------

say "Installing service to $INSTALL_DIR"
install -d -m 0755 "$INSTALL_DIR"
rm -rf "${INSTALL_DIR:?}/steamos_led"
cp -r "$SOURCE_DIR/server/steamos_led" "$INSTALL_DIR/"
install -m 0755 "$SOURCE_DIR/server/steamos-led-serial" "$INSTALL_DIR/steamos-led-serial"
find "$INSTALL_DIR/steamos_led" -type f -exec chmod 0644 {} +

if [[ -f "$CONFIG_PATH" ]]; then
    say "Keeping existing $CONFIG_PATH"
    warn "check that LED_COUNT/SERIAL_PORT/BAUD there still match your setup"
else
    say "Writing $CONFIG_PATH"
    install -m 0644 "$SOURCE_DIR/server/steamos-led-serial.conf" "$CONFIG_PATH"
    sed -i \
        -e "s|^LED_COUNT=.*|LED_COUNT=$LED_COUNT|" \
        -e "s|^SERIAL_PORT=.*|SERIAL_PORT=$SERIAL_PORT|" \
        -e "s|^BAUD=.*|BAUD=$BAUD|" \
        "$CONFIG_PATH"
fi

say "Installing udev rule to $UDEV_PATH"
install -m 0644 "$SOURCE_DIR/udev/99-steamos-led-serial.rules" "$UDEV_PATH"
udevadm control --reload >/dev/null 2>&1 || warn "could not reload udev rules"
udevadm trigger --subsystem-match=tty >/dev/null 2>&1 || true

say "Installing systemd unit to $UNIT_PATH"
sed "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
    "$SOURCE_DIR/server/steamos-led-serial.service" > "$UNIT_PATH"
chmod 0644 "$UNIT_PATH"

# --- kernel shim check -----------------------------------------------------

if [[ ! -e "$SHIM_DEVICE" ]]; then
    modprobe leds-valve-shim >/dev/null 2>&1 || true
fi
if [[ ! -e "$SHIM_DEVICE" ]]; then
    warn "$SHIM_DEVICE is missing - the leds-valve-shim kernel module is not loaded."
    warn "The service will start and wait for it. Install the module from"
    warn "https://github.com/rpf16rj/steamos-led-bar-release (leds-valve-shim/install.sh),"
    warn "then: sudo systemctl restart steamos-led-serial"
fi

# --- enable ----------------------------------------------------------------

say "Enabling steamos-led-serial.service"
systemctl daemon-reload
systemctl enable --now steamos-led-serial.service

sleep 1
if systemctl is-active --quiet steamos-led-serial.service; then
    say "Service is running."
else
    warn "Service is not active. Check: journalctl -u steamos-led-serial -n 40"
fi

cat <<EOF

Done.

  Config:   $CONFIG_PATH
  Logs:     journalctl -u steamos-led-serial -f
  Restart:  sudo systemctl restart steamos-led-serial

Test the strip without Steam (stop the service first so the port is free):

  sudo systemctl stop steamos-led-serial
  sudo $INSTALL_DIR/steamos-led-serial --self-test
  sudo systemctl start steamos-led-serial

EOF
