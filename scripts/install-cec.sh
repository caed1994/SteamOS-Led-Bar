#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# Installs or removes the vendored SteamOS CEC Toolkit.
#
#   install-cec.sh install <source-dir> [user]
#   install-cec.sh remove  <source-dir> [user]
#
# Run through pkexec by the control panel, so this runs as root.
#
# The awkward part, and why this script exists at all: the toolkit's own
# installer refuses to run as root and shells out to `sudo` about forty times.
# That is the right shape for somebody at a terminal and the wrong one for a
# window with nowhere to type a password. Rather than fork the installer - a
# vendored tree we edit is a tree we cannot diff against upstream - this drops
# back to the desktop user and gives that user a sudo rule for the duration.
#
# **The rule is not a security boundary and is not pretending to be one.** It
# lists the seven programs the installer calls, but `sudo install` and `sudo
# rm` are root by any other name, so narrowing it buys documentation rather
# than containment. What contains it is time: it is written after polkit has
# already authorised this script as root, and removed before the script ends,
# on every path out including a signal. Whoever triggered this had root for
# the moment already - the rule hands over nothing extra, it only hands it
# over in the shape the toolkit's installer expects to find it.
#
# A copy left behind by a kill -9 is the one case the trap cannot cover, so
# every run clears a stale one first and the panel's Status & Repair page
# reports one it finds.

set -euo pipefail

ACTION="${1:-}"
SOURCE="${2:-}"
WANTED_USER="${3:-}"

RULE="/etc/sudoers.d/zz-steamos-utility-center-cec-install"

usage() {
    echo "usage: install-cec.sh install|remove <source-dir> [user]" >&2
    echo "       install-cec.sh resume-wake on|off" >&2
    echo "       install-cec.sh wake-ids" >&2
    exit 2
}

[[ "$ACTION" == "install" || "$ACTION" == "remove" \
        || "$ACTION" == "resume-wake" || "$ACTION" == "wake-ids" ]] || usage
if [[ "$ACTION" == "resume-wake" ]]; then
    # The second argument is on|off rather than a directory: this action
    # installs nothing and only switches a unit that is already there.
    [[ "$SOURCE" == "on" || "$SOURCE" == "off" ]] || usage
elif [[ "$ACTION" == "wake-ids" ]]; then
    :                       # takes no arguments; see add_bluetooth_wake_ids
else
    [[ -n "$SOURCE" ]] || usage
fi

if [[ "$(id -u)" -ne 0 ]]; then
    echo "install-cec.sh has to run as root - the panel starts it with pkexec." >&2
    exit 1
fi

# The resume-wake unit, which the toolkit installs and then enables only
# alongside the Steam button - and its control program has it in neither
# service table, so nothing it offers can switch it afterwards. One
# systemctl, and none of the rest of this script applies.
RESUME_WAKE_UNIT="steamos-cec-resume-wake.service"

# --- letting a controller wake the machine -----------------------------------
#
# The toolkit switches USB wakeup on for the radio a controller talks to, and
# finds that radio three ways: an exact vendor:product list, a regular
# expression over the device's name, and the USB class for Bluetooth. On a
# machine that is not a Steam Deck all three miss.
#
#   USB_WAKE_USB_IDS=8087:0032          an Intel chip - the Deck's
#   USB_WAKE_MATCH=bluetooth|xbox|...   matched against "maker product"
#   bDeviceClass == e0                  the Bluetooth device class
#
# Measured on an AM5 board with a MediaTek combo chip:
#
#   0e8d:0616 MediaTek Inc. Wireless_Device
#   class=ef sub=02 proto=01
#
# Not the Intel id. A Bluetooth radio whose name does not contain the word
# Bluetooth. And ef/02/01 is Interface Association: "I am several things, my
# classes are in my interfaces" - which is what every wifi-and-bluetooth combo
# chip says, so the class check cannot match one. The toolkit's own log said
# "matched":0 and nothing explained why.
#
# This is that check done one level down, where the answer is. The ids it
# finds are added to USB_WAKE_USB_IDS, which is the one route of the three
# that is exact - so it does not matter what the radio calls itself.
SYSFS_USB="${SYSFS_USB:-/sys/bus/usb/devices}"
TOOLKIT_CONFIG="${ROOT:-}/etc/steamos-cec-toolkit.conf"

# Class, subclass and protocol of a Bluetooth interface, as sysfs spells them.
BLUETOOTH_INTERFACE="e0 01 01"

bluetooth_usb_ids() {
    local interface parent found=()
    for interface in "$SYSFS_USB"/*:*/bInterfaceClass; do
        [[ -r "$interface" ]] || continue
        local dir="${interface%/bInterfaceClass}"
        local class sub proto
        class="$(cat "$dir/bInterfaceClass" 2>/dev/null || true)"
        sub="$(cat "$dir/bInterfaceSubClass" 2>/dev/null || true)"
        proto="$(cat "$dir/bInterfaceProtocol" 2>/dev/null || true)"
        [[ "$class $sub $proto" == "$BLUETOOTH_INTERFACE" ]] || continue
        # /sys/bus/usb/devices/1-12:1.0 belongs to .../1-12
        parent="${dir%:*}"
        local vendor product
        vendor="$(cat "$parent/idVendor" 2>/dev/null || true)"
        product="$(cat "$parent/idProduct" 2>/dev/null || true)"
        [[ -n "$vendor" && -n "$product" ]] || continue
        local id="$vendor:$product"
        [[ " ${found[*]-} " == *" $id "* ]] || found+=("$id")
    done
    printf '%s\n' "${found[@]-}"
}

