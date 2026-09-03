# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The window, actually built.

Everything else about the panel is checked by reading its source, because a
build machine has neither tkinter nor a display. That catches misspelled style
names and settings that do not exist, and it cannot catch the other kind of
mistake at all: the rail that highlighted the wrong page was a perfectly
well-spelled line of code.

So this file builds the real window when there is something to build it with,
and skips otherwise. It is the only test here that needs a display.
"""

import io
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, os.path.join(HERE, "..", "gui"))

import appsettings                                          # noqa: E402
import kdetheme                                             # noqa: E402
import syssettings                                          # noqa: E402
from steamos_utility_center import cec                                 # noqa: E402
from steamos_utility_center import lact                                # noqa: E402
from test_lact import (FakeDaemon, DEVICES, STATS, CLOCKS,  # noqa: E402
                       NEW_DEVICES, NEW_STATS, NEW_CLOCKS, NEW_CONFIG,
                       CONFIG, RDNA3_FAN)

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:                                         # pragma: no cover
    tk = ttk = None


def _panel_module():
    """Import the panel, which has no .py on the end of it."""
    import importlib.machinery
    import importlib.util
    path = os.path.join(HERE, "..", "gui", "steamos-utility-center-panel")
    loader = importlib.machinery.SourceFileLoader("steamos_utility_center_panel", path)
    spec = importlib.util.spec_from_loader("steamos_utility_center_panel", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _has_display():
    if tk is None:
        return False
    try:
        root = tk.Tk()
    except tk.TclError:
        return False
    root.destroy()
    return True


@unittest.skipUnless(_has_display(), "no tkinter or no display")
class LiveWindowTest(unittest.TestCase):
    """Built for real, and asked what it ended up looking like."""

    @classmethod
    def setUpClass(cls):
        cls.panel_module = _panel_module()

    def setUp(self):
        self.root = tk.Tk()
        self.panel = self.panel_module.Panel(self.root)
        self.root.update_idletasks()

    def tearDown(self):
        self.root.destroy()

    def _pages(self):
        return list(self.panel.notebook.tabs())

    def _page_named(self, title):
        """The page whose tab says this.

        By name rather than by position: a page added anywhere but the end
        shifts every index after it, and what that looks like is half a dozen
        of these tests measuring the wrong page and failing about layout.
        """
        for page in self._pages():
            if self.panel.notebook.tab(page, "text").strip() == title:
                return page
        self.fail("no page called %r" % title)

    def test_every_page_has_an_entry_in_the_rail(self):
        entries = self.panel.rail.winfo_children()
        self.assertEqual(len(entries), len(self._pages()))
        self.assertEqual([entry.cget("value") for entry in entries],
                         self._pages())

    def test_the_rail_says_which_page_is_open(self):
        # The fault of this test: the code set the rail one time, at the build
        # step. A page change in the code therefore left the mark on the first
        # open page.
        #
        # Each call here is update() and not update_idletasks(). A page
        # selection posts a virtual event, and only the full call delivers it.
        for page in self._pages():
            self.panel.notebook.select(page)
            self.root.update()
            self.assertEqual(self.panel._rail_choice.get(), page,
                             self.panel.notebook.tab(page, "text"))

    def test_the_rail_opens_the_page_it_names(self):
        for entry in self.panel.rail.winfo_children():
            entry.invoke()
            self.root.update()
            self.assertEqual(self.panel.notebook.select(),
                             entry.cget("value"))

    def test_the_notebook_draws_no_tab_row_of_its_own(self):
        # Both at the same time give two lists of the same six pages. Tk does
        # not report an empty layout as empty. It puts a "null" element there.
        # So this test proves that no element in it draws a label.
        layout = str(self.root.tk.call("ttk::style", "layout",
                                       "TNotebook.Tab"))
        self.assertNotIn("label", layout)
        self.assertNotIn("Tab", layout)

    def test_every_page_draws(self):
        for page in self._pages():
            self.panel.notebook.select(page)
            self.root.update()

    def test_apply_is_offered_only_where_there_are_settings(self):
        for page in self._pages():
            self.panel.notebook.select(page)
            self.root.update()
            self.assertEqual(self.panel._apply_shown,
                             page in self.panel._settings_tabs,
                             self.panel.notebook.tab(page, "text"))

    def test_the_preview_runs_only_while_it_is_open(self):
        self.panel.notebook.select(self.panel.preview_tab)
        self.root.update()
        self.assertIsNotNone(self.panel._preview_job,
                             "the preview did not start")
        self.panel.notebook.select(self._page_named("Strip"))
        self.root.update()
        # The booked frame runs once more and then declines to book another.
        self.root.after(80, self.root.quit)
        self.root.mainloop()
        self.assertIsNone(self.panel._preview_job,
                          "the preview kept animating off-screen")

    def test_the_controls_in_a_column_stand_the_same_height(self):
        # A switch, a drop-down and a slider have no reason to agree on a
        # height unless they are made to, and a column alternating between
        # three of them has no rhythm whatever the padding between the rows.
        self.panel.notebook.select(self._page_named("Notifications"))
        self.root.update()
        heights = {}
        for key in ("NOTIFY", "ACHIEVEMENT_COLOR", "NOTIFY_STYLE"):
            _labels, controls = self.panel._rows[key]
            heights[key] = controls[0].winfo_height()
        self.assertEqual(len(set(heights.values())), 1, heights)

    def test_a_row_that_governs_others_has_the_same_room_either_side_of_it(self):
        # A row that opens a block has space above it and below it. The rows of
        # that block are close together below it. Equal space on both sides
        # keeps the row separate from the row below it.
        #
        # The measurement uses the Strip page, where this pattern is still
        # present. The notification blocks were the example before, and each of
        # them is one line now. See the flash rows. So the rainbow choice, with
        # the temperature marks indented below it, is the last example.
        self.panel.notebook.select(self._page_named("Strip"))
        # The block must be on the page for the measurement, and a choice now
        # decides which block is there. See the note above DEPENDS_ON.
        self.panel.vars["RAINBOW_SHOWS"][0].set("Temperature")
        self.root.update()
        rows = ("STANDBY_PULSE", "RAINBOW_SHOWS", "TEMPERATURE_MIN",
                "TEMPERATURE_MAX")
        # The measurement uses the name labels and not the controls. Each label
        # is a plain label of one height. A switch, a menu and a slider have
        # three different heights. A control that is shorter than its row also
        # stands in the centre of it. A measurement from control to control
        # therefore reports the widgets and the spacing together. This test is
        # about the rows.
        tops = [self.panel._rows[key][0][0].winfo_rooty() for key in rows]
        above, below, tight = (tops[index + 1] - tops[index]
                               for index in range(3))
        self.assertEqual(above, below,
                         "the row is not the same distance from the row above "
                         "it as from the one it governs")
        self.assertLess(tight, below,
                        "the rows it governs are no closer to each other than "
                        "it is to them")

    def test_a_notification_keeps_its_switch_colour_and_shape_on_one_line(self):
        # What the page is now: four notifications as four lines you can
        # compare down a column, rather than twelve near-identical rows you
        # had to scroll between. Three controls at the same height is the
        # whole of that claim.
        self.panel.notebook.select(self._page_named("Notifications"))
        self.root.update()
        for prefix, switch in (("ACHIEVEMENT", "NOTIFY_ACHIEVEMENTS"),
                               ("MESSAGE", "NOTIFY_MESSAGES"),
                               ("FRIEND", "NOTIFY_FRIEND_ONLINE"),
                               ("PHONE", "NOTIFY_PHONE")):
            tops = {key: self.panel._rows[key][1][0].winfo_rooty()
                    for key in (switch, prefix + "_COLOR", prefix + "_STYLE")}
            self.assertEqual(len(set(tops.values())), 1,
                             "%s is not on one line: %s" % (prefix, tops))

    def test_a_notification_s_controls_stand_clear_of_each_other(self):
        # Measured, because getting it wrong looks fine in the code: the slack
        # was on the switch column at first, which pushed the colour menus far
        # off to the right; moving it to the last column pulled them back so
        # far that the switch and the menu touched. Neither reads as a row.
        self.panel.notebook.select(self._page_named("Notifications"))
        self.root.update()
        for prefix, switch in (("ACHIEVEMENT", "NOTIFY_ACHIEVEMENTS"),
                               ("PHONE", "NOTIFY_PHONE")):
            parts = [self.panel._rows[key][1][0] for key in
                     (switch, prefix + "_COLOR", prefix + "_STYLE")]
            for left, right in zip(parts, parts[1:]):
                room = right.winfo_rootx() - (left.winfo_rootx()
                                              + left.winfo_width())
                self.assertGreaterEqual(room, self.panel_module.ROW_GAP,
                                        "%s: %dpx between two controls"
                                        % (prefix, room))
                self.assertLess(room, self.panel_module.GROUP_GAP * 3,
                                "%s: %dpx is a gap, not a row" % (prefix, room))

    def _greyed(self, key):
        return "disabled" in self.panel._rows[key][1][0].state()

    def _shown(self, key):
        """Whether the row is on the page at all.

        This is the second half of the answer. A row that depends on a switch
        becomes grey and keeps its position. A row that depends on a choice goes
        away. See the note above DEPENDS_ON. grid_info() returns nothing after a
        grid_remove, and it returns the old position again after a grid.
        """
        labels, controls = self.panel._rows[key]
        return bool((labels + controls)[0].grid_info())

    def test_the_slot_reaches_only_the_rows_its_own_choice_needs(self):
        """One menu, and two sets of rows waiting on two different answers.

        These rows become grey and do not go away, as each other row here does.
        But the choice makes them grey, and not the page. The temperature marks
        have no meaning while the slot shows the load. The two colours of the
        gauge have no meaning while the slot shows the temperature. Two incorrect
        answers together give four settings that no user can reach, and no text on
        the page gives the reason.
        """
        self.panel.notebook.select(self._page_named("Strip"))
        self.root.update()
        slot = self.panel.vars["RAINBOW_SHOWS"][0]
        #                                temperature  load
        for label, wanted in (("Temperature", (True, False)),
                              ("CPU and GPU load", (False, True)),
                              ("Steam's rainbow", (False, False)),
                              ("Fire", (False, False))):
            slot.set(label)
            self.root.update()
            self.assertEqual((self._shown("TEMPERATURE_MIN"),
                              self._shown("LOAD_CPU_COLOR")), wanted, label)
            for key in ("LOAD_GPU_COLOR", "LOAD_SWAP"):
                self.assertEqual(self._shown(key),
                                 self._shown("LOAD_CPU_COLOR"),
                                 "%s is not on the gauge's own rule" % key)
            self.assertEqual(self._shown("TEMPERATURE_SENSOR"),
                             self._shown("TEMPERATURE_MIN"),
                             "the marks and the sensor are one decision too")

    def test_a_desktop_scene_brings_the_same_rows_back(self):
        """Two places ask for these gauges, and either one puts them in play.

        The rainbow slot belongs to Game Mode. The desktop scenes no longer use
        that slot, so there is a second way to select the temperature gauge: the
        scene. That scene reads the marks and the sensor. A rule that hides them
        because the *other* mode shows another effect hides the settings that the
        bar uses. It also hides them on a different page from the changed page,
        and no user looks for them there.
        """
        self.panel.notebook.select(self._page_named("Strip"))
        self.root.update()
        slot = self.panel.vars["RAINBOW_SHOWS"][0]
        scene = self.panel.vars["DESKTOP_SCENE"][0]
        slot.set("Fire")                # neither block, as far as Game Mode goes
        self.root.update()
        self.assertFalse(self._shown("TEMPERATURE_MIN"))
        self.assertFalse(self._shown("LOAD_CPU_COLOR"))

        for label, wanted in (("Temperature", (True, False)),
                              ("CPU and GPU load", (False, True)),
                              ("Steam's rainbow", (False, False)),
                              ("Breath", (False, False))):
            scene.set(label)
            self.root.update()
            self.assertEqual((self._shown("TEMPERATURE_MIN"),
                              self._shown("LOAD_CPU_COLOR")), wanted, label)

        # And either one is enough on its own, which is the half a rule
        # written as "both" would get wrong the other way round.
        scene.set("Breath")
        slot.set("Temperature")
        self.root.update()
        self.assertTrue(self._shown("TEMPERATURE_MIN"))

    def test_a_row_taken_away_comes_back_where_it_was(self):
        """grid_remove rather than grid_forget, and the difference matters.

        forget removes the position, so the row returns at the next free
        position of the grid. On this page that position is below each other
        row, in the incorrect group.
        """
        self.panel.notebook.select(self._page_named("Strip"))
        slot = self.panel.vars["RAINBOW_SHOWS"][0]
        slot.set("Temperature")
        self.root.update()
        before = {key: self.panel._rows[key][0][0].grid_info()["row"]
                  for key in ("TEMPERATURE_MIN", "TEMPERATURE_SENSOR")}
        slot.set("Fire")
        self.root.update()
        self.assertFalse(self._shown("TEMPERATURE_MIN"))
        slot.set("Temperature")
        self.root.update()
        self.assertEqual({key: self.panel._rows[key][0][0].grid_info()["row"]
                          for key in before}, before)

    def test_the_page_closes_up_behind_a_row_it_took_away(self):
        """The whole point of taking it away rather than greying it.

        An empty grid row keeps the minimum height of each row, and that height
        keeps one spacing for a column of switches, menus and sliders. A minimum
        height below a row that is gone is the space of that row. Three such
        spaces below the slot give a page that looks like a draw fault.
        """
        self.panel.notebook.select(self._page_named("Strip"))
        slot = self.panel.vars["RAINBOW_SHOWS"][0]
        strip = ("LED_COUNT", "REVERSE", "MAX_BRIGHTNESS", "MIN_BRIGHTNESS",
                 "PATROL_DOTS", "SPEED", "STANDBY_PULSE", "RAINBOW_SHOWS",
                 "TEMPERATURE_MIN", "TEMPERATURE_MAX", "TEMPERATURE_SENSOR",
                 "LOAD_CPU_COLOR", "LOAD_GPU_COLOR", "LOAD_SWAP")

        def lowest():
            return max(self.panel._rows[key][0][0].winfo_rooty()
                       for key in strip if self._shown(key))

        slot.set("Fire")                # neither block is on the page
        for _ in range(4):
            self.root.update()
        self.assertEqual(lowest(),
                         self.panel._rows["RAINBOW_SHOWS"][0][0].winfo_rooty(),
                         "something is still sitting below the slot")
        bare = lowest()

        slot.set("Temperature")
        for _ in range(4):
            self.root.update()
        self.assertGreater(lowest(), bare, "the marks did not come back")

    def test_a_scene_with_no_colour_takes_the_colour_away(self):
        """The entry that means several values, which DEPENDS_ON could not say.

        Three of the five scenes take the colour, and two do not. One rule for
        each scene gives three rules that must all be true, and the colour then
        never appears. This test reads the window and not the table, because a
        user sees the control.

        The row goes away and does not become grey. A scene is a choice, and the
        colour belongs to the scenes with a colour. See the note above
        DEPENDS_ON.
        """
        self.panel.notebook.select(self._page_named("Desktop mode"))
        self.root.update()
        scene = self.panel.vars["DESKTOP_SCENE"][0]
        for label, wanted in (("One colour", False), ("Breath", False),
                              ("Patrol", False), ("Steam's rainbow", True),
                              ("Fire", True), ("Aurora", True),
                              ("Temperature", True), ("CPU and GPU load", True),
                              ("Off", True), ("Leave it to Steam", True)):
            scene.set(label)
            self.root.update()
            self.assertEqual(self._shown("DESKTOP_COLOR"), not wanted, label)

    def test_each_of_the_three_goes_by_a_rule_of_its_own(self):
        """Because no two of them apply to the same set of scenes.

        A rainbow has a brightness and a speed but no colour of yours; one
        colour standing still has a colour and a brightness but no speed; the
        temperature gauge has a brightness and nothing else, and the load
        gauge answers to none of the three. Greying them together would put
        settings that do something out of reach, which is the same to look at
        as a knob that is broken.
        """
        self.panel.notebook.select(self._page_named("Desktop mode"))
        self.root.update()
        scene = self.panel.vars["DESKTOP_SCENE"][0]
        #                       colour brightness speed   - True means hidden
        for label, wanted in (("One colour", (False, False, True)),
                              ("Breath", (False, False, False)),
                              ("Patrol", (False, False, False)),
                              ("Steam's rainbow", (True, False, False)),
                              ("Fire", (True, False, False)),
                              ("Aurora", (True, False, False)),
                              ("Temperature", (True, False, True)),
                              ("CPU and GPU load", (True, True, True)),
                              ("Off", (True, True, True)),
                              ("Leave it to Steam", (True, True, True))):
            scene.set(label)
            self.root.update()
            self.assertEqual(
                (not self._shown("DESKTOP_COLOR"),
                 not self._shown("DESKTOP_BRIGHTNESS"),
                 not self._shown("DESKTOP_SPEED")), wanted, label)

    def _corner(self, widget):
        """The very corner pixel of the swatch this widget wears.

        That pixel is background. A colour sample is a square with round
        corners, so the pixel outside a corner holds the background colour of
        the sample. That is the fault of this test: a background that is
        different from the colour of the widget shows a box behind the colour.
        """
        image = widget.cget("image")
        self.assertTrue(image, "the widget wears no swatch")
        return self.root.nametowidget(".").tk.call(image, "get", 0, 0)

    def _shade(self, name):
        colour = self.panel.roles.get(name, name)
        return tuple(int(colour[index:index + 2], 16)
                     for index in (1, 3, 5))

    def _rgb(self, answer):
        if isinstance(answer, str):
            answer = answer.split()
        return tuple(int(part) for part in answer)

    def test_a_greyed_field_carries_a_swatch_baked_for_being_greyed(self):
        """Reported, and only visible in the dark theme.

        A colour sample has no alpha channel, and one quarter of its pixels is a
        mix with the background. A sample with the normal shade therefore shows a
        box on a grey field. In the light theme the two shades are almost equal
        and nothing is visible, and that is why this fault reached a release.
        """
        # On a row a *switch* governs: those are the ones still greyed, and
        # greying is what this is about. A row waiting on a choice is taken
        # away, and a swatch nobody can see needs no shade.
        self.panel.notebook.select(self._page_named("Notifications"))
        switch = self.panel.vars["NOTIFY_ACHIEVEMENTS"][0]
        field = self.panel._widgets["ACHIEVEMENT_COLOR"]

        switch.set(True)
        self.root.update()
        self.assertEqual(self._rgb(self._corner(field)),
                         self._shade("surface"))

        switch.set(False)               # the colour has nothing to say here
        self.root.update()
        self.assertTrue(self._greyed("ACHIEVEMENT_COLOR"))
        self.assertEqual(
            self._rgb(self._corner(field)),
            self._shade(self.panel_module.material.disabled_container(
                self.panel.roles)),
            "the swatch is still baked for an ungreyed field")

    def test_what_shows_through_the_stipple_is_the_shade_around_it(self):
        """The other half of that fault, and the half the image cannot show.

        Tk draws an image on a disabled widget through a stipple of fifty per
        cent, and that stipple is the pattern over a grey colour. The background
        option of the style gives the colour in the holes, and the field element
        does not. With two different colours, each second pixel below the sample
        had the normal shade, and the field around them had the grey shade. That
        gives a box at the position of a sample with an incorrect background.

        For that reason this test does not read the sample. A test of the image
        passed while the screen was still incorrect. A measurement on a dark
        theme showed that before this correction.
        """
        field = self.panel._widgets["DESKTOP_COLOR"]
        field.state(["disabled"])
        self.root.update()
        style = ttk.Style(self.root)
        self.assertEqual(
            style.lookup("Field.TButton", "background", ["disabled"]),
            self.panel._ground_under(field),
            "the stipple shows a different shade than the swatch is baked on")

    def test_the_swatch_comes_back_when_the_field_does(self):
        # The other way round, which a fix that only ever greyed would pass.
        self.panel.notebook.select(self._page_named("Desktop mode"))
        scene = self.panel.vars["DESKTOP_SCENE"][0]
        field = self.panel._widgets["DESKTOP_COLOR"]
        scene.set("Steam's rainbow")
        self.root.update()
        scene.set("Breath")
        self.root.update()
        self.assertEqual(self._rgb(self._corner(field)),
                         self._shade("surface"))

    def test_applying_does_not_wake_a_setting_a_switch_holds_shut(self):
        """Reported: Apply ungreyed the colour under a rainbow scene.

        Each button in the window becomes disabled during a command, and a
        drop-down field is a button. A call that enables each of them at the end
        therefore returned settings that DEPENDS_ON must hold closed. A close and
        a new start of the window corrected it, and that made it look like a draw
        fault and not a logic fault.
        """
        self.panel.vars["NOTIFY_ACHIEVEMENTS"][0].set(False)
        self.root.update()
        self.assertTrue(self._greyed("ACHIEVEMENT_COLOR"))
        self.panel._set_busy(True)
        self.root.update()
        self.panel._set_busy(False)
        self.root.update()
        self.assertTrue(self._greyed("ACHIEVEMENT_COLOR"),
                        "the colour woke up when the command ended")
        # And the swatch went back with it, rather than being left behind.
        self.assertEqual(
            self._rgb(self._corner(self.panel._widgets["ACHIEVEMENT_COLOR"])),
            self._shade(self.panel_module.material.disabled_container(
                self.panel.roles)))

    def test_a_colour_button_is_rebaked_under_the_pointer(self):
        """The same fault on the Test page's colour dialog.

        An outlined button mixes its own fill below the pointer. A colour sample
        with the plain shade therefore shows a box on the hover shade. This test
        sets the states directly and does not move a pointer. The correction must
        draw the sample again at a state change, and a test with a real mouse
        examines X.
        """
        seen = []

        def look():
            # From inside the dialog's own loop: it is modal, so the
            # constructor does not return until the window is gone.
            window = next(child for child in self.root.winfo_children()
                          if isinstance(child, tk.Toplevel))
            button = next(widget for widget in self._all(window)
                          if isinstance(widget, ttk.Button)
                          and widget.cget("image"))
            seen.append(self._rgb(self._corner(button)))
            button.state(["active"])
            button.event_generate("<Enter>")
            self.root.update()
            self.root.update_idletasks()
            seen.append(self._rgb(self._corner(button)))
            window.destroy()

        self.root.after(150, look)
        self.panel_module.ColourDialog(self.root, self.panel._follow_shade)
        self.assertEqual(len(seen), 2, "the dialog never came up")
        self.assertNotEqual(seen[0], seen[1],
                            "the swatch kept the shade it was baked against")

    def _all(self, widget):
        for child in widget.winfo_children():
            yield child
            for deeper in self._all(child):
                yield deeper

    def test_a_slider_that_governs_nothing_reconfigures_nothing(self):
        """Reported: dragging a slider put the CPU and the GPU up.

        Each moved control asks each dependent row for its new state, and a drag
        sends one such event for each pixel of the movement. Almost no answer
        changes. A measurement gave eleven hundred questions and no change over
        fifty steps. But the code applied each answer: approximately thirty widget
        calls and five new colour samples, nine milliseconds for each step. The
        window also painted itself at the rate of the events.

        This test counts the calls and does not measure the time. A time on a
        build machine gives no useful number. The number of widget calls is the
        value that fell.
        """
        touched = []
        for key, (_labels, controls) in self.panel._rows.items():
            for control in controls:
                original = control.configure
                control.configure = (
                    lambda *a, __o=original, __k=key, **kw:
                    (touched.append(__k), __o(*a, **kw))[1])
        images = []
        original_photo = self.panel_module._photo
        self.panel_module._photo = (
            lambda picture, keep=True, __o=original_photo:
            (images.append(1), __o(picture, keep=keep))[1])
        self.addCleanup(setattr, self.panel_module, "_photo", original_photo)

        speed = self.panel.vars["SPEED"][0]
        for step in range(20):
            speed.set(0.5 + step * 0.1)
            self.root.update()
        self.assertEqual(touched, [], "rows were reconfigured for nothing")
        self.assertEqual(images, [], "colour chips were redrawn for nothing")

    def test_a_burst_of_moves_is_caught_up_with_once(self):
        # What a drag looks like between two redraws. Doing the work per
        # event rather than per redraw is what no amount of making the work
        # cheaper would fix.
        runs = []
        real = self.panel._catch_up
        self.panel._catch_up = lambda *a: (runs.append(1), real(*a))[1]
        speed = self.panel.vars["SPEED"][0]
        for step in range(20):
            speed.set(0.5 + step * 0.1)
        self.assertEqual(runs, [], "it did the work before the redraw")
        self.root.update()
        self.assertEqual(len(runs), 1, "once for the burst, not once each")

    def test_a_switch_that_does_govern_something_still_greys_it(self):
        """The control for the two above, and for the other half of the rule.

        A switch is not a choice: the setting under it belongs to the feature
        the switch turns on, so it is greyed and stays where you can see it
        exists. Only rows waiting on a choice are taken away.
        """
        self.panel.notebook.select(self._page_named("Notifications"))
        self.root.update()
        for state, wanted in ((False, True), (True, False)):
            self.panel.vars["NOTIFY_ACHIEVEMENTS"][0].set(state)
            self.root.update()
            self.assertEqual(self._greyed("ACHIEVEMENT_COLOR"), wanted, state)
            self.assertTrue(self._shown("ACHIEVEMENT_COLOR"),
                            "a switch does not take its setting away")

    def test_explanations_wrap_to_the_page_and_not_to_the_window(self):
        # The rail takes one sixth of the width. A wrap at the width of the
        # window therefore made the text wider than its page. A label with text
        # wider than its space is not wrapped, and the page cuts it. A read of
        # the notebook width during the Configure event of the window also
        # gives the width before the change. So this test reads the sizes here.
        for width in (1200, 860, 1400, 820):
            self.root.geometry("%dx900" % width)
            for _ in range(6):
                self.root.update()
            for page in self._pages():
                self.panel.notebook.select(page)
                # A page that has just been mapped needs more than one pass
                # before its width is the one it will keep.
                for _ in range(4):
                    self.root.update()
                # The labels of this page only. A page after its first display
                # still reports itself as mapped after a change of the page, and
                # it keeps the width of its last layout.
                belongs = str(self.root.nametowidget(page)) + "."
                for label in self.panel._wrapped:
                    if not str(label).startswith(belongs):
                        continue
                    self.assertLessEqual(
                        label.winfo_reqwidth(), label.winfo_width(),
                        "at %d px wide, %r is cut off"
                        % (width, label.cget("text")[:40]))

    def test_the_preview_strip_fills_the_room_it_is_given(self):
        # The strip is laid out for the canvas it ends up with rather than
        # drawn at one fixed size, so what has to hold is that seventeen LEDs
        # land inside it, in order and without overlapping, at any width.
        self.panel.notebook.select(self.panel.preview_tab)
        canvas = self.panel.preview_canvas
        for width in (900, 1360, 840):
            self.root.geometry("%dx900" % width)
            for _ in range(6):
                self.root.update()
            boxes = [canvas.coords(body)
                     for _halo, body, _spill in self.panel._preview_items]
            self.assertEqual(len(boxes), 17)
            self.assertGreaterEqual(min(box[0] for box in boxes), 0, width)
            self.assertLessEqual(max(box[2] for box in boxes),
                                 canvas.winfo_width(), width)
            self.assertLessEqual(max(box[3] for box in boxes),
                                 canvas.winfo_height(), width)
            for left, right in zip(boxes, boxes[1:]):
                self.assertLess(left[2], right[0],
                                "the LEDs overlap at %d px wide" % width)
            # And they are worth looking at: an LED thinner than this is a
            # line, not a preview.
            self.assertGreater(boxes[0][2] - boxes[0][0], 12, width)

    def _bar(self, page):
        holder = self.root.nametowidget(page)
        return next(child for child in holder.winfo_children()
                    if child.winfo_class() == "TScrollbar")

    def test_a_page_too_long_for_the_window_can_be_scrolled_to_its_end(self):
        # The settings pages are longer than a 1080p screen at a large desktop
        # font, so a user must reach the foot of such a page.
        #
        # This test uses the minimum size of the window. That is the worst case,
        # and it is also the one size for this test. Tk refuses a geometry below
        # the minimum. The value 1100x520 of an earlier version therefore left
        # the window at its minimum, and the page fitted there.
        self.root.geometry("%dx%d" % (self.panel_module.MIN_WIDTH,
                                      self.panel_module.MIN_HEIGHT))
        page = self._page_named("Notifications")        # the longest
        self.panel.notebook.select(page)
        for _ in range(6):
            self.root.update()
        canvas = self.panel._scrollers[page]
        self.assertLess(canvas.yview()[1], 1.0, "there is nothing to scroll")
        self.assertTrue(self._bar(page).winfo_ismapped(),
                        "a page that does not fit offers no scrollbar")
        canvas.yview_moveto(1.0)
        self.root.update()
        self.assertAlmostEqual(canvas.yview()[1], 1.0, places=2)

    def test_a_page_that_fits_keeps_its_scrollbar_out_of_the_way(self):
        self.root.geometry("1100x1000")
        page = self._page_named("Advanced")             # the shortest
        self.panel.notebook.select(page)
        for _ in range(6):
            self.root.update()
        self.assertFalse(self._bar(page).winfo_ismapped(),
                         "a page with room to spare still shows a scrollbar")

    def _long_page(self):
        """A page with more in it than the window can show, scrolled to."""
        self.root.geometry("%dx%d" % (self.panel_module.MIN_WIDTH,
                                      self.panel_module.MIN_HEIGHT))
        page = self._page_named("Notifications")        # the longest
        self.panel.notebook.select(page)
        for _ in range(8):
            self.root.update()
        return page

    def test_the_scrollbar_is_ours_and_carries_no_arrow_buttons(self):
        # The scrollbar of clam has a bevelled arrow button at each end of a
        # sunken box, and grip lines on the thumb. It is the one widget of this
        # window with a very old look. A bar with round ends in the margin
        # replaces it. So two things must hold: dress() drew the parts on the
        # screen, and neither end is a button.
        page = self._long_page()
        bar = self._bar(page)
        self.assertTrue(bar.winfo_ismapped())
        drawn = str(ttk.Style(self.root).layout("Vertical.TScrollbar"))
        self.assertIn("Material.Scroll.thumb", drawn)
        self.assertNotIn("arrow", drawn.lower(),
                         "the scrollbar still has arrow buttons")
        # And from the other side: what the pointer finds at either end.
        for y in (1, bar.winfo_height() - 2):
            self.assertIn(bar.identify(bar.winfo_width() // 2, y),
                          ("Material.Scroll.thumb", "Material.Scroll.trough"),
                          "an unexpected part at y=%d" % y)

    def test_the_scrollbar_thumb_can_still_be_dragged(self):
        # ttk finds the part to move by the *end* of the element name: *thumb,
        # *trough and *uparrow. Its bindings also use that end to tell a drag
        # from a page jump. With another name, such as Material.Scroll.bar, the
        # new bar draws correctly and does nothing under a drag.
        page = self._long_page()
        bar, canvas = self._bar(page), self.panel._scrollers[page]
        canvas.yview_moveto(0)
        self.root.update()
        start = canvas.yview()[0]
        middle = bar.winfo_width() // 2
        bar.event_generate("<Button-1>", x=middle, y=10)
        self.root.update()
        bar.event_generate("<B1-Motion>", x=middle,
                           y=10 + bar.winfo_height() // 3)
        self.root.update()
        bar.event_generate("<ButtonRelease-1>", x=middle, y=10)
        self.root.update()
        self.assertGreater(canvas.yview()[0], start,
                           "dragging the thumb moved nothing")

    def test_the_scrollbar_fits_the_room_the_pages_reserve_for_it(self):
        # _rewrap wraps the paragraphs of a page to the width after the bar
        # takes its side. A reserve below the real width of the bar puts the
        # last word of a sentence below the bar.
        page = self._long_page()
        self.assertLessEqual(self._bar(page).winfo_width(),
                             self.panel_module.SCROLLBAR_ROOM,
                             "the scrollbar is wider than the room kept free")

    def test_the_foot_of_the_window_keeps_its_place_on_a_short_screen(self):
        # pack gives out the space in the order of the calls. With the pages
        # first, and with all the space, a short window had no Apply row and no
        # status bar. Both came later, and no space was available.
        self.root.geometry("1100x520")
        self.panel.notebook.select(self._page_named("Notifications"))
        for _ in range(6):
            self.root.update()
        bottom = self.root.winfo_rooty() + self.root.winfo_height()
        for _side, button in self.panel._apply_buttons:
            self.assertTrue(button.winfo_ismapped(),
                            "%s was squeezed out" % button.cget("text"))
            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                                 bottom, button.cget("text"))
        self.assertTrue(self.panel.link.label.winfo_ismapped(),
                        "the status bar went missing")

    def test_a_page_asks_for_the_width_its_content_needs(self):
        # A canvas does not pass on the size of its content. It asks for its
        # own default width. Without a value here, the window opened at that
        # default, and each page went into it. The drop-downs were cut in the
        # middle of a word, and the sliders had no movement.
        for page in self._pages():
            canvas = self.panel._scrollers[page]
            inner = canvas.nametowidget(canvas.winfo_children()[0])
            self.assertGreaterEqual(
                canvas.winfo_reqwidth(), inner.winfo_reqwidth(),
                "%s asks for less width than it holds"
                % self.panel.notebook.tab(page, "text"))

    def test_nothing_on_a_page_is_squeezed_out_of_shape(self):
        # The width the window opens at has to be one every control fits in.
        self.panel._refit()
        for _ in range(6):
            self.root.update()
        self.panel.notebook.select(self._page_named("Strip"))
        # The sensor menu is the widest control of the page, and it is on the
        # page only while the slot shows the temperature.
        self.panel.vars["RAINBOW_SHOWS"][0].set("Temperature")
        for _ in range(4):
            self.root.update()
        for key in ("TEMPERATURE_SENSOR", "RAINBOW_SHOWS", "LED_COUNT"):
            _labels, controls = self.panel._rows[key]
            widget = controls[0]
            self.assertGreaterEqual(widget.winfo_width(),
                                    widget.winfo_reqwidth(), key)

    def test_the_window_never_opens_taller_than_the_screen(self):
        self.panel._refit()
        for _ in range(4):
            self.root.update()
        self.assertLessEqual(self.root.winfo_height(),
                             self.root.winfo_screenheight())

    def _apply_button(self):
        return self.panel._apply_buttons[0][1]

    def _is_dead(self):
        return "disabled" in self._apply_button().state()

    def test_apply_is_dead_while_the_window_and_the_file_agree(self):
        # A button that is always active reports nothing about the result of a
        # press. That state is also the answer to the question "did the last
        # Apply write this", and the window could not answer that question.
        self.assertTrue(self._is_dead())
        self.assertEqual(self.panel.unsaved.cget("text"), "")

    def test_the_count_says_how_many_settings_differ(self):
        self.panel.vars["SPEED"][0].set(2.5)
        self.root.update()
        self.assertFalse(self._is_dead())
        self.assertEqual(self.panel.unsaved.cget("text"), "1 unsaved change")
        self.panel.vars["NOTIFY"][0].set(False)
        self.root.update()
        self.assertEqual(self.panel.unsaved.cget("text"), "2 unsaved changes")

    def test_putting_a_setting_back_settles_it_again(self):
        was = self.panel.vars["SPEED"][0].get()
        self.panel.vars["SPEED"][0].set(2.5)
        self.root.update()
        self.assertFalse(self._is_dead())
        self.panel.vars["SPEED"][0].set(was)
        self.root.update()
        self.assertTrue(self._is_dead(), "a setting put back still counts")

    def test_a_slider_counts_as_well_as_a_switch(self):
        # Sliders were not followed at all before: only the controls another
        # row could hang off were, so dragging one changed nothing visible.
        self.panel.vars["GAMMA"][0].set(2.0)
        self.root.update()
        self.assertIn("GAMMA", self.panel._differences())

    def test_a_colour_the_file_spells_in_capitals_is_not_a_change(self):
        # The menus hold the display form of a value, and the file can hold the
        # same colour in another case. A comparison of the raw strings made the
        # window report an unsaved change that no user made.
        #
        # The colour of the window, in the other case. As a literal here, this
        # value stopped being the same colour at the next change of the
        # default, and the test then proved nothing.
        holding = self.panel._value_for(
            "ACHIEVEMENT_COLOR", self.panel.vars["ACHIEVEMENT_COLOR"][0].get())
        self.assertRegex(holding, r"^#[0-9a-f]{6}$")
        self.panel.config["ACHIEVEMENT_COLOR"] = holding.upper()
        self.panel._refresh_unsaved()
        self.root.update()
        self.assertNotIn("ACHIEVEMENT_COLOR", self.panel._differences())

    def test_a_colour_menu_shows_the_colour_it_holds(self):
        # A list of colours with the names only is the one control in a window
        # about light with no view of the colours.
        button = self.panel._widgets["ACHIEVEMENT_COLOR"]
        self.assertTrue(button.cget("image"),
                        "the chosen colour has no swatch on it")

    def test_a_menu_that_holds_no_colours_carries_no_swatch(self):
        # Told by the value rather than by a list kept by hand: anything the
        # file holds as #rrggbb is a colour, and nothing else is.
        button = self.panel._widgets["ACHIEVEMENT_STYLE"]
        self.assertFalse(button.cget("image"))

    def test_the_swatch_follows_what_is_chosen(self):
        button = self.panel._widgets["ACHIEVEMENT_COLOR"]
        first = button.cget("image")
        variable, _kind = self.panel.vars["ACHIEVEMENT_COLOR"]
        variable.set("Bronze")
        self.root.update()
        self.assertNotEqual(button.cget("image"), first)

    def test_a_colour_the_menu_never_offered_still_gets_a_swatch(self):
        # Editing the file by hand and pressing Reload is how these are meant
        # to be used from a terminal, so an unknown value becomes an entry.
        variable, _kind = self.panel.vars["MESSAGE_COLOR"]
        self.panel._show_values({"MESSAGE_COLOR": "#123456"})
        self.root.update()
        self.assertEqual(variable.get(), "#123456")
        self.assertTrue(self.panel._widgets["MESSAGE_COLOR"].cget("image"))

    def test_the_preview_follows_the_strip_length(self):
        # The code builds the canvas one time for each length and then moves
        # its items. A change of the setting must therefore build it again.
        # Without that, the page draws a strip of seventeen LEDs for each
        # machine.
        self.panel.notebook.select(self.panel.preview_tab)
        for _ in range(6):
            self.root.update()
        self.assertEqual(len(self.panel._preview_items), 17)
        self.panel.vars["LED_COUNT"][0].set(48)
        # Time has to pass: the rebuild happens on the next drawn frame, and
        # update() alone runs no timer that is not already due.
        self.root.after(200, self.root.quit)
        self.root.mainloop()
        self.assertEqual(len(self.panel._preview_items), 48)
        canvas = self.panel.preview_canvas
        boxes = [canvas.coords(body)
                 for _halo, body, _spill in self.panel._preview_items]
        self.assertLessEqual(max(box[2] for box in boxes),
                             canvas.winfo_width())
        for left, right in zip(boxes, boxes[1:]):
            self.assertLess(left[2], right[0], "the LEDs overlap")

    def _drop(self, key):
        """Open one drop-down and hand back the list it dropped.

        This moves the pointer away first. A row below the pointer takes the
        hover fill at the moment the list opens. A test of the filled row then
        reads the position of the mouse. On an X server with no window manager
        that position is the middle of the screen, and these lists open at
        approximately that position.
        """
        self.root.event_generate(
            "<Motion>", warp=True,
            x=self.root.winfo_screenwidth() - self.root.winfo_rootx() - 1,
            y=self.root.winfo_screenheight() - self.root.winfo_rooty() - 1)
        self.root.update()
        self.panel._open_menu(key)
        self.root.update()
        return self.panel._popup

    def test_a_list_opens_under_the_field_and_not_over_it(self):
        # It used to open over the field, lined up so the row you were on
        # covered it. That put the list somewhere different depending on which
        # entry was chosen, and hid the thing you had just pressed.
        popup = self._drop("ACHIEVEMENT_COLOR")
        field = self.panel._widgets["ACHIEVEMENT_COLOR"]
        self.assertEqual(popup.window.winfo_rooty(),
                         field.winfo_rooty() + field.winfo_height())
        self.assertEqual(popup.window.winfo_rootx(), field.winfo_rootx())
        popup.close()

    def test_it_opens_in_the_same_place_whatever_is_chosen(self):
        # The property the old placement did not have, and the reason this
        # one is worth a test of its own.
        variable = self.panel.vars["ACHIEVEMENT_COLOR"][0]
        seen = set()
        for label, _value in self.panel._menus["ACHIEVEMENT_COLOR"][:4]:
            variable.set(label)
            popup = self._drop("ACHIEVEMENT_COLOR")
            seen.add(popup.window.winfo_rooty())
            popup.close()
            self.root.update()
        self.assertEqual(len(seen), 1, seen)

    def test_a_list_with_no_room_below_opens_upwards(self):
        # Rather than off the bottom of the screen, or clamped back over the
        # field it came from.
        #
        # The window is moved first and the list opened after: moving it while
        # a list is open is a focus change, and a focus change is what takes a
        # list down.
        screen = self.root.winfo_screenheight()
        self.root.geometry("+0+%d" % max(0, screen - 120))
        self.root.update()

        popup = self._drop("ACHIEVEMENT_COLOR")
        field = self.panel._widgets["ACHIEVEMENT_COLOR"]
        top = popup.window.winfo_rooty()
        self.assertGreater(field.winfo_rooty() + field.winfo_height() +
                           popup.window.winfo_height(), screen,
                           "the field is not near enough the bottom to test")
        self.assertLessEqual(top + popup.window.winfo_height(), screen,
                             "it ran off the screen")
        self.assertLessEqual(top, field.winfo_rooty(),
                             "there was no room below, so it should go above")
        popup.close()

    def test_a_drop_down_opens_a_list_of_what_it_offers(self):
        popup = self._drop("ACHIEVEMENT_COLOR")
        self.assertIsNotNone(popup)
        self.assertEqual([label for _row, label, _value in popup.rows],
                         [label for label, _value
                          in self.panel._menus["ACHIEVEMENT_COLOR"]])
        popup.close()

    def test_the_row_you_are_on_is_filled_rather_than_ticked(self):
        # What a tk.Menu could not do: it marks the current entry with an
        # indicator and nothing else, where every other list on the desktop
        # fills the row.
        popup = self._drop("ACHIEVEMENT_COLOR")
        chosen = self.panel.vars["ACHIEVEMENT_COLOR"][0].get()
        for row, label, _value in popup.rows:
            wanted = ("secondary_container" if label == chosen
                      else "surface_container")
            self.assertEqual(str(row.cget("background")),
                             self.panel.roles[wanted], label)
        popup.close()

    def test_a_swatch_is_drawn_against_the_shade_it_lands_on(self):
        # A swatch has no alpha: it carries its background in its rounded
        # corners and in the gap beside it. One baked against the plain shade
        # sat on the filled row as a patch of the wrong colour, which is what
        # showed up behind the colours.
        plain = self.panel._swatch("#ffd700", "surface_container")
        filled = self.panel._swatch("#ffd700", "secondary_container")
        edge = plain.width() - 1                # the gap, which is all ground
        self.assertNotEqual(plain.get(edge, 0), filled.get(edge, 0))

    def test_the_filled_row_carries_a_swatch_of_its_own(self):
        popup = self._drop("ACHIEVEMENT_COLOR")
        chosen = self.panel.vars["ACHIEVEMENT_COLOR"][0].get()
        images = {label: str(row.cget("image"))
                  for row, label, _value in popup.rows}
        self.assertNotIn(images[chosen],
                         [name for label, name in images.items()
                          if label != chosen],
                         "the filled row reuses the plain row\'s swatch")
        popup.close()

    def test_picking_a_row_takes_the_value_and_closes(self):
        popup = self._drop("ACHIEVEMENT_COLOR")
        row, label, _value = popup.rows[1]
        row.event_generate("<Button-1>")
        self.root.update()
        self.assertEqual(self.panel.vars["ACHIEVEMENT_COLOR"][0].get(), label)
        self.assertIsNone(self.panel._popup, "the list stayed open")

    def test_a_drop_down_can_be_taken_down_from_outside(self):
        # A measurement on a real desktop gave this: a tk.Menu stayed above a
        # file manager that took the focus, and Tk gave the menu no control of
        # that. This list belongs to this project, so this code closes it.
        popup = self._drop("ACHIEVEMENT_COLOR")
        self.assertTrue(popup.window.winfo_ismapped())
        self.panel._dismiss_menus()
        self.root.update()
        self.assertIsNone(self.panel._popup)

    def test_nothing_grabs_while_a_list_is_open(self):
        # A measurement under a window manager, with another application above
        # the panel, gave this: with a grab of each type the list stayed on the
        # screen, and without a grab it closed. A grab takes the focus event
        # that reports the change to another application. An override-redirect
        # window gets that event only, and a list with no decoration must be
        # such a window.
        popup = self._drop("ACHIEVEMENT_COLOR")
        self.assertIsNone(self.root.grab_current(),
                          "a grab would swallow the notice that closes it")
        popup.close()

    def test_a_click_in_the_panel_takes_the_list_down(self):
        # What the grab used to do, done where it can be done without one.
        self._drop("ACHIEVEMENT_COLOR")
        self.panel._panel_click(
            type("Click", (), {"widget": self.panel.notebook})())
        self.root.update()
        self.assertIsNone(self.panel._popup, "the list stayed up")

    def test_pressing_the_field_again_closes_its_own_list(self):
        # The press took it down already; this is the release that follows,
        # and it must not put it straight back up.
        button = self.panel._widgets["ACHIEVEMENT_COLOR"]
        button.invoke()
        self.root.update()
        self.assertIsNotNone(self.panel._popup)
        button.invoke()
        self.root.update()
        self.assertIsNone(self.panel._popup, "the field re-opened its list")

    def test_opening_one_takes_down_the_last(self):
        first = self._drop("ACHIEVEMENT_COLOR")
        second = self._drop("MESSAGE_COLOR")
        self.assertIsNot(first, second)
        self.assertFalse(first.window.winfo_exists())
        second.close()

    def test_the_list_wears_the_window_s_own_colours(self):
        popup = self._drop("ACHIEVEMENT_SHAPE"
                           if "ACHIEVEMENT_SHAPE" in self.panel._menus
                           else "ACHIEVEMENT_STYLE")
        self.assertEqual(str(popup.window.cget("background")),
                         self.panel.roles["outline_variant"])
        chosen = self.panel.vars["ACHIEVEMENT_STYLE"][0].get()
        for row, label, _value in popup.rows:
            if label == chosen:
                continue                        # the filled one, checked above
            self.assertEqual(str(row.cget("foreground")),
                             self.panel.roles["on_surface"], label)
            self.assertEqual(str(row.cget("background")),
                             self.panel.roles["surface_container"], label)
        popup.close()

    def test_the_arrow_keys_walk_the_list(self):
        popup = self._drop("ACHIEVEMENT_COLOR")
        labels = [label for _row, label, _value in popup.rows]
        variable = self.panel.vars["ACHIEVEMENT_COLOR"][0]
        variable.set(labels[0])
        popup._step(1)
        self.assertEqual(variable.get(), labels[1])
        popup._step(-1)
        self.assertEqual(variable.get(), labels[0])
        # And it stops at the ends rather than wrapping into a surprise.
        popup._step(-1)
        self.assertEqual(variable.get(), labels[0])
        popup.close()

    def test_no_drop_down_anywhere_is_one_of_tks(self):
        # The two on Status & repair were left as comboboxes when the settings
        # pages changed over, so that page still had the old list on it: Tk's
        # own, posted under a grab it does not let go of, floating over
        # whatever was raised next. Walked rather than listed, because the
        # next one added would be the next one to go wrong this way.
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Combobox):
                    found.append(str(child))
                walk(child)

        walk(self.root)
        self.assertEqual(found, [], "a Tk drop-down is still in the window")

    def test_the_branch_and_the_firmware_drop_a_list_of_our_own(self):
        for key in ("branch", "firmware"):
            popup = self._drop(key)
            self.assertIsNotNone(popup, key)
            self.assertEqual([label for _row, label, _value in popup.rows],
                             [label for label, _value
                              in self.panel._menus[key]], key)
            # The whole point of it being ours: nothing is grabbed, so the
            # notice that another application took the focus still arrives.
            self.assertIsNone(self.root.grab_current(), key)
            popup.close()

    def test_the_firmware_field_still_names_the_build_to_flash(self):
        # The menu shows "ESP8266 (GPIO2)". The flasher gets "esp8266_gpio2".
        for label, environment in self.panel_module.ledpanel.FIRMWARE_ENVS:
            self.panel.firmware.set(label)
            self.assertEqual(self.panel._value_for("firmware", label),
                             environment)

    def test_a_fetch_refills_the_branch_list_without_losing_the_choice(self):
        self.panel.branch.set("a-branch-this-clone-does-not-have")
        self.panel._refresh_branches()
        self.assertIn(("a-branch-this-clone-does-not-have",) * 2,
                      self.panel._menus["branch"])

    def test_a_check_says_what_it_found_where_it_can_be_read(self):
        # The log is folded nearly always now, and it was the only place the
        # answer ever appeared.
        self.panel._say_update(*self.panel_module.ledpanel.update_verdict(
            "Already up to date with origin/main.\n"))
        self.root.update()
        self.assertIn("Up to date", self.panel.update_state.cget("text"))
        self.panel._say_update(*self.panel_module.ledpanel.update_verdict(
            "2 commit(s) waiting on origin/main:\n  abc one\n"))
        self.root.update()
        self.assertIn("2 updates waiting",
                      self.panel.update_state.cget("text"))

    def test_nothing_to_install_is_a_button_that_cannot_be_pressed(self):
        ledpanel = self.panel_module.ledpanel
        self.panel._say_update(ledpanel.UPDATE_CURRENT, "Up to date.")
        self.root.update()
        self.assertIn("disabled", self.panel.update_button.state())
        self.panel._say_update(ledpanel.UPDATE_AVAILABLE, "2 updates waiting.")
        self.root.update()
        self.assertNotIn("disabled", self.panel.update_button.state())

    def test_never_having_asked_is_not_the_same_as_nothing_waiting(self):
        # A disabled button before the first read is the window that refuses an
        # action that it can complete.
        self.assertEqual(self.panel._update_state,
                         self.panel_module.ledpanel.UPDATE_UNKNOWN)
        self.assertNotIn("disabled", self.panel.update_button.state())

    def test_choosing_another_branch_drops_the_old_answer(self):
        ledpanel = self.panel_module.ledpanel
        self.panel._say_update(ledpanel.UPDATE_CURRENT, "Up to date.")
        self.root.update()
        self.panel.branch.set("some-other-branch")
        self.root.update()
        self.assertEqual(self.panel._update_state, ledpanel.UPDATE_UNKNOWN)
        self.assertEqual(self.panel.update_state.cget("text"), "")
        self.assertNotIn("disabled", self.panel.update_button.state())

    def test_the_install_button_stays_dead_after_a_command_ends(self):
        # Everything comes back when a command ends; this one has a reason of
        # its own that outlives it, the way Apply has.
        ledpanel = self.panel_module.ledpanel
        self.panel._say_update(ledpanel.UPDATE_CURRENT, "Up to date.")
        self.panel._set_busy(True)
        self.root.update()
        self.panel._set_busy(False, 0)
        self.root.update()
        self.assertIn("disabled", self.panel.update_button.state())

    def test_a_command_that_fails_says_so_beside_the_status_lights(self):
        # The window has no log pane and no longer quotes the command either:
        # the line beside the lights is one fixed sentence saying where the
        # whole of it is.
        self.panel._set_busy(False, 0)
        self.root.update()
        self.assertFalse(self.panel.problem.winfo_ismapped(),
                         "a command that worked left a warning behind")
        self.panel._set_busy(False, 1)
        self.root.update()
        self.assertTrue(self.panel.problem.winfo_ismapped(),
                        "nothing said the command failed")
        self.assertIn(self.panel_module.PROBLEM_TEXT,
                      self.panel.problem.cget("text"))

    def test_the_next_command_that_works_takes_the_warning_away(self):
        # A warning left standing beside a command that has just worked is
        # worse than no warning at all.
        self.panel._set_busy(False, 1)
        self.root.update()
        self.assertTrue(self.panel.problem.winfo_ismapped())
        self.panel._set_busy(False, 0)
        self.root.update()
        self.assertFalse(self.panel.problem.winfo_ismapped())

    def test_it_says_the_same_thing_whatever_failed(self):
        """Deliberately, and this is the test for the decision.

        The bar showed the last line of the failed command before. That line
        shares a bar with two indicators, so the bar cut a real error to the
        available width. The cut removed the end of the line, and the end gives
        the fault. A short text that is always complete is better than a reason
        that the bar sometimes cuts. The reason is still on stderr.
        """
        seen = set()
        for output in ("cp: cannot create regular file: Permission denied\n",
                       "cannot run: [Errno 2] No such file or directory\n",
                       ""):
            if output:
                self.panel._write(output)
            self.panel._set_busy(False, 1)
            self.root.update()
            seen.add(self.panel.problem.cget("text"))
        self.assertEqual(len(seen), 1, seen)

    def test_it_says_where_the_rest_of_it_is(self):
        # A warning with no next step in it is a warning somebody lives with.
        said = self.panel_module.PROBLEM_TEXT.lower()
        self.assertIn("terminal", said)
        self.assertIn("failed", said)

    def test_the_line_fits_the_narrowest_window_with_both_lights_showing(self):
        """Measured, because nothing trims it any more.

        The bar cut this text to its width before. A fixed sentence needs no
        cut, but only while it fits. The space is the width after the two
        indicators. This test measures at the minimum width of the panel. That
        width is small enough for a failure, and this test must find it before a
        user does.
        """
        self.panel._set_busy(False, 1)
        self.root.geometry("%dx900" % self.panel_module.MIN_WIDTH)
        for _ in range(6):
            self.root.update()
        line = self.panel.problem
        self.assertEqual(line.winfo_width(), line.winfo_reqwidth(),
                         "%r does not fit the bar" % line.cget("text"))
        # And the bar is still one line tall, which is the thing that would
        # move every page above it.
        self.assertEqual(self.panel.statusbar.winfo_height(),
                         self.panel.link.label.winfo_reqheight())

    def test_the_foot_reports_and_does_not_label(self):
        """The version used to sit at the right-hand end of this bar.

        A user asked for that change, and it is correct. The foot of each page
        holds the values that change during a session: the connection of the bar,
        and the result of the last command. A version number is neither of the
        two. It is still on the About page and on the status page, and a user
        reads it there.
        """
        said = " ".join(child.cget("text")
                        for child in self.panel.statusbar.winfo_children()
                        if isinstance(child, ttk.Label))
        self.assertNotIn(self.panel_module.VERSION, said, said)
        self.assertNotIn("SteamOS Utility Center", said, said)

    def test_but_the_version_is_still_somewhere_to_be_found(self):
        # The removal of the label must not remove the number. A user gives the
        # version first in each report.
        self.panel._open_section("app")
        for _ in range(4):
            self.root.update()
        said = []
        for widget in self._every_widget():
            try:
                said.append(str(widget.cget("text")))
            except tk.TclError:                 # not a widget that carries any
                continue
        self.assertIn(self.panel_module.VERSION, " ".join(said))

    def test_a_terminal_that_went_away_does_not_break_the_window(self):
        """The README tells people to start this from a terminal.

        A close of that terminal, with the window open, leaves a broken pipe at
        the other end of stderr. A write to it raises an exception. Without this
        code, that exception comes out of the middle of an install.
        """
        class Gone:
            def write(self, _text):
                raise BrokenPipeError(32, "Broken pipe")

        was, sys.stderr = sys.stderr, Gone()
        try:
            self.panel._write("the install carried on\n")
        finally:
            sys.stderr = was

    def test_what_a_command_printed_goes_to_stderr(self):
        # Whoever wants a log runs the install script in a terminal, where the
        # output is selectable, searchable and outlives the window.
        said = io.StringIO()
        was, sys.stderr = sys.stderr, said
        try:
            self.panel._write("halfway through\n")
        finally:
            sys.stderr = was
        self.assertEqual(said.getvalue(), "halfway through\n")

    def test_a_running_command_deadens_every_button_that_starts_one(self):
        # There was a moving rule under the title as well. It said only what
        # the greyed buttons already say, and said it by animating four pixels
        # sixty times a second for the length of an install.
        self.panel._set_busy(True)
        self.root.update()
        for button in self.panel._busy_buttons:
            self.assertIn("disabled", button.state(), button.cget("text"))
        # The ones that only fold something away stay live: they start
        # nothing, so greying them would take away a control for no reason.
        self.assertTrue(self.panel._fold_buttons, "no fold buttons to check")
        for name in self.panel._fold_buttons:
            self.assertNotIn("disabled",
                             self.root.nametowidget(name).state())
        self.panel._set_busy(False, 0)

    def test_the_window_animates_nothing_while_a_command_runs(self):
        """The rule is gone, and nothing was left booking frames for it.

        A timer for a canvas that no longer exists is not visible and not
        harmless. It calls that canvas, and Tk reports an invalid command name
        with almost no stack.
        """
        self.assertFalse(hasattr(self.panel, "progress"))
        source = self.panel_module.__file__ or ""
        self.assertTrue(source)
        with open(source) as handle:
            self.assertNotIn("Progress", handle.read())

    def test_apply_keeps_its_own_reason_to_be_dead_afterwards(self):
        # Each control becomes active again at the end of a command. Apply is
        # the exception, because it has a reason of its own to stay disabled.
        self.panel._set_busy(True)
        self.root.update()
        self.panel._set_busy(False, 0)
        self.root.update()
        self.assertTrue(self._is_dead(), "Apply came back with nothing to do")
        self.panel.vars["SPEED"][0].set(2.5)
        self.root.update()
        self.assertFalse(self._is_dead())

    def _sections(self):
        return [entry[0] for entry in self.panel_module.SECTIONS] + ["about"]

    def test_every_section_has_an_entry_in_the_sidebar(self):
        self.assertEqual(sorted(self.panel._sidebar_entries),
                         sorted(self._sections()))
        self.assertEqual(sorted(self.panel._section_pages),
                         sorted(self._sections()))

    def test_the_sidebar_says_which_section_is_open(self):
        # One entry is always selected, and never two. The mark is the one
        # element on the screen that gives the open page. An old mark
        # therefore gives an incorrect page.
        for key in self._sections():
            self.panel._open_section(key)
            self.root.update()
            lit = [name for name, entry
                   in self.panel._sidebar_entries.items() if entry.selected]
            self.assertEqual(lit, [key])

    def test_the_header_names_the_section_that_is_open(self):
        for key, title, _subtitle, _icon in self.panel_module.SECTIONS:
            self.panel._open_section(key)
            self.root.update()
            self.assertEqual(self.panel.section_title.cget("text"), title)

    def test_clicking_a_sidebar_entry_opens_its_section(self):
        # Through the binding rather than by calling _open_section, because
        # the binding is the part a canvas has to do for itself: a ttk widget
        # would have brought its own command, and this one does not.
        entry = self.panel._sidebar_entries["about"]
        entry.canvas.event_generate("<Button-1>", x=10, y=10)
        self.root.update()
        self.assertEqual(self.panel.section, "about")
        self.assertEqual(self.panel.sections.select(),
                         self.panel._section_pages["about"])

    def test_apply_is_offered_only_where_there_are_settings(self):
        """Two levels to ask now, and the answer has to come from both.

        A section that is one table of settings always has some; the strip has
        some on four of its seven pages; the two unbuilt sections and About
        never do. Apply standing under a page with nothing on it is a button
        that would write a file nobody edited.
        """
        for key, wanted in (("strip", True), ("power", True),
                            ("cec", False), ("keyboard", True),
                            ("status", False), ("app", False),
                            ("about", False)):
            self.panel._open_section(key)
            self.root.update()
            self.assertEqual(self.panel._apply_shown, wanted, key)

        self.panel._open_section("strip")
        for title, wanted in (("Strip", True), ("Advanced", True),
                              ("Preview", False), ("Test", False)):
            self.panel.notebook.select(self._page_named(title))
            self.root.update()
            self.assertEqual(self.panel._apply_shown, wanted, title)

    def _labels_under(self, widget):
        """Every piece of text under a widget, however deeply nested."""
        found = []
        for child in widget.winfo_children():
            if child.winfo_class() in ("TLabel", "TButton"):
                try:
                    found.append(str(child.cget("text")))
                except tk.TclError:                         # pragma: no cover
                    pass
            found += self._labels_under(child)
        return found

    def test_the_installation_and_the_board_are_on_different_pages(self):
        """Where the three blocks of the old Status & repair page went.

        The install check and the update are about the complete toolbox, and they
        are a section now. The ESP flash is the one block about the strip only, and
        it is on the Test page. It stands beside the self-test, and that test
        reports a board that needs a new firmware.

        This reads the built window and not the source, because the subject is the
        page where a user finds the button.
        """
        self.panel._open_section("status")
        self.root.update()
        status = self._labels_under(
            self.root.nametowidget(self.panel._section_pages["status"]))
        self.assertIn("Rebuild and reinstall", status)
        self.assertTrue(any("update" in text.lower() for text in status),
                        status)
        self.assertFalse(any("flash" in text.lower() and "bar" not in
                             text.lower() for text in status), status)

        self.panel._open_section("strip")
        self.panel.notebook.select(self._page_named("Test"))
        self.root.update()
        tests = self._labels_under(
            self.root.nametowidget(self.panel.notebook.select()))
        self.assertIn("Self-test", tests)
        self.assertTrue(any("firmware" in text.lower() for text in tests),
                        tests)
        self.assertNotIn("Rebuild and reinstall", tests)

    def test_each_status_block_folds_on_its_own(self):
        """One fold per part now, where there used to be one for the page.

        The page was the LED checklist and a single Details button. It is one
        block per part of the toolbox, so a fold that opened all of them would
        be the old page again with more in it.
        """
        # This test builds the condition and does not assume it. On a machine
        # with no settings, only the LED bar has a detail block. A test that
        # needs two blocks then reads the state of the test machine.
        self.panel.power = {"CPU_GOVERNOR": "powersave"}
        self.panel.system = {syssettings.LAYOUT: "de"}
        self.panel._open_section("status")
        self.panel.refresh_status()
        for _ in range(4):
            self.root.update()
        keys = [part.key for part in self.panel._read_parts() if part.detail]
        self.assertGreater(len(keys), 1, "only one part has any detail")

        was = set(self.panel._open_parts)
        self.panel._fold_part(keys[-1])
        for _ in range(4):
            self.root.update()
        # Exactly that one changed, and nothing else moved with it.
        self.assertEqual(self.panel._open_parts ^ was, {keys[-1]})
        self.assertLessEqual(self.root.winfo_height(),
                             self.root.winfo_screenheight())

    def test_a_broken_part_unfolds_itself_once_and_stays_where_it_is_put(self):
        """Opening itself is right; opening again after you shut it is not.

        The panel reads the page again at the end of each command, and during an
        install that is every few seconds. A block that opens at each read is a
        block that no user can close.
        """
        self.panel._open_section("status")
        for _ in range(6):
            self.root.update()
        broken = [part for part in self.panel._read_parts()
                  if part.ok is False and part.detail]
        if not broken:                                       # pragma: no cover
            self.skipTest("nothing on this machine is broken to unfold")
        key = broken[0].key
        self.assertIn(key, self.panel._open_parts, "it stayed folded")
        self.panel._fold_part(key)
        for _ in range(4):
            self.root.update()
        self.assertNotIn(key, self.panel._open_parts)
        self.panel.refresh_status()
        for _ in range(4):
            self.root.update()
        self.assertNotIn(key, self.panel._open_parts,
                         "it opened itself again after being folded away")

    def test_the_wheel_scrolls_the_section_in_front_of_you(self):
        """Not the strip's page, which is what one notebook meant.

        _open_page is the fix and this is the case it was wrong for: with a
        section other than the strip open, the wheel was still asking the
        inner notebook what was on screen and scrolling a page nobody could
        see.
        """
        self.panel._open_section("about")
        self.root.update()
        self.assertEqual(self.panel._open_page(),
                         self.panel._section_pages["about"])
        self.panel._open_section("strip")
        self.panel.notebook.select(self._page_named("Notifications"))
        self.root.update()
        self.assertEqual(self.panel._open_page(), self.panel.notebook.select())

    def test_the_foot_of_the_window_says_whether_the_bar_is_there(self):
        # Three states and three colours. The state is unknown before the first
        # read, and then it is good or bad. A grey dot with "looking" is
        # correct there. A red dot reports a fault for a question with no
        # answer.
        seen = {}
        for connected in (None, True, False):
            self.panel._say_link(connected)
            self.root.update()
            seen[connected] = self.panel.link.canvas.itemcget(
                self.panel.link.dot, "fill")
            self.assertTrue(self.panel.link.label.cget("text").strip())
        self.assertEqual(len(set(seen.values())), 3, seen)
        self.assertEqual(seen[True], self.panel.roles["positive"])
        self.assertEqual(seen[False], self.panel.roles["error"])

    def test_the_led_indicator_is_always_in_the_bar(self):
        # It answers a question this whole window is about, so unlike the
        # HDMI CEC one there is no state in which it has nothing to say.
        self.assertTrue(self.panel.link.canvas.winfo_ismapped())
        self.assertTrue(self.panel.link.label.winfo_ismapped())

    def _every_widget(self, widget=None, found=None):
        found = [] if found is None else found
        widget = self.root if widget is None else widget
        found.append(widget)
        for child in widget.winfo_children():
            self._every_widget(child, found)
        return found

    def test_the_cpu_menus_are_built_from_the_machine(self):
        """Not from a list in the panel, which is the whole point of them.

        This build machine has no cpufreq, so the governor menu holds only
        "leave it alone". A list in the code gets that case wrong, because it
        offers governors that this machine does not have.
        """
        self.panel._open_section("power")
        self.root.update()
        offered = [value for _label, value
                   in self.panel._menus["CPU_GOVERNOR"]]
        self.assertEqual(offered,
                         [""] + list(self.panel_module.power.governors()))

    def test_a_machine_with_no_preference_file_gets_no_row_for_one(self):
        """Never built, rather than built and greyed.

        A driver in passive mode has no energy_performance_preference, and this
        machine has no cpufreq. A menu with no entry is a row for a setting that
        does not exist. The row is also not in self.vars, so no code collects it
        and no code compares it.
        """
        self.panel._open_section("power")
        self.root.update()
        self.assertEqual(self.panel_module.power.epp_values(), ())
        self.assertNotIn("CPU_EPP", self.panel._rows)
        self.assertNotIn("CPU_EPP", self.panel.vars)
        # And Apply is still offered, for the governor that is there.
        self.assertTrue(self.panel._apply_shown)

    def test_the_stored_preference_survives_a_page_that_cannot_show_it(self):
        # Collected from the file rather than from the widgets, so a config
        # written on a machine that has an EPP is not emptied by opening the
        # panel on one that does not.
        self.panel.power = dict(self.panel.power, CPU_EPP="balance_power")
        self.assertEqual(self.panel._collect_power()["CPU_EPP"],
                         "balance_power")

    def test_the_cpu_settings_never_reach_the_led_config_or_a_profile(self):
        # Third file, third writer. A governor in the LED service's config is
        # a key it does not know, and in a profile it would change the CPU
        # every time somebody tried a different look for the bar.
        self.panel._open_section("power")
        self.root.update()
        values = self.panel._collect()
        for key in ("CPU_GOVERNOR", "CPU_EPP"):
            self.assertNotIn(key, values)
            self.assertNotIn(key, self.panel_module.ledpanel.profile_text(
                values))
        merged = dict(self.panel_module.config_module.DEFAULTS)
        merged.update(values)
        self.panel_module.config_module.validate(merged)

    def test_nothing_stands_on_a_ground_its_picture_was_not_drawn_against(self):
        """The bug this window keeps making, caught by arithmetic this time.

        Every rounded thing here is a nine-slice image, and an image keeps its
        own corners whatever it is stretched over. So a widget wearing one has
        to stand on a parent of exactly the colour that picture was drawn
        against, or the corners show as a box of the wrong shade around it.

        Four times now: the inner list when it was given a card, a card put
        inside another card, the Output handle when the page changed colour
        underneath it, and every button in the Apply row at the same moment.
        Each was found by looking at a screenshot. dress() records the ground
        of each picture as it makes it, so this can be found by walking.
        """
        grounds = self.panel.roles["_grounds"]
        self.assertTrue(grounds, "dress recorded no grounds at all")
        style = ttk.Style(self.root)

        def background(widget):
            if isinstance(widget, tk.Canvas):
                return str(widget.cget("background"))
            try:
                named = str(widget.cget("style")) or widget.winfo_class()
            except tk.TclError:                 # no -style option: a toplevel
                return str(widget.cget("background"))
            return str(style.lookup(named, "background"))

        checked = 0
        for widget in self._every_widget():
            try:
                named = str(widget.cget("style"))
            except tk.TclError:
                continue
            if named not in grounds:
                continue
            parent = widget.nametowidget(widget.winfo_parent())
            on = background(parent)
            if not on:                          # nothing to compare against
                continue
            checked += 1
            self.assertEqual(
                grounds[named].lower(), on.lower(),
                "%s wears a picture drawn against %s and stands on %s"
                % (named, grounds[named], on))
        self.assertGreater(checked, 10, "hardly anything was checked")

    def test_no_label_is_drawn_on_a_ground_it_does_not_stand_on(self):
        """The same bug one layer up from the pictures, and a fifth instance.

        The test above reads the nine-slice images. This test reads the second
        half of the same rule: the background of a label, from each source. There
        are two incorrect methods, and this test finds both. The first is a manual
        colour that is not the colour of the parent. The second is a style with the
        colour of the page on a widget that stands on a card.

        A measurement found this, and not a look at the screen. The About page,
        both placeholder pages and the new CEC page each gave surface_container_low
        to labels on a card with the colour surface_container_lowest. Each
        explanation on those pages therefore had a band of a third colour behind
        it. Three pages with one fault are one habit and not three errors, and for
        that reason a test checks it.
        """
        style = ttk.Style(self.root)

        def ground(widget):
            # A tk widget has no style, and the caller must give it a colour. A
            # themed widget takes the colour of its style, and a caller can give
            # it another colour. This test finds that second case.
            if isinstance(widget, (tk.Canvas, tk.Text, tk.Listbox)):
                return ""
            try:
                own = str(widget.cget("background"))
            except tk.TclError:
                own = ""                # no such option: it has only a style
            if not own:
                try:
                    named = str(widget.cget("style"))
                except tk.TclError:                          # pragma: no cover
                    return ""
                own = str(style.lookup(named or widget.winfo_class(),
                                       "background"))
            return own.lower()

        # The key is the widget, because _every_widget reads the complete
        # window and not the open section. Without that key, this test reports
        # one incorrect label one time for each visited section.
        checked, wrong = 0, {}
        for section, _t, _s, _i in self.panel_module.SECTIONS + (
                self.panel_module.ABOUT,):
            self.panel._open_section(section)
            self.root.update()
            for widget in self._every_widget():
                if not isinstance(widget, ttk.Label):
                    continue
                mine = ground(widget)
                theirs = ground(widget.nametowidget(widget.winfo_parent()))
                if not mine or not theirs:
                    continue
                checked += 1
                if mine != theirs:
                    # Each of them, and not the first one. They occur in
                    # groups: six of them came on the day of this test. A
                    # test that stops at the first one makes one pass into
                    # six runs.
                    wrong[str(widget)] = (
                        "%r drawn on %s, stands on %s"
                        % (str(widget.cget("text"))[:48], mine, theirs))
        self.assertEqual(sorted(wrong.values()), [],
                         "\n" + "\n".join(sorted(wrong.values())))
        self.assertGreater(checked, 20, "hardly any label was checked")

    def test_no_frame_is_a_band_of_a_colour_its_parent_is_not(self):
        """The same rule as the labels, one level up, and the gap that it left.

        The Update group was a plain ttk.Frame, and such a frame has the colour of
        a card. On the old status page that was correct, because that page was a
        card. The group moved to App Settings, it kept the colour, and it became a
        band of the wrong colour at the width of the content. The label test above
        passed for that complete time, because each label in the band matched the
        band.

        A card is the one frame with a colour that is different from its parent.
        That contrast makes a card. Each other frame must match its parent.
        """
        style = ttk.Style(self.root)
        surfaces = {"Card.TFrame"}

        def ground(widget):
            try:
                named = str(widget.cget("style"))
            except tk.TclError:                              # pragma: no cover
                return ""
            return str(style.lookup(named or widget.winfo_class(),
                                    "background")).lower()

        wrong = {}
        for section, _t, _s, _i in self.panel_module.SECTIONS + (
                self.panel_module.ABOUT,):
            self.panel._open_section(section)
            self.root.update()
            for widget in self._every_widget():
                if not isinstance(widget, ttk.Frame):
                    continue
                if str(widget.cget("style")) in surfaces:
                    continue                    # a card, and meant to differ
                mine = ground(widget)
                parent = widget.nametowidget(widget.winfo_parent())
                if isinstance(parent, (tk.Canvas, tk.Toplevel, tk.Tk)):
                    continue                    # not a themed ground to match
                theirs = ground(parent)
                if not mine or not theirs or mine == theirs:
                    continue
                wrong[str(widget)] = ("%s: %s on %s, stands on %s"
                                      % (section, widget.cget("style")
                                         or widget.winfo_class(),
                                         mine, theirs))
        self.assertEqual(sorted(wrong.values()), [],
                         "\n" + "\n".join(sorted(wrong.values())))

    def test_no_card_stands_on_another_card(self):
        """Reported: dark notches inside the corners of the placeholder cards.

        A card carries its own corners, and they are painted against the
        *page*, because that is what a card normally stands on. Put one inside
        another and those four corners are four notches of the page's colour
        on top of the card underneath.

        Structural rather than by pixel, so it catches the next one as well:
        the fix is a flat OnCard.TFrame for anything that stands on a card,
        and the mistake is easy to repeat because the two look identical in
        the source and differ only where they overlap.
        """
        def cards(widget):
            found = []
            for parent in self._every_widget(widget):
                try:
                    if str(parent.cget("style")) == "Card.TFrame":
                        found.append(parent)
                except tk.TclError:                         # no -style option
                    pass
            return found

        for card in cards(self.root):
            inside = [str(other) for other in cards(card)
                      if other is not card]
            self.assertEqual(inside, [],
                             "%s is a card standing on the card %s"
                             % (inside, card))

    def test_the_window_is_dark_whatever_the_desktop_is(self):
        """The window is always dark, and no desktop setting changes that.

        This build machine reports Breeze *light*, because kdetheme.read() uses
        that scheme with no Plasma. A window that follows the desktop is therefore
        light here. This test proves that the window is dark.

        The preview is the reason. A page of lit LEDs against a white window shows
        weak LEDs.
        """
        self.assertFalse(kdetheme.is_dark(kdetheme.read()),
                         "this machine reports a dark desktop, so this test "
                         "cannot tell the two apart")
        self.assertLess(kdetheme.luminance(self.panel.roles["surface"]), 0.2,
                        "the window came up light")
        self.assertGreater(kdetheme.luminance(self.panel.roles["on_surface"]),
                           0.5, "dark text on a dark window")

    def test_the_accent_still_comes_from_the_desktop(self):
        # The half of following the desktop that is kept. Dark is ours; which
        # colour the pills and the headings are is Plasma's.
        self.assertEqual(self.panel_module.material.scheme(
            "#3daee9", dark=True)["primary"], self.panel.roles["primary"])

    def test_a_setting_that_hangs_off_a_switch_is_greyed_with_it(self):
        variable, _kind = self.panel.vars["NOTIFY"]
        _labels, controls = self.panel._rows["NOTIFY_DURATION"]
        variable.set(False)
        self.root.update_idletasks()
        for control in controls:
            self.assertIn("disabled", control.state())
        variable.set(True)
        self.root.update_idletasks()
        for control in controls:
            self.assertNotIn("disabled", control.state())


@unittest.skipUnless(_has_display(), "no tkinter or no display")
class SystemPageTest(unittest.TestCase):
    """The machine's own settings, in the window that edits two files.

    Built against a home directory of its own. The panel reads and writes the
    real one through $HOME, so a test that did not move it would be a test
    that rewrote the keyboard layout of whoever ran the suite.

    Everything here is about the seam rather than about the keyboard: the
    window has one Apply for two files now, and what must not happen is a
    setting reaching the wrong one of them.
    """

    @classmethod
    def setUpClass(cls):
        cls.panel_module = _panel_module()

    def setUp(self):
        import tempfile
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.home = holder.name
        was = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self.addCleanup(lambda: os.environ.__setitem__("HOME", was)
                        if was is not None else os.environ.pop("HOME", None))
        self.path = syssettings.path_for(syssettings.LAYOUT, self.home)
        self._build()

    def _build(self):
        """A fresh window, reading whatever the fake home says right now."""
        if getattr(self, "root", None) is not None:
            self.root.destroy()
        self.root = tk.Tk()
        self.panel = self.panel_module.Panel(self.root)
        # No test must run pkexec. This code records the call instead, and
        # that record is also the assertion of the test.
        self.ran = []
        self.panel.runner.start = lambda command, done=None: (
            self.ran.append(command), True)[1]
        self.root.update()
        self.addCleanup(self._destroy)

    def _destroy(self):
        if getattr(self, "root", None) is not None:
            self.root.destroy()
            self.root = None

    def _layout(self):
        return self.panel.vars[syssettings.LAYOUT][0]

    def _on_disk(self):
        return syssettings.read(self.home)[syssettings.LAYOUT]

    def _label(self, value):
        return dict((choice, label) for label, choice
                    in syssettings.layouts())[value]

    # -- reading -----------------------------------------------------------

    def test_a_layout_already_set_is_what_the_window_opens_on(self):
        syssettings.write({syssettings.LAYOUT: "fr"}, self.home)
        self._build()
        self.assertEqual(self._layout().get(), self._label("fr"))

    def test_and_it_is_not_an_unsaved_change(self):
        # The window agreeing with the file is the whole of what the unsaved
        # count means. A page that read its own file wrong would open showing
        # a change nobody made.
        syssettings.write({syssettings.LAYOUT: "fr"}, self.home)
        self._build()
        self.assertEqual(self.panel._differences(), [])

    def test_choosing_one_is_an_unsaved_change(self):
        self._layout().set(self._label("de"))
        self.root.update()
        self.assertEqual(self.panel._differences(), [syssettings.LAYOUT])

    # -- writing -----------------------------------------------------------

    def test_applying_writes_it(self):
        self._layout().set(self._label("de"))
        self.root.update()
        self.panel.apply_settings()
        self.root.update()
        self.assertEqual(self._on_disk(), "de")
        self.assertEqual(self.panel._differences(), [],
                         "the window still thinks it is unsaved")

    def test_applying_a_layout_asks_for_no_password_and_restarts_nothing(self):
        """The reason the two files are kept apart at all.

        The panel writes a keyboard layout as the user, into the home directory
        of that user. A call to the helper that writes /etc gives a password
        question for a file that needs no password. It also restarts the LED
        service for a setting that the service does not read.
        """
        self._layout().set(self._label("de"))
        self.root.update()
        self.panel.apply_settings()
        self.root.update()
        self.assertEqual(self.ran, [], "it went through pkexec anyway")

    def test_a_service_setting_still_goes_through_the_helper(self):
        # The second half: the split must keep the privileged path for the
        # settings that need it.
        self.panel.vars["LED_COUNT"][0].set(42)
        self.root.update()
        self.panel.apply_settings()
        self.root.update()
        self.assertTrue(self.ran, "the service's config was never installed")
        self.assertIn("pkexec", self.ran[0][0])

    def test_changing_both_writes_one_and_installs_the_other(self):
        self._layout().set(self._label("de"))
        self.panel.vars["LED_COUNT"][0].set(42)
        self.root.update()
        self.panel.apply_settings()
        self.root.update()
        self.assertEqual(self._on_disk(), "de")
        self.assertTrue(self.ran, "the service's config was never installed")

    def test_going_back_to_the_system_default_takes_the_file_away(self):
        syssettings.write({syssettings.LAYOUT: "de"}, self.home)
        self._build()
        self._layout().set(syssettings.UNSET_LABEL)
        self.root.update()
        self.panel.apply_settings()
        self.root.update()
        self.assertFalse(os.path.exists(self.path), "the file is still there")

    # -- the two files staying apart ---------------------------------------

    def test_the_service_config_is_never_asked_to_hold_a_layout(self):
        """The failure the split exists to prevent, at its own seam.

        _collect builds what gets written into /etc and validated by the LED
        service. A system setting in there is a key the service does not know:
        it would be refused by validate, and if it were not, it would be
        written into the service's config file and left there.
        """
        self._layout().set(self._label("de"))
        self.root.update()
        values = self.panel._collect()
        self.assertNotIn(syssettings.LAYOUT, values)
        merged = dict(self.panel_module.config_module.DEFAULTS)
        merged.update(values)
        self.panel_module.config_module.validate(merged)

    def test_a_profile_does_not_carry_the_machine_s_settings(self):
        # A profile is a set of LED settings you swap between. Carrying a
        # keyboard layout in one would change the machine every time somebody
        # tried a different look for the bar.
        self._layout().set(self._label("de"))
        self.root.update()
        self.assertNotIn(syssettings.LAYOUT,
                         self.panel_module.ledpanel.profile_text(
                             self.panel._collect()))

    def test_reload_picks_up_a_file_that_changed_underneath(self):
        # Both files, because the button says "Reload from file" and there
        # are two. Reloading only the service's would leave this page showing
        # an edit that Reload appeared to have thrown away.
        self._layout().set(self._label("de"))
        self.root.update()
        syssettings.write({syssettings.LAYOUT: "fr"}, self.home)
        self.panel.reload_settings()
        self.root.update()
        self.assertEqual(self._layout().get(), self._label("fr"))
        self.assertEqual(self.panel._differences(), [])

    # -- the menu ----------------------------------------------------------

    def test_a_layout_the_menu_does_not_list_still_shows_up(self):
        """Editing the file by hand is a supported way to use this.

        The menu cannot hold ninety-nine entries, because it does not scroll. So
        the file is the method for a layout that the menu does not list. The
        window must then keep that value, and it must not write the first entry
        of the menu over it.
        """
        syssettings.write({syssettings.LAYOUT: "kz"}, self.home)
        self._build()
        self.assertEqual(self.panel._value_for(
            syssettings.LAYOUT, self._layout().get()), "kz")
        self.assertEqual(self.panel._differences(), [])

    def test_the_whole_menu_fits_on_the_screen(self):
        """Measured on the window, which is the only place it shows.

        The drop-down takes the size of its entries, and the screen then limits
        it. Tk draws no entry below the bottom edge, and a click cannot reach one.
        At twenty-eight entries the menu was 126 pixels longer than a 1280x800
        display, which is the display of a Steam Machine. The suite passed at that
        time.
        """
        self.panel._open_section("keyboard")
        self.root.update()
        self.panel._open_menu(syssettings.LAYOUT)
        self.root.update()
        popup = self.panel._popup.window
        self.assertLessEqual(popup.winfo_reqheight(),
                             popup.winfo_screenheight(),
                             "the last entries are off the bottom edge")


@unittest.skipUnless(_has_display(), "no tkinter or no display")
class CecPageTest(unittest.TestCase):
    """The switches, and what they believe.

    Nothing here has a CEC adapter or the toolkit installed, and none of that
    is needed: the page reads a status document and runs commands, so the
    document is handed to it and the commands are recorded instead of run.

    These tests examine the one property that a page of live switches gets
    wrong: does it show the machine, or does it show the last click.
    """

    @classmethod
    def setUpClass(cls):
        cls.panel_module = _panel_module()

    def setUp(self):
        self.said = self._status()
        # This machine has no toolkit, and the window asks before it shows the
        # indicator at the foot. So this code must give the answer before the
        # build step of the window.
        was = cec.installed
        cec.installed = lambda home=None: True
        self.addCleanup(lambda: setattr(cec, "installed", was))
        self.root = tk.Tk()
        self.addCleanup(self._destroy)
        self.panel = self.panel_module.Panel(self.root)
        self.ran = []
        # Recorded rather than run, and the recording is the assertion: what
        # this page does is entirely "which command, with which arguments".
        self.panel.runner.start = lambda command, done=None: (
            self.ran.append((command, done)), True)[1]
        self.panel_module.ledpanel.cec_status = (
            lambda home=None, run=None: self.said)
        self.root.update()
        self.panel._open_section("cec")
        self.root.update()

    def _destroy(self):
        if getattr(self, "root", None) is not None:
            self.root.destroy()
            self.root = None

    def _status(self, **changes):
        found = {
            "ok": True, "version": "v0.1.26",
            "config": {"CEC_DEVICE": "/dev/cec0",
                       "CEC_AUDIO_LOGICAL_ADDRESS": "5",
                       "HDMI_ALSA_CARD_NAME": "alsa_card.pci-0000_03_00.1"},
            "cec_device": {"device": "/dev/cec0", "exists": True,
                           "readable": True, "writable": True},
            "external_volume": {"enabled": False},
            "services": {name: {"is_enabled": False}
                         for name, kind, _l, _s in cec.FEATURES
                         if kind == cec.USER_SERVICE},
            "system_services": {name: {"is_enabled": False}
                                for name, kind, _l, _s in cec.FEATURES
                                if kind == cec.SYSTEM_SERVICE},
        }
        found.update(changes)
        return found

    def _not_installed(self):
        """Pretend the toolkit is not there, for as long as the block lasts."""
        import contextlib

        @contextlib.contextmanager
        def pretend():
            was_status = self.panel_module.ledpanel.cec_status
            was_installed = cec.installed
            self.panel_module.ledpanel.cec_status = (
                lambda home=None, run=None: None)
            cec.installed = lambda home=None: False
            try:
                yield
            finally:
                self.panel_module.ledpanel.cec_status = was_status
                cec.installed = was_installed

        return pretend()

    def _finish(self, code=0):
        """Let the last recorded command's callback run, as the Runner would."""
        _command, done = self.ran[-1]
        if done is not None:
            done(code)
        self.root.update()

    # -- which half of the page ------------------------------------------

    def test_with_no_toolkit_it_offers_to_install_rather_than_showing_switches(self):
        self.panel_module.ledpanel.cec_status = lambda home=None, run=None: None
        self.panel._reread_cec()
        self.root.update()
        self.assertTrue(self.panel.cec_missing.winfo_ismapped())
        self.assertFalse(self.panel.cec_present.winfo_ismapped())

    def test_installing_while_the_window_is_open_swaps_the_halves(self):
        """Installing is a thing that happens with the page in front of you.

        This code builds both pages first and packs one of them, so the page can
        change itself. A page with the state of its build step needs a rebuild of
        the complete window.
        """
        self.panel_module.ledpanel.cec_status = lambda home=None, run=None: None
        self.panel._reread_cec()
        self.root.update()
        self.panel_module.ledpanel.cec_status = (
            lambda home=None, run=None: self.said)
        self.panel._reread_cec()
        self.root.update()
        self.assertTrue(self.panel.cec_present.winfo_ismapped())
        self.assertFalse(self.panel.cec_missing.winfo_ismapped())

    def test_reading_the_status_takes_the_headline_with_it(self):
        """The line under the title is drawn from this answer, and drawn first.

        The first read runs a moment after the window opens, so the headline
        comes from a status of None. cec_part reports that as a toolkit that
        gives no answer, and each page counts it as a problem. Without a
        correction, the foot of the window reports HDMI CEC as ready below a
        title that reports a fault. The "look again" button of the page also does
        not clear it.
        """
        # The state the window opens in: the toolkit is there, nobody has
        # asked it anything yet.
        self.panel._cec = None
        self.panel.refresh_status()
        self.assertIn("HDMI CEC", self.panel.headline.cget("text"),
                      "not the state this is about")
        self.panel._reread_cec()
        self.root.update()
        self.assertNotIn("HDMI CEC", self.panel.headline.cget("text"),
                         "the answer arrived and the headline kept the "
                         "unasked-yet reading")

    # -- the switches ------------------------------------------------------

    def test_every_feature_gets_a_switch(self):
        self.assertEqual(sorted(self.panel._cec_vars),
                         sorted(name for name, _k, _l, _s in cec.FEATURES))

    def test_the_switches_open_where_the_machine_says(self):
        self.said["services"]["steam-button"]["is_enabled"] = True
        self.said["external_volume"]["enabled"] = True
        self.panel._reread_cec()
        self.root.update()
        on = {n for n, v in self.panel._cec_vars.items() if v.get()}
        self.assertEqual(on, {"steam-button", "external-volume"})

    def test_a_click_on_its_own_reaches_nothing(self):
        """What the Apply button is for.

        Each switch here starts or stops a unit, and one half of them control the
        suspend behaviour of the machine. A click is a decision and not an
        applied change. A user also corrects an accidental click with a second
        click, and does not wait for a service to stop and start.
        """
        self.panel._cec_vars["tv-standby"].set(True)
        self.panel._cec_toggled("tv-standby")
        self.assertEqual(self.ran, [])

    def test_applying_runs_the_switch_that_moved(self):
        self.panel._cec_vars["tv-standby"].set(True)
        self.panel._cec_toggled("tv-standby")
        self.panel._apply_cec_features()
        command, _done = self.ran[-1]
        self.assertEqual(command[1:], ["set-service", "tv-standby", "on"])

    def test_a_system_service_goes_the_other_way_round(self):
        self.panel._cec_vars["usb-wake"].set(True)
        self.panel._cec_toggled("usb-wake")
        self.panel._apply_cec_features()
        self.assertEqual(self.ran[-1][0][1:],
                         ["set-system-service", "usb-wake", "on"])

    def test_a_switch_put_back_before_applying_is_no_change_at_all(self):
        # Compared with the machine rather than counted from the clicks, so
        # turning one on and off again applies nothing.
        self.panel._cec_vars["tv-standby"].set(True)
        self.panel._cec_toggled("tv-standby")
        self.panel._cec_vars["tv-standby"].set(False)
        self.panel._cec_toggled("tv-standby")
        self.panel._apply_cec_features()
        self.assertEqual(self.ran, [])

    def test_apply_is_dead_until_a_switch_has_moved(self):
        self.assertIn("disabled", self.panel.cec_apply.state())
        self.panel._cec_vars["tv-standby"].set(True)
        self.panel._cec_toggled("tv-standby")
        self.assertNotIn("disabled", self.panel.cec_apply.state())

    def test_two_switches_go_one_after_the_other(self):
        """The runner holds one command, so they are chained.

        Started together the second is refused, which would leave half a page
        applied and nothing to say which half.
        """
        self.panel._cec_vars["tv-standby"].set(True)
        self.panel._cec_toggled("tv-standby")
        self.panel._cec_vars["usb-wake"].set(True)
        self.panel._cec_toggled("usb-wake")
        self.panel._apply_cec_features()
        self.assertEqual(len(self.ran), 1, "both went at once")
        first = self.ran[-1][0][2]
        self._finish(code=0)
        started = [one[0][2] for one in self.ran]
        self.assertIn("tv-standby", started)
        self.assertIn("usb-wake", started)
        self.assertNotEqual(started[0], started[1], first)

    def test_settling_the_switches_does_not_set_them_all_going(self):
        """Writing a variable fires the same handler a click does.

        Without the guard, a status read after one change moves each other
        switch on the page. Each of those changes then reads the status again.
        This test examines that loop and not the guard itself.
        """
        self.panel._reread_cec()
        self.root.update()
        self.assertEqual(self.ran, [], "settling the page ran commands")

    def test_a_switch_that_did_not_take_goes_back_where_it_was(self):
        """The machine's answer wins over the click.

        The machine can refuse a change. An absent helper does that, and a unit
        that does not start does that. The toolkit reports the state in both
        cases. A switch at the position of the click reports the opinion of this
        window.
        """
        self.panel._cec_vars["tv-standby"].set(True)
        self.panel._cec_toggled("tv-standby")
        self.panel._apply_cec_features()
        # The command ran and the machine still says off, which is what a
        # refused toggle looks like from here.
        self._finish(code=1)
        self.assertFalse(self.panel._cec_vars["tv-standby"].get())

    def test_applying_while_something_else_runs_puts_the_switches_back(self):
        # The Runner takes one command at a time. Nothing was applied, so the
        # switches go back to what the machine says rather than standing as
        # this window's opinion.
        self.panel.runner.start = lambda command, done=None: False
        self.panel._cec_vars["boot-wake"].set(True)
        self.panel._cec_toggled("boot-wake")
        self.panel._apply_cec_features()
        self.assertFalse(self.panel._cec_vars["boot-wake"].get())

    # -- the adapter -------------------------------------------------------

    def test_a_working_adapter_is_said_in_the_colour_of_good_news(self):
        self.assertIn("/dev/cec0", self.panel.cec_adapter.cget("text"))
        self.assertEqual(str(self.panel.cec_adapter.cget("style")),
                         "Good.TLabel")

    def test_an_adapter_that_is_there_but_shut_says_which_of_the_two_it_is(self):
        """Not the same problem as having no adapter, and not the same fix.

        The toolkit has a helper and a udev rule for this condition, and a
        suspend or a SteamOS update causes it. So "reinstall" is the answer
        here, and "connect an adapter" is the answer to the other condition.
        """
        self.said["cec_device"]["writable"] = False
        self.panel._reread_cec()
        self.root.update()
        said = self.panel.cec_adapter.cget("text")
        self.assertIn("cannot write", said)
        self.assertIn("Reinstalling", said)
        self.assertEqual(str(self.panel.cec_adapter.cget("style")),
                         "Bad.TLabel")

    def test_no_adapter_says_that_instead(self):
        self.said["cec_device"] = {"device": "/dev/cec0", "exists": False,
                                   "readable": False, "writable": False}
        self.panel._reread_cec()
        self.root.update()
        self.assertIn("not there", self.panel.cec_adapter.cget("text"))

    # -- the rest ----------------------------------------------------------

    def test_the_config_boxes_open_on_what_the_toolkit_has(self):
        self.assertEqual(self.panel._cec_entries["CEC_DEVICE"].get(),
                         "/dev/cec0")

    def test_saving_the_boxes_sends_every_one_of_them(self):
        import json
        box = self.panel._cec_entries["CEC_DEVICE"]
        box.delete(0, "end")
        box.insert(0, "/dev/cec1")
        self.panel._save_cec_config()
        command, _done = self.ran[-1]
        self.assertEqual(command[1], "set-config")
        sent = json.loads(command[2])
        self.assertEqual(sent["CEC_DEVICE"], "/dev/cec1")
        self.assertEqual(sorted(sent), sorted(k for k, _l, _s, _c in cec.SHOWN))

    def test_the_sleep_action_is_a_choice_and_not_a_typed_word(self):
        """Two answers, one of them spelled inactive-source.

        A user types this into a box. Without the hyphen the setting has no
        result, and the page still reports a success. There is also no third
        value that a user wants.
        """
        self.assertIn("CEC_SLEEP_TV_ACTION", self.panel._cec_menus)
        self.assertEqual(
            [value for _label, value
             in self.panel._menus["CEC_SLEEP_TV_ACTION"]],
            ["standby", "inactive-source"])

    def test_a_setting_the_file_does_not_carry_opens_on_the_default(self):
        # Not on a blank: the toolkit does something when the value is not in
        # the file, and that something is what the field has to say.
        self.assertEqual(
            self.panel._cec_menus["CEC_SLEEP_TV_ACTION"].get(), "Standby")

    def test_saving_a_choice_sends_the_value_and_not_the_label(self):
        """The file wants inactive-source; the window shows Inactive source.

        Sending what is on screen writes a word the toolkit does not know,
        and every part of this page would report it saved.
        """
        import json
        self.panel._cec_menus["CEC_SLEEP_TV_ACTION"].set("Inactive source")
        self.panel._save_cec_config()
        self.assertEqual(
            json.loads(self.ran[-1][0][2])["CEC_SLEEP_TV_ACTION"],
            "inactive-source")

    def test_a_value_the_list_does_not_offer_is_kept_rather_than_replaced(self):
        # Editing the file by hand is how the other forty settings are meant
        # to be used, and a value this window has never heard of is not one to
        # quietly swap for the default.
        self.said["config"]["CEC_SLEEP_TV_ACTION"] = "whatever-they-wrote"
        self.panel._reread_cec()
        self.root.update()
        self.assertEqual(self.panel._cec_menus["CEC_SLEEP_TV_ACTION"].get(),
                         "whatever-they-wrote")

    def test_the_delay_is_a_box_of_its_own(self):
        # The other half of "sleep when the television switches away", which
        # was a number in a file and nowhere on the page.
        self.assertIn("INPUT_INACTIVE_SUSPEND_DELAY_SECONDS",
                      self.panel._cec_entries)

    def test_a_box_with_spaces_round_it_is_trimmed(self):
        # A trailing space in CEC_DEVICE is a device node that does not exist,
        # and nothing on the page would say why.
        import json
        box = self.panel._cec_entries["CEC_DEVICE"]
        box.delete(0, "end")
        box.insert(0, "  /dev/cec1  ")
        self.panel._save_cec_config()
        self.assertEqual(json.loads(self.ran[-1][0][2])["CEC_DEVICE"],
                         "/dev/cec1")

    def test_an_action_runs_and_changes_nothing_that_needs_saving(self):
        self.panel._cec_action("wake")
        self.assertEqual(self.ran[-1][0][1:], ["wake"])

    def test_the_page_reads_the_machine_again_after_anything_it_ran(self):
        """Every command here can change what the page shows.

        A change of a switch clearly changes the page. An action also changes
        it: discover-cec writes the adapter into the configuration, and the
        boxes above then hold the old value.
        """
        for start in (lambda: self.panel._cec_action("discover-cec"),
                      lambda: self.panel._save_cec_config(),
                      lambda: (self.panel._cec_vars["boot-wake"].set(True),
                               self.panel._apply_cec_features())):
            self.ran = []
            start()
            self.assertIsNotNone(self.ran[-1][1],
                                 "%s left nothing to read the status back"
                                 % self.ran[-1][0])

    def test_installing_asks_first_and_then_runs_our_bridge(self):
        asked = []
        self.panel._ask = lambda *a, **k: (asked.append(a), True)[1]
        self.panel._install_cec()
        self.assertTrue(asked, "it installed without asking")
        command, _done = self.ran[-1]
        self.assertEqual(command[0], "pkexec")
        self.assertTrue(command[1].endswith("install-cec.sh"))
        self.assertEqual(command[2], "install")

    def test_saying_no_to_the_question_installs_nothing(self):
        self.panel._ask = lambda *a, **k: False
        self.panel._install_cec()
        self.assertEqual(self.ran, [])

    def test_removing_asks_too(self):
        self.panel._ask = lambda *a, **k: False
        self.panel._remove_cec()
        self.assertEqual(self.ran, [])
        self.panel._ask = lambda *a, **k: True
        self.panel._remove_cec()
        self.assertEqual(self.ran[-1][0][2], "remove")

    # -- the indicator at the foot -----------------------------------------

    def _cec_light(self):
        return self.panel.cec_link.canvas.itemcget(self.panel.cec_link.dot,
                                                   "fill")

    def test_the_foot_of_the_window_says_whether_cec_can_reach_the_tv(self):
        """The adapter, not the switches.

        Every feature can be on and none of them work if nothing can be sent,
        and that is the question worth answering from a page about something
        else. Which features are on is what you open the section for.
        """
        self.panel._reread_cec()
        self.root.update()
        self.assertTrue(self.panel.cec_link.canvas.winfo_ismapped())
        self.assertEqual(self._cec_light(), self.panel.roles["positive"])
        self.assertIn("ready", self.panel.cec_link.label.cget("text"))

    def test_an_adapter_that_cannot_be_reached_shows_red_down_there_too(self):
        self.said["cec_device"]["writable"] = False
        self.panel._reread_cec()
        self.root.update()
        self.assertEqual(self._cec_light(), self.panel.roles["error"])

    def test_a_machine_with_no_cec_toolkit_gets_no_second_indicator(self):
        """Nothing to indicate, so nothing in the bar.

        A permanent grey "HDMI CEC not installed" makes the foot of the window
        report an absence that no user asked about. It also reports it on each
        page, for each session, on each machine that needs the LEDs only.
        """
        with self._not_installed():
            self.panel._reread_cec()
            self.root.update()
            self.assertFalse(self.panel.cec_link.canvas.winfo_ismapped())
            self.assertFalse(self.panel.cec_link.label.winfo_ismapped())

    def test_installing_brings_it_into_the_bar_without_a_restart(self):
        with self._not_installed():
            self.panel._reread_cec()
            self.root.update()
        self.panel._reread_cec()
        self.root.update()
        self.assertTrue(self.panel.cec_link.canvas.winfo_ismapped())

    def test_a_toolkit_that_will_not_answer_is_a_fault_not_a_silence(self):
        # Installed and not answering is a red light; not looked at yet is a
        # grey one. Reported the same way, the first would look like the
        # window still thinking about it.
        def broken(home=None, run=None):
            raise cec.CecError("no JSON came back")

        self.panel_module.ledpanel.cec_status = broken
        self.panel._reread_cec()
        self.root.update()
        self.assertEqual(self._cec_light(), self.panel.roles["error"])
        self.assertIn("not answering",
                      self.panel.cec_link.label.cget("text"))

    def test_it_comes_after_the_led_light_however_late_it_arrives(self):
        """Packed with an anchor rather than at the end of the row.

        A window with a failure message holds that line at the left. An
        indicator that comes later therefore goes to the other side of it. The
        sentence about one light then stands between the two lights.
        """
        with self._not_installed():
            self.panel._reread_cec()
            self.panel._write("something broke\n")
            self.panel._set_busy(False, 1)
            self.root.update()
        self.panel._reread_cec()
        self.root.update()
        self.assertLess(self.panel.link.label.winfo_x(),
                        self.panel.cec_link.canvas.winfo_x())
        self.assertLess(self.panel.cec_link.label.winfo_x(),
                        self.panel.problem.winfo_x())

    def test_the_two_lights_are_not_pushed_against_each_other(self):
        # Measured: with no room in front of the second dot the two read as
        # one sentence with a bullet in the middle of it.
        self.panel._reread_cec()
        self.root.update()
        gap = (self.panel.cec_link.canvas.winfo_x()
               - (self.panel.link.label.winfo_x()
                  + self.panel.link.label.winfo_width()))
        self.assertGreaterEqual(gap, self.panel_module.ROW_GAP, gap)

    def test_it_is_read_once_after_the_window_is_up_not_while_it_is_built(self):
        """The bar shows this from every page, so it cannot wait to be opened.

        But steamos-cec-toolkitctl runs a handful of systemctl calls, and on
        the construction path that is a delay on every startup of every
        machine that has CEC installed. Booked instead, for a moment after.
        """
        self._destroy()
        asked = []
        self.panel_module.ledpanel.cec_status = (
            lambda home=None, run=None: (asked.append(1), self.said)[1])
        self.root = tk.Tk()
        panel = self.panel_module.Panel(self.root)
        # Before an update call, so this test asks whether the build step made
        # the call. The build of this window takes longer than the delay, so
        # the timer is due at the first update. A check after that update reads
        # the clock and not the code.
        self.assertEqual(asked, [], "the machine was asked while the window "
                                    "was being built")
        self.assertIsNotNone(panel._cec_first, "and nothing was booked to ask")
        self.root.after(self.panel_module.CEC_FIRST_LOOK + 60,
                        self.root.quit)
        self.root.mainloop()
        self.assertEqual(len(asked), 1)

    def test_a_toolkit_that_answers_rubbish_is_not_installed_as_far_as_this_goes(self):
        # read_status raises rather than returning an empty document, and the
        # page must not turn that into eight switches all showing off.
        def broken(home=None, run=None):
            raise cec.CecError("no JSON came back")

        self.panel_module.ledpanel.cec_status = broken
        self.panel._reread_cec()
        self.root.update()
        self.assertTrue(self.panel.cec_missing.winfo_ismapped())


