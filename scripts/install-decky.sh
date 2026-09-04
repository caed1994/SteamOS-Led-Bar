#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# Installs the Game Mode plugin, and makes Decky read it.
#
#   install-decky.sh <source-dir> <user>
#
# The panel runs it through pkexec, and install.sh runs it as root during a
# full installation. One program, so the two cannot install different files.
#
# Root is necessary for both halves. Decky Loader keeps its plugin directory
# as root and its loader reads them as root, so a copy as the desktop user
# fails there. And the loader reads a plugin when it starts, so a new one
# appears only after its service is restarted.
#
# What it does not do: install Decky. That is a program of other people, and
# this script says so and stops.
#
# Exit status:
#   0  installed
#   2  a wrong call
#   3  no Decky Loader on this machine
#   1  anything else

set -euo pipefail

# The files that Decky reads. dist/index.js is built and in the repository,
# because nobody must run npm on a Steam Machine.
FILES=(plugin.json main.py package.json dist/index.js)
PLUGIN="homebrew/plugins/SteamOS Utility Center"
UNIT="plugin_loader.service"

SOURCE="${1:-}"
WANTED_USER="${2:-}"
[[ -n "$SOURCE" && -n "$WANTED_USER" ]] \
    || { echo "usage: install-decky.sh <source-dir> <user>" >&2; exit 2; }
[[ -d "$SOURCE/decky" ]] \
    || { echo "no decky/ in $SOURCE - is that the clone?" >&2; exit 2; }

if [[ "$(id -u)" -ne 0 ]]; then
    echo "install-decky.sh has to run as root: Decky keeps its plugins as" >&2
    echo "root, and the loader is a system service." >&2
    exit 1
fi

HOME_DIR="$(getent passwd "$WANTED_USER" | cut -d: -f6)"
[[ -n "$HOME_DIR" && -d "$HOME_DIR" ]] \
    || { echo "no home directory for $WANTED_USER" >&2; exit 2; }

# ROOT is empty on a machine and a directory in the tests, the same way
# scripts/user-unit.sh and the other scripts here use it.
if [[ ! -d "${ROOT:-}$HOME_DIR/homebrew" ]]; then
    echo "Decky Loader is not installed." >&2
    echo "There is no $HOME_DIR/homebrew on this machine." >&2
    echo "Install Decky from https://decky.xyz and press this again." >&2
    exit 3
fi

WHERE="${ROOT:-}$HOME_DIR/$PLUGIN"
echo "Installing the Game Mode plugin to $WHERE"
install -d -m 0755 "$WHERE/dist"
for file in "${FILES[@]}"; do
    [[ -f "$SOURCE/decky/$file" ]] \
        || { echo "decky/$file is not in $SOURCE - is it up to date?" >&2;
             exit 1; }
    install -m 0644 "$SOURCE/decky/$file" "$WHERE/$file"
done

# Decky reads its plugins when the loader starts, so the new one appears at
# the next start and not before it.
echo "Restarting $UNIT"
if ! systemctl restart "$UNIT"; then
    echo "the plugin is installed, and $UNIT did not restart." >&2
    echo "Restart the machine to see it." >&2
    exit 1
fi

echo "The Game Mode plugin is installed. Open the Quick Access menu."
