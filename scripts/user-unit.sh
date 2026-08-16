# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# Shared shell helpers for the units that run in the desktop session.
#
# Both are *user* systemd units - one talks to the logged-in user's Steam
# client, the other reads that user's session bus - while install.sh and
# uninstall.sh both run as root. Deriving "which user, which directory, which
# session" twice is how the two scripts drift apart, and the failure mode is
# quiet: a dangling enable symlink left behind in somebody's ~/.config. So it
# is derived once, here.
#
# Sourced, not executed.

WATCHER_UNIT="steamos-led-achievements.service"
PHONE_UNIT="steamos-led-phone.service"
# Everything installed into the user's systemd, walked rather than named twice:
# a unit added to one script and missed in the other is a file nobody removes.
WATCHER_UNITS=("$WATCHER_UNIT" "$PHONE_UNIT")
# Must match WantedBy= in those units: it is where the enable symlink goes.
WATCHER_WANTS="default.target.wants"

# Sets WATCHER_USER, WATCHER_HOME, WATCHER_DIR and WATCHER_RUNTIME for the
# desktop user who invoked sudo. Returns non-zero, quietly, when there is no
# such user. The installer also uses this to run PlatformIO as that user.
watcher_user_dirs() {
    WATCHER_USER="${SUDO_USER:-}"
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
