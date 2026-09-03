# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The CEC module: the work of another project, forked, and kept complete.

This tree was a copy at one time, and the test here proved that the copy was
unchanged. It is no longer a copy, because this project corrected five faults
in it. See cec-toolkit/README.md. So the question is different now. Two
faults can occur with no message, and this file checks both.

A file goes missing. This project took the tree as a subtree, and the tree
installs itself from inside. A file that the installer needs, and that nobody
copied, looks correct here. It gives a broken install on a machine of a user.

The licence becomes difficult to find. This project cannot change the MIT
licence, and a fork with no record of its source is code with no history. The
record is ORIGIN. That file also makes "take the corrections of the source
project" a diff and not a guess.

No test here needs the network. The current state of the source project is
not available offline. The state of this tree is available, and this tree is
the half with the faults.
"""

import json
import os
import re
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
CEC = os.path.join(REPO, "cec-toolkit")


def record(where=CEC):
    """The ORIGIN file, read the way a shell would read it."""
    values = {}
    with open(os.path.join(where, "ORIGIN")) as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
    return values


class ProvenanceTest(unittest.TestCase):
    """Where it came from, in a form somebody can act on."""

    def setUp(self):
        self.record = record()

    def test_it_names_where_it_came_from(self):
        self.assertTrue(self.record["ORIGIN_URL"].startswith("https://"))
        self.assertIn("steamos-cec-toolkit", self.record["ORIGIN_URL"])

    def test_it_names_a_commit_and_not_a_branch(self):
        # A branch name moves, so a fork recorded against one has no fixed
        # thing to be diffed from. Tags can be moved too, which is why the
        # commit is recorded beside the tag rather than instead of it.
        self.assertRegex(self.record["ORIGIN_COMMIT"], r"^[0-9a-f]{40}$")

    def test_the_version_says_it_is_not_upstream_any_more(self):
        # VERSION is what this tree calls itself, and it is installed and
        # reported as the toolkit's version. Reporting a bare upstream tag
        # would say this is a copy of a release it no longer is, which is the
        # one thing a bug report must not be wrong about.
        with open(os.path.join(CEC, "VERSION")) as handle:
            said = handle.read().strip()
        self.assertTrue(said.startswith(self.record["ORIGIN_TAG"]), said)
        self.assertNotEqual(said, self.record["ORIGIN_TAG"], said)

    def test_it_keeps_the_licence_it_arrived_under(self):
        # MIT, and it stays MIT. This project is GPL-3.0-or-later, and that
        # licence can hold MIT code. It cannot change the licence of another
        # copyright holder, and a fork does not change that rule.
        with open(os.path.join(CEC, "LICENSE")) as handle:
            said = handle.read()
        self.assertIn("MIT License", said)
        self.assertIn("contributors", said)     # upstream's, still there
        self.assertIn("caed1994", said)         # and ours, for the changes

    def test_its_readme_says_it_is_a_fork_and_whose(self):
        with open(os.path.join(CEC, "README.md")) as handle:
            said = handle.read()
        self.assertIn("fork", said.lower())
        self.assertIn("Twsts/steamos-cec-toolkit", said)
        self.assertIn("MIT", said)


class CompleteTest(unittest.TestCase):
    """Taking a subtree is where files get lost."""

    def _installers(self):
        for name in ("install.sh", "uninstall.sh"):
            with open(os.path.join(CEC, name)) as handle:
                yield name, handle.read()

    def test_every_file_the_installer_reaches_for_is_here(self):
        """The failure this is about happens on somebody else's machine.

        This project left out decky/ and assets/ on purpose. The plugin is a
        second front end for the same helper, and the assets are screenshots of
        that plugin. A file that the installer installs, and that this tree does
        not have, looks correct at the fork and gives a broken install later.
        """
        wanted = set()
        for _name, text in self._installers():
            wanted.update(re.findall(r"\$PROJECT_DIR/([A-Za-z0-9_./-]+)", text))
        self.assertTrue(wanted, "found no files to check - has the "
                                "installer stopped using $PROJECT_DIR?")
        for each in sorted(wanted):
            self.assertTrue(os.path.exists(os.path.join(CEC, each)),
                            "the installer installs %s and it is not here"
                            % each)

    def test_nothing_here_refers_to_what_was_left_out(self):
        for where, _dirs, files in os.walk(CEC):
            for name in files:
                if name in ("ORIGIN", "README.md"):
                    continue            # the two files whose job is to say so
                path = os.path.join(where, name)
                with open(path, "rb") as handle:
                    text = handle.read().decode("utf-8", "replace")
                for gone in ("$PROJECT_DIR/decky", "$PROJECT_DIR/assets"):
                    self.assertNotIn(gone, text, "%s wants %s" % (path, gone))

    def test_nothing_here_sends_people_to_upstreams_installer(self):
        """It would put the unfixed programs back.

        The docs said "repair by rerunning the latest installer" and pointed
        at upstream's release. That was right while this was an unmodified
        copy and is now a way to undo every fix in here.
        """
        for where, _dirs, files in os.walk(CEC):
            for name in files:
                if name == "ORIGIN":
                    continue            # names the URL, tells nobody to run it
                path = os.path.join(where, name)
                with open(path, "rb") as handle:
                    text = handle.read().decode("utf-8", "replace")
                self.assertNotIn("steamos-cec-toolkit-installer.sh", text,
                                 "%s sends people to upstream's installer"
                                 % path)

    def test_the_programs_it_installs_can_be_run(self):
        # install.sh copies these with `install -m 0755`, so this mode is not
        # the mode on disk. But a file in this tree with no execute bit cannot
        # run here. A developer must be able to run it here, and that is the
        # reason this tree is in the repository.
        for name in sorted(os.listdir(os.path.join(CEC, "bin"))):
            self.assertTrue(os.access(os.path.join(CEC, "bin", name), os.X_OK),
                            "%s is not executable" % name)

    def test_the_shell_in_it_parses(self):
        for name, _text in self._installers():
            done = subprocess.run(["bash", "-n", os.path.join(CEC, name)],
                                  capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)


class InstalledAndRemovedTest(unittest.TestCase):
    """Every user file the installer writes, the uninstaller takes away.

    A unit left behind is not inert: it is enabled, so systemd goes on
    starting it at every login with the program it names already deleted.
    Both halves of that were true of boot-wake before the fork.
    """

    def setUp(self):
        with open(os.path.join(CEC, "install.sh")) as handle:
            self.install = handle.read()
        with open(os.path.join(CEC, "uninstall.sh")) as handle:
            self.uninstall = handle.read()

    def _installed_into_home(self, under, suffix=""):
        # Ends with the suffix, so `install -d` of a drop-in *directory*
        # called cec-audio-control.service.d is not mistaken for a unit.
        return set(name for name
                   in re.findall(r'"\$HOME/%s/([A-Za-z0-9_.-]+)"' % under,
                                 self.install)
                   if name.endswith(suffix))

    def test_every_user_program_is_removed_again(self):
        for name in sorted(self._installed_into_home(r"\.local/bin")):
            self.assertIn('rm -f "$HOME/.local/bin/%s"' % name,
                          self.uninstall, name)

    def test_every_user_unit_is_removed_again(self):
        for name in sorted(self._installed_into_home(r"\.config/systemd/user",
                                          ".service")):
            self.assertIn('rm -f "$HOME/.config/systemd/user/%s"' % name,
                          self.uninstall, name)

    def test_every_user_unit_is_disabled_before_it_is_deleted(self):
        # A deleted unit file leaves its enable symlink behind, and systemd
        # goes on trying to start something that is not there.
        for name in sorted(self._installed_into_home(r"\.config/systemd/user",
                                          ".service")):
            self.assertIn("systemctl --user disable --now %s" % name,
                          self.uninstall, name)


class FixedHereTest(unittest.TestCase):
    """The six fixes, each of which was a workaround somewhere else first.

    Checked because each one is a single line or two in a file nobody reads
    often, and each one silently un-breaks a whole feature. A revert that
    passes every other test in this repository would be caught only here.
    """

    def _read(self, *parts):
        with open(os.path.join(CEC, *parts)) as handle:
            return handle.read()

    def test_something_puts_the_adapter_on_the_bus(self):
        program = self._read("bin", "steamos-cec-register")
        self.assertIn("--playback", program)
        self.assertIn("Logical Address Mask", program)
        unit = self._read("systemd", "user", "steamos-cec-register.service")
        # Before the wake paths. After them, the registration is too late.
        self.assertIn("Before=steamos-cec-boot-wake.service", unit)
        # oneshot, or Before= does not mean "finished before".
        self.assertIn("Type=oneshot", unit)
        # And installed and enabled for everybody: it is not a feature.
        install = self._read("install.sh")
        self.assertIn("systemctl --user enable steamos-cec-register.service",
                      install)

    def test_the_boot_wake_does_not_hold_the_session_up(self):
        unit = self._read("systemd", "user", "steamos-cec-boot-wake.service")
        self.assertIn("Type=simple", unit)
        self.assertNotIn("Type=oneshot", unit)

    def test_the_permissions_helper_waits_for_the_device(self):
        helper = self._read("bin", "steamos-cec-permissions-apply")
        self.assertIn("--wait", helper)
        unit = self._read("systemd", "system",
                          "steamos-cec-permissions.service")
        self.assertIn("--wait", unit)
        # But not as a oneshot: waiting there would hold multi-user.target.
        self.assertIn("Type=simple", unit)
        # The udev rule must not pass it, because udev stops a slow RUN+=.
        self.assertNotIn("--wait", self._read("udev",
                                              "70-steamos-cec-toolkit.rules"))

    def test_the_installer_works_out_where_the_machine_is_plugged_in(self):
        # CEC_PHYSICAL_ADDRESS is what lets a wake broadcast <Active Source>,
        # which is the message that switches the television's input over.
        # Nothing used to write it, so waking turned the set on and left it
        # where it was.
        self.assertIn("CEC_PHYSICAL_ADDRESS",
                      self._read("bin", "steamos-cec-register"))
        self.assertIn("steamos-cec-register", self._read("install.sh"))

    def test_usb_wake_looks_at_the_interfaces_too(self):
        helper = self._read("bin", "steamos-cec-usb-wake-apply")
        self.assertIn("bInterfaceClass", helper)
        self.assertIn("has_bluetooth_interface", helper)

    def test_the_standby_before_sleep_runs_once(self):
        """The unit or the system-sleep hook, and never both.

        The switch installed both, and both run the same helper. systemd ran
        the unit before sleep.target and then the hook from
        systemd-suspend.service, so each suspend sent the standby twice and
        cost twice the time. A shutdown ran the unit only, because systemd
        runs no system-sleep hook there.
        """
        control = self._read("bin", "steamos-cec-power-standby-control")
        # The unit is the one that stays: it covers a suspend and a shutdown.
        self.assertIn('systemctl enable "$UNIT"', control)
        self.assertNotIn('ln -sf "$HELPER" "$HOOK"', control)
        unit = self._read("systemd", "system",
                          "steamos-cec-before-sleep.service")
        self.assertIn("WantedBy=sleep.target shutdown.target", unit)

    def test_a_hook_from_an_earlier_install_is_taken_away(self):
        """Or the second run continues on a machine that updates.

        Three places remove it: the switch, so turning the feature on repairs
        the machine; the installer, so an update repairs it without the
        switch; and the uninstaller, which named a path this toolkit never
        wrote and left the hook as a symlink to a helper that was gone.
        """
        hook = "/etc/systemd/system-sleep/steamos-cec-before-sleep"
        control = self._read("bin", "steamos-cec-power-standby-control")
        self.assertEqual(control.count('rm -f "$HOOK"'), 2, "on and off")
        self.assertIn('HOOK="%s"' % hook, control)
        for name in ("install.sh", "uninstall.sh"):
            self.assertIn("rm -f %s" % hook, self._read(name), name)

    def test_the_settle_time_is_not_two_seconds_on_every_suspend(self):
        """It is on each suspend and each shutdown, so it is short.

        cec-ctl returns once the adapter sent the message, and a television
        acknowledges CEC in milliseconds. The wait is for the set to act on
        it before the HDMI link goes away.
        """
        helper = self._read("bin", "steamos-cec-before-sleep")
        self.assertIn("TV_STANDBY_SETTLE_SECONDS:-0.5", helper)
        # And named in the file, or nobody can raise it for a slow set.
        self.assertIn("TV_STANDBY_SETTLE_SECONDS",
                      self._read("config",
                                 "steamos-cec-toolkit.conf.example"))

    def test_the_bus_calls_cannot_hold_the_suspend(self):
        """busctl waits 25 seconds for a method call by default.

        Two calls go to the cecd of Steam. A daemon that is not there fails at
        once, and a daemon that stopped answering held the suspend for 50
        seconds. The calls are an attempt: the cec-ctl messages below them do
        the same work.
        """
        helper = self._read("bin", "steamos-cec-before-sleep")
        self.assertEqual(helper.count("busctl --user --timeout="), 2)
        self.assertNotIn("busctl --user call", helper)


class UsbWakeMatchTest(unittest.TestCase):
    """The radios that steamos-cec-usb-wake-apply accepts for a wake.

    It uses three methods: an exact vendor:product list, a regular expression
    for the name of the device, and the USB class for Bluetooth. On a machine
    that is not a Steam Deck, all three can fail. A measurement on an AM5 board
    gave this:

        0e8d:0616 MediaTek Inc. Wireless_Device
        class=ef sub=02 proto=01

    That id is not the Intel id in the list. The name of that Bluetooth radio
    does not hold the word Bluetooth. And ef/02/01 is Interface Association,
    which means "the classes are in the interfaces". Each combination wifi and
    Bluetooth chip reports that class, so a check of the *device* class never
    matched one. The helper printed "matched":0 and gave no reason.

    This project corrected that from the outside for some time, and wrote the id
    into USB_WAKE_USB_IDS at the install step. The class check now reads the
    interfaces, which hold the answer.
    """

    HELPER = os.path.join(CEC, "bin", "steamos-cec-usb-wake-apply")

    BLUETOOTH = ("e0", "01", "01")
    KEYBOARD = ("03", "01", "01")
    HUB = ("09", "00", "00")

    def _machine(self, where, devices):
        """A fake /sys/bus/usb/devices from {id: (name, [(class, sub, proto)])}.

        Interfaces are children of the device, which is where they really
        live: /sys/bus/usb/devices/1-12:1.0 is a symlink to a directory inside
        the one 1-12 points at. Modelling them as siblings would pass a test
        the real bus fails.
        """
        usb = os.path.join(where, "usb")
        for index, (usb_id, (name, interfaces)) in enumerate(devices.items()):
            port = "1-%d" % (index + 1)
            at = os.path.join(usb, port)
            os.makedirs(os.path.join(at, "power"), exist_ok=True)
            vendor, product = usb_id.split(":")
            written = {"idVendor": vendor, "idProduct": product,
                       "product": name, "manufacturer": "",
                       "bDeviceClass": "ef", "bDeviceSubClass": "02",
                       "bDeviceProtocol": "01", "power/wakeup": "disabled"}
            for leaf, value in written.items():
                with open(os.path.join(at, leaf), "w") as handle:
                    handle.write(value + "\n")
            for number, (klass, sub, proto) in enumerate(interfaces):
                inside = os.path.join(at, "%s:1.%d" % (port, number))
                os.makedirs(inside, exist_ok=True)
                for leaf, value in (("bInterfaceClass", klass),
                                    ("bInterfaceSubClass", sub),
                                    ("bInterfaceProtocol", proto)):
                    with open(os.path.join(inside, leaf), "w") as handle:
                        handle.write(value + "\n")
        return usb

    def _ask(self, devices, action="status"):
        with tempfile.TemporaryDirectory() as where:
            usb = self._machine(where, devices)
            done = subprocess.run(
                ["bash", self.HELPER, action], capture_output=True, text=True,
                env=dict(os.environ, USB_WAKE_SYSFS=usb,
                         STEAMOS_CEC_CONFIG=os.path.join(where, "none"),
                         USB_WAKE_STATE_FILE=os.path.join(where, "state")))
            self.assertEqual(done.returncode, 0, done.stderr)
            return json.loads(done.stdout), usb

    def test_a_combo_chip_is_found_by_its_interface(self):
        """The whole point: the class it hides is one level down."""
        said, _usb = self._ask(
            {"0e8d:0616": ("Wireless_Device", [self.BLUETOOTH])})
        self.assertEqual(said["matched"], 1)
        self.assertIn("0e8d:0616", said["devices"][0]["label"])

    def test_two_bluetooth_interfaces_on_one_chip_are_one_device(self):
        """A radio usually has several. It is still one thing to allow."""
        said, _usb = self._ask(
            {"0e8d:0616": ("Wireless_Device",
                           [self.BLUETOOTH, self.BLUETOOTH])})
        self.assertEqual(said["matched"], 1)

    def test_an_interface_that_is_not_bluetooth_is_not_enough(self):
        """A keyboard that wakes the machine is a machine that wakes itself."""
        said, _usb = self._ask({"24ae:9db6": ("Keyboard", [self.KEYBOARD])})
        self.assertEqual(said["matched"], 0)

    def test_a_hub_is_not_a_radio(self):
        said, _usb = self._ask({"1d6b:0002": ("xHCI Host Controller",
                                              [self.HUB])})
        self.assertEqual(said["matched"], 0)

    def test_a_machine_with_no_radio_says_so_rather_than_nothing(self):
        said, _usb = self._ask({})
        self.assertEqual(said["matched"], 0)
        self.assertEqual(said["devices"], [])

    def test_switching_it_on_and_off_again_puts_the_setting_back(self):
        """`off` restores what was there, it does not force "disabled".

        A user can have a radio with wake permission from before this install.
        A change of this function to off is not a reason to remove that
        permission.
        """
        with tempfile.TemporaryDirectory() as where:
            usb = self._machine(
                where, {"0e8d:0616": ("Wireless_Device", [self.BLUETOOTH])})
            wakeup = os.path.join(usb, "1-1", "power", "wakeup")
            env = dict(os.environ, USB_WAKE_SYSFS=usb,
                       STEAMOS_CEC_CONFIG=os.path.join(where, "none"),
                       USB_WAKE_STATE_FILE=os.path.join(where, "state"))
            on = subprocess.run(["bash", self.HELPER, "on"],
                                capture_output=True, text=True, env=env)
            self.assertEqual(on.returncode, 0, on.stderr)
            with open(wakeup) as handle:
                self.assertEqual(handle.read().strip(), "enabled")
            off = subprocess.run(["bash", self.HELPER, "off"],
                                 capture_output=True, text=True, env=env)
            self.assertEqual(off.returncode, 0, off.stderr)
            with open(wakeup) as handle:
                self.assertEqual(handle.read().strip(), "disabled")

    def test_the_bus_it_reads_can_be_pointed_somewhere_else(self):
        """Which is the only reason any of the above can be tested at all."""
        with open(self.HELPER) as handle:
            self.assertIn('USB_WAKE_SYSFS="${USB_WAKE_SYSFS:-'
                          '/sys/bus/usb/devices}"', handle.read())


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
