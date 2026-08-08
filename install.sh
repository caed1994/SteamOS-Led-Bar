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

# Where the achievement watcher's user unit lives, and how to reach the user's
# systemd. Shared with uninstall.sh so the two cannot disagree.
# shellcheck source=scripts/user-unit.sh
source "$SOURCE_DIR/scripts/user-unit.sh"

LED_COUNT=""
SERIAL_PORT=""
BAUD=""
ASSUME_YES=0
SKIP_MODULE=0
REBUILD_MODULE=0
SKIP_WATCHER=0
FLASH_ENV=""

# The firmware builds, in the order the menu offers them. Descriptions say
# which pin the strip goes on, because that is the part that has to match the
# wiring - see docs/WIRING.md.
FIRMWARE_ENVS=(
    "nodemcuv2:ESP8266 (NodeMCU, D1 mini), strip on GPIO2 - recommended"
    "esp8266_gpio14:ESP8266, strip on GPIO14 / D5 - keeps older wiring"
    "esp32dev:ESP32, strip on GPIO16"
    "esp32s3:ESP32-S3, strip on GPIO16"
    "d1_mini:ESP8266 with the D1 mini board profile, strip on GPIO2"
)

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
  --skip-watcher  do not install the achievement watcher user service
  --flash ENV     also flash the ESP firmware (e.g. nodemcuv2, esp32dev),
                  or a number from the menu; omit to be asked, 0 to skip
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
        --skip-watcher) SKIP_WATCHER=1; shift ;;
        --flash) FLASH_ENV="${2:-}"; shift 2 ;;
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

# --- firmware ---------------------------------------------------------------

