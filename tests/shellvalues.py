# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Reading a value out of the shell scripts, for tests to check against.

install.sh and uninstall.sh agree through scripts/user-unit.sh. That file
gives the unit names, the link and the directory. A test of those agreements
must read the same file, and it must not repeat the values. Two copies of one
error also match each other.

Bash reads the file, because bash reads it on the machine. A parser here is
one more program that can disagree with the script.
"""

import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED = os.path.join(HERE, "..", "scripts", "user-unit.sh")


def shell_value(name, path=SHARED):
    """One variable from a shell script: a string, or a list for an array."""
    done = subprocess.run(
        ["bash", "-c", 'source "$1"; printf "%%s\\n" "${%s[@]}"' % name,
         "_", path],
        stdout=subprocess.PIPE, text=True, check=True)
    lines = [line for line in done.stdout.split("\n") if line]
    return lines[0] if len(lines) == 1 else lines
