#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# Installer for the SteamOS Utility Center.
#
#   sudo ./install.sh                 interactive
#   sudo ./install.sh --leds 60 --port /dev/steamos-led-esp --yes
#
# Everything lands in /var/lib so it survives SteamOS system updates, which
# reset the read-only rootfs.

set -euo pipefail

# Named by the kernel module, which is vendored from another project and is
# not ours to rename - see scripts/user-unit.sh.
SHIM_DEVICE="/dev/valve-leds-shim"

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Every path this installs to, where the achievement watcher's user unit
# lives, and how to reach the user's systemd. Shared with uninstall.sh so the
# two cannot disagree - they used to hold their own copies of the paths.
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
# wiring - see docs/WIRING.md. None of them is recommended over another: the
# soldering has already decided, and a hint here only sends people looking for
# a difference that is not there.
FIRMWARE_ENVS=(
    "nodemcuv2:ESP8266 (NodeMCU, D1 mini), strip on GPIO2"
    "esp8266_gpio14:ESP8266, strip on GPIO14 / D5 - keeps older wiring"
    "esp32dev:ESP32, strip on GPIO16"
    "esp32s3:ESP32-S3, strip on GPIO16"
    "d1_mini:ESP8266 with the D1 mini board profile, strip on GPIO2"
)

# say() and warn() come from scripts/user-unit.sh, which the shared code there
# uses as well. die() is the installer's own: stopping is its decision.
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
  --skip-watcher  do not install the desktop-session user services
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
[[ -f "$SOURCE_DIR/server/steamos-utility-center" ]] \
    || die "run this script from inside the cloned repository"

# --- the read-only rootfs ---------------------------------------------------
#
# Once, before anything is asked or written. pacman needs it as much as the
# suspend hook and the kernel module do, and doing it around each write in turn
# is how the hook ended up being installed while the rootfs had been locked
# again, which aborts the whole run. See scripts/user-unit.sh.

unlock_rootfs || true

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
    if ! python3 "$SOURCE_DIR/server/steamos-utility-center" --list-ports 2>/dev/null; then
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

# Before a single new file is written. The old install has to be stopped and
# out of the way first: its service holds the serial port the new one is about
# to want, and its settings have to be carried across before the step below
# would write fresh defaults over the top of nothing. See migrate_old_install.
migrate_old_install

say "Installing service to $INSTALL_DIR"
install -d -m 0755 "$INSTALL_DIR"
rm -rf "${INSTALL_DIR:?}/steamos_utility_center"
cp -r "$SOURCE_DIR/server/steamos_utility_center" "$INSTALL_DIR/"
install -m 0755 "$SOURCE_DIR/server/steamos-utility-center" "$INSTALL_DIR/steamos-utility-center"
install -m 0755 "$SOURCE_DIR/server/steamos-utility-center-power" "$INSTALL_DIR/steamos-utility-center-power"
find "$INSTALL_DIR/steamos_utility_center" -type f -exec chmod 0644 {} +

# A name to type. Everything lives in /var/lib so it survives a SteamOS
# update, and nothing there is on anybody's PATH - so every command in the
# README was one people could read and not run. A symlink costs nothing and
# makes the documentation true.
#
# On the read-only rootfs, so a SteamOS update takes it away again along with
# the kernel module - and the same re-run of this script puts both back. Not
# fatal either way: the full path works whether this exists or not.
# COMMAND_LINK is in scripts/user-unit.sh, so the uninstaller removes the same
# one this creates.
COMMAND_STATUS="$INSTALL_DIR/steamos-utility-center"

if install -d -m 0755 "$(dirname "$COMMAND_LINK")" 2>/dev/null \
   && ln -sfn "$INSTALL_DIR/steamos-utility-center" "$COMMAND_LINK" 2>/dev/null; then
    say "Linking $COMMAND_LINK"
    COMMAND_STATUS="steamos-utility-center"
else
    warn "could not write $COMMAND_LINK - run it by its full path instead"
fi

ln -sfn "$INSTALL_DIR/steamos-utility-center-power" "$POWER_COMMAND_LINK" 2>/dev/null \
    || warn "could not write $POWER_COMMAND_LINK - run it by its full path"

if [[ -f "$CONFIG_PATH" ]]; then
    say "Keeping existing $CONFIG_PATH"
    warn "check that LED_COUNT/SERIAL_PORT/BAUD there still match your setup"