# Turns "2", "esp32dev" or "" into an environment name, or nothing for "no".
resolve_firmware_choice() {
    local choice="$1" entry
    [[ -z "$choice" || "$choice" == "0" || "$choice" == "n" ]] && return 0

    if [[ "$choice" =~ ^[0-9]+$ ]]; then
        (( choice >= 1 && choice <= ${#FIRMWARE_ENVS[@]} )) \
            || die "pick a number between 0 and ${#FIRMWARE_ENVS[@]}"
        printf '%s' "${FIRMWARE_ENVS[choice - 1]%%:*}"
        return 0
    fi
    for entry in "${FIRMWARE_ENVS[@]}"; do
        [[ "$choice" == "${entry%%:*}" ]] && { printf '%s' "$choice"; return 0; }
    done
    die "unknown firmware environment: $choice"
}

if [[ -z "$FLASH_ENV" && $ASSUME_YES -eq 0 ]]; then
    # Default is 0: flashing is the one step that touches the hardware, and
    # nobody re-running the installer for a config change expects it.
    say "Flash the ESP firmware as well?"
    echo "   0  no - it is already flashed (default)"
    for index in "${!FIRMWARE_ENVS[@]}"; do
        printf '   %d  %s\n' "$((index + 1))" "${FIRMWARE_ENVS[index]#*:}"
    done
    FLASH_ENV="$(ask 'Firmware' 0)"
fi
FLASH_ENV="$(resolve_firmware_choice "$FLASH_ENV")"

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

# --- achievement watcher (a user service) ----------------------------------

# Set by install_achievement_watcher for the summary at the end, so the
# outcome is decided once where it is known rather than reconstructed later.
WATCHER_STATUS="not attempted"

install_achievement_watcher() {
    local source="$SOURCE_DIR/server/$WATCHER_UNIT"

    if [[ $SKIP_WATCHER -eq 1 ]]; then
        say "Skipping the achievement watcher (--skip-watcher)"
        WATCHER_STATUS="skipped (--skip-watcher)"
        return 0
    fi
    if [[ ! -f "$source" ]]; then
        warn "$WATCHER_UNIT not found in the repository"
        WATCHER_STATUS="NOT installed - unit missing from the repository"
        return 1
    fi

    # It has to run in the desktop session: Steamworks talks to the Steam
    # client of the logged-in user, and this script runs as root.
    if ! watcher_user_dirs; then
        warn "cannot tell which desktop user to install the watcher for."
        warn "Run the installer with sudo from your normal account, or start"
        warn "it yourself - see \"Flashing on a real achievement\" in the README."
        WATCHER_STATUS="NOT installed - run the installer with sudo from your account"
        return 1
    fi

    local wants="$WATCHER_DIR/$WATCHER_WANTS"
    say "Installing the achievement watcher for $WATCHER_USER"
    # Create the directories as the user: "install -d" would leave any missing
    # parent (~/.config on a fresh account) owned by root, which quietly breaks
    # everything else that writes there.
    if ! runuser -u "$WATCHER_USER" -- mkdir -p "$wants"; then
        warn "cannot create $wants"
        WATCHER_STATUS="NOT installed - could not write to $WATCHER_DIR"
        return 1
    fi
    # Same @INSTALL_DIR@ substitution as the system unit, so moving the
    # install directory keeps both of them pointing at the real binary.
    sed "s|@INSTALL_DIR@|$INSTALL_DIR|g" "$source" > "$WATCHER_DIR/$WATCHER_UNIT"
    chown "$WATCHER_USER:$WATCHER_USER" "$WATCHER_DIR/$WATCHER_UNIT"
    chmod 0644 "$WATCHER_DIR/$WATCHER_UNIT"

    # Enable by writing the symlink systemctl would create. Doing it directly
    # avoids needing the user's session bus, which root cannot reach reliably.
    ln -sfn "../$WATCHER_UNIT" "$wants/$WATCHER_UNIT"
    chown -h "$WATCHER_USER:$WATCHER_USER" "$wants/$WATCHER_UNIT"

    # If the user has a live session, pick it up now instead of at next login.
    user_systemctl daemon-reload || true
    if user_systemctl restart "$WATCHER_UNIT"; then
        say "Watcher running now"
        WATCHER_STATUS="running for $WATCHER_USER"
    else
        say "Watcher enabled; it starts with your next login"
        WATCHER_STATUS="enabled for $WATCHER_USER, starts at next login"
    fi
    return 0
}

# --- firmware ---------------------------------------------------------------

# Where PlatformIO lives depends on how it was installed: the standalone
# installer puts it in ~/.platformio/penv/bin, "pip install --user" in
# ~/.local/bin, a distribution package somewhere on the system PATH. So ask
# the user's login shell first - that is the setup that already works when
# they run ./flash-esp.sh by hand, whatever their profile does - and only fall
# back to the known locations if their profile does not put it on PATH.
find_pio() {
    local candidate
    candidate="$(runuser -l "$WATCHER_USER" -c 'command -v pio' 2>/dev/null \
                 | tail -1)"
    if [[ -n "$candidate" && -x "$candidate" ]]; then
        printf '%s' "$candidate"
        return 0
    fi
    for candidate in "$WATCHER_HOME/.platformio/penv/bin/pio" \
                     "$WATCHER_HOME/.local/bin/pio"; do
        if [[ -x "$candidate" ]]; then
            printf '%s' "$candidate"
            return 0
        fi
    done
    return 1
}

flash_firmware() {
    [[ -n "$FLASH_ENV" ]] || return 0

    # PlatformIO lives in the user's home - the toolchains land in
    # ~/.platformio and "pio" itself in ~/.local/bin - so this has to run as
    # them. As root it would either not find pio at all, or download a few
    # hundred megabytes of toolchain into a root-owned ~/.platformio that
    # breaks every later run.
    if ! watcher_user_dirs; then
        warn "cannot tell which user to flash as - run the installer with"
        warn "sudo from your normal account, or run ./flash-esp.sh yourself."
        return 1
    fi

    # The service holds the serial port exclusively. It is started further
    # down, so stopping any older copy here leaves the port free.
    systemctl stop steamos-led-serial.service >/dev/null 2>&1 || true

    local ini="$SOURCE_DIR/firmware/led-client/platformio.ini"
    if ! grep -q "^\[env:$FLASH_ENV\]" "$ini"; then
        warn "the menu offers '$FLASH_ENV' but $ini has no such environment."
        warn "The two have drifted apart - please report this."
        return 1
    fi

    local pio_path
    if ! pio_path="$(find_pio)"; then
        warn "PlatformIO (pio) not found for $WATCHER_USER. Install it with:"
        warn "    python3 -m pip install --user platformio"
        warn "The service is installed either way; flash later with"
        warn "    ./flash-esp.sh $FLASH_ENV"
        return 1
    fi

    say "Flashing firmware '$FLASH_ENV' as $WATCHER_USER (${pio_path})"
    runuser -u "$WATCHER_USER" -- env \
        "HOME=$WATCHER_HOME" \
        "PATH=$(dirname "$pio_path"):$PATH" \
        bash "$SOURCE_DIR/flash-esp.sh" "$FLASH_ENV"
}

FIRMWARE_STATUS="not flashed (say so at the prompt, or --flash, to change that)"
if [[ -n "$FLASH_ENV" ]]; then
    if flash_firmware; then
        FIRMWARE_STATUS="flashed: $FLASH_ENV"
    else
        FIRMWARE_STATUS="FAILED to flash $FLASH_ENV - see above"
        warn "firmware flashing failed; the service is installed either way."
    fi
fi

# --- control panel ----------------------------------------------------------

PANEL_STATUS="not installed"

install_control_panel() {
    local source="$SOURCE_DIR/gui/steamos-led-panel.desktop"
    [[ -f "$source" ]] || { PANEL_STATUS="not in the repository"; return 1; }

    if ! watcher_user_dirs; then
        PANEL_STATUS="skipped - no desktop user to install it for"
        return 1
    fi

    # The menu entry points into the clone rather than into INSTALL_DIR: the
    # panel's repair button re-runs install.sh, which only exists here.
    local dir="$WATCHER_HOME/.local/share/applications"
    runuser -u "$WATCHER_USER" -- mkdir -p "$dir" || {
        PANEL_STATUS="could not write to $dir"; return 1; }
    # An absolute path is a valid Icon= value, so the icon can live in the
    # clone next to the panel instead of being copied into an icon theme.
    local icon="$SOURCE_DIR/gui/steamos-led-panel.png"
    [[ -f "$icon" ]] || icon="preferences-desktop-display"
    sed -e "s|@SOURCE_DIR@|$SOURCE_DIR|g" -e "s|@ICON@|$icon|g" "$source" \
        > "$dir/steamos-led-panel.desktop"
    chown "$WATCHER_USER:$WATCHER_USER" "$dir/steamos-led-panel.desktop"
    chmod 0644 "$dir/steamos-led-panel.desktop"

    if runuser -u "$WATCHER_USER" -- python3 -c 'import tkinter' >/dev/null 2>&1
    then
        PANEL_STATUS="in the application menu as \"SteamOS LED bar\""
    else
        PANEL_STATUS="installed, but python3-tk is missing - see the README"
        warn "the control panel needs tkinter, which is not installed."
        warn "Everything it does is also available from the terminal."
    fi
    return 0
}

say "Installing the control panel menu entry"
install_control_panel || true

# --- start it --------------------------------------------------------------

say "Enabling steamos-led-serial.service"
systemctl daemon-reload
systemctl enable steamos-led-serial.service
# Deliberately not "enable --now": that starts a stopped service but leaves a
# running one alone, so installing over a running copy would keep serving the
# old code from the old unit - files updated, behaviour not. Restarting always
# lands on what was just installed.
systemctl restart steamos-led-serial.service

if systemctl is-active --quiet steamos-led-serial.service; then
    say "Service is running."
else
    warn "Service is not active. Check: journalctl -u steamos-led-serial -n 40"
fi

# The service creates the notification pipe at startup and carries on without
# it if that fails, logging one warning - which means a broken setup still
# reports "active" and simply never flashes. Check for it here instead.
notify_setting() {  # notify_setting <KEY> <default>
    local value=""
    if [[ -f "$CONFIG_PATH" ]]; then
        value="$(sed -n "s/^$1=\(.*\)\$/\1/p" "$CONFIG_PATH" \
                 | tail -1 | tr -d '[:space:]')"
    fi
    printf '%s' "${value:-$2}"
}

if [[ "$(notify_setting NOTIFY 1)" =~ ^(1|true|yes|on)$ ]]; then
    NOTIFY_FIFO="$(notify_setting NOTIFY_FIFO /run/steamos-led-serial/notify)"
    for _ in 1 2 3 4 5 6; do
        [[ -p "$NOTIFY_FIFO" ]] && break
        sleep 0.5
    done
    if [[ -p "$NOTIFY_FIFO" ]]; then
        say "Notification pipe ready ($NOTIFY_FIFO)"
    else
        warn "the service is running, but $NOTIFY_FIFO was not created -"
        warn "achievement and message flashes will not work."
        warn "The reason is in: journalctl -u steamos-led-serial -n 40"
    fi
fi

install_achievement_watcher || true

cat <<EOF

Done.

  Panel:    $PANEL_STATUS
  Firmware: $FIRMWARE_STATUS
  Config:   $CONFIG_PATH
  Logs:     journalctl -u steamos-led-serial -f
  Restart:  sudo systemctl restart steamos-led-serial

Achievement and message flashes: $WATCHER_STATUS
  Check:    $INSTALL_DIR/steamos-led-serial --steam-check   (with a game running)
  Log:      journalctl --user -u steamos-led-achievements -f

Test the strip without Steam (stop the service first so the port is free):

  sudo systemctl stop steamos-led-serial
  sudo $INSTALL_DIR/steamos-led-serial --self-test
  sudo systemctl start steamos-led-serial

EOF
