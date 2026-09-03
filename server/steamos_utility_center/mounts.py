# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The drives that this machine mounts, and how they survive a SteamOS update.

A SteamOS update writes the new image into the other partition slot and boots
into it. /etc belongs to that image, so a line that a person adds to
/etc/fstab is in the old slot only. The new slot has the fstab of the image,
and the line is gone. /home and /var are their own partitions and stay.

So this project does not write /etc/fstab. It writes one systemd mount unit
for each drive. systemd builds the same units from fstab, so this is the same
mechanism one level down. The difference is important: fstab also holds the
entries for /, /boot, /home and /var, and a copy of it that survives an update
writes those entries over the entries of the new image. A mount unit is a file
of this project alone, and it is safe to keep.

Three things carry a drive across an update:

- /var/lib/steamos-utility-center/mounts.conf is the record of what a person
  asked for. /var is its own partition, so this file stays.
- /etc/atomic-update.conf.d/steamos-utility-center.conf asks SteamOS to keep
  the units. See keep_list_text.
- steamos-utility-center-mounts.service writes the units again at each boot
  from the record above. This covers an image that does not honour the
  keep-list.

Nothing here writes to /etc. To write a unit needs root, and that work is in
scripts/apply-mounts.sh. This module is the half that a test can read.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

# The record of what a person asked for, on the partition that an update keeps.
STATE_DIR = "/var/lib/steamos-utility-center"
STATE_PATH = os.path.join(STATE_DIR, "mounts.conf")

# Where a mount unit goes, and what asks SteamOS to keep it.
UNIT_DIR = "/etc/systemd/system"
KEEP_LIST = "/etc/atomic-update.conf.d/steamos-utility-center.conf"

# The target that a mount unit is wanted by.
#
# Wanted by, and not required by. A required unit that does not start takes the
# target with it, and the target is the boot. A drive that is not connected
# must not stop the machine. This is the `nofail` of fstab, and it is the
# default here because a second drive is never the drive that boots.
WANTED_BY = "multi-user.target"

# The mark of a unit that this project wrote.
#
# An update, or a person, can leave a mount unit behind. The applier removes a
# unit that this project wrote and no longer wants, and it must not remove a
# unit that another program wrote. A line in the file is the evidence.
MARK = "# written by the SteamOS Utility Center"

# The filesystems that this page offers.
#
# Each one of them is a filesystem that the kernel of SteamOS mounts and that a
# second drive carries. exfat and ntfs3 have no permissions of their own, so
# they take the owner from the mount options. See needs_owner_option.
TYPES = ("ext4", "btrfs", "xfs", "f2fs", "exfat", "ntfs3", "vfat")

# The filesystems that carry no owner of their own.
#
# ext4 and its family record a user id for each file, so a person owns a
# directory after one chown. exfat, ntfs3 and vfat record none, and the mount
# options give the owner of every file. A chown on one of those fails, and the
# page offers the option instead.
NO_OWNER = ("exfat", "ntfs3", "vfat")

# What a drive gets when a person names no option.
#
# noatime is here because a games drive writes an access time for each file
# that a game reads, and no part of SteamOS reads that time.
DEFAULT_OPTIONS = "defaults,noatime"

# How long systemd waits for the device before it gives up on the drive.
DEFAULT_TIMEOUT = "5s"

# A mount point that this project refuses.
#
# Each of these is a directory that SteamOS or the boot needs. A mount unit on
# one of them takes the machine, and no games drive is worth that. / is in the
# list twice over: it is a prefix of every path, so the test below is not a
# prefix test.
REFUSED = ("/", "/boot", "/efi", "/etc", "/usr", "/var", "/home", "/proc",
           "/sys", "/dev", "/run", "/tmp", "/bin", "/sbin", "/lib", "/lib64",
           "/opt", "/root", "/srv")

# A UUID as blkid writes it, and as /dev/disk/by-uuid carries it.
#
# vfat has a short one of eight hexadecimal digits with a hyphen in the middle,
# and each other filesystem here has the long form. Both are accepted, and
# nothing else is: the value goes into a path, and a value with a slash in it
# is a path of somebody else.
UUID = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}"
                  r"-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$|^[0-9A-Fa-f]{4}-"
                  r"[0-9A-Fa-f]{4}$")

# The characters that a mount option can hold.
#
# The value goes into a unit file, on a line of its own. A new line in it is a
# second option that nobody asked for, and a value with one is refused.
OPTIONS = re.compile(r"^[A-Za-z0-9_,=.:/+-]*$")


