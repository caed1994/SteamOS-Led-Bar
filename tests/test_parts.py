# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Every part of the toolbox, described the same way.

Three states and not two, a clear difference between "not installed" and
"broken", and a summary that counts each part and not one part.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, os.path.join(HERE, "..", "gui"))

import ledpanel                                             # noqa: E402
from steamos_utility_center import cec                                 # noqa: E402
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

        The settings page shows the selected value. The machine can run a
        different value, because the unit did not start or the driver refused
        it. No other part of this window reports that difference.
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


class CecVersionPartTest(unittest.TestCase):
    """A toolkit that answers every question and is not the one in the clone.

    It was reported as ready. update.sh brought a newer cec-toolkit/ into the
    clone, install.sh named the toolkit nowhere, and the copy on the machine
    stayed as old as it was.
    """

    def setUp(self):
        import tempfile
        from steamos_utility_center import cec as cec_module
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.clone = holder.name
        os.makedirs(os.path.join(self.clone, cec_module.SOURCE))
        with open(os.path.join(self.clone, cec_module.SOURCE, "VERSION"),
                  "w", encoding="utf-8") as handle:
            handle.write("v9.9.9\n")

    def test_a_toolkit_of_this_clone_is_ready(self):
        part = ledpanel.cec_part(cec_status(version="v9.9.9"), True,
                                 self.clone)
        self.assertTrue(part.ok)

    def test_an_older_one_is_not_ready_and_says_both_versions(self):
        part = ledpanel.cec_part(cec_status(version="v9.9.8"), True,
                                 self.clone)
        self.assertFalse(part.ok)
        self.assertIn("v9.9.8", part.verdict)
        self.assertIn("v9.9.9", part.verdict)
        self.assertEqual(part.repair, "install-cec")

    def test_it_says_what_to_do_about_it(self):
        part = ledpanel.cec_part(cec_status(version="v9.9.8"), True,
                                 self.clone)
        self.assertTrue(any("install" in line.lower() for line in part.detail),
                        part.detail)

    def test_a_caller_with_no_clone_asks_no_such_question(self):
        """The status page passes it. A caller that does not is unchanged."""
        self.assertTrue(ledpanel.cec_part(cec_status(version="v0.0.1"),
                                          True).ok)

    def test_the_version_is_in_the_detail_either_way(self):
        part = ledpanel.cec_part(cec_status(version="v9.9.9"), True,
                                 self.clone)
        self.assertTrue(any("v9.9.9" in line for line in part.detail),
                        part.detail)


