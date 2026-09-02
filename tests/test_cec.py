# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The seam between this panel and the CEC toolkit.

No machine here has a CEC adapter, a television, or the toolkit installed, and
none of that is needed: the module builds commands and reads answers, so the
commands can be checked as lists and the answers fed in as recorded documents.

The status documents below are the shapes steamos-cec-toolkitctl actually
produces, trimmed to the keys this panel reads.
"""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_utility_center import cec                                  # noqa: E402


def status(**changes):
    """A status document with everything off, changed as asked."""
    found = {
        "ok": True,
        "version": "v0.1.26",
        "config": {"CEC_DEVICE": "/dev/cec0",
                   "CEC_AUDIO_LOGICAL_ADDRESS": "5",
                   "HDMI_ALSA_CARD_NAME": "alsa_card.pci-0000_03_00.1"},
        "cec_device": {"device": "/dev/cec0", "exists": True,
                       "readable": True, "writable": True},
        "external_volume": {"enabled": False},
        "services": {name: {"is_enabled": False, "is_active": False}
                     for name, kind, _l, _s in cec.FEATURES
                     if kind == cec.USER_SERVICE},
        "system_services": {name: {"is_enabled": False, "is_active": False}
                            for name, kind, _l, _s in cec.FEATURES
                            if kind == cec.SYSTEM_SERVICE},
    }
    found.update(changes)
    return found


class FeatureTableTest(unittest.TestCase):

    def test_every_feature_has_a_name_a_label_and_a_sentence(self):
        for name, kind, label, said in cec.FEATURES:
            self.assertTrue(name and label and said, name)
            self.assertIn(kind, (cec.USER_SERVICE, cec.SYSTEM_SERVICE,
                                 cec.EXTERNAL_VOLUME, cec.RESUME_WAKE), name)
            self.assertNotEqual(label, name, "%s is unlabelled" % name)
            # A sentence, not a restated label. Every one of these switches
            # does something to the television or to sleep, and a switch whose
            # explanation is its own name is one nobody can safely try.
            self.assertGreater(len(said), len(label), name)

    def test_the_names_are_the_toolkit_s_own(self):
        """They are passed straight to set-service, so they are not ours.

        Read out of the vendored control program rather than copied into a
        list here: a rename upstream has to fail loudly at this seam, because
        the alternative is a switch that silently stops matching anything.
        """
        source = os.path.join(HERE, "..", "cec-toolkit",
                              "bin", "steamos-cec-toolkitctl")
        with open(source) as handle:
            text = handle.read()
        for name, kind, _label, _said in cec.FEATURES:
            if kind in (cec.EXTERNAL_VOLUME, cec.RESUME_WAKE):
                continue                # neither is in a service table
            self.assertIn('"%s":' % name, text,
                          "the toolkit has no service called %s" % name)

    def test_the_resume_wake_unit_is_the_toolkit_s_own(self):
        """The one switch that names a unit instead of a service.

        toolkitctl has it in neither table, so the seam that would catch a
        rename is the unit file itself - which is what this switch enables.
        """
        unit = os.path.join(HERE, "..", "cec-toolkit",
                            "systemd", "system", cec.RESUME_WAKE_UNIT)
        self.assertTrue(os.path.exists(unit), unit)

    def test_switching_it_goes_through_our_helper_not_the_toolkit(self):
        # toolkitctl cannot switch it, and it is a root unit - so this is the
        # one feature whose command is a pkexec of ours.
        command = cec.toggle_command("resume-wake", True, source_dir="/clone")
        self.assertEqual(command[0], "pkexec")
        self.assertTrue(command[1].endswith("scripts/install-cec.sh"))
        self.assertEqual(command[2:], ["resume-wake", "on"])

    def test_what_systemd_says_decides_whether_it_is_on(self):
        self.assertTrue(cec.resume_wake_enabled("enabled\n"))
        self.assertFalse(cec.resume_wake_enabled("disabled\n"))
        # A runner that hands back nothing for a bad exit is saying "off",
        # which is also what a unit that is not there means here.
        self.assertFalse(cec.resume_wake_enabled(None))
        self.assertTrue(cec.feature_on({cec.RESUME_WAKE_REPORT: True},
                                       "resume-wake"))
        self.assertFalse(cec.feature_on({}, "resume-wake"))

    def test_both_service_kinds_and_the_volume_are_covered(self):
        kinds = {kind for _n, kind, _l, _s in cec.FEATURES}
        self.assertEqual(kinds, {cec.USER_SERVICE, cec.SYSTEM_SERVICE,
                                 cec.EXTERNAL_VOLUME, cec.RESUME_WAKE})

    def test_no_explanation_runs_past_two_short_lines(self):
        """Reported: the page was a wall of prose nobody read.

        These are switches on a page with nine of them. What each one does is
        worth a sentence; how it does it, what it is configured by, and which
        televisions misbehave are worth a README - and were all in here.

        Counted rather than eyeballed, because prose grows back: every time
        something is learned about one of these the temptation is to write it
        down right here, where it is read most and belongs least.
        """
        for name, _kind, _label, said in cec.FEATURES:
            self.assertLessEqual(len(said), 160,
                                 "%s: %d characters" % (name, len(said)))
            self.assertNotIn("\n", said, name)

    def test_no_explanation_names_a_setting_the_page_already_offers(self):
        """The config keys were spelled out in the prose *and* in the fields.

        CEC_SLEEP_TV_ACTION and INPUT_INACTIVE_SUSPEND_DELAY_SECONDS both
        have a box of their own further down the same page, with a label in
        words. Naming them here as well was the switch explaining the page to
        itself.
        """
        for name, _kind, _label, said in cec.FEATURES:
            for key, _label2, _help, _choices in cec.SHOWN:
                self.assertNotIn(key, said, "%s names %s" % (name, key))

    def test_no_feature_is_listed_twice(self):
        names = [name for name, _k, _l, _s in cec.FEATURES]
        self.assertEqual(len(names), len(set(names)))


class ReadStatusTest(unittest.TestCase):

    def test_what_the_toolkit_prints_comes_back_as_a_dictionary(self):
        self.assertEqual(cec.read_status(json.dumps(status()))["version"],
                         "v0.1.26")

    def test_output_that_is_not_json_is_an_error_not_an_empty_status(self):
        """A half-finished install prints a traceback, not a document.

        Read as "nothing is enabled", that traceback would draw a page of
        switches all showing off, which is a lie about the machine - and the
        one after it, where somebody switches one on, would fail for a reason
        nothing on screen explains.
        """
        with self.assertRaises(cec.CecError):
            cec.read_status("Traceback (most recent call last):\n")

    def test_json_that_is_not_a_status_is_refused_too(self):
        with self.assertRaises(cec.CecError):
            cec.read_status("[1, 2, 3]")

    def test_nothing_is_printed_at_all(self):
        with self.assertRaises(cec.CecError):
            cec.read_status("")


class FeatureStateTest(unittest.TestCase):

    def test_a_user_service_that_is_enabled_reads_as_on(self):
        found = status()
        found["services"]["steam-button"]["is_enabled"] = True
        self.assertTrue(cec.feature_on(found, "steam-button"))
        self.assertFalse(cec.feature_on(found, "boot-wake"))

    def test_a_system_service_is_read_from_its_own_half(self):
        # Two dictionaries, and the toolkit keeps them apart because one is
        # asked with `systemctl --user` and the other is not. Looking in the
        # wrong one finds nothing and reports off.
        found = status()
        found["system_services"]["usb-wake"]["is_enabled"] = True
        self.assertTrue(cec.feature_on(found, "usb-wake"))

    def test_the_volume_integration_has_a_state_of_its_own(self):
        found = status(external_volume={"enabled": True})
        self.assertTrue(cec.feature_on(found, "external-volume"))

    def test_enabled_is_the_question_not_active(self):
        """boot-wake runs once at session start and exits.

        It is enabled and doing its job and almost never active, so a switch
        that asked whether it was running would show it off whenever it had
        finished - which is nearly always.
        """
        found = status()
        found["services"]["boot-wake"] = {"is_enabled": True,
                                          "is_active": False}
        self.assertTrue(cec.feature_on(found, "boot-wake"))

    def test_a_status_missing_a_feature_reads_as_off_rather_than_crashing(self):
        # An older toolkit than the vendored one, on somebody's machine from
        # before. A missing key is a feature that is not there to be on.
        self.assertFalse(cec.feature_on({"services": {}}, "steam-button"))
        self.assertFalse(cec.feature_on({}, "usb-wake"))
        self.assertFalse(cec.feature_on({}, "external-volume"))

    def test_every_feature_in_the_table_can_be_read(self):
        found = status()
        for name, _k, _l, _s in cec.FEATURES:
            self.assertFalse(cec.feature_on(found, name), name)


class CommandTest(unittest.TestCase):

    HOME = "/home/deck"

    def _tail(self, command):
        self.assertEqual(command[0], cec.command_path(self.HOME))
        return command[1:]

    def test_a_user_service_is_switched_with_set_service(self):
        self.assertEqual(
            self._tail(cec.toggle_command("steam-button", True, self.HOME)),
            ["set-service", "steam-button", "on"])

    def test_a_system_service_has_its_own_subcommand(self):
        # It goes through a NOPASSWD helper rather than systemctl --user, so
        # the toolkit refuses the name under the other subcommand entirely.
        self.assertEqual(
            self._tail(cec.toggle_command("usb-wake", False, self.HOME)),
            ["set-system-service", "usb-wake", "off"])

    def test_the_volume_integration_takes_no_name(self):
        self.assertEqual(
            self._tail(cec.toggle_command("external-volume", True, self.HOME)),
            ["set-external-volume", "on"])

    def test_every_feature_can_be_switched_both_ways(self):
        for name, _k, _l, _s in cec.FEATURES:
            for state in (True, False):
                command = cec.toggle_command(name, state, self.HOME)
                self.assertEqual(command[-1], "on" if state else "off", name)

    def test_the_actions_are_the_toolkit_s_own_subcommands(self):
        self.assertEqual(self._tail(cec.action_command("wake", self.HOME)),
                         ["wake"])
        self.assertEqual(
            self._tail(cec.action_command("volume-up", self.HOME)),
            ["volume", "up"])

    def test_a_discovery_is_an_action_too(self):
        self.assertEqual(
            self._tail(cec.action_command("discover-cec", self.HOME)),
            ["discover-cec"])

    def test_an_action_nobody_defined_is_a_mistake_worth_raising(self):
        with self.assertRaises(KeyError):
            cec.action_command("format-the-disk", self.HOME)

    def test_settings_go_over_as_one_json_argument(self):
        command = cec.set_config_command({"CEC_DEVICE": "/dev/cec1"},
                                         self.HOME)
        self.assertEqual(command[1], "set-config")
        self.assertEqual(json.loads(command[2]), {"CEC_DEVICE": "/dev/cec1"})

    def test_a_value_with_a_space_in_it_stays_one_argument(self):
        # HDMI_ALSA_CARD_NICK is "HDA ATI HDMI". Built by hand into a string
        # this would arrive as three settings, two of them nonsense.
        command = cec.set_config_command(
            {"HDMI_ALSA_CARD_NICK": "HDA ATI HDMI"}, self.HOME)
        self.assertEqual(len(command), 3)
        self.assertEqual(json.loads(command[2])["HDMI_ALSA_CARD_NICK"],
                         "HDA ATI HDMI")

    def test_the_status_command_is_asked_of_the_installed_copy(self):
        # Not of the vendored tree. The installed one is the one whose config
        # and services are the machine's; running the repository's copy would
        # report a machine nobody is using.
        self.assertTrue(cec.status_command(self.HOME)[0].startswith(self.HOME))


class InstalledTest(unittest.TestCase):

    def setUp(self):
        import tempfile
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.home = holder.name

    def test_a_machine_without_the_toolkit_says_so(self):
        self.assertFalse(cec.installed(self.home))

    def test_the_control_program_is_what_is_looked_for(self):
        """Not the config file.

        /etc/steamos-cec-toolkit.conf is listed in atomic-update.conf.d, so
        SteamOS carries it across an OS update - and it outlives an uninstall.
        A page that keyed off the config would offer to configure a toolkit
        that is not there.
        """
        where = cec.command_path(self.home)
        os.makedirs(os.path.dirname(where))
        with open(where, "w") as handle:
            handle.write("#!/usr/bin/env python3\n")
        self.assertFalse(cec.installed(self.home), "not executable yet")
        os.chmod(where, 0o755)
        self.assertTrue(cec.installed(self.home))


class DeviceTest(unittest.TestCase):

    def test_an_adapter_that_is_there_and_writable_is_usable(self):
        self.assertTrue(cec.usable(status()))

    def test_no_adapter_is_not_usable(self):
        self.assertFalse(cec.usable(status(cec_device={
            "device": "/dev/cec0", "exists": False,
            "readable": False, "writable": False})))

    def test_an_adapter_that_cannot_be_written_is_not_usable_either(self):
        """CEC is a conversation, not a broadcast anybody may listen to.

        Readable but not writable is the shape a permissions problem takes
        after a suspend or a SteamOS update, which the toolkit installs a udev
        rule and a helper to repair - so it is worth telling apart from having
        no adapter, and both are worth calling unusable.
        """
        self.assertFalse(cec.usable(status(cec_device={
            "device": "/dev/cec0", "exists": True,
            "readable": True, "writable": False})))

    def test_a_status_with_no_device_section_does_not_crash(self):
        found = cec.device({})
        self.assertFalse(found["exists"])
        self.assertFalse(cec.usable({}))


# What cec-ctl reported on the machine this was found on: a good physical
# address, and no place on the bus at all. Kept verbatim, spacing included -
# the parsing is the whole point and a tidied-up copy would not test it.
UNREGISTERED = """\
Driver version           : 7.2.0
Available Logical Addresses: 4
DRM Connector Info       : card 0, connector 93
Physical Address         : 3.0.0.0
Logical Address Mask     : 0x0000
CEC Version              : 2.0
OSD Name                 : ''
Logical Addresses        : 0
"""

REGISTERED = UNREGISTERED.replace(
    "Logical Address Mask     : 0x0000",
    "Logical Address Mask     : 0x0010").replace(
    "Logical Addresses        : 0",
    "Logical Addresses        : 1")

NO_PICTURE = UNREGISTERED.replace("3.0.0.0", "f.f.f.f")


class AudioProbeTest(unittest.TestCase):
    """Asking the television whether it does volume at all.

    Established the hard way on a Samsung: it accepts every volume key,
    acts on none of them, and answers nothing - so the switch looked broken
    and every log said the message had been sent. Asked directly it replied
    in 26 ms:

        GIVE_SYSTEM_AUDIO_MODE_STATUS (0x7d)
            Received from TV (0): FEATURE_ABORT reason: refused (0x04)

    Which is the difference between "will not" and "did not hear".
    """

    def test_it_asks_the_device_the_settings_name(self):
        command = cec.audio_probe_command(
            {"CEC_DEVICE": "/dev/cec1", cec.AUDIO_ADDRESS: "5"})
        self.assertEqual(command[:5],
                         ["cec-ctl", "-d", "/dev/cec1", "--to", "5"])
        self.assertIn("--give-system-audio-mode-status", command)

    def test_no_settings_means_the_usual_adapter_and_the_television(self):
        command = cec.audio_probe_command()
        self.assertEqual(command[:5],
                         ["cec-ctl", "-d", cec.DEFAULT_DEVICE, "--to", "0"])

    def test_an_empty_address_is_the_television_rather_than_nothing(self):
        """A blank setting must not become "--to " with no target."""
        command = cec.audio_probe_command({cec.AUDIO_ADDRESS: "  "})
        self.assertEqual(command[4], "0")

    def test_it_asks_and_changes_nothing(self):
        """A Try it button that altered the volume would not be a question."""
        command = cec.audio_probe_command()
        for word in ("--user-control-pressed", "--playback", "--standby"):
            self.assertNotIn(word, command)

    def test_it_is_offered_beside_the_other_things_to_try(self):
        labels = dict((name, label) for name, label, _tail in cec.ACTIONS)
        self.assertIn(cec.AUDIO_PROBE, labels)
        self.assertEqual(labels[cec.AUDIO_PROBE], "Ask about volume")

    def test_the_toolkit_actions_still_go_through_the_toolkit(self):
        """It is the one action here that is not a toolkit subcommand."""
        self.assertTrue(cec.action_command("wake")[0].endswith("toolkitctl"))
        self.assertEqual(cec.action_command(cec.AUDIO_PROBE)[0], "cec-ctl")

    def test_the_switch_says_what_the_feature_needs(self):
        """It promised the television and delivers only with an amplifier."""
        said = dict((name, text) for name, _k, _l, text in cec.FEATURES)
        self.assertIn("soundbar", said["external-volume"])
        self.assertIn("Ask about volume", said["external-volume"])


# The adapter's own place on the bus - reading it, claiming it, repairing the
# permissions, and telling the toolkit where this machine is plugged in - was
# tested here while it was a unit of this panel working around a toolkit that
# registered nothing. It is the toolkit's own program now, so the tests went
# with it: tests/test_cec_register.py, against
# cec-toolkit/bin/steamos-cec-register.


class RequirementsTest(unittest.TestCase):

    def test_a_machine_with_everything_is_missing_nothing(self):
        self.assertEqual(
            cec.missing(module_check=lambda: True, which=lambda _n: "/usr/bin"),
            ())

    def test_each_missing_program_is_named_with_a_reason(self):
        absent = cec.missing(module_check=lambda: True,
                             which=lambda name: None if name == "cec-ctl"
                             else "/usr/bin/" + name)
        self.assertEqual(len(absent), 1)
        name, why = absent[0]
        self.assertEqual(name, "cec-ctl")
        # "cec-ctl is missing" tells somebody nothing they can act on. The
        # package it is in is the part they can do something about.
        self.assertIn("v4l-utils", why)

    def test_the_python_module_is_looked_for_differently_and_still_reported(self):
        """It is the one requirement the toolkit's installer only warns about.

        So it is the one you can install straight past and discover from a
        service log days later, which is exactly the sort of thing this page
        should say out loud.
        """
        absent = cec.missing(module_check=lambda: False,
                             which=lambda _n: "/usr/bin")
        self.assertEqual([name for name, _why in absent], ["python dbus_next"])

    def test_it_is_asked_of_the_machine_rather_than_assumed(self):
        # The real lookups, whatever this machine happens to have. What is
        # checked is that asking does not raise - the answer is the machine's.
        for name, why in cec.missing():
            self.assertTrue(name and why)


class ShownSettingsTest(unittest.TestCase):

    def test_the_settings_on_the_page_are_ones_the_toolkit_has(self):
        example = os.path.join(HERE, "..", "cec-toolkit",
                               "config", "steamos-cec-toolkit.conf.example")
        with open(example) as handle:
            text = handle.read()
        for key, _label, _said, _choices in cec.SHOWN:
            self.assertIn("\n%s=" % key, text,
                          "%s is not a setting the toolkit reads" % key)

    def test_each_one_is_labelled_and_explained(self):
        for key, label, said, _choices in cec.SHOWN:
            self.assertNotEqual(label, key)
            self.assertTrue(said.strip())

    def test_the_config_comes_out_of_the_status(self):
        self.assertEqual(cec.config(status())["CEC_DEVICE"], "/dev/cec0")

    def test_a_status_with_no_config_is_an_empty_one(self):
        self.assertEqual(cec.config({}), {})
        self.assertEqual(cec.config({"config": "not a dictionary"}), {})


class PanelCommandTest(unittest.TestCase):
    """What the window would run, checked without a window."""

    def setUp(self):
        sys.path.insert(0, os.path.join(HERE, "..", "gui"))
        import ledpanel
        self.ledpanel = ledpanel

    def test_installing_goes_through_our_own_script_not_theirs(self):
        """Not `pkexec cec-toolkit/install.sh`.

        That installer refuses to run as root, which is what pkexec makes it -
        so aimed straight at it the prompt would be spent to be told no.
        """
        command = self.ledpanel.install_cec_command("/repo")
        self.assertEqual(command[0], "pkexec")
        self.assertTrue(command[1].endswith("scripts/install-cec.sh"))
        self.assertEqual(command[2], "install")
        self.assertIn("cec-toolkit", command[3])

    def test_removing_is_the_same_script_with_the_other_word(self):
        command = self.ledpanel.install_cec_command("/repo", remove=True)
        self.assertEqual(command[2], "remove")

    def test_a_machine_without_the_toolkit_has_no_status_rather_than_a_blank(self):
        """None and {} are different pages.

        None is "not installed", which is a page offering to install. An empty
        status is "installed and reporting nothing", which is a page of
        switches all showing off. Returning the second for the first would
        offer to configure a toolkit that is not there.
        """
        with tempfile.TemporaryDirectory() as home:
            self.assertIsNone(self.ledpanel.cec_status(home))

    def test_it_asks_the_toolkit_when_there_is_one(self):
        with tempfile.TemporaryDirectory() as home:
            where = cec.command_path(home)
            os.makedirs(os.path.dirname(where))
            with open(where, "w") as handle:
                handle.write("#!/bin/sh\n")
            os.chmod(where, 0o755)
            asked = []

            def run(command):
                asked.append(command)
                if command == cec.resume_wake_command():
                    return "enabled\n"
                return json.dumps(status())

            found = self.ledpanel.cec_status(home, run=run)
        # Two: the toolkit's own status, and systemd for the one switch that
        # status does not report on.
        self.assertEqual(asked, [cec.status_command(home),
                                 cec.resume_wake_command()])
        self.assertTrue(found[cec.RESUME_WAKE_REPORT])
        self.assertEqual(found["version"], "v0.1.26")

    def test_a_toolkit_that_will_not_answer_is_not_installed_as_far_as_the_page_goes(self):
        # It is asked on every visit to the page and on a timer. A toolkit
        # mid-restart answering nothing must not put a line in the log and a
        # warning in the status bar each time.
        with tempfile.TemporaryDirectory() as home:
            where = cec.command_path(home)
            os.makedirs(os.path.dirname(where))
            with open(where, "w") as handle:
                handle.write("#!/bin/sh\n")
            os.chmod(where, 0o755)
            self.assertIsNone(self.ledpanel.cec_status(home,
                                                       run=lambda _c: None))

    def test_asking_a_program_that_is_not_there_is_not_a_crash(self):
        # The real runner, against a path that does not exist. This is the
        # timer's path, and an exception on it would come out of a callback.
        self.assertIsNone(self.ledpanel._run_quietly(["/nonexistent/toolkitctl",
                                                      "status"]))


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
