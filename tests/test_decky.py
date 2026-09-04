# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Game Mode plugin, and where it touches the rest of this project.

Nothing here runs Decky, and nothing here runs a browser. What is under test
is every seam that a person cannot see until the plugin is on a machine: a
method that the page calls and the backend does not have, an area name that
the command does not know, or a path that an update takes away.

The plugin holds no rule of its own, and the tests here are what keeps that
true. A rule that reaches the page is a second answer to a question that
server/steamos_utility_center/ctl.py already answers.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DECKY = os.path.join(HERE, "..", "decky")
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_utility_center import cec               # noqa: E402
from steamos_utility_center import ctl               # noqa: E402


def read(*parts):
    with open(os.path.join(DECKY, *parts), encoding="utf-8") as handle:
        return handle.read()


class ManifestTest(unittest.TestCase):
    """What Decky reads before it starts the plugin."""

    def setUp(self):
        self.manifest = json.loads(read("plugin.json"))

    def test_the_plugin_asks_for_no_root(self):
        """The whole design in one line.

        A plugin with the root flag runs its backend as root, and this one has
        no reason to: it starts a command, and that command asks sudo for the
        three programs that need rights. A plugin that ran as root would carry
        far more than those three.
        """
        self.assertEqual(self.manifest.get("flags"), [])

    def test_it_has_a_name_and_an_author(self):
        self.assertTrue(self.manifest.get("name"))
        self.assertTrue(self.manifest.get("author"))

    def test_the_installer_writes_it_under_that_name(self):
        """Decky finds a plugin by its directory. A directory of another name
        is a plugin that is on the disk and not in the menu.
        """
        sys.path.insert(0, HERE)
        from shellvalues import shell_value
        self.assertIn(self.manifest["name"], shell_value("DECKY_PLUGIN"))


class InstallTest(unittest.TestCase):
    """How the plugin reaches ~/homebrew/plugins, and what is said when it
    does not.

    The first version of this step copied as the desktop user and said nothing
    when it skipped. Decky Loader keeps that directory as root, so the copy
    failed, and a person then looked for a plugin that was never written with
    nothing on the screen to say why.
    """

    def _text(self):
        with open(os.path.join(HERE, "..", "install.sh"),
                  encoding="utf-8") as handle:
            return handle.read()

    def _step(self):
        text = self._text()
        start = text.index("install_decky_plugin() {")
        return text[start:text.index("\ninstall_decky_plugin", start)]

    def test_the_installer_has_the_step(self):
        self.assertIn("install_decky_plugin", self._text())

    def test_it_copies_as_root(self):
        """Decky keeps that directory as root, and its loader reads as root."""
        self.assertNotIn("runuser", self._step())
        self.assertIn("install -d", self._step())

    def test_it_says_something_when_it_skips(self):
        """A silent skip is the fault this step had.

        Every branch that installs nothing says so. There is no path out of
        this step that leaves the screen unchanged.
        """
        lines = self._step().splitlines()
        for branch in ("No desktop user", "No Decky Loader"):
            self.assertIn(branch, "\n".join(lines), branch)
        # Every way out of this step says something on the three lines above
        # it. A `return 0` with nothing above it is a silent skip.
        for index, line in enumerate(lines):
            if "return 0" not in line:
                continue
            above = " ".join(lines[max(0, index - 3):index + 1])
            self.assertTrue("say " in above or "warn " in above,
                            "line %d leaves without a word: %s"
                            % (index + 1, line.strip()))

    def test_it_names_every_file_the_plugin_needs(self):
        """Decky reads plugin.json, runs main.py and loads dist/index.js."""
        step = self._step()
        for file in ("plugin.json", "main.py", "dist/index.js"):
            self.assertIn(file, step, file)

    def test_it_says_how_to_make_decky_read_it(self):
        """A copied plugin appears at the next start of the loader."""
        self.assertIn("plugin_loader", self._step())