class GpuBlockTest(unittest.TestCase):
    """The graphics card block, against a fake lactd on a real socket.

    These tests use no mock of the daemon. The fixture is a unix socket with
    the same protocol. So they examine the complete path of the page: it reads
    five documents, it draws from them, it collects the values on the screen,
    and it sends them back.
    """

    @classmethod
    def setUpClass(cls):
        cls.panel_module = _panel_module()

    def setUp(self):
        self.daemon = FakeDaemon(answers=dict(self._answers()))
        self.addCleanup(self.daemon.close)
        self._point_at(self.daemon.path)
        self.root = tk.Tk()
        self.addCleanup(self._destroy)
        self.panel = self.panel_module.Panel(self.root)
        # This records the message and does not show it. A modal dialog in a
        # test has no user to close it, so the suite waits and does not fail. A
        # stack dump found the fixed socket path below in that way, and no
        # failure message reported it.
        self.said = []
        self.panel._say = lambda title, message: self.said.append(message)
        self.root.update()
        self.panel._open_section("power")
        self.root.update()

    def _answers(self, clocks=None):
        return {
            "list_devices": DEVICES,
            "device_stats": STATS,
            "device_clocks_info": CLOCKS if clocks is None else clocks,
            "get_gpu_config": CONFIG,
            "list_profiles": {"profiles": ["quiet", "loud"],
                              "current_profile": "quiet"},
            "set_gpu_config": 5,
            "confirm_pending_config": None,
            "set_profile": None,
        }

    def _point_at(self, path):
        """Aim both copies of the module at the fake daemon's socket."""
        for module in (lact, self.panel_module.lact,
                       self.panel_module.ledpanel.lact_module):
            was = module.SOCKET_PATH
            module.SOCKET_PATH = path
            self.addCleanup(
                lambda m=module, w=was: setattr(m, "SOCKET_PATH", w))

    def _destroy(self):
        if getattr(self, "root", None) is not None:
            self.root.destroy()
            self.root = None

    def _cards(self):
        return self.panel.gpu_box.winfo_children()

    # -- whether the block is there at all ---------------------------------

    def test_a_machine_without_lact_gets_no_block(self):
        """Not an empty one, and not a message. Nothing.

        Most machines never run LACT. A permanent "LACT is not installed" below
        the CPU settings makes the page report an absence that no user asked
        about. The second status light follows the same rule.
        """
        self._point_at("/nonexistent/lactd.sock")
        self.panel._reread_gpu()
        self.root.update()
        self.assertEqual(self._cards(), [])

    def test_a_daemon_that_will_not_answer_says_so_rather_than_hiding(self):
        """Installed and unhappy is not the same as absent.

        Hiding the block would leave somebody who *has* LACT wondering why the
        panel does not see it, which is the case a message is for.
        """
        broken = FakeDaemon(answers={})
        self.addCleanup(broken.close)
        self._point_at(broken.path)
        self.panel._reread_gpu()
        self.root.update()
        self.assertTrue(self._cards())
        self.assertTrue(self.panel._gpu_error)

    def test_a_card_that_is_there_is_named(self):
        self.assertTrue(self._cards())
        self.assertEqual(self.panel._gpu["name"], DEVICES[0]["name"])

    def test_it_does_not_ask_until_the_section_is_opened(self):
        # Five socket calls on the build path give five calls at each start of
        # the window and at each change of the theme. Most users never open
        # this page. The CEC read is later for the same reason.
        self._destroy()
        self.root = tk.Tk()
        panel = self.panel_module.Panel(self.root)
        self.assertFalse(panel._gpu_asked)
        panel._open_section("power")
        self.assertTrue(panel._gpu_asked)

    # -- the knobs ---------------------------------------------------------

    def test_every_knob_the_card_reported_gets_a_slider(self):
        # The values of this card, and not the complete table. Two of the
        # controls are alternatives, because a card reports an absolute core
        # clock or an offset from one, and never both. KNOBS holds each
        # possible control, and this list holds the controls of this card.
        reported = self.panel_module.ledpanel.gpu_knobs(self.panel._gpu)
        self.assertTrue(reported, "the card in this fixture reported none")
        for knob in reported:
            self.assertIn(knob["key"], self.panel._gpu_vars, knob["key"])

    def test_a_card_with_no_clocks_table_gets_only_the_power_slider(self):
        """Most integrated graphics, and this machine can be one.

        Four sliders that write nothing are worse than one slider that works. A
        user sets a clock, the page accepts it, and nothing changes.
        """
        bare = FakeDaemon(answers=dict(self._answers(clocks={})))
        self.addCleanup(bare.close)
        self._point_at(bare.path)
        self.panel._reread_gpu()
        self.root.update()
        self.assertIn(lact.POWER_CAP, self.panel._gpu_vars)
        self.assertNotIn("max_core_clock", self.panel._gpu_vars)

    def test_an_unset_maximum_starts_at_the_card_s_own_maximum(self):
        """Not at the bottom of the range, which is what it first did.

        A slider at 200 MHz draws an untouched card as one clamped to its
        lowest clock. That is a lie about the machine, and the kind somebody
        acts on.
        """
        self.assertEqual(round(self.panel._gpu_vars["max_core_clock"].get()),
                         1600)
        self.assertEqual(round(self.panel._gpu_vars["voltage_offset"].get()), 0)

    def test_the_power_slider_starts_at_what_the_card_is_set_to(self):
        self.assertEqual(round(self.panel._gpu_vars[lact.POWER_CAP].get()), 15)

    # -- the fan -----------------------------------------------------------

    def test_the_curve_and_the_fixed_speed_are_never_both_shown(self):
        for mode, curve_shown in ((lact.FAN_CURVE, True),
                                  (lact.FAN_STATIC, False)):
            self.panel._gpu_vars["fan_enabled"].set(True)
            self.panel._gpu_vars["fan_mode"].set(
                dict((value, label) for label, value in
                     [("Curve", lact.FAN_CURVE),
                      ("Fixed speed", lact.FAN_STATIC)])[mode])
            self.panel._gpu_fan_changed()
            self.root.update()
            self.assertEqual(
                bool(self.panel.gpu_fan_curve.winfo_ismapped()), curve_shown,
                mode)
            self.assertEqual(
                bool(self.panel.gpu_fan_static.winfo_ismapped()),
                not curve_shown, mode)

    def test_the_mode_is_read_as_lact_spells_it_not_as_the_menu_shows_it(self):
        """A drop-down here holds the label on the screen.

        A comparison of that label with the word of LACT matches nothing. The
        page then drew the fixed-speed slider, and the menu showed Curve.
        """
        self.panel._gpu_vars["fan_mode"].set("Curve")
        self.assertEqual(self.panel._gpu_mode(), lact.FAN_CURVE)
        self.panel._gpu_vars["fan_mode"].set("Fixed speed")
        self.assertEqual(self.panel._gpu_mode(), lact.FAN_STATIC)

    def test_nothing_about_the_fan_shows_while_it_is_not_controlled(self):
        # Off means the card's firmware drives it, and every value below is
        # inert until it is on.
        self.panel._gpu_vars["fan_enabled"].set(False)
        self.panel._gpu_fan_changed()
        self.root.update()
        self.assertFalse(self.panel.gpu_fan_curve.winfo_ismapped())
        self.assertFalse(self.panel.gpu_fan_static.winfo_ismapped())

    def test_the_curve_opens_on_what_the_card_has(self):
        self.assertEqual(self.panel._gpu_curve.curve, {40: 0.3, 80: 1.0})

    def test_the_curve_canvas_gets_the_height_it_asks_for(self):
        """Measured, because it did not.

        The inner frame of a page is a canvas window item with the size from the
        scroller. A new widget in it does not make it larger. The graph asked for
        200 pixels, it got 120, and it lost its lower half. See _refit_page.
        """
        self.panel._gpu_vars["fan_enabled"].set(True)
        self.panel._gpu_fan_changed()
        for _ in range(4):
            self.root.update()
        canvas = self.panel._gpu_curve.canvas
        self.assertEqual(canvas.winfo_height(), canvas.winfo_reqheight())

    # -- the card's own fan settings ---------------------------------------

    def _with_firmware(self):
        """Redraw the block as if the card were an RDNA3 one."""
        answers = dict(self._answers())
        answers["device_stats"] = dict(STATS, **RDNA3_FAN)
        newer = FakeDaemon(answers=answers)
        self.addCleanup(newer.close)
        self._point_at(newer.path)
        self.panel._reread_gpu()
        self.root.update()
        return newer

    def test_an_older_card_gets_none_of_the_firmware_settings(self):
        """The 6000-series case, and the whole point of the detection.

        LACT reads each of these from sysfs and reports each one with a file. An
        older card therefore reports none of them, and the page draws no block. It
        does not draw a row of controls that write nothing.
        """
        firmware = [key for key in self.panel._gpu_vars
                    if key.startswith("fw:")]
        self.assertEqual(firmware, [])

    def test_a_newer_card_gets_all_six(self):
        self._with_firmware()
        firmware = sorted(key[3:] for key in self.panel._gpu_vars
                          if key.startswith("fw:"))
        self.assertEqual(firmware,
                         sorted(writes for _k, writes, _l, _u
                                in lact.FIRMWARE))

    def test_the_switch_and_the_sliders_open_on_what_the_card_reports(self):
        self._with_firmware()
        self.assertTrue(self.panel._gpu_vars["fw:zero_rpm"].get())
        self.assertEqual(
            round(self.panel._gpu_vars["fw:acoustic_limit"].get()), 3200)

    def test_they_are_written_under_the_names_the_daemon_accepts(self):
        """Four of the six are reported under one name and set under another.

        A page that returns the value that it read writes `zero_rpm_enable`. The
        daemon does not know that key. It also accepts the document, because an
        unknown key is not a bad key.
        """
        self._with_firmware()
        made = self.panel._collect_gpu()
        block = made[lact.FIRMWARE_CONFIG]
        self.assertIn("zero_rpm", block)
        self.assertNotIn("zero_rpm_enable", block)
        self.assertIn("target_temperature", block)
        self.assertNotIn("target_temp", block)

    def test_they_go_back_even_while_lact_is_not_driving_the_fan(self):
        """They are the firmware's settings, not LACT's control loop.

        Collected only when the switch above is on, they would be
        unreachable for exactly the people they are for: somebody leaving the
        card to look after its own fan.
        """
        self._with_firmware()
        self.panel._gpu_vars["fan_enabled"].set(False)
        made = self.panel._collect_gpu()
        self.assertFalse(made["fan_control_enabled"])
        self.assertIn("zero_rpm", made[lact.FIRMWARE_CONFIG])

    def test_a_card_reporting_only_some_of_them_gets_only_those(self):
        answers = dict(self._answers())
        answers["device_stats"] = dict(STATS, **{"fan": {"pmfw_info": {
            "zero_rpm_enable": False}}})
        partial = FakeDaemon(answers=answers)
        self.addCleanup(partial.close)
        self._point_at(partial.path)
        self.panel._reread_gpu()
        self.root.update()
        firmware = [key for key in self.panel._gpu_vars
                    if key.startswith("fw:")]
        self.assertEqual(firmware, ["fw:zero_rpm"])

    # -- sending it back ---------------------------------------------------

    def test_what_is_collected_is_the_whole_config_not_a_patch(self):
        """set_gpu_config replaces the document rather than patching it.

        So a setting this page does not show has to be carried across
        untouched, or applying a fan curve silently turns off whatever else
        was configured on that card.
        """
        self.panel._gpu_vars[lact.POWER_CAP].set(20)
        made = self.panel._collect_gpu()
        self.assertEqual(made["power_cap"], 20.0)
        self.assertEqual(made["fan_control_settings"]["temperature_key"],
                         CONFIG["fan_control_settings"]["temperature_key"])

    def test_the_curve_on_screen_is_what_gets_sent(self):
        self.panel._gpu_curve.curve = {45: 0.4, 90: 1.0}
        made = self.panel._collect_gpu()
        self.assertEqual(made["fan_control_settings"]["curve"],
                         {"45": 0.4, "90": 1.0})

    def test_applying_asks_whether_to_keep_it(self):
        """The daemon reverts unless confirmed, so the window has to ask.

        Without the question there are two results. The window sends a
        confirmation that no user gave, and that removes the safety function. Or
        the window never confirms, and each setting reverses itself after five
        seconds.
        """
        asked = []
        self.panel_module.CountdownDialog = (
            lambda parent, seconds: (asked.append(seconds),
                                     type("A", (), {"answer": True})())[1])
        self.panel._apply_gpu()
        self.assertEqual(asked, [5], "it did not ask, or ignored the daemon's "
                                     "own number of seconds")
        sent = [one for one in self.daemon.asked
                if one["command"] == "confirm_pending_config"]
        self.assertEqual(sent[-1]["args"], {"command": "confirm"})

    def test_saying_no_puts_the_settings_back_at_once(self):
        # And not a wait for the end of the clock. A user with an answer must
        # not watch a countdown to its end.
        self.panel_module.CountdownDialog = (
            lambda parent, seconds: type("A", (), {"answer": False})())
        self.panel._apply_gpu()
        sent = [one for one in self.daemon.asked
                if one["command"] == "confirm_pending_config"]
        self.assertEqual(sent[-1]["args"], {"command": "revert"})

    def test_a_card_that_refuses_the_settings_says_so_and_asks_nothing(self):
        refusing = FakeDaemon(answers=dict(self._answers()), refuse=True)
        self.addCleanup(refusing.close)
        self._point_at(refusing.path)
        asked = []
        self.panel_module.CountdownDialog = (
            lambda parent, seconds: (asked.append(seconds),
                                     type("A", (), {"answer": True})())[1])
        self.panel._apply_gpu()
        self.assertTrue(self.said, "it failed silently")
        self.assertEqual(asked, [], "it asked about settings that never landed")

    # -- profiles ----------------------------------------------------------

    def test_the_profiles_lact_has_are_offered_with_the_default(self):
        # The default one has no name in LACT, and a menu with a blank entry
        # is a menu with a bug in it.
        labels = [label for label, _value
                  in self.panel._menus["gpu-profile"]]
        self.assertIn("Default", labels)
        self.assertIn("quiet", labels)

    def test_choosing_one_switches_at_once_rather_than_waiting_for_apply(self):
        """A profile carries its own settings, so it replaces everything.

        Queuing it behind Apply would mean the sliders below showing one
        profile's values while another was chosen above them.
        """
        self.panel._gpu_vars["profile"].set("loud")
        self.root.update()
        self.assertEqual(self.said, [])
        sent = [one for one in self.daemon.asked
                if one["command"] == "set_profile"]
        self.assertEqual(sent[-1]["args"], {"name": "loud"})


