"""Tests for the control panel's logic, which is kept out of the widgets.

There is no display here and there will not be one on a build machine, so
everything worth testing - what counts as broken, and what fixes it - lives in
ledpanel.py with no tkinter in sight.
"""

import ast
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, os.path.join(HERE, "..", "gui"))

import kdetheme  # noqa: E402
import ledpanel  # noqa: E402
import roundrect  # noqa: E402
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

    def test_restarting_the_watcher_needs_no_rights_and_stays_the_user(self):
        # It is a user unit: "systemctl restart" as root would look for a
        # system unit of that name and find nothing, and pkexec would put a
        # password prompt in front of something that needs none.
        command = ledpanel.restart_watcher_command()
        self.assertNotIn("pkexec", command)
        self.assertIn("--user", command)
        self.assertIn(ledpanel.WATCHER, command)

    def test_the_self_test_needs_rights_to_free_the_port(self):
        command = ledpanel.self_test_command("/repo", seconds=5)
        self.assertEqual(command[0], "pkexec")
        self.assertIn("5", command)

    def test_applying_a_config_goes_through_the_helper(self):
        command = ledpanel.apply_config_command("/repo", "/tmp/staged.conf")
        self.assertEqual(command[0], "pkexec")
        self.assertIn("/repo/scripts/apply-config.sh", command)
        self.assertIn("/tmp/staged.conf", command)


class DesktopEntryTest(unittest.TestCase):
    """The menu entry is written by install.sh from a template."""

    def _template(self):
        path = os.path.join(HERE, "..", "gui", "steamos-led-panel.desktop")
        with open(path) as handle:
            return handle.read()

    def test_every_placeholder_is_substituted_by_the_installer(self):
        # One left behind is a menu entry that does not start, or has no icon.
        path = os.path.join(HERE, "..", "install.sh")
        with open(path) as handle:
            installer = handle.read()
        for line in self._template().splitlines():
            for token in ("@SOURCE_DIR@", "@ICON@"):
                if token in line and not line.startswith("#"):
                    self.assertIn("s|%s|" % token, installer, token)

    def test_the_window_and_the_entry_agree_on_the_wm_class(self):
        # This pair is what ties the running window to the menu entry. Get it
        # wrong and the desktop names the window after the interpreter that
        # happens to run it - "python3" in the task bar, with a stock icon.
        panel = os.path.join(HERE, "..", "gui", "steamos-led-panel")
        with open(panel) as handle:
            tree = ast.parse(handle.read())
        declared = next(node.value.value for node in tree.body
                        if isinstance(node, ast.Assign)
                        and getattr(node.targets[0], "id", "") == "WM_CLASS")

        entry = [line.partition("=")[2] for line in
                 self._template().splitlines()
                 if line.startswith("StartupWMClass=")]
        self.assertEqual(entry, [declared])

        # ... and it has to actually reach Tk, which cannot be told afterwards.
        used = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "Tk"]
        self.assertTrue(used, "the panel does not create a Tk root")
        for call in used:
            names = [keyword.arg for keyword in call.keywords]
            self.assertIn("className", names)

    def test_the_icon_is_the_file_next_to_the_panel(self):
        clone = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, clone, ignore_errors=True)
        os.makedirs(os.path.join(clone, "gui"))
        picture = os.path.join(clone, "gui", ledpanel.ICON_NAME)
        open(picture, "wb").close()
        self.assertEqual(ledpanel.panel_icon(clone), picture)

    def test_a_missing_icon_falls_back_to_a_theme_name(self):
        # A menu entry with no picture at all looks broken, and an Icon= line
        # pointing at a file that is not there gets exactly that.
        clone = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, clone, ignore_errors=True)
        icon = ledpanel.panel_icon(clone)
        self.assertEqual(icon, ledpanel.FALLBACK_ICON)
        self.assertNotIn("/", icon, "a theme icon is a name, not a path")


