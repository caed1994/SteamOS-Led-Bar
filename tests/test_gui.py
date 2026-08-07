"""Tests for the control panel's logic, which is kept out of the widgets.

There is no display here and there will not be one on a build machine, so
everything worth testing - what counts as broken, and what fixes it - lives in
ledpanel.py with no tkinter in sight.
"""

import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, os.path.join(HERE, "..", "gui"))

import kdetheme  # noqa: E402
import ledpanel  # noqa: E402
from steamos_led import config as config_module  # noqa: E402


class FakeProbe:
    """Answers about a machine, without being one."""

    def __init__(self, present=(), fifos=(), active=(), user_active=(),
                 release="6.11.11-valve"):
        self.present = set(present)
        self.fifos = set(fifos)
        self.active = set(active)
        self.user_active = set(user_active)
        self.release = release

    def exists(self, path):
        return path in self.present

    def is_fifo(self, path):
        return path in self.fifos

    def unit_active(self, unit, user=False):
        return unit in (self.user_active if user else self.active)

    def kernel_release(self):
        return self.release


def healthy(release="6.11.11-valve"):
    return FakeProbe(
        present=(ledpanel.BINARY, ledpanel.UNIT_PATH, ledpanel.CONFIG_PATH,
                 ledpanel.UDEV_PATH, ledpanel.SHIM_DEVICE,
                 ledpanel.module_path(release)),
        fifos=("/run/steamos-led-serial/notify",),
        active=(ledpanel.SERVICE,),
        user_active=(ledpanel.WATCHER,),
        release=release)


class HealthyInstallationTest(unittest.TestCase):
    def test_nothing_is_reported_broken(self):
        checks = ledpanel.run_checks(probe=healthy())
        self.assertEqual(ledpanel.broken(checks), [])
        self.assertIn("in order", ledpanel.repair_summary(checks))

    def test_the_notification_check_is_skipped_when_switched_off(self):
        probe = healthy()
        probe.fifos = set()
        checks = ledpanel.run_checks(probe=probe, config={"NOTIFY": False})
        self.assertEqual(ledpanel.broken(checks), [])

    def test_a_custom_pipe_location_is_honoured(self):
        probe = healthy()
        probe.fifos = {"/run/elsewhere/notify"}
        checks = ledpanel.run_checks(
            probe=probe, config={"NOTIFY": True,
                                 "NOTIFY_FIFO": "/run/elsewhere/notify"})
        self.assertEqual(ledpanel.broken(checks), [])


class AfterASteamUpdateTest(unittest.TestCase):
    """The case this panel exists for.

    A SteamOS update brings a new kernel. The module was built for the old
    one, so it is simply not there any more and the LED device disappears with
    it - while everything else looks perfectly healthy, which is what makes it
    puzzling from the outside.
    """

    def setUp(self):
        self.probe = healthy(release="6.11.11-valve")
        self.probe.release = "6.14.2-valve"          # the update landed
        self.probe.present.discard(ledpanel.SHIM_DEVICE)
        self.checks = ledpanel.run_checks(probe=self.probe)

    def test_the_missing_module_is_named(self):
        names = [check.name for check in ledpanel.broken(self.checks)]
        self.assertTrue(any("Kernel module" in name for name in names), names)

    def test_the_new_kernel_version_is_in_the_message(self):
        module = next(check for check in self.checks
                      if check.name.startswith("Kernel module"))
        self.assertIn("6.14.2-valve", module.name)
        self.assertIn("6.14.2-valve", module.detail)

    def test_the_summary_explains_rather_than_counts(self):
        summary = ledpanel.repair_summary(self.checks)
        self.assertIn("SteamOS update", summary)
        self.assertIn("reinstall", summary.lower())

    def test_all_of_it_is_repairable(self):
        self.assertTrue(all(check.repairable
                            for check in ledpanel.broken(self.checks)))