class WrappingTest(unittest.TestCase):
    """No line of explanation laid out wider than the card it is in.

    A ttk label does not become shorter, and it does not wrap itself. Tk draws
    it at the value of wraplength, and its parent cuts it at the edge. The
    wraplength here is the width of the page less the indent of each line. That
    indent was a constant before. That constant came from the width of a
    switch, and a column of setting names is much wider. Each long explanation
    on the CEC page therefore went past the side of its card.
    """

    @classmethod
    def setUpClass(cls):
        cls.panel_module = _panel_module()

    def setUp(self):
        was = cec.installed
        cec.installed = lambda home=None: True
        self.addCleanup(lambda: setattr(cec, "installed", was))
        self.panel_module.ledpanel.cec_status = (
            lambda home=None, run=None: {
                "cec_device": {"device": "/dev/cec0", "exists": True,
                               "readable": True, "writable": True},
                "services": {}, "system_services": {},
                "external_volume": {"enabled": False},
                "config": {"CEC_DEVICE": "/dev/cec0"}})
        self.root = tk.Tk()
        self.addCleanup(self._destroy)
        self.panel = self.panel_module.Panel(self.root)
        self.root.update()

    def _destroy(self):
        if getattr(self, "root", None) is not None:
            self.root.destroy()
            self.root = None

    def _card_of(self, widget):
        up = widget.master
        while up is not None:
            try:
                if str(up.cget("style")) == "Card.TFrame":
                    return up
            except tk.TclError:
                pass
            up = getattr(up, "master", None)
        return None

    def _too_wide(self, key):
        self.panel._open_section(key)
        for _ in range(4):
            self.root.update_idletasks()
            self.root.update()
        out = []
        for label in self.panel._wrapped:
            if not label.winfo_ismapped():
                continue
            card = self._card_of(label)
            if card is None:
                continue
            room = (card.winfo_rootx() + card.winfo_width()
                    - label.winfo_rootx())
            if label.winfo_reqwidth() > room:
                out.append((label.cget("text")[:40],
                            label.winfo_reqwidth() - room))
        return out

    def test_nothing_on_the_cec_page_runs_off_its_card(self):
        # The page this was found on: the settings there stand behind a whole
        # column of names, and the constant said a switch.
        self.assertEqual(self._too_wide("cec"), [])

    def test_nor_on_the_pages_that_were_already_right(self):
        for key in ("strip", "power", "keyboard"):
            self.assertEqual(self._too_wide(key), [], key)

    def _notebook_of(self, label):
        """The notebook whose page this label is on, of the two there are."""
        up = label
        while up is not None:
            if up in (self.panel.notebook, self.panel.sections):
                return up
            up = getattr(up, "master", None)
        return None

    def _laid_out_to(self, label):
        """The page width this label's wraplength was worked out from."""
        return label.cget("wraplength") + self.panel._inset_of(label)

    def test_each_page_is_laid_out_to_its_own_width(self):
        """Two notebooks, one number, and whichever spoke last won.

        The sections are at the left, so the tabs of the strip have the width of
        the rail less than a section with one page. On the machine of this report
        that is 180 px. Both notebooks wrote the same wraplength, so each page took
        the width of the last notebook with a size change. The same window did it
        in both directions.

        With the narrow value, the explanations of the CEC page wrapped 180 px
        before the edge of their card, and the right third of the card was empty.
        With the wide value, the explanations of the strip went 180 px past the
        edge of their card. A label that is wider than its space is not wrapped,
        and the card cuts it.

        This test has two halves, because the second half finds the order of the
        release. No text must be wider than its page, and a page with more space
        than the narrow page must use that space.
        """
        seen, roomy = 0, 0
        for key in ("strip", "cec", "status", "strip", "cec"):
            self.assertEqual(self._too_wide(key), [], key)
            for label in self.panel._wrapped:
                if not label.winfo_ismapped():
                    continue
                book = self._notebook_of(label)
                if book is None or book.winfo_width() <= 1:
                    continue
                seen += 1
                self.assertLessEqual(
                    self._laid_out_to(label), book.winfo_width(),
                    "on %s, %r is laid out to %d in a page of %d"
                    % (key, str(label.cget("text"))[:40],
                       self._laid_out_to(label), book.winfo_width()))
                if book is self.panel.sections:
                    inner = self.panel.notebook.winfo_width()
                    if self._laid_out_to(label) > inner:
                        roomy += 1
        self.assertGreater(seen, 10, "hardly anything was checked")
        self.assertGreater(
            roomy, 0, "every page was laid out to the narrower notebook's "
                      "width, so the sections are wrapping short")


