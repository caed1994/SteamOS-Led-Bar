# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The machine's own settings, which are not the LED service's.

Every test here works in a home directory of its own. Nothing in this file may
touch the real one: the whole point of the module is that it edits a file
somebody else's settings also live in, and a test that got that wrong would be
a test that deleted them.
"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "gui"))

import syssettings                                          # noqa: E402


class HomeTest(unittest.TestCase):
    """A home of its own, and the path of the file inside it."""

    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.home = holder.name
        self.path = syssettings.path_for(syssettings.LAYOUT, self.home)

    def _write_file(self, text):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as handle:
            handle.write(text)

    def _read_file(self):
        try:
            with open(self.path) as handle:
                return handle.read()
        except FileNotFoundError:
            return None


class ReadTest(HomeTest):

    def test_a_machine_that_has_never_set_one_reads_as_unset(self):
        # The ordinary state, and not a fault: there is nothing to repair in a
        # file that was never written.
        self.assertEqual(syssettings.read(self.home),
                         {syssettings.LAYOUT: syssettings.UNSET})

    def test_what_was_written_is_what_comes_back(self):
        syssettings.write({syssettings.LAYOUT: "de"}, self.home)
        self.assertEqual(syssettings.read(self.home)[syssettings.LAYOUT], "de")

    def test_it_goes_where_systemd_reads_it(self):
        """The one fact the whole feature rests on.

        systemd's user manager reads ~/.config/environment.d/*.conf at login
        and nothing anywhere else. A file written correctly into the wrong
        directory is a setting that silently never applies, and there is
        nothing on screen afterwards that would say so.
        """
        self.assertEqual(
            os.path.relpath(self.path, self.home),
            os.path.join(".config", "environment.d", "10-keyboard.conf"))

    def test_a_quoted_value_is_read_without_its_quotes(self):
        # Editing the file by hand is a supported way to use it, and quoting
        # is how half the internet writes an environment file.
        self._write_file('XKB_DEFAULT_LAYOUT="fr"\n')
        self.assertEqual(syssettings.read(self.home)[syssettings.LAYOUT], "fr")

    def test_the_last_one_wins_the_way_systemd_does(self):
        # Two lines naming the same variable: systemd's generator takes the
        # last. Reading the first would show a layout that is not in force.
        self._write_file("XKB_DEFAULT_LAYOUT=fr\nXKB_DEFAULT_LAYOUT=us\n")
        self.assertEqual(syssettings.read(self.home)[syssettings.LAYOUT], "us")

    def test_a_commented_line_is_not_a_setting(self):
        self._write_file("#XKB_DEFAULT_LAYOUT=fr\n")
        self.assertEqual(syssettings.read(self.home)[syssettings.LAYOUT],
                         syssettings.UNSET)

    def test_a_longer_name_that_starts_the_same_is_a_different_setting(self):
        # XKB_DEFAULT_LAYOUT is a prefix of nothing standard, but it is a
        # prefix, and matching on one would read somebody else's variable.
        self._write_file("XKB_DEFAULT_LAYOUT_EXTRA=fr\n")
        self.assertEqual(syssettings.read(self.home)[syssettings.LAYOUT],
                         syssettings.UNSET)