class NotificationColourTest(unittest.TestCase):
    """The panel offers a few colours; the config file takes any."""

    PALETTES = (("ACHIEVEMENT_COLOR", ledpanel.ACHIEVEMENT_COLOURS),
                ("MESSAGE_COLOR", ledpanel.MESSAGE_COLOURS))

    def test_every_offered_colour_is_one_the_service_accepts(self):
        from steamos_led import notify
        for _key, palette in self.PALETTES:
            for label, value in palette:
                notify.parse_color(value)       # raises if it is not one
                self.assertTrue(label, value)

    def test_the_first_entry_is_what_the_service_ships_with(self):
        # The menu opens on the default, so the default has to be in it - and
        # first, because that is where "Gold (default)" belongs.
        for key, palette in self.PALETTES:
            self.assertEqual(palette[0][1].lower(),
                             config_module.DEFAULTS[key].lower(), key)

    def test_the_entries_are_distinct(self):
        # Labels are what the menu is keyed on, values what gets written.
        for key, palette in self.PALETTES:
            labels = [label for label, _value in palette]
            values = [value.lower() for _label, value in palette]
            self.assertEqual(len(set(labels)), len(labels), key)
            self.assertEqual(len(set(values)), len(values), key)


class MenuTranslationTest(unittest.TestCase):
    """Both drop-downs show one thing and write another."""

    CHOICES = (("Gold", "#ffd700"), ("Bronze", "#cd7f32"))

    def test_a_value_finds_its_entry(self):
        self.assertEqual(ledpanel.menu_label(self.CHOICES, "#cd7f32"), "Bronze")

    def test_the_case_of_a_hand_written_colour_does_not_matter(self):
        self.assertEqual(ledpanel.menu_label(self.CHOICES, "#CD7F32"), "Bronze")

    def test_a_value_the_menu_does_not_offer_has_no_entry(self):
        self.assertIsNone(ledpanel.menu_label(self.CHOICES, "#123456"))

    def test_an_entry_finds_its_value(self):
        self.assertEqual(ledpanel.menu_value(self.CHOICES, "Gold"), "#ffd700")

    def test_an_entry_nobody_put_there_is_its_own_value(self):
        # This is how a colour typed into the config file by hand survives
        # being shown and applied again.
        self.assertEqual(ledpanel.menu_value(self.CHOICES, "#123456"),
                         "#123456")

    def test_a_value_round_trips_through_its_entry(self):
        for _label, value in self.CHOICES:
            entry = ledpanel.menu_label(self.CHOICES, value)
            self.assertEqual(ledpanel.menu_value(self.CHOICES, entry), value)


