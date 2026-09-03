# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The drives that this machine mounts, and the files that carry them.

Nothing here mounts anything, and nothing here writes to /etc. The module
under test builds text and reads a record, and the step that needs root is
scripts/apply-mounts.sh. So each test runs on a build machine with one drive
and no second one.

The two tests that matter most are the unit name and the refusal. A unit whose
name is not the escaped mount point never mounts, and systemd reports nothing
about it. A unit on /usr replaces the system with somebody's games drive at
the next boot.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_utility_center import mounts            # noqa: E402


GAMES = {
    "uuid": "12345678-1234-1234-1234-123456789abc",
    "where": "/mnt/games",
    "type": "ext4",
}


class EscapeTest(unittest.TestCase):
    """The unit name, which systemd derives from the mount point itself.

    A mount unit only mounts the directory that its own name spells. Under any
    other name systemd loads the file, reports it as a unit, and never
    connects it to the directory. So this is held against systemd-escape where
    the machine has it, and against recorded answers where it does not.
    """

    RECORDED = (
        ("/mnt/games", "mnt-games.mount"),
        ("/mnt/my-games", "mnt-my\\x2dgames.mount"),
        ("/mnt/games/steam library", "mnt-games-steam\\x20library.mount"),
        ("/mnt/a.b", "mnt-a.b.mount"),
        ("/srv/x_y", "srv-x_y.mount"),
        ("/mnt/spiele", "mnt-spiele.mount"),
        ("/", "-.mount"),
    )

    def test_it_matches_the_recorded_answers(self):
        for where, wanted in self.RECORDED:
            self.assertEqual(mounts.escape(where), wanted, where)

    def test_it_matches_systemd_escape(self):
        """The authority, where the build machine has it.

        A hyphen is the case that a reading of the rules gets wrong. systemd
        separates the parts of the path with a hyphen, so a hyphen inside a
        part becomes \\x2d. Without that, /mnt/my-games and /mnt/my/games
        would be one unit name.
        """
        if not shutil.which("systemd-escape"):
            self.skipTest("no systemd-escape on this machine")
        for where, _wanted in self.RECORDED:
            done = subprocess.run(
                ["systemd-escape", "--path", "--suffix=mount", where],
                capture_output=True, text=True, check=True)
            self.assertEqual(mounts.escape(where), done.stdout.strip(), where)

    def test_a_trailing_slash_is_the_same_drive(self):
        self.assertEqual(mounts.escape("/mnt/games/"),
                         mounts.escape("/mnt/games"))


class RefusalTest(unittest.TestCase):
    """What must never reach a unit file.

    Each value here is written into a file that root reads at every boot. The
    mount point is the dangerous one: a unit on /usr or on / hands the system
    to a second drive, and the machine does not come back.
    """

    def _refused(self, **changes):
        entry = dict(GAMES)
        entry.update(changes)
        with self.assertRaises(mounts.MountError):
            mounts.validate(entry)

    def test_the_directories_of_steamos_are_refused(self):
        for where in ("/", "/usr", "/etc", "/var", "/home", "/boot"):
            self._refused(where=where)

    def test_a_relative_mount_point_is_refused(self):
        self._refused(where="mnt/games")

    def test_a_mount_point_that_climbs_out_is_refused(self):
        # /mnt/../usr is /usr, and the list above would not catch it.
        self._refused(where="/mnt/../usr")

    def test_a_device_that_is_not_a_uuid_is_refused(self):
        # The value goes into a path under /dev/disk/by-uuid. A value with a
        # slash in it is the path of somebody else.
        for uuid in ("", "/dev/sda2", "../../dev/sda2", "not-a-uuid"):
            self._refused(uuid=uuid)

    def test_a_short_vfat_uuid_is_accepted(self):
        entry = dict(GAMES, uuid="A1B2-C3D4", type="vfat")
        self.assertTrue(mounts.validate(entry))

    def test_a_filesystem_this_page_does_not_write_is_refused(self):
        self._refused(type="zfs")
        self._refused(type="")

    def test_an_option_with_a_new_line_is_refused(self):
        # A new line in the options is a second line in the unit file, and
        # that line is a setting nobody asked for.
        self._refused(options="defaults\nWhat=/dev/sda1")

    def test_the_ordinary_drive_is_accepted(self):
        self.assertTrue(mounts.validate(GAMES))


