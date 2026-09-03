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
# Each path that this removes. The names are here one time, and install.sh
# uses the same names.
#
# The two scripts had their own copies. An uninstaller that looked for a name
# that the installer no longer wrote thus left a service that nothing
# removes.
# shellcheck source=scripts/user-unit.sh
source "$SOURCE_DIR/scripts/user-unit.sh"

# A person who types "uninstall" wants each part removed.
#
# Two parts are worth a second thought: the settings and the kernel module.
# Each of them is still one option away. That option now keeps them, and before
# it removed them.
#
# A default that leaves one half of the installation is a default that each
# person who did not read this must undo.
KEEP_CONF=0
KEEP_MODULE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-conf) KEEP_CONF=1; shift ;;
        --keep-module) KEEP_MODULE=1; shift ;;
        # These two options now ask for the default behaviour. This script
        # accepts them and does not refuse them. They are in the README of each
        # clone from before this change, and people type them from memory.
        --purge|--remove-module) shift ;;
        -h|--help) sed -n '5,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

# The code below asks what it removes and not what it keeps. That is the
# clearer question at the place where the removal occurs.
PURGE=$(( 1 - KEEP_CONF ))
REMOVE_MODULE=$(( 1 - KEEP_MODULE ))

[[ $EUID -eq 0 ]] || { echo "run as root: sudo ./uninstall.sh" >&2; exit 1; }

# --- the read-only rootfs ---------------------------------------------------
#
# This is before the first removal and not before the kernel module.
#
# The suspend hook is under /usr/lib/systemd. `rm -f` on a locked root
# filesystem does not return with no message. It fails with "Read-only file
# system".
#
# Under set -e, that failure ended the uninstall after three steps. The udev
# rule was gone. The service files, the command link and the configuration were
# all still there, and nothing reported that.
#
# install.sh uses the same function, so the two cannot become different.
unlock_rootfs || true

# --- the units that run in the desktop session ------------------------------

remove_user_units() {
    watcher_user_dirs || return 0

    local unit removed=0
    # Each unit that this project installs, and not the current units only.
    # This list is what stops a file from an older installation from staying
    # in a ~/.config directory with nothing to remove it.
    for unit in "${WATCHER_UNITS[@]}"; do
        [[ -f "$WATCHER_DIR/$unit" ]] || continue
        user_systemctl stop "$unit" || true
        rm -f "$WATCHER_DIR/$unit" "$WATCHER_DIR/$WATCHER_WANTS/$unit"
        removed=1
    done

    # And the units that older releases installed and this one does not. See
    # RETIRED_USER_UNITS in scripts/user-unit.sh. Without this, they continue
    # to run at each login and nothing here removes them.
    if remove_retired_user_files; then
        removed=1
    fi
    [[ $removed -eq 1 ]] || return 0

    user_systemctl daemon-reload || true
    echo "Removed the desktop-session services for $WATCHER_USER."
}

remove_user_units

# --- what the installer put in that user's home -----------------------------
#
# This is not under a root path, so no rm -f line below reaches it. This
# project wrote each of these files, and the person who uninstalls did not.

remove_menu_entry() {
    watcher_user_dirs || return 0

    local applications="$WATCHER_HOME/$PANEL_ENTRY_DIR"
    local removed=0
    if [[ -f "$applications/$PANEL_ENTRY" ]]; then
        rm -f "$applications/$PANEL_ENTRY"
        removed=1
    fi

    # Each size, and not the size of the icon of this clone. The installer
    # stores the icon under the width that it reads from the PNG, so an older
    # installation can have left one in another directory.
    local icon
    for icon in "$WATCHER_HOME"/$PANEL_ICON_GLOB; do
        [[ -f "$icon" ]] || continue
        rm -f "$icon"
        removed=1
    done

    [[ $removed -eq 1 ]] || return 0
    echo "Removed the control panel's menu entry and icon for $WATCHER_USER."
    # Without this, the menu continues to offer the entry and to start a
    # program that is not there, until the cache is built again.
    refresh_desktop_caches "$applications"
}

