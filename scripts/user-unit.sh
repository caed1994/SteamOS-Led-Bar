# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# What install.sh and uninstall.sh have to agree on.
#
# This is mostly the units that run in the desktop session. Both are *user*
# systemd units. One speaks to the Steam client of the logged-in user, and the
# other reads the session bus of that user. install.sh and uninstall.sh both
# run as root.
#
# Two answers to "which user, which directory, which session" are how the two
# scripts become different, and that failure is quiet: it leaves an enable
# symlink in a ~/.config directory with no unit behind it.
#
# This file thus finds the answer one time.
#
# Sourced, not executed.

# How both scripts write a message. They are here because the shared code below
# also writes messages. A helper that printed in two forms, one for each
# script, is a helper whose output a person cannot read.
#
# `die` stays with the installer. To stop is a decision of the installer and
# not a shared decision.
say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m warning:\033[0m %s\n' "$*" >&2; }

# --- where everything lands -------------------------------------------------
#
# These names are here one time and not in each script.
#
# install.sh and uninstall.sh each had a copy of these seven paths, with equal
# text. Two constants that a person must keep equal become different.
#
# The installer then writes one name and the uninstaller looks for another
# name, and that leaves a service that nothing removes.
NAME="steamos-utility-center"

# Each path below starts with this. On a machine it is empty, and the paths are
# the absolute paths that they read as.
#
# It is here so that a test can run the migration against a directory that the
# test built. That is the only way to check the removal of an old installation
# with no old installation to remove.
#
# Only the tests set it.
ROOT="${ROOT:-}"

INSTALL_DIR="$ROOT/var/lib/$NAME"
# The commit of the files in that directory. The installer writes it and the
# panel reads it.
#
# It is the answer to a question that cost two evenings. "pulled" and
# "installed" are two steps, and the screen did not separate them. A clone
# three commits ahead of the running copy thus looked the same as a current
# machine.
#
# It is inside INSTALL_DIR, so the one rm -rf of the uninstaller removes it.
STAMP_PATH="$INSTALL_DIR/installed-from"
CONFIG_PATH="$ROOT/etc/$NAME.conf"
POWER_CONFIG_PATH="$ROOT/etc/$NAME-power.conf"
UNIT_DIR="$ROOT/etc/systemd/system"
UNIT_PATH="$UNIT_DIR/$NAME.service"
POWER_UNIT_PATH="$UNIT_DIR/$NAME-power.service"
UDEV_PATH="$ROOT/etc/udev/rules.d/99-$NAME.rules"
# The drives of the System page.
#
# The record is under /var, which is its own partition on SteamOS and survives
# an update whatever the new image does with /etc. The unit writes the mount
# units again from it at every boot. The keep-list asks SteamOS to carry the
# files of this project into the new image, which is the official way and the
# one that needs no unit at all. See server/steamos_utility_center/mounts.py.
MOUNTS_RECORD_PATH="$INSTALL_DIR/mounts.conf"
MOUNTS_UNIT_PATH="$UNIT_DIR/$NAME-mounts.service"
MOUNTS_APPLIER_PATH="$INSTALL_DIR/$NAME-mounts-apply"
KEEP_LIST_PATH="$ROOT/etc/atomic-update.conf.d/$NAME.conf"
SLEEP_HOOK_PATH="$ROOT/usr/lib/systemd/system-sleep/$NAME"
# This name did not change with the other names, because this project does not
# write the file. The installer of leds-valve-shim writes it under this name,
# and this project keeps that script unmodified. See
# leds-valve-shim/PROVENANCE.md.
#
# A change to the name here leaves the uninstaller with a file that nothing
# writes, and it leaves the real file on the machine. That file loads the
# module at each boot.
MODULES_LOAD="$ROOT/etc/modules-load.d/steamos-led-bar.conf"

# --- the kernel shim, on every kernel that has one --------------------------
#
# The installer puts the module into the updates/ directory of the running
# kernel.
#
# A SteamOS update brings a new kernel and keeps the modules of the old kernel.
# The copy for the previous kernel thus stays in its own directory.
#
# That copy does nothing, because its kernel does not run. It also stayed after
# an uninstall that read `uname -r` only.
#
# Both roots are walked because /lib/modules is a symlink to /usr/lib/modules
# on Arch and a directory of its own elsewhere; the same file found twice
# under two names is dropped by comparing what it resolves to.
SHIM_NAME="leds-valve-shim"

shim_copies() {
    local root path resolved seen=""
    for root in "$ROOT/usr/lib/modules" "$ROOT/lib/modules"; do
        [[ -d "$root" ]] || continue
        for path in "$root"/*/updates/"$SHIM_NAME".ko; do
            [[ -e "$path" ]] || continue
            resolved="$(readlink -f "$path")"
            [[ "$seen" == *"|$resolved|"* ]] && continue
            seen="$seen|$resolved|"
            printf '%s\n' "$path"
        done
    done
}

shim_release() {    # shim_release PATH - the kernel one copy was built for
    local where="${1%/updates/*}"
    printf '%s\n' "${where##*/}"
}