else
    say "Writing $CONFIG_PATH"
    install -m 0644 "$SOURCE_DIR/server/steamos-utility-center.conf" "$CONFIG_PATH"
    sed -i \
        -e "s|^LED_COUNT=.*|LED_COUNT=$LED_COUNT|" \
        -e "s|^SERIAL_PORT=.*|SERIAL_PORT=$SERIAL_PORT|" \
        -e "s|^BAUD=.*|BAUD=$BAUD|" \
        "$CONFIG_PATH"
fi

say "Installing udev rule to $UDEV_PATH"
install -m 0644 "$SOURCE_DIR/udev/99-steamos-utility-center.rules" "$UDEV_PATH"
udevadm control --reload >/dev/null 2>&1 || warn "could not reload udev rules"
udevadm trigger --subsystem-match=tty >/dev/null 2>&1 || true

say "Installing the suspend hook to $SLEEP_HOOK_PATH"
# What tells the strip the machine is going to sleep. Without it the strip
# simply goes dark during a suspend, as it did before - so a system without
# /usr/lib/systemd/system-sleep is a missing feature, not a failed install.
if [ -d "$(dirname "$SLEEP_HOOK_PATH")" ]; then
    install -m 0755 "$SOURCE_DIR/systemd-sleep/steamos-utility-center" \
        "$SLEEP_HOOK_PATH"
else
    warn "no $(dirname "$SLEEP_HOOK_PATH") - the strip will go dark in standby"
fi

say "Installing systemd unit to $UNIT_PATH"
sed "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
    "$SOURCE_DIR/server/steamos-utility-center.service" > "$UNIT_PATH"
chmod 0644 "$UNIT_PATH"

# The CPU settings. Its unit is installed but deliberately not enabled: with
# nothing set in the config file it would run at every boot to do nothing, and
# a service somebody did not ask for is a service they have to wonder about.
# The panel enables it the first time a setting is applied - see
# scripts/apply-power.sh.
say "Installing systemd unit to $POWER_UNIT_PATH"
sed "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
    "$SOURCE_DIR/server/steamos-utility-center-power.service" > "$POWER_UNIT_PATH"
chmod 0644 "$POWER_UNIT_PATH"

if [[ -f "$POWER_CONFIG_PATH" ]]; then
    say "Keeping existing $POWER_CONFIG_PATH"
else
    say "Writing $POWER_CONFIG_PATH"
    install -m 0644 "$SOURCE_DIR/server/steamos-utility-center-power.conf" \
        "$POWER_CONFIG_PATH"
fi

# --- the units that run in the desktop session ------------------------------

# Set by install_user_units for the summary at the end, so the outcome is
# decided once where it is known rather than reconstructed later.
WATCHER_STATUS="not attempted"
# Appended to it: installed and running is only half the answer if they stop
# the moment you switch to Game Mode.
LINGER_NOTE=""

# One unit into the user's systemd. Says nothing: the caller owns the summary,
# because it is the one that knows whether a failure here is the whole story.
install_one_user_unit() {
    local unit="$1"
    local source="$SOURCE_DIR/server/$unit"
    local wants="$WATCHER_DIR/$WATCHER_WANTS"

    [[ -f "$source" ]] || return 1
    # Same @INSTALL_DIR@ substitution as the system unit, so moving the
    # install directory keeps them all pointing at the real binary.
    sed "s|@INSTALL_DIR@|$INSTALL_DIR|g" "$source" > "$WATCHER_DIR/$unit" \
        || return 1
    chown "$WATCHER_USER:$WATCHER_USER" "$WATCHER_DIR/$unit"
    chmod 0644 "$WATCHER_DIR/$unit"

    # Enable by writing the symlink systemctl would create. Doing it directly
    # avoids needing the user's session bus, which root cannot reach reliably.
    ln -sfn "../$unit" "$wants/$unit"
    chown -h "$WATCHER_USER:$WATCHER_USER" "$wants/$unit"
    return 0
}