class WriteTest(HomeTest):

    def test_setting_one_creates_the_directory_it_needs(self):
        # A machine that has never had an environment.d has no such directory,
        # which is most machines.
        self.assertFalse(os.path.exists(os.path.dirname(self.path)))
        syssettings.write({syssettings.LAYOUT: "de"}, self.home)
        self.assertIn("XKB_DEFAULT_LAYOUT=de", self._read_file())

    def test_several_layouts_are_a_setting_and_not_a_mistake(self):
        # libxkbcommon takes a comma-separated list, which is how a keyboard
        # comes to have two layouts to switch between.
        syssettings.write({syssettings.LAYOUT: "de,us"}, self.home)
        self.assertEqual(syssettings.read(self.home)[syssettings.LAYOUT],
                         "de,us")

    def test_choosing_it_twice_does_not_write_it_twice(self):
        for _ in range(3):
            syssettings.write({syssettings.LAYOUT: "de"}, self.home)
        self.assertEqual(self._read_file().count("XKB_DEFAULT_LAYOUT="), 1)
        self.assertEqual(self._read_file().count(syssettings.MARK), 1)

    def test_changing_it_replaces_rather_than_appends(self):
        syssettings.write({syssettings.LAYOUT: "de"}, self.home)
        syssettings.write({syssettings.LAYOUT: "fr"}, self.home)
        self.assertEqual(self._read_file().count("XKB_DEFAULT_LAYOUT="), 1)
        self.assertEqual(syssettings.read(self.home)[syssettings.LAYOUT], "fr")

    def test_going_back_to_the_system_default_leaves_nothing_behind(self):
        """Not an empty value - no value, and no file either.

        XKB_DEFAULT_LAYOUT= is a layout that is empty, which libxkbcommon
        treats as a layout rather than as silence. Choosing "leave it to the
        system" has to leave the machine as it was before any of this.
        """
        syssettings.write({syssettings.LAYOUT: "de"}, self.home)
        syssettings.write({syssettings.LAYOUT: syssettings.UNSET}, self.home)
        self.assertIsNone(self._read_file(), "the file is still there")
        self.assertEqual(syssettings.read(self.home)[syssettings.LAYOUT],
                         syssettings.UNSET)

    def test_clearing_one_that_was_never_set_is_not_an_error(self):
        self.assertEqual(
            syssettings.write({syssettings.LAYOUT: syssettings.UNSET},
                              self.home), [])

    def test_it_says_which_settings_it_actually_wrote(self):
        self.assertEqual(syssettings.write({syssettings.LAYOUT: "de"},
                                           self.home), [syssettings.LAYOUT])


class OtherPeoplesLinesTest(HomeTest):
    """The failure this module exists to not have.

    environment.d is a shared directory and 10-keyboard.conf is a shared file:
    the number is low precisely so other things can be layered over it. Both
    the panel writing a setting and the panel taking one away have to leave
    every line that is not ours exactly as it was.
    """

    STRANGER = ("# my own note\n"
                "XKB_DEFAULT_MODEL=pc105\n"
                "XKB_DEFAULT_OPTIONS=grp:alt_shift_toggle\n"
                "SOMETHING_ELSE=entirely\n")

    def test_setting_ours_keeps_theirs(self):
        self._write_file(self.STRANGER)
        syssettings.write({syssettings.LAYOUT: "de"}, self.home)
        text = self._read_file()
        for line in self.STRANGER.splitlines():
            self.assertIn(line, text, line)

    def test_clearing_ours_keeps_theirs_and_the_file(self):
        # The dangerous direction: "leave it to the system" removes the file
        # when it is ours alone, and must not when it is not.
        self._write_file(self.STRANGER + "XKB_DEFAULT_LAYOUT=de\n")
        syssettings.write({syssettings.LAYOUT: syssettings.UNSET}, self.home)
        text = self._read_file()
        self.assertIsNotNone(text, "somebody else's settings were deleted")
        self.assertNotIn("XKB_DEFAULT_LAYOUT", text)
        for line in self.STRANGER.splitlines():
            self.assertIn(line, text, line)

    def test_a_hand_written_layout_is_replaced_not_duplicated(self):
        # Written without our mark above it, which is what a line somebody
        # added themselves looks like.
        self._write_file("XKB_DEFAULT_LAYOUT=fr\n")
        syssettings.write({syssettings.LAYOUT: "de"}, self.home)
        self.assertEqual(self._read_file().count("XKB_DEFAULT_LAYOUT="), 1)
        self.assertEqual(syssettings.read(self.home)[syssettings.LAYOUT], "de")

    def test_a_login_never_reads_half_a_file(self):
        """Replaced in one step, so the file is never briefly truncated.

        It is read at login by the user manager, and rewriting it in place
        would have a window in which it is empty. Checked by what is left
        beside it: a temporary file that was moved into place is gone, and one
        that was written in place would leave no temporary at all - so this
        also fails if the atomic write is dropped for a plain open().
        """
        self._write_file(self.STRANGER)
        before = set(os.listdir(os.path.dirname(self.path)))
        syssettings.write({syssettings.LAYOUT: "de"}, self.home)
        after = set(os.listdir(os.path.dirname(self.path)))
        self.assertEqual(after, before, "a temporary file was left behind")
        # And the inode changed, which a rewrite in place would not do.
        self.assertIn("XKB_DEFAULT_LAYOUT=de", self._read_file())