class SensorMenuTest(unittest.TestCase):
    """The sensor setting is a path into /sys, which is no way to ask someone
    a question - so the menu is built out of what the machine reports."""

    def _sensor(self, chip, label, path=None, rank=(0, 0)):
        return {"chip": chip, "label": label, "rank": rank,
                "path": path or "/sys/class/hwmon/hwmon0/temp1_input"}

    def test_automatic_comes_first_and_says_what_it_picked(self):
        # Otherwise "Automatic" is a promise with no way to check it.
        chosen = self._sensor("k10temp", "Tctl")
        label, value = ledpanel.sensor_choices([chosen], chosen)[0]
        self.assertEqual(value, "auto")
        self.assertIn("k10temp", label)
        self.assertIn("Tctl", label)

    def test_a_machine_with_no_sensors_still_offers_automatic(self):
        self.assertEqual(ledpanel.sensor_choices([], None),
                         [("Automatic", "auto")])

    def test_every_sensor_is_offered_by_its_path(self):
        sensors = [self._sensor("k10temp", "Tctl", "/sys/a", (0, 0)),
                   self._sensor("nvme", "Composite", "/sys/b", (5, 4))]
        values = [value for _label, value in
                  ledpanel.sensor_choices(sensors, sensors[0])]
        self.assertEqual(values, ["auto", "/sys/a", "/sys/b"])

    def test_the_better_answer_is_listed_first(self):
        sensors = [self._sensor("nvme", "Composite", "/sys/b", (5, 4)),
                   self._sensor("k10temp", "Tctl", "/sys/a", (0, 0))]
        values = [value for _label, value in
                  ledpanel.sensor_choices(sensors, sensors[1])]
        self.assertEqual(values, ["auto", "/sys/a", "/sys/b"])

    def test_an_entry_is_a_name_and_not_a_measurement(self):
        # A temperature in the menu would be stale the moment it opened, and
        # the menu is a place to choose a sensor, not to read one.
        sensors = [self._sensor("k10temp", "Tctl", "/sys/hwmon0/temp1_input")]
        label = ledpanel.sensor_choices(sensors, sensors[0])[1][0]
        self.assertEqual(label, "k10temp Tctl")

    def test_an_unlabelled_sensor_keeps_its_own_name(self):
        sensors = [self._sensor("k10temp", "", "/sys/hwmon0/temp1_input")]
        label = ledpanel.sensor_choices(sensors, sensors[0])[1][0]
        self.assertEqual(label, "k10temp temp1")

    def test_a_configured_sensor_that_is_gone_is_kept_visible(self):
        # Dropping it would look like the setting had changed by itself.
        choices = ledpanel.sensor_choices([], None, current="/sys/unplugged")
        self.assertIn("/sys/unplugged", [value for _label, value in choices])
        self.assertIn("not found", choices[-1][0])

    def test_the_configured_sensor_is_not_listed_twice(self):
        sensors = [self._sensor("k10temp", "Tctl", "/sys/a")]
        choices = ledpanel.sensor_choices(sensors, sensors[0],
                                          current="/sys/a")
        self.assertEqual([value for _label, value in choices],
                         ["auto", "/sys/a"])

    def test_the_labels_are_distinct(self):
        # They are what the menu is keyed on, so two identical ones would make
        # a choice unreachable - and without a reading to tell them apart,
        # two inputs on one chip collide that much more easily.
        sensors = [self._sensor("k10temp", "", "/sys/hwmon0/temp1_input"),
                   self._sensor("k10temp", "", "/sys/hwmon0/temp2_input")]
        labels = [label for label, _value in
                  ledpanel.sensor_choices(sensors, sensors[0])]
        self.assertEqual(len(set(labels)), len(labels))

    def test_sensors_that_describe_themselves_the_same_are_told_apart(self):
        # An amdgpu with two "edge" inputs: identical lines, and one of them
        # would be unreachable in the menu.
        sensors = [self._sensor("amdgpu", "edge", "/sys/hwmon1/temp1_input"),
                   self._sensor("amdgpu", "edge", "/sys/hwmon2/temp1_input")]
        labels = [label for label, _value in
                  ledpanel.sensor_choices(sensors, sensors[0])]
        self.assertEqual(len(set(labels)), len(labels))
        self.assertIn("hwmon1/temp1", labels[1])
        self.assertIn("hwmon2/temp1", labels[2])


