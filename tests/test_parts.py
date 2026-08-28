# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Every part of the toolbox, described the same way.

The status page used to be the LED checklist and nothing else, on a page
headed "this installation" - while the toolbox had grown to install four
things. The other three said their piece on their own settings pages, in
their own shapes, and nowhere together.

What is checked here is that shape: three states rather than two, "not
installed" telling itself apart from "broken", and a summary that counts
across all of them instead of over one.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, os.path.join(HERE, "..", "gui"))

import ledpanel                                             # noqa: E402
from steamos_led import cec                                 # noqa: E402
from test_gui import healthy                                # noqa: E402


def cec_status(**changes):
    found = {
        "config": {"CEC_DEVICE": "/dev/cec0"},
        "cec_device": {"device": "/dev/cec0", "exists": True,
                       "readable": True, "writable": True},
        "external_volume": {"enabled": False},
        "services": {}, "system_services": {},
    }
    found.update(changes)
    return found


class ThreeStatesTest(unittest.TestCase):
    """The distinction the old page could not make."""

    def test_a_part_nobody_installed_is_not_a_part_that_is_broken(self):
        """And this is the whole reason ok has three values.

        A machine that never wanted HDMI CEC is not a machine with a problem.
        Counted as one, the window would carry a permanent red number over
        every page telling somebody to fix something they chose not to have.
        """
        for part in (ledpanel.cec_part(None, installed=False),
                     ledpanel.power_part({"CPU_GOVERNOR": ""}, {}),
                     ledpanel.layout_part("")):
            self.assertIsNone(part.ok, part.key)
            self.assertFalse(part.installed, part.key)

    def test_an_uninstalled_part_still_says_what_it_would_be(self):
        # A blank block reads as one that failed to draw.
        for part in (ledpanel.cec_part(None, installed=False),
                     ledpanel.power_part({}, {}),
                     ledpanel.layout_part("")):
            self.assertTrue(part.verdict.strip(), part.key)
            self.assertTrue(part.name.strip(), part.key)

    def test_every_part_has_a_key_of_its_own(self):
        parts = [ledpanel.led_part(ledpanel.run_checks(probe=healthy())),
                 ledpanel.power_part({}, {}),
                 ledpanel.cec_part(None, False),
                 ledpanel.layout_part(""),
                 ledpanel.panel_part("1.0.0")]
        keys = [part.key for part in parts]
        self.assertEqual(len(keys), len(set(keys)), keys)

    def test_a_repair_names_one_the_window_can_label(self):
        # A part that gains a repair must not gain a button with no text on
        # it, and the two tables live in different files.
        parts = [ledpanel.led_part(ledpanel.run_checks(probe=healthy())),
                 ledpanel.cec_part(cec_status(cec_device={
                     "device": "/dev/cec0", "exists": True,
                     "readable": True, "writable": False}), True)]
        for part in parts:
            if part.repair:
                self.assertIn(part.repair, ledpanel.REPAIR_LABELS, part.key)


class LedPartTest(unittest.TestCase):

    def test_a_whole_installation_says_so(self):
        part = ledpanel.led_part(ledpanel.run_checks(probe=healthy()))
        self.assertTrue(part.ok)
        self.assertIn("running", part.verdict.lower())

    def test_the_kernel_module_gets_its_own_sentence(self):
        """The one a SteamOS update reliably causes, and the one with a fix.

        Worth saying instead of a count, because "1 of 11 checks failed" tells
        somebody to go looking and this tells them what happened.
        """
        probe = healthy()
        probe.present = set(probe.present) - {ledpanel.module_path(
            probe.release)}
        part = ledpanel.led_part(ledpanel.run_checks(probe=probe))
        self.assertFalse(part.ok)
        self.assertIn("kernel module", part.verdict.lower())
        self.assertIn("reinstalling", part.verdict.lower())

    def test_anything_else_is_counted(self):
        probe = healthy()
        probe.active = set()
        part = ledpanel.led_part(ledpanel.run_checks(probe=probe))
        self.assertFalse(part.ok)
        self.assertIn("failed", part.verdict)

    def test_the_checks_are_carried_along_as_its_detail(self):
        # The block unfolds to the list this page used to be.
        checks = ledpanel.run_checks(probe=healthy())
        self.assertEqual(ledpanel.led_part(checks).detail, checks)

    def test_it_offers_the_reinstall(self):
        self.assertEqual(
            ledpanel.led_part(ledpanel.run_checks(probe=healthy())).repair,
            "reinstall")


class PowerPartTest(unittest.TestCase):

    def test_nothing_set_is_the_ordinary_state_and_not_a_fault(self):
        # Leaving the CPU as SteamOS had it is this project's default.
        self.assertIsNone(
            ledpanel.power_part({"CPU_GOVERNOR": ""}, {}).ok)

    def test_a_governor_that_took_is_in_order(self):
        part = ledpanel.power_part(
            {"CPU_GOVERNOR": "powersave"},
            {"current": {"CPU_GOVERNOR": "powersave"}})
        self.assertTrue(part.ok)
        self.assertIn("powersave", part.verdict)

    def test_a_governor_that_did_not_take_is_the_interesting_case(self):
        """Written to the file and not running is invisible everywhere else.

        The settings page shows what you chose; the machine may be doing
        something else because the unit did not run or the driver refused.
        Nothing else in this window would ever say so.
        """
        part = ledpanel.power_part(
            {"CPU_GOVERNOR": "performance"},
            {"current": {"CPU_GOVERNOR": "powersave"}})
        self.assertFalse(part.ok)
        self.assertIn("performance", part.verdict)
        self.assertIn("powersave", part.verdict)

    def test_the_preference_is_detail_rather_than_verdict(self):
        part = ledpanel.power_part(
            {"CPU_GOVERNOR": "powersave", "CPU_EPP": "balance_power"},
            {"current": {"CPU_GOVERNOR": "powersave"}})
        self.assertIn("balance_power", " ".join(part.detail))