remove_stale_shims() {  # remove_stale_shims [release to keep]
    local keep="${1:-}" path release
    while read -r path; do
        [[ -n "$path" ]] || continue
        release="$(shim_release "$path")"
        [[ -n "$keep" && "$release" == "$keep" ]] && continue
        rm -f "$path"
        depmod "$release" >/dev/null 2>&1 || true
        say "  removed the shim built for $release"
    done < <(shim_copies)
}

# The name that a person can type. Each file that this project installs is in
# /var/lib, so that a SteamOS update does not remove it. Nothing in /var/lib is
# on a PATH. Without this link, a person can read each command in the README
# and can run none of them.
COMMAND_LINK="$ROOT/usr/local/bin/$NAME"
# The second program that this installs, with a link for the same reason. The
# README gives the command, and a command that a person cannot type is worse
# than no command.
POWER_COMMAND_LINK="$ROOT/usr/local/bin/$NAME-power"
# The control surface that speaks JSON, for a caller that is not this panel: a
# Decky plugin in Game Mode, or a script. See server/steamos_utility_center/ctl.py.
CTL_COMMAND_LINK="$ROOT/usr/local/bin/${NAME}ctl"
# The rule that lets that command apply a change with no password. The command
# writes it, and the uninstaller removes it. See ctl.sudoers_text, which is
# where the text of it is.
SUDO_RULE_PATH="$ROOT/etc/sudoers.d/zz-$NAME"
# Where Decky Loader keeps a plugin, under the home of the desktop user. It is
# in /home, so a SteamOS update does not take it away.
DECKY_PLUGIN="homebrew/plugins/SteamOS Utility Center"

WATCHER_UNIT="$NAME-achievements.service"
PHONE_UNIT="$NAME-phone.service"
# Each unit that this installs into the systemd of the user. Both scripts walk
# this list. A unit in one script and not in the other is a file that nothing
# removes.
WATCHER_UNITS=("$WATCHER_UNIT" "$PHONE_UNIT")
# This must be equal to WantedBy= in those units. It is the directory of the
# enable symlink.
WATCHER_WANTS="default.target.wants"

# The user who started this, because each script that reads this file runs as
# root.
#
# There are two variables, because there are two routes. sudo from a terminal
# sets SUDO_USER. pkexec sets PKEXEC_UID and no SUDO_USER, and the control
# panel starts the installer through pkexec.
#
# A read of SUDO_USER alone is why an installation from the panel found no
# user for the user units and skipped each of them: the watchers, the menu
# entry, and the linger setting that keeps them alive in Game Mode.
#
# It reported that in one line, which moves past in the log. The panel then
# reported the linger setting as a fault. That report was correct, and it was
# about a step that the panel itself skipped.
#
# scripts/flash-firmware.sh reads both variables, and it always did.
invoking_user() {
    local uid="${PKEXEC_UID:-${SUDO_UID:-}}"
    if [[ -n "$uid" ]]; then
        id -nu "$uid" 2>/dev/null
        return
    fi
    [[ -n "${SUDO_USER:-}" ]] || return 1
    printf '%s\n' "$SUDO_USER"
}