# The two lines that the installer adds to the .bashrc of the user, so that
# "pio" is a command that the person can type.
#
# PlatformIO stays. It is another person's program, the person can use it for
# other work, and it is some hundred megabytes to download again.
#
# The two lines name this installer, so this script removes them. The summary
# gives the commands to add them again.
remove_platformio_path() {
    watcher_user_dirs || return 0

    local profile="$WATCHER_HOME/.bashrc"
    [[ -f "$profile" ]] || return 0
    grep -qF "$PLATFORMIO_PATH_MARK" "$profile" 2>/dev/null || return 0

    # This matches the exact line, and it runs as the user. A wider match
    # edits a line that the person wrote. A write as root gives the .bashrc
    # of that person to root.
    #
    # It writes through the same inode, so the owner and the mode do not
    # change.
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
# The page of this panel installs the toolkit from cec-toolkit/, so this
# script removes it.
#
# Its units, its helpers, its udev rule and its sudoers file each arrived
# through a button here, and this uninstaller did not remove them before.
#
# This uses the same script that the page uses. The uninstaller of the toolkit
# refuses to run as root, and it thus needs the desktop user.
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

# A stop of the service makes the strip dark before the process exits.
systemctl disable --now "$NAME.service" 2>/dev/null || true
rm -f "$UNIT_PATH"

# The CPU settings. This disables the unit, so nothing applies them at the next
# boot.
#
# The current settings stay. To put the governor back needs the value from
# before the installation, and nothing recorded that value. It is a setting and
# not a change to reverse.
systemctl disable "$NAME-power.service" 2>/dev/null || true
rm -f "$POWER_UNIT_PATH"

# The drives of the System page.
#
# Unmount each one before the unit files go, or the drive stays mounted until
# the machine restarts. systemd forgets a unit whose file is gone at the next
# daemon-reload, so `stop` after that finds no such unit.
#
# The record under /var stays. It is the answer to "which drives did I have",
# and a second install reads it back. --purge takes it, with the settings.
systemctl disable --now "$NAME-mounts.service" 2>/dev/null || true
rm -f "$MOUNTS_UNIT_PATH" "$MOUNTS_APPLIER_PATH"
shopt -s nullglob
for mount_unit in "$UNIT_DIR"/*.mount; do
    grep -q "written by the SteamOS Utility Center" "$mount_unit" || continue
    say "Unmounting $(basename "$mount_unit")"
    systemctl disable --now "$(basename "$mount_unit")" 2>/dev/null || true
    rm -f "$mount_unit"
done
shopt -u nullglob

# And the file that asked SteamOS to keep all of the above.
rm -f "$KEEP_LIST_PATH"

# And each file from before the rename.
#
# A person who uninstalls can have run the new installer never: they clone,
# pull, and run this script. The names to remove are thus the old names.
#
# This walks the same list that the migration walks. Without that, this is the
# one script with no knowledge of those names.
remove_old_install "$PURGE"
systemctl daemon-reload

# The linger setting. The installer switches it on, so that the services of the
# desktop user continue in Game Mode.
#
# This switches it off again. This project switched it on. A report of "left in
# place" gives each machine with this installation a switch that nobody asked
# for and nobody finds.
if [[ -n "${WATCHER_USER:-}" ]] && linger_is_on "$WATCHER_USER"; then
    loginctl disable-linger "$WATCHER_USER" >/dev/null 2>&1 || true
    echo "Turned lingering back off for $WATCHER_USER."
fi

rm -f "$UDEV_PATH"
rm -f "$SLEEP_HOOK_PATH"
udevadm control --reload >/dev/null 2>&1 || true

# Only when the link is still ours. A person who put their own
# steamos-utility-center there keeps it. A link to another program is not a
# link that this script installed.
if [[ -L "$COMMAND_LINK" \
      && "$(readlink "$COMMAND_LINK")" == "$INSTALL_DIR/$NAME" ]]; then
    rm -f "$COMMAND_LINK"
fi
if [[ -L "$POWER_COMMAND_LINK" \
      && "$(readlink "$POWER_COMMAND_LINK")" == "$INSTALL_DIR/$NAME-power" ]]; then
    rm -f "$POWER_COMMAND_LINK"
fi
if [[ -L "$CTL_COMMAND_LINK" \
      && "$(readlink "$CTL_COMMAND_LINK")" == "$INSTALL_DIR/${NAME}ctl" ]]; then
    rm -f "$CTL_COMMAND_LINK"
fi

# The rule that let the control command apply a change with no password. It
# names programs in $INSTALL_DIR, which the next line removes. A rule that
# stayed would name programs that are not there, which is not a fault and is
# not tidy either.
rm -f "$SUDO_RULE_PATH"

# The Game Mode plugin, where this installed one. Only the directory that this
# project writes, and not the plugins of other people beside it.
if watcher_user_dirs && [[ -d "$WATCHER_HOME/$DECKY_PLUGIN" ]]; then
    echo "Removing the Game Mode plugin."
    rm -rf "${WATCHER_HOME:?}/$DECKY_PLUGIN"
fi

rm -rf "${INSTALL_DIR:?}"

if [[ $PURGE -eq 1 ]]; then
    rm -f "$CONFIG_PATH" "$POWER_CONFIG_PATH"
    # The record of the drives goes with the settings, and not before. It is
    # under /var and the rm -rf above already took it, so this is the line
    # that says so rather than a second removal.
    # The settings of the panel. They are in the home directory of the desktop
    # user and not in /etc, and this script did not report that file before.
    [[ -n "${WATCHER_HOME:-}" ]] \
        && rm -f "$WATCHER_HOME/.config/$PANEL_CONFIG"
fi
echo "Removed."

# --- kernel module ---------------------------------------------------------
#
# This run unlocked the root filesystem at its start. It stays unlocked until
# the exit trap locks it again. See unlock_rootfs.

if [[ $REMOVE_MODULE -eq 1 ]]; then
    if lsmod | grep -q "^${MODULE_NAME//-/_}"; then
        rmmod "$MODULE_NAME" 2>/dev/null \
            || modprobe -r "$MODULE_NAME" 2>/dev/null || true
        echo "Module unloaded."
    fi
    # Each kernel with a copy, and not the running kernel only. A SteamOS
    # update keeps the modules of the previous kernel. Without this, the shim
    # for that kernel stays on the machine.
    remove_stale_shims
    rm -f "$MODULES_LOAD"
    depmod "$RELEASE" || true
    echo "Module removed, from every kernel that had one."
fi

# --- what is still here, and why -------------------------------------------
#
# This reports these items and does not leave them for a person to find.
#
# Each item above belongs to this project. Each item below belongs to another
# person, or to the person at the machine, or is a switch that other programs
# can use. This script thus reports each of them and removes none of them.

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
