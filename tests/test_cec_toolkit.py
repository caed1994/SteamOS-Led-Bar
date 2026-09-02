# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The CEC module: somebody else's work, forked, and now ours to keep whole.

It was vendored once, and the test here checked it was still an unmodified
copy. It is not one any more - five bugs in it are fixed in place, see
cec-toolkit/README.md - so the question has changed. Two things can still go
wrong quietly and both are checked:

Files get left out. The tree was taken as a subtree and installs itself from
inside; something the installer reaches for and nobody copied looks identical
here and only shows up as a broken install on a machine we cannot see.

The licence stops being findable. MIT is not ours to relabel and a fork with
no record of where it came from is code nobody can trace. The record is
ORIGIN, which is also what turns "take upstream's fixes" into a diff rather
than a guess.

Nothing here needs the network. What upstream says today is not knowable
offline; what our own tree says about itself is, and that is the half that
goes wrong.
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
        # MIT, and it stays MIT. This project is GPL-3.0-or-later, which can
        # carry MIT code - it cannot relicense somebody else's copyright, and
        # a fork does not change that.
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

        decky/ and assets/ were deliberately left out - the plugin is a second
        front end for the same helper and the assets are screenshots of it.
        Leaving out something the installer actually installs looks identical
        at fork time and only shows up as a broken install later.
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
        # install.sh copies these with `install -m 0755`, so the mode here is
        # not what lands on disk - but a file that is not executable in the
        # tree cannot be tried out where it is either, and trying things out
        # is why this tree is in the repository rather than fetched.
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
    """The five fixes, each of which was a workaround somewhere else first.

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
        # Before the wake paths, or it is registering after the fact.
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
        # And the udev rule must not pass it - udev kills a slow RUN+=.
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


class UsbWakeMatchTest(unittest.TestCase):
    """Which radios steamos-cec-usb-wake-apply is willing to wake from.

    It looks three ways - an exact vendor:product list, a regular expression
    over the device's name, and the USB class for Bluetooth - and on anything
    but a Steam Deck all three could miss. Measured on an AM5 board:

        0e8d:0616 MediaTek Inc. Wireless_Device
        class=ef sub=02 proto=01

    Not the Intel id the list carries. A Bluetooth radio whose name does not
    contain the word Bluetooth. And ef/02/01 is Interface Association - "my
    classes are in my interfaces" - which is what every wifi-and-Bluetooth
    combo chip says, so the *device* class check could never match one. The
    helper printed "matched":0 and gave no reason.

    This was worked around from outside for a while, by writing the id into
    USB_WAKE_USB_IDS at install time. The class check looks at the interfaces
    now, where the answer is.
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

        Somebody may have had the radio allowed to wake before any of this
        was installed, and turning the feature off is not a reason to take
        that away.
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