class PanelSettingsTest(unittest.TestCase):
    """The settings list has to agree with the configuration it edits."""

    TABLES = ("SETTINGS", "ADVANCED")

    def _tables(self):
        """Both settings tables, as AST nodes.

        Read out of the panel rather than imported: importing pulls in tkinter,
        which a build machine has no reason to have.
        """
        path = os.path.join(HERE, "..", "gui", "steamos-led-panel")
        with open(path) as handle:
            tree = ast.parse(handle.read())
        found = {}
        for node in tree.body:
            name = (getattr(node.targets[0], "id", "")
                    if isinstance(node, ast.Assign) else "")
            if name in self.TABLES:
                found[name] = node.value
        for name in self.TABLES:
            self.assertIn(name, found, "%s not found in the panel" % name)
        return [found[name] for name in self.TABLES]

    def _settings(self):
        """Every key the panel offers, on whichever tab."""
        return [entry.elts[0].value
                for table in self._tables() for entry in table.elts]

    def test_the_tabs_are_in_the_order_they_are_worked_through(self):
        # Everyday settings, then the ones you set once, then testing, then
        # repair - which is also the order of how often they are opened.
        path = os.path.join(HERE, "..", "gui", "steamos-led-panel")
        with open(path) as handle:
            tree = ast.parse(handle.read())
        tabs = [keyword.value.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "add"
                and getattr(getattr(node.func, "value", None), "id", "")
                == "notebook"
                for keyword in node.keywords if keyword.arg == "text"]
        self.assertEqual([tab.strip() for tab in tabs],
                         ["Settings", "Advanced settings", "Test",
                          "Status && repair"])

    def _firmware_max_leds(self):
        path = os.path.join(HERE, "..", "firmware", "led-client",
                            "platformio.ini")
        with open(path) as handle:
            limits = [int(value) for value
                      in re.findall(r"-D MAX_LEDS=(\d+)", handle.read())]
        self.assertTrue(limits, "no MAX_LEDS flags found in platformio.ini")
        return min(limits)

    def test_the_strip_length_stays_within_what_the_firmware_accepts(self):
        # A board rejects a frame longer than its MAX_LEDS and the strip goes
        # dark, so a slider that can ask for more is a slider that can break
        # the bar. The service still takes longer strips from the config file
        # for firmware built with a higher limit.
        entry = next(entry for table in self._tables() for entry in table.elts
                     if entry.elts[0].value == "LED_COUNT")
        self.assertLessEqual(entry.elts[4].value, self._firmware_max_leds())

    def test_every_slider_can_stop_on_both_of_its_ends(self):
        # The knob snaps to multiples of the step, so an end that is not one
        # cannot be set: the top of a range would be quietly unreachable, and
        # a bottom end could snap below what the service accepts.
        for entry in [entry for table in self._tables() for entry in table.elts]:
            key, kind = entry.elts[0].value, entry.elts[2].value
            step = entry.elts[5].value
            if kind not in ("int", "float"):
                self.assertIsNone(step, key)
                continue
            self.assertGreater(step, 0, key)
            if kind == "int":
                self.assertEqual(step, int(step), key)
            for edge in (entry.elts[3].value, entry.elts[4].value):
                self.assertAlmostEqual(
                    round(edge / step) * step, edge, places=6,
                    msg="%s cannot stop on %s in steps of %s"
                        % (key, edge, step))

    def test_no_setting_of_the_coupled_pair_is_refused(self):
        """The two temperature marks are the one pair of sliders that interact.

        validate() wants a span between them, and two independent knobs cannot
        express that - so the ranges are kept apart instead. If they ever
        overlap, Apply starts refusing settings the panel itself offered.
        """
        marks = {}
        for entry in [entry for table in self._tables()
                      for entry in table.elts]:
            if entry.elts[0].value in ("TEMPERATURE_MIN", "TEMPERATURE_MAX"):
                marks[entry.elts[0].value] = (entry.elts[3].value,
                                              entry.elts[4].value)
        self.assertEqual(len(marks), 2, "both marks should be on a tab")

        low, high = marks["TEMPERATURE_MIN"], marks["TEMPERATURE_MAX"]
        for cold in range(int(low[0]), int(low[1]) + 1):
            for hot in range(int(high[0]), int(high[1]) + 1):
                settings = dict(config_module.DEFAULTS)
                settings["TEMPERATURE_MIN"] = float(cold)
                settings["TEMPERATURE_MAX"] = float(hot)
                try:
                    config_module.validate(settings)
                except config_module.ConfigError as exc:
                    self.fail("the panel offers %d/%d, which the service "
                              "refuses: %s" % (cold, hot, exc))

    def test_the_two_tabs_do_not_offer_the_same_setting_twice(self):
        # They share one dict of widgets, so a key on both tabs would leave one
        # of the two silently ignored when Apply collects them.
        keys = self._settings()
        self.assertEqual(len(set(keys)), len(keys))

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
        for entry in [entry for table in self._tables() for entry in table.elts]:
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




