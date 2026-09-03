#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# Switches the unit that wakes the television after a resume.
#
#   resume-wake.sh on|off
#
# One program that does one thing, and that is the whole reason it exists.
#
# steamos-cec-resume-wake.service is a unit of root: it is wanted by
# suspend.target, and to enable it writes a link into a directory of /etc.
# Every switch of this kind in the CEC toolkit has a program like this one,
# and a line in a sudoers file that permits it. This switch is not upstream's,
# so it had none, and it went through scripts/install-cec.sh under pkexec.
#
# That works on a desktop and nowhere else. Game Mode runs no polkit agent, so
# the one switch on that page that a person could not move was this one.
#
# A sudoers rule for install-cec.sh is not the answer. That script also
# installs and removes the whole toolkit, and a rule for it permits all of
# that. A rule names a program, so the program has to be this small.
#
# See server/steamos_utility_center/ctl.py, which writes the two lines that
# permit this: one for "on" and one for "off", with no wildcard in either.

set -euo pipefail

UNIT="steamos-cec-resume-wake.service"

STATE="${1:-}"
[[ "$STATE" == "on" || "$STATE" == "off" ]] \
    || { echo "usage: resume-wake.sh on|off" >&2; exit 2; }

# ROOT is empty on a machine and a directory in the tests, the same way
# scripts/user-unit.sh and scripts/install-cec.sh use it.
if [[ ! -f "${ROOT:-}/etc/systemd/system/$UNIT" ]]; then
    echo "$UNIT is not there - install the CEC toolkit first." >&2
    exit 1
fi

if [[ "$STATE" == "on" ]]; then
    systemctl enable "$UNIT"
else
    systemctl disable "$UNIT"
fi