class FoldTest(unittest.TestCase):
    """Opening a block's Details shows a paragraph. That is the whole job.

    A user reported this: the Check again button below the blocks came back in
    pieces at each open of the CPU details or the graphics details. It showed
    one half of its label and parts of the elements behind it. It became
    correct at the next movement of the pointer. The fault was in the draw step
    and not in the layout: a measurement showed the same size and the same
    position for the complete time.

    The cause was a call to refresh_status() from the fold. That function reads
    the complete machine again, and it then destroys and builds each block. The
    button is outside the blocks, so it survives that step, and the taller page
    moves it. It moves over an area that Tk destroyed and did not draw again,
    and X copies the pixels of a window that moves.
    """

    @classmethod
    def setUpClass(cls):
        cls.panel_module = _panel_module()

    def setUp(self):
        was = self.panel_module.ledpanel.power_part
        self.panel_module.ledpanel.power_part = (
            lambda current, available: self.panel_module.ledpanel.Part(
                "power", "CPU power", True, "Running powersave.",
                ["Wanted: powersave", "Running: powersave"]))
        self.addCleanup(lambda: setattr(self.panel_module.ledpanel,
                                        "power_part", was))
        self.root = tk.Tk()
        self.addCleanup(self._destroy)
        self.panel = self.panel_module.Panel(self.root)
        self.root.update()
        self.panel._open_section("status")
        self.panel.refresh_status()
        self._settle()

    def _destroy(self):
        if getattr(self, "root", None) is not None:
            self.root.destroy()
            self.root = None

    def _settle(self):
        for _ in range(4):
            self.root.update_idletasks()
            self.root.update()

    def test_the_detail_is_built_before_anybody_opens_it(self):
        self.assertIn("power", self.panel._part_details)
        self.assertFalse(self.panel._part_details["power"].winfo_ismapped())

    def test_opening_and_closing_shows_and_hides_it(self):
        holder = self.panel._part_details["power"]
        self.panel._fold_part("power")
        self._settle()
        self.assertTrue(holder.winfo_ismapped())
        self.panel._fold_part("power")
        self._settle()
        self.assertFalse(holder.winfo_ismapped())

    def test_the_arrow_says_which_way_it_is(self):
        arrow = self.panel._fold_arrows["power"]
        self.assertIn("\u25be", str(arrow.cget("text")))
        self.panel._fold_part("power")
        self._settle()
        self.assertIn("\u25b4", str(arrow.cget("text")))

    def test_it_does_not_ask_the_machine_anything(self):
        """A fold is a paragraph, not a fresh look at the hardware.

        The old one re-ran every check, the LACT socket and a KDE Connect
        probe with a two-second timeout, to show text it already had.
        """
        asked = []
        plain = self.panel._read_parts
        self.panel._read_parts = lambda: (asked.append(1), plain())[1]
        self.panel._fold_part("power")
        self._settle()
        self.assertEqual(asked, [])

    def test_nothing_on_the_page_is_destroyed_and_rebuilt(self):
        """The artefact's actual cause, asserted rather than described.

        The blocks used to be thrown away and made again on every fold, and
        the one widget outside them was dragged across the hole that left.
        """
        def everything():
            found = []
            def walk(widget):
                for child in widget.winfo_children():
                    found.append(str(child))
                    walk(child)
            walk(self.panel.parts_box)
            return found

        before = everything()
        self.panel._fold_part("power")
        self._settle()
        self.assertEqual(everything(), before)