class RoundedRectangleTest(unittest.TestCase):
    """The shapes ttk cannot draw itself.

    ttk has no corner radius, so rounded parts are supplied as images. There
    is no display here to look at them, so the pixels are checked instead.
    """

    WHITE, BLACK, RED = "#ffffff", "#000000", "#ff0000"

    def test_the_middle_is_filled(self):
        picture = roundrect.rows(20, 20, 6, self.WHITE, self.BLACK)
        self.assertEqual(picture[10][10], self.WHITE)

    def test_the_corners_are_cut_away(self):
        picture = roundrect.rows(20, 20, 8, self.WHITE, self.BLACK)
        for y, x in ((0, 0), (0, 19), (19, 0), (19, 19)):
            self.assertEqual(picture[y][x], self.BLACK,
                             "corner (%d,%d) should be background" % (x, y))

    def test_a_zero_radius_keeps_its_corners(self):
        # Sharp corners have to fall out of the same formula, or there would
        # be two code paths and only one of them tested.
        picture = roundrect.rows(20, 20, 0, self.WHITE, self.BLACK)
        self.assertEqual(picture[0][0], self.WHITE)
        self.assertEqual(picture[19][19], self.WHITE)

    def test_the_edges_are_antialiased(self):
        # A pixel exactly on the curve must be a blend, not one or the other -
        # that is the whole difference between round and jagged.
        picture = roundrect.rows(40, 40, 12, self.WHITE, self.BLACK)
        flat = [pixel for row in picture for pixel in row]
        blends = [pixel for pixel in flat
                  if pixel not in (self.WHITE, self.BLACK)]
        self.assertGreater(len(blends), 20,
                           "no intermediate shades: the edge is jagged")

    def test_the_size_is_what_was_asked_for(self):
        picture = roundrect.rows(30, 12, 4, self.WHITE, self.BLACK)
        self.assertEqual(len(picture), 12)
        self.assertEqual(len(picture[0]), 30)

    def test_a_radius_larger_than_the_shape_is_clamped(self):
        # Asking for a radius bigger than half the box would fold the shape
        # inside out; it has to come out as a pill instead.
        picture = roundrect.rows(20, 10, 500, self.WHITE, self.BLACK)
        self.assertEqual(picture[5][10], self.WHITE)
        self.assertEqual(picture[0][0], self.BLACK)

    def test_a_border_rings_the_shape(self):
        picture = roundrect.rows(30, 30, 8, self.WHITE, self.BLACK,
                                 border=self.RED, border_width=2)
        middle = picture[15][15]
        edge = picture[15][0]
        self.assertEqual(middle, self.WHITE, "the fill should survive")
        self.assertEqual(edge, self.RED, "the border should be on the edge")

    def test_the_fast_path_agrees_with_the_plain_one(self):
        """rows() measures a shape once and reuses it for every pixel.

        coverage() is the same arithmetic written the obvious way, per pixel,
        so it is the reference the shortcut has to keep matching.
        """
        for width, height, radius in ((20, 20, 6), (24, 14, (6, 6, 0, 0)),
                                      (13, 31, 0), (40, 12, 6.0)):
            picture = roundrect.rows(width, height, radius,
                                     self.WHITE, self.BLACK)
            for y in range(height):
                for x in range(width):
                    expected = roundrect.blend(
                        self.BLACK, self.WHITE,
                        roundrect.coverage(x, y, width, height, radius))
                    self.assertEqual(picture[y][x], expected,
                                     "%dx%d radius %r at (%d, %d)"
                                     % (width, height, radius, x, y))

    def test_a_pill_is_round_at_both_ends(self):
        picture = roundrect.pill(40, 12, self.WHITE, self.BLACK)
        self.assertEqual(picture[6][20], self.WHITE, "filled in the middle")
        self.assertEqual(picture[0][0], self.BLACK, "cut at the top left")
        self.assertEqual(picture[11][39], self.BLACK, "and the bottom right")

    def test_a_pill_keeps_its_full_height_in_the_middle(self):
        picture = roundrect.pill(40, 12, self.WHITE, self.BLACK)
        column = [picture[y][20] for y in range(12)]
        self.assertEqual(column[0], self.WHITE)
        self.assertEqual(column[-1], self.WHITE)

    def test_blending_ends_where_it_should(self):
        self.assertEqual(roundrect.blend(self.BLACK, self.WHITE, 0), self.BLACK)
        self.assertEqual(roundrect.blend(self.BLACK, self.WHITE, 1), self.WHITE)
        self.assertEqual(roundrect.blend(self.BLACK, self.WHITE, 0.5), "#808080")

    def test_the_put_string_has_one_group_per_row(self):
        # PhotoImage.put() wants {row} {row}; getting that wrong silently
        # produces a smeared image rather than an error.
        picture = roundrect.rows(3, 2, 0, self.WHITE, self.BLACK)
        text = roundrect.as_put_string(picture)
        self.assertEqual(text.count("{"), 2)
        self.assertEqual(text, "{#ffffff #ffffff #ffffff} "
                               "{#ffffff #ffffff #ffffff}")

    def test_it_works_on_a_dark_background_too(self):
        # The images are blended against whatever they sit on, so both
        # directions have to come out right.
        light = roundrect.rows(20, 20, 6, "#eff0f1", "#ffffff")
        dark = roundrect.rows(20, 20, 6, "#31363b", "#232629")
        self.assertEqual(light[10][10], "#eff0f1")
        self.assertEqual(dark[10][10], "#31363b")




