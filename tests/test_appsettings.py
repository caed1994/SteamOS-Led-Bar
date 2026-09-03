# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The panel's own preferences, in a home directory of its own."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "gui"))

import appsettings                                          # noqa: E402


class HomeTest(unittest.TestCase):

    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.home = holder.name

    def _text(self):
        with open(appsettings.path(self.home)) as handle:
            return handle.read()


class ReadWriteTest(HomeTest):

    def test_a_machine_that_has_never_set_one_gets_the_default(self):
        self.assertEqual(appsettings.read(self.home), dict(appsettings.DEFAULTS))

    def test_dark_is_what_it_ships_as(self):
        # The window is built for it: this is a panel for a strip of light and
        # its preview page is judged against the window round it.
        self.assertEqual(appsettings.DEFAULTS[appsettings.THEME],
                         appsettings.THEME_DARK)

    def test_what_was_written_is_what_comes_back(self):
        appsettings.write({appsettings.THEME: appsettings.THEME_LIGHT},
                          self.home)
        self.assertEqual(appsettings.read(self.home)[appsettings.THEME],
                         appsettings.THEME_LIGHT)

    def test_it_creates_the_directory_it_needs(self):
        self.assertFalse(os.path.exists(os.path.dirname(
            appsettings.path(self.home))))
        appsettings.write(dict(appsettings.DEFAULTS), self.home)
        self.assertTrue(os.path.exists(appsettings.path(self.home)))

    def test_the_file_says_what_it_is_for(self):
        # It is somebody's home directory, and a bare NAME=value file there
        # with no name on it is a file nobody can place.
        appsettings.write(dict(appsettings.DEFAULTS), self.home)
        self.assertIn("SteamOS Utility Center", self._text())

    def test_settings_from_before_the_rename_are_still_read(self):
        """The panel's preferences moved with the project's name.

        The installer moves the file. But the user opens the panel, and the
        user can open it without a second run of the installer. Without this
        code, the theme of the user and each other value come back as the
        default, and no message reports the file that the panel did not read.
        """
        os.makedirs(os.path.join(self.home, appsettings.CONFIG_DIR))
        with open(os.path.join(self.home, appsettings.CONFIG_DIR,
                               appsettings.OLD_CONFIG_FILE), "w") as handle:
            handle.write("%s=%s\n" % (appsettings.THEME,
                                      appsettings.THEME_LIGHT))
        self.assertEqual(appsettings.read(self.home)[appsettings.THEME],
                         appsettings.THEME_LIGHT)

    def test_the_current_name_wins_over_the_old_one(self):
        # Two files come from a machine that migrated and then opened an older
        # panel. A manual migration also gives two files. The panel writes the
        # current file, so the current file wins.
        os.makedirs(os.path.join(self.home, appsettings.CONFIG_DIR))
        with open(os.path.join(self.home, appsettings.CONFIG_DIR,
                               appsettings.OLD_CONFIG_FILE), "w") as handle:
            handle.write("%s=%s\n" % (appsettings.THEME,
                                      appsettings.THEME_LIGHT))
        appsettings.write({appsettings.THEME: appsettings.THEME_DARK},
                          self.home)
        self.assertEqual(appsettings.read(self.home)[appsettings.THEME],
                         appsettings.THEME_DARK)

    def test_it_is_written_in_one_step(self):
        appsettings.write(dict(appsettings.DEFAULTS), self.home)
        self.assertEqual(os.listdir(os.path.dirname(
            appsettings.path(self.home))), [appsettings.CONFIG_FILE])

    def test_a_value_this_version_has_never_heard_of_is_not_a_crash(self):
        """Somebody's future, or somebody's typo.

        Neither condition must stop the panel, so both give the default. The
        panel must not refuse to start because of a preference.
        """
        os.makedirs(os.path.dirname(appsettings.path(self.home)))
        with open(appsettings.path(self.home), "w") as handle:
            handle.write("THEME=solarized\n")
        self.assertEqual(appsettings.read(self.home)[appsettings.THEME],
                         appsettings.DEFAULTS[appsettings.THEME])

    def test_an_unreadable_file_is_not_a_crash_either(self):
        os.makedirs(os.path.dirname(appsettings.path(self.home)))
        os.mkdir(appsettings.path(self.home))       # a directory, not a file
        self.assertEqual(appsettings.read(self.home),
                         dict(appsettings.DEFAULTS))

    def test_comments_are_not_settings(self):
        os.makedirs(os.path.dirname(appsettings.path(self.home)))
        with open(appsettings.path(self.home), "w") as handle:
            handle.write("# THEME=light\n")
        self.assertEqual(appsettings.read(self.home)[appsettings.THEME],
                         appsettings.THEME_DARK)


class ValidateTest(unittest.TestCase):

    def test_every_choice_is_accepted(self):
        for theme in appsettings.THEMES:
            appsettings.validate({appsettings.THEME: theme})

    def test_anything_else_is_refused(self):
        with self.assertRaises(appsettings.SettingError):
            appsettings.validate({appsettings.THEME: "solarized"})

    def test_writing_goes_through_it(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        with self.assertRaises(appsettings.SettingError):
            appsettings.write({appsettings.THEME: "solarized"}, holder.name)


class ChoicesTest(unittest.TestCase):

    def test_there_are_three_and_following_the_desktop_is_one(self):
        """The third is the behaviour the dark window replaced.

        Keeping it means this setting adds a choice rather than taking one
        away from anybody who liked the window following their theme.
        """
        values = [value for _label, value in appsettings.theme_choices()]
        self.assertEqual(values, list(appsettings.THEMES))
        self.assertIn(appsettings.THEME_SYSTEM, values)

    def test_dark_leads(self):
        self.assertEqual(appsettings.theme_choices()[0][1],
                         appsettings.THEME_DARK)

    def test_every_one_of_them_has_a_label(self):
        for label, value in appsettings.theme_choices():
            self.assertTrue(label.strip(), value)
            self.assertNotEqual(label, value, "unlabelled: %s" % value)


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