class MountError(ValueError):
    """A drive that must not be written. The panel catches it by name."""


def escape(where):
    """Returns the unit name of a mount point, as systemd escapes it.

    systemd names a mount unit after its own mount point: /mnt/games becomes
    mnt-games.mount. A unit with another name is a unit that systemd does not
    connect to the directory, and it never mounts anything.

    The rules are those of systemd-escape --path. The leading slash goes, each
    remaining slash becomes a hyphen, and a character that is not a letter, a
    digit, a colon or an underscore becomes \\x plus two hexadecimal digits.
    A hyphen in the path is such a character, or a directory called a-b and the
    path a/b would give one unit name.
    """
    path = "/" + where.strip("/")
    if path == "/":
        return "-.mount"
    out = []
    for step, part in enumerate(path.strip("/").split("/")):
        if step:
            out.append("-")
        for number, letter in enumerate(part):
            if letter.isascii() and (letter.isalnum() or letter in ":_"):
                out.append(letter)
            elif letter == "." and (number or step):
                out.append(letter)
            else:
                out.append("\\x%02x" % ord(letter))
    return "".join(out) + ".mount"


def unit_path(where, root=""):
    """Returns the path of the mount unit of one drive."""
    return os.path.join(root + UNIT_DIR, escape(where))


def needs_owner_option(kind):
    """Returns whether this filesystem takes its owner from the options."""
    return kind in NO_OWNER


def owner_options(uid, gid):
    """Returns the options that give a filesystem with no owner to one user."""
    return "uid=%d,gid=%d" % (int(uid), int(gid))


def validate(entry):
    """Raises MountError when one drive must not be written.

    Each value here goes into a unit file that root reads, so each value is
    examined. The mount point is the value that needs the most care: a unit on
    /usr or on / replaces the system with a second drive at the next boot.
    """
    where = str(entry.get("where", "")).rstrip("/") or "/"
    if not where.startswith("/"):
        raise MountError("the mount point must start with a slash: %s"
                         % entry.get("where", ""))
    if ".." in where.split("/"):
        raise MountError("the mount point must not hold .. : %s" % where)
    if where in REFUSED:
        raise MountError("%s belongs to SteamOS, so this refuses to mount "
                         "over it" % where)
    if "\n" in where or "\\" in where:
        raise MountError("the mount point holds a character that a unit file "
                         "cannot carry")

    uuid = str(entry.get("uuid", ""))
    if not UUID.match(uuid):
        raise MountError("%s is not a UUID. The page reads one off the drive."
                         % (uuid or "an empty value"))

    kind = str(entry.get("type", ""))
    if kind not in TYPES:
        raise MountError("%s is not a filesystem that this page writes"
                         % (kind or "an empty value"))

    options = str(entry.get("options", ""))
    if not OPTIONS.match(options):
        raise MountError("the mount options hold a character that a unit file "
                         "cannot carry: %s" % options)
    return True


def unit_text(entry):
    """Returns the mount unit of one drive.

    The device is named by UUID under /dev/disk/by-uuid, and not by /dev/sda2.
    The kernel gives out the sd names in the order that it finds the drives, so
    a second drive on a second port takes the name of the first one. A UUID
    belongs to the filesystem.
    """
    validate(entry)
    where = str(entry["where"]).rstrip("/")
    options = str(entry.get("options", "")) or DEFAULT_OPTIONS
    timeout = str(entry.get("timeout", "")) or DEFAULT_TIMEOUT
    return "\n".join((
        MARK,
        "# The panel of this project writes this file. Its record is",
        "# %s." % STATE_PATH,
        "",
        "[Unit]",
        "Description=%s, mounted by the SteamOS Utility Center" % where,
        "",
        "[Mount]",
        "What=/dev/disk/by-uuid/%s" % entry["uuid"],
        "Where=%s" % where,
        "Type=%s" % entry["type"],
        "Options=%s" % options,
        "TimeoutSec=%s" % timeout,
        "",
        "[Install]",
        "WantedBy=%s" % WANTED_BY,
        "",
    ))


def ours(text):
    """Returns whether this project wrote a unit, from the text of the file."""
    return MARK in text


def read(path=None):
    """Returns the drives of the record, or an empty list.

    The record is JSON of one object for each drive. A file that this cannot
    read is an empty list and not an error: the page must open on a machine
    with a damaged record, or a person cannot repair it.
    """
    try:
        with open(path or STATE_PATH, encoding="utf-8") as handle:
            found = json.load(handle)
    except (OSError, ValueError):
        return []
    if not isinstance(found, list):
        return []
    return [entry for entry in found if isinstance(entry, dict)]


