# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The bar in Desktop Mode: what it shows, and whose it is.

Two halves, and only one of them can be checked here. What a scene looks like
is arithmetic and is settled below to the pixel. Whether *this* machine's Game
Mode session is one the process table gives away is a question about somebody's
Steam Machine, and no test can answer it - which is what
`steamos-led-serial --desktop` is for.
"""

import os
import sys
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_led import config as config_module      # noqa: E402
from steamos_led import desktop, render, service, shim   # noqa: E402


def _proc(root, entries):
    """A pretend /proc: {pid: process name}, plus the clutter a real one has."""
    for pid, name in entries.items():
        os.makedirs(os.path.join(root, str(pid)))
        with open(os.path.join(root, str(pid), "comm"), "w") as handle:
            handle.write(name + "\n")
    for extra in ("cpuinfo", "self", "sys"):
        os.makedirs(os.path.join(root, extra), exist_ok=True)
    return root


class SceneTest(unittest.TestCase):
    """A scene as a snapshot, which is all it is."""

    def test_leaving_it_to_steam_is_no_scene_at_all(self):
        # The default, and it has to stay nothing rather than become a scene
        # that happens to draw what Steam drew: the whole loop hangs off this
        # being None, down to whether the ESP keeps its startup breath.
        self.assertIsNone(desktop.scene_snapshot(
            desktop.SCENE_STEAM, "#ffffff", 128))

    def test_every_scene_but_that_one_draws_something(self):
        for name in desktop.SCENES:
            if name == desktop.SCENE_STEAM:
                continue
            scene = desktop.scene_snapshot(name, "#00ff88", 200)
            self.assertIsNotNone(scene, name)
            self.assertEqual(scene.effect, desktop.SCENE_EFFECTS[name], name)

    def test_a_scene_that_does_not_exist_is_refused(self):
        with self.assertRaises(ValueError):
            desktop.scene_snapshot("disco", "#ffffff", 128)

    def test_the_colour_is_the_one_that_was_asked_for(self):
        scene = desktop.scene_snapshot(desktop.SCENE_COLOR, "#25d366", 128)
        self.assertEqual(scene.base_color(), (0x25, 0xd3, 0x66))

    def test_a_colour_that_is_not_one_is_refused_rather_than_guessed_at(self):
        with self.assertRaises(ValueError):
            desktop.scene_snapshot(desktop.SCENE_COLOR, "greenish", 128)

    def test_the_brightness_cannot_be_set_outside_what_a_byte_holds(self):
        # It reaches the wire as one, so a config file saying 400 would wrap
        # rather than clamp, and 400 would come out dark.
        for asked, wanted in ((-5, 0), (400, 255), (128, 128)):
            scene = desktop.scene_snapshot(desktop.SCENE_BREATH, "#ffffff",
                                           asked)
            self.assertEqual(scene.brightness_scale, wanted)

    def test_it_runs_at_the_rate_a_game_s_effects_run_at(self):
        # The same effect in the same colour must not breathe at one speed in
        # a game and another on the desktop. The delay is what sets that, and
        # the module's own default is the one Steam starts from.
        scene = desktop.scene_snapshot(desktop.SCENE_BREATH, "#ffffff", 128)
        self.assertEqual(scene.delay, render.DELAY_DEFAULT)

    def test_the_animated_ones_say_they_are(self):
        # What the loop reads to decide between the full frame rate and the
        # idle one. A breath drawn four times a second is a stutter.
        for name in (desktop.SCENE_BREATH, desktop.SCENE_PATROL,
                     desktop.SCENE_RAINBOW):
            scene = desktop.scene_snapshot(name, "#ffffff", 128)
            self.assertTrue(scene.is_animated, name)
        for name in (desktop.SCENE_COLOR, desktop.SCENE_OFF):
            scene = desktop.scene_snapshot(name, "#ffffff", 128)
            self.assertFalse(scene.is_animated, name)

    def test_a_scene_reaches_the_strip_as_the_colour_it_names(self):
        """The two ends of it: a config setting, and lit pixels.

        Through the renderer that draws Steam's snapshots, because that is the
        point of a scene being one - an effect that looked different on the
        desktop than in a game would be the bug this shape exists to avoid.
        """
        renderer = render.Renderer(led_count=17)
        scene = desktop.scene_snapshot(desktop.SCENE_COLOR, "#00ff00", 255)
        payload = renderer.render(scene, 0.0)
        self.assertEqual(set(payload[0::3]), {0})       # no red
        self.assertEqual(set(payload[1::3]), {255})     # all green
        self.assertEqual(set(payload[2::3]), {0})       # no blue

    def test_off_is_a_dark_strip_rather_than_a_dim_one(self):
        renderer = render.Renderer(led_count=17)
        scene = desktop.scene_snapshot(desktop.SCENE_OFF, "#ffffff", 255)
        self.assertEqual(set(renderer.render(scene, 0.0)), {0})

    def test_the_brightness_is_a_brightness_and_not_a_switch(self):
        renderer = render.Renderer(led_count=17)
        bright = renderer.render(
            desktop.scene_snapshot(desktop.SCENE_COLOR, "#ffffff", 255), 0.0)
        dim = renderer.render(
            desktop.scene_snapshot(desktop.SCENE_COLOR, "#ffffff", 64), 0.0)
        self.assertGreater(max(bright), max(dim))
        self.assertGreater(max(dim), 0)


class GameModeTest(unittest.TestCase):
    """Reading the process table, which is how the service tells the two apart."""

    def setUp(self):
        import tempfile
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = holder.name

    def test_a_desktop_session_has_no_game_mode_process_in_it(self):
        _proc(self.root, {1: "systemd", 900: "plasmashell", 901: "kwin_wayland",
                          902: "steam", 903: "steamwebhelper"})
        self.assertEqual(desktop.running_game_mode(self.root), "")

    def test_the_compositor_game_mode_runs_under_is_the_giveaway(self):
        # Steam is running in both modes and so is a compositor; gamescope is
        # the one that is only there in Game Mode.
        _proc(self.root, {1: "systemd", 700: "gamescope", 701: "steam"})
        self.assertEqual(desktop.running_game_mode(self.root), "gamescope")

    def test_the_wrapper_that_starts_it_counts_too(self):
        # Spelled "gamescope-session" in one SteamOS release and
        # "gamescope-session-plus" in another, which is why the front of the
        # name is what is matched rather than the whole of it.
        for name in ("gamescope-session", "gamescope-session-plus"):
            root = os.path.join(self.root, name)
            os.makedirs(root)
            _proc(root, {1: "systemd", 700: name})
            self.assertEqual(desktop.running_game_mode(root), name, name)

    def test_something_merely_mentioning_it_is_not_it(self):
        # comm is the executable's name, not a command line, so this is only
        # possible for a program actually called that - but a startswith over
        # names is exactly the check that would take "gamescopereaper" for the
        # session, and the answer would be a bar frozen on Steam's last state.
        _proc(self.root, {1: "systemd", 700: "not-gamescope"})
        self.assertEqual(desktop.running_game_mode(self.root), "")

    def test_a_process_that_exits_while_being_read_is_not_an_error(self):
        # /proc is a directory listing of things that are leaving. Between
        # the listing and the read is a race nobody can win, and losing it
        # must not take the service down.
        os.makedirs(os.path.join(self.root, "404"))     # listed, no comm
        _proc(self.root, {700: "gamescope"})
        self.assertEqual(desktop.running_game_mode(self.root), "gamescope")

    def test_a_machine_with_no_proc_at_all_reads_as_the_desktop(self):
        self.assertEqual(desktop.running_game_mode("/nowhere"), "")


class OwnershipTest(unittest.TestCase):
    """Who has the bar, which is the whole of the switching."""

    def _snapshot(self, seq=9, wrote_at=0.0):
        snapshot = shim.make_snapshot(effect=shim.EFFECT_MANUAL)
        snapshot.seq = seq
        snapshot.monotonic_ns = int(wrote_at * 1e9)
        return snapshot

    def _watch(self, running="", **kwargs):
        """A watch reading a made-up process table.

        `running` is what it finds, or a list of what it finds on each look in
        turn - the last of which it goes on finding, so a test says what
        changes and not how often it is asked.
        """
        answers = [running] if isinstance(running, str) else list(running)
        self.looked = []

        def look():
            self.looked.append(1)
            return answers[min(len(self.looked) - 1, len(answers) - 1)]

        return desktop.Ownership(look=look, **kwargs)

    def test_game_mode_means_the_bar_is_steam_s(self):
        watch = self._watch("gamescope")
        self.assertTrue(watch.steam_has_it(self._snapshot(wrote_at=0.0), 1000.0))

    def test_the_desktop_gets_it_once_steam_has_gone_quiet(self):
        watch = self._watch("")
        self.assertFalse(watch.steam_has_it(self._snapshot(wrote_at=0.0), 1000.0))

    def test_a_write_that_just_happened_still_counts_as_steam_s(self):
        """The handover, which is the one moment both answers are wrong.

        Leaving Game Mode, the compositor and the last LED write stop within a
        moment of each other. Which of the two this notices first is not worth
        depending on, so a scene waits out the grace rather than flickering
        between the two owners at the switch.
        """
        watch = self._watch("", grace=8.0)
        self.assertTrue(watch.steam_has_it(self._snapshot(wrote_at=997.0),
                                           1000.0))
        self.assertFalse(watch.steam_has_it(self._snapshot(wrote_at=980.0),
                                            1000.0))

    def test_a_device_nobody_ever_wrote_to_is_not_a_recent_write(self):
        # The module stamps the time when it loads, so an untouched device
        # carries a timestamp that reads as a write moments ago - and on a
        # machine freshly booted into Desktop Mode that would hold the scene
        # off for the whole grace, for a write that never happened.
        untouched = self._snapshot(seq=shim.UNTOUCHED_SEQ, wrote_at=999.0)
        self.assertIsNone(desktop.steam_wrote_ago(untouched, 1000.0))
        self.assertFalse(self._watch("").steam_has_it(untouched, 1000.0))

    def test_nothing_to_look_at_is_not_a_recent_write_either(self):
        self.assertIsNone(desktop.steam_wrote_ago(None, 1000.0))

    def test_the_process_table_is_not_read_once_a_frame(self):
        # Sixty times a second, times a few hundred processes, for an answer
        # that cannot change faster than somebody can switch sessions.
        watch = self._watch("gamescope", interval=2.0)
        for tick in range(120):             # two seconds at 60 fps
            watch.steam_has_it(self._snapshot(), 1000.0 + tick / 60.0)
        self.assertEqual(len(self.looked), 1)
        watch.steam_has_it(self._snapshot(), 1003.0)
        self.assertEqual(len(self.looked), 2)

    def test_the_switch_is_noticed_rather_than_remembered_forever(self):
        # Leaving Game Mode with nothing else changing: the same stale
        # snapshot, and the bar has to become the desktop's anyway.
        watch = self._watch(["gamescope", ""], interval=1.0)
        old = self._snapshot(wrote_at=0.0)
        self.assertTrue(watch.steam_has_it(old, 1000.0))
        self.assertFalse(watch.steam_has_it(old, 1002.0))

    def test_it_says_so_when_the_answer_changes(self):
        # The line that answers both "why is my scene not showing" and "why is
        # the bar ignoring Steam", which look identical from in front of it.
        watch = self._watch(["", "gamescope"], interval=1.0)
        watch.game_mode(1000.0)
        with self.assertLogs("steamos_led.desktop", "INFO") as caught:
            watch.game_mode(1002.0)
        self.assertIn("Game Mode", caught.output[0])

    def test_it_does_not_say_it_again_every_time_it_looks(self):
        watch = self._watch("gamescope", interval=0.0)
        with self.assertLogs("steamos_led.desktop", "INFO") as caught:
            for tick in range(5):
                watch.game_mode(1000.0 + tick)
        self.assertEqual(len(caught.output), 1)

    def test_the_first_answer_is_said_even_when_it_is_the_dull_one(self):
        """Because the journal is the only way to see any of this.

        There is no terminal in Game Mode, so what the service decided during
        a session can only be read afterwards - and a log that only spoke up
        on a change would leave a service started on the desktop saying
        nothing at all, which reads exactly like a check that never ran.
        """
        with self.assertLogs("steamos_led.desktop", "INFO") as caught:
            self._watch("").game_mode(1000.0)
        self.assertEqual(len(caught.output), 1)
        self.assertIn(desktop.GAME_MODE_MARK, caught.output[0])


class JournalTest(unittest.TestCase):
    """Reading back what the service saw while nobody could watch."""

    SAMPLE = "\n".join((
        "2026-08-21T18:02:11+0200 fractal steamos-led-serial[901]: INFO "
        "steamos-led: reading LED state from /dev/valve-leds-shim",
        "2026-08-21T18:02:11+0200 fractal steamos-led-serial[901]: INFO "
        "steamos-led: " + desktop.ON_THE_DESKTOP,
        "2026-08-21T19:41:03+0200 fractal steamos-led-serial[901]: INFO "
        "steamos-led: " + desktop.IN_GAME_MODE % "gamescope",
        "2026-08-21T21:05:55+0200 fractal steamos-led-serial[901]: INFO "
        "steamos-led: " + desktop.ON_THE_DESKTOP,
    ))

    def test_the_lines_it_wants_are_the_ones_it_wrote(self):
        """The two ends of it, which is the whole reason this can be trusted.

        A report that searched for wording the log had stopped using would
        answer "nothing here" forever - and that reads exactly like a machine
        whose Game Mode was never recognised, which is the bug it is meant to
        find.
        """
        found = desktop.read_journal(self.SAMPLE)
        self.assertEqual(len(found), 3)
        self.assertIn("gamescope", found[1])

    def test_the_rest_of_the_service_s_log_is_not_in_the_way(self):
        self.assertTrue(all("reading LED state" not in line
                            for line in desktop.read_journal(self.SAMPLE)))

    def test_only_the_last_few_are_kept(self):
        many = "\n".join([desktop.ON_THE_DESKTOP] * 50)
        self.assertEqual(len(desktop.read_journal(many)),
                         desktop.JOURNAL_LINES)

    def test_nothing_to_read_is_no_lines_rather_than_a_crash(self):
        for text in ("", None, "some other unit entirely"):
            self.assertEqual(desktop.read_journal(text), [])

    def test_it_asks_for_this_service_s_own_log(self):
        command = desktop.journal_command()
        self.assertEqual(command[0], "journalctl")
        self.assertIn(desktop.JOURNAL_UNIT, command)
        self.assertIn("--no-pager", command, "it must not wait for a key")

    def test_a_journal_that_answers_is_read(self):
        lines, why_not = desktop.journal_ownership(["printf", "%s",
                                                    self.SAMPLE])
        self.assertEqual(why_not, "")
        self.assertEqual(len(lines), 3)

    def test_a_journal_that_refuses_is_a_complaint_and_not_an_empty_list(self):
        # The difference between "look again with sudo" and "this machine's
        # Game Mode was never recognised", which are opposite conclusions.
        lines, why_not = desktop.journal_ownership(["false"])
        self.assertEqual(lines, [])
        self.assertTrue(why_not)

    def test_a_machine_without_journalctl_is_not_an_error(self):
        lines, why_not = desktop.journal_ownership(["/nowhere/journalctl"])
        self.assertEqual(lines, [])
        self.assertIn("journalctl", why_not)


class ConfigurationTest(unittest.TestCase):
    """The settings this adds, and what the service will not accept."""

    def _config(self, **overrides):
        settings = dict(config_module.DEFAULTS)
        settings.update(overrides)
        return settings

    def test_it_leaves_the_bar_to_steam_until_somebody_says_otherwise(self):
        # An update that started animating somebody's bar on the desktop
        # would be this deciding for them.
        self.assertEqual(config_module.DEFAULTS["DESKTOP_SCENE"],
                         desktop.SCENE_STEAM)

    def test_every_offered_scene_is_accepted(self):
        for scene in desktop.SCENES:
            config_module.validate(self._config(DESKTOP_SCENE=scene))

    def test_a_scene_that_does_not_exist_is_refused(self):
        with self.assertRaises(config_module.ConfigError):
            config_module.validate(self._config(DESKTOP_SCENE="disco"))

    def test_a_colour_that_is_not_one_is_refused_at_load(self):
        # Otherwise the mistake surfaces as a service that will not start,
        # hours later, from a line nobody remembers editing.
        with self.assertRaises(config_module.ConfigError) as caught:
            config_module.validate(self._config(DESKTOP_COLOR="pinkish"))
        self.assertIn("DESKTOP_COLOR", str(caught.exception))

    def test_a_brightness_outside_a_byte_is_refused(self):
        for value in (-1, 256):
            with self.assertRaises(config_module.ConfigError):
                config_module.validate(self._config(DESKTOP_BRIGHTNESS=value))

    def test_the_shipped_file_names_every_desktop_setting(self):
        path = os.path.join(HERE, "..", "server", "steamos-led-serial.conf")
        with open(path) as handle:
            text = handle.read()
        for key in ("DESKTOP_SCENE", "DESKTOP_COLOR", "DESKTOP_BRIGHTNESS"):
            self.assertIn("\n%s=" % key, text, key)
        # And names the scenes, because a menu of five words is exactly what
        # somebody editing the file by hand cannot guess.
        for scene in desktop.SCENES:
            self.assertIn(scene, text, scene)

    def test_the_service_builds_the_scene_the_settings_describe(self):
        self.assertIsNone(service.build_scene(self._config()))
        scene = service.build_scene(self._config(DESKTOP_SCENE="patrol",
                                                 DESKTOP_COLOR="#3a76f0",
                                                 DESKTOP_BRIGHTNESS=90))
        self.assertEqual(scene.effect, shim.EFFECT_PATROL)
        self.assertEqual(scene.base_color(), (0x3a, 0x76, 0xf0))
        self.assertEqual(scene.brightness_scale, 90)


if __name__ == "__main__":
    unittest.main()