add_bluetooth_wake_ids() {
    if [[ ! -f "$TOOLKIT_CONFIG" ]]; then
        echo "No $TOOLKIT_CONFIG, so there is nothing to add the radio to." >&2
        return 0
    fi
    local current wanted id found=() added=()
    current="$(sed -n 's/^USB_WAKE_USB_IDS=//p' "$TOOLKIT_CONFIG" \
               | tail -n 1 | tr -d '"'"'"'"')"
    wanted="$current"
    while read -r id; do
        [[ -n "$id" ]] || continue
        found+=("$id")
        [[ " $wanted " == *" $id "* ]] && continue
        wanted="${wanted:+$wanted }$id"
        added+=("$id")
    done < <(bluetooth_usb_ids)

    # What was found is said either way, and the two answers are told apart.
    # They were not: finding nothing printed the same line as finding one that
    # was already listed, so a machine where this does not work at all read
    # exactly like one where it had nothing left to do - which is no way to
    # answer "did it find mine?".
    if [[ ${#found[@]} -eq 0 ]]; then
        echo "No Bluetooth radio on the USB bus, so there is nothing to" \
             "allow. One built into the board and not wired through USB" \
             "will not appear here."
        return 0
    fi
    if [[ ${#added[@]} -eq 0 ]]; then
        echo "Found ${found[*]}, already allowed to wake this machine."
        return 0
    fi
    # Replaced whole rather than appended: the file may carry the key once,
    # with any value, and a second line of the same key is a file that reads
    # differently depending on which one is sourced last.
    if grep -q '^USB_WAKE_USB_IDS=' "$TOOLKIT_CONFIG"; then
        sed -i "s|^USB_WAKE_USB_IDS=.*|USB_WAKE_USB_IDS=\"$wanted\"|" \
            "$TOOLKIT_CONFIG"
    else
        printf 'USB_WAKE_USB_IDS="%s"\n' "$wanted" >> "$TOOLKIT_CONFIG"
    fi
    echo "Found ${found[*]}. Added ${added[*]} to the radios allowed to" \
         "wake this machine."
}

if [[ "$ACTION" == "wake-ids" ]]; then
    add_bluetooth_wake_ids
    exit 0
fi
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
    echo "no $(basename "$INSTALLER") in $SOURCE - is that the vendored toolkit?" >&2
    exit 1
fi

# Who to install for. PKEXEC_UID is set by pkexec to the uid that asked, which
# is the desktop user this panel is running as - and it is the honest answer
# where an argument is one the caller chose. The argument is accepted so this
# can be run by hand and tested; it is only consulted when there is no
# PKEXEC_UID to prefer.
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
    # Half of what the toolkit installs is user systemd units and a
    # WirePlumber config in somebody's home. Installed into root's home they
    # would be in a session that never runs a television.
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

# Armed before anything writes the rule, and covering the ways out that are
# not a clean return. A copy left by a previous run that was killed outright
# is dealt with by the same trap rather than by a separate sweep here: the
# file is replaced, not appended to, so this run's own exit takes both away.
trap drop_the_rule EXIT INT TERM

# Written to a temporary file and checked before it is put in place. A
# malformed file in /etc/sudoers.d takes *sudo itself* down, not just this
# rule - which on a machine whose only other way in is this panel is a bad
# afternoon. visudo -c is the check sudo would do, done while it still costs
# nothing.
#
# The five programs are the ones the vendored installers actually call under
# sudo, and a test derives that list from the tree rather than trusting this
# comment. Three other sudo calls in there are not listed and do not need to
# be: two are the toolkit's own root helpers, which its *permanent* sudoers
# file already covers by the time they run, and the third is behind a --verify
# flag this script does not pass.
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

# What the installer needs to find, and nothing it does not. systemctl --user
# talks to a per-user bus, so without these two the user half of the install
# fails while the root half succeeds - which is the worst of the outcomes,
# because it looks installed.
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
    # Files for everything, switched on for nothing. The panel's page is eight
    # switches, and a feature that arrived already on is one nobody chose -
    # the volume integration is the only one the toolkit's installer turns on
    # by default, and set-external-volume writes its own files, so turning it
    # on later from the page is a complete path rather than a half one.
    flags=(--no-external-volume)
fi

echo "Running the CEC toolkit's own $ACTION as $TARGET."
runuser -u "$TARGET" -- env "${export_env[@]}" bash "$INSTALLER" "${flags[@]}"

if [[ "$ACTION" == "install" ]]; then
    # After the toolkit's own installer, which writes the config this edits -
    # and only writes it when it is not already there, so an id added here
    # survives being installed over the top.
    add_bluetooth_wake_ids || true
    echo
    echo "Installed. Nothing is switched on yet - the panel's HDMI CEC page"
    echo "has a switch for each feature."
else
    echo
    echo "Removed."
fi