# Sets WATCHER_USER, WATCHER_HOME, WATCHER_DIR and WATCHER_RUNTIME for the
# desktop user who asked for this. Returns non-zero, quietly, when there is no
# such user. The installer also uses this to run PlatformIO as that user.
watcher_user_dirs() {
    WATCHER_USER="$(invoking_user || true)"
    [[ -n "$WATCHER_USER" && "$WATCHER_USER" != "root" ]] || return 1

    local home
    home="$(getent passwd "$WATCHER_USER" | cut -d: -f6)"
    [[ -n "$home" && -d "$home" ]] || return 1

    WATCHER_HOME="$home"
    WATCHER_DIR="$home/.config/systemd/user"
    WATCHER_RUNTIME="/run/user/$(id -u "$WATCHER_USER")"
    return 0
}

# Runs `systemctl --user ...` in the session of that user. It returns non-zero
# when there is no session. That is not an error: the unit is enabled on the
# disk in both cases, and it starts at the next login.
user_systemctl() {
    [[ -d "$WATCHER_RUNTIME" ]] || return 1
    runuser -u "$WATCHER_USER" -- env "XDG_RUNTIME_DIR=$WATCHER_RUNTIME" \
        systemctl --user "$@" >/dev/null 2>&1
}

# Whether the systemd of that user continues to run with no open session.
#
# The control panel asks the same question, and it is the one question with an
# answer. The installer asks it before it sets the switch. The uninstaller asks
# it to report that it left the switch on.
linger_is_on() {    # linger_is_on USER
    local state
    state="$(loginctl show-user "$1" --property=Linger 2>/dev/null || true)"
    [[ "$state" == *"Linger=yes"* ]]
}

# --- the desktop user's own home --------------------------------------------
#
# Three files go there, and the paths of root do not cover them. The script
# that writes them and the script that removes them must use equal names for
# each of the three. See the note at the top of this file.

# The menu entry of the control panel, and the icon that it names. The
# installer stores the icon under the width that it reads from the PNG. The
# uninstaller thus matches each size and does not guess one. An older
# installation can have left an icon in another directory.
PANEL_ENTRY_DIR=".local/share/applications"
PANEL_ENTRY="$NAME.desktop"
# The name of the installed icon. The Icon= line of the entry gives the same
# name. It is a theme icon name and not a path, so it stays correct after a
# move of the clone.
#
# The name is here and both scripts use it. The installer wrote one name and
# the pattern below looked for another name. That leaves an icon that nothing
# removes.
PANEL_ICON="$NAME"
PANEL_ICON_GLOB=".local/share/icons/hicolor/*/apps/$PANEL_ICON.png"
# The preferences of the panel. The panel writes them as the user, so
# gui/appsettings.py decides this name and this file does not.
#
# The name is here so that the migration can move the file. A test compares it
# with the name in the panel.
PANEL_CONFIG="$NAME-panel.conf"

# What the installer adds to the .bashrc of the user, so that "pio" is a
# command that the person can type.
#
# Both lines are here as written, because the uninstaller removes them by an
# exact match. A wider match edits a line that the person wrote.
PLATFORMIO_PATH_NOTE="# PlatformIO, added by the SteamOS Utility Center installer."
# The text from before the rename. The uninstaller removes these lines by an
# exact match.
#
# A change to the text with no copy of the old text leaves a comment in each
# .bashrc from an older installation. That comment names an installer whose
# name no longer exists, and nothing removes it.
OLD_PLATFORMIO_PATH_NOTE="# PlatformIO, added by the SteamOS LED bar installer."
PLATFORMIO_PATH_LINE='export PATH="$HOME/.platformio/penv/bin:$PATH"'
# What the check looks for. A second installation thus adds no second copy, and
# the uninstaller can report whether there is a copy.
PLATFORMIO_PATH_MARK=".platformio/penv/bin"