class BackendTest(unittest.TestCase):
    """The Python half, which is a caller of the command and nothing else."""

    def setUp(self):
        self.text = read("main.py")
        self.tree = ast.parse(self.text)
        self.methods = [node.name for node in ast.walk(self.tree)
                        if isinstance(node, ast.AsyncFunctionDef)]
        self.code = self._without_prose()

    def _without_prose(self):
        """The file with its comments and docstrings taken out.

        The prose of this file names the things it must not do, which is why
        it is prose. A test that read it would find every word it looks for.
        """
        tree = ast.parse(self.text)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and \
                    isinstance(first.value, ast.Constant) and \
                    isinstance(first.value.value, str):
                first.value.value = ""
        return ast.unparse(tree)

    def test_it_calls_the_command_in_var_lib(self):
        """/usr/local/bin is on the read-only root, and an update takes it.

        Both paths are there after an installation. Only one of them is on a
        partition that survives a SteamOS update, and a plugin that named the
        other one would stop working at an update with no fault of its own.
        """
        found = [node.value.value for node in ast.walk(self.tree)
                 if isinstance(node, ast.Assign)
                 and isinstance(node.value, ast.Constant)
                 and any(getattr(target, "id", "") == "CTL"
                         for target in node.targets)]
        self.assertEqual(found, [ctl.INSTALL_DIR + "/steamos-utility-centerctl"])

    def test_it_corrects_the_three_variables_of_the_session(self):
        """Decky starts a plugin with no session around it.

        Without the first two, every `systemctl --user` of the CEC toolkit
        fails, and each switch on that page is a user unit. The third is
        removed: Decky runs inside the environment of Steam, and a system
        program that inherits Steam's LD_LIBRARY_PATH loads the wrong
        libraries.
        """
        self.assertIn("XDG_RUNTIME_DIR", self.code)
        self.assertIn("DBUS_SESSION_BUS_ADDRESS", self.code)
        self.assertIn("LD_LIBRARY_PATH", self.code)

    def test_it_holds_no_rule_of_its_own(self):
        """Every method is a call to the command.

        A name of a setting, a list of what a machine offers, or a refusal in
        this file is a second answer to a question that ctl.py answers.
        """
        for name in ("CPU_GOVERNOR", "RAINBOW_SHOWS", "steam-button",
                     "sudoers", "systemctl", "pkexec", "sudo"):
            self.assertNotIn(name, self.code, name)

    def test_the_methods_the_page_calls_are_all_here(self):
        called = set(re.findall(r'callable<[^>]*>\("([a-z_]+)"\)',
                                read("src", "index.tsx")))
        self.assertTrue(called, "the page calls nothing at all")
        for name in sorted(called):
            self.assertIn(name, self.methods, name)