class UnitTest(unittest.TestCase):
    """The unit file itself."""

    def test_the_device_is_named_by_uuid(self):
        # Not /dev/sda2. The kernel gives out those names in the order it
        # finds the drives, so a second drive on a second port takes the name
        # of the first one. A UUID belongs to the filesystem.
        text = mounts.unit_text(GAMES)
        self.assertIn("What=/dev/disk/by-uuid/%s" % GAMES["uuid"], text)
        self.assertNotIn("/dev/sd", text)

    def test_it_is_wanted_and_not_required(self):
        """A drive that is not connected must not stop the boot.

        This is the `nofail` of fstab. A required unit that does not start
        takes its target with it, and the target here is multi-user.target.
        """
        text = mounts.unit_text(GAMES)
        self.assertIn("WantedBy=multi-user.target", text)
        self.assertNotIn("RequiredBy", text)

    def test_it_carries_a_device_timeout(self):
        self.assertIn("TimeoutSec=5s", mounts.unit_text(GAMES))

    def test_the_options_have_noatime_by_default(self):
        # A games drive writes an access time for each file that a game reads,
        # and no part of SteamOS reads that time back.
        self.assertIn("Options=defaults,noatime", mounts.unit_text(GAMES))

    def test_it_carries_the_mark_of_this_project(self):
        # The applier removes a unit of this project that nobody wants any
        # more. It must not remove the mount unit of another program.
        text = mounts.unit_text(GAMES)
        self.assertTrue(mounts.ours(text))
        self.assertFalse(mounts.ours("[Mount]\nWhere=/mnt/games\n"))

    def test_a_refused_drive_never_becomes_a_unit(self):
        with self.assertRaises(mounts.MountError):
            mounts.unit_text(dict(GAMES, where="/usr"))


class RecordTest(unittest.TestCase):
    """The record in /var, which is the half that an update cannot reach."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.path = os.path.join(self.root, "mounts.conf")

    def test_it_reads_back_what_it_wrote(self):
        with open(self.path, "w") as handle:
            handle.write(mounts.text([GAMES]))
        found = mounts.read(self.path)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["where"], "/mnt/games")
        self.assertEqual(found[0]["options"], mounts.DEFAULT_OPTIONS)

    def test_a_file_that_is_not_there_is_no_drives(self):
        self.assertEqual(mounts.read(self.path), [])

    def test_a_damaged_record_is_no_drives_and_not_an_error(self):
        """The page has to open on a machine with a damaged file.

        A record that raises is a page that cannot be drawn, and the page is
        where a person repairs the record.
        """
        with open(self.path, "w") as handle:
            handle.write("{ this is not json")
        self.assertEqual(mounts.read(self.path), [])

    def test_a_refused_drive_never_reaches_the_record(self):
        with self.assertRaises(mounts.MountError):
            mounts.text([dict(GAMES, where="/etc")])

    def test_two_drives_on_one_mount_point_are_reported(self):
        # Which one wins is the order systemd happens to take, so the page
        # refuses the pair rather than the second of them.
        pair = [GAMES, dict(GAMES, uuid="87654321-4321-4321-4321-cba987654321")]
        self.assertEqual(mounts.duplicates(pair), ["/mnt/games"])
        self.assertEqual(mounts.duplicates([GAMES]), [])


class KeepListTest(unittest.TestCase):
    """What asks SteamOS to carry this project into the new slot."""

    def test_the_mount_units_are_in_it(self):
        said = mounts.keep_list_text([GAMES])
        self.assertIn("/etc/systemd/system/mnt-games.mount", said)

    def test_the_enable_symlink_is_in_it_too(self):
        # A unit that survives without its symlink is a unit that systemd
        # loads and never starts, which is a drive that does not mount.
        said = mounts.keep_list_text([GAMES])
        self.assertIn(
            "/etc/systemd/system/multi-user.target.wants/mnt-games.mount",
            said)

    def test_the_keep_list_keeps_itself(self):
        # Or the next update takes away the file that protects everything
        # else, and nothing after that update is protected at all.
        self.assertIn(mounts.KEEP_LIST, mounts.keep_list_text([]))

    def test_fstab_is_never_in_it(self):
        """The one path that must not be preserved.

        /etc/fstab also holds the entries for /, /boot, /home and /var. A copy
        of it that survives an update writes the entries of the old image over
        the entries of the new one, and the machine that does not boot is a
        worse outcome than the games drive that does not mount.
        """
        said = mounts.keep_list_text([GAMES], extra=["/etc/passwd"])
        # The path lines only. The comment above them names fstab to say why
        # it is absent, and a test that searched the whole file would pass on
        # a file that both explains the rule and breaks it.
        paths = [line.strip() for line in said.splitlines()
                 if line.strip() and not line.startswith("#")]
        self.assertNotIn("/etc/fstab", paths)
        self.assertIn("/etc/passwd", paths)

    def test_the_files_of_this_project_go_in_beside_them(self):
        said = mounts.keep_list_text([], extra=["/etc/steamos-utility-center.conf"])
        self.assertIn("/etc/steamos-utility-center.conf", said)


class StaleTest(unittest.TestCase):
    """A unit that this project wrote and nobody wants any more."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.units = os.path.join(self.root, mounts.UNIT_DIR.lstrip("/"))
        os.makedirs(self.units)

    def _put(self, name, text):
        with open(os.path.join(self.units, name), "w") as handle:
            handle.write(text)

    def test_a_unit_of_ours_that_went_away_is_reported(self):
        self._put("mnt-old.mount", mounts.unit_text(
            dict(GAMES, where="/mnt/old")))
        self.assertEqual(mounts.stale_units([GAMES], root=self.root),
                         ["mnt-old.mount"])

    def test_a_unit_that_is_still_wanted_is_left(self):
        self._put("mnt-games.mount", mounts.unit_text(GAMES))
        self.assertEqual(mounts.stale_units([GAMES], root=self.root), [])

    def test_a_mount_unit_of_another_program_is_never_touched(self):
        """It carries no mark of this project, so it is not this project's.

        Somebody can have a mount unit of their own, written by hand or by
        another tool. A page that removes it takes a drive away from a person
        who never asked this project about it.
        """
        self._put("srv-backup.mount", "[Mount]\nWhere=/srv/backup\n")
        self.assertEqual(mounts.stale_units([], root=self.root), [])