class GpuPartTest(unittest.TestCase):

    def _state(self, **changes):
        found = {"gpu": "1002:163F", "name": "VanGogh [AMD Custom GPU]",
                 "config": {"fan_control_enabled": False},
                 "stats": {"power": {"cap_current": 15.0, "cap_max": 25.0,
                                     "cap_min": 4.0}},
                 "clocks": {}, "profiles": [], "profile": ""}
        found.update(changes)
        return found

    def test_no_lact_is_not_installed_rather_than_broken(self):
        """Most machines will never run it, and it is not ours to install.

        Counted as a fault, every one of them would carry a red number over
        every page about a tool they chose not to have.
        """
        self.assertIsNone(ledpanel.gpu_part(None).ok)

    def test_no_socket_is_the_answer_and_not_a_window_that_did_not_ask(self):
        """`state is None` had three meanings, and this read it as one.

        No daemon, a daemon that gives no answer, and a window that did not
        ask are three different things. The page reported all three as
        "LACT is not running", so a person whose card was under LACT's
        control read that on the status page while the page beside it set the
        card.
        """
        gone = ledpanel.gpu_part(None, available=False)
        self.assertIsNone(gone.ok)
        self.assertIn("not running", gone.verdict)

        looking = ledpanel.gpu_part(None, available=True, asked=False)
        self.assertIsNone(looking.ok)
        self.assertIn("Looking", looking.verdict)

        quiet = ledpanel.gpu_part(None, available=True, asked=True)
        self.assertFalse(quiet.ok, "a daemon that will not answer is a fault")
        self.assertIn("did not answer", quiet.verdict)

    def test_a_card_it_read_is_reported_whatever_the_socket_says(self):
        """An answer in hand beats a file test that raced it."""
        self.assertTrue(ledpanel.gpu_part(self._state(), available=True).ok)

    def test_a_daemon_that_would_not_answer_is_a_fault_with_its_own_words(self):
        part = ledpanel.gpu_part(None, "the socket refused this user")
        self.assertFalse(part.ok)
        self.assertIn("refused", part.verdict)

    def test_a_daemon_with_no_card_is_a_fault_too(self):
        # The daemon runs and has no card. The block must report that. With no
        # text, the block looks like a draw fault.
        part = ledpanel.gpu_part(self._state(gpu="", name=""))
        self.assertFalse(part.ok)
        self.assertIn("no graphics card", part.verdict)

    def test_a_card_that_answers_is_in_order_and_named(self):
        part = ledpanel.gpu_part(self._state())
        self.assertTrue(part.ok)
        self.assertIn("VanGogh", part.verdict)

    def test_the_detail_says_what_can_actually_be_set(self):
        """Reports the settings, and an integrated card offers one or none.

        A user reads the status page when the block above has fewer sliders
        than the user expects.
        """
        part = ledpanel.gpu_part(self._state())
        self.assertIn("Power limit", " ".join(part.detail))
        self.assertIn("Card:", " ".join(part.detail))

    def test_a_card_with_nothing_settable_says_that_rather_than_nothing(self):
        part = ledpanel.gpu_part(self._state(stats={}))
        self.assertIn("nothing", " ".join(part.detail))

    def test_it_says_who_is_driving_the_fan(self):
        left = ledpanel.gpu_part(self._state())
        self.assertIn("firmware", " ".join(left.detail))
        taken = ledpanel.gpu_part(self._state(
            config={"fan_control_enabled": True}))
        self.assertIn("LACT", " ".join(taken.detail))

    def test_it_offers_no_repair(self):
        # Nothing here installs LACT, so there is nothing this window could
        # press a button to fix.
        self.assertEqual(ledpanel.gpu_part(self._state()).repair, "")


class LayoutPartTest(unittest.TestCase):

    def test_it_is_never_a_fault(self):
        """There is nothing here that can break.

        A line is in a file or it is not in a file. So this reports set or
        unset, and it never counts against the summary of the window. A
        keyboard layout is a preference, and a preference cannot break.
        """
        for layout in ("", "de", "fr"):
            self.assertNotEqual(ledpanel.layout_part(layout).ok, False)

    def test_a_layout_that_is_set_is_named_the_way_the_menu_names_it(self):
        part = ledpanel.layout_part("de", {"de": "German"})
        self.assertTrue(part.verdict.startswith("German."), part.verdict)

    def test_it_says_when_it_takes_effect(self):
        # The one fact that a user needs about this setting. It is in the line
        # and not behind a fold. One sentence does not need a Details button
        # that the user must find and click.
        part = ledpanel.layout_part("de")
        self.assertIn("login", part.verdict.lower())
        self.assertEqual(part.detail, [])


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