# --- the names this project installed under before it was renamed -----------
#
# The name was "SteamOS LED bar", and each file on a machine had a name that
# started with steamos-led.
#
# The project grew past the LED bar. It now has the CPU and GPU power, HDMI
# CEC, and the keyboard layout. Its new name is the SteamOS Utility Center, and
# each file has a new name.
#
# The rename is the simple half. The difficult half is a machine with the old
# installation. An installation under new names removes nothing under the old
# names.
#
# Without this list, an update leaves the old steamos-led-serial.service
# enabled and running beside the new unit. Two processes then hold one serial
# port.
#
# The settings in /etc/steamos-led-serial.conf also stop, because nothing reads
# that file. Each setting thus returns to its default with no message.
#
# migrate_old_install below is the answer, and it runs before the installer
# writes anything.
#
# This is data and not a list of rm lines, so that the test walks the same list
# as the installer. Each name here is a *former* name: nothing writes these
# names now. The scripts search for them and remove them.
OLD_NAME="steamos-led"
OLD_INSTALL_DIR="$ROOT/var/lib/steamos-led-serial"

# The old unit and the unit that replaces it. These scripts stop the old unit
# and disable it before they delete it. A unit file that a script only removes
# leaves its enable symlink, and systemd continues to start a unit that is not
# there.
OLD_SYSTEM_UNITS=("steamos-led-serial.service:$UNIT_PATH"
                  "steamos-led-power.service:$POWER_UNIT_PATH")
OLD_USER_UNITS=("steamos-led-achievements.service:$WATCHER_UNIT"
                "steamos-led-phone.service:$PHONE_UNIT")

# What this project installed into the desktop session before and does not
# install now.
#
# These are not renames: they became nothing. They thus cannot go into the list
# above. A file with no replacement still needs a removal, or systemd runs it
# at each login.
#
#   $NAME-cec.service      put the CEC adapter on the bus before the CEC
#                          toolkit's wake service. The CEC module does that
#                          itself now - cec-toolkit/bin/steamos-cec-register.
#   the drop-in            set Type=simple on that toolkit's boot wake, so it
#                          stopped holding the session up. Its own unit says
#                          Type=simple now.
RETIRED_USER_UNITS=("$NAME-cec.service")
RETIRED_USER_DROPINS=("steamos-cec-boot-wake.service.d/10-$NAME.conf")

# The old configuration and its new position. These scripts move it and do not
# delete it. It holds the answers that a person gave the installer, and each
# setting that the person changed after that. Nothing can find those values
# again.
OLD_CONFIGS=("$ROOT/etc/steamos-led-serial.conf:$CONFIG_PATH"
             "$ROOT/etc/steamos-led-power.conf:$POWER_CONFIG_PATH")

# And the other files, which hold no state. These scripts remove them, and the
# new installation writes its own copies.
OLD_FILES=("$ROOT/etc/udev/rules.d/99-steamos-led-serial.rules"
           "$ROOT/usr/lib/systemd/system-sleep/steamos-led-serial"
           "$ROOT/usr/local/bin/steamos-led-serial"
           "$ROOT/usr/local/bin/steamos-led-power")
# /etc/modules-load.d/steamos-led-bar.conf is deliberately *not* in that list.
#
# It is not an old name of this project. The installer of the shim writes it
# under that name now. A removal here stops the load of the kernel module at
# the next boot, and the strip is then dark after an update.

# The one line inside a carried-over config that would otherwise be wrong.
#
# NOTIFY_FIFO is a setting and not a comment. It names a directory that the
# *unit* makes, through RuntimeDirectory=.
#
# A new name for the unit gives a new name to that directory. A configuration
# that this moves with no change thus names /run/steamos-led-serial, and
# nothing makes that directory now.
#
# The flashes then stop, and nothing in the log gives the reason.
#
# Only when it still holds the old default. Somebody who pointed it somewhere
# of their own meant it, and rewriting that would be this script overruling a
# setting rather than repairing one.
OLD_FIFO="/run/steamos-led-serial/notify"
NEW_FIFO="/run/$NAME/notify"

# Deliberately not renamed, and worth saying so where the list is:
#
#   /dev/valve-leds-shim and the leds-valve-shim module. Both are an
#   unmodified copy from another project. They name the LED interface of
#   Valve and not a part of this project. The name is also in the .ko file.
#
#   /dev/steamos-led-esp, the udev symlink for the ESP. A configuration with
#   SERIAL_PORT=/dev/steamos-led-esp names a piece of hardware. A new name
#   for the symlink stops the strip. The name gives the board of the LED
#   bar, and that is what the board is.