class PageTest(unittest.TestCase):
    """The TypeScript half, and the names it shares with the command."""

    def setUp(self):
        self.text = read("src", "index.tsx")

    def _areas(self):
        return set(re.findall(r'(?:getArea|write)\(\s*"([a-z]+)"', self.text))

    def test_every_area_it_names_is_one_the_command_knows(self):
        named = self._areas()
        self.assertTrue(named)
        for area in sorted(named):
            self.assertIn(area, ctl.AREAS, area)

    def test_every_action_it_names_is_one_the_command_knows(self):
        """The page names none today. Waking a television is what the
        television's own remote is for, and the toolkit does it by itself.
        This holds for the day one comes back.
        """
        for action in sorted(set(re.findall(r'doAction\("([a-z-]+)"\)',
                                            self.text))):
            self.assertIn(action, ctl.ACTIONS, action)

    def test_the_keyboard_is_not_on_the_page(self):
        """A layout is set one time, and that belongs in the panel."""
        self.assertNotIn("keyboard", self._areas())

    def test_every_switch_of_the_toolkit_can_be_moved_here(self):
        """There was one that could not, and it needed a program of its own.

        resume-wake is a unit of root. Every switch of that kind in the
        toolkit has a small program behind it and a line in a sudoers file
        that permits it, and this one had neither: it went through the
        installer under pkexec, which asks. So it was the one switch on this
        page that a person could look at and not move.

        It has its own program now. This holds the page to that: a switch that
        the page draws and refuses to move is a switch that reads as broken.
        """
        self.assertNotIn("BY_HAND", self.text)
        self.assertNotIn("set in the panel", self.text)

    def test_each_setting_it_writes_is_one_the_command_accepts(self):
        """A key with a spelling error is a refusal that a person cannot fix."""
        for key in re.findall(r'write\("strip",\s*\{\s*([A-Z_]+)', self.text):
            self.assertIn(key, ctl.AREA["strip"]["keys"], key)
        for key in re.findall(r'write\("power",\s*\{\s*([A-Z_]+)', self.text):
            self.assertIn(key, ctl.AREA["power"]["keys"], key)

    def test_the_switches_come_from_the_command_and_not_from_a_list_here(self):
        """A switch that the toolkit gains must appear with no work here."""
        for name in cec.BY_NAME:
            self.assertNotIn('"%s"' % name, self.text, name)

    def test_no_slider_writes_while_it_moves(self):
        """A slider wrote at every step, and each step restarted the service.

        systemd counts five starts in ten seconds and refuses the sixth, so
        two seconds of moving one slider left the bar dark and the service
        failed.

        The brightness slider is gone for that reason. The sliders of the
        graphics card are a different thing: each one holds a value, and a
        button sends them. They must stay that way, and for a second reason.
        The daemon takes a change to the card back unless it is told to keep
        it, and a slider that sent at every step would start that clock at
        every step.
        """
        self.assertNotIn("MAX_BRIGHTNESS", self.text)
        for part in self.text.split("<SliderField")[1:]:
            one = part[:part.index("/>")]
            self.assertIn("held.wanted[knob.key] = value", one, one[:300])
            self.assertNotIn("write(", one, one[:300])
            self.assertNotIn("setArea(", one, one[:300])

    def test_the_card_is_sent_by_a_button_and_kept_by_a_second_one(self):
        """LACT's own safety, and it must not be worked around.

        A voltage offset that is too low hangs the card. The daemon puts the
        card back after some seconds unless somebody says to keep it, and a
        hang that was kept comes back at every boot. So nothing here confirms
        by itself.
        """
        self.assertIn('doAction("gpu-keep")', self.text)
        self.assertNotIn("gpu-revert", self.text)
        # The send and the confirmation are two presses, and the page draws
        # the second one only after the first one.
        self.assertIn("keeping", self.text)

    def test_a_setting_uses_the_row_this_page_builds(self):
        """Field and Dropdown, and not Steam's own DropdownItem.

        DropdownItem is Steam's settings row with Steam's dropdown inside it,
        and this project cannot see what that row does with a prop it does not
        know. renderButtonValue is declared on Dropdown, so the row here hands
        it to Dropdown and nothing passes it on the way.
        """
        for part in self.text.split("<Choice")[1:]:
            one = part[:part.index("/>")]
            self.assertIn("value=", one, one[:200])
            self.assertIn("onPick=", one, one[:200])
        self.assertIn("<Field", self.text)
        self.assertIn("<Dropdown", self.text)

    def test_that_row_draws_its_closed_box_from_the_value_it_is_given(self):
        """Two sources for one box is one box that can disagree with itself."""
        # The body, and not the list of props above it: the first "\n}" in
        # this function closes that list.
        one = self.text[self.text.index("function Choice"):]
        one = one[one.index("return ("):]
        one = one[:one.index("\n}")]
        self.assertIn("selectedOption={props.value}", one)
        self.assertIn("renderButtonValue=", one)
        drawn = one.split("renderButtonValue={")[1].split("onChange")[0]
        self.assertIn("props.value", drawn)

    def test_the_option_lists_keep_their_identity(self):
        """A list rebuilt at every render is a new list of new objects.

        A box that holds the option it was given then holds an object that is
        no longer in the list it has, which is one way to name a value that
        is gone.
        """
        for name in ("rainbowOptions", "sceneOptions", "governorOptions",
                     "eppOptions"):
            self.assertIn("const %s = useMemo(" % name, self.text, name)
            self.assertIn("options={%s}" % name, self.text, name)

    def test_the_page_asks_for_one_status_and_not_two(self):
        """The fault that turned every switch off by itself.

        The switches of the CEC toolkit are in `status --full` and in nothing
        else: only systemd knows whether a unit is enabled. A timer asked for
        the cheap status every five seconds and put the answer in the same
        place, so every five seconds the switches lost their state and the
        page drew them all as off. Reopening the menu brought them back for
        five seconds, which is what a person sees.

        One status, so there is no second answer to overwrite the first.
        """
        self.assertIn("cec_features", self.text)
        self.assertIn("get_full_status", self.text)
        self.assertNotIn('callable<[], Status>("get_status")', self.text)

    def test_the_page_keeps_its_values_outside_the_component(self):
        """Steam builds the panel again when a dropdown menu closes.

        Every useState in it goes back to its first value at that moment.
        That is the whole of the fault: a pick reached the machine, and the
        value this page held did not survive the pick. A test dropdown with
        three options and no backend showed it. Its state went back to "one"
        at every pick.

        So the values live outside the component, where one that is built
        again reads the same ones.
        """
        self.assertIn("const held = {", self.text)
        # useState is named in the comment that explains this and nowhere
        # else. useReducer draws; it holds no value.
        for line in self.text.splitlines():
            if line.strip().startswith("//"):
                continue
            self.assertNotIn("useState", line, line)
        self.assertIn("useReducer", self.text)

    def test_there_is_no_timer(self):
        """A page in a menu is opened, used and closed.

        A timer costs a fork for each answer while a game runs, and it was
        the reason the switches lost their state. The page reads when it
        opens and after each change, which is when an answer can differ.
        """
        self.assertNotIn("setInterval", self.text)

    def test_the_card_offers_what_lact_reports_and_no_list_of_its_own(self):
        """A control with no range is a control that this card does not have.

        The card decides, and the daemon says so. A list in this file would
        draw a slider for a control that writes nowhere.
        """
        from steamos_utility_center import lact
        for key, _label, _unit, _source, _end in lact.KNOBS:
            self.assertNotIn('"%s"' % key, self.text, key)
        self.assertIn("gpu?.offers?.knobs", self.text)

    def test_the_fan_and_the_firmware_are_not_on_the_page(self):
        """Those are for a person with the window of LACT open and a stress
        test in progress. A second and worse LACT is not what this is.
        """
        for word in ("zero_rpm", "fan", "acoustic", "curve"):
            self.assertNotIn(word, self.text.lower(), word)

    def test_the_status_block_and_the_drives_are_off_the_page(self):
        """Both are answers to a question that nobody asked on a sofa.

        The section titles, and not the words. "This machine has no cpufreq"
        is a sentence on the page and not the block that was taken off it.
        """
        for title in ('title="This machine"', 'title="Drives"'):
            self.assertNotIn(title, self.text, title)
        self.assertNotIn("repair-drives", self.text)


