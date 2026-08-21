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

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, os.path.join(HERE, "..", "gui"))

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

    def test_a_scene_with_no_colour_greys_the_colour_out(self):
        """The entry that means several values, which DEPENDS_ON could not say.

        Three of the five scenes take the colour and two do not, and a rule
        per scene would be three rules that all have to hold at once - which
        is a colour greyed out forever. Checked here rather than by reading
        the table, because what people see is the control.
        """
        self.panel.notebook.select(self._page_named("Desktop mode"))
        self.root.update()
        scene = self.panel.vars["DESKTOP_SCENE"][0]
        for label, wanted in (("One colour", False), ("Breath", False),
                              ("Patrol", False), ("Rainbow", True),
                              ("Off", True), ("Leave it to Steam", True)):
            scene.set(label)
            self.root.update()
            self.assertEqual(self._greyed("DESKTOP_COLOR"), wanted, label)

    def test_each_of_the_three_is_greyed_by_a_rule_of_its_own(self):
        """Because no two of them apply to the same set of scenes.

        A rainbow has a brightness and a speed but no colour of yours; one
        colour standing still has a colour and a brightness but no speed.
        Greying all three together would put two settings that do something
        out of reach, which is the same to look at as a knob that is broken.
        """
        self.panel.notebook.select(self._page_named("Desktop mode"))
        self.root.update()
        scene = self.panel.vars["DESKTOP_SCENE"][0]
        #                       colour brightness speed
        for label, wanted in (("One colour", (False, False, True)),
                              ("Breath", (False, False, False)),
                              ("Patrol", (False, False, False)),
                              ("Rainbow", (True, False, False)),
                              ("Off", (True, True, True)),
                              ("Leave it to Steam", (True, True, True))):
            scene.set(label)
            self.root.update()
            self.assertEqual(
                (self._greyed("DESKTOP_COLOR"),
                 self._greyed("DESKTOP_BRIGHTNESS"),
                 self._greyed("DESKTOP_SPEED")), wanted, label)

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
        self.root.geometry("1100x520")
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
        # Apply row and no log at all - both were packed later and there was
        # nothing left to give them.
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
        self.assertTrue(self.panel.output_toggle.winfo_ismapped(),
                        "the log went missing")

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

    def _log_order(self):
        """Which of "grow the window" and "fill it" happened first."""
        order = []
        pack, forget = self.panel.output_box.pack, self.panel.output_box.pack_forget
        geometry = self.root.geometry

        def note(what, call):
            def spy(*args, **kwargs):
                order.append(what)
                return call(*args, **kwargs)
            return spy

        self.panel.output_box.pack = note("log", pack)
        self.panel.output_box.pack_forget = note("log", forget)
        self.root.geometry = note("window", geometry)
        try:
            self.panel.toggle_output()
        finally:
            self.panel.output_box.pack = pack
            self.panel.output_box.pack_forget = forget
            self.root.geometry = geometry
        return order

    def test_the_window_grows_before_the_log_is_put_into_it(self):
        # The log sits at the foot and the pages are what expands, so packing
        # it into a window that had not grown yet takes its height out of the
        # pages until the window catches up - the whole thing jumps up and
        # then settles back down. Grow first, then fill; empty first, then
        # shrink.
        #
        # The order is what is checked rather than the heights along the way:
        # with no window manager running, a geometry request takes effect at
        # once and the squeezed frame never gets drawn, so measuring here
        # would pass whichever way round the two calls went.
        self.assertEqual(self._log_order()[:2], ["window", "log"],
                         "the log is packed before the window has grown")
        self.assertTrue(self.panel._output_open)

    def test_the_log_is_taken_out_before_the_window_shrinks(self):
        self.panel._show_output(True)
        for _ in range(4):
            self.root.update()
        self.assertEqual(self._log_order()[:2], ["log", "window"],
                         "the window shrinks before the log has gone")
        self.assertFalse(self.panel._output_open)

    def test_the_log_stays_folded_and_marks_its_own_handle(self):
        # It used to open itself on anything written to it. The bar under the
        # title now says that something is running, so opening as well would
        # be the window rearranging itself to tell you what you can see - it
        # marks its handle instead.
        self.assertFalse(self.panel._output_open)
        self.assertFalse(self.panel.output_box.winfo_ismapped())
        self.assertNotIn("•", self.panel.output_toggle.cget("text"))
        self.panel._write("something happened\n")
        self.root.update_idletasks()
        self.assertFalse(self.panel._output_open, "the log opened by itself")
        self.assertIn("•", self.panel.output_toggle.cget("text"))

    def test_opening_the_log_clears_the_mark(self):
        self.panel._write("something happened\n")
        self.panel._show_output(True)
        self.root.update_idletasks()
        self.assertNotIn("•", self.panel.output_toggle.cget("text"))

    def test_a_command_that_fails_opens_the_log_at_the_reason(self):
        # The one thing worth interrupting you for. A command that worked has
        # nothing to say that the bar under the title did not already.
        self.panel._set_busy(False, 0)
        self.root.update_idletasks()
        self.assertFalse(self.panel._output_open)
        self.panel._set_busy(False, 1)
        self.root.update_idletasks()
        self.assertTrue(self.panel._output_open)

    def test_a_running_command_shows_a_bar_and_deadens_the_buttons(self):
        self.assertFalse(self.panel.progress.canvas.winfo_ismapped())
        self.panel._set_busy(True)
        self.root.update()
        self.assertTrue(self.panel.progress.canvas.winfo_ismapped(),
                        "nothing says a command is running")
        for button in self.panel._busy_buttons:
            self.assertIn("disabled", button.state(), button.cget("text"))
        # The two that only fold something away stay live: watching the log is
        # the one thing worth doing while a command runs.
        self.assertNotIn("disabled", self.panel.output_toggle.state())
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


if __name__ == "__main__":
    unittest.main()