def text(entries):
    """Returns the record of these drives, ready to write."""
    out = []
    for entry in entries:
        validate(entry)
        out.append({
            "uuid": str(entry["uuid"]),
            "where": str(entry["where"]).rstrip("/"),
            "type": str(entry["type"]),
            "options": str(entry.get("options", "")) or DEFAULT_OPTIONS,
            "timeout": str(entry.get("timeout", "")) or DEFAULT_TIMEOUT,
        })
    return json.dumps(out, indent=2, sort_keys=True) + "\n"


def duplicates(entries):
    """Returns the mount points that more than one drive asks for.

    Two units on one mount point is one drive over the other, and which one
    wins is the order that systemd happens to take. The page refuses the pair
    rather than the second of them, because neither is more correct.
    """
    seen, twice = set(), []
    for entry in entries:
        where = str(entry.get("where", "")).rstrip("/")
        if where in seen and where not in twice:
            twice.append(where)
        seen.add(where)
    return twice


def _run(command):
    """Returns the output of a command, or "" when it does not answer."""
    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def partitions(run=_run):
    """Returns the partitions of this machine, as the page offers them.

    Read from lsblk and not from a list, for the reason that the sensor menu
    and the governor menu are read from the machine: the drives of a machine
    are the answer of that machine.

    A partition with no UUID carries no filesystem that this can mount, and it
    is left out. The partition that holds / is left out for the same reason:
    SteamOS mounts it, and a second unit on it is the refusal above.
    """
    said = run(["lsblk", "--json", "--bytes", "--paths",
                "--output", "NAME,UUID,FSTYPE,SIZE,LABEL,MOUNTPOINT"])
    try:
        tree = json.loads(said or "{}")
    except ValueError:
        return []
    out = []

    def walk(nodes):
        for node in nodes:
            walk(node.get("children") or [])
            uuid = node.get("uuid") or ""
            kind = node.get("fstype") or ""
            if not uuid or not UUID.match(uuid):
                continue
            if (node.get("mountpoint") or "") in REFUSED:
                continue
            out.append({
                "uuid": uuid,
                "type": kind,
                "device": node.get("name") or "",
                "label": node.get("label") or "",
                "size": node.get("size") or 0,
                "mountpoint": node.get("mountpoint") or "",
            })

    walk(tree.get("blockdevices") or [])
    return out


def size_said(size):
    """Returns a size in bytes as a short line, for a menu.

    One decimal below ten, and none above it. A whole number alone reads 1.8 T
    and 2.4 T both as "2T", and a menu of drives is the one place where two
    sizes must not look the same.
    """
    try:
        left = float(size)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "K", "M", "G", "T"):
        if left < 1024 or unit == "T":
            if unit == "B" or left >= 10:
                return "%.0f%s" % (left, unit)
            return "%.1f%s" % (left, unit)
        left /= 1024.0
    return ""                                           # pragma: no cover


def partition_said(found):
    """Returns one line for a partition, for the menu of the page."""
    parts = [found.get("device") or found.get("uuid", "")]
    if found.get("label"):
        parts.append(found["label"])
    if found.get("type"):
        parts.append(found["type"])
    said = size_said(found.get("size", 0))
    if said:
        parts.append(said)
    return "  ".join(part for part in parts if part)


def keep_list_text(entries, extra=()):
    """Returns the file that asks SteamOS to keep this project across a update.

    A SteamOS update rebuilds /etc from the new image. The paths in
    /etc/atomic-update.conf.d/*.conf are the paths that holo-sync-var carries
    into the new slot. The CEC toolkit writes such a file, and this project
    wrote none: its own configuration, its two units and its udev rule had no
    protection at all.

    /etc/fstab is not here and must never be. It also holds the entries for /,
    /boot, /home and /var. A copy of it that survives an update writes the
    entries of the old image over the entries of the new one.
    """
    lines = [
        "# Keep the files of the SteamOS Utility Center across a SteamOS "
        "update.",
        "#",
        "# A SteamOS update rebuilds /etc from the new image. holo-sync-var "
        "carries",
        "# the paths below into the new slot. See "
        "server/steamos_utility_center/mounts.py.",
        "#",
        "# /etc/fstab is deliberately not here. It also holds the entries for "
        "/,",
        "# /boot, /home and /var, and a copy of it that survives an update "
        "writes",
        "# those entries over the entries of the new image.",
        "",
        KEEP_LIST,
        "",
    ]
    lines.extend(extra)
    if extra:
        lines.append("")
    if entries:
        lines.append("# The drives of the System page.")
        for entry in sorted(entries, key=lambda one: str(one.get("where"))):
            lines.append(os.path.join(UNIT_DIR, escape(entry["where"])))
            lines.append(os.path.join(
                UNIT_DIR, "%s.wants" % WANTED_BY, escape(entry["where"])))
        lines.append("")
    return "\n".join(lines)


