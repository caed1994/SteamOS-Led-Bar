# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# What install.sh and uninstall.sh have to agree on.
#
# Mostly the units that run in the desktop session. Both are *user* systemd
# units - one talks to the logged-in user's Steam client, the other reads that
# user's session bus - while install.sh and uninstall.sh both run as root.
# Deriving "which user, which directory, which session" twice is how the two
# scripts drift apart, and the failure mode is quiet: a dangling enable symlink
# left behind in somebody's ~/.config. So it is derived once, here.
#
# Sourced, not executed.

# How both scripts say things. Here because the shared code below says things
# too, and a helper that printed differently depending on which script sourced
# it would be a helper you cannot read the output of. `die` stays with the
# installer: stopping is its decision, not a shared one.
say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m warning:\033[0m %s\n' "$*" >&2; }

# --- where everything lands -------------------------------------------------
#
# Named once here rather than in each script. install.sh and uninstall.sh both
# had their own copy of these seven paths, spelled identically, and a pair of
# constants that have to match by hand is a pair that eventually does not: the
# installer writing one name and the uninstaller looking for another leaves
# a service nothing can remove.
NAME="steamos-utility-center"

# Everything below hangs off this, and on a real machine it is empty - the
# paths are the absolute ones they read as. It is here so the migration can be
# run against a directory a test built, which is the only way to check that an
# old install is cleared away without having an old install to break. Nothing
# sets it but the tests.
ROOT="${ROOT:-}"

INSTALL_DIR="$ROOT/var/lib/$NAME"
# Which commit the files in there came from. Written by the installer, read by
# the panel, and the answer to a question that has now cost two evenings:
# "pulled" and "installed" are two steps, and nothing on screen told them
# apart - so a clone three commits ahead of the running copy looked exactly
# like an up-to-date machine. Inside INSTALL_DIR, so the uninstaller's one
# rm -rf takes it with everything else.
STAMP_PATH="$INSTALL_DIR/installed-from"
CONFIG_PATH="$ROOT/etc/$NAME.conf"
POWER_CONFIG_PATH="$ROOT/etc/$NAME-power.conf"
UNIT_DIR="$ROOT/etc/systemd/system"
UNIT_PATH="$UNIT_DIR/$NAME.service"
POWER_UNIT_PATH="$UNIT_DIR/$NAME-power.service"
UDEV_PATH="$ROOT/etc/udev/rules.d/99-$NAME.rules"
SLEEP_HOOK_PATH="$ROOT/usr/lib/systemd/system-sleep/$NAME"
# Not renamed with the rest, because this project does not write it: the
# vendored leds-valve-shim installer does, under this name, and that script is
# kept unmodified - see leds-valve-shim/PROVENANCE.md. Renaming our copy of
# the name would leave the uninstaller looking for a file nothing writes, and
# the real one behind, still loading the module at every boot.
MODULES_LOAD="$ROOT/etc/modules-load.d/steamos-led-bar.conf"

# --- the kernel shim, on every kernel that has one --------------------------
#
# The module is installed into the running kernel's own updates/ directory. A
# SteamOS update brings a new kernel and leaves the old one's modules exactly
# where they are - so the copy built for the kernel before last sits in its
# own directory for ever: it does nothing, because that kernel is not the one
# running, and it survived an uninstall that only ever looked at `uname -r`.
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

# The name you can type. Everything this project installs lives in /var/lib so
# it survives a SteamOS update, and nothing there is on anybody's PATH - so
# without this, every command in the README is one you can read and not run.
COMMAND_LINK="$ROOT/usr/local/bin/$NAME"
# The second program this installs, linked for the same reason: the README
# tells you to run it, and a command you can read and not type is worse than
# no command at all.
POWER_COMMAND_LINK="$ROOT/usr/local/bin/$NAME-power"

WATCHER_UNIT="$NAME-achievements.service"
PHONE_UNIT="$NAME-phone.service"
# Everything installed into the user's systemd, walked rather than named twice:
# a unit added to one script and missed in the other is a file nobody removes.
WATCHER_UNITS=("$WATCHER_UNIT" "$PHONE_UNIT")
# Must match WantedBy= in those units: it is where the enable symlink goes.
WATCHER_WANTS="default.target.wants"

# Who is asking, given that the scripts sourcing this always run as root.
#
# Two spellings, because there are two ways in: sudo from a terminal sets
# SUDO_USER, and pkexec - which is how the control panel runs the installer -
# sets PKEXEC_UID and no SUDO_USER at all.
#
# Reading only SUDO_USER is why an install started from the panel decided
# there was nobody to install the user units for and skipped the lot: the
# watchers, the menu entry, and the lingering that keeps them alive across
# Game Mode. It said so in one line that scrolls past in the log, and the
# panel then reported the lingering as a problem - correctly, and about
# something it had just skipped itself. scripts/flash-firmware.sh reads both
# and always did.
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

# Run `systemctl --user ...` in that user's session. Returns non-zero when
# there is no live session - which is not an error: the unit is enabled on
# disk either way and starts at the next login.
user_systemctl() {
    [[ -d "$WATCHER_RUNTIME" ]] || return 1
    runuser -u "$WATCHER_USER" -- env "XDG_RUNTIME_DIR=$WATCHER_RUNTIME" \
        systemctl --user "$@" >/dev/null 2>&1
}

# Whether that user's systemd is set to keep running with no session open.
# The same question the control panel asks, and the only one that settles it -
# asked by the installer before it turns the switch on, and by the uninstaller
# to say that it left it on.
linger_is_on() {    # linger_is_on USER
    local state
    state="$(loginctl show-user "$1" --property=Linger 2>/dev/null || true)"
    [[ "$state" == *"Linger=yes"* ]]
}

# --- the desktop user's own home --------------------------------------------
#
# Three things land there that root's paths do not cover, and all three have to
# be named identically by the script that writes them and the one that takes
# them back - see the note at the top of this file.

# The control panel's menu entry, and the icon it points at. The icon is filed
# under the width read out of the PNG, so the uninstaller matches every size
# rather than guessing at one: an older install may have left one elsewhere.
PANEL_ENTRY_DIR=".local/share/applications"
PANEL_ENTRY="$NAME.desktop"
# The installed icon's name, which is also what the entry's Icon= line says -
# a theme icon name rather than a path, so it survives the clone being moved.
# Named here and used by both scripts: the installer wrote one name and the
# glob below looked for another for exactly as long as it took to notice, and
# what that leaves behind is an icon nothing removes.
PANEL_ICON="$NAME"
PANEL_ICON_GLOB=".local/share/icons/hicolor/*/apps/$PANEL_ICON.png"
# The panel's own preferences, which it writes as the user - so this is not
# where the name is decided, gui/appsettings.py is. Repeated here only so the
# migration can move the file, and checked against the panel by a test.
PANEL_CONFIG="$NAME-panel.conf"

# What the installer appends to the user's .bashrc so that "pio" is a command
# they can type. Both lines exactly as written, because the uninstaller deletes
# them by exact match - anything looser would edit a line somebody put there
# themselves.
PLATFORMIO_PATH_NOTE="# PlatformIO, added by the SteamOS Utility Center installer."
# What it said before the rename. The uninstaller strips these lines by exact
# match, so changing the wording without keeping the old one would leave every
# .bashrc written by an older install carrying a comment nothing removes -
# from an installer under a name that no longer exists.
OLD_PLATFORMIO_PATH_NOTE="# PlatformIO, added by the SteamOS LED bar installer."
PLATFORMIO_PATH_LINE='export PATH="$HOME/.platformio/penv/bin:$PATH"'
# What the idempotence check looks for, so a second install does not stack a
# second copy - and so the uninstaller can tell whether there is one at all.
PLATFORMIO_PATH_MARK=".platformio/penv/bin"

# --- the names this project installed under before it was renamed -----------
#
# It was "SteamOS LED bar", and everything it put on a machine was called
# steamos-led-something. The project grew past the LED bar - CPU and GPU power,
# HDMI CEC, the keyboard layout - and was renamed to the SteamOS Utility
# Centre, which renamed all of that with it.
#
# The rename is the easy half. The hard half is the machine that already has
# the old one: nothing about installing under new names removes what is
# sitting there under the old ones. Left alone, an update would leave the old
# steamos-led-serial.service enabled and running beside the new unit - two
# processes on one serial port - and the settings in /etc/steamos-led-serial.conf
# would simply stop being read, so every one of them would silently go back to
# its default. That is what migrate_old_install below is for, and it runs
# before the installer writes anything.
#
# Kept as data rather than as a sequence of rm lines, so the test can walk the
# same list the installer does. Everything here is a *former* name: nothing in
# this block is written any more, only looked for and taken away.
OLD_NAME="steamos-led"
OLD_INSTALL_DIR="$ROOT/var/lib/steamos-led-serial"

# old unit -> the unit that replaces it. The old one is stopped and disabled
# before it is deleted: a unit file that is merely removed leaves its enable
# symlink behind, and systemd goes on trying to start something that is not
# there.
OLD_SYSTEM_UNITS=("steamos-led-serial.service:$UNIT_PATH"
                  "steamos-led-power.service:$POWER_UNIT_PATH")
OLD_USER_UNITS=("steamos-led-achievements.service:$WATCHER_UNIT"
                "steamos-led-phone.service:$PHONE_UNIT")

# What this project used to install into the desktop session and does not any
# more. Not renames - there is nothing they became - so they cannot go in the
# list above, and a file with no replacement still has to be taken away or
# systemd goes on running it at every login.
#
#   $NAME-cec.service      put the CEC adapter on the bus before the CEC
#                          toolkit's wake service. The CEC module does that
#                          itself now - cec-toolkit/bin/steamos-cec-register.
#   the drop-in            set Type=simple on that toolkit's boot wake, so it
#                          stopped holding the session up. Its own unit says
#                          Type=simple now.
RETIRED_USER_UNITS=("$NAME-cec.service")
RETIRED_USER_DROPINS=("steamos-cec-boot-wake.service.d/10-$NAME.conf")

# old config -> where it is carried to. Moved rather than deleted: these hold
# the answers somebody gave the installer and every setting they have changed
# since, and there is no getting them back.
OLD_CONFIGS=("$ROOT/etc/steamos-led-serial.conf:$CONFIG_PATH"
             "$ROOT/etc/steamos-led-power.conf:$POWER_CONFIG_PATH")

# And the rest, which carry no state and are simply removed - the new install
# writes its own.
OLD_FILES=("$ROOT/etc/udev/rules.d/99-steamos-led-serial.rules"
           "$ROOT/usr/lib/systemd/system-sleep/steamos-led-serial"
           "$ROOT/usr/local/bin/steamos-led-serial"
           "$ROOT/usr/local/bin/steamos-led-power")
# /etc/modules-load.d/steamos-led-bar.conf is deliberately *not* in that list.
# It is not an old name of ours: the vendored shim installer writes it under
# that name today, and removing it here would stop the kernel module loading
# at the next boot - which is the strip going dark after an update.

# The one line inside a carried-over config that would otherwise be wrong.
#
# NOTIFY_FIFO is a live setting, not a comment, and it names a directory the
# *unit* creates - RuntimeDirectory=. Renaming the unit renames that directory,
# so a config carried across verbatim would point the notification pipe at
# /run/steamos-led-serial, which nothing creates any more: flashes would stop
# arriving, with nothing in the log to say why.
#
# Only when it still holds the old default. Somebody who pointed it somewhere
# of their own meant it, and rewriting that would be this script overruling a
# setting rather than repairing one.
OLD_FIFO="/run/steamos-led-serial/notify"
NEW_FIFO="/run/$NAME/notify"

# Deliberately not renamed, and worth saying so where the list is:
#
#   /dev/valve-leds-shim and the leds-valve-shim module - vendored unmodified
#   from another project, and they name Valve's LED interface rather than
#   anything of ours. The name is also compiled into the .ko.
#
#   /dev/steamos-led-esp - the udev symlink for the ESP. A config that has
#   SERIAL_PORT=/dev/steamos-led-esp in it is pointing at a piece of hardware,
#   and renaming the symlink under it would stop the strip outright. It names
#   the LED bar's board, which is still exactly what it is.

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

# Take the old install away and bring its settings across. Run as root, before
# anything new is written, and safe to run on a machine that never had the old
# names - which is what old_install_present is asked first.
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
            # Both present, which is a machine somebody has already migrated
            # and then put the old file back, or one where this ran twice.
            # The new one is the one in use; the old is left where it is
            # rather than thrown away, because it is still somebody's settings.
            warn "$new exists too - leaving $old alone, it is no longer read"
            continue
        fi
        mv "$old" "$new"
        say "  moved your settings from $old to $new"
    done

    # The pipe the notifications arrive on - see OLD_FIFO. Twice over, on
    # purpose: the grep decides whether to touch the file at all, and the sed
    # is anchored to the whole line so that what it rewrites is that exact
    # setting. Either alone would leave a pipe somebody pointed elsewhere
    # untouched; both is what keeps a later edit to one of them from being the
    # edit that starts overwriting somebody's own path.
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

# The uninstaller's half of the same list: take the old install away and keep
# nothing. Separate from migrate_old_install rather than a flag on it, because
# the two disagree about the one thing that matters - what happens to the
# settings. Migrating carries them across; uninstalling is being asked to
# remove this project, and a config file left named after a project that is no
# longer installed is litter.
#
# Called by uninstall.sh, which somebody may run without ever having run the
# new installer: they pull, they uninstall, and every name on the machine is
# an old one. --purge decides the configs here exactly as it does for the
# current names.
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
# A drop-in's directory goes only when it is empty - it belongs to somebody
# else's unit, and they may have overrides of their own in it.
remove_retired_user_files() {
    # Returns 0 only when it actually removed something - both callers say so
    # out loud, and "there is no desktop user" is not something to announce.
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

# The half of it that lives in the desktop user's home. Separate because it
# needs that user to have been worked out, and because a machine with no
# desktop user still has the root half to clear away.
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

    # And the panel's own preferences, which are the user's to keep. The panel
    # reads the old name as a fallback too - see gui/appsettings.py - so this
    # is belt and braces rather than the only path.
    # $NAME-panel.conf, which is what gui/appsettings.py reads - not
    # $NAME.conf. Named wrongly here the file would be moved somewhere the
    # panel never looks, which is the theme and the window size quietly back
    # to their defaults. There is a test tying the two names together.
    local old_prefs="$WATCHER_HOME/.config/steamos-led-panel.conf"
    local new_prefs="$WATCHER_HOME/.config/$PANEL_CONFIG"
    if [[ -f "$old_prefs" && ! -f "$new_prefs" ]]; then
        mv "$old_prefs" "$new_prefs"
        chown "$WATCHER_USER:$WATCHER_USER" "$new_prefs"
        say "  moved the panel's settings to $new_prefs"
    fi
}

refresh_desktop_caches() {  # refresh_desktop_caches <applications dir>
    # Plasma reads the menu from a cache it builds itself, so an entry that has
    # been rewritten keeps its old icon - and one that has been removed goes on
    # being offered, and launching something that is no longer there - until
    # that cache is rebuilt. Best effort: none of this is worth failing over,
    # and a logout fixes it anyway.
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

# --- the read-only rootfs ---------------------------------------------------
#
# SteamOS mounts / read-only, and both scripts touch it: the installer writes
# the suspend hook under /usr/lib/systemd and the kernel module under
# /usr/lib/modules, and the uninstaller takes the same two back out. Under
# set -e a write to a locked rootfs does not warn, it ends the run - so this is
# done once, at the top, before anything is written or removed.
#
# Here rather than in each script for the reason everything else in this file
# is here. The installer had it and the uninstaller had its own, later copy
# that only covered the kernel module - so removing the suspend hook, three
# steps earlier, failed on a read-only /usr and aborted the whole uninstall
# with the service files still in place and nothing said about it.
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