class ValidateTest(unittest.TestCase):
    """What must never reach a file that every login reads."""

    def test_a_plain_layout_is_fine(self):
        for good in ("de", "us", "de,us", "gb", "latam", "ru_1"):
            syssettings.validate({syssettings.LAYOUT: good})

    def test_unset_is_fine(self):
        syssettings.validate({syssettings.LAYOUT: syssettings.UNSET})

    def test_a_second_line_cannot_be_smuggled_in(self):
        """The one that matters.

        The value is written into a file read by the session's environment
        generator. A newline in it would not be a bad layout, it would be a
        second variable - set by us, on somebody's behalf, without either of
        us having said so.
        """
        with self.assertRaises(syssettings.SettingError):
            syssettings.validate({syssettings.LAYOUT: "de\nPATH=/tmp/evil"})

    def test_the_shapes_that_are_not_layouts_are_refused(self):
        for bad in ("de us", "DE", "de;rm -rf", "de,", ",de", "de/../us",
                    'de"', "de$(id)"):
            with self.assertRaises(syssettings.SettingError, msg=bad):
                syssettings.validate({syssettings.LAYOUT: bad})

    def test_writing_goes_through_it(self):
        # Not only the panel's own check: anything calling write() gets this,
        # because the file is the thing being protected rather than the GUI.
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        with self.assertRaises(syssettings.SettingError):
            syssettings.write({syssettings.LAYOUT: "de\nEVIL=1"}, holder.name)


class LayoutMenuTest(unittest.TestCase):
    """What the panel offers, and what it does when it cannot read the list."""

    def test_the_names_come_from_the_machine_when_it_has_them(self):
        found = syssettings._parse_rules([
            "! model\n", "  pc105          Generic 105-key PC\n",
            "! layout\n",
            "  de              German\n",
            "  us              English (US)\n",
            "\n",
            "! variant\n", "  nodeadkeys      de: Eliminate dead keys\n"])
        self.assertEqual(found, {"de": "German", "us": "English (US)"})

    def test_a_missing_rules_file_is_not_an_error(self):
        # A Wayland-only machine can have no xkb rules installed at all. The
        # codes alone still make a working menu.
        self.assertEqual(syssettings.layout_names(("/nonexistent/evdev.lst",)),
                         {})

    def test_leaving_it_alone_is_the_first_entry(self):
        # It is the default and it is what undoes everything below it, so it
        # is where a menu is already open at.
        offered = syssettings.layouts({})
        self.assertEqual(offered[0], (syssettings.UNSET_LABEL,
                                      syssettings.UNSET))

    def test_german_and_us_are_both_offered(self):
        # Named in the request this came from, and the two most likely wanted.
        values = [value for _label, value in syssettings.layouts()]
        self.assertIn("de", values)
        self.assertIn("us", values)

    def test_the_code_is_in_the_label(self):
        # It is what goes in the file, it is what people search the list for,
        # and it is the only part that is the same in every language.
        labels = dict((value, label)
                      for label, value in syssettings.layouts())
        self.assertIn("(de)", labels["de"])

    def test_a_layout_the_menu_does_not_list_can_still_be_offered(self):
        # The way out of a list that cannot be long: a value already in the
        # file keeps its place rather than being silently replaced.
        values = [value for _label, value in
                  syssettings.layouts(extra=("kz",))]
        self.assertIn("kz", values)
        self.assertEqual(len(values), len(set(values)), "duplicated an entry")

    def test_it_stays_short_enough_to_fit_on_a_steam_machine(self):
        """Measured, not guessed - and the reason this list is not all 99.

        The panel's drop-down does not scroll: it sizes itself to its entries
        and is clamped to the screen, so entries past the bottom edge cannot
        be clicked. At the theme's own row height twenty-eight entries came to
        926 pixels on a 1280x800 display, which is a Steam Machine's.

        Twenty is the ceiling this keeps to, which measured 662 - two thirds
        of that screen, leaving room for a desktop with a larger font.
        """
        self.assertLessEqual(len(syssettings.layouts()), 20)

    def test_every_one_it_offers_is_a_value_it_would_accept(self):
        for _label, value in syssettings.layouts():
            syssettings.validate({syssettings.LAYOUT: value})


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