# Whether anything from before the rename is on this machine.
old_install_present() {
    local entry
    for entry in "${OLD_CONFIGS[@]}" "${OLD_SYSTEM_UNITS[@]}"; do
        [[ -e "${entry%%:*}" ]] && return 0
    done
    for entry in "${OLD_FILES[@]}"; do
        [[ -e "$entry" ]] && return 0
    done
    [[ -d "$OLD_INSTALL_DIR" ]] && return 0
    return 1
}

# Removes the old installation and moves its settings. It runs as root, before
# a write of a new file. It is also safe on a machine that never had the old
# names, and old_install_present is the first question for that reason.
#
# Safe to run twice, which matters: an install that fails halfway through is
# one somebody runs again, and the second run finds a machine that is already
# half migrated.
migrate_old_install() {
    old_install_present || return 0

    say "Found an install from before the rename - bringing it across"

    local entry old new
    for entry in "${OLD_SYSTEM_UNITS[@]}"; do
        old="${entry%%:*}"
        [[ -e "$UNIT_DIR/$old" ]] || continue
        # Stopped before it is disabled, and both before the file goes: a
        # running service holds the serial port, and the new one cannot open
        # it while it does.
        systemctl stop "$old" >/dev/null 2>&1 || true
        systemctl disable "$old" >/dev/null 2>&1 || true
        rm -f "$UNIT_DIR/$old"
        say "  removed the old $old"
    done

    for entry in "${OLD_CONFIGS[@]}"; do
        old="${entry%%:*}"; new="${entry#*:}"
        [[ -f "$old" ]] || continue
        if [[ -f "$new" ]]; then
            # Both files are present. A person migrated this machine and
            # then put the old file back, or this script ran two times.
            #
            # The new file is the file in use. This script leaves the old
            # file, because it is still the settings of a person.
            warn "$new exists too - leaving $old alone, it is no longer read"
            continue
        fi
        mv "$old" "$new"
        say "  moved your settings from $old to $new"
    done

    # The pipe of the notifications. See OLD_FIFO.
    #
    # This checks two times, and that is deliberate. The grep decides whether
    # to change the file. The sed matches the full line, so that it writes
    # that exact setting.
    #
    # Each check alone leaves a pipe that a person moved. Together they stop a
    # later edit of one of them from a write over the path of a person.
    if [[ -f "$CONFIG_PATH" ]] \
       && grep -qxF "NOTIFY_FIFO=$OLD_FIFO" "$CONFIG_PATH"; then
        sed -i "s|^NOTIFY_FIFO=$OLD_FIFO\$|NOTIFY_FIFO=$NEW_FIFO|" \
            "$CONFIG_PATH"
        say "  pointed NOTIFY_FIFO at $NEW_FIFO"
    fi

    for entry in "${OLD_FILES[@]}"; do
        [[ -e "$entry" ]] || continue
        rm -f "$entry"
        say "  removed $entry"
    done

    if [[ -d "$OLD_INSTALL_DIR" && "$OLD_INSTALL_DIR" != "$INSTALL_DIR" ]]; then
        rm -rf "${OLD_INSTALL_DIR:?}"
        say "  removed $OLD_INSTALL_DIR"
    fi

    migrate_old_user_files
    systemctl daemon-reload >/dev/null 2>&1 || true
    udevadm control --reload >/dev/null 2>&1 || true
}