install_user_units() {
    if [[ $SKIP_WATCHER -eq 1 ]]; then
        say "Skipping the desktop-session services (--skip-watcher)"
        WATCHER_STATUS="skipped (--skip-watcher)"
        return 0
    fi

    local unit
    for unit in "${WATCHER_UNITS[@]}"; do
        if [[ ! -f "$SOURCE_DIR/server/$unit" ]]; then
            warn "$unit not found in the repository"
            WATCHER_STATUS="NOT installed - unit missing from the repository"
            return 1
        fi
    done

    # They have to run in the desktop session: Steamworks talks to the Steam
    # client of the logged-in user and the notification bus is that user's,
    # while this script runs as root.
    if ! watcher_user_dirs; then
        warn "cannot tell which desktop user to install the watchers for."
        warn "Run the installer with sudo from your normal account, or start"
        warn "them yourself - see \"Achievements, messages and friends\" in the README."
        WATCHER_STATUS="NOT installed - run the installer with sudo from your account"
        return 1
    fi

    say "Installing the desktop-session services for $WATCHER_USER"
    # Create the directories as the user: "install -d" would leave any missing
    # parent (~/.config on a fresh account) owned by root, which quietly breaks
    # everything else that writes there.
    if ! runuser -u "$WATCHER_USER" -- mkdir -p "$WATCHER_DIR/$WATCHER_WANTS"; then
        warn "cannot create $WATCHER_DIR/$WATCHER_WANTS"
        WATCHER_STATUS="NOT installed - could not write to $WATCHER_DIR"
        return 1
    fi
    for unit in "${WATCHER_UNITS[@]}"; do
        if ! install_one_user_unit "$unit"; then
            warn "could not install $unit"
            WATCHER_STATUS="NOT installed - could not write $unit"
            return 1
        fi
    done

    # Keep the user's systemd running when no session is open. Without this it
    # stops with the last session, taking every user unit with it - and
    # switching between Desktop Mode and Game Mode ends a session. Both
    # watchers are written to outlive that switch, and neither can while the
    # thing that runs them does not.
    #
    # Measured on a Steam Deck: in Game Mode the bridge was not running at
    # all, which is why it never even reported that KDE Connect had gone.
    if enable_linger "$WATCHER_USER"; then
        say "Keeping $WATCHER_USER's services running across Game Mode"
        LINGER_NOTE=""
    else
        warn "could not enable lingering for $WATCHER_USER. The watchers will"
        warn "stop when you switch to Game Mode; turn it on by hand with:"
        warn "  sudo loginctl enable-linger $WATCHER_USER"
        LINGER_NOTE=" - but NOT across Game Mode, see above"
    fi

    user_systemctl daemon-reload || true
    WATCHER_STATUS="enabled for $WATCHER_USER, starts at next login$LINGER_NOTE"
    return 0
}

# linger_is_on() is in scripts/user-unit.sh: the uninstaller asks the same
# question to say whether it left the switch on.
#
# Turn it on, and then look. Trusting the exit code is how the log can say it
# was done while the panel says it is off, with nothing anywhere to explain
# the difference - so what loginctl had to say for itself is passed on rather
# than thrown away, as it used to be.
enable_linger() {   # enable_linger USER
    local reply
    linger_is_on "$1" && return 0
    reply="$(loginctl enable-linger "$1" 2>&1)" || true
    linger_is_on "$1" && return 0
    [[ -n "$reply" ]] && warn "loginctl: $reply"
    return 1
}

# Starting them is separate from installing them, and deliberately later: the
# achievement watcher wants the service up, and the bridge wants its pipe. The
# files, though, have to be on disk before anything that can fail - see where
# this is called from.
start_user_units() {
    if [[ $SKIP_WATCHER -eq 1 || -z "${WATCHER_USER:-}" ]]; then
        return 0                        # nothing was installed to start
    fi

    # The achievement watcher decides the summary line: the phone bridge exits
    # straight away while NOTIFY_PHONE is off, which is the shipped default and
    # not something to report as a failure.
    user_systemctl restart "$PHONE_UNIT" || true
    if user_systemctl restart "$WATCHER_UNIT"; then
        say "Watchers running now"
        WATCHER_STATUS="running for $WATCHER_USER$LINGER_NOTE"
    fi
    return 0
}

# Here, before anything that can fail. Everything after this point - pacman,
# the kernel module, the firmware flash - can end the run under set -e, and
# these files depend on none of it. Installed at the end, as they were, they
# were simply absent on a machine where an earlier step went wrong, and
# nothing anywhere said so. Starting them is separate and stays late.
install_user_units || true

