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
SKIP_MODULE=0
REBUILD_MODULE=0

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m warning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m error:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    cat <<'EOF'

Options:
  --leds N        number of LEDs on the strip (default: 17)
  --port PATH     serial device, or "auto" (default: auto)
  --baud RATE     serial baud rate (default: 230400)
  --skip-module   do not touch the leds-valve-shim kernel module
  --rebuild-module  rebuild and reinstall the module even if it is loaded
  -y, --yes       accept defaults, no prompts
  -h, --help      this text
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --leds) LED_COUNT="${2:-}"; shift 2 ;;
        --port) SERIAL_PORT="${2:-}"; shift 2 ;;
        --baud) BAUD="${2:-}"; shift 2 ;;
        --skip-module) SKIP_MODULE=1; shift ;;
        --rebuild-module) REBUILD_MODULE=1; shift ;;
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

# --- kernel shim -----------------------------------------------------------

module_build_hint() {
    cat >&2 <<EOF

The module needs make, gcc and the kernel headers for $(uname -r):

  SteamOS / Arch:  sudo steamos-readonly disable
                   sudo pacman -S base-devel
                   pacman -Ss headers | grep "\$(uname -r | cut -d- -f3-)"
  Debian/Ubuntu:   sudo apt install build-essential "linux-headers-\$(uname -r)"
  Fedora:          sudo dnf install kernel-devel-\$(uname -r) gcc make

Re-run this installer afterwards, or: sudo ./install.sh --rebuild-module

EOF
}

install_shim_module() {
    local dir="$SOURCE_DIR/leds-valve-shim"
    local release module
    release="$(uname -r)"
    module="$dir/leds-valve-shim.ko"

    if [[ $SKIP_MODULE -eq 1 ]]; then
        say "Leaving the kernel module alone (--skip-module)"
        return 0
    fi

    modprobe leds-valve-shim >/dev/null 2>&1 || true
    if [[ -e "$SHIM_DEVICE" && $REBUILD_MODULE -eq 0 ]]; then
        say "Kernel module already active ($SHIM_DEVICE exists)"
        return 0
    fi

    # A .ko built for another kernel makes the module installer abort on its
    # vermagic check. Drop it so a kernel update simply rebuilds.
    if [[ -f "$module" ]]; then
        local vermagic
        vermagic="$(modinfo -F vermagic "$module" 2>/dev/null | awk '{print $1}')"
        if [[ "$vermagic" != "$release" || $REBUILD_MODULE -eq 1 ]]; then
            say "Discarding stale module build (${vermagic:-unknown} != $release)"
            rm -f "$module"
        fi
    fi

    if [[ ! -f "$module" ]]; then
        local missing=()
        command -v make >/dev/null 2>&1 || missing+=("make")
        command -v gcc >/dev/null 2>&1 || missing+=("gcc")
        [[ -d "/lib/modules/$release/build" ]] || missing+=("kernel headers")
        if (( ${#missing[@]} > 0 )); then
            warn "cannot build the kernel module, missing: ${missing[*]}"
            module_build_hint
            return 1
        fi
    fi

    say "Building and installing the leds-valve-shim kernel module"
    "$dir/install.sh" || return 1
    [[ -e "$SHIM_DEVICE" ]] || return 1
    return 0
}

MODULE_OK=1
install_shim_module || MODULE_OK=0

if [[ $MODULE_OK -eq 0 ]]; then
    warn "$SHIM_DEVICE is not available."
    warn "The service will still be installed and waits for the device to appear."
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