# The half of the same list for the uninstaller: remove the old installation
# and keep nothing.
#
# This is a separate function and not an option on migrate_old_install. The two
# disagree about the important part, which is the settings.
#
# A migration moves the settings. An uninstall removes this project, and a
# configuration file with the name of a project that is not installed is
# rubbish.
#
# uninstall.sh calls this. A person can run uninstall.sh with no run of the new
# installer: they pull and they uninstall, and each name on the machine is an
# old name.
#
# --purge decides the configurations here as it does for the current names.
remove_old_install() {  # remove_old_install [purge]
    old_install_present || return 0
    echo "Removing what was left under the old steamos-led-* names."

    local entry old
    for entry in "${OLD_SYSTEM_UNITS[@]}"; do
        old="${entry%%:*}"
        systemctl disable --now "$old" >/dev/null 2>&1 || true
        rm -f "$UNIT_DIR/$old"
    done
    for entry in "${OLD_FILES[@]}"; do
        rm -f "$entry"
    done
    [[ -d "$OLD_INSTALL_DIR" && "$OLD_INSTALL_DIR" != "$INSTALL_DIR" ]] \
        && rm -rf "${OLD_INSTALL_DIR:?}"
    if [[ "${1:-0}" -eq 1 ]]; then
        for entry in "${OLD_CONFIGS[@]}"; do
            rm -f "${entry%%:*}"
        done
    fi
    remove_old_user_files
    return 0
}

# Units and drop-ins this project no longer installs, taken out of a session
# that still has them. Shared by the installer and the uninstaller: an upgrade
# has to remove them too, or the machine goes on running last release's units
# beside this one's.
#
# The directory of a drop-in goes only when it is empty. It belongs to another
# person's unit, and that person can have overrides of their own in it.
remove_retired_user_files() {
    # This returns 0 only when it removed a file. Both callers report the
    # result, and "there is no desktop user" is not a result to report.
    watcher_user_dirs || return 1

    local unit dropin gone=1
    for unit in "${RETIRED_USER_UNITS[@]}"; do
        [[ -f "$WATCHER_DIR/$unit" ]] || continue
        user_systemctl stop "$unit" || true
        rm -f "$WATCHER_DIR/$unit" "$WATCHER_DIR/$WATCHER_WANTS/$unit"
        gone=0
    done
    for dropin in "${RETIRED_USER_DROPINS[@]}"; do
        [[ -f "$WATCHER_DIR/$dropin" ]] || continue
        rm -f "$WATCHER_DIR/$dropin"
        rmdir "$WATCHER_DIR/$(dirname "$dropin")" 2>/dev/null || true
        gone=0
    done
    return $gone
}

# The user half of that, which uninstall.sh's own remove_user_units and
# remove_menu_entry only cover for the current names.
remove_old_user_files() {
    watcher_user_dirs || return 0

    local entry old icon
    for entry in "${OLD_USER_UNITS[@]}"; do
        old="${entry%%:*}"
        [[ -f "$WATCHER_DIR/$old" ]] || continue
        user_systemctl stop "$old" || true
        rm -f "$WATCHER_DIR/$old" "$WATCHER_DIR/$WATCHER_WANTS/$old"
    done
    rm -f "$WATCHER_HOME/$PANEL_ENTRY_DIR/steamos-led-panel.desktop"
    for icon in "$WATCHER_HOME"/.local/share/icons/hicolor/*/apps/steamos-led-panel.png; do
        [[ -f "$icon" ]] && rm -f "$icon"
    done
    return 0
}

# The half in the home directory of the desktop user. It is separate, because
# it needs the name of that user, and because a machine with no desktop user
# still has the root half to remove.
migrate_old_user_files() {
    watcher_user_dirs || return 0

    local entry old
    for entry in "${OLD_USER_UNITS[@]}"; do
        old="${entry%%:*}"
        [[ -f "$WATCHER_DIR/$old" ]] || continue
        user_systemctl stop "$old" || true
        rm -f "$WATCHER_DIR/$old" "$WATCHER_DIR/$WATCHER_WANTS/$old"
        say "  removed the old $old from $WATCHER_USER's session"
    done
    user_systemctl daemon-reload || true

    # The menu entry and its icon. Removed rather than moved: the installer
    # writes a fresh entry a few steps later, and a stale one left behind is
    # a second Utility Centre in the launcher that starts nothing.
    local entry_path="$WATCHER_HOME/$PANEL_ENTRY_DIR/steamos-led-panel.desktop"
    if [[ -f "$entry_path" ]]; then
        rm -f "$entry_path"
        say "  removed the old menu entry"
        refresh_desktop_caches "$WATCHER_HOME/$PANEL_ENTRY_DIR"
    fi
    local icon
    for icon in "$WATCHER_HOME"/.local/share/icons/hicolor/*/apps/steamos-led-panel.png; do
        [[ -f "$icon" ]] || continue
        rm -f "$icon"
    done

    # And the preferences of the panel, which the user keeps. The panel also
    # reads the old name. See gui/appsettings.py. This move is thus additional
    # protection and not the one route.
    #
    # The name is $NAME-panel.conf, which is what gui/appsettings.py reads. It
    # is not $NAME.conf. A wrong name here moves the file to a directory that
    # the panel does not read, and the theme and the window size then return
    # to their defaults. A test compares the two names.
    local old_prefs="$WATCHER_HOME/.config/steamos-led-panel.conf"
    local new_prefs="$WATCHER_HOME/.config/$PANEL_CONFIG"
    if [[ -f "$old_prefs" && ! -f "$new_prefs" ]]; then
        mv "$old_prefs" "$new_prefs"
        chown "$WATCHER_USER:$WATCHER_USER" "$new_prefs"
        say "  moved the panel's settings to $new_prefs"
    fi
}

