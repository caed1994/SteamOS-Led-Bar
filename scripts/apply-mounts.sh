#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# Installs the drives of the System page and mounts them.
#
#   apply-mounts.sh <staged-record> [directory-to-give-away]
#
# The boot-time repair unit runs it with the record itself and no directory.
# See server/steamos-utility-center-mounts.service.
#
# This is separate from the control panel, as apply-config.sh and
# apply-power.sh are, and for the same reason. The step with root must be one
# short script that a person can read. It must not be a command line that a
# GUI builds.
#
# steamos-utility-center --write-mounts writes the unit files, and this script
# does not. One tested program builds the text, and this script does the
# systemctl work that a test cannot do.
#
# This script never writes /etc/fstab. See
# server/steamos_utility_center/mounts.py for the reason.

set -euo pipefail

RECORD="/var/lib/steamos-utility-center/mounts.conf"
APPLIER="/var/lib/steamos-utility-center/steamos-utility-center"
REPAIR_UNIT="steamos-utility-center-mounts.service"
MARK="written by the SteamOS Utility Center"

STAGED="${1:-}"
OWNER_DIR="${2:-}"
[[ -n "$STAGED" ]] || { echo "usage: apply-mounts.sh <record> [dir]" >&2; exit 2; }

# The staged file comes from a directory that the desktop user can write, and
# this program runs as root.
#
# A symlink there would make this read a file that the user cannot read.
# install(1) would then copy, say, /etc/shadow into a file that everybody can
# read. So this refuses anything but a regular file, and it refuses one that
# belongs to somebody else.
#
# Nobody asked when the boot-time unit calls this with the record, and there is
# no user to compare against in that case. That one is permitted, and every
# other call must name a file of the user who made it.
#
# The same check is in apply-config.sh, apply-power.sh and apply-mounts.sh, and
# tests/test_ctl.py runs the three of them to keep them equal.
ASKED_BY="${PKEXEC_UID:-${SUDO_UID:-}}"
[[ ! -L "$STAGED" ]] || { echo "$STAGED is a symlink" >&2; exit 2; }
[[ -f "$STAGED" ]] || { echo "no such file: $STAGED" >&2; exit 2; }
STAGED_OWNER="$(stat -c %u "$STAGED" 2>/dev/null || echo -1)"
[[ -z "$ASKED_BY" || "$STAGED_OWNER" == "$ASKED_BY" ]] \
    || { echo "$STAGED does not belong to whoever asked" >&2; exit 2; }
[[ -x "$APPLIER" ]] || { echo "no $APPLIER - is this installed?" >&2; exit 2; }

# The user that asked, which is the user a drive is given to.
#
# pkexec sets PKEXEC_UID to the uid that asked. That uid is the desktop user
# who runs this panel, and it is the correct answer.
TARGET_UID="${PKEXEC_UID:-${SUDO_UID:-}}"

# SteamOS keeps / read-only, and a unit file goes into /etc.
LOCK_AGAIN=0
relock_rootfs() {
    [[ "$LOCK_AGAIN" -eq 1 ]] || return 0
    steamos-readonly enable || true
}
trap relock_rootfs EXIT
if command -v steamos-readonly >/dev/null 2>&1; then
    if steamos-readonly status 2>/dev/null | grep -q enabled; then
        steamos-readonly disable
        LOCK_AGAIN=1
    fi
fi

# One call, which examines the record and then writes it.
#
# A record that this refuses leaves every unit as it was. The program that
# writes the units knows the rules, and a second copy of the rules in this
# script would be a second answer to the same question.
if ! said="$("$APPLIER" --write-mounts "$STAGED" 2>&1)"; then
    echo "the drives were rejected, so the old ones stay:" >&2
    echo "$said" >&2
    exit 1
fi
echo "$said"

# Unmount a drive that the new record does not name, before the reload.
#
# The unit file of that drive is off disk already. systemd keeps a unit that
# it loaded until the next daemon-reload, so it still knows this one by name
# and `stop` still unmounts it. After the reload it does not, and the drive
# would stay mounted until the machine restarts.
while read -r verb path; do
    [[ "$verb" == "removed" && "$path" == *.mount ]] || continue
    unit="$(basename "$path")"
    echo "Unmounting $unit"
    systemctl stop "$unit" || true
    systemctl disable "$unit" >/dev/null 2>&1 || true
done <<< "$said"

# The record itself, unless this run *is* the record.
#
# The boot-time repair unit calls this script with the record as the staged
# file, and install refuses a copy of a file onto itself.
if [[ "$(readlink -f "$STAGED")" != "$(readlink -f "$RECORD")" ]]; then
    install -d -m 0755 "$(dirname "$RECORD")"
    install -m 0644 "$STAGED" "$RECORD"
fi

systemctl daemon-reload

# Enable and mount each unit that this project wrote.
started=0
failed=0
shopt -s nullglob
for file in /etc/systemd/system/*.mount; do
    grep -q "$MARK" "$file" || continue
    unit="$(basename "$file")"
    systemctl enable "$unit" >/dev/null 2>&1 || true
    if systemctl restart "$unit"; then
        started=$((started + 1))
    else
        failed=$((failed + 1))
        echo "warning: $unit did not mount. Is the drive connected?" >&2
        systemctl status "$unit" --no-pager -n 5 2>/dev/null || true
    fi
done
shopt -u nullglob

# The unit that writes the units again at the next boot, for an update that
# did not honour the keep-list.
if [[ -f "/etc/systemd/system/$REPAIR_UNIT" ]]; then
    systemctl enable "$REPAIR_UNIT" >/dev/null 2>&1 || true
fi

# Give one directory to the desktop user.
#
# ext4 and its family record a user id for each file, so this is one chown and
# it stays. exfat, ntfs3 and vfat record no owner, and a mount option gives
# them one; the page writes that option and does not offer this button there.
if [[ -n "$OWNER_DIR" ]]; then
    if [[ -z "$TARGET_UID" ]]; then
        echo "cannot tell which user to give $OWNER_DIR to" >&2
        exit 1
    fi
    if ! mountpoint -q "$OWNER_DIR"; then
        # Without this the chown walks the empty directory under the mount
        # point, reports success, and changes nothing that a person can see.
        echo "$OWNER_DIR is not mounted, so there is nothing to give away" >&2
        exit 1
    fi
    echo "Giving $OWNER_DIR to $(id -un "$TARGET_UID")"
    chown -R "$TARGET_UID:$(id -g "$TARGET_UID")" "$OWNER_DIR"
fi

echo "$started drive(s) mounted, $failed did not"
exit 0
