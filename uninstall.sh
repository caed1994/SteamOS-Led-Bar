#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# Removes the SteamOS Utility Center, and by default everything it installed.
#   sudo ./uninstall.sh [--keep-conf] [--keep-module]
# --keep-conf    leaves the settings: /etc/steamos-utility-center.conf, the
#                power config, and the panel's own in the desktop user's home
# --keep-module  leaves the leds-valve-shim kernel module loaded and installed

set -euo pipefail

MODULE_NAME="leds-valve-shim"
RELEASE="$(uname -r)"
MODULE_PATH="/usr/lib/modules/$RELEASE/updates/${MODULE_NAME}.ko"

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Every path this removes, named once and shared with install.sh: the two used
# to hold their own copies, and an uninstaller looking for a name the
# installer had stopped writing leaves a service nothing can take away.
# shellcheck source=scripts/user-unit.sh
source "$SOURCE_DIR/scripts/user-unit.sh"

# Somebody typing "uninstall" wants it gone. The two things worth a second
# thought - the settings and the kernel module - are still one flag away, but
# the flag is now the one that keeps them rather than the one that removes
# them: a default that leaves half the machine behind is a default that has to
# be undone by everybody who did not read this.
KEEP_CONF=0
KEEP_MODULE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-conf) KEEP_CONF=1; shift ;;
        --keep-module) KEEP_MODULE=1; shift ;;
        # What these used to ask for is what happens anyway now. Taken rather
        # than refused, because they are in the README of every clone made
        # before this changed, and in the fingers of anyone who has run it.
        --purge|--remove-module) shift ;;
        -h|--help) sed -n '5,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

# The body below asks what is being removed rather than what is being kept,
# which is the way round it reads best where the removing happens.
PURGE=$(( 1 - KEEP_CONF ))
REMOVE_MODULE=$(( 1 - KEEP_MODULE ))

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
            profile="$1"; scratch="$profile.steamos-utility-center.$$"
            grep -vxF "$2" "$profile" | grep -vxF "$3" | grep -vxF "$4" \
                > "$scratch" || true
            cat "$scratch" > "$profile" && rm -f "$scratch"
        ' _ "$profile" "$PLATFORMIO_PATH_NOTE" "$PLATFORMIO_PATH_LINE" \
          "$OLD_PLATFORMIO_PATH_NOTE"; then
        echo "Took the PlatformIO PATH line back out of $profile."
        PLATFORMIO_NOTE=1
    else
        echo "Could not edit $profile - remove this line by hand:" >&2
        echo "    $PLATFORMIO_PATH_LINE" >&2
    fi
}

# --- the HDMI CEC toolkit ---------------------------------------------------
#
# Installed from vendor/ by this panel's own page, so it is this script's to
# take away again: its units, helpers, udev rule and sudoers file are all
# things somebody got by pressing a button here, and none of them were touched
# by this uninstaller before.
#
# Driven through the same script the page uses, because the toolkit's own
# uninstaller refuses to run as root and has to be handed the desktop user.
remove_cec_toolkit() {
    watcher_user_dirs || return 0
    local control="$WATCHER_HOME/.local/bin/steamos-cec-toolkitctl"
    [[ -x "$control" ]] || return 0
    echo "Removing the HDMI CEC toolkit."
    if ! bash "$SOURCE_DIR/scripts/install-cec.sh" remove \
            "$SOURCE_DIR/vendor/steamos-cec-toolkit" "$WATCHER_USER"; then
        echo "  the toolkit's own uninstaller did not finish - what is left" >&2
        echo "  can be removed with: $control uninstall" >&2
    fi
}

PLATFORMIO_NOTE=0
remove_menu_entry
remove_platformio_path
remove_cec_toolkit

# Stopping the service blanks the strip before the process exits.
systemctl disable --now "$NAME.service" 2>/dev/null || true
rm -f "$UNIT_PATH"

