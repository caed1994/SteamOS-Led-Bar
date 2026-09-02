# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The bridge between a window with no terminal and an installer wanting sudo.

The toolkit's installer refuses to run as root and calls `sudo` about forty
times. This script is what lets a GUI drive it: it runs as root through
pkexec, drops back to the desktop user, and lends that user a sudo rule for
the length of the install.

The rule is the part worth testing hardest. A malformed file in
/etc/sudoers.d takes sudo itself down for the whole machine, and one left
behind is a grant nobody asked to keep - so the checks here are that it is
valid before it is installed, and gone afterwards on every path out.

Most of this only runs as root, because the thing being tested is a program
whose first act is to check that it is root. The decision tests above that
line run anywhere.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "install-cec.sh")
RULE = "/etc/sudoers.d/zz-steamos-utility-center-cec-install"

# Somebody who is not root and is not the user running the tests, to install
# for. Read from the machine rather than named, so this does not depend on a
# particular distribution's accounts.
def _somebody():
    for name in ("ubuntu", "deck", "claude", "nobody"):
        try:
            import pwd
            entry = pwd.getpwnam(name)
        except KeyError:
            continue
        if entry.pw_uid and os.path.isdir(entry.pw_dir):
            return name
    return ""


SOMEBODY = _somebody()
AS_ROOT = unittest.skipUnless(os.geteuid() == 0, "needs root")
HAS_USER = unittest.skipUnless(SOMEBODY, "no ordinary user with a home here")


def run(*args, **kwargs):
    return subprocess.run(["bash", SCRIPT] + list(args), capture_output=True,
                          text=True, **kwargs)


