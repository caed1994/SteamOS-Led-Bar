# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The CPU governor and the EPP, against a sysfs built here.

No test uses the real sysfs. The machine of the suite has its own cpufreq,
or it has none. A test that reads the real sysfs gives an answer from the
build machine. A test that writes it changes how the build machine runs.
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
from steamos_utility_center import power                               # noqa: E402


def _applier():
    """Returns the installed script. It has no .py suffix, so this loads it by
        path.
        """
    path = os.path.join(HERE, "..", "server", "steamos-utility-center-power")
    loader = importlib.machinery.SourceFileLoader("steamos_utility_center_power", path)
    spec = importlib.util.spec_from_loader("steamos_utility_center_power", loader)
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
        # The active mode and the passive mode offer different governors, and
        # only one of the two has an EPP. The mode therefore decides the rows
        # of the page.
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
        # setting and there is nothing to check. See epp_in_play.
        with self.assertRaises(ValueError):
            power.validate({"CPU_GOVERNOR": "powersave", "CPU_EPP": "turbo"},
                           self.root)

    def test_an_epp_without_a_governor_is_not_checked_at_all(self):
        # This is not an error. The applier does not write it, so a refusal
        # refuses a value with no result.
        power.validate({"CPU_GOVERNOR": "", "CPU_EPP": "turbo"}, self.root)

    def test_the_message_names_what_is_on_offer(self):
        # So the refusal itself lists the values that the machine accepts.
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

        Under `performance` the kernel fixes the firmware at its highest
        preference, and the EPP file accepts no other value. A write of another
        value gives a refusal, and the applier reports that as a failure. The
        setting itself was correct.
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
    """A user reported a refused setting, one step after a governor change.

    The kernel reduces energy_performance_available_preferences to one value
    under the performance governor. After a run with that governor, a user
    selected powersave and a preference together. The check then used a list of
    one value and refused the pair. The pair was valid, and the state of the
    machine before the change caused the refusal.
    """

    def setUp(self):
        super().setUp()
        # The state of the machine after a run with `performance`.
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

        This test sets the pair and then sets the governor back to "leave it
        to SteamOS". The preference stays in the configuration file, because
        the panel hides the row and does not clear it. The applier must stop
        the write of that value. Without that, the setting stays active and
        nothing on the screen shows it.
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

    intel_pstate in its active mode offers the same two governors and the same
    five preferences as amd-pstate. It also fixes the preference under the same
    governor. So each function here works on it. This test proves that, and a
    comment does not claim it.

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

        A refusal refuses a configuration that is correct on its own machine.
        This machine also has no file for the value.
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
        """An install with no settings does not change the CPU."""
        shipped = os.path.join(HERE, "..", "server", "steamos-utility-center-power.conf")
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
        # Without the entry, the setting looks different for no reason.
        offered = ledpanel.power_choices(power.governors(self.root),
                                         current="ondemand")
        self.assertIn("ondemand", [value for _label, value in offered])
        label = dict((v, k) for k, v in offered)["ondemand"]
        self.assertIn("not offered", label)

    def test_the_wording_explains_the_kernel_s_own_words(self):
        # The name "powersave" reads as a setting for a battery. On amd-pstate
        # in active mode it is the normal setting, and it is the one that gives
        # the firmware a range.
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