class WakeRadioButtonTest(unittest.TestCase):
    """The button that answers "which radios can wake this machine?".

    A question, not a repair. The toolkit switches wakeup on for the radios it
    matches and reports that nowhere the page reads, so "the switch is on and
    it still does not wake" was a state with nowhere at all to look.
    """

    @classmethod
    def setUpClass(cls):
        cls.panel_module = _panel_module()

    def setUp(self):
        was = cec.installed
        cec.installed = lambda home=None: True
        self.addCleanup(lambda: setattr(cec, "installed", was))
        self.panel_module.ledpanel.cec_status = (
            lambda home=None, run=None: {
                "cec_device": {"device": "/dev/cec0", "exists": True,
                               "readable": True, "writable": True},
                "services": {}, "system_services": {},
                "external_volume": {"enabled": False},
                "config": {"CEC_DEVICE": "/dev/cec0"}})
        self.root = tk.Tk()
        self.addCleanup(self._destroy)
        self.panel = self.panel_module.Panel(self.root)
        self.root.update()
        self.panel._open_section("cec")
        for _ in range(4):
            self.root.update_idletasks()
            self.root.update()

    def _destroy(self):
        if getattr(self, "root", None) is not None:
            self.root.destroy()
            self.root = None

    def _buttons(self):
        found = []
        def walk(widget):
            for child in widget.winfo_children():
                try:
                    if child.winfo_ismapped():
                        found.append(str(child.cget("text")))
                except tk.TclError:
                    pass
                walk(child)
        walk(self.root)
        return found

    def test_the_page_offers_it(self):
        self.assertIn("Which radios can wake it", self._buttons())

    def test_it_asks_the_toolkits_own_helper(self):
        started = []
        self.panel.runner.start = lambda command, then=None: (
            started.append(list(command)), True)[1]
        self.panel._ask_wake_radios()
        self.assertEqual(started[0][-1], "status")
        # No password: the toolkit's installer writes a NOPASSWD rule for
        # exactly this program, so the button asks for nothing.
        self.assertEqual(started[0][:2], ["sudo", "-n"])
        self.assertNotIn("pkexec", started[0])

    def _answer(self, json_text):
        self.panel.runner.sink = lambda text, tag=None: None
        self.panel.runner.transcript = [json_text]
        self.panel.runner.start = lambda command, then=None: (
            then(0) if then else None, True)[1]
        self.panel._ask_wake_radios()
        self.root.update_idletasks()
        return str(self.panel.cec_radios.cget("text"))

    def test_the_answer_lands_on_the_page(self):
        """This window has no log pane.

        The Runner's output goes to stderr, so a sentence written only there
        is not feedback: it is a message to whoever happened to start the
        panel from a terminal. That was shipped once and is what this checks.
        """
        said = self._answer(
            '{"helper":{"devices":[{"label":"MediaTek (0e8d:0616)",'
            '"after":"enabled"}]}}')
        self.assertIn("0e8d:0616", said)
        self.assertIn("wake this machine", said)
        self.assertTrue(self.panel.cec_radios.winfo_ismapped())

    def test_nothing_is_shown_before_it_is_asked(self):
        self.assertFalse(self.panel.cec_radios.winfo_ismapped())

    def test_a_helper_that_did_not_answer_is_said_too(self):
        """The empty answer is the one somebody most needs a sentence for."""
        said = self._answer("sudo: a password is required")
        self.assertIn("did not answer", said)
        self.assertTrue(self.panel.cec_radios.winfo_ismapped())