class ArgumentTest(unittest.TestCase):
    """What it refuses before it does anything at all."""

    def test_resume_wake_wants_on_or_off(self):
        # The second argument is a state here, not a directory, and anything
        # else is a switch that would silently do nothing.
        self.assertEqual(run("resume-wake").returncode, 2)
        self.assertEqual(run("resume-wake", "/some/dir").returncode, 2)

    @AS_ROOT
    def test_resume_wake_says_so_when_the_unit_is_not_installed(self):
        with tempfile.TemporaryDirectory() as root:
            done = run("resume-wake", "on", env=dict(os.environ, ROOT=root))
        self.assertEqual(done.returncode, 1)
        self.assertIn("install the CEC toolkit first", done.stderr)

    @AS_ROOT
    def test_resume_wake_enables_the_unit_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as room:
            root = os.path.join(room, "root")
            os.makedirs(os.path.join(root, "etc/systemd/system"))
            with open(os.path.join(root, "etc/systemd/system",
                                   "steamos-cec-resume-wake.service"),
                      "w") as handle:
                handle.write("[Unit]\n")
            stubs = os.path.join(room, "stubs")
            os.makedirs(stubs)
            log = os.path.join(room, "calls")
            with open(os.path.join(stubs, "systemctl"), "w") as handle:
                handle.write('#!/usr/bin/env bash\necho "$*" >> "%s"\n' % log)
            os.chmod(os.path.join(stubs, "systemctl"), 0o755)
            done = run("resume-wake", "on",
                       env=dict(os.environ, ROOT=root,
                                PATH=stubs + ":" + os.environ["PATH"]))
            self.assertEqual(done.returncode, 0, done.stderr)
            with open(log) as handle:
                self.assertEqual(handle.read().split("\n")[0],
                                 "enable steamos-cec-resume-wake.service")

    def test_it_parses(self):
        done = subprocess.run(["bash", "-n", SCRIPT], capture_output=True,
                              text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_no_arguments_is_the_usage(self):
        done = run()
        self.assertEqual(done.returncode, 2)
        self.assertIn("usage:", done.stderr)

    def test_an_action_it_does_not_have_is_refused(self):
        # Not treated as one of the two it knows. "reinstall" reaching the
        # install path would be an install nobody asked for.
        self.assertEqual(run("reinstall", "/tmp").returncode, 2)

    def test_it_wants_somewhere_to_install_from(self):
        self.assertEqual(run("install").returncode, 2)


class BluetoothWakeIdTest(unittest.TestCase):
    """Finding the radio a controller talks to, which the toolkit misses.

    It looks for one three ways - an exact vendor:product list, a regex over
    the device's name, and the Bluetooth USB class - and on anything but a
    Steam Deck all three miss. Measured on an AM5 board:

        0e8d:0616 MediaTek Inc. Wireless_Device
        class=ef sub=02 proto=01

    Not the Intel id the list carries. A Bluetooth radio whose name does not
    contain the word Bluetooth. And ef/02/01 is Interface Association - "my
    classes are in my interfaces" - which is what every wifi-and-bluetooth
    combo chip says, so the class check cannot match one. The toolkit printed
    "matched":0 and no reason.
    """

    def _machine(self, where, devices):
        """A fake /sys/bus/usb/devices, from {id: [(class, sub, proto)]}."""
        usb = os.path.join(where, "usb")
        for index, (usb_id, interfaces) in enumerate(devices.items()):
            name = "1-%d" % (index + 1)
            vendor, product = usb_id.split(":")
            os.makedirs(os.path.join(usb, name), exist_ok=True)
            for leaf, value in (("idVendor", vendor), ("idProduct", product)):
                with open(os.path.join(usb, name, leaf), "w") as handle:
                    handle.write(value)
            for number, (klass, sub, proto) in enumerate(interfaces):
                at = os.path.join(usb, "%s:1.%d" % (name, number))
                os.makedirs(at, exist_ok=True)
                for leaf, value in (("bInterfaceClass", klass),
                                    ("bInterfaceSubClass", sub),
                                    ("bInterfaceProtocol", proto)):
                    with open(os.path.join(at, leaf), "w") as handle:
                        handle.write(value)
        return usb

    def _config(self, where, text):
        at = os.path.join(where, "root", "etc")
        os.makedirs(at, exist_ok=True)
        with open(os.path.join(at, "steamos-cec-toolkit.conf"), "w") as handle:
            handle.write(text)
        return os.path.join(where, "root")

    def _ids(self, root):
        path = os.path.join(root, "etc", "steamos-cec-toolkit.conf")
        with open(path) as handle:
            for line in handle:
                if line.startswith("USB_WAKE_USB_IDS="):
                    return line.split("=", 1)[1].strip().strip('"')
        return None

    def _run(self, where, devices, config='USB_WAKE_USB_IDS="8087:0032"\n'):
        usb = self._machine(where, devices)
        root = self._config(where, config)
        done = run("wake-ids", env=dict(os.environ, SYSFS_USB=usb, ROOT=root))
        return done, root

    BLUETOOTH = ("e0", "01", "01")
    KEYBOARD = ("03", "01", "01")

    @AS_ROOT
    def test_a_combo_chip_is_found_by_its_interface(self):
        """The whole point: the class it hides is one level down."""
        with tempfile.TemporaryDirectory() as where:
            done, root = self._run(where, {"0e8d:0616": [self.BLUETOOTH]})
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(self._ids(root), "8087:0032 0e8d:0616")

    @AS_ROOT
    def test_what_is_already_there_is_kept(self):
        with tempfile.TemporaryDirectory() as where:
            _done, root = self._run(where, {"0e8d:0616": [self.BLUETOOTH]})
            self.assertIn("8087:0032", self._ids(root))

    @AS_ROOT
    def test_running_it_twice_adds_nothing_the_second_time(self):
        with tempfile.TemporaryDirectory() as where:
            _done, root = self._run(where, {"0e8d:0616": [self.BLUETOOTH]})
            once = self._ids(root)
            usb = os.path.join(where, "usb")
            again = run("wake-ids",
                        env=dict(os.environ, SYSFS_USB=usb, ROOT=root))
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertEqual(self._ids(root), once)
            self.assertIn("already allowed", again.stdout)
            # And it names what it found, so "did it find mine?" has an
            # answer even on the run where there was nothing left to do.
            self.assertIn("0e8d:0616", again.stdout)

    @AS_ROOT
    def test_two_bluetooth_interfaces_on_one_chip_are_one_id(self):
        """A radio usually has several. It is still one device to allow."""
        with tempfile.TemporaryDirectory() as where:
            _done, root = self._run(
                where, {"0e8d:0616": [self.BLUETOOTH, self.BLUETOOTH]})
            self.assertEqual(self._ids(root), "8087:0032 0e8d:0616")

    @AS_ROOT
    def test_nothing_else_on_the_bus_is_allowed_to_wake_anything(self):
        """A keyboard that wakes the machine is a machine that wakes itself."""
        with tempfile.TemporaryDirectory() as where:
            _done, root = self._run(where, {"24ae:9db6": [self.KEYBOARD]})
            self.assertEqual(self._ids(root), "8087:0032")

    @AS_ROOT
    def test_finding_none_is_not_reported_as_nothing_left_to_do(self):
        """The two were one sentence, and they are opposite answers.

        A machine where this does not work at all read exactly like one where
        every radio was already listed - which is no way to answer "did it
        find mine?".
        """
        with tempfile.TemporaryDirectory() as where:
            done, root = self._run(where, {})
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("No Bluetooth radio", done.stdout)
            self.assertNotIn("already allowed", done.stdout)
            self.assertEqual(self._ids(root), "8087:0032")

    @AS_ROOT
    def test_it_names_what_it_found_whether_or_not_it_changed_anything(self):
        with tempfile.TemporaryDirectory() as where:
            done, _root = self._run(where, {"0e8d:0616": [self.BLUETOOTH]})
            self.assertIn("Found 0e8d:0616", done.stdout)

    @AS_ROOT
    def test_a_config_without_the_key_gets_one(self):
        with tempfile.TemporaryDirectory() as where:
            _done, root = self._run(where, {"0e8d:0616": [self.BLUETOOTH]},
                                    config="CEC_DEVICE=/dev/cec0\n")
            self.assertEqual(self._ids(root), "0e8d:0616")

    @AS_ROOT
    def test_no_config_at_all_is_said_rather_than_crashed_over(self):
        with tempfile.TemporaryDirectory() as where:
            usb = self._machine(where, {"0e8d:0616": [self.BLUETOOTH]})
            done = run("wake-ids", env=dict(os.environ, SYSFS_USB=usb,
                                            ROOT=os.path.join(where, "none")))
            self.assertEqual(done.returncode, 0)
            self.assertIn("nothing to add", done.stderr)

    def test_it_takes_no_arguments(self):
        self.assertIn("wake-ids", run().stderr)


class SourceTest(unittest.TestCase):

    @AS_ROOT
    def test_a_directory_with_no_installer_in_it_is_refused(self):
        """Before the sudo rule is written, not after.

        The order matters more than the refusal: a wrong path noticed after
        the rule is in place is a window where the machine is carrying a grant
        for an install that was never going to happen.
        """
        with tempfile.TemporaryDirectory() as empty:
            done = run("install", empty)
            self.assertEqual(done.returncode, 1)
            self.assertIn("vendored toolkit", done.stderr)
            self.assertFalse(os.path.exists(RULE))

    @AS_ROOT
    def test_installing_for_root_is_refused(self):
        # Half of what the toolkit installs is user systemd units and a
        # WirePlumber config in a home directory. In root's home they would be
        # in a session that never runs a television.
        with _fake_toolkit() as source:
            done = run("install", source, "root", env=_no_pkexec())
            self.assertEqual(done.returncode, 1)
            self.assertIn("root", done.stderr)
            self.assertFalse(os.path.exists(RULE))


class NotRootTest(unittest.TestCase):

    @AS_ROOT
    @HAS_USER
    def test_run_as_an_ordinary_user_it_says_so_rather_than_half_working(self):
        with _fake_toolkit() as source:
            done = subprocess.run(
                ["runuser", "-u", SOMEBODY, "--", "bash", SCRIPT,
                 "install", source],
                capture_output=True, text=True, env=_no_pkexec())
        self.assertEqual(done.returncode, 1)
        self.assertIn("pkexec", done.stderr)


def _no_pkexec():
    """The environment without PKEXEC_UID, so the argument is what decides."""
    env = dict(os.environ)
    env.pop("PKEXEC_UID", None)
    return env


class _fake_toolkit:
    """A source directory shaped like the vendored tree, that installs nothing.

    The real installer wants cec-ctl, a CEC adapter and a live session bus.
    What is under test here is the bridge around it - who it runs as, what it
    hands over, and what it cleans up - so the installer is replaced by one
    that reports its own circumstances and exits.
    """

    def __init__(self, code=0):
        self.code = code

    def __enter__(self):
        self.holder = tempfile.TemporaryDirectory()
        # Reachable by somebody other than the user who made it. A directory
        # made by root is 0700, and the point of this fixture is that another
        # user runs what is inside it - which is also true of the real thing:
        # the vendored tree is in the desktop user's own clone.
        os.chmod(self.holder.name, 0o755)
        for name in ("install.sh", "uninstall.sh"):
            path = os.path.join(self.holder.name, name)
            with open(path, "w") as handle:
                handle.write(
                    "#!/usr/bin/env bash\n"
                    "echo \"ran=%s\"\n" % name +
                    "echo \"whoami=$(id -un)\"\n"
                    "echo \"home=$HOME\"\n"
                    "echo \"runtime=$XDG_RUNTIME_DIR\"\n"
                    "echo \"bus=$DBUS_SESSION_BUS_ADDRESS\"\n"
                    "echo \"flags=$*\"\n"
                    # One the rule grants and one it does not, so the probe
                    # says both that the bridge works and that it is narrowed.
                    "sudo -n install --version >/dev/null 2>&1"
                    " && echo 'sudo=yes' || echo 'sudo=no'\n"
                    "sudo -n id -u >/dev/null 2>&1"
                    " && echo 'anything=yes' || echo 'anything=no'\n"
                    "exit %d\n" % self.code)
            os.chmod(path, 0o755)
        return self.holder.name

    def __exit__(self, *_exc):
        self.holder.cleanup()
        return False


class BridgeTest(unittest.TestCase):
    """The whole path, run for real. Root only - it is a root script."""

    def setUp(self):
        self.addCleanup(self._clear)
        self._clear()

    def _clear(self):
        if os.geteuid() == 0 and os.path.exists(RULE):
            os.remove(RULE)

    def _said(self, output):
        return dict(line.split("=", 1) for line in output.splitlines()
                    if "=" in line and not line.startswith(" "))

    @AS_ROOT
    @HAS_USER
    def test_the_installer_runs_as_the_desktop_user_and_not_as_root(self):
        with _fake_toolkit() as source:
            done = run("install", source, SOMEBODY, env=_no_pkexec())
        self.assertEqual(done.returncode, 0, done.stderr)
        said = self._said(done.stdout)
        self.assertEqual(said["whoami"], SOMEBODY)
        self.assertEqual(said["ran"], "install.sh")

    @AS_ROOT
    @HAS_USER
    def test_it_hands_over_the_session_the_user_half_needs(self):
        """systemctl --user talks to a per-user bus.

        Without these the root half of the install succeeds and the user half
        fails, which is the worst of the outcomes available: it looks
        installed, and none of the services are there.
        """
        import pwd
        uid = pwd.getpwnam(SOMEBODY).pw_uid
        with _fake_toolkit() as source:
            done = run("install", source, SOMEBODY, env=_no_pkexec())
        said = self._said(done.stdout)
        self.assertEqual(said["home"], pwd.getpwnam(SOMEBODY).pw_dir)
        self.assertEqual(said["runtime"], "/run/user/%d" % uid)
        self.assertIn("/run/user/%d/bus" % uid, said["bus"])

    @AS_ROOT
    @HAS_USER
    def test_installing_looks_for_the_bluetooth_radio_too(self):
        """Every install, not only the one that goes looking for it by hand.

        The button on the CEC page is for a radio plugged in later. An
        install that did not do it as well would leave every fresh machine
        exactly where the one this was found on started: a controller that
        cannot wake it, and a helper reporting "matched":0 with no reason.
        """
        with tempfile.TemporaryDirectory() as where:
            usb = os.path.join(where, "usb", "1-12:1.0")
            os.makedirs(usb)
            for leaf, value in (("bInterfaceClass", "e0"),
                                ("bInterfaceSubClass", "01"),
                                ("bInterfaceProtocol", "01")):
                with open(os.path.join(usb, leaf), "w") as handle:
                    handle.write(value)
            device = os.path.join(where, "usb", "1-12")
            os.makedirs(device)
            for leaf, value in (("idVendor", "0e8d"), ("idProduct", "0616")):
                with open(os.path.join(device, leaf), "w") as handle:
                    handle.write(value)
            etc = os.path.join(where, "root", "etc")
            os.makedirs(etc)
            with open(os.path.join(etc, "steamos-cec-toolkit.conf"), "w") as f:
                f.write('USB_WAKE_USB_IDS="8087:0032"\n')
            with _fake_toolkit() as source:
                done = run("install", source, SOMEBODY,
                           env=dict(_no_pkexec(),
                                    SYSFS_USB=os.path.join(where, "usb"),
                                    ROOT=os.path.join(where, "root")))
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("0e8d:0616", done.stdout)
            with open(os.path.join(etc, "steamos-cec-toolkit.conf")) as handle:
                self.assertIn("8087:0032 0e8d:0616", handle.read())

    @AS_ROOT
    @HAS_USER
    def test_it_looks_after_the_toolkit_has_written_its_config(self):
        """Order, not only presence.

        The toolkit's own installer writes /etc/steamos-cec-toolkit.conf when
        it is not already there. Looking for the radio before that would edit
        a file about to be replaced, or none at all.
        """
        with open(SCRIPT) as handle:
            text = handle.read()
        ran = text.index('runuser -u "$TARGET"')
        looked = text.rindex("add_bluetooth_wake_ids")
        self.assertLess(ran, looked,
                        "the radio is looked for before the toolkit installs")

    @AS_ROOT
    @HAS_USER
    def test_a_radio_it_cannot_add_does_not_fail_the_install(self):
        """Waking is one feature of nine. Losing it must not lose the rest."""
        with _fake_toolkit() as source:
            done = run("install", source, SOMEBODY,
                       env=dict(_no_pkexec(), SYSFS_USB="/nowhere",
                                ROOT="/nowhere"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Installed.", done.stdout)

    @AS_ROOT
    @HAS_USER
    def test_the_installer_can_use_sudo_while_it_runs(self):
        # The whole point. Without the rule the installer's first `sudo
        # install` asks for a password at a terminal that is not there.
        with _fake_toolkit() as source:
            done = run("install", source, SOMEBODY, env=_no_pkexec())
        self.assertEqual(self._said(done.stdout)["sudo"], "yes")

    @AS_ROOT
    @HAS_USER
    def test_the_rule_covers_the_installer_and_not_everything(self):
        """Narrowing this is documentation, not containment, and says so.

        `sudo install` writes any file anywhere, so a rule listing it is root
        by another name and the script's comment does not pretend otherwise.
        What the narrowing buys is that the file names what the installer
        touches - so a future upstream that starts calling something else
        fails here, visibly, instead of silently gaining the run of the
        machine under a rule that said ALL.
        """
        with _fake_toolkit() as source:
            done = run("install", source, SOMEBODY, env=_no_pkexec())
        self.assertEqual(self._said(done.stdout)["anything"], "no")

    @AS_ROOT
    @HAS_USER
    def test_a_rule_that_does_not_check_out_is_not_installed(self):
        """The check is consulted, not just run.

        A malformed file in /etc/sudoers.d does not break one rule - sudo
        refuses to start at all, for everybody, until somebody with a root
        shell removes it. On a machine whose other way in is this panel that
        is unrecoverable from the desk, so the interesting case is not that
        the file is usually fine but that a bad one stops the install.
        """
        shims = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, shims)
        liar = os.path.join(shims, "visudo")
        with open(liar, "w") as handle:
            handle.write("#!/usr/bin/env bash\necho 'parse error' >&2\nexit 1\n")
        os.chmod(liar, 0o755)
        env = _no_pkexec()
        env["PATH"] = shims + os.pathsep + env["PATH"]
        with _fake_toolkit() as source:
            done = run("install", source, SOMEBODY, env=env)
        self.assertEqual(done.returncode, 1)
        self.assertIn("nothing was changed", done.stderr)
        self.assertFalse(os.path.exists(RULE))
        # And it stopped rather than carrying on without the rule, which
        # would be an install that hangs on a password prompt.
        self.assertNotIn("ran=", done.stdout)

    def test_the_rule_lists_every_program_the_installers_sudo(self):
        """Derived from the vendored tree, not from memory.

        A `sudo` call upstream adds that this rule does not cover is an
        install that stops halfway with a password prompt nobody can answer -
        on somebody else's machine, with half the files written.
        """
        import re
        tree = os.path.join(HERE, "..", "vendor", "steamos-cec-toolkit")
        called = set()
        for name in ("install.sh", "uninstall.sh"):
            with open(os.path.join(tree, name)) as handle:
                # At command position only. A bare search also matches the
                # word inside `echo "Configuring sudo permissions"`, which is
                # how this test first failed - on its own regex, not on the
                # rule it was checking.
                called.update(re.findall(
                    r"(?m)(?:^|&&|\|\||;)\s*(?:if\s+)?sudo\s+([a-z][a-z0-9-]*)\b",
                    handle.read()))
        with open(SCRIPT) as handle:
            listed = re.search(r"for program in ([^;]+); do",
                               handle.read()).group(1).split()
        # Two of the toolkit's own root helpers are called by absolute path
        # and are covered by the permanent sudoers file it installs before it
        # reaches them; the regex above only catches bare program names.
        self.assertEqual(sorted(called - set(listed)), [],
                         "the installers sudo something the rule omits")

    @AS_ROOT
    @HAS_USER
    def test_the_rule_is_gone_when_it_finishes(self):
        with _fake_toolkit() as source:
            run("install", source, SOMEBODY, env=_no_pkexec())
        self.assertFalse(os.path.exists(RULE), "the grant outlived the install")

    @AS_ROOT
    @HAS_USER
    def test_the_rule_is_gone_when_the_install_fails_too(self):
        """The path that gets forgotten.

        An installer that exits non-zero is the ordinary case here - no CEC
        adapter, no cec-ctl - so a rule that only got cleaned up on success
        would be left behind on most real machines rather than on rare ones.
        """
        with _fake_toolkit(code=1) as source:
            done = run("install", source, SOMEBODY, env=_no_pkexec())
        self.assertNotEqual(done.returncode, 0)
        self.assertFalse(os.path.exists(RULE))

    @AS_ROOT
    @HAS_USER
    def test_a_rule_left_behind_by_a_killed_run_is_cleared_by_the_next(self):
        # The one case the trap cannot cover is the one signal it cannot see.
        with open(RULE, "w") as handle:
            handle.write("# left over from a run that was killed\n")
        os.chmod(RULE, 0o440)
        with _fake_toolkit() as source:
            run("install", source, SOMEBODY, env=_no_pkexec())
        self.assertFalse(os.path.exists(RULE))

    @AS_ROOT
    @HAS_USER
    def test_what_it_writes_is_a_file_sudo_will_accept(self):
        """A malformed file in sudoers.d takes sudo down for the machine.

        Not just this rule - sudo refuses to run at all. On a machine whose
        other way in is this panel, that is a bad afternoon, so the file is
        checked with sudo's own checker while it still costs nothing.
        """
        with _fake_toolkit() as source:
            done = run("install", source, SOMEBODY, env=_no_pkexec())
        self.assertEqual(done.returncode, 0, done.stderr)
        # And sudo is still working afterwards, which is the actual claim.
        self.assertEqual(subprocess.run(["sudo", "-n", "true"]).returncode, 0)

    @AS_ROOT
    @HAS_USER
    def test_nothing_is_switched_on_by_the_install(self):
        """The page is eight switches, so the install should leave eight offs.

        The toolkit's own installer turns the volume integration on by
        default. Left alone, the page would open with one feature already on
        that nobody chose - and set-external-volume writes its own files, so
        turning it on from the page later is a complete path.
        """
        with _fake_toolkit() as source:
            done = run("install", source, SOMEBODY, env=_no_pkexec())
        flags = self._said(done.stdout)["flags"]
        self.assertIn("--no-external-volume", flags)
        self.assertNotIn("--enable-", flags)

    @AS_ROOT
    @HAS_USER
    def test_removing_runs_the_uninstaller_not_the_installer(self):
        with _fake_toolkit() as source:
            done = run("remove", source, SOMEBODY, env=_no_pkexec())
        self.assertEqual(done.returncode, 0, done.stderr)
        said = self._said(done.stdout)
        self.assertEqual(said["ran"], "uninstall.sh")
        # And no --no-external-volume: the uninstaller has no such flag and
        # would refuse the whole run over it.
        self.assertEqual(said["flags"].strip(), "")


class VendoredTreeTest(unittest.TestCase):
    """The bridge and the tree it drives have to agree about the file names."""

    def test_the_installers_it_looks_for_are_the_ones_that_are_there(self):
        tree = os.path.join(HERE, "..", "vendor", "steamos-cec-toolkit")
        for name in ("install.sh", "uninstall.sh"):
            self.assertTrue(os.path.exists(os.path.join(tree, name)), name)

    def test_the_uninstaller_really_has_no_feature_flags(self):
        # The claim above, checked against the file rather than remembered.
        with open(os.path.join(HERE, "..", "vendor", "steamos-cec-toolkit",
                               "uninstall.sh")) as handle:
            self.assertNotIn("--no-external-volume", handle.read())


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
