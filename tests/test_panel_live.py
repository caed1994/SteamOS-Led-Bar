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
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, os.path.join(HERE, "..", "gui"))

import appsettings                                          # noqa: E402
import kdetheme                                             # noqa: E402
import syssettings                                          # noqa: E402

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:                                         # pragma: no cover
    tk = ttk = None


def _panel_module():
    """Import the panel, which has no .py on the end of it."""
    import importlib.machinery
    import importlib.util
    path = os.path.join(HERE, "..", "gui", "steamos-led-panel")
    loader = importlib.machinery.SourceFileLoader("steamos_led_panel", path)
    spec = importlib.util.spec_from_loader("steamos_led_panel", loader)
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
        # The bug this is here for: the rail was set once when it was built,
        # so anything that changed the page in code left the pill behind on
        # whatever had been open first.
        #
        # update() rather than update_idletasks() throughout: selecting a page
        # posts a virtual event, and only the full one delivers it.
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
        # Both at once would be two lists of the same six pages. An emptied
        # layout is not reported back as empty - Tk puts a "null" element in
        # its place - so what is checked is that nothing in it draws a label.
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
        # A row that opens a block stands clear of both what is above it and
        # what it governs; the rows it governs sit close together under it.
        # Equal gaps either side are what stop it reading as glued to the row
        # beneath it.
        #
        # Measured on the Strip page, where the pattern still is one: the
        # notification blocks used to be the example and are now one line each
        # - see the flash rows - so the rainbow choice with the temperature
        # marks stepped in under it is what is left of the shape.
        self.panel.notebook.select(self._page_named("Strip"))
        # The block has to be on the page to be measured, and which block is
        # on it is now a choice - see the note above DEPENDS_ON.
        self.panel.vars["RAINBOW_SHOWS"][0].set("Temperature")
        self.root.update()
        rows = ("STANDBY_PULSE", "RAINBOW_SHOWS", "TEMPERATURE_MIN",
                "TEMPERATURE_MAX")
        # Measured on the name labels rather than on the controls. All of them
        # are plain labels of one height, where a switch, a menu and a slider
        # are three heights - and a control shorter than its row sits centred
        # in it, so control to control reports the widgets as much as the
        # spacing. The rows are what this is about.
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

        The other half of the answer: a row waiting on a switch is greyed and
        stays, a row waiting on a choice is taken away - see the note above
        DEPENDS_ON. grid_info() empties when a widget is grid_remove'd, and
        fills again with the place it had.
        """
        labels, controls = self.panel._rows[key]
        return bool((labels + controls)[0].grid_info())

    def test_the_slot_reaches_only_the_rows_its_own_choice_needs(self):
        """One menu, and two sets of rows waiting on two different answers.

        Greyed rather than hidden, like everything else here - but greyed by
        the choice, not by the page: the temperature marks mean nothing while
        the slot is showing the load, and the gauge's two colours mean nothing
        while it is showing the temperature. Both wrong at once would be four
        settings out of reach with nothing on the page to say why.
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

        The rainbow slot is Game Mode's, and since the desktop's scenes came
        off that slot there is a second way to ask for the temperature gauge:
        pick it as the scene. Its marks and its sensor are what that scene
        reads, so hiding them because the *other* mode is showing something
        else would be hiding the settings the bar is actually using - on a
        different page from the one that was changed, where nobody would think
        to look for them.
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

        forget throws the placement away, so the row would come back at
        whatever position the grid handed out next - which on this page means
        underneath everything, in the wrong group.
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

        An empty grid row still has the floor every row is given, so that a
        column of switches, menus and sliders keeps one rhythm - and a floor
        under a row that is gone is the gap the row used to fill. Three of
        those below the slot is a page that looks like it failed to draw.
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

        Three of the five scenes take the colour and two do not, and a rule
        per scene would be three rules that all have to hold at once - which
        is a colour gone forever. Checked here rather than by reading the
        table, because what people see is the control.

        Taken away rather than greyed: a scene is a choice, and the colour
        belongs to the scenes that have one - see the note above DEPENDS_ON.
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

        Which is all ground: a swatch is a rounded square, so the pixel
        outside its curve is whatever the swatch was baked against. That is
        the whole of the fault being tested - if it does not match what the
        widget is painted with, it shows as a box behind the colour.
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

        A swatch has no alpha - a quarter of its pixels are a blend with
        whatever is behind it - so one baked against the ordinary shade sits
        on a greyed field as a box. In the light theme the two shades are
        near enough that nothing shows, which is why this went out.
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

        Tk draws an image on a disabled widget through a fifty per cent
        stipple - that is the hatching over a greyed colour - and what shows
        through the holes is the style's own background option, not the shade
        the field element paints. Left disagreeing, every other pixel under
        the swatch came out the ordinary shade while the field around them
        was the greyed one: a box, in exactly the place a swatch baked
        against the wrong ground would put one.

        Which is why this is not checked on the swatch. A test that read the
        image passed while the screen was still wrong - measured, on a dark
        theme, before this was found.
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

        Every button in the window is greyed while a command runs, and the
        drop-down fields are buttons - so letting them all go again at the
        end handed back settings that DEPENDS_ON is meant to be holding.
        Until the window was closed and opened, which is what made it look
        like a drawing fault rather than a logic one.
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

        An outlined button blends its own fill under the pointer, so a swatch
        baked against the plain shade shows as a box on the hovered one.
        Driven by the states themselves rather than by moving a real pointer:
        what the fix has to do is redraw when the state moves, and a test
        that needed the mouse would be testing X.
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

        Every moved control asks every dependent row whether it should still
        be greyed, and a drag delivers one of those per pixel the pointer
        travels. Almost none of the answers ever change - measured, eleven
        hundred asked and none changed over fifty steps - but each was being
        applied anyway: thirty-odd widget reconfigures and five colour chips
        redrawn from scratch, nine milliseconds a step, and the window
        repainted as fast as the events arrived.

        Counted rather than timed. A stopwatch on a build machine says
        nothing anybody can act on; "it stopped touching the widgets" is the
        thing that made it cheap.
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
        # The rail takes a sixth of the width, so wrapping to the window laid
        # the text out wider than the page it sits in - and a label whose text
        # is wider than its slot is not wrapped, it is cut off. Asking the
        # notebook its width while handling the window's own Configure gives
        # the width before the resize, so the sizes are walked here.
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
                # Only this page's own labels: a page that has been shown once
                # still reports itself mapped after the notebook moves on, and
                # keeps whatever width it had when it was last laid out.
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
        # The settings pages are longer than a 1080p screen can hold at a
        # large desktop font, so the foot of one has to be reachable.
        #
        # At the smallest the window may be, which is the worst case and is
        # also the only size this can be asked at: a geometry under the
        # minimum is refused outright, so the 1100x520 that used to be here
        # left the window at its minimum and the page fitting comfortably.
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

    def test_the_foot_of_the_window_keeps_its_place_on_a_short_screen(self):
        # Pack hands out room in the order it was asked for it. With the pages
        # asking first and taking the lot, a window too short for them had no
        # Apply row and no status bar at all - both were packed later and there
        # was nothing left to give them.
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
        self.assertTrue(self.panel.link_said.winfo_ismapped(),
                        "the status bar went missing")

    def test_a_page_asks_for_the_width_its_content_needs(self):
        # A canvas does not pass on the size of what it holds - it asks for its
        # own default width. Without saying otherwise the window opened at that
        # default and every page came out squeezed into it, drop-downs cut off
        # mid-word and sliders with no travel.
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
        # The sensor menu is the widest control on the page, and it is only
        # on the page while the slot is showing the temperature.
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
        # A button that is always pressable says nothing about whether
        # pressing it would do anything - which is also the answer to "did I
        # already apply that?", the question the window could not answer.
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
        # The menus hold what a value looks like, and the file may hold the
        # same colour in another case. Comparing the raw strings made the
        # window claim an unsaved change nobody had made.
        # The colour the window is holding, spelled the other way: written out
        # as a literal this stopped being the same colour the moment the
        # default moved, and then tested nothing at all.
        holding = self.panel._value_for(
            "ACHIEVEMENT_COLOR", self.panel.vars["ACHIEVEMENT_COLOR"][0].get())
        self.assertRegex(holding, r"^#[0-9a-f]{6}$")
        self.panel.config["ACHIEVEMENT_COLOR"] = holding.upper()
        self.panel._refresh_unsaved()
        self.root.update()
        self.assertNotIn("ACHIEVEMENT_COLOR", self.panel._differences())

    def test_a_colour_menu_shows_the_colour_it_holds(self):
        # A list of colours that shows only their names is the one place in a
        # window about light where you cannot see what you are choosing.
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
        # The canvas is built once per length and only moved afterwards, so
        # changing the setting has to rebuild it - otherwise the page draws a
        # seventeen LED strip and calls it yours.
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

        With the pointer moved out of the way first. A row under the pointer
        fills as a hover the moment the list maps, so a test asking which row
        is filled would otherwise be asking where the mouse happens to be -
        and on a bare X server that is the middle of the screen, which is
        roughly where these lists open.
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
        # Measured on a real desktop: a tk.Menu went on floating over a file
        # manager that had taken the focus, and Tk gave it no say in that.
        # This one is ours, so it can simply be told.
        popup = self._drop("ACHIEVEMENT_COLOR")
        self.assertTrue(popup.window.winfo_ismapped())
        self.panel._dismiss_menus()
        self.root.update()
        self.assertIsNone(self.panel._popup)

    def test_nothing_grabs_while_a_list_is_open(self):
        # Measured under a window manager, with another application raised
        # over the panel: with a grab of either kind the list stayed up,
        # without one it goes. A grab swallows the focus change that says this
        # application is no longer the one being used, and for an
        # override-redirect window - which is what an undecorated list has to
        # be - that notice is the only thing it ever gets.
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
        # It shows "ESP8266 - GPIO2"; the flasher is given "esp8266_gpio2".
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
        # A button dead before anyone has looked would be the window refusing
        # to do something it can perfectly well do.
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

    def test_a_command_that_fails_says_so_beside_the_status_light(self):
        # The window has no log pane: what a command printed goes to stderr,
        # and what is left in here is one line saying that something went
        # wrong, next to the light that says whether the bar is there.
        self.panel._write("Running the installer\n")
        self.panel._write("cp: cannot create regular file: Permission denied\n")
        self.panel._set_busy(False, 0)
        self.root.update()
        self.assertFalse(self.panel.problem.winfo_ismapped(),
                         "a command that worked left a warning behind")
        self.panel._set_busy(False, 1)
        self.root.update()
        self.assertTrue(self.panel.problem.winfo_ismapped(),
                        "nothing said the command failed")
        self.assertIn("Permission denied", self.panel.problem.cget("text"))

    def test_the_next_command_that_works_takes_the_warning_away(self):
        # A warning left standing beside a command that has just worked is
        # worse than no warning at all.
        self.panel._write("it broke\n")
        self.panel._set_busy(False, 1)
        self.root.update()
        self.assertTrue(self.panel.problem.winfo_ismapped())
        self.panel._set_busy(False, 0)
        self.root.update()
        self.assertFalse(self.panel.problem.winfo_ismapped())

    def test_the_reason_is_the_command_s_last_word_not_the_window_s(self):
        """What the window says around a command is not what went wrong.

        The Runner keeps the command's own output apart from it, and that is
        what is read: taking the last line of the log instead would report
        "Saved 12 settings" as the reason an Apply that saved them and then
        failed to restart the service had failed.
        """
        self.panel.runner.transcript = ["Applying...\n",
                                        "write error: Invalid argument\n"]
        self.panel._write("Saved 12 settings to /etc/steamos-led-serial.conf\n")
        self.assertEqual(self.panel._last_said(),
                         "write error: Invalid argument")

    def test_a_command_that_never_started_still_says_why(self):
        # There was no process, so the Runner has no transcript of one - and
        # "cannot run: no such file" is the whole of what went wrong. The
        # window's two bookends round it are how the failure was reported,
        # not what it was.
        self.panel.runner.transcript = []
        self.panel._write("$ /usr/local/bin/steamos-led-power --apply\n")
        self.panel._write("cannot run: [Errno 2] No such file or directory\n")
        self.panel._write("[exit 1]\n")
        self.assertEqual(self.panel._last_said(),
                         "cannot run: [Errno 2] No such file or directory")

    def test_a_command_that_printed_nothing_shows_no_empty_warning(self):
        self.panel._set_busy(False, 1)
        self.root.update()
        self.assertFalse(self.panel.problem.winfo_ismapped(),
                         "an empty warning is worse than none")

    def test_a_long_reason_is_cut_rather_than_wrapped(self):
        # The status bar is one line tall and shares it with the dot, its
        # sentence and the version. A message long enough to wrap would push
        # every page above it up.
        tall = self.root.winfo_height()
        self.panel._write("x" * 400 + "\n")
        self.panel._set_busy(False, 1)
        self.root.update()
        said = self.panel.problem.cget("text")
        self.assertLessEqual(len(said), self.panel_module.PROBLEM_CHARS + 2)
        self.assertTrue(said.endswith("\u2026"), said[-8:])
        self.assertEqual(self.panel.statusbar.winfo_height(),
                         self.panel.link_said.winfo_reqheight())
        self.assertEqual(self.root.winfo_height(), tall)

    def test_a_terminal_that_went_away_does_not_break_the_window(self):
        """The README tells people to start this from a terminal.

        Closing that terminal with the window still open leaves a broken pipe
        on the other end of stderr, and writing to it raises - which without
        this would come out of the middle of an install. The line is kept
        either way: the blip has to work with nobody reading the log.
        """
        class Gone:
            def write(self, _text):
                raise BrokenPipeError(32, "Broken pipe")

        was, sys.stderr = sys.stderr, Gone()
        try:
            self.panel._write("the install carried on\n")
        finally:
            sys.stderr = was
        self.assertIn("the install carried on", "".join(self.panel._log))

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

    def test_a_running_command_shows_a_bar_and_deadens_the_buttons(self):
        self.assertFalse(self.panel.progress.canvas.winfo_ismapped())
        self.panel._set_busy(True)
        self.root.update()
        self.assertTrue(self.panel.progress.canvas.winfo_ismapped(),
                        "nothing says a command is running")
        for button in self.panel._busy_buttons:
            self.assertIn("disabled", button.state(), button.cget("text"))
        # The one that only folds something away stays live: it starts
        # nothing, so greying it would take away a control for no reason.
        self.assertNotIn("disabled", self.panel.details.state())
        self.panel._set_busy(False, 0)          # the bar books its own next step

    def test_apply_keeps_its_own_reason_to_be_dead_afterwards(self):
        # Everything comes back when a command ends - except Apply, which has
        # a reason of its own that outlives it.
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
        # One selected, always, and never two. The pill is the only thing on
        # screen that says where you are, so a stale one is the window lying
        # about what it is showing.
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

        Checking the installation and updating it are about the whole toolbox
        and are a section now. Flashing the ESP is the one block that was only
        ever about the strip, and it is on the Test page - beside the
        self-test, which is what tells you the board needs reflashing.

        Read off the built window rather than off the source, because what
        this is about is which page a person finds the button on.
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

    def test_the_checklist_still_folds_where_the_page_now_lives(self):
        """It opens, it closes, and the window stays on the screen.

        Not the window's height: _details_change works out how much taller to
        make it, but that is about the *smoothness* of the fold - _fit_window
        settles the size afterwards either way, and on a screen this short the
        window is already at the cap where neither can grow it. What has to
        hold is that the list appears and goes away, on a page that has just
        moved out of one notebook and into another.
        """
        self.panel._open_section("status")
        # Closed first, and deliberately: refresh_status unfolds the list on a
        # machine with something wrong with it, which this one has - so the
        # window opens with it already down and "open it" would be a no-op.
        for show in (False, True, False):
            self.panel._show_details(show)
            for _ in range(4):
                self.root.update()
            self.assertEqual(bool(self.panel.checklist.winfo_ismapped()),
                             show)
            self.assertLessEqual(self.root.winfo_height(),
                                 self.root.winfo_screenheight())
        # And the arithmetic is asked of the notebook the page is in. Against
        # the strip's, which is what it used to read, this is a number about a
        # stack the checklist is not in.
        self.assertIsInstance(self.panel._details_change(True), int)

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
        # Three states and three colours: unknown before anything has been
        # read, and then good or bad. Grey saying "looking" is honest where a
        # red one would be a fault report about an unanswered question.
        seen = {}
        for connected in (None, True, False):
            self.panel._say_link(connected)
            self.root.update()
            seen[connected] = self.panel.link_dot.itemcget(
                self.panel._dot, "fill")
            self.assertTrue(self.panel.link_said.cget("text").strip())
        self.assertEqual(len(set(seen.values())), 3, seen)
        self.assertEqual(seen[True], self.panel.roles["positive"])
        self.assertEqual(seen[False], self.panel.roles["error"])

    def _every_widget(self, widget=None, found=None):
        found = [] if found is None else found
        widget = self.root if widget is None else widget
        found.append(widget)
        for child in widget.winfo_children():
            self._every_widget(child, found)
        return found

    def test_the_cpu_menus_are_built_from_the_machine(self):
        """Not from a list in the panel, which is the whole point of them.

        This build machine has no cpufreq at all, so the governor menu comes
        out with nothing but "leave it alone" - the case a hardcoded list
        would get wrong by offering governors that do not exist here.
        """
        self.panel._open_section("power")
        self.root.update()
        offered = [value for _label, value
                   in self.panel._menus["CPU_GOVERNOR"]]
        self.assertEqual(offered,
                         [""] + list(self.panel_module.power.governors()))

    def test_a_machine_with_no_preference_file_gets_no_row_for_one(self):
        """Never built, rather than built and greyed.

        A driver in passive mode has no energy_performance_preference at all,
        and this machine has no cpufreq whatever - a menu with nothing in it
        is a row about a setting that does not exist. Left out of self.vars
        too, so nothing collects it and nothing compares it.
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

    def test_no_card_stands_on_another_card(self):
        """Reported: dark notches inside the corners of the placeholder cards.

        Card.TFrame is drawn with a nine-slice image, and that image carries
        its own corners - painted against the *page*, because that is what a
        card normally stands on. Put one inside another and those four corners
        are four notches of the page's colour on top of the card underneath.

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
        """Forced, and nothing about the desktop may talk it out of it.

        This build machine reports Breeze *light* - kdetheme.read() falls back
        to it when there is no Plasma to ask - so a window that followed the
        desktop would come up light right here. That it does not is the whole
        assertion.

        The preview is why: a page of lit LEDs judged against a white window
        is a page of washed-out LEDs.
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
        # Nothing in a test may actually run pkexec. Recorded instead, which
        # is also the assertion: whether the privileged half ran at all.
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

        A keyboard layout is written by the panel, as you, into your own home.
        Sending it through the helper that installs /etc would be a password
        prompt for a file that needs none - and would bounce the LED service
        for a setting it does not read.
        """
        self._layout().set(self._label("de"))
        self.root.update()
        self.panel.apply_settings()
        self.root.update()
        self.assertEqual(self.ran, [], "it went through pkexec anyway")

    def test_a_service_setting_still_goes_through_the_helper(self):
        # The other half: the split must not have taken the privileged path
        # away from the settings that do need it.
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

        The menu cannot be ninety-nine entries long - it does not scroll - so
        the way to an unlisted layout is the file. Opening the window must not
        then quietly replace it with the first entry that does fit.
        """
        syssettings.write({syssettings.LAYOUT: "kz"}, self.home)
        self._build()
        self.assertEqual(self.panel._value_for(
            syssettings.LAYOUT, self._layout().get()), "kz")
        self.assertEqual(self.panel._differences(), [])

    def test_the_whole_menu_fits_on_the_screen(self):
        """Measured on the window, which is the only place it shows.

        The drop-down sizes itself to its entries and is then clamped to the
        screen: entries past the bottom edge are drawn nowhere and cannot be
        clicked. At twenty-eight entries this overflowed a 1280x800 display -
        a Steam Machine's own - by 126 pixels, and the suite was green.
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
class AppearanceTest(unittest.TestCase):
    """Switching the window's colours, which rebuilds it.

    In a home of its own, because the preference is saved the moment it is
    picked and the real one belongs to whoever is running the suite.
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

        ttk element names live in a theme and cannot be redefined within one.
        A second dressing that reused the theme would raise Duplicate element
        for every picture, and dress() swallows what it cannot draw - so the
        window would have kept the old colours and said nothing.
        """
        self._pick(appsettings.THEME_LIGHT)
        self.assertFalse(self._dark())
        self._pick(appsettings.THEME_DARK)
        self.assertTrue(self._dark(), "the second switch did not take")

    def test_following_the_desktop_is_the_third_answer(self):
        # This machine reports Breeze light - kdetheme falls back to it when
        # there is no Plasma - so following it means a light window here.
        self.assertFalse(kdetheme.is_dark(kdetheme.read()))
        self._pick(appsettings.THEME_SYSTEM)
        self.assertFalse(self._dark())

    def test_the_choice_is_remembered(self):
        self._pick(appsettings.THEME_LIGHT)
        self.assertEqual(appsettings.read(self.home)[appsettings.THEME],
                         appsettings.THEME_LIGHT)

    def test_it_needs_no_apply(self):
        # A look you have to press a button to see is a look you are choosing
        # blind, so the row is not offered on this page at all.
        self.panel._open_section("app")
        self.root.update()
        self.assertFalse(self.panel._apply_shown)

    # -- what the rebuild has to carry -------------------------------------

    def test_the_page_you_were_on_is_still_open(self):
        self._pick(appsettings.THEME_LIGHT)
        self.assertEqual(self.panel.section, "app")

    def test_an_unapplied_edit_survives_it(self):
        """The one thing the new window cannot read back off disk.

        Everything else comes from the files again. An edit that had not been
        applied is only in the widgets, so throwing the widgets away would
        throw it away - and a look setting that quietly discards your unsaved
        work is a trap.
        """
        self.panel._open_section("strip")
        self.root.update()
        self.panel.vars["LED_COUNT"][0].set(42)
        self.root.update()
        self._pick(appsettings.THEME_LIGHT)
        self.assertEqual(int(self.panel.vars["LED_COUNT"][0].get()), 42)
        self.assertIn("LED_COUNT", self.panel._differences())

    def test_the_log_is_carried_across_rather_than_said_again(self):
        """It survives the rebuild, and stderr does not hear it twice.

        Replaying it through _write would leave the list looking right and
        print the whole session out again for every theme change - so what is
        checked is the stderr, which is the half that would go wrong.
        """
        self.panel._write("something worth keeping\n")
        said = io.StringIO()
        was, sys.stderr = sys.stderr, said
        try:
            self._pick(appsettings.THEME_LIGHT)
        finally:
            sys.stderr = was
        self.assertIn("something worth keeping", "".join(self.panel._log))
        self.assertNotIn("worth keeping", said.getvalue())

    def test_a_failure_showing_at_the_foot_is_still_showing_afterwards(self):
        """The one thing in the status bar that a rebuild could silently drop.

        The dot re-reads the link a moment later on its own, and the version
        is a constant - the blip is the only part of that line whose reason to
        be there lives nowhere but in the window that is being thrown away.
        """
        self.panel._write("the pipe went away\n")
        self.panel._set_busy(False, 1)
        self.root.update()
        self._pick(appsettings.THEME_LIGHT)
        self.root.update()
        self.assertTrue(self.panel.problem.winfo_ismapped(),
                        "the warning went with the old window")
        self.assertIn("the pipe went away", self.panel.problem.cget("text"))

    # -- what it must not leave behind -------------------------------------

    def test_the_wheel_is_not_bound_twice(self):
        """bind_all is on the interpreter, not on a widget.

        Left alone, every rebuild would add another handler pointing at a
        window that no longer exists - so the wheel would scroll a destroyed
        page, and Tk would report an invalid command name for each one.
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