class RestartLimitTest(unittest.TestCase):
    """The service must survive a person who changes a setting twice.

    systemd's start limit is for a service that crashes and starts again by
    itself. A change that a person asked for is not that, and the applier says
    so by clearing the counter before it restarts.
    """

    def _applier(self):
        with open(os.path.join(HERE, "..", "scripts", "apply-config.sh"),
                  encoding="utf-8") as handle:
            return handle.read()

    def test_the_counter_is_cleared_before_the_restart(self):
        text = self._applier()
        self.assertIn("reset-failed", text)
        self.assertLess(text.index("reset-failed"),
                        text.index('systemctl restart'))

    def test_clearing_it_never_stops_the_applier(self):
        """A unit that is not failed is not an error, and the script has -e."""
        line = [one for one in self._applier().splitlines()
                if "reset-failed" in one and one.startswith("systemctl")]
        self.assertEqual(len(line), 1, line)
        self.assertTrue(line[0].rstrip().endswith("|| true"), line[0])


class BuiltTest(unittest.TestCase):
    """What Decky loads, which is the built file and not the source.

    Nobody must run npm on a Steam Machine to get a plugin that works, so the
    built file is in the repository. A source that moved ahead of it is a
    plugin that shows the old page and no sign of why.
    """

    def test_the_built_file_is_there(self):
        self.assertTrue(os.path.exists(os.path.join(DECKY, "dist", "index.js")))

    def test_it_was_built_from_this_source(self):
        built = read("dist", "index.js")
        for sign in ("SteamOS Utility Center", "Rainbow slot", "RAINBOW_SHOWS",
                     "get_full_status"):
            self.assertIn(sign, built, sign)


if __name__ == "__main__":
    unittest.main()
