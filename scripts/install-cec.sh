#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# Installs or removes the SteamOS CEC Toolkit under cec-toolkit/.
#
#   install-cec.sh install <source-dir> [user]
#   install-cec.sh remove  <source-dir> [user]
#
# Run through pkexec by the control panel, so this runs as root.
#
# This is the difficult part, and it is the reason for this script.
#
# The installer of the toolkit refuses to run as root, and it calls `sudo`
# approximately forty times. That is correct for a person at a terminal. It is
# wrong for a window with no place to type a password.
#
# This script does not rewrite that installer. The toolkit is a module of its
# own, and a person with no panel installs it from a terminal.
#
# This script thus changes to the desktop user and gives that user a sudo rule
# for the time of the installation.
#
# **The rule is not a security boundary.** It names the seven programs that the
# installer calls. But `sudo install` and `sudo rm` are root with another name,
# so a shorter list is documentation and not containment.
#
# Time is the containment. This script writes the rule after polkit gives it
# root, and removes the rule before the script ends. It removes the rule on
# each path out, and a signal is one of them.
#
# The person who started this already had root for that moment. The rule thus
# gives nothing more. It gives the same rights in the form that the installer
# of the toolkit expects.
#
# A `kill -9` leaves a copy of the rule, and the trap cannot cover that case.
# Each run thus removes an old copy first, and the Status and Repair page of
# the panel reports a copy that it finds.

set -euo pipefail

ACTION="${1:-}"
SOURCE="${2:-}"
WANTED_USER="${3:-}"

RULE="/etc/sudoers.d/zz-steamos-utility-center-cec-install"

usage() {
    echo "usage: install-cec.sh install|remove <source-dir> [user]" >&2
    echo "       install-cec.sh resume-wake on|off" >&2
    exit 2
}

[[ "$ACTION" == "install" || "$ACTION" == "remove" \
        || "$ACTION" == "resume-wake" ]] || usage
if [[ "$ACTION" == "resume-wake" ]]; then
    # The second argument is on|off rather than a directory: this action
    # installs nothing and only switches a unit that is already there.
    [[ "$SOURCE" == "on" || "$SOURCE" == "off" ]] || usage
else
    [[ -n "$SOURCE" ]] || usage
fi

if [[ "$(id -u)" -ne 0 ]]; then
    echo "install-cec.sh has to run as root - the panel starts it with pkexec." >&2
    exit 1
fi

# The resume-wake unit. The toolkit installs it and enables it with the Steam
# button only.
#
# Its control program has the unit in neither service table, so nothing that
# the program offers can switch it. This action is one systemctl command, and
# the other parts of this script do not apply to it.
RESUME_WAKE_UNIT="steamos-cec-resume-wake.service"

# ROOT is empty on a machine and a directory in the tests, the same way
# scripts/user-unit.sh uses it.
if [[ "$ACTION" == "resume-wake" ]]; then
    if [[ ! -f "${ROOT:-}/etc/systemd/system/$RESUME_WAKE_UNIT" ]]; then
        echo "$RESUME_WAKE_UNIT is not there - install the CEC toolkit first." >&2
        exit 1
    fi
    if [[ "$SOURCE" == "on" ]]; then
        systemctl enable "$RESUME_WAKE_UNIT"
    else
        systemctl disable "$RESUME_WAKE_UNIT"
    fi
    exit 0
fi

INSTALLER="$SOURCE/$ACTION.sh"
[[ "$ACTION" == "install" ]] || INSTALLER="$SOURCE/uninstall.sh"
if [[ ! -f "$INSTALLER" ]]; then
    echo "no $(basename "$INSTALLER") in $SOURCE - is that the CEC toolkit?" >&2
    exit 1
fi

# The user to install for.
#
# pkexec sets PKEXEC_UID to the uid that asked. That uid is the desktop user
# that runs this panel, and it is the correct answer. An argument is a value
# that the caller selected.
#
# This script accepts the argument, so that a person can run it by hand and a
# test can drive it. It reads the argument only when there is no PKEXEC_UID.
if [[ -n "${PKEXEC_UID:-}" ]]; then
    TARGET="$(getent passwd "$PKEXEC_UID" | cut -d: -f1 || true)"
elif [[ -n "$WANTED_USER" ]]; then
    TARGET="$WANTED_USER"