class NotInstalledTest(unittest.TestCase):
    def test_everything_is_reported_missing(self):
        checks = ledpanel.run_checks(probe=FakeProbe())
        self.assertEqual(len(ledpanel.broken(checks)), len(checks))

    def test_the_summary_counts_them(self):
        checks = ledpanel.run_checks(probe=FakeProbe())
        self.assertIn("problem", ledpanel.repair_summary(checks))


class CommandTest(unittest.TestCase):
    """What the buttons actually run."""

    def test_repairing_never_touches_the_board(self):
        # Reinstalling after a system update must not reflash the ESP: the
        # firmware survives it, and a surprise flash is the last thing someone
        # fixing a dark bar needs.
        command = ledpanel.reinstall_command("/home/deck/SteamOS-Led-Bar")
        self.assertIn("--flash", command)
        self.assertEqual(command[command.index("--flash") + 1], "0")

    def test_repairing_rebuilds_the_module_unattended(self):
        command = ledpanel.reinstall_command("/home/deck/SteamOS-Led-Bar")
        self.assertIn("--rebuild-module", command)
        self.assertIn("--yes", command)

    def test_repairing_asks_for_rights(self):
        self.assertEqual(ledpanel.reinstall_command("/repo")[0], "pkexec")

    def test_flashing_the_bar_asks_for_nothing(self):
        # The trigger pipe is world-writable on purpose, so trying a colour
        # must not put a password prompt in the way.
        self.assertNotIn("pkexec", ledpanel.notify_command("achievement"))

    def test_steam_questions_run_as_the_user(self):
        # Steamworks talks to the logged-in user's Steam client; as root it
        # would find nothing at all.
        for command in (ledpanel.steam_check_command(),
                        ledpanel.probe_messages_command()):
            self.assertNotIn("pkexec", command)

    def test_the_self_test_needs_rights_to_free_the_port(self):
        command = ledpanel.self_test_command("/repo", seconds=5)
        self.assertEqual(command[0], "pkexec")
        self.assertIn("5", command)

    def test_applying_a_config_goes_through_the_helper(self):
        command = ledpanel.apply_config_command("/repo", "/tmp/staged.conf")
        self.assertEqual(command[0], "pkexec")
        self.assertIn("/repo/scripts/apply-config.sh", command)
        self.assertIn("/tmp/staged.conf", command)


class PanelSettingsTest(unittest.TestCase):
    """The settings list has to agree with the configuration it edits."""

    def _settings(self):
        # Read the table out of the panel rather than importing it: importing
        # pulls in tkinter, which a build machine has no reason to have.
        path = os.path.join(HERE, "..", "gui", "steamos-led-panel")
        with open(path) as handle:
            tree = ast.parse(handle.read())
        for node in tree.body:
            if (isinstance(node, ast.Assign)
                    and getattr(node.targets[0], "id", "") == "SETTINGS"):
                return [entry.elts[0].value for entry in node.value.elts]
        self.fail("SETTINGS not found in the panel")

    def test_every_setting_shown_is_a_real_option(self):
        for key in self._settings():
            self.assertIn(key, config_module.DEFAULTS, key)

    def test_the_install_time_ones_are_left_out(self):
        # Serial port, baud rate, device path and library paths are decisions
        # made once, at install time. A slider is the wrong shape for them,
        # and getting one wrong takes the bar down rather than changing how it
        # looks - which is what everything in this panel should do.
        shown = self._settings()
        for key in ("SERIAL_PORT", "BAUD", "DEVICE", "STEAM_LIBRARY",
                    "STEAM_ROUTE"):
            self.assertNotIn(key, shown, key)

    def test_the_panel_never_writes_a_value_the_service_would_reject(self):
        # The sliders repeat the bounds validate() enforces. If the two ever
        # disagree, the panel offers a value that kills the service on
        # restart - so check the ends of every numeric range against it.
        path = os.path.join(HERE, "..", "gui", "steamos-led-panel")
        with open(path) as handle:
            tree = ast.parse(handle.read())
        table = next(node.value for node in tree.body
                     if isinstance(node, ast.Assign)
                     and getattr(node.targets[0], "id", "") == "SETTINGS")

        for entry in table.elts:
            key, _label, kind = (entry.elts[0].value, entry.elts[1].value,
                                 entry.elts[2].value)
            if kind not in ("int", "float"):
                continue
            low, high = entry.elts[3].value, entry.elts[4].value
            for edge in (low, high):
                candidate = dict(config_module.DEFAULTS)
                candidate[key] = int(edge) if kind == "int" else float(edge)
                # The two frame rates are coupled, and the panel resolves
                # that itself by moving the idle rate - so mirror that rule
                # here instead of pretending the ends are independent.
                if key == "IDLE_FPS":
                    candidate["FPS"] = max(candidate["FPS"], candidate[key])
                if key == "FPS":
                    candidate["IDLE_FPS"] = min(candidate["IDLE_FPS"],
                                                candidate[key])
                try:
                    config_module.validate(candidate)
                except config_module.ConfigError as exc:
                    self.fail("the panel offers %s=%s, which the service "
                              "rejects: %s" % (key, edge, exc))