class AdapterGoneNoticeTest(unittest.TestCase):
    """The warning on the page where the switches actually are."""

    @classmethod
    def setUpClass(cls):
        cls.panel_module = _panel_module()

    def setUp(self):
        was = cec.installed
        cec.installed = lambda home=None: True
        self.addCleanup(lambda: setattr(cec, "installed", was))
        self.reachable = False
        self.on = {"steam-button"}
        self.panel_module.ledpanel.cec_status = (
            lambda home=None, run=None: {
                "cec_device": {"device": "/dev/cec0",
                               "exists": self.reachable,
                               "readable": self.reachable,
                               "writable": self.reachable},
                "services": dict((name, {"is_enabled": True})
                                 for name in self.on),
                "system_services": {},
                "external_volume": {"enabled": False},
                "config": {"CEC_DEVICE": "/dev/cec0"}})
        self.root = tk.Tk()
        self.addCleanup(self._destroy)
        self.panel = self.panel_module.Panel(self.root)
        self.root.update()

    def _destroy(self):
        if getattr(self, "root", None) is not None:
            self.root.destroy()
            self.root = None

    def _open(self):
        self.panel._open_section("cec")
        self.panel._reread_cec()
        for _ in range(4):
            self.root.update_idletasks()
            self.root.update()

    def test_it_is_shown_when_the_adapter_is_out_and_a_feature_is_on(self):
        self._open()
        self.assertTrue(self.panel.cec_cost.winfo_ismapped())
        self.assertIn("Turn them off",
                      str(self.panel.cec_cost.cget("text")))

    def test_it_is_written_in_the_colour_of_bad_news(self):
        """Not the muted grey the other explanations on this page wear.

        Those say what a switch does and are read once. This one is a bill
        being run up on every start, and it has to be told apart from them
        at a glance.
        """
        self._open()
        style = ttk.Style(self.root)
        self.assertEqual(str(self.panel.cec_cost.cget("style")),
                         "Bad.TLabel")
        self.assertEqual(
            str(style.lookup("Bad.TLabel", "foreground")).lower(),
            str(self.panel.roles["error"]).lower())

    def test_it_still_stands_on_the_ground_it_is_packed_onto(self):
        """A colour change is the usual way to get this one wrong."""
        style = ttk.Style(self.root)
        self.assertEqual(
            str(style.lookup("Bad.TLabel", "background")).lower(),
            str(self.panel.roles["surface"]).lower())

    def test_it_takes_no_room_when_there_is_nothing_to_say(self):
        """A card growing a blank line when nothing is wrong looks broken."""
        self.reachable = True
        self._open()
        self.assertFalse(self.panel.cec_cost.winfo_ismapped())

    def test_it_goes_away_again_when_the_adapter_comes_back(self):
        self._open()
        self.assertTrue(self.panel.cec_cost.winfo_ismapped())
        self.reachable = True
        self._open()
        self.assertFalse(self.panel.cec_cost.winfo_ismapped())


