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
except ImportError:                                         # pragma: no cover
    tk = None


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
        self.panel.notebook.select(self._pages()[0])
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
        self.panel.notebook.select(self._pages()[1])
        self.root.update()
        heights = {}
        for key in ("NOTIFY", "ACHIEVEMENT_COLOR", "NOTIFY_STYLE"):
            _labels, controls = self.panel._rows[key]
            heights[key] = controls[0].winfo_height()
        self.assertEqual(len(set(heights.values())), 1, heights)

    def test_a_switch_has_the_same_room_either_side_of_it(self):
        # A switch that opens a block stands clear of both what is above it
        # and what it governs; the rows it governs sit close together under it.
        # Equal gaps either side are what stop the switch reading as glued to
        # the drop-down beneath it.
        self.panel.notebook.select(self._pages()[1])
        self.root.update()
        rows = ("NOTIFY", "NOTIFY_ACHIEVEMENTS", "ACHIEVEMENT_COLOR",
                "ACHIEVEMENT_STYLE", "NOTIFY_MESSAGES")
        tops = [self.panel._rows[key][1][0].winfo_rooty() for key in rows]
        above, below, tight, closing = (tops[index + 1] - tops[index]
                                        for index in range(4))
        self.assertEqual(above, below,
                         "the switch is not the same distance from the row "
                         "above it as from the one it governs")
        self.assertLess(tight, below,
                        "the rows a switch governs are no closer to each "
                        "other than the switch is to them")
        self.assertEqual(closing, above,
                         "a block does not close the way it opened")

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
        page = self._pages()[1]                 # notifications, the longest
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
        page = self._pages()[2]                 # advanced, the shortest
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
        self.panel.notebook.select(self._pages()[1])
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
        self.panel.notebook.select(self._pages()[0])
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
        self.panel.config["ACHIEVEMENT_COLOR"] = "#FFD700"
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
        """Open one drop-down and hand back the list it dropped."""
        self.panel._open_menu(key)
        self.root.update()
        return self.panel._popup

    def test_a_drop_down_opens_a_list_of_what_it_offers(self):
        popup = self._drop("ACHIEVEMENT_COLOR")
        self.assertIsNotNone(popup)
        self.assertEqual([label for _row, label in popup.rows],
                         [label for label, _value
                          in self.panel._menus["ACHIEVEMENT_COLOR"]])
        popup.close()

    def test_the_row_you_are_on_is_filled_rather_than_ticked(self):
        # What a tk.Menu could not do: it marks the current entry with an
        # indicator and nothing else, where every other list on the desktop
        # fills the row.
        popup = self._drop("ACHIEVEMENT_COLOR")
        chosen = self.panel.vars["ACHIEVEMENT_COLOR"][0].get()
        for row, label in popup.rows:
            wanted = ("secondary_container" if label == chosen
                      else "surface_container")
            self.assertEqual(str(row.cget("background")),
                             self.panel.roles[wanted], label)
        popup.close()

    def test_picking_a_row_takes_the_value_and_closes(self):
        popup = self._drop("ACHIEVEMENT_COLOR")
        row, label = popup.rows[1]
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

    def test_a_click_in_another_window_takes_the_list_down(self):
        # The complaint this is here for, twice over: the list went on
        # floating above whatever was clicked next. A menu holds the screen
        # while it is open, which is what makes the click that dismisses it
        # arrive here at all.
        popup = self._drop("ACHIEVEMENT_COLOR")
        self.assertIs(self.root.grab_current(), popup.window,
                      "the list does not hold the screen")
        other = tk.Toplevel(self.root)
        other.geometry("200x150+900+100")
        for _ in range(4):
            self.root.update()
        other.event_generate("<Button-1>", x=10, y=10, warp=True)
        for _ in range(4):
            self.root.update()
        self.assertIsNone(self.panel._popup, "the list stayed up")
        other.destroy()

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
        for row, label in popup.rows:
            if label == chosen:
                continue                        # the filled one, checked above
            self.assertEqual(str(row.cget("foreground")),
                             self.panel.roles["on_surface"], label)
            self.assertEqual(str(row.cget("background")),
                             self.panel.roles["surface_container"], label)
        popup.close()

    def test_the_arrow_keys_walk_the_list(self):
        popup = self._drop("ACHIEVEMENT_COLOR")
        labels = [label for _row, label in popup.rows]
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
