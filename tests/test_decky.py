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
        named = set(re.findall(r'doAction\("([a-z-]+)"\)', self.text))
        self.assertTrue(named)
        for action in sorted(named):
            self.assertIn(action, ctl.ACTIONS, action)

    def test_the_keyboard_is_not_on_the_page(self):
        """A layout is set one time, and that belongs in the panel."""
        self.assertNotIn("keyboard", self._areas())

    def test_the_switch_that_needs_a_password_is_off_the_page(self):
        """resume-wake controls a unit of root. Game Mode cannot ask.

        The page shows it and refuses to move it, rather than leaving it out:
        a switch that is simply absent reads as a switch that was lost.
        """
        for name in ctl.BY_HAND:
            self.assertIn(name, self.text, name)
        self.assertIn("BY_HAND", self.text)

    def test_each_setting_it_writes_is_one_the_command_accepts(self):
        """A key with a spelling error is a refusal that a person cannot fix."""
        for key in re.findall(r'write\("strip",\s*\{\s*([A-Z_]+)', self.text):
            self.assertIn(key, ctl.AREA["strip"]["keys"], key)
        for key in re.findall(r'write\("power",\s*\{\s*([A-Z_]+)', self.text):
            self.assertIn(key, ctl.AREA["power"]["keys"], key)

    def test_the_switches_come_from_the_command_and_not_from_a_list_here(self):
        """A switch that the toolkit gains must appear with no work here."""
        for name in cec.BY_NAME:
            if name in ctl.BY_HAND:
                continue
            self.assertNotIn('"%s"' % name, self.text, name)

    def test_the_polled_half_is_the_cheap_one(self):
        """A fork for each answer is a cost that a game pays.

        The page asks for the full status when it opens and on a change. The
        timer asks for the other one.
        """
        timer = self.text[self.text.index("setInterval"):]
        self.assertIn("refreshCheap", timer[:120])


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
        for sign in ("SteamOS Utility Center", "repair-drives", "get_status"):
            self.assertIn(sign, built, sign)


if __name__ == "__main__":
    unittest.main()