# --- build prerequisites ----------------------------------------------------
#
# On a machine that has never built anything, getting the module compiled is
# three problems deep and none of them is the module: the rootfs is read-only,
# pacman's keyring has never been initialised so every install fails on
# signatures rather than on the package, and the headers are named after the
# exact kernel rather than after "linux". Finding that name by hand is where a
# first install stalls, so work it out here.

# Both places a distribution keeps its module trees. Named once so the header
# lookup and the "is it there" check cannot start disagreeing, and so the
# tests can point them somewhere harmless.
MODULES_ROOTS=("/usr/lib/modules" "/lib/modules")

kernel_headers_package() {  # kernel_headers_package [release]
    local release="${1:-$(uname -r)}"
    local root name
    # Arch records the package a kernel came from beside its modules, and the
    # headers are that name with -headers on the end. SteamOS follows it:
    # linux-neptune-616 -> linux-neptune-616-headers.
    for root in "${MODULES_ROOTS[@]}"; do
        if [[ -r "$root/$release/pkgbase" ]]; then
            name="$(tr -d '[:space:]' < "$root/$release/pkgbase")"
            if [[ -n "$name" ]]; then
                printf '%s-headers' "$name"
                return 0
            fi
        fi
    done
    # No pkgbase to read - derive it from the release instead. Everything a
    # SteamOS kernel is called ends in neptune-NNN, which is the package.
    if [[ "$release" =~ (neptune(-[0-9]+)?) ]]; then
        printf 'linux-%s-headers' "${BASH_REMATCH[1]}"
        return 0
    fi
    return 1
}

kernel_build_dir() {  # kernel_build_dir [release] - where the headers landed
    local release="${1:-$(uname -r)}"
    local root
    for root in "${MODULES_ROOTS[@]}"; do
        if [[ -d "$root/$release/build" ]]; then
            printf '%s/%s/build' "$root" "$release"
            return 0
        fi
    done
    return 1
}

missing_build_tools() {  # one word per line, empty when nothing is missing
    command -v make >/dev/null 2>&1 || printf 'make\n'
    command -v gcc  >/dev/null 2>&1 || printf 'gcc\n'
    kernel_build_dir >/dev/null || printf 'headers\n'
}

rootfs_is_readonly() {
    command -v steamos-readonly >/dev/null 2>&1 \
        && steamos-readonly status 2>/dev/null | grep -qi enabled
}

pacman_keyring_ready() {
    [[ -d /etc/pacman.d/gnupg ]] || return 1
    pacman-key --list-keys 2>/dev/null | grep -q .
}

prepare_pacman() {
    if ! pacman_keyring_ready; then
        say "Initialising pacman's keyring - it has never been used here"
        pacman-key --init >/dev/null 2>&1 || { warn "pacman-key --init failed"; return 1; }
        pacman-key --populate >/dev/null 2>&1 \
            || { warn "pacman-key --populate failed"; return 1; }
    fi
    # -Sy and not -Syu, deliberately. A full upgrade would pull a newer kernel
    # while the old one is still running, and headers for a kernel you are not
    # running build a module that will not load.
    say "Refreshing package lists"
    pacman -Sy --noconfirm >/dev/null || { warn "pacman -Sy failed"; return 1; }
    # Best effort: on a fresh image the shipped keys can already be too old to
    # verify current packages. Neither name exists everywhere, so a miss here
    # is not a failure.
    local keyring
    for keyring in archlinux-keyring holo-keyring; do
        pacman -S --needed --noconfirm "$keyring" >/dev/null 2>&1 || true
    done
    return 0
}