class MissingTest(unittest.TestCase):
    """The question the repair unit asks at every boot.

    An update that did not honour the keep-list leaves the record in /var and
    no unit in /etc. The drive is then configured and not mounted, which is
    exactly the fault this whole page exists to prevent.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, mounts.UNIT_DIR.lstrip("/")))

    def test_a_drive_with_no_unit_is_reported(self):
        self.assertEqual(mounts.missing_units([GAMES], root=self.root),
                         [GAMES])

    def test_a_drive_with_its_unit_is_not(self):
        with open(mounts.unit_path("/mnt/games", self.root), "w") as handle:
            handle.write(mounts.unit_text(GAMES))
        self.assertEqual(mounts.missing_units([GAMES], root=self.root), [])


class PartitionTest(unittest.TestCase):
    """The drives that the page offers, read off the machine.

    Read from lsblk rather than typed, for the reason the sensor menu and the
    governor menu are read from the machine: a UUID is not something to ask a
    person to copy by hand.
    """

    LSBLK = json.dumps({"blockdevices": [
        {"name": "/dev/sda", "uuid": None, "fstype": None, "size": 0,
         "label": None, "mountpoint": None, "children": [
             {"name": "/dev/sda1", "uuid": "AAAA-BBBB", "fstype": "vfat",
              "size": 536870912, "label": "EFI", "mountpoint": "/boot"},
             {"name": "/dev/sda2",
              "uuid": "12345678-1234-1234-1234-123456789abc",
              "fstype": "ext4", "size": 2000398934016, "label": "games",
              "mountpoint": None},
         ]},
    ]})

    def _found(self, said=None):
        return mounts.partitions(run=lambda _command: (
            self.LSBLK if said is None else said))

    def test_a_partition_with_a_filesystem_is_offered(self):
        found = self._found()
        self.assertEqual([one["uuid"] for one in found],
                         ["12345678-1234-1234-1234-123456789abc"])
        self.assertEqual(found[0]["label"], "games")

    def test_the_partition_steamos_boots_from_is_not(self):
        # It is mounted at /boot, which the page refuses anyway. Offering it
        # is offering a person the one drive they must not touch.
        self.assertNotIn("AAAA-BBBB", [one["uuid"] for one in self._found()])

    def test_a_machine_where_lsblk_says_nothing_offers_nothing(self):
        # A build machine, a container, or lsblk that is not installed. The
        # page has to open there and say it found no drives.
        self.assertEqual(self._found(""), [])
        self.assertEqual(self._found("not json"), [])

    def test_the_line_in_the_menu_names_the_drive(self):
        said = mounts.partition_said(self._found()[0])
        self.assertIn("/dev/sda2", said)
        self.assertIn("games", said)
        self.assertIn("ext4", said)
        # 1.8T, and not "2T". A whole number reads 1.8 and 2.4 the same way,
        # and a menu of drives is where two sizes must stay apart.
        self.assertIn("1.8T", said)


class OwnerTest(unittest.TestCase):
    """Which filesystems take an owner, and which take a mount option.

    ext4 records a user id for each file, so one chown settles it. exfat and
    vfat record none, and every file belongs to whoever the mount options say.
    A chown on one of those fails, and a page that offered it would offer a
    button that cannot work.
    """

    def test_ext4_is_owned_by_a_chown(self):
        self.assertFalse(mounts.needs_owner_option("ext4"))
        self.assertFalse(mounts.needs_owner_option("btrfs"))

    def test_exfat_is_owned_by_the_mount_options(self):
        for kind in ("exfat", "ntfs3", "vfat"):
            self.assertTrue(mounts.needs_owner_option(kind), kind)

    def test_the_option_names_the_user_by_number(self):
        # A name is resolved in the session, and the mount happens at boot
        # before any session. The number is what the kernel takes.
        self.assertEqual(mounts.owner_options(1000, 1000), "uid=1000,gid=1000")


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()


class WriteTest(unittest.TestCase):
    """Writing the units, against a directory built here.

    Never against /etc. The root of every path is a parameter for the reason
    the power tests take one: a test that wrote the real /etc would change how
    the build machine boots.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.units = os.path.join(self.root, mounts.UNIT_DIR.lstrip("/"))

    def _write(self, entries, **kwargs):
        return mounts.write_units(entries, root=self.root, **kwargs)

    def test_it_writes_the_unit_under_the_escaped_name(self):
        done = self._write([GAMES])
        path = os.path.join(self.units, "mnt-games.mount")
        self.assertIn(path, done["written"])
        with open(path) as handle:
            self.assertIn("Where=/mnt/games", handle.read())

    def test_it_makes_the_mount_point(self):
        # systemd makes it too, but only at the moment it mounts. A directory
        # that is there now lets the panel give the drive away before the
        # drive is connected.
        self._write([GAMES])
        self.assertTrue(os.path.isdir(os.path.join(self.root, "mnt", "games")))

    def test_it_writes_the_keep_list(self):
        done = self._write([GAMES])
        self.assertIn(self.root + mounts.KEEP_LIST, done["written"])

    def test_the_keep_list_covers_the_files_of_this_project(self):
        """The gap that ate the fstab line of a person, in our own files.

        This project wrote nothing into /etc/atomic-update.conf.d, so its
        configuration, its units and its udev rule had the same exposure as
        that line. The CEC toolkit has had a keep-list since v0.1.15.
        """
        self._write([GAMES])
        with open(self.root + mounts.KEEP_LIST) as handle:
            said = handle.read()
        self.assertIn("/etc/steamos-utility-center.conf", said)
        self.assertIn("/etc/systemd/system/steamos-utility-center.service",
                      said)
        self.assertIn("/etc/udev/rules.d/99-steamos-utility-center.rules",
                      said)

    def test_a_drive_that_went_away_takes_its_unit_with_it(self):
        self._write([GAMES])
        done = self._write([])
        self.assertIn(os.path.join(self.units, "mnt-games.mount"),
                      done["removed"])
        self.assertFalse(os.path.exists(
            os.path.join(self.units, "mnt-games.mount")))

    def test_its_enable_symlink_goes_too(self):
        # A symlink to a unit file that is gone makes systemd report a failure
        # at every boot, for a drive nobody wants any more.
        self._write([GAMES])
        wants = os.path.join(self.units, "multi-user.target.wants")
        os.makedirs(wants)
        link = os.path.join(wants, "mnt-games.mount")
        os.symlink(os.path.join(self.units, "mnt-games.mount"), link)
        done = self._write([])
        self.assertIn(link, done["removed"])
        self.assertFalse(os.path.lexists(link))

    def test_two_drives_on_one_mount_point_are_refused(self):
        pair = [GAMES, dict(GAMES, uuid="87654321-4321-4321-4321-cba987654321")]
        with self.assertRaises(mounts.MountError):
            self._write(pair)

    def test_a_refused_drive_leaves_the_old_units_alone(self):
        """A record this refuses must not be a machine with no drives.

        The applier calls this before it replaces anything, so a rejection
        keeps whatever was working. See scripts/apply-mounts.sh.
        """
        self._write([GAMES])
        with self.assertRaises(mounts.MountError):
            self._write([dict(GAMES, where="/usr")])
        self.assertTrue(os.path.exists(
            os.path.join(self.units, "mnt-games.mount")))


