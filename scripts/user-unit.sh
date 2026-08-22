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

# The name you can type. Everything this project installs lives in /var/lib so
# it survives a SteamOS update, and nothing there is on anybody's PATH - so
# without this, every command in the README is one you can read and not run.
COMMAND_LINK="/usr/local/bin/steamos-led-serial"

WATCHER_UNIT="steamos-led-achievements.service"
PHONE_UNIT="steamos-led-phone.service"
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