# The CPU settings. Disabling stops it being reapplied at the next boot; what
# is set right now is left as it is, because putting the governor back would
# mean knowing what it was before this was ever installed, and nothing here
# recorded that. It is a setting, not a change to be undone.
systemctl disable "$NAME-power.service" 2>/dev/null || true
rm -f "$POWER_UNIT_PATH"

# And anything left from before the rename. Somebody uninstalling may never
# have run the new installer at all - they clone, pull, and run this - so the
# names it is asked to remove are the old ones. Walking the same list the
# migration walks is what keeps this from being the one script that does not
# know about them.
remove_old_install "$PURGE"
systemctl daemon-reload

# Lingering, which the installer turns on so the desktop user's services
# survive Game Mode. Turned off again here: it was this project that switched
# it on, and reporting it as "left in place" left every machine that ever had
# this installed with a switch nobody asked for and nobody would find.
if [[ -n "${WATCHER_USER:-}" ]] && linger_is_on "$WATCHER_USER"; then
    loginctl disable-linger "$WATCHER_USER" >/dev/null 2>&1 || true
    echo "Turned lingering back off for $WATCHER_USER."
fi

rm -f "$UDEV_PATH"
rm -f "$SLEEP_HOOK_PATH"
udevadm control --reload >/dev/null 2>&1 || true

# Only if it is still ours. Somebody who put their own steamos-utility-center
# there is entitled to keep it, and a link pointing somewhere else is not
# something this script installed.
if [[ -L "$COMMAND_LINK" \
      && "$(readlink "$COMMAND_LINK")" == "$INSTALL_DIR/$NAME" ]]; then
    rm -f "$COMMAND_LINK"
fi
if [[ -L "$POWER_COMMAND_LINK" \
      && "$(readlink "$POWER_COMMAND_LINK")" == "$INSTALL_DIR/$NAME-power" ]]; then
    rm -f "$POWER_COMMAND_LINK"
fi

rm -rf "${INSTALL_DIR:?}"

if [[ $PURGE -eq 1 ]]; then
    rm -f "$CONFIG_PATH" "$POWER_CONFIG_PATH"
    # The panel's own settings, which live in the desktop user's home rather
    # than in /etc and were the one file this script never mentioned.
    [[ -n "${WATCHER_HOME:-}" ]] \
        && rm -f "$WATCHER_HOME/.config/$PANEL_CONFIG"
fi
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
    # Every kernel that has a copy, not only the one running: a SteamOS
    # update leaves the previous kernel's modules in place, so the shim built
    # for it would otherwise stay on the machine for ever.
    remove_stale_shims
    rm -f "$MODULES_LOAD"
    depmod "$RELEASE" || true
    echo "Module removed, from every kernel that had one."
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
    if [[ -n "${WATCHER_HOME:-}" ]]; then
        echo "  $WATCHER_HOME/.config/$PANEL_CONFIG"
    fi
    echo "      your settings, kept because of --keep-conf"
fi
if [[ $REMOVE_MODULE -eq 0 ]]; then
    echo "  the $MODULE_NAME kernel module"
    echo "      kept because of --keep-module"
else
    echo "  /usr/lib/depmod.d/10-updates.conf"
    echo "      it only sets the general search order for updates/, and other"
    echo "      modules may rely on it"
fi
echo "  $SOURCE_DIR"
echo "      the clone, which is yours - delete it if you like"
if [[ -n "${WATCHER_HOME:-}" && -d "$WATCHER_HOME/.platformio" ]]; then
    echo "  $WATCHER_HOME/.platformio"
    echo "      PlatformIO, which builds the ESP firmware. It is not ours,"
    echo "      and other projects may be using it."
    if [[ $PLATFORMIO_NOTE -eq 1 ]]; then
        echo "      To go on typing \"pio\", put this back in ~/.bashrc:"
        echo "        $PLATFORMIO_PATH_LINE"
    fi
fi
