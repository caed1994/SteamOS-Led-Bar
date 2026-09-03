# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Putting the CEC adapter on the bus: cec-toolkit/bin/steamos-cec-register.

The program belongs to the CEC module and not to this panel. It was a unit
of this project for some time, and it corrected a toolkit that registered
nothing. The correction belongs at the fault, so the program and its tests
moved there.

The tests are about care. Each exit that is not "claim an address that nobody
holds" must leave the adapter in its first condition. The daemon of Steam can
use the adapter. To take the bus from that daemon, and to correct a television
that is already on, is a worse fault than the first one.

No machine here has a CEC adapter, a television or cec-ctl, and the tests need
none of those. Each external call of the program is a keyword argument with a
real default.
"""

import contextlib
import io
import importlib.machinery
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.join(HERE, "..", "cec-toolkit", "bin", "steamos-cec-register")


def load():
    """The toolkit's program, imported despite having no .py on the end."""
    loader = importlib.machinery.SourceFileLoader("steamos_cec_register",
                                                  PROGRAM)
    spec = importlib.util.spec_from_loader("steamos_cec_register", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


register = load()


# What cec-ctl prints about an adapter, recorded from the machine this was
# found on: a working cable, a listening television on HDMI 3, and an adapter
# that is not on the bus.
UNREGISTERED = """\
Driver version           : 7.2.0
Available Logical Addresses: 4
DRM Connector Info       : card 0, connector 93
Physical Address         : 3.0.0.0
Logical Address Mask     : 0x0000
CEC Version              : 2.0
Logical Addresses        : 0
"""

REGISTERED = UNREGISTERED.replace(
    "Logical Address Mask     : 0x0000",
    "Logical Address Mask     : 0x0010").replace(
    "Logical Addresses        : 0",
    "Logical Addresses        : 1")

# f.f.f.f means "the position is not known". There is no link, or the
# machine did not read an EDID yet.
NO_PICTURE = UNREGISTERED.replace("3.0.0.0", "f.f.f.f")


class Answer:
    """What subprocess.run hands back, as much of it as this reads."""

    def __init__(self, returncode=0, stdout=""):
        self.returncode, self.stdout = returncode, stdout


class RegisterCase(unittest.TestCase):
    """The module state each test sets, put back afterwards.

    CONFIG and CEC_DEVICE are read from two files at import, which on a build
    machine means they are empty. Setting them is how a test says "the toolkit
    is configured like this"; TOOLKITCTL is how it says whether there is one.
    """

    def setUp(self):
        self.ran = []
        for name in ("CONFIG", "CEC_DEVICE", "TOOLKITCTL"):
            self._keep(name)
        # The address is already in the file, so most tests examine the
        # register step alone. TellTheToolkitTest clears it, because the
        # write of the address is its subject.
        register.CONFIG = {register.PHYSICAL_ADDRESS: "3.0.0.0"}
        register.CEC_DEVICE = "/dev/cec0"
        # An executable that exists, so the "is there a toolkit" check passes
        # without this test tree carrying a program of its own.
        register.TOOLKITCTL = sys.executable

    def _keep(self, name):
        was = getattr(register, name)
        self.addCleanup(lambda name=name, was=was: setattr(register, name, was))

    def _clock(self):
        at = [0.0]
        return (lambda: at[0],
                lambda seconds: at.__setitem__(0, at[0] + seconds), at)

    def _words(self):
        return [" ".join(row) for row in self.ran]


class ReadingTheAdapterTest(unittest.TestCase):
    """Reading whether the adapter is on the bus at all.

    The bug this is about: every wake path asks the adapter which logical
    address it holds, and when it holds none they send anyway from address 4 -
    which nothing owns, so the television has no reason to listen. Standby went
    out from the unregistered address as a broadcast and worked the whole time,
    which is why it looked like a television that would turn off but not on.
    """

    def test_an_adapter_with_no_address_is_read_as_such(self):
        self.assertIs(register.registered(UNREGISTERED), False)
        self.assertEqual(register.physical_address(UNREGISTERED), "3.0.0.0")

    def test_an_adapter_already_on_the_bus_is_read_as_such(self):
        self.assertIs(register.registered(REGISTERED), True)

    def test_an_adapter_with_no_picture_has_no_address(self):
        """Claiming a place against f.f.f.f would mean nothing on any bus."""
        self.assertEqual(register.physical_address(NO_PICTURE), "")

    def test_an_answer_this_does_not_understand_is_not_a_no(self):
        """None, and None must not read as "not registered".

        Anything else and a cec-ctl that words its report differently would
        have the adapter taken off whoever holds it, on every session start.
        """
        for text in ("", "cec-ctl: no such device",
                     "Physical Address : 3.0.0.0"):
            self.assertIsNone(register.registered(text), text)

    def test_the_count_answers_when_the_mask_is_missing(self):
        self.assertIs(register.registered("Logical Addresses        : 1"), True)
        self.assertIs(register.registered("Logical Addresses        : 0"), False)


class RegisterRunTest(RegisterCase):
    """One adapter, and what is done to it."""

    def _run(self, answers):
        def run(command, timeout=15):
            self.ran.append(list(command))
            for match, answer in answers:
                if match in command:
                    return answer
            return Answer(0, "")
        return run

    def _go(self, answers, devices=("/dev/cec0",), wait=3.0):
        now, sleep, _at = self._clock()
        return register.register(wait, devices=list(devices),
                                 run=self._run(answers), sleep=sleep, now=now,
                                 writable=lambda _path: True)

    def test_an_unregistered_adapter_is_put_on_the_bus(self):
        self.assertEqual(self._go([("-d", Answer(0, UNREGISTERED))]), 0)
        self.assertIn("--playback", self.ran[-1])
        # Named, because this is what the television shows as the source.
        self.assertIn(register.REGISTERED_NAME, self.ran[-1])

    def test_an_adapter_already_on_the_bus_is_only_read(self):
        self._go([("-d", Answer(0, REGISTERED))])
        self.assertEqual(self.ran, [["cec-ctl", "-d", "/dev/cec0"]])

    def test_reading_the_adapter_changes_nothing(self):
        """The report has to be a report: it runs before every decision."""
        self._go([("-d", Answer(0, REGISTERED))])
        for word in ("--playback", "--to", "--raw-msg", "-f"):
            self.assertNotIn(word, self.ran[0])

    def test_an_adapter_this_cannot_read_is_left_alone(self):
        self._go([("-d", Answer(1, "cec-ctl: cannot open /dev/cec0"))])
        self.assertEqual(len(self.ran), 1)

    def test_an_answer_it_does_not_understand_is_left_alone(self):
        self._go([("-d", Answer(0, "something else entirely"))])
        self.assertEqual(len(self.ran), 1)

    def test_it_waits_for_a_picture_and_then_gives_up(self):
        """An adapter with no link has nowhere to register against.

        The program waits for the link and does not refuse at once, because a
        television that starts gives a link after some seconds. The program
        then stops and does not wait without a limit, because the wake service
        waits for this unit.
        """
        self._go([("-d", Answer(0, NO_PICTURE))], wait=3.0)
        sent = [word for row in self.ran for word in row]
        self.assertNotIn("--playback", sent)
        self.assertGreater(len(self.ran), 1, "it did not wait at all")

    def test_a_picture_arriving_late_is_still_registered(self):
        answers = [Answer(0, NO_PICTURE), Answer(0, NO_PICTURE),
                   Answer(0, UNREGISTERED)]

        def run(command, timeout=15):
            self.ran.append(list(command))
            if "--playback" in command:
                return Answer(0, "")
            return answers.pop(0) if answers else Answer(0, UNREGISTERED)

        now, sleep, _at = self._clock()
        register.register(30.0, devices=["/dev/cec0"], run=run, sleep=sleep,
                          now=now, writable=lambda _path: True)
        self.assertIn("--playback", self.ran[-1])

    def test_a_machine_with_no_adapter_is_not_a_failure(self):
        """A machine can have the toolkit installed and no adapter plugged in."""
        self.assertEqual(self._go([], devices=()), 0)
        self.assertEqual(self.ran, [])

    def test_every_adapter_is_considered(self):
        self._go([("-d", Answer(0, UNREGISTERED))],
                 devices=("/dev/cec0", "/dev/cec1"))
        read = [row for row in self.ran
                if row[:1] == ["cec-ctl"] and "--playback" not in row]
        self.assertEqual(sorted({row[2] for row in read}),
                         ["/dev/cec0", "/dev/cec1"])


class AdapterAppearsLateTest(RegisterCase):
    """Reported: it works after unplugging the adapter and plugging it back.

    That report describes a race. A session start runs at the same time as the
    enumeration of the adapter. A run that looks one time, and finds nothing,
    then stops. The adapter never gets a registration, and only a removal and a
    new connection let a program see it.

    The program now looks more than one time, inside the same time budget as the
    physical address. The wake service therefore waits no longer than before.
    """

    def setUp(self):
        super().setUp()
        was = register.glob
        self.addCleanup(lambda: setattr(register, "glob", was))
        register.CONFIG = {register.PHYSICAL_ADDRESS: "1.0.0.0"}

    def _looks(self, appearances):
        seen = list(appearances)

        class Stub:
            @staticmethod
            def glob(_pattern):
                return seen.pop(0) if len(seen) > 1 else seen[0]

        register.glob = Stub

    def _go(self, appearances, wait=5.0, report=UNREGISTERED):
        self._looks(appearances)
        now, sleep, at = self._clock()

        def run(command, timeout=15):
            self.ran.append(list(command))
            return Answer(0, report if command[0] == "cec-ctl" else "")

        code = register.register(wait, run=run, sleep=sleep, now=now,
                                 writable=lambda _path: True)
        return code, at

    def test_an_adapter_that_turns_up_a_moment_late_is_still_registered(self):
        self._go([[], [], ["/dev/cec0"]])
        self.assertIn("--playback", self.ran[-1])

    def test_one_that_is_there_at_once_is_not_waited_for(self):
        self._go([["/dev/cec0"]])
        self.assertEqual(self.ran[0], ["cec-ctl", "-d", "/dev/cec0"])

    def test_saying_none_turned_up_says_how_long_it_waited(self):
        """The message that cost an evening said neither of the two things.

        "No CEC adapter on this machine" reads as a final result, and the unit
        printed it in its first second. A machine with a slow adapter therefore
        looked the same as a machine with no CEC, and the log gave no reason to
        look again.
        """
        with self.assertLogs(register.LOG, "WARNING") as said:
            self._go([[]], wait=3.0)
        whole = "\n".join(said.output)
        self.assertIn("3s", whole)
        self.assertIn("--wait", whole)

    def test_a_machine_with_no_adapter_gives_up_inside_the_budget(self):
        """The wake service waits for this unit, and the wait has no purpose."""
        with self.assertLogs(register.LOG, "WARNING"):
            code, _at = self._go([[]], wait=3.0)
        self.assertEqual(code, 0)
        self.assertEqual(self.ran, [])

    def test_the_budget_is_shared_with_the_wait_for_a_picture(self):
        """Both waits come out of one clock.

        Otherwise an adapter that appears late and then has no picture would
        hold the wake service for twice as long as the program promises.
        """
        with self.assertLogs(register.LOG, "WARNING"):
            _code, at = self._go([[], [], ["/dev/cec0"]], wait=5.0,
                                 report=NO_PICTURE)
        # One poll of slack: the loop checks the clock after sleeping.
        self.assertLessEqual(at[0], 5.0 + register.POLL_SECONDS,
                             "it waited past its own budget")


class HandItBackTest(RegisterCase):
    """The result of a removal and a new connection, and the conditions for it.

    This log from the reported machine shows the need:

        [10.0] Starting Repair SteamOS CEC device permissions...
        [10.1] Finished Repair SteamOS CEC device permissions.
        [12.4] kernel: Registered IR keymap rc-cec
        [12.5] cecd: Could not add device /dev/cec0: EACCES

    The permissions unit ran two seconds before the device existed, and its
    helper returned with no message and no device. The udev rule must also
    repair the permissions, and it lost a race with the daemon of Steam. That
    daemon reads the device one time and never reads it again. So no program
    held a logical address, and each wake went out from an incorrect address.
    """

    def setUp(self):
        super().setUp()
        register.CONFIG = {register.PHYSICAL_ADDRESS: "3.0.0.0"}
        self.states = [UNREGISTERED]

    def _run(self, command, timeout=15):
        self.ran.append(list(command))
        if command[0] == "cec-ctl" and "--playback" not in command:
            return Answer(0, self.states[0] if len(self.states) == 1
                          else self.states.pop(0))
        return Answer(0, "")

    def _go(self, wait=10.0, devices=("/dev/cec0",)):
        now, sleep, _at = self._clock()
        return register.register(wait, devices=list(devices), run=self._run,
                                 sleep=sleep, now=now,
                                 writable=lambda _path: False)

    def test_the_daemon_gets_another_go_before_we_claim_anything(self):
        """Its job, not ours. We are the last resort, not the first."""
        self._go()
        order = self._words()
        restart = next(i for i, row in enumerate(order) if "restart" in row)
        claim = next(i for i, row in enumerate(order) if "--playback" in row)
        self.assertLess(restart, claim)

    def test_the_permissions_are_repaired_through_the_toolkits_helper(self):
        self._go()
        self.assertIn("sudo -n " + register.PERMISSIONS_HELPER, self._words())

    def test_a_device_this_user_can_write_needs_no_repair(self):
        """Only the fault is repaired. Reapplying an ACL that is right is noise."""
        now, sleep, _at = self._clock()
        register.register(10.0, devices=["/dev/cec0"], run=self._run,
                          sleep=sleep, now=now, writable=lambda _path: True)
        self.assertNotIn("sudo -n " + register.PERMISSIONS_HELPER,
                         self._words())
        # The daemon still gets its go: nothing holds an address.
        self.assertTrue([row for row in self._words() if "restart" in row])

    def test_an_adapter_the_daemon_takes_is_then_left_alone(self):
        """The good ending: it was only ever cecd's to hold."""
        self.states = [UNREGISTERED, REGISTERED]
        self._go()
        self.assertNotIn("--playback",
                         [word for row in self.ran for word in row])

    def test_nothing_is_nudged_when_the_daemon_already_has_it(self):
        """Never restart a daemon that works correctly.

        This is the rule for each step of the escalation. The program does not
        touch an adapter that a program holds. Only an adapter that no program
        holds is a reason to stop a service.
        """
        self.states = [REGISTERED]
        self._go()
        self.assertEqual(self._words(), ["cec-ctl -d /dev/cec0"])

    def test_the_daemon_is_given_one_go_and_not_a_loop(self):
        """Otherwise a machine with no working adapter restarts it forever."""
        self._go(wait=10.0)
        self.assertEqual(
            len([row for row in self._words() if "restart" in row]), 1)

    def test_two_adapters_still_restart_the_one_daemon_once(self):
        """The permissions are per adapter; the daemon is not one of them."""
        self._go(devices=("/dev/cec0", "/dev/cec1"))
        self.assertEqual(
            len([row for row in self._words() if "restart" in row]), 1)
        self.assertEqual(
            len([row for row in self._words()
                 if register.PERMISSIONS_HELPER in row]), 2)


class TellTheToolkitTest(RegisterCase):
    """Filling in the address the toolkit ships without.

    The three wake paths broadcast <Active Source> only with this value. Without
    it they do not send that message, and the television starts and keeps its
    input. Only `discover-cec` writes the value, and the installer did not run
    that program.
    """

    def _go(self, config, devices=("/dev/cec0",), device="/dev/cec0",
            report=None, wait=3.0):
        register.CONFIG = config
        register.CEC_DEVICE = device
        now, sleep, _at = self._clock()

        def run(command, timeout=15):
            self.ran.append(list(command))
            if command[0] != "cec-ctl":
                return Answer(0, "")
            return Answer(0, report if report is not None else UNREGISTERED)

        return register.register(wait, devices=list(devices), run=run,
                                 sleep=sleep, now=now,
                                 writable=lambda _path: True)

    def _told(self):
        return [row for row in self.ran if "set-config" in row]

    def test_an_empty_address_is_filled_in_from_the_adapter(self):
        self._go({register.PHYSICAL_ADDRESS: ""})
        self.assertEqual(len(self._told()), 1)
        self.assertIn('"CEC_PHYSICAL_ADDRESS": "3.0.0.0"', self._told()[0][-1])

    def test_a_config_that_never_had_the_key_is_filled_in_too(self):
        self._go({})
        self.assertEqual(len(self._told()), 1)

    def test_an_address_already_there_is_not_written_over(self):
        """A value somebody chose is theirs, right or wrong."""
        self._go({register.PHYSICAL_ADDRESS: "2.0.0.0"})
        self.assertEqual(self._told(), [])

    def test_only_the_adapter_the_toolkit_is_set_to_use(self):
        """A machine with two adapters must not be told about the other one."""
        self._go({register.PHYSICAL_ADDRESS: ""}, devices=("/dev/cec0",),
                 device="/dev/cec1")
        self.assertEqual(self._told(), [])

    def test_an_adapter_already_on_the_bus_still_gets_its_address_told(self):
        """The two faults are independent, and so are their fixes.

        The program does not touch an adapter that the daemon of Steam
        registered. But the toolkit still does not know the position of the
        machine, and that value switches the input.
        """
        self._go({register.PHYSICAL_ADDRESS: ""}, report=REGISTERED)
        self.assertEqual(len(self._told()), 1)

    def test_an_adapter_with_no_picture_tells_nothing(self):
        """f.f.f.f is not an address, and writing it is worse than none."""
        self._go({register.PHYSICAL_ADDRESS: ""}, report=NO_PICTURE)
        self.assertEqual(self._told(), [])

    def test_no_toolkit_means_nothing_to_tell(self):
        register.TOOLKITCTL = "/nowhere/steamos-cec-toolkitctl"
        self._go({register.PHYSICAL_ADDRESS: ""})
        self.assertEqual(self._told(), [])


class SayingSoTest(RegisterCase):
    """What the log says, which is the only thing anybody sees.

    It runs at session start on a machine with no terminal open. A run that
    puts something right and then reports "nothing needed registering" is the
    log disowning what it just did, and a run that says nothing at all is
    indistinguishable from one that never started.
    """

    def _go(self, report, writable=True):
        now, sleep, _at = self._clock()

        def run(command, timeout=15):
            self.ran.append(list(command))
            return Answer(0, report if command[0] == "cec-ctl" else "")

        with self.assertLogs(register.LOG) as said:
            register.register(3.0, devices=["/dev/cec0"], run=run, sleep=sleep,
                              now=now, writable=lambda _path: writable)
        return "\n".join(said.output)

    def test_an_adapter_somebody_else_holds_is_a_quiet_good_ending(self):
        """Not a failure, and said as one line rather than none.

        An adapter cecd already has is the outcome this program most wants;
        exiting non-zero for it would paint the session red for doing the
        right thing.
        """
        said = self._go(REGISTERED)
        self.assertIn("already on the bus", said)
        self.assertIn("Nothing needed registering", said)

    def test_a_run_that_did_something_does_not_say_it_did_nothing(self):
        """The log disowning what it has just done is worse than a quiet one."""
        said = self._go(UNREGISTERED)
        self.assertIn("registered at 3.0.0.0", said)
        self.assertNotIn("Nothing needed registering", said)

    def test_repairing_the_permissions_counts_as_having_done_something(self):
        register.CONFIG = {register.PHYSICAL_ADDRESS: "3.0.0.0"}
        said = self._go(UNREGISTERED, writable=False)
        self.assertIn("repaired its permissions", said)
        self.assertNotIn("Nothing needed registering", said)


class CommandLineTest(unittest.TestCase):
    """--wait, which is the answer to "is my adapter merely slow?"."""

    def _wait(self, argv):
        was = sys.argv
        sys.argv = ["steamos-cec-register"] + argv
        self.addCleanup(lambda: setattr(sys, "argv", was))
        seen = []
        real = register.register
        register.register = lambda wait, **rest: seen.append(wait) or 0
        self.addCleanup(lambda: setattr(register, "register", real))
        register.main()
        return seen[0]

    def test_the_default_is_the_budget_the_unit_promises(self):
        self.assertEqual(self._wait([]), register.DEFAULT_WAIT_SECONDS)

    def test_a_longer_one_can_be_asked_for_by_hand(self):
        self.assertEqual(self._wait(["--wait", "60"]), 60.0)

    def test_a_bad_one_is_refused_rather_than_taken_as_zero(self):
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            with self.assertRaises(SystemExit):
                self._wait(["--wait", "soon"])


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