class TabAndCheckboxShapeTest(unittest.TestCase):
    """Shapes for the two parts that looked wrong on a real screen."""

    FILL, BACK, EDGE = "#ffffff", "#000000", "#808080"

    def test_a_tab_can_be_round_on_top_and_square_below(self):
        picture = roundrect.rows(24, 14, (6, 6, 0, 0), self.FILL, self.BACK)
        self.assertEqual(picture[0][0], self.BACK, "top left is cut")
        self.assertEqual(picture[0][23], self.BACK, "top right is cut")
        self.assertEqual(picture[13][0], self.FILL, "bottom left stays square")
        self.assertEqual(picture[13][23], self.FILL, "and bottom right")

    def test_an_open_bottom_has_no_line_across_it(self):
        # This was the visible bug: a border along the bottom of every tab
        # reads as one stripe struck through the whole row - and nine-slice
        # scaling repeats those rows, so it is drawn again and again.
        picture = roundrect.rows(24, 14, (6, 6, 0, 0), self.FILL, self.BACK,
                                 border=self.EDGE, border_width=1,
                                 open_bottom=True)
        self.assertEqual(set(picture[-1]), {self.FILL},
                         "the bottom row must be plain fill")
        self.assertIn(self.EDGE, picture[7], "the sides keep their border")

    def test_without_that_the_bottom_line_is_there(self):
        # The opposite case, so the flag is shown to be doing the work.
        picture = roundrect.rows(24, 14, (6, 6, 0, 0), self.FILL, self.BACK,
                                 border=self.EDGE, border_width=1)
        self.assertIn(self.EDGE, picture[-1])

    def test_four_radii_have_to_be_four(self):
        with self.assertRaises(ValueError):
            roundrect.corner_radii((4, 4, 4))

    def test_one_radius_becomes_four_equal_corners(self):
        self.assertEqual(roundrect.corner_radii(5), (5.0, 5.0, 5.0, 5.0))

    def test_a_tick_lands_inside_its_box(self):
        picture = roundrect.rows(20, 20, 5, self.BACK, self.BACK)
        roundrect.draw_check(picture, self.FILL)
        ink = [(x, y) for y, row in enumerate(picture)
               for x, pixel in enumerate(row) if pixel != self.BACK]
        self.assertTrue(ink, "nothing was drawn")
        for x, y in ink:
            self.assertTrue(2 <= x <= 17 and 2 <= y <= 17,
                            "the tick ran outside the box at (%d,%d)" % (x, y))

    def test_a_tick_looks_like_a_tick(self):
        # Two strokes meeting low and left: the lowest ink should sit left of
        # centre, and the highest to the right of it.
        picture = roundrect.rows(20, 20, 5, self.BACK, self.BACK)
        roundrect.draw_check(picture, self.FILL)
        ink = [(x, y) for y, row in enumerate(picture)
               for x, pixel in enumerate(row) if pixel != self.BACK]
        lowest = max(ink, key=lambda point: point[1])
        highest = min(ink, key=lambda point: point[1])
        self.assertLess(lowest[0], 12, "the corner of the tick is on the left")
        self.assertGreater(highest[0], lowest[0], "and it rises to the right")

    def test_the_checkbox_is_big_enough_to_hit(self):
        # clam's own indicator is a handful of pixels; that was the complaint.
        path = os.path.join(HERE, "..", "gui", "steamos-led-panel")
        with open(path) as handle:
            tree = ast.parse(handle.read())
        size = next(node.value.value for node in tree.body
                    if isinstance(node, ast.Assign)
                    and getattr(node.targets[0], "id", "") == "CHECKBOX_SIZE")
        self.assertGreaterEqual(size, 16)

    def test_segment_coverage_is_thickest_on_the_line(self):
        on_line = roundrect.segment_coverage(5, 5, (0, 5), (10, 5), 3)
        beside = roundrect.segment_coverage(5, 9, (0, 5), (10, 5), 3)
        self.assertEqual(on_line, 1.0)
        self.assertEqual(beside, 0.0)