refresh_desktop_caches() {  # refresh_desktop_caches <applications dir>
    # Plasma reads the menu from a cache that it builds. Until Plasma builds
    # that cache again, an entry that this changed keeps its old icon, and an
    # entry that this removed stays in the menu and starts a program that is
    # not there.
    #
    # This is an attempt and not a requirement. None of it is worth a failure,
    # and a logout also repairs it.
    runuser -u "$WATCHER_USER" -- update-desktop-database "$1" >/dev/null 2>&1 \
        || true
    local cache
    for cache in kbuildsycoca6 kbuildsycoca5; do
        command -v "$cache" >/dev/null 2>&1 || continue
        runuser -u "$WATCHER_USER" -- "$cache" --noincremental >/dev/null 2>&1 \
            || true
        break
    done
}

# --- the modules ------------------------------------------------------------
#
# The core is the panel, the control command and the shared code. The LED bar,
# the CPU and GPU power, HDMI CEC and the drives are modules, and a person
# takes each one on its own.
#
# server/steamos_utility_center/modules.py holds the list of them and the rule
# for "is this one installed". These scripts ask it.
#
# A copy of that rule here would be a second answer to one question. This file
# exists because two copies of seven paths became different, and a fifth copy
# of the same idea is the same fault with a new name.

# Every module and whether this machine has it, as "name on" or "name off",
# one per line and in the order of the pages of the panel.
#
# It gives every module and not the installed ones only, because the caller
# needs both halves: which to install, and which to offer.
module_states() {   # module_states [home of the desktop user]
    PYTHONPATH="$SOURCE_DIR/server" python3 -c '
import sys
from steamos_utility_center import modules
home = sys.argv[1] or None
for name in modules.ORDER:
    print(name, "on" if modules.installed(name, home=home) else "off")
' "${1:-}"
}

# What one module is and what it brings, for a person who must decide.
#
# The panel puts the same sentences on the page. They are in modules.py, so
# the page and this text cannot become different.
module_says() {     # module_says <name>
    PYTHONPATH="$SOURCE_DIR/server" python3 -c '
import sys, textwrap
from steamos_utility_center import modules
name = sys.argv[1]
said = modules.SAYS[name]
print("  %-8s %s" % (name, said["title"]))
for key in ("does", "brings", "needs"):
    label = {"does": "", "brings": "Installs ", "needs": "Needs "}[key]
    for line in textwrap.wrap(label + said[key], 66):
        print("           " + line)
' "$1"
}

# --- what more than one script removes --------------------------------------
#
# Each function here is a removal that both the uninstaller and a module
# removal must do. A second copy in the installer is a copy that stops at a
# different point.

# The two units that this project puts into the systemd of the desktop user.
#
# Each unit that this project installs, and not the current units only. That
# list is what stops a file from an older installation from staying in a
# ~/.config directory with nothing to remove it.
remove_user_units() {
    watcher_user_dirs || return 0

    local unit removed=0
    for unit in "${WATCHER_UNITS[@]}"; do
        [[ -f "$WATCHER_DIR/$unit" ]] || continue
        user_systemctl stop "$unit" || true
        rm -f "$WATCHER_DIR/$unit" "$WATCHER_DIR/$WATCHER_WANTS/$unit"
        removed=1
    done

    # And the units that older releases installed and this one does not. See
    # RETIRED_USER_UNITS above. Without this, they continue to run at each
    # login and nothing here removes them.
    if remove_retired_user_files; then
        removed=1
    fi
    [[ $removed -eq 1 ]] || return 0

    user_systemctl daemon-reload || true
    say "Removed the desktop-session services for $WATCHER_USER"
}