install_build_prerequisites() {  # install_build_prerequisites <missing...>
    local missing=("$@")
    local packages=() headers=""

    command -v pacman >/dev/null 2>&1 || return 1

    if [[ " ${missing[*]} " == *" make "* || " ${missing[*]} " == *" gcc "* ]]; then
        packages+=("base-devel")
    fi
    if [[ " ${missing[*]} " == *" headers "* ]]; then
        headers="$(kernel_headers_package || true)"
        [[ -n "$headers" ]] || return 1      # nothing specific to ask for
        packages+=("$headers")
    fi
    (( ${#packages[@]} > 0 )) || return 1

    say "The kernel module has to be built, and this machine is missing:"
    printf '       %s\n' "${packages[@]}"
    if [[ $ASSUME_YES -eq 0 ]]; then
        local answer
        answer="$(ask 'Install them with pacman now?' 'y')"
        [[ "$answer" =~ ^[YyJj] ]] || { say "Leaving that to you."; return 1; }
    fi

    # pacman writes to the rootfs, which was unlocked at the top of this run
    # and stays that way until the end - see unlock_rootfs.
    if rootfs_is_readonly; then
        warn "the rootfs is read-only, so pacman cannot install anything"
        return 1
    fi

    prepare_pacman || return 1
    say "Installing ${packages[*]}"
    pacman -S --needed --noconfirm "${packages[@]}" || return 1
    return 0
}

# --- kernel shim -----------------------------------------------------------

module_build_hint() {
    local release headers packages
    release="$(uname -r)"
    headers="$(kernel_headers_package "$release" 2>/dev/null || true)"
    packages="base-devel${headers:+ $headers}"
    cat >&2 <<EOF

The module needs make, gcc and the kernel headers for $release.

  SteamOS / Arch:  sudo steamos-readonly disable
                   sudo pacman-key --init
                   sudo pacman-key --populate
                   sudo pacman -Sy $packages
  Debian/Ubuntu:   sudo apt install build-essential "linux-headers-$release"
  Fedora:          sudo dnf install "kernel-devel-$release" gcc make

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
        mapfile -t missing < <(missing_build_tools)
        if (( ${#missing[@]} > 0 )) \
           && install_build_prerequisites "${missing[@]}"; then
            mapfile -t missing < <(missing_build_tools)
        fi
        if (( ${#missing[@]} > 0 )); then
            warn "cannot build the kernel module, missing: ${missing[*]}"
            module_build_hint
            return 1
        fi
    fi

    say "Building and installing the leds-valve-shim kernel module"
    "$dir/install.sh" || return 1
    [[ -e "$SHIM_DEVICE" ]] || return 1
    # The kernel this was just built for is the one running. Every other copy
    # belongs to a kernel a SteamOS update has moved on from, and is a file
    # nothing will ever load again - see remove_stale_shims.
    remove_stale_shims "$release"
    return 0
}

MODULE_OK=1
install_shim_module || MODULE_OK=0

if [[ $MODULE_OK -eq 0 ]]; then
    warn "$SHIM_DEVICE is not available."
    warn "The service will still be installed and waits for the device to appear."
fi

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

# The standalone installer, not pip. SteamOS keeps the rootfs read-only, so a
# system-wide pip install cannot write at all, and "pip install --user" lands
# in a directory the next system update resets. This one puts the whole thing
# under ~/.platformio, which survives - and it is what PlatformIO themselves
# tell you to use.
PLATFORMIO_INSTALLER_URL="https://raw.githubusercontent.com/platformio/platformio-core-installer/master/get-platformio.py"

# PLATFORMIO_PATH_LINE and its comment are in scripts/user-unit.sh: the
# uninstaller deletes exactly these two lines again, and a line written in one
# spelling and looked for in another is a line nobody removes.

add_platformio_to_path() {
    local profile="$WATCHER_HOME/.bashrc"
    # The standalone installer leaves the PATH to you, so without this "pio"
    # is only found by things that know where to look - this installer does,
    # your shell does not.
    if grep -qF "$PLATFORMIO_PATH_MARK" "$profile" 2>/dev/null; then
        return 0                        # already there; do not stack copies
    fi
    say "Adding PlatformIO to the PATH in $profile"
    if ! runuser -u "$WATCHER_USER" -- bash -c '
            printf "\n%s\n%s\n" "$1" "$2" >> "$3"
        ' _ "$PLATFORMIO_PATH_NOTE" "$PLATFORMIO_PATH_LINE" "$profile"; then
        warn "could not write $profile - add this line yourself:"
        warn "    $PLATFORMIO_PATH_LINE"
    fi
}

install_platformio() {
    # As the user, not as root: the toolchains land in ~/.platformio, and a
    # root-owned copy of that breaks every later run they make themselves.
    say "Fetching $PLATFORMIO_INSTALLER_URL"
    runuser -u "$WATCHER_USER" -- env "HOME=$WATCHER_HOME" bash -c '
        set -euo pipefail
        script="$(mktemp -t get-platformio-XXXXXX.py)"
        trap "rm -f \"$script\"" EXIT
        curl -fsSL -o "$script" "$1"
        python3 "$script"
    ' _ "$PLATFORMIO_INSTALLER_URL" || {
        warn "the PlatformIO installer did not finish - see above"
        return 1
    }
    add_platformio_to_path
    say "PlatformIO installed. Open a new shell, or run: source ~/.bashrc"
    return 0
}

# Offered on every run, not only when flashing was asked for. It is what the
# firmware is built with, and discovering it is missing the first time you want
# to flash is one download too late.
ensure_platformio() {
    watcher_user_dirs || return 1       # no user to install for
    find_pio >/dev/null && return 0     # already there, nothing to ask

    say "PlatformIO is not installed for $WATCHER_USER. It builds and flashes"
    say "the ESP firmware, here or later with ./flash-esp.sh."
    if [[ $ASSUME_YES -eq 0 ]]; then
        local answer
        answer="$(ask 'Install it now (downloads from github.com)?' 'y')"
        [[ "$answer" =~ ^[YyJj] ]] || {
            say "Leaving that to you - see 'What you need' in the README."
            return 1
        }
    fi
    install_platformio
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
    systemctl stop steamos-utility-center.service >/dev/null 2>&1 || true

    local ini="$SOURCE_DIR/firmware/led-client/platformio.ini"
    if ! grep -q "^\[env:$FLASH_ENV\]" "$ini"; then
        warn "the menu offers '$FLASH_ENV' but $ini has no such environment."
        warn "The two have drifted apart - please report this."
        return 1
    fi

    # ensure_platformio ran further up and has already asked, so a miss here
    # means the answer was no, or the install did not finish.
    local pio_path=""
    pio_path="$(find_pio || true)"
    if [[ -z "$pio_path" ]]; then
        warn "PlatformIO (pio) not found for $WATCHER_USER. Install it with:"
        warn "    curl -fsSL -o get-platformio.py $PLATFORMIO_INSTALLER_URL"
        warn "    python3 get-platformio.py"
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

ensure_platformio || true

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

png_width() {  # png_width <file> - pixels, or nothing if it will not read
    # Bytes 16-19 of a PNG are the width, big endian. Worth reading: the icon
    # is a file anyone can replace, and it has to land in the directory for
    # its own size or the menu picks a scaled copy of the wrong one.
    od -An -tu4 -j16 -N4 --endian=big "$1" 2>/dev/null | tr -d ' '
}

install_panel_icon() {
    # Prints what the Icon= line should say. An icon theme is where a menu
    # looks first, so the picture goes in there under the entry's own name;
    # a name also survives the clone being moved, which an absolute path
    # would not. Falls back to the path, and then to a stock icon, because
    # an entry with no picture at all looks broken.
    local source_icon="$SOURCE_DIR/gui/steamos-utility-center-panel.png"
    if [[ ! -f "$source_icon" ]]; then
        printf 'preferences-desktop-display'
        return 0
    fi

    local width
    width="$(png_width "$source_icon")"
    [[ "$width" =~ ^[0-9]+$ ]] || width=512
    local icon_dir="$WATCHER_HOME/.local/share/icons/hicolor/${width}x${width}/apps"

    # PANEL_ICON is in scripts/user-unit.sh, so the name written here and the
    # one the uninstaller globs for cannot drift apart.
    if runuser -u "$WATCHER_USER" -- mkdir -p "$icon_dir" \
       && cp "$source_icon" "$icon_dir/$PANEL_ICON.png"; then
        chown "$WATCHER_USER:$WATCHER_USER" "$icon_dir/$PANEL_ICON.png"
        chmod 0644 "$icon_dir/$PANEL_ICON.png"
        printf '%s' "$PANEL_ICON"
    else
        printf '%s' "$source_icon"
    fi
}

install_control_panel() {
    local source="$SOURCE_DIR/gui/steamos-utility-center-panel.desktop"
    [[ -f "$source" ]] || { PANEL_STATUS="not in the repository"; return 1; }

    if ! watcher_user_dirs; then
        PANEL_STATUS="skipped - no desktop user to install it for"
        return 1
    fi

    # The menu entry points into the clone rather than into INSTALL_DIR: the
    # panel's repair button re-runs install.sh, which only exists here.
    # PANEL_ENTRY_DIR and PANEL_ENTRY are in scripts/user-unit.sh, so the
    # uninstaller takes back the same file this writes.
    local dir="$WATCHER_HOME/$PANEL_ENTRY_DIR"
    runuser -u "$WATCHER_USER" -- mkdir -p "$dir" || {
        PANEL_STATUS="could not write to $dir"; return 1; }
    sed -e "s|@SOURCE_DIR@|$SOURCE_DIR|g" \
        -e "s|@ICON@|$(install_panel_icon)|g" "$source" \
        > "$dir/$PANEL_ENTRY"
    chown "$WATCHER_USER:$WATCHER_USER" "$dir/$PANEL_ENTRY"
    chmod 0644 "$dir/$PANEL_ENTRY"
    refresh_desktop_caches "$dir"

    if runuser -u "$WATCHER_USER" -- python3 -c 'import tkinter' >/dev/null 2>&1
    then
        PANEL_STATUS="in the application menu as \"SteamOS Utility Center\""
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

say "Enabling steamos-utility-center.service"
systemctl daemon-reload
systemctl enable steamos-utility-center.service
# Deliberately not "enable --now": that starts a stopped service but leaves a
# running one alone, so installing over a running copy would keep serving the
# old code from the old unit - files updated, behaviour not. Restarting always
# lands on what was just installed.
systemctl restart steamos-utility-center.service

# systemctl restart returns once the process has been launched, so a service
# that dies on its own configuration is genuinely "active" for the moment in
# between - and a crash loop cycles through active, failed and activating, so
# one glance can land anywhere. Look again once it has had time to fail, and
# count the restarts.
sleep 4
restarts="$(systemctl show -p NRestarts --value steamos-utility-center.service \
            2>/dev/null || true)"
if systemctl is-active --quiet steamos-utility-center.service \
   && [[ "${restarts:-0}" == "0" ]]; then
    say "Service is running."
else
    if [[ "${restarts:-0}" != "0" ]]; then
        warn "Service started and then died ${restarts}x - it is not staying up."
    else
        warn "Service is not active."
    fi
    # Print the reason here rather than leaving it to be dug out of the
    # journal: a bad line in the configuration says so plainly, and that is
    # exactly the message worth seeing at the end of an install.
    warn "The last thing it said:"
    journalctl -u steamos-utility-center.service -n 5 --no-pager -o cat 2>/dev/null \
        | sed 's/^/    /' >&2 || true
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
    NOTIFY_FIFO="$(notify_setting NOTIFY_FIFO /run/steamos-utility-center/notify)"
    for _ in 1 2 3 4 5 6; do
        [[ -p "$NOTIFY_FIFO" ]] && break
        sleep 0.5
    done
    if [[ -p "$NOTIFY_FIFO" ]]; then
        say "Notification pipe ready ($NOTIFY_FIFO)"
    else
        warn "the service is running, but $NOTIFY_FIFO was not created -"
        warn "no flash of any kind will work."
        warn "The reason is in: journalctl -u steamos-utility-center -n 40"
    fi
fi

start_user_units || true

# Everything that needed a writable / has been written by now. Do it here
# rather than leaving it to the exit trap, so the last thing on screen is the
# summary and not a line about filesystems.
relock_rootfs

cat <<EOF

Done.

  Panel:    $PANEL_STATUS
  Firmware: $FIRMWARE_STATUS
  Command:  $COMMAND_STATUS
  Config:   $CONFIG_PATH
  Logs:     journalctl -u steamos-utility-center -f
  Restart:  sudo systemctl restart steamos-utility-center

Desktop-session services: $WATCHER_STATUS
  Achievements, messages and friends
  Check:    $COMMAND_STATUS --steam-check   (with a game running)
  Log:      journalctl --user -u steamos-utility-center-achievements -f

  Phone notifications over KDE Connect - off until you switch it on in the
  panel, under Notifications
  Try it:   $COMMAND_STATUS --watch-phone --print   (as yourself, not with sudo)
  Log:      journalctl --user -u steamos-utility-center-phone -f

Test the strip without Steam (stop the service first so the port is free):

  sudo systemctl stop steamos-utility-center
  sudo $COMMAND_STATUS --self-test
  sudo systemctl start steamos-utility-center

EOF