def stale_units(entries, root="", listing=None):
    """Returns the mount units that this project wrote and no longer wants.

    A drive that a person removes from the page leaves its unit on disk, and a
    unit that stays mounts the drive at the next boot. So the applier removes
    it. It removes a unit with the mark of this project only, because a mount
    unit that another program wrote is not this project's to take away.
    """
    wanted = {escape(entry["where"]) for entry in entries
              if entry.get("where")}
    directory = root + UNIT_DIR
    try:
        names = listing if listing is not None else os.listdir(directory)
    except OSError:
        return []
    out = []
    for name in sorted(names):
        if not name.endswith(".mount") or name in wanted:
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8",
                      errors="replace") as handle:
                if ours(handle.read()):
                    out.append(name)
        except OSError:                                 # pragma: no cover
            continue
    return out


def missing_units(entries, root=""):
    """Returns the drives whose unit is not on disk.

    This is the question that the repair unit asks at each boot, and that the
    status page asks. An update that did not honour the keep-list leaves the
    record in /var and no unit in /etc, and the drive is then configured and
    not mounted.
    """
    return [entry for entry in entries
            if entry.get("where")
            and not os.path.exists(unit_path(entry["where"], root))]


# The files that this project already writes into /etc, and that nothing
# protected until the keep-list existed.
#
# The suspend hook is not here. It is in /usr/lib/systemd/system-sleep, and the
# keep-list covers /etc. A SteamOS update thus still takes that one file, and
# the installer writes it again.
PROJECT_FILES = (
    "/etc/steamos-utility-center.conf",
    "/etc/steamos-utility-center-power.conf",
    "/etc/systemd/system/steamos-utility-center.service",
    "/etc/systemd/system/steamos-utility-center-power.service",
    "/etc/systemd/system/multi-user.target.wants/steamos-utility-center.service",
    "/etc/systemd/system/multi-user.target.wants/"
    "steamos-utility-center-power.service",
    "/etc/systemd/system/steamos-utility-center-mounts.service",
    "/etc/systemd/system/multi-user.target.wants/"
    "steamos-utility-center-mounts.service",
    "/etc/udev/rules.d/99-steamos-utility-center.rules",
    # The rule that lets the control command apply a change with no password.
    # Without it in this list, a SteamOS update leaves a machine on which the
    # panel operates and Game Mode does not. See ctl.sudoers_text.
    "/etc/sudoers.d/zz-steamos-utility-center",
)


def write_units(entries, root="", keep=True):
    """Writes the mount unit of each drive, and returns what it wrote.

    It writes and removes files, and it runs no systemctl. The caller does
    that, because a test must be able to call this without a systemd. See
    scripts/apply-mounts.sh.

    A unit that this project wrote, for a drive that nobody wants any more, is
    removed here. A mount unit of another program is left alone. See
    stale_units.
    """
    for entry in entries:
        validate(entry)
    twice = duplicates(entries)
    if twice:
        raise MountError("two drives ask for %s. One mount point takes one "
                         "drive." % ", ".join(twice))

    directory = root + UNIT_DIR
    os.makedirs(directory, exist_ok=True)
    written, removed = [], []
    for entry in entries:
        path = unit_path(entry["where"], root)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(unit_text(entry))
        os.chmod(path, 0o644)
        written.append(path)
        # The mount point itself. systemd makes it, but only at the moment it
        # mounts. A directory that is there now also lets the panel give the
        # drive to a person before it is connected.
        os.makedirs(root + entry["where"], exist_ok=True)

    for name in stale_units(entries, root):
        os.unlink(os.path.join(directory, name))
        removed.append(os.path.join(directory, name))
        # And the symlink that enables it, or systemd reports a unit file that
        # is not there at every boot.
        link = os.path.join(directory, "%s.wants" % WANTED_BY, name)
        if os.path.islink(link) or os.path.exists(link):
            os.unlink(link)
            removed.append(link)

    if keep:
        path = root + KEEP_LIST
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(keep_list_text(entries, extra=PROJECT_FILES))
        os.chmod(path, 0o644)
        written.append(path)
    return {"written": written, "removed": removed}