# The mount units of the drives on the System page.
#
# Unmount each one before the unit files go, or the drive stays mounted until
# the machine restarts. systemd forgets a unit whose file is gone at the next
# daemon-reload, so `stop` after that finds no such unit.
remove_mount_units() {
    local mount_unit
    shopt -s nullglob
    for mount_unit in "$UNIT_DIR"/*.mount; do
        grep -q "written by the SteamOS Utility Center" "$mount_unit" \
            || continue
        say "Unmounting $(basename "$mount_unit")"
        systemctl disable --now "$(basename "$mount_unit")" 2>/dev/null || true
        rm -f "$mount_unit"
    done
    shopt -u nullglob
}

# The HDMI CEC toolkit.
#
# Its units, its helpers, its udev rule and its sudoers file each arrived
# through one script, so one script takes them back. The uninstaller of the
# toolkit refuses to run as root, and it thus needs the desktop user.
remove_cec_toolkit() {
    watcher_user_dirs || return 0
    local control="$WATCHER_HOME/.local/bin/steamos-cec-toolkitctl"
    [[ -x "$control" ]] || return 0
    say "Removing the HDMI CEC toolkit"
    if ! bash "$SOURCE_DIR/scripts/install-cec.sh" remove \
            "$SOURCE_DIR/cec-toolkit" "$WATCHER_USER"; then
        warn "the toolkit's own uninstaller did not finish - what is left"
        warn "can be removed with: $control uninstall"
        return 1
    fi
    return 0
}

# The Game Mode plugin, where this project installed one. Only the directory
# that this project writes, and not the plugins of other people beside it.
remove_decky_plugin() {
    watcher_user_dirs || return 0
    [[ -d "$WATCHER_HOME/$DECKY_PLUGIN" ]] || return 0
    say "Removing the Game Mode plugin"
    rm -rf "${WATCHER_HOME:?}/$DECKY_PLUGIN"
}

# --- the read-only rootfs ---------------------------------------------------
#
# SteamOS mounts / read-only, and both scripts write to it. The installer
# writes the suspend hook under /usr/lib/systemd and the kernel module under
# /usr/lib/modules. The uninstaller removes both.
#
# Under set -e, a write to a locked root filesystem gives no warning. It ends
# the run. This function thus runs one time, at the start, before a write and
# before a removal.
#
# It is here and not in each script, for the reason of each other value in this
# file. The installer had this function, and the uninstaller had a later copy
# that covered the kernel module only.
#
# The removal of the suspend hook, three steps earlier, thus failed on a
# read-only /usr. That failure ended the uninstall with the service files in
# place, and nothing reported it.
#
# The kernel module's own installer does the same dance; finding it already
# unlocked, it leaves it alone.

ROOTFS_RELOCK=0

relock_rootfs() {
    [[ $ROOTFS_RELOCK -eq 1 ]] || return 0
    ROOTFS_RELOCK=0                     # so the exit trap does not repeat it
    say "Locking the read-only rootfs again"
    steamos-readonly enable \
        || warn "could not lock it again: sudo steamos-readonly enable"
}

unlock_rootfs() {
    command -v steamos-readonly >/dev/null 2>&1 || return 0
    steamos-readonly status 2>/dev/null | grep -qi enabled || return 0
    say "Unlocking the read-only rootfs"
    if ! steamos-readonly disable; then
        warn "could not unlock the rootfs. Anything under /usr - the suspend"
        warn "hook and the kernel module - cannot be touched while / stays"
        warn "read-only."
        return 1
    fi
    ROOTFS_RELOCK=1
    # Put it back however this ends, including the die() paths.
    trap relock_rootfs EXIT
    return 0
}
