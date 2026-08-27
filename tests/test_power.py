# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The CPU governor and the EPP, against a sysfs built here.

Never against the real one. The machine running the suite has its own cpufreq
or none at all, and a test that read it would be a test whose answer depends
on the build machine - and one that wrote it would be a test that changes how
the build machine runs.
"""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, os.path.join(HERE, "..", "gui"))

import ledpanel                                             # noqa: E402
from steamos_led import power                               # noqa: E402


def _applier():
    """The installed script, imported by path - it has no .py on the end."""
    path = os.path.join(HERE, "..", "server", "steamos-led-power")
    loader = importlib.machinery.SourceFileLoader("steamos_led_power", path)
    spec = importlib.util.spec_from_loader("steamos_led_power", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FakeCpu(unittest.TestCase):
    """A sysfs of our own: two policies, amd-pstate in active mode."""

    GOVERNORS = "performance powersave"
    PREFERENCES = "default performance balance_performance balance_power power"
    MODE = "active"
    DRIVER = "amd-pstate-epp"
    CPUS = 2

    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = holder.name
        self.applier = _applier()
        for cpu in range(self.CPUS):
            self._policy(cpu)
        if self.MODE:
            state = os.path.join(self.root,
                                 "sys/devices/system/cpu/amd_pstate")
            os.makedirs(state)
            self._put(os.path.join(state, "status"), self.MODE)

    def _policy(self, cpu):
        where = os.path.join(self.root,
                             "sys/devices/system/cpu/cpu%d/cpufreq" % cpu)
        os.makedirs(where)
        self._put(os.path.join(where, power.DRIVER), self.DRIVER)
        self._put(os.path.join(where, power.GOVERNORS_AVAILABLE),
                  self.GOVERNORS)
        self._put(os.path.join(where, power.GOVERNOR), "powersave")
        if self.PREFERENCES:
            self._put(os.path.join(where, power.EPP_AVAILABLE),
                      self.PREFERENCES)
            self._put(os.path.join(where, power.EPP), "balance_performance")
        return where

    def _put(self, path, text):
        with open(path, "w") as handle:
            handle.write(text + "\n")

    def _get(self, cpu, name):
        with open(os.path.join(
                self.root, "sys/devices/system/cpu/cpu%d/cpufreq" % cpu,
                name)) as handle:
            return handle.read().strip()

    def _apply(self, **values):
        said = []
        settings = dict(power.DEFAULTS, **values)
        code = self.applier.apply_settings(settings, self.root, said.append)
        return code, "\n".join(said)


class ReadingTest(FakeCpu):

    def test_it_offers_what_the_machine_offers(self):
        self.assertEqual(power.governors(self.root),
                         ("performance", "powersave"))
        self.assertIn("balance_power", power.epp_values(self.root))

    def test_it_finds_every_policy_and_not_only_the_first(self):
        # The files are per policy. A machine written to on cpu0 alone is one
        # running its cores under different governors, with nothing to say so.
        self.assertEqual(len(power.policies(self.root)), self.CPUS)

    def test_it_names_the_driver(self):
        self.assertEqual(power.driver(self.root), "amd-pstate-epp")

    def test_it_reports_the_driver_mode(self):
        # active and passive offer different governors and only one of them
        # has an EPP, so which it is matters to what a page should show.
        self.assertEqual(power.driver_mode(self.root), "active")

    def test_what_is_set_right_now(self):
        self.assertEqual(power.current(self.root)["CPU_GOVERNOR"], "powersave")

    def test_a_machine_with_no_cpufreq_offers_nothing_and_does_not_raise(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(lambda: os.rmdir(empty))
        self.assertEqual(power.policies(empty), [])
        self.assertEqual(power.governors(empty), ())
        self.assertEqual(power.epp_values(empty), ())
        self.assertEqual(power.driver_mode(empty), "")
        self.assertEqual(power.current(empty)["CPU_GOVERNOR"], power.UNSET)


class ValidateTest(FakeCpu):

    def test_a_value_the_machine_offers_is_accepted(self):
        power.validate({"CPU_GOVERNOR": "powersave", "CPU_EPP": "power"},
                       self.root)

    def test_leaving_them_alone_is_always_valid(self):
        power.validate(dict(power.DEFAULTS), self.root)

    def test_a_governor_this_machine_does_not_have_is_refused(self):
        """A config file carried over from a machine in another driver mode.

        `ondemand` is a real governor and this machine does not have it: in
        amd-pstate active mode the kernel offers two. Written to sysfs it is
        an error the write returns and nothing reads.
        """
        with self.assertRaises(ValueError):
            power.validate({"CPU_GOVERNOR": "ondemand"}, self.root)

    def test_an_epp_this_machine_does_not_have_is_refused(self):
        # With a governor, because without one the preference is not a
        # setting at all and there is nothing to check - see epp_in_play.
        with self.assertRaises(ValueError):
            power.validate({"CPU_GOVERNOR": "powersave", "CPU_EPP": "turbo"},
                           self.root)

    def test_an_epp_without_a_governor_is_not_checked_at_all(self):
        # Not an oversight: it is not going to be written either, so refusing
        # it would be refusing to save a value that changes nothing.
        power.validate({"CPU_GOVERNOR": "", "CPU_EPP": "turbo"}, self.root)

    def test_the_message_names_what_is_on_offer(self):
        # So the answer to "then what may I set" is in the refusal itself.
        with self.assertRaises(ValueError) as caught:
            power.validate({"CPU_GOVERNOR": "ondemand"}, self.root)
        self.assertIn("powersave", str(caught.exception))


class ApplyTest(FakeCpu):

    def test_it_writes_every_policy(self):
        code, _said = self._apply(CPU_GOVERNOR="performance")
        self.assertEqual(code, 0)
        for cpu in range(self.CPUS):
            self.assertEqual(self._get(cpu, power.GOVERNOR), "performance")

    def test_it_writes_both_when_the_governor_allows_it(self):
        code, _said = self._apply(CPU_GOVERNOR="powersave", CPU_EPP="power")
        self.assertEqual(code, 0)
        self.assertEqual(self._get(0, power.GOVERNOR), "powersave")
        self.assertEqual(self._get(0, power.EPP), "power")

    def test_the_performance_governor_leaves_the_preference_alone(self):
        """The kernel's rule, and the reason this is one program.

        Under `performance` the firmware is pinned to its top preference and
        the EPP file stops accepting anything else. Writing it anyway would
        be a refusal reported as a failure - on a setting that was applied
        exactly as asked.
        """
        before = self._get(0, power.EPP)
        code, said = self._apply(CPU_GOVERNOR="performance", CPU_EPP="power")
        self.assertEqual(code, 0)
        self.assertEqual(self._get(0, power.EPP), before)
        self.assertIn("pins it", said)

    def test_nothing_set_touches_nothing(self):
        # The state of a fresh installation, and it must leave the CPU as
        # SteamOS set it rather than writing a default of ours.
        before = (self._get(0, power.GOVERNOR), self._get(0, power.EPP))
        code, said = self._apply()
        self.assertEqual(code, 0)
        self.assertEqual((self._get(0, power.GOVERNOR),
                          self._get(0, power.EPP)), before)
        self.assertIn("leaving the CPU alone", said)

    def test_a_value_the_machine_refuses_stops_before_anything_is_written(self):
        before = self._get(0, power.GOVERNOR)
        code, said = self._apply(CPU_GOVERNOR="ondemand", CPU_EPP="power")
        self.assertEqual(code, 2)
        self.assertEqual(self._get(0, power.GOVERNOR), before)
        self.assertEqual(self._get(0, power.EPP), "balance_performance")
        self.assertIn("refusing", said)

    def test_a_machine_with_no_cpufreq_is_not_a_failure(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(lambda: os.rmdir(empty))
        said = []
        code = self.applier.apply_settings({"CPU_GOVERNOR": "powersave"},
                                           empty, said.append)
        self.assertEqual(code, 0)
        self.assertIn("nothing to set", "\n".join(said))


class PinnedListTest(FakeCpu):
    """Reported: the setting was refused after the governor had been applied.

    The kernel collapses energy_performance_available_preferences to the one
    pinned value while the performance governor is running. So after applying
    that governor, picking powersave and a preference in the same sitting was
    checked against a list of one and refused - a valid pair, rejected because
    of the state the machine happened to be in on the way there.
    """

    def setUp(self):
        super().setUp()
        # What the machine looks like once `performance` has been applied.
        for cpu in range(self.CPUS):
            where = os.path.join(self.root,
                                 "sys/devices/system/cpu/cpu%d/cpufreq" % cpu)
            self._put(os.path.join(where, power.GOVERNOR), "performance")
            self._put(os.path.join(where, power.EPP_AVAILABLE), "performance")

    def test_the_machine_really_does_report_one_value(self):
        # The premise, so this test fails honestly if the shape ever changes.
        self.assertEqual(power._offered(power.EPP_AVAILABLE, self.root),
                         ("performance",))

    def test_but_what_may_be_chosen_is_the_whole_set(self):
        self.assertIn("balance_performance", power.epp_values(self.root))

    def test_and_the_pair_that_was_refused_is_accepted(self):
        power.validate({"CPU_GOVERNOR": "powersave",
                        "CPU_EPP": "balance_performance"}, self.root)

    def test_and_applying_it_writes_both(self):
        code, said = self._apply(CPU_GOVERNOR="powersave",
                                 CPU_EPP="balance_performance")
        self.assertEqual(code, 0, said)
        self.assertEqual(self._get(0, power.GOVERNOR), "powersave")
        self.assertEqual(self._get(0, power.EPP), "balance_performance")

    def test_a_governor_is_still_checked_against_the_machine(self):
        # The list of governors is not collapsed by anything, so that half
        # goes on being checked as strictly as before.
        with self.assertRaises(ValueError):
            power.validate({"CPU_GOVERNOR": "ondemand"}, self.root)


class GovernorRulesTheEppTest(FakeCpu):
    """Without a governor of ours, the preference is not ours to set either."""

    def test_it_is_not_in_play_without_a_governor(self):
        self.assertFalse(power.epp_in_play({"CPU_GOVERNOR": "",
                                            "CPU_EPP": "power"}, self.root))

    def test_so_applying_writes_neither(self):
        before = (self._get(0, power.GOVERNOR), self._get(0, power.EPP))
        code, said = self._apply(CPU_GOVERNOR="", CPU_EPP="power")
        self.assertEqual(code, 0)
        self.assertEqual((self._get(0, power.GOVERNOR),
                          self._get(0, power.EPP)), before)
        self.assertIn("leaving the CPU alone", said)

    def test_a_preference_left_in_the_file_stops_applying_with_it(self):
        """The half that would otherwise be a setting nobody can see.

        Set the pair, then take the governor back to "leave it to SteamOS".
        The preference is still in the config file - the panel hides the row
        rather than clearing it - and it must stop being written, or it is a
        setting still in force with nothing on screen that shows it.
        """
        self._apply(CPU_GOVERNOR="powersave", CPU_EPP="power")
        self.assertEqual(self._get(0, power.EPP), "power")
        self._put(os.path.join(self.root,
                               "sys/devices/system/cpu/cpu0/cpufreq",
                               power.EPP), "default")
        code, _said = self._apply(CPU_GOVERNOR="", CPU_EPP="power")
        self.assertEqual(code, 0)
        self.assertEqual(self._get(0, power.EPP), "default",
                         "the hidden preference was written anyway")

    def test_and_it_is_in_play_with_one(self):
        self.assertTrue(power.epp_in_play({"CPU_GOVERNOR": "powersave",
                                           "CPU_EPP": "power"}, self.root))


class IntelTest(FakeCpu):
    """The same shape, from the other vendor.

    intel_pstate in its active mode offers the same two governors and the
    same five preferences as amd-pstate does, and pins the preference under
    the same governor - so everything here works on it, and this is the test
    that says so rather than an assumption in a comment.

    What Intel does not have is /sys/devices/system/cpu/amd_pstate/status.
    The mode is in the driver's own name instead: intel_pstate is the active
    one, intel_cpufreq is that driver in passive mode.
    """

    DRIVER = "intel_pstate"
    MODE = ""                           # no amd_pstate directory at all

    def test_the_driver_is_named_without_asking_amd(self):
        self.assertEqual(power.driver(self.root), "intel_pstate")
        self.assertEqual(power.driver_mode(self.root), "")

    def test_the_report_does_not_say_what_the_machine_is_not(self):
        said = []
        self.applier.report(self.root, said.append)
        text = "\n".join(said)
        self.assertIn("intel_pstate", text)
        self.assertNotIn("amd_pstate", text)

    def test_the_preferences_are_the_same_five(self):
        self.assertEqual(set(power.epp_values(self.root)),
                         set(power.PINNED_FALLBACK))

    def test_and_setting_both_works_the_same_way(self):
        code, said = self._apply(CPU_GOVERNOR="powersave",
                                 CPU_EPP="balance_power")
        self.assertEqual(code, 0, said)
        self.assertEqual(self._get(0, power.GOVERNOR), "powersave")
        self.assertEqual(self._get(0, power.EPP), "balance_power")

    def test_and_performance_pins_the_preference_here_too(self):
        before = self._get(0, power.EPP)
        code, said = self._apply(CPU_GOVERNOR="performance", CPU_EPP="power")
        self.assertEqual(code, 0)
        self.assertEqual(self._get(0, power.EPP), before)
        self.assertIn("pins it", said)


class OldIntelTest(FakeCpu):
    """acpi-cpufreq: classic governors, no preference file anywhere."""

    DRIVER = "acpi-cpufreq"
    GOVERNORS = "conservative ondemand userspace powersave performance"
    PREFERENCES = ""
    MODE = ""

    def test_there_is_no_preference_to_set(self):
        self.assertEqual(power.epp_values(self.root), ())
        self.assertFalse(power.epp_in_play({"CPU_GOVERNOR": "ondemand"},
                                           self.root))

    def test_but_the_governor_still_works(self):
        code, said = self._apply(CPU_GOVERNOR="ondemand", CPU_EPP="power")
        self.assertEqual(code, 0, said)
        self.assertEqual(self._get(0, power.GOVERNOR), "ondemand")


class PassiveModeTest(FakeCpu):
    """The other mode, where the classic governors are back and EPP is gone."""

    GOVERNORS = "conservative ondemand userspace powersave performance schedutil"
    PREFERENCES = ""
    MODE = "passive"
    DRIVER = "amd-pstate"

    def test_the_classic_governors_are_offered(self):
        self.assertIn("schedutil", power.governors(self.root))

    def test_there_is_no_energy_preference_at_all(self):
        self.assertEqual(power.epp_values(self.root), ())

    def test_so_it_is_not_a_setting_here_and_is_never_written(self):
        """No file to write, so the value is carried and ignored.

        Refusing it would be refusing to save a config that is perfectly
        good on the machine it came from - and this one has nowhere to put it
        either way.
        """
        wanted = {"CPU_GOVERNOR": "schedutil", "CPU_EPP": "power"}
        self.assertFalse(power.epp_in_play(wanted, self.root))
        power.validate(wanted, self.root)
        code, said = self._apply(**wanted)
        self.assertEqual(code, 0)
        self.assertIn("no preference to set", said)

    def test_and_a_governor_it_does_have_still_applies(self):
        code, _said = self._apply(CPU_GOVERNOR="schedutil")
        self.assertEqual(code, 0)
        self.assertEqual(self._get(0, power.GOVERNOR), "schedutil")


class ConfigFileTest(FakeCpu):

    def test_the_file_round_trips(self):
        path = os.path.join(self.root, "power.conf")
        with open(path, "w") as handle:
            handle.write(ledpanel.power_config_text(
                {"CPU_GOVERNOR": "powersave", "CPU_EPP": "power"}))
        self.assertEqual(ledpanel.read_power_config(path),
                         {"CPU_GOVERNOR": "powersave", "CPU_EPP": "power"})
        self.assertEqual(self.applier.load(path),
                         {"CPU_GOVERNOR": "powersave", "CPU_EPP": "power"})

    def test_a_missing_file_reads_as_leaving_it_alone(self):
        self.assertEqual(ledpanel.read_power_config(
            os.path.join(self.root, "nope.conf")), dict(power.DEFAULTS))

    def test_comments_and_blank_lines_are_not_settings(self):
        path = os.path.join(self.root, "c")
        with open(path, "w") as handle:
            handle.write("# CPU_GOVERNOR=performance\n\nCPU_EPP=power\n")
        self.assertEqual(ledpanel.read_power_config(path),
                         {"CPU_GOVERNOR": "", "CPU_EPP": "power"})

    def test_the_panel_and_the_applier_read_it_the_same_way(self):
        # Two readers, one file. They disagreeing is the panel showing one
        # thing and the boot applying another.
        path = os.path.join(self.root, "c")
        with open(path, "w") as handle:
            handle.write('CPU_GOVERNOR="performance"\n# note\nCPU_EPP=\n')
        self.assertEqual(ledpanel.read_power_config(path),
                         self.applier.load(path))

    def test_the_shipped_file_sets_nothing(self):
        """An installation nobody has configured leaves the CPU alone."""
        shipped = os.path.join(HERE, "..", "server", "steamos-led-power.conf")
        self.assertEqual(ledpanel.read_power_config(shipped),
                         dict(power.DEFAULTS))


class MenuTest(FakeCpu):

    def test_leaving_it_alone_leads(self):
        offered = ledpanel.power_choices(power.governors(self.root))
        self.assertEqual(offered[0][1], power.UNSET)

    def test_every_value_the_machine_offers_is_in_the_menu(self):
        offered = ledpanel.power_choices(power.governors(self.root))
        self.assertEqual([value for _label, value in offered][1:],
                         ["performance", "powersave"])

    def test_a_configured_value_the_machine_lacks_is_kept_and_marked(self):
        # Dropping it would look like the setting had changed by itself.
        offered = ledpanel.power_choices(power.governors(self.root),
                                         current="ondemand")
        self.assertIn("ondemand", [value for _label, value in offered])
        label = dict((v, k) for k, v in offered)["ondemand"]
        self.assertIn("not offered", label)

    def test_the_wording_explains_the_kernel_s_own_words(self):
        # "powersave" reads like something for a battery, and on amd-pstate
        # active it is the ordinary setting - the one that lets the firmware
        # range at all.
        offered = ledpanel.power_choices(power.governors(self.root),
                                         labels=power.LABELS)
        labels = dict((value, label) for label, value in offered)
        self.assertIn("firmware", labels["powersave"])


class PinnedTest(unittest.TestCase):

    def test_the_performance_governor_is_the_one_that_pins_it(self):
        self.assertFalse(power.epp_applies(power.PINNED_GOVERNOR))

    def test_and_every_other_governor_lets_it_through(self):
        for governor in ("powersave", "schedutil", "ondemand", ""):
            self.assertTrue(power.epp_applies(governor), governor)


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