class KdeThemeTest(unittest.TestCase):
    """Reading the desktop's own colours instead of inventing some.

    tkinter knows nothing about Plasma, which is why an unstyled window looks
    foreign. Plasma writes its scheme to ~/.config/kdeglobals as plain INI, so
    it can just be read.
    """

    BREEZE_DARK = """
[General]
ColorScheme=BreezeDark
font=Noto Sans,10,-1,5,50,0,0,0,0,0

[Colors:Window]
BackgroundNormal=49,54,59
ForegroundNormal=252,252,252

[Colors:View]
BackgroundNormal=35,38,41
ForegroundNormal=252,252,252
ForegroundNegative=218,68,83
ForegroundPositive=39,174,96

[Colors:Button]
BackgroundNormal=49,54,59
ForegroundNormal=252,252,252

[Colors:Selection]
BackgroundNormal=61,174,233
ForegroundNormal=252,252,252
"""

    def _write(self, text):
        import tempfile
        handle = tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_colours_come_from_the_scheme(self):
        palette = kdetheme.read(self._write(self.BREEZE_DARK))
        self.assertEqual(palette["window"], "#31363b")
        self.assertEqual(palette["view"], "#232629")
        self.assertEqual(palette["selection"], "#3daee9")

    def test_a_dark_scheme_is_recognised_as_dark(self):
        self.assertTrue(kdetheme.is_dark(kdetheme.read(
            self._write(self.BREEZE_DARK))))
        self.assertFalse(kdetheme.is_dark(kdetheme.BREEZE_LIGHT))

    def test_the_font_is_read(self):
        palette = kdetheme.read(self._write(self.BREEZE_DARK))
        self.assertEqual(palette["font"], ("Noto Sans", 10))

    def test_a_missing_file_still_gives_a_complete_palette(self):
        palette = kdetheme.read("/nonexistent/kdeglobals")
        for key in kdetheme.BREEZE_LIGHT:
            self.assertIn(key, palette)
            self.assertTrue(palette[key].startswith("#"), key)

    def test_a_half_written_scheme_is_filled_in(self):
        # A hand-edited or partial file must not leave holes in the window.
        palette = kdetheme.read(self._write(
            "[Colors:Window]\nBackgroundNormal=10,20,30\n"))
        self.assertEqual(palette["window"], "#0a141e")
        self.assertEqual(palette["selection"],
                         kdetheme.BREEZE_LIGHT["selection"])

    def test_nonsense_values_are_ignored_rather_than_crashing(self):
        palette = kdetheme.read(self._write(
            "[Colors:Window]\nBackgroundNormal=not,a,colour\n"))
        self.assertEqual(palette["window"], kdetheme.BREEZE_LIGHT["window"])

    def test_out_of_range_values_are_ignored(self):
        palette = kdetheme.read(self._write(
            "[Colors:Window]\nBackgroundNormal=300,0,0\n"))
        self.assertEqual(palette["window"], kdetheme.BREEZE_LIGHT["window"])

    def test_an_alpha_channel_is_tolerated(self):
        self.assertEqual(kdetheme.parse_color("1,2,3,255"), "#010203")

    def test_derived_shades_follow_the_scheme(self):
        # Borders and hover states have no entry in the scheme - Qt draws them
        # itself - so they are mixed, which has to work in the dark too.
        for palette in (kdetheme.BREEZE_LIGHT,
                        kdetheme.read(self._write(self.BREEZE_DARK))):
            shades = kdetheme.derived(palette)
            for key in ("border", "hover", "muted", "raised"):
                self.assertRegex(shades[key], r"^#[0-9a-f]{6}$", key)
            # A border has to be visible against its own background.
            self.assertNotEqual(shades["border"], palette["window"])

    def test_dark_and_light_borders_go_opposite_ways(self):
        dark = kdetheme.read(self._write(self.BREEZE_DARK))
        light = kdetheme.BREEZE_LIGHT
        self.assertGreater(kdetheme.luminance(kdetheme.derived(dark)["border"]),
                           kdetheme.luminance(dark["window"]),
                           "a dark scheme needs a lighter border")
        self.assertLess(kdetheme.luminance(kdetheme.derived(light)["border"]),
                        kdetheme.luminance(light["window"]),
                        "a light scheme needs a darker one")

    def test_a_broken_font_line_does_not_break_the_palette(self):
        palette = kdetheme.read(self._write("[General]\nfont=\n"))
        self.assertIsNone(palette["font"])
        self.assertEqual(kdetheme.parse_font("Noto Sans"), ("Noto Sans", 10))
        self.assertEqual(kdetheme.parse_font("Noto Sans,huge"),
                         ("Noto Sans", 10))

    def test_absurd_font_sizes_are_clamped(self):
        self.assertEqual(kdetheme.parse_font("X,900")[1], 32)
        self.assertEqual(kdetheme.parse_font("X,1")[1], 6)




