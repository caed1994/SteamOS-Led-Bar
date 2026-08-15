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

    def test_a_block_is_fenced_off_from_what_is_around_it(self):
        # The stepped-in rows under a switch belong to it; the gap above the
        # next switch is what says so, every row being the same height.
        self.panel.notebook.select(self._pages()[1])
        self.root.update()
        rows = ("NOTIFY_ACHIEVEMENTS", "ACHIEVEMENT_COLOR",
                "ACHIEVEMENT_STYLE", "NOTIFY_MESSAGES")
        tops = [self.panel._rows[key][1][0].winfo_rooty() for key in rows]
        inside = [tops[index + 1] - tops[index] for index in range(2)]
        crossing = tops[3] - tops[2]
        self.assertEqual(len(set(inside)), 1,
                         "the rows inside a block are not evenly spaced")
        self.assertGreater(crossing, inside[0],
                           "a block runs into the next with no gap")

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
                self.root.update()
                for label in self.panel._wrapped:
                    if not label.winfo_ismapped():
                        continue
                    self.assertLessEqual(
                        label.winfo_reqwidth(), label.winfo_width(),
                        "at %d px wide, %r is cut off"
                        % (width, label.cget("text")[:40]))

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