class HeadlineTest(unittest.TestCase):
    """What the line under the section's title says, on the page it is on.

    Reported with a photograph: "Everything is in order." over a card reading
    "Not installed yet", on the page about the thing that was not installed.
    """

    @classmethod
    def setUpClass(cls):
        cls.panel_module = _panel_module()

    def setUp(self):
        # The toolkit is not installed on this machine and is not made to
        # look installed here: absent is the state under test.
        self.root = tk.Tk()
        self.addCleanup(self._destroy)
        self.panel = self.panel_module.Panel(self.root)
        self.root.update()

    def _destroy(self):
        if getattr(self, "root", None) is not None:
            self.root.destroy()
            self.root = None

    def _headline(self, section):
        self.panel._open_section(section)
        self.panel.refresh_status()
        for _ in range(4):
            self.root.update_idletasks()
            self.root.update()
        return str(self.panel.headline.cget("text"))

    def test_the_cec_page_says_cec_is_not_installed(self):
        self.assertIn("Not installed", self._headline("cec"))

    def test_it_is_not_painted_as_a_fault_for_being_absent(self):
        """A machine that never wanted CEC has nothing wrong with it.

        Green contradicts the card. Red reports a fault to a user who did not
        install an optional part.
        """
        self._headline("cec")
        self.assertEqual(str(self.panel.headline.cget("style")),
                         "Page.Muted.TLabel")

    def test_another_page_still_gets_the_answer_over_the_whole_window(self):
        said = self._headline("status")
        self.assertNotIn("Not installed", said)


class CardTest(unittest.TestCase):
    """A card is not one picture stretched behind it, and here is why.

    ttk fills a frame with an image by a scale step, and it scales again at
    each redraw. The cost is therefore the area of the card, and a card is the
    largest element in this window. The four cards of the CEC page, and one of
    them is 828x1071, cost 149 ms of one wheel step together. The page moved
    two times each second on the machine of that report. The same page, with
    the corners as four small pictures, moved in 3.7 ms.

    Checked structurally rather than by the clock, because a timing test on a
    build machine says more about the build machine than about the window.
    """

    @classmethod
    def setUpClass(cls):
        cls.panel_module = _panel_module()

    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self._destroy)
        self.panel = self.panel_module.Panel(self.root)
        self.root.update()

    def _destroy(self):
        if getattr(self, "root", None) is not None:
            self.root.destroy()
            self.root = None

    def _cards(self):
        found = []
        def walk(widget):
            for child in widget.winfo_children():
                try:
                    if str(child.cget("style")) == "Card.TFrame":
                        found.append(child)
                except tk.TclError:
                    pass
                walk(child)
        walk(self.root)
        return found

    def test_a_card_is_laid_out_like_any_other_frame(self):
        """No element of its own, so nothing to stretch.

        The look comes from the background it is configured with and the four
        corners pinned to it. Compared against a plain frame's layout rather
        than spelled out here: what a default layout is called is Tk's
        business, and the point is only that a card no longer has one of ours.
        """
        style = ttk.Style(self.root)
        self.assertEqual(style.layout("Card.TFrame"), style.layout("TFrame"))
        self.assertEqual(str(style.lookup("Card.TFrame", "background")),
                         self.panel.roles["surface"])

    def test_every_card_carries_its_four_corners(self):
        """Four pictures, at the four corners, and also on the padded card.

        One card is built with a padding of its own, and place() measures
        against a ttk frame's outer size rather than its padded one. Asserted
        because the two are a plausible thing for Tk to disagree about, and
        the failure is four rounded squares floating ten pixels inside the
        edges of the preview stage.
        """
        radius = self.panel_module.CARD_RADIUS
        cards = self._cards()
        self.assertGreater(len(cards), 3, "no cards were built at all")
        for card in cards:
            corners = [child for child in card.winfo_children()
                       if isinstance(child, tk.Label)
                       and child.winfo_manager() == "place"]
            self.assertEqual(len(corners), 4,
                             "%s has %d corners" % (card, len(corners)))
            if not card.winfo_ismapped():
                continue
            wide, tall = card.winfo_width(), card.winfo_height()
            self.assertEqual(
                sorted((corner.winfo_x(), corner.winfo_y())
                       for corner in corners),
                sorted([(0, 0), (wide - radius, 0), (0, tall - radius),
                        (wide - radius, tall - radius)]),
                "%s: %dx%d" % (card, wide, tall))

    def test_the_corners_are_the_size_they_are_drawn_at(self):
        """Never scaled: that is the whole point of them.

        A picture placed at its own size costs nothing to draw again. The
        moment one is stretched to the widget it is in, the cost is the
        widget's area and the stutter is back.
        """
        radius = self.panel_module.CARD_RADIUS
        for photo, where in self.panel._card_corners():
            self.assertEqual((photo.width(), photo.height()),
                             (radius, radius))
            self.assertNotIn("relwidth", where)
            self.assertNotIn("relheight", where)

    def test_a_corner_carries_the_page_s_colour_outside_the_shape(self):
        """Which is what makes a card have to stand on the page.

        The corners are the one part of a card with the colour of its parent. The
        recorded background of Card.TFrame therefore applies to these four
        pictures, and the test that reads that record still has a subject.
        """
        photo, _where = self.panel._card_corners()[0]      # the top left one
        page = self.panel.roles["_page"]
        self.assertEqual(tuple(photo.get(0, 0)),
                         tuple(int(page[i:i + 2], 16) for i in (1, 3, 5)))
        self.assertEqual(self.panel.roles["_grounds"]["Card.TFrame"].lower(),
                         page)


class GroundTest(unittest.TestCase):
    """Nothing draws its own background in the wrong colour.

    A ttk radio button paints two elements itself: a label with a -background,
    and an indicator that is a picture with its background in it. The style
    gives both colours at its build step. A radio button for a card, packed
    onto a page, therefore leaves a block of the colour of the card behind each
    name. The effect names on the preview page had that fault.
    """

    @classmethod
    def setUpClass(cls):
        cls.panel_module = _panel_module()

    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self._destroy)
        self.panel = self.panel_module.Panel(self.root)
        self.root.update()

    def _destroy(self):
        if getattr(self, "root", None) is not None:
            self.root.destroy()
            self.root = None

    def _radios(self, widget, out=None):
        out = [] if out is None else out
        for child in widget.winfo_children():
            if child.winfo_class() == "TRadiobutton":
                out.append(child)
            self._radios(child, out)
        return out

    def test_every_radio_stands_on_the_ground_it_is_packed_onto(self):
        style = ttk.Style(self.root)
        self.panel._open_section("strip")
        self.root.update()
        found = [radio for radio in self._radios(self.panel.notebook)
                 if str(radio.cget("style")) != "Rail.TRadiobutton"]
        self.assertTrue(found, "no radios to check")
        for radio in found:
            self.assertEqual(
                style.lookup(str(radio.cget("style")), "background"),
                style.lookup(str(radio.master.cget("style")) or "TFrame",
                             "background"),
                "%r sits on %r" % (radio.cget("text"),
                                   radio.master.cget("style")))


class ButtonRowTest(unittest.TestCase):
    """No button squeezed narrower than the name on it.

    The rows are equal columns that stretch, so four of them hold four names
    of one width, for each text. A name that is wider than its quarter is not
    wrapped, and the row cuts it. The font of the desktop gives the width of a
    name. For that reason this test builds the window at a size that this
    machine does not use. At its own size, four names fit and the fault is not
    visible.
    """

    @classmethod
    def setUpClass(cls):
        cls.panel_module = _panel_module()

    def setUp(self):
        import kdetheme
        was = kdetheme.read
        kdetheme.read = lambda *a, **k: dict(was(*a, **k),
                                             font=("DejaVu Sans", 14))
        self.addCleanup(lambda: setattr(kdetheme, "read", was))
        was_installed = cec.installed
        cec.installed = lambda home=None: True
        self.addCleanup(lambda: setattr(cec, "installed", was_installed))
        self.panel_module.ledpanel.cec_status = (
            lambda home=None, run=None: {
                "cec_device": {"device": "/dev/cec0", "exists": True,
                               "readable": True, "writable": True},
                "services": {}, "system_services": {},
                "external_volume": {"enabled": False}, "config": {}})
        self.root = tk.Tk()
        self.addCleanup(self._destroy)
        self.panel = self.panel_module.Panel(self.root)
        self.root.update()
        self.root.geometry("1300x900")
        for _ in range(4):
            self.root.update_idletasks()
            self.root.update()

    def _destroy(self):
        if getattr(self, "root", None) is not None:
            self.root.destroy()
            self.root = None

    def test_no_button_is_narrower_than_its_own_name(self):
        squeezed = []
        for key in ("strip", "power", "cec", "status"):
            self.panel._open_section(key)
            for _ in range(4):
                self.root.update_idletasks()
                self.root.update()
            for row, buttons in self.panel._button_rows:
                if not row.winfo_ismapped():
                    continue
                squeezed += [(key, button.cget("text"))
                             for button in buttons
                             if button.winfo_width() < button.winfo_reqwidth() - 1]
        self.assertEqual(squeezed, [])


class SidebarWidthTest(unittest.TestCase):
    """The rail is as wide as the names in it need.

    Its two lines are canvas text. Canvas text does not wrap and does not
    become shorter, and the canvas cuts it at its edge. The font of the desktop
    gives the width. The fixed width was sufficient for the default of Plasma,
    and it removed the end of each subtitle at a larger font.
    """

    @classmethod
    def setUpClass(cls):
        cls.panel_module = _panel_module()

    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self._destroy)
        self.panel = self.panel_module.Panel(self.root)
        self.root.update()

    def _destroy(self):
        if getattr(self, "root", None) is not None:
            self.root.destroy()
            self.root = None

    def _fonts(self, size):
        return {"title": ("DejaVu Sans", size, "bold"),
                "subtitle": ("DejaVu Sans",
                             size + self.panel_module.TYPE_SUPPORT)}

    def test_a_bigger_font_gets_a_wider_rail(self):
        small = self.panel._sidebar_width(self._fonts(9))
        large = self.panel._sidebar_width(self._fonts(16))
        self.assertGreater(large, small)

    def test_it_is_never_narrower_than_the_window_was_drawn_for(self):
        # A narrow font gets the rail this window was laid out around rather
        # than a rail that hugs the words.
        self.assertGreaterEqual(self.panel._sidebar_width(self._fonts(6)),
                                self.panel_module.SIDEBAR_WIDTH)

    def test_nor_wider_than_the_window_can_spare(self):
        self.assertLessEqual(self.panel._sidebar_width(self._fonts(40)),
                             self.panel_module.SIDEBAR_WIDTH_MAX)

    def test_no_name_is_cut_off_at_a_font_this_machine_does_not_use(self):
        """The case the fixed width lost, built here rather than waited for.

        This container's font fits the old 268 either way, so the rail can
        only be caught being too narrow by drawing it at a size somebody
        else's desktop would ask for.
        """
        fonts = self._fonts(16)
        width = self.panel._sidebar_width(fonts)
        for entry in self.panel_module.SECTIONS + (self.panel_module.ABOUT,):
            made = self.panel_module.SidebarEntry(
                self.root, self.panel.roles, fonts, entry,
                lambda _key: None, width)
            self.root.update_idletasks()
            for item in made.canvas.find_all():
                if made.canvas.type(item) != "text":
                    continue
                self.assertLessEqual(
                    made.canvas.bbox(item)[2], width,
                    "%s: %r" % (entry[0], made.canvas.itemcget(item, "text")))

    def test_no_name_in_the_rail_is_cut_off(self):
        for key, entry in self.panel._sidebar_entries.items():
            for item in entry.canvas.find_all():
                if entry.canvas.type(item) != "text":
                    continue
                right = entry.canvas.bbox(item)[2]
                self.assertLessEqual(
                    right, entry.width,
                    "%s: %r" % (key, entry.canvas.itemcget(item, "text")))