class ComboboxReadabilityTest(unittest.TestCase):
    """A read-only combobox draws its text as a selection.

    Without saying what the selection colours are, the label comes out in the
    highlight colours over the field colour - which is how "stretch" and
    "bloom" ended up pale grey on pale grey and all but unreadable on a real
    screen.
    """

    def setUp(self):
        path = os.path.join(HERE, "..", "gui", "steamos-led-panel")
        with open(path) as handle:
            self.source = handle.read()
        self.tree = ast.parse(self.source)

    def _map_states(self, style_name):
        """Which widget states style.map() names for this style."""
        states = set()
        for node in ast.walk(self.tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "map"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == style_name):
                continue
            for keyword in node.keywords:
                for inner in ast.walk(keyword.value):
                    if isinstance(inner, ast.Constant) and \
                            isinstance(inner.value, str):
                        states.add((keyword.arg, inner.value))
        return states

    def test_the_readonly_state_is_given_its_own_colours(self):
        states = self._map_states("TCombobox")
        self.assertIn(("foreground", "readonly"), states)
        self.assertIn(("fieldbackground", "readonly"), states)

    def test_the_selection_colours_are_pinned_too(self):
        # This is the actual fix: without it the text keeps the highlight
        # colours even though nothing is really selected.
        states = self._map_states("TCombobox")
        self.assertIn(("selectforeground", "readonly"), states)
        self.assertIn(("selectbackground", "readonly"), states)

    def test_a_disabled_one_is_muted_rather_than_invisible(self):
        states = self._map_states("TCombobox")
        self.assertIn(("foreground", "disabled"), states)