class PanelStyleTest(unittest.TestCase):
    """Every custom ttk style used has to be one that was configured.

    A misspelled style name is the quietest bug tkinter has: the widget simply
    keeps the default look and nothing is reported. There is no display on a
    build machine to notice it either, so the two lists are compared here.
    """

    def setUp(self):
        path = os.path.join(HERE, "..", "gui", "steamos-led-panel")
        with open(path) as handle:
            self.tree = ast.parse(handle.read())

    def _configured(self):
        names = set()
        for node in ast.walk(self.tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("configure", "map")
                    and getattr(node.func.value, "id", "") == "style"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)):
                names.add(node.args[0].value)
        return names

    def _used(self):
        names = set()
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "style":
                    continue
                # style= is not always a plain string: a check that is either
                # good or bad picks its style with a conditional, so collect
                # every name in the expression.
                for inner in ast.walk(keyword.value):
                    if isinstance(inner, ast.Constant) and \
                            isinstance(inner.value, str):
                        names.add(inner.value)
        return names

    def test_every_style_used_was_configured(self):
        configured, used = self._configured(), self._used()
        for name in used:
            self.assertIn(name, configured,
                          "%s is applied to a widget but never configured" % name)

    def test_the_custom_styles_are_all_used(self):
        # A configured style nobody applies is dead weight, and usually means
        # a rename happened on one side only.
        configured, used = self._configured(), self._used()
        for name in configured:
            if "." not in name or name.startswith(("T", ".")):
                continue        # the built-in classes, styled wholesale
            if name.split(".")[-1].startswith("T") and name.count(".") == 1 \
                    and name.split(".")[0] not in ("Horizontal",):
                self.assertIn(name, used, "%s is configured but never used"
                              % name)

    def test_the_status_marks_use_the_schemes_own_colours(self):
        # Good and bad have to come from the desktop scheme, not from a
        # hardcoded green and red that vanish in someone's dark theme.
        source = ast.dump(self.tree)
        self.assertIn("Good.TLabel", source)
        self.assertIn("Bad.TLabel", source)
        for hardcoded in ("'green'", "'red'", "#00ff00", "#ff0000"):
            self.assertNotIn(hardcoded, source)


if __name__ == "__main__":
    unittest.main()