class ApplierTest(unittest.TestCase):
    """scripts/apply-mounts.sh, read rather than run.

    It calls systemctl and steamos-readonly, so a build machine cannot run it.
    What can be checked is the order, and the order is where this kind of
    script goes wrong. The uninstaller of this project had exactly that fault
    once: a step in the wrong place ended the run three steps in.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "..", "scripts",
                               "apply-mounts.sh")) as handle:
            cls.text = handle.read()

    def _at(self, needle):
        where = self.text.find(needle)
        self.assertNotEqual(where, -1, "not in apply-mounts.sh: %s" % needle)
        return where

    def test_it_never_writes_fstab(self):
        """The one file this must not touch.

        /etc/fstab also holds the entries for /, /boot, /home and /var. This
        page writes a mount unit instead, which is a file of this project.
        """
        for line in self.text.splitlines():
            if line.strip().startswith("#"):
                continue
            self.assertNotIn("/etc/fstab", line)

    def test_a_removed_drive_is_unmounted_before_the_reload(self):
        """systemd forgets a unit whose file is gone at the next reload.

        Its file is off disk by then, so `stop` after the reload finds no such
        unit and the drive stays mounted until the machine restarts.
        """
        self.assertLess(self._at("systemctl stop"),
                        self._at("systemctl daemon-reload"))

    def test_the_record_is_replaced_only_after_the_units_are_accepted(self):
        # A record that this refuses must leave the machine as it was.
        self.assertLess(self._at("--write-mounts"), self._at('install -m 0644'))

    def test_it_gives_the_directory_away_only_when_it_is_mounted(self):
        """Or the chown walks the empty directory under the mount point.

        That reports success and changes nothing a person can see, which is
        the worst of the three answers.
        """
        self.assertIn("mountpoint -q", self.text)
        self.assertLess(self._at("mountpoint -q"), self._at("chown -R"))

    def test_it_puts_the_rootfs_back(self):
        # SteamOS keeps / read-only and a unit file goes into /etc. A script
        # that unlocks and stops leaves the machine open.
        self.assertIn("trap relock_rootfs EXIT", self.text)

    def test_it_only_touches_the_mount_units_of_this_project(self):
        # Somebody can have a mount unit of their own. A script that enables
        # every .mount in /etc takes over a drive nobody asked about.
        self.assertIn('grep -q "$MARK"', self.text)


class RepairUnitTest(unittest.TestCase):
    """The unit that writes the mount units again at every boot.

    The keep-list is the official way and works on an image that honours it.
    This unit is what makes the drives survive an image that does not: the
    record is in /var, which is its own partition.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "..", "server",
                               "steamos-utility-center-mounts.service")) as f:
            cls.text = f.read()

    def test_it_runs_the_same_applier_the_panel_runs(self):
        # And holds no logic of its own. Two copies of the rules are two
        # answers to one question.
        self.assertIn("steamos-utility-center-mounts-apply", self.text)

    def test_it_does_nothing_on_a_machine_with_no_drives(self):
        self.assertIn("ConditionPathExists=%s" % mounts.STATE_PATH, self.text)

    def test_it_is_a_oneshot_that_stays(self):
        # Type=oneshot, or WantedBy= starts it and systemd reports it failed
        # the moment it exits.
        self.assertIn("Type=oneshot", self.text)
        self.assertIn("RemainAfterExit=yes", self.text)

    def test_it_runs_after_the_local_filesystems(self):
        # It reads /var and writes /etc.
        self.assertIn("After=local-fs.target", self.text)

    def test_it_is_wanted_by_the_ordinary_target(self):
        self.assertIn("WantedBy=multi-user.target", self.text)