class TouchTargetTest(unittest.TestCase):
    """The controls have to be big enough to hit.

    This runs on a machine people also use handheld, with a trackpad rather
    than a mouse on a desk. Desktop guidelines put the smallest sensible
    target at around twenty pixels; ttk's own defaults are well under that,
    which is what made the first version fiddly.
    """

    MINIMUM = 20

    def setUp(self):
        path = os.path.join(HERE, "..", "gui", "steamos-led-panel")
        with open(path) as handle:
            tree = ast.parse(handle.read())
        self.sizes = {node.targets[0].id: node.value.value
                      for node in tree.body
                      if isinstance(node, ast.Assign)
                      and isinstance(node.value, ast.Constant)
                      and getattr(node.targets[0], "id", "").isupper()}

    def test_the_checkbox_is_hittable(self):
        self.assertGreaterEqual(self.sizes["CHECKBOX_SIZE"], self.MINIMUM)

    def test_the_slider_knob_is_hittable(self):
        self.assertGreaterEqual(self.sizes["KNOB_DIAMETER"], self.MINIMUM)

    def test_the_dropdown_arrow_is_hittable(self):
        # Not the full twenty: the arrow sits inside a field that is taller
        # than it, so the clickable area is larger than the glyph.
        self.assertGreaterEqual(self.sizes["ARROW_SIZE"], 16)

    def test_the_knob_stands_proud_of_its_groove(self):
        # A knob no bigger than the groove is invisible as a handle.
        self.assertGreater(self.sizes["KNOB_DIAMETER"],
                           self.sizes["TRACK_THICKNESS"])

    def test_the_groove_is_centred_evenly_in_the_knob_sized_image(self):
        # The groove is drawn inside an image as tall as the knob, so ttk
        # sizes the widget to fit the knob. An odd difference would put the
        # groove half a pixel off centre.
        slack = self.sizes["KNOB_DIAMETER"] - self.sizes["TRACK_THICKNESS"]
        self.assertEqual(slack % 2, 0)

    def test_the_groove_is_thick_enough_to_see(self):
        self.assertGreaterEqual(self.sizes["TRACK_THICKNESS"], 8)




class GameModeTest(unittest.TestCase):
    """Privileged actions cannot work in Game Mode, and must say so.

    pkexec needs a polkit agent to ask for a password with. Game Mode runs
    none, and pkexec's fallback wants a controlling terminal, which a program
    Steam launched does not have - so it exits 127 complaining about /dev/tty,
    which explains nothing at all to whoever pressed the button.
    """

    def test_gamescope_is_recognised(self):
        self.assertTrue(ledpanel.in_game_mode(
            {"GAMESCOPE_WAYLAND_DISPLAY": "gamescope-0"}))
        self.assertTrue(ledpanel.in_game_mode(
            {"XDG_CURRENT_DESKTOP": "gamescope"}))

    def test_a_desktop_session_is_not_game_mode(self):
        self.assertFalse(ledpanel.in_game_mode({"XDG_CURRENT_DESKTOP": "KDE"}))
        self.assertFalse(ledpanel.in_game_mode({}))

    def test_the_real_failure_is_recognised(self):
        # Verbatim from the machine.
        output = ("Error creating textual authentication agent: Error opening "
                  "current controlling terminal for the process ('/dev/tty'): "
                  "No such device or address")
        self.assertTrue(ledpanel.looks_like_no_auth_agent(output, 127))

    def test_a_command_that_worked_is_never_blamed_on_the_agent(self):
        self.assertFalse(ledpanel.looks_like_no_auth_agent("", 0))
        self.assertFalse(ledpanel.looks_like_no_auth_agent(
            "Error creating textual authentication agent", 0))

    def test_an_ordinary_failure_is_not_mistaken_for_it(self):
        # A refused password or a broken config must keep its own message.
        self.assertFalse(ledpanel.looks_like_no_auth_agent(
            "the new configuration was rejected, keeping the old one", 1))
        self.assertFalse(ledpanel.looks_like_no_auth_agent(
            "Request dismissed", 126))

    def test_the_advice_says_where_to_go_instead(self):
        self.assertIn("Desktop Mode", ledpanel.NO_AGENT_ADVICE)
        # And that not everything is lost here.
        self.assertIn("Test tab", ledpanel.NO_AGENT_ADVICE)


if __name__ == "__main__":
    unittest.main()