else
    TARGET=""
fi

if [[ -z "$TARGET" ]]; then
    echo "could not tell which user to install for." >&2
    exit 1
fi
if [[ "$TARGET" == "root" ]]; then
    # One half of the toolkit is user systemd units and a WirePlumber
    # configuration in a home directory. In the home directory of root, they
    # are in a session that never controls a television.
    echo "the CEC toolkit installs into a desktop session, and root is not one." >&2
    exit 1
fi

TARGET_HOME="$(getent passwd "$TARGET" | cut -d: -f6)"
TARGET_UID="$(id -u "$TARGET")"
if [[ -z "$TARGET_HOME" || ! -d "$TARGET_HOME" ]]; then
    echo "$TARGET has no home directory to install into." >&2
    exit 1
fi

for needed in runuser visudo; do
    command -v "$needed" >/dev/null 2>&1 || {
        echo "missing required command: $needed" >&2
        exit 1
    }
done

drop_the_rule() {
    rm -f "$RULE"
}

# This trap is set before the write of the rule, and it covers each exit that
# is not a normal return.
#
# The same trap also removes a copy from a previous run that a signal stopped.
# This script replaces the file and does not add to it, so the exit of this run
# removes both copies.
trap drop_the_rule EXIT INT TERM

# This writes the rule to a temporary file and checks it before it puts it in
# place.
#
# A malformed file in /etc/sudoers.d stops *sudo itself* and not this rule
# alone. On a machine whose one other route in is this panel, that is a serious
# fault.
#
# visudo -c is the check that sudo makes. This runs it while it costs
# nothing.
#
# The five programs are the programs that the installers of the toolkit call
# under sudo. A test reads that list from the tree and does not use this
# comment.
#
# Three other sudo calls in the toolkit are not in the list, and they need no
# entry. Two of them are the root helpers of the toolkit, and its *permanent*
# sudoers file covers them at the time of the call. The third is behind a
# --verify flag that this script does not give.
staged="$(mktemp)"
{
    echo "# Written by the SteamOS Utility Center while installing the CEC"
    echo "# toolkit, and removed again when it finishes. If you are reading"
    echo "# this in a running system, an install was killed part way through"
    echo "# and this file is safe to delete."
    for program in install systemctl rm sed udevadm; do
        where="$(command -v "$program" || true)"
        [[ -n "$where" ]] && echo "$TARGET ALL=(root) NOPASSWD: $where"
    done
} > "$staged"

if ! visudo -c -q -f "$staged"; then
    rm -f "$staged"
    echo "the temporary sudo rule did not check out, so nothing was changed." >&2
    exit 1
fi
install -m 0440 -o root -g root "$staged" "$RULE"
rm -f "$staged"

# What the installer needs, and nothing more.
#
# systemctl --user speaks to a bus of one user. Without these two variables,
# the user half of the installation thus fails and the root half succeeds.
#
# That is the worst result, because the installation looks complete.
export_env=(
    "HOME=$TARGET_HOME"
    "USER=$TARGET"
    "LOGNAME=$TARGET"
    "XDG_RUNTIME_DIR=/run/user/$TARGET_UID"
    "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$TARGET_UID/bus"
    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)

if [[ ! -d "/run/user/$TARGET_UID" ]]; then
    echo "warning: $TARGET has no session bus at /run/user/$TARGET_UID." >&2
    echo "         The user services will be installed but not started." >&2
fi

flags=()
if [[ "$ACTION" == "install" ]]; then
    # This installs the files of each feature and switches none of them on.
    #
    # The page of the panel is eight switches, and a feature that is on at the
    # start is a feature that nobody selected.
    #
    # The volume integration is the one feature that the installer of the
    # toolkit switches on. set-external-volume writes its own files, so a
    # switch on the page later is a complete path.
    flags=(--no-external-volume)
fi

echo "Running the CEC toolkit's own $ACTION as $TARGET."
runuser -u "$TARGET" -- env "${export_env[@]}" bash "$INSTALLER" "${flags[@]}"

if [[ "$ACTION" == "install" ]]; then
    echo
    echo "Installed. Nothing is switched on yet - the panel's HDMI CEC page"
    echo "has a switch for each feature."
else
    echo
    echo "Removed."
fi