class CecPartTest(unittest.TestCase):

    def test_no_toolkit_is_not_installed_rather_than_broken(self):
        self.assertIsNone(ledpanel.cec_part(None, installed=False).ok)

    def test_a_toolkit_that_will_not_answer_is_broken_and_repairable(self):
        part = ledpanel.cec_part(None, installed=True)
        self.assertFalse(part.ok)
        self.assertEqual(part.repair, "install-cec")

    def test_a_reachable_adapter_is_in_order(self):
        part = ledpanel.cec_part(cec_status(), True)
        self.assertTrue(part.ok)
        self.assertIn("/dev/cec0", part.verdict)

    def test_an_adapter_that_cannot_be_written_to_is_a_problem(self):
        # Which is what a suspend or a SteamOS update leaves behind, and the
        # toolkit ships a helper and a udev rule for exactly it.
        part = ledpanel.cec_part(cec_status(cec_device={
            "device": "/dev/cec0", "exists": True,
            "readable": True, "writable": False}), True)
        self.assertFalse(part.ok)
        self.assertEqual(part.repair, "install-cec")

    def test_it_counts_the_features_that_are_on(self):
        found = cec_status(external_volume={"enabled": True})
        part = ledpanel.cec_part(found, True)
        self.assertIn("1 feature", part.verdict)
        self.assertIn("external-volume", " ".join(part.detail))

    def test_the_feature_names_come_from_the_toolkit_s_own_table(self):
        found = cec_status()
        found["services"] = {"steam-button": {"is_enabled": True}}
        part = ledpanel.cec_part(found, True)
        self.assertIn("steam-button", " ".join(part.detail))
        self.assertIn("steam-button", [n for n, _k, _l, _s in cec.FEATURES])


class LayoutPartTest(unittest.TestCase):

    def test_it_is_never_a_fault(self):
        """There is nothing here that can break.

        A line is in a file or it is not, so this reports set or unset and
        never counts against the window's summary - a keyboard layout is a
        preference, and a preference cannot be broken.
        """
        for layout in ("", "de", "fr"):
            self.assertNotEqual(ledpanel.layout_part(layout).ok, False)

    def test_a_layout_that_is_set_is_named_the_way_the_menu_names_it(self):
        part = ledpanel.layout_part("de", {"de": "German"})
        self.assertEqual(part.verdict, "German")

    def test_it_says_when_it_takes_effect(self):
        # The thing everybody gets wrong about this setting.
        part = ledpanel.layout_part("de")
        self.assertIn("login", " ".join(part.detail).lower())


class PanelPartTest(unittest.TestCase):

    def test_it_is_always_installed_because_you_are_looking_at_it(self):
        self.assertTrue(ledpanel.panel_part("1.0.0").ok)

    def test_it_carries_the_version(self):
        self.assertIn("1.2.3", ledpanel.panel_part("1.2.3").verdict)

    def test_an_available_update_is_said_here_too(self):
        part = ledpanel.panel_part("1.0.0", ledpanel.UPDATE_AVAILABLE,
                                   "3 commit(s) waiting.")
        self.assertIn("3 commit", part.verdict)

    def test_being_up_to_date_adds_nothing(self):
        # A line saying "no update" on every visit is a line that stops being
        # read, and then the one that matters is not read either.
        part = ledpanel.panel_part("1.0.0", ledpanel.UPDATE_CURRENT,
                                   "Up to date.")
        self.assertEqual(part.verdict, "Version 1.0.0.")


class SummaryTest(unittest.TestCase):

    def _parts(self, **broken):
        parts = [ledpanel.led_part(ledpanel.run_checks(probe=healthy())),
                 ledpanel.cec_part(cec_status(), True),
                 ledpanel.layout_part(""),
                 ledpanel.panel_part("1.0.0")]
        for part in parts:
            if part.key in broken:
                part.ok = broken[part.key]
        return parts

    def test_all_well_says_so(self):
        self.assertIn("in order", ledpanel.parts_summary(self._parts()))

    def test_one_problem_is_named_rather_than_counted(self):
        """"1 problem(s) found" makes somebody go looking for it.

        With one thing wrong the sentence can be the same one its own block
        shows, which is where they are about to look anyway.
        """
        said = ledpanel.parts_summary(self._parts(cec=False))
        self.assertIn("HDMI CEC", said)
        self.assertNotIn("1 problem", said)

    def test_several_are_counted_and_named(self):
        said = ledpanel.parts_summary(self._parts(cec=False, led=False))
        self.assertIn("2 problems", said)
        self.assertIn("HDMI CEC", said)
        self.assertIn("LED bar", said)

    def test_parts_nobody_installed_are_not_problems(self):
        """The bug this replaces, in reverse.

        The old summary counted the LED checklist alone, so a machine whose
        CEC adapter had gone read "Everything is in order" while the CEC page
        said otherwise. Counting uninstalled parts would be the same mistake
        the other way: a permanent complaint about something nobody wanted.
        """
        parts = [ledpanel.cec_part(None, False),
                 ledpanel.power_part({"CPU_GOVERNOR": ""}, {}),
                 ledpanel.layout_part("")]
        self.assertIn("in order", ledpanel.parts_summary(parts))

    def test_an_empty_list_is_not_a_crash(self):
        self.assertIn("in order", ledpanel.parts_summary([]))


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
