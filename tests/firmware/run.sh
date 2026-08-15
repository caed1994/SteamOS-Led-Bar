#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# Compiles the firmware against stubbed Arduino/NeoPixelBus headers and
# exercises its protocol handling on the host. No board required.
#
#   ./tests/firmware/run.sh

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BINARY="$(mktemp -t fwtest.XXXXXX)"
trap 'rm -f "$BINARY"' EXIT

g++ -std=c++11 -Wall -Wextra \
    -I "$HERE/stubs" \
    -include "$HERE/stubs/Arduino.h" \
    -include "$HERE/stubs/NeoPixelBus.h" \
    "$HERE/main_test.cpp" -o "$BINARY"

"$BINARY"
