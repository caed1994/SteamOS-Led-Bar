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

    def test_the_log_folds_away_until_something_writes_to_it(self):
        self.assertFalse(self.panel._output_open)
        self.assertFalse(self.panel.output_box.winfo_ismapped())
        self.panel._write("something happened\n")
        self.root.update_idletasks()
        self.assertTrue(self.panel._output_open)

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
