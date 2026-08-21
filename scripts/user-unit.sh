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