class ApplierCommandTest(unittest.TestCase):
    """With a password or without one, and what decides which.

    The installer writes a sudoers rule for the appliers it installed. A run
    that matches a line of it needs no password. Everything else goes through
    pkexec, which asks.
    """

    def setUp(self):
        from steamos_utility_center import ctl
        self.ctl = ctl
        # An installation where everything is in place. The test machine has
        # none of these files, so the answer is a stub rather than a mkdir in
        # /var/lib.
        self.there = {ctl.SUDO_RULE, ctl.APPLY_CONFIG, ctl.APPLY_POWER,
                      ctl.APPLY_MOUNTS}
        was = ledpanel.os.path.exists
        ledpanel.os.path.exists = lambda path: path in self.there or was(path)
        self.addCleanup(setattr, ledpanel.os.path, "exists", was)

    def test_an_installation_with_the_rule_needs_no_password(self):
        command = ledpanel.apply_config_command(
            "/src", self.ctl.STAGED["strip"])
        self.assertEqual(command[:2], ["sudo", "-n"])
        self.assertEqual(command[2], self.ctl.APPLY_CONFIG)

    def test_no_rule_means_it_asks(self):
        self.there.discard(self.ctl.SUDO_RULE)
        command = ledpanel.apply_config_command(
            "/src", self.ctl.STAGED["strip"])
        self.assertEqual(command[0], "pkexec")

    def test_no_installation_means_it_asks(self):
        """A clone with nothing installed still works, through the scripts."""
        self.there.discard(self.ctl.APPLY_POWER)
        command = ledpanel.apply_power_command("/src",
                                               self.ctl.STAGED["power"])
        self.assertEqual(command[0], "pkexec")
        self.assertIn("apply-power.sh", command[1])

    def test_a_file_the_rule_does_not_name_means_it_asks(self):
        """No line of the rule matches a name that nobody knew in advance."""
        command = ledpanel.apply_config_command("/src", "/tmp/anything.conf")
        self.assertEqual(command[0], "pkexec")

    def test_asking_can_be_asked_for(self):
        command = ledpanel.apply_config_command(
            "/src", self.ctl.STAGED["strip"], ask=True)
        self.assertEqual(command[0], "pkexec")

    def test_giving_a_drive_away_always_asks(self):
        """The chown walks a whole drive as root, so a person answers for it.

        It carries a second argument, and no line of the rule matches a
        command of two. The rule leaves it out on purpose.
        """
        command = ledpanel.apply_mounts_command(
            "/src", self.ctl.STAGED["drives"], "/mnt/games")
        self.assertEqual(command[0], "pkexec")
        self.assertEqual(command[-1], "/mnt/games")

    def test_writing_the_drives_without_a_chown_needs_no_password(self):
        command = ledpanel.apply_mounts_command("/src",
                                                self.ctl.STAGED["drives"])
        self.assertEqual(command[:2], ["sudo", "-n"])

    def test_every_area_of_the_rule_has_an_applier(self):
        """A name in one and not the other is a run that nothing permits."""
        for area in self.ctl.STAGED:
            self.assertIn(area, self.ctl.APPLIER)


class DriveTroubleTest(unittest.TestCase):
    """The reason a drive did not mount, taken out of the applier's output.

    Every line here came from a real machine. The panel showed "A command
    failed. Start the panel from a terminal to see why", and the third line
    from the end said exactly what was wrong.
    """

    REAL = """wrote /etc/systemd/system/mnt-SN7100.mount
wrote /etc/atomic-update.conf.d/steamos-utility-center.conf
Job failed. See "journalctl -xe" for details.
warning: mnt-SN7100.mount did not mount. Is the drive connected?
* mnt-SN7100.mount - /mnt/SN7100, mounted by the SteamOS Utility Center
     Loaded: loaded (/etc/systemd/system/mnt-SN7100.mount; enabled)
     Active: failed (Result: resources)
      Where: /mnt/SN7100
       What: /dev/disk/by-uuid/7fba6088-cfa1-45c2-a61d-703d64ec2867

Sep 03 20:26:02 FractalMachine systemd[1]: mnt-SN7100.mount: Mount path /mnt/SN7100 is not canonical (contains a symlink).
Sep 03 20:26:02 FractalMachine systemd[1]: mnt-SN7100.mount: Failed with result 'resources'.
"""

    def test_it_finds_the_line_that_answers_the_question(self):
        said = ledpanel.drive_trouble(self.REAL, most=2)
        self.assertIn("not canonical", said)

    def test_the_date_and_the_host_are_not_in_it(self):
        """A card is not wide enough for a journal prefix and an answer."""
        said = ledpanel.drive_trouble(self.REAL, most=3)
        self.assertNotIn("FractalMachine", said)
        self.assertNotIn("Sep 03", said)

    def test_the_lines_that_report_progress_are_left_out(self):
        said = ledpanel.drive_trouble(self.REAL, most=4)
        self.assertNotIn("wrote /etc", said)
        self.assertNotIn("Where:", said)

    def test_it_holds_the_number_of_lines_it_was_asked_for(self):
        self.assertEqual(len(ledpanel.drive_trouble(self.REAL,
                                                    most=2).splitlines()), 2)

    def test_a_run_that_printed_nothing_gives_nothing(self):
        self.assertEqual(ledpanel.drive_trouble(""), "")
        self.assertEqual(ledpanel.drive_trouble([]), "")

    def test_a_correct_run_gives_nothing_worth_showing(self):
        self.assertEqual(
            ledpanel.drive_trouble("wrote /etc/systemd/system/mnt-games.mount\n"
                                   "1 drive(s) mounted, 0 did not\n"), "")


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