class NewerCardBlockTest(unittest.TestCase):
    """The same block against the newer card, through the whole path.

    A fake daemon answers with the documents of an RX 9070 XT, so these tests
    examine the window: it reads the report of the card, it draws sliders from
    it, it collects them, and it sends them back. This card needs correct code
    for each of those steps. It reports no absolute core clock, a voltage
    window of its own, and a memory clock at twice the written value. The panel
    showed two sliders where LACT showed five, and one of the two by a factor
    of two.
    """

    @classmethod
    def setUpClass(cls):
        cls.panel_module = _panel_module()

    def setUp(self):
        self.daemon = FakeDaemon(answers={
            "list_devices": NEW_DEVICES,
            "device_stats": NEW_STATS,
            "device_clocks_info": NEW_CLOCKS,
            "get_gpu_config": NEW_CONFIG,
            "list_profiles": {"profiles": [], "current_profile": ""},
            "set_gpu_config": 5,
            "confirm_pending_config": None,
            "set_profile": None,
        })
        self.addCleanup(self.daemon.close)
        for module in (lact, self.panel_module.lact,
                       self.panel_module.ledpanel.lact_module):
            was = module.SOCKET_PATH
            module.SOCKET_PATH = self.daemon.path
            self.addCleanup(
                lambda m=module, w=was: setattr(m, "SOCKET_PATH", w))
        self.root = tk.Tk()
        self.addCleanup(self._destroy)
        self.panel = self.panel_module.Panel(self.root)
        self.panel._open_section("power")
        self.root.update()

    def _destroy(self):
        if getattr(self, "root", None) is not None:
            self.root.destroy()
            self.root = None

    def _keep(self):
        """Answer the confirm dialog yes, as somebody at the machine would."""
        was = self.panel_module.CountdownDialog
        self.panel_module.CountdownDialog = (
            lambda parent, seconds: type("A", (), {"answer": True})())
        self.addCleanup(
            lambda: setattr(self.panel_module, "CountdownDialog", was))

    def test_the_window_draws_every_slider_this_card_reports(self):
        drawn = set(self.panel._gpu_vars)
        self.assertLessEqual(
            {"power_cap", "gpu_clock_offset", "voltage_offset",
             "max_memory_clock", "min_memory_clock"}, drawn)
        # And not the one it reports no range for, which would write nowhere.
        self.assertNotIn("max_core_clock", drawn)

    def test_the_sliders_open_on_the_numbers_the_other_window_shows(self):
        """Against LACT, reading the same card at the same moment.

        A user with both windows open reads one machine. These are the numbers of
        LACT for this card. A panel with a factor of two needs a conversion from
        that user. A panel that starts at the maximum also reports a clock that the
        card does not run.
        """
        opened = dict((key, round(variable.get())) for key, variable
                      in self.panel._gpu_vars.items()
                      if key in ("power_cap", "gpu_clock_offset",
                                 "voltage_offset", "max_memory_clock",
                                 "min_memory_clock"))
        self.assertEqual(opened, {"power_cap": 373, "gpu_clock_offset": 0,
                                  "voltage_offset": -20,
                                  "max_memory_clock": 2518,
                                  "min_memory_clock": 194})

    def test_applying_sends_what_lact_itself_would_have_written(self):
        """The end of it: the document that reaches the daemon.

        These are the same three settings, from this window and not from the
        window of LACT. The document must hold the values that LACT stores: a
        memory clock at one half, and an offset in its table. Without that, the
        sliders move, the Apply reports a success, and the card does not change.
        """
        self._keep()
        self.panel._gpu_vars["gpu_clock_offset"].set(15)
        self.panel._gpu_vars["max_memory_clock"].set(2400)
        self.panel._gpu_vars["min_memory_clock"].set(400)
        self.panel._apply_gpu()
        sent = [one for one in self.daemon.asked
                if one["command"] == "set_gpu_config"]
        self.assertTrue(sent, "nothing was sent to the daemon")
        config = sent[-1]["args"]["config"]
        self.assertEqual(config["max_memory_clock"], 1200)
        self.assertEqual(config["min_memory_clock"], 200)
        self.assertEqual(config["gpu_clock_offsets"], {"0": 15})
        # Not into the block an older daemon keeps, which this one has not
        # got: it would take the document, report success, and change nothing.
        self.assertNotIn("clocks_configuration", config)

    def test_what_nobody_touched_is_sent_back_as_it_was(self):
        # set_gpu_config replaces the document, so a setting left out is a
        # setting turned off on somebody's card.
        self._keep()
        self.panel._apply_gpu()
        config = [one for one in self.daemon.asked
                  if one["command"] == "set_gpu_config"][-1]["args"]["config"]
        self.assertEqual(config["power_cap"], 373.0)
        self.assertEqual(config["voltage_offset"], -20)
        self.assertEqual(config["pmfw_options"]["target_temperature"], 85)


class CountdownTest(unittest.TestCase):
    """The dialog that keeps a bad setting from being permanent."""

    @classmethod
    def setUpClass(cls):
        cls.panel_module = _panel_module()

    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)

    def test_nobody_answering_is_answered_as_no(self):
        """Which is the case the daemon's own timer exists for.

        A clock that the card cannot hold makes the screen black, and no user
        can press a button there. The dialog then closes itself with the answer
        "put them back", and that answer makes the fault recoverable. It must
        also agree with the action of the daemon.
        """
        made = self.panel_module.CountdownDialog(self.root, 1)
        self.assertFalse(made.answer)

    def test_it_counts_down_in_the_seconds_it_was_given(self):
        seen = []
        was = self.panel_module.CountdownDialog._tick

        def watch(self):
            seen.append(self.left)
            was(self)

        self.panel_module.CountdownDialog._tick = watch
        try:
            self.panel_module.CountdownDialog(self.root, 2)
        finally:
            self.panel_module.CountdownDialog._tick = was
        self.assertEqual(seen[:3], [2, 1, 0])


class AppearanceTest(unittest.TestCase):
    """Switching the window's colours, which rebuilds it.

    These tests use a home directory of their own. The panel writes the
    preference at the moment of the selection, and the real home directory
    belongs to the user of the suite.
    """

    @classmethod
    def setUpClass(cls):
        cls.panel_module = _panel_module()

    def setUp(self):
        import tempfile
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.home = holder.name
        was = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self.addCleanup(lambda: os.environ.__setitem__("HOME", was)
                        if was is not None else os.environ.pop("HOME", None))
        self.root = tk.Tk()
        self.panel = self.panel_module.Panel(self.root)
        self.root.update()
        self.addCleanup(self._destroy)

    def _destroy(self):
        if self.root is not None:
            self.root.destroy()
            self.root = None

    def _pick(self, theme):
        label = dict((value, label) for label, value
                     in appsettings.theme_choices())[theme]
        self.panel._open_section("app")
        self.root.update()
        self.panel.vars["THEME"][0].set(label)
        for _ in range(8):
            self.root.update()

    def _dark(self):
        return kdetheme.luminance(self.panel.roles["surface"]) < 0.2

    # -- the switch --------------------------------------------------------

    def test_the_window_opens_dark_and_can_be_made_light(self):
        self.assertTrue(self._dark(), "it did not open dark")
        self._pick(appsettings.THEME_LIGHT)
        self.assertFalse(self._dark(), "it stayed dark")

    def test_and_back_again(self):
        """Twice over, which is what the theme names are counted for.

        An element name of ttk belongs to one theme, and a second definition in
        that theme is not possible. A second style pass in the same theme raises
        Duplicate element for each picture. dress() does not report a picture that
        it cannot draw, so the window keeps the old colours and gives no message.
        """
        self._pick(appsettings.THEME_LIGHT)
        self.assertFalse(self._dark())
        self._pick(appsettings.THEME_DARK)
        self.assertTrue(self._dark(), "the second switch did not take")

    def test_following_the_desktop_is_the_third_answer(self):
        # This machine reports Breeze light, because kdetheme uses that scheme
        # with no Plasma. This setting therefore gives a light window here.
        self.assertFalse(kdetheme.is_dark(kdetheme.read()))
        self._pick(appsettings.THEME_SYSTEM)
        self.assertFalse(self._dark())

    def test_the_choice_is_remembered(self):
        self._pick(appsettings.THEME_LIGHT)
        self.assertEqual(appsettings.read(self.home)[appsettings.THEME],
                         appsettings.THEME_LIGHT)

    def test_it_needs_no_apply(self):
        # A user who must press a button to see a look selects that look with
        # no view of it. So this page has no Apply row.
        self.panel._open_section("app")
        self.root.update()
        self.assertFalse(self.panel._apply_shown)

    # -- what the rebuild has to carry -------------------------------------

    def test_the_page_you_were_on_is_still_open(self):
        self._pick(appsettings.THEME_LIGHT)
        self.assertEqual(self.panel.section, "app")

    def test_an_unapplied_edit_survives_it(self):
        """The one thing the new window cannot read back off disk.

        Each other value comes from the files again. An edit with no Apply is in
        the widgets only, so a rebuild of the widgets removes it. A look setting
        that removes the unsaved work of a user, with no message, is a fault.
        """
        self.panel._open_section("strip")
        self.root.update()
        self.panel.vars["LED_COUNT"][0].set(42)
        self.root.update()
        self._pick(appsettings.THEME_LIGHT)
        self.assertEqual(int(self.panel.vars["LED_COUNT"][0].get()), 42)
        self.assertIn("LED_COUNT", self.panel._differences())

    def test_the_rebuild_does_not_reprint_the_session(self):
        """The window keeps no log to carry across, and must not invent one.

        The window held a copy before, so the status bar could show the last
        line. The bar now shows one fixed sentence, so the copy is gone. The
        fault of that copy is also gone: a rebuild sent the copy through _write
        and printed the complete session to stderr again, at each change of the
        theme. This test stays, because a later change can bring the copy back.
        """
        self.panel._write("something already said\n")
        said = io.StringIO()
        was, sys.stderr = sys.stderr, said
        try:
            self._pick(appsettings.THEME_LIGHT)
        finally:
            sys.stderr = was
        # No output at all, and not only "not the line above". A rebuild
        # replaces the widgets of the window and runs no command. So each line
        # from it comes from the window itself, and the one source of such a
        # line was the copy of the log.
        self.assertEqual(said.getvalue(), "", "the rebuild printed something")

    def test_a_failure_showing_at_the_foot_is_still_showing_afterwards(self):
        """The one thing in the status bar that a rebuild could silently drop.

        The dot reads the link again a moment later, and the version is a
        constant. The failure message is the one part of that line with a reason
        that exists only in the old window.
        """
        self.panel._set_busy(False, 1)
        self.root.update()
        self._pick(appsettings.THEME_LIGHT)
        self.root.update()
        self.assertTrue(self.panel.problem.winfo_ismapped(),
                        "the warning went with the old window")

    # -- what it must not leave behind -------------------------------------

    def test_the_wheel_is_not_bound_twice(self):
        """bind_all is on the interpreter, not on a widget.

        Without a cleanup step, each rebuild adds one more handler for a window
        that no longer exists. The wheel then scrolls a destroyed page, and Tk
        reports an invalid command name for each handler.
        """
        # By how many there are, not by what they say: each rebuild binds a
        # method of a freshly built window, so the script differs every time
        # even when there is exactly one.
        def handlers():
            return self.root.bind_all("<MouseWheel>").count("_wheel")

        before = handlers()
        self.assertEqual(before, 1, "the window did not bind the wheel")
        self._pick(appsettings.THEME_LIGHT)
        self._pick(appsettings.THEME_DARK)
        self.assertEqual(handlers(), before)

    def test_the_old_window_leaves_no_widgets_behind(self):
        # One sidebar, not three: the children are destroyed rather than
        # merely unpacked, or every switch would stack another window's worth
        # of widgets under the root.
        def frames(widget):
            return sum(1 for child in widget.winfo_children()) + sum(
                frames(child) for child in widget.winfo_children())

        before = frames(self.root)
        self._pick(appsettings.THEME_LIGHT)
        self._pick(appsettings.THEME_DARK)
        self.assertLess(abs(frames(self.root) - before), before // 4,
                        "the window grew across rebuilds")

    def test_the_theme_never_reaches_the_service_config_or_a_profile(self):
        values = self.panel._collect()
        self.assertNotIn("THEME", values)
        self.assertNotIn("THEME", self.panel_module.ledpanel.profile_text(
            values))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(_has_display(), "no tkinter or no display")
class DrivesPageTest(unittest.TestCase):
    """The drives on the System page, in a real window.

    Nothing here mounts anything. The partitions come from a recorded lsblk
    answer, and the privileged applier is recorded rather than run: a test
    that ran pkexec would ask whoever runs the suite for a password.

    What is under test is the seam between the page and the applier. The rules
    themselves are in tests/test_mounts.py.
    """

    LSBLK = {"blockdevices": [
        {"name": "/dev/sdb1", "uuid": "12345678-1234-1234-1234-123456789abc",
         "fstype": "ext4", "size": 2000398934016, "label": "games",
         "mountpoint": None},
        {"name": "/dev/sdc1", "uuid": "A1B2-C3D4", "fstype": "exfat",
         "size": 512110190592, "label": "shared", "mountpoint": None},
    ]}

    @classmethod
    def setUpClass(cls):
        cls.panel_module = _panel_module()

    def setUp(self):
        import tempfile
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.home = holder.name
        was = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self.addCleanup(lambda: os.environ.__setitem__("HOME", was)
                        if was is not None else os.environ.pop("HOME", None))

        # The record and the machine, both answered here. Panel.__init__ reads
        # the record, so this stands before the window is built.
        self.mounts = self.panel_module.mounts
        self.record = []
        self._was = (self.mounts.read, self.mounts.partitions)
        self.mounts.read = lambda path=None: list(self.record)
        self.mounts.partitions = lambda run=None: [
            dict(one, uuid=one["uuid"], type=one["fstype"],
                 device=one["name"], label=one["label"], size=one["size"],
                 mountpoint="")
            for one in self.LSBLK["blockdevices"]]
        self.addCleanup(self._put_back)

        self.root = tk.Tk()
        self.addCleanup(self._destroy)
        self.panel = self.panel_module.Panel(self.root)
        self.said = []
        self.panel._say = lambda title, message: self.said.append(message)
        self.panel._ask = lambda *args, **kwargs: True
        self.ran = []
        self.panel.runner.start = lambda command, done=None: (
            self.ran.append(command), True)[1]
        self.root.update()
        self.panel._open_section("keyboard")
        self.root.update()

    def _put_back(self):
        self.mounts.read, self.mounts.partitions = self._was

    def _destroy(self):
        if getattr(self, "root", None) is not None:
            self.root.destroy()
            self.root = None

    def _every_widget(self, widget=None, found=None):
        found = [] if found is None else found
        widget = self.root if widget is None else widget
        found.append(widget)
        for child in widget.winfo_children():
            self._every_widget(child, found)
        return found

    def _staged(self):
        """The record that the page handed to the applier, read back."""
        self.assertTrue(self.ran, "no command reached the applier")
        with open(self.ran[-1][2]) as handle:
            return json.load(handle)

    GAMES = {"uuid": "12345678-1234-1234-1234-123456789abc",
             "where": "/mnt/games", "type": "ext4",
             "options": "defaults,noatime", "timeout": "5s"}
    SHARED = {"uuid": "A1B2-C3D4", "where": "/mnt/shared", "type": "exfat",
              "options": "defaults,noatime,uid=1000,gid=1000",
              "timeout": "5s"}

    def _draw(self, *records):
        """Puts a record on the page, and returns the box that holds it."""
        self.record = list(records)
        self.panel._drives = list(records)
        self.panel._show_drives()
        self.root.geometry("%dx820" % self.NARROW)
        self.root.update()
        return self.panel.drives_box

    def _named(self, name):
        return [widget for widget in self._every_widget()
                if isinstance(widget, ttk.Button)
                and str(widget.cget("text")) == name]

    def _add(self, where, which=0):
        offered = self.panel._menus["drive-partition"]
        self.panel._drive_choice.set(offered[which][0])
        self.panel._drive_where.delete(0, "end")
        self.panel._drive_where.insert(0, where)
        self.panel.add_drive()

    def test_the_page_offers_the_partitions_of_the_machine(self):
        """Read off lsblk, and not typed.

        A UUID is not a value to ask a person to copy by hand, and it is the
        value that decides which drive gets mounted where.
        """
        offered = [said for said, _uuid
                   in self.panel._menus["drive-partition"]]
        self.assertTrue(any("/dev/sdb1" in one for one in offered), offered)
        self.assertTrue(any("games" in one for one in offered), offered)

    def test_picking_one_offers_a_mount_point(self):
        # The label of the filesystem is the name a person gave the drive.
        offered = self.panel._menus["drive-partition"]
        self.panel._drive_choice.set(offered[0][0])
        self.root.update()
        self.assertEqual(self.panel._drive_where.get(), "/mnt/games")

    def test_adding_one_hands_the_record_to_the_applier(self):
        self._add("/mnt/games")
        found = self._staged()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["where"], "/mnt/games")
        self.assertEqual(found[0]["uuid"],
                         "12345678-1234-1234-1234-123456789abc")

    def test_the_command_is_the_privileged_applier(self):
        self._add("/mnt/games")
        command = self.ran[-1]
        self.assertEqual(command[0], "pkexec")
        self.assertTrue(command[1].endswith("scripts/apply-mounts.sh"),
                        command[1])

    def test_nothing_the_page_runs_names_fstab(self):
        """The one file this page must never write.

        /etc/fstab also holds the entries for /, /boot, /home and /var. This
        page writes a mount unit, which belongs to this project alone.
        """
        self._add("/mnt/games")
        for command in self.ran:
            for part in command:
                self.assertNotIn("fstab", part)

    def test_a_mount_point_that_belongs_to_steamos_never_reaches_root(self):
        """Refused in the window, before any privileged command runs.

        The applier refuses it as well, and both are wanted: a page that sent
        it and relied on the refusal would ask for a password first, and then
        report a failure for something it knew about.
        """
        self._add("/usr")
        self.assertEqual(self.ran, [])
        self.assertTrue(self.said)
        self.assertIn("/usr", self.said[-1])

    def test_a_drive_with_no_mount_point_is_refused(self):
        self._add("")
        self.assertEqual(self.ran, [])
        self.assertTrue(self.said)

    def test_a_filesystem_with_no_owner_takes_one_from_the_options(self):
        """exfat records no owner, so every file belongs to whoever mounts it.

        Without this the drive belongs to root, Steam cannot write a library
        to it, and no chown can change that: the filesystem has nowhere to
        record the answer.
        """
        self._add("/mnt/shared", which=1)
        found = self._staged()
        self.assertIn("uid=%d" % os.getuid(), found[0]["options"])
        self.assertIn("gid=%d" % os.getgid(), found[0]["options"])

    def test_a_configured_drive_is_no_longer_offered_to_add(self):
        # Two units on one UUID is one drive mounted twice, and the second
        # mount is the one that fails.
        self.record = [{"uuid": "12345678-1234-1234-1234-123456789abc",
                        "where": "/mnt/games", "type": "ext4",
                        "options": "defaults,noatime", "timeout": "5s"}]
        self.panel._drives = list(self.record)
        self.panel._show_drives()
        self.root.update()
        offered = [uuid for _said, uuid
                   in self.panel._menus["drive-partition"]]
        self.assertNotIn("12345678-1234-1234-1234-123456789abc", offered)

    def test_giving_a_drive_away_names_it_to_the_applier(self):
        """The chown, as the second argument and not as a second command.

        One prompt for the whole operation. A second pkexec would be a second
        password for a thing a person asked for once.
        """
        entry = {"uuid": "12345678-1234-1234-1234-123456789abc",
                 "where": "/mnt/games", "type": "ext4",
                 "options": "defaults,noatime", "timeout": "5s"}
        self.panel._drives = [entry]
        self.panel.own_drive(entry)
        self.assertEqual(self.ran[-1][-1], "/mnt/games")

    def test_removing_one_writes_the_record_without_it(self):
        entry = {"uuid": "12345678-1234-1234-1234-123456789abc",
                 "where": "/mnt/games", "type": "ext4",
                 "options": "defaults,noatime", "timeout": "5s"}
        self.panel._drives = [entry]
        self.panel.remove_drive(entry)
        self.assertEqual(self._staged(), [])

    # Two widths, both above the smallest window this panel allows. What is
    # under test is what the page does with the width between them.
    NARROW = 1160
    WIDE = 1400

    def test_the_buttons_stay_beside_the_drive_in_a_wide_window(self):
        """The extra width of the window belongs to the empty column.

        The first version of this card put Remove on the right of a row that
        filled the card. The button then followed the edge of the window, and
        a wide window left a hand of white space between the name of a drive
        and the button that takes it away. This measures that: the button must
        not move when the window gets wider.
        """
        self._draw(self.GAMES)
        remove = self._named("Remove")[0]
        was = remove.winfo_x()
        self.root.geometry("%dx820" % self.WIDE)
        self.root.update()
        self.assertGreater(self.panel.drives_box.winfo_width(), 0)
        self.assertEqual(remove.winfo_x(), was,
                         "the button followed the edge of the window")

    def test_the_buttons_of_two_drives_stand_in_one_column(self):
        """One grid for every drive, and not one row each.

        The second drive here is exfat, which offers no "Take ownership". That
        column stays empty on its row rather than closing up, so Remove stands
        under Remove.
        """
        self._draw(self.GAMES, self.SHARED)
        found = self._named("Remove")
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0].winfo_x(), found[1].winfo_x())
        self.assertNotEqual(found[0].winfo_y(), found[1].winfo_y())

    def test_a_filesystem_with_no_owner_is_not_offered_a_chown(self):
        """exfat records no owner, so chown on it fails.

        The mount options carry the owner for such a drive, and the page
        writes them when the drive is added.
        """
        self._draw(self.SHARED)
        self.assertEqual(self._named("Take ownership"), [])
        self._draw(self.GAMES)
        self.assertEqual(len(self._named("Take ownership")), 1)

    def test_the_explanation_is_on_the_page(self):
        """A label that nothing packs exists and is never drawn.

        _note builds the label and the caller places it, and the first version
        of this page forgot the second half. The window opened, every test
        passed, and the paragraph that says why this is not /etc/fstab was
        simply not there. Nothing but a look at the window found it.
        """
        found = [widget for widget in self._every_widget()
                 if isinstance(widget, ttk.Label)
                 and str(widget.cget("text")).startswith("A second drive")]
        self.assertTrue(found, "the drives card has no explanation")
        self.assertTrue(found[0].winfo_ismapped(),
                        "the explanation was built and never placed")
