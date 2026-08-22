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
        # the module's own default is the one Steam starts from - so speed 1
        # has to land exactly on it.
        scene = desktop.scene_snapshot(desktop.SCENE_BREATH, "#ffffff", 128)
        self.assertEqual(scene.delay, render.DELAY_DEFAULT)
        self.assertEqual(desktop.delay_for(1.0), render.DELAY_DEFAULT)

    def test_asking_for_twice_the_speed_halves_the_cycle(self):
        """Which is what makes the number on the slider mean anything.

        delay is a position, not a duration, and the cycle scales linearly
        with it - so the multiplier is that the other way up. Checked through
        the renderer rather than on the field, because the field is only
        believable if the seconds come out right.
        """
        for speed, wanted in ((2.0, 0.5), (0.5, 2.0), (1.0, 1.0)):
            scene = desktop.scene_snapshot(desktop.SCENE_BREATH, "#ffffff",
                                           128, speed)
            plain = desktop.scene_snapshot(desktop.SCENE_BREATH, "#ffffff", 128)
            options = render.Renderer(led_count=17)
            self.assertAlmostEqual(
                render._cycle(scene, render.BREATH_CYCLE, options.speed_scale),
                render._cycle(plain, render.BREATH_CYCLE, options.speed_scale)
                * wanted, places=6, msg=speed)

    def test_the_speed_cannot_ask_for_a_delay_the_module_has_no_room_for(self):
        # It is one byte with a range the module advertises, so a multiplier
        # outside what that can carry has to land on the end rather than wrap
        # - and 0, which is "as fast as possible", is a real setting.
        for speed in (0.01, 0.4, 1.0, 4.0, 1000.0):
            delay = desktop.delay_for(speed)
            self.assertGreaterEqual(delay, 0, speed)
            self.assertLessEqual(delay, render.DELAY_MAX, speed)

    def test_a_speed_of_nothing_is_the_default_rather_than_a_division(self):
        self.assertEqual(desktop.delay_for(0), render.DELAY_DEFAULT)
        self.assertEqual(desktop.delay_for(-1), render.DELAY_DEFAULT)

    def test_the_slider_s_own_ends_reach_the_module_s(self):
        # A floor below which every multiplier lands on the same slowest step
        # is a slider whose bottom half does nothing.
        self.assertEqual(desktop.delay_for(config_module.DESKTOP_SPEED_FLOOR),
                         render.DELAY_MAX)
        self.assertGreater(
            desktop.delay_for(config_module.DESKTOP_SPEED_FLOOR),
            desktop.delay_for(config_module.DESKTOP_SPEED_FLOOR * 1.5),
            "the slow end of the slider is flat")

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


class SteadyLoad:
    """A load source that always has the same reading to give.

    Steady on purpose: the gauge glides towards each new value, so a source
    that moved would make every frame differ from the last and a test asking
    "did this setting change the bar" could not tell which change it saw.
    """

    def fractions(self):
        return (0.7, 0.3)


class DescribeTest(unittest.TestCase):
    """What --desktop says a scene is doing, which is how a knob is trusted."""

    def _said(self, scene):
        return desktop.describe(scene, "#00b0ff", 90, 2.0)

    def test_every_setting_that_applies_is_named(self):
        said = self._said(desktop.SCENE_BREATH)
        for part in ("#00b0ff", "90", "2"):
            self.assertIn(part, said, said)

    def test_a_rainbow_still_names_its_brightness(self):
        """The report that started this.

        A rainbow makes its own colours, so the line only listed the colour
        and the brightness together - and a report naming neither reads as a
        brightness that is not in play. Which is exactly what came back:
        "the slider does nothing".
        """
        said = self._said(desktop.SCENE_RAINBOW)
        self.assertIn("brightness 90", said)
        self.assertNotIn("#00b0ff", said, "a rainbow has no colour of yours")

    def test_a_still_scene_claims_no_speed(self):
        # The other half of the same rule: naming a setting that does nothing
        # is how somebody comes to move it and see no change.
        said = self._said(desktop.SCENE_COLOR)
        self.assertIn("#00b0ff", said)
        self.assertIn("brightness 90", said)
        self.assertNotIn("speed", said)

    def test_a_dark_bar_claims_none_of_them(self):
        self.assertEqual(self._said(desktop.SCENE_OFF), desktop.SCENE_OFF)

    def test_it_is_named_the_way_the_setting_is(self):
        # Not "manual", which is what the shim calls the effect one colour
        # becomes - the word in the report has to be the word in the file.
        self.assertTrue(self._said(desktop.SCENE_COLOR).startswith(
            desktop.SCENE_COLOR))

    def _named_when_it_matters(self, **renderer_options):
        """Each setting moved, the strip re-rendered, the line checked.

        Not "does the table say so" - that would only be the implementation
        read back. Both ways round matter and for the same reason: a setting
        doing something and going unmentioned is what "the slider does
        nothing" was, and a setting mentioned while doing nothing is how
        somebody comes to move it and see no change.
        """
        shows = renderer_options.get("rainbow_shows", render.SHOWS_RAINBOW)
        renderer = render.Renderer(led_count=17, **renderer_options)

        def lit(scene, color="#00b0ff", brightness=90, speed=2.0):
            return renderer.render(
                desktop.scene_snapshot(scene, color, brightness, speed), 0.4)

        for name in desktop.SCENES:
            if name == desktop.SCENE_STEAM:
                continue
            said = desktop.describe(name, "#00b0ff", 90, 2.0, shows)
            for word, other in (("colour", lit(name, color="#ff0000")),
                                ("brightness", lit(name, brightness=30)),
                                ("speed", lit(name, speed=0.5))):
                self.assertEqual(word in said, other != lit(name),
                                 "%s showing %s: %s" % (name, shows, word))

    def test_a_setting_is_named_exactly_when_it_changes_the_bar(self):
        self._named_when_it_matters()

    def test_the_gauge_in_the_rainbow_slot_answers_to_neither(self):
        """Reported: the two sliders must not reach the CPU and GPU gauge.

        A rainbow scene shows whatever RAINBOW_SHOWS puts in that slot, and
        with the load gauge there the bar is drawing a reading - so brightness
        and speed would change what it says rather than how it looks. The
        report has to stop naming them at the same moment they stop working,
        which is what the walk above checks for every scene at once.
        """
        self._named_when_it_matters(load=SteadyLoad(),
                                    rainbow_shows=render.SHOWS_LOAD)


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

    def test_a_download_on_the_desktop_keeps_the_bar_steam_s(self):
        """Which is why the grace is worth having at all.

        Steam writes the progress bar as it fills, and every write pushes the
        grace out again - so the bar stays Steam's for as long as the download
        is filling it. Nothing set out to do that; it fell out of the grace,
        and it is the half of this behaviour worth keeping.
        """
        watch = self._watch("", grace=2.0)
        for when in (10.0, 11.0, 12.0):
            filling = self._snapshot(wrote_at=when)
            filling.pixels = [(0, 120, 255, 255)] * 17
            self.assertTrue(watch.steam_has_it(filling, when + 0.5), when)

    def test_steam_putting_back_what_it_found_is_steam_letting_go(self):
        """Reported: a few seconds of the Game Mode effect after a download.

        When the download finishes Steam restores the effect that was set in
        Game Mode - so the last thing it writes is the state it was resting
        at before any of it started, and the grace went on showing that for
        its full length. A strange few seconds: an effect nobody asked for,
        between the download and the desktop's own.
        """
        watch = self._watch("", grace=2.0)
        resting = self._snapshot(wrote_at=-60.0)

        # At rest, and the scene learns what Steam is resting at.
        self.assertFalse(watch.steam_has_it(resting, 0.0))
        self.assertIsNotNone(watch.at_rest)

        # A download takes the bar.
        filling = self._snapshot(wrote_at=10.0)
        filling.pixels = [(0, 120, 255, 255)] * 17
        self.assertTrue(watch.steam_has_it(filling, 10.1))

        # And putting the effect back hands it straight over, rather than
        # showing that effect until the grace runs out.
        restored = self._snapshot(wrote_at=20.0)
        self.assertFalse(watch.steam_has_it(restored, 20.1),
                         "the Game Mode effect held the bar after a download")

    def test_a_write_that_is_not_the_resting_state_still_takes_the_bar(self):
        # The control. An early-out that let go of anything at all would be a
        # scene that covered the download it is meant to give way to.
        watch = self._watch("", grace=2.0)
        self.assertFalse(watch.steam_has_it(self._snapshot(wrote_at=-60.0), 0.0))
        moved = self._snapshot(wrote_at=10.0)
        moved.pixels = [(255, 0, 0, 255)] * 17
        self.assertTrue(watch.steam_has_it(moved, 10.1))

    def test_it_only_learns_the_resting_state_while_the_scene_has_the_bar(self):
        # What is on the shim while Steam is driving is not what Steam will
        # rest at - it is the middle of a download. Learning it there would
        # make the very next progress write look like a restore.
        watch = self._watch("", grace=2.0)
        watch.steam_has_it(self._snapshot(wrote_at=-60.0), 0.0)
        resting = watch.at_rest
        filling = self._snapshot(wrote_at=10.0)
        filling.pixels = [(0, 120, 255, 255)] * 17
        watch.steam_has_it(filling, 10.1)
        self.assertEqual(watch.at_rest, resting, "it learned mid-download")

    def test_a_service_that_started_mid_download_still_recovers(self):
        # Nothing was learned before Steam took the bar, so the restore is
        # not recognised and the grace runs out the way it always did. Worse
        # by two seconds, once, and right again from then on.
        watch = self._watch("", grace=2.0)
        self.assertTrue(watch.steam_has_it(self._snapshot(wrote_at=10.0), 10.1))
        self.assertIsNone(watch.at_rest)
        self.assertFalse(watch.steam_has_it(self._snapshot(wrote_at=10.0), 13.0))
        self.assertIsNotNone(watch.at_rest)

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


class ReportedDownloadTest(unittest.TestCase):
    """The whole sequence, walked once, in the order it was reported in.

    Kept as one test rather than split up because what was wrong was not any
    single answer - each of them was defensible - but the shape of the run:
    scene, download, and then a few seconds of something nobody asked for.
    """

    class Device:
        """The shim: it holds what Steam last wrote, and when."""

        def __init__(self):
            self.state, self.seq, self.written = None, shim.UNTOUCHED_SEQ, 0.0

        def steam_writes(self, snapshot, when):
            self.state, self.written = snapshot, when
            self.seq += 1

        def read(self):
            if self.state is None:
                return None
            self.state.seq = self.seq
            self.state.monotonic_ns = int(self.written * 1e9)
            return self.state

    def _download(self, filled):
        snapshot = shim.make_snapshot(shim.EFFECT_MANUAL, (0, 120, 255))
        snapshot.pixels = [(0, 120, 255, 255) if led < filled else (0, 0, 0, 0)
                           for led in range(17)]
        return snapshot

    def test_the_bar_goes_scene_download_scene_and_nothing_between(self):
        device = self.Device()
        watch = desktop.Ownership(look=lambda: "")      # Desktop Mode
        resting = shim.make_snapshot(shim.EFFECT_RAINBOW)
        device.steam_writes(resting, -60.0)             # set in Game Mode

        shown = []
        def at(when):
            shown.append("Steam" if watch.steam_has_it(device.read(), when)
                         else "scene")

        at(0.0)
        for step, when in enumerate((10.0, 11.0, 12.0, 13.0)):
            device.steam_writes(self._download(4 * step + 4), when)
            at(when + 0.5)
        device.steam_writes(resting, 14.0)              # the download is done
        at(14.1)
        at(20.0)

        self.assertEqual(shown, ["scene"] + ["Steam"] * 4 + ["scene", "scene"],
                         "the Game Mode effect showed between the two")

    def test_the_tail_is_gone_however_long_the_grace_is(self):
        """Which is what makes the two changes independent.

        The grace is now short, and short is a guess about how often Steam
        writes while a download runs - something nothing here can know. If
        that guess is wrong the grace goes back up, and this is what says
        the reported fault does not come back with it.
        """
        for grace in (2.0, 8.0, 60.0):
            device = self.Device()
            watch = desktop.Ownership(look=lambda: "", grace=grace)
            resting = shim.make_snapshot(shim.EFFECT_RAINBOW)
            device.steam_writes(resting, -600.0)
            self.assertFalse(watch.steam_has_it(device.read(), 0.0), grace)
            device.steam_writes(self._download(9), 10.0)
            self.assertTrue(watch.steam_has_it(device.read(), 10.1), grace)
            device.steam_writes(resting, 14.0)
            self.assertFalse(watch.steam_has_it(device.read(), 14.1), grace)


class DownloadGapTest(unittest.TestCase):
    """The gap between Steam's writes, which the grace was shorter than.

    Reported from a real machine, downloading a 100 GB game over a fast line:
    about two seconds of the download's bar, then about five of the desktop's
    own effect, then the download's again, all the way through. Steam writes
    the progress bar once per step it can show, and the grace ran out in every
    gap between two of them - so the bar changed hands a dozen times a minute
    for a quarter of an hour.

    The gap is not a constant, either. The bar has the same number of steps
    whatever the connection, so it is the download's length divided by that -
    seven seconds here, a minute on a line a tenth as fast.
    """

    Device = ReportedDownloadTest.Device

    def _download(self, filled):
        snapshot = shim.make_snapshot(shim.EFFECT_MANUAL, (0, 120, 255))
        snapshot.pixels = [(0, 120, 255, 255) if led < filled else (0, 0, 0, 0)
                           for led in range(17)]
        return snapshot

    def _run(self, gap, steps=8, look=""):
        """A download whose bar moves every `gap` seconds; who had the bar."""
        device = self.Device()
        watch = desktop.Ownership(look=lambda: look)
        resting = shim.make_snapshot(shim.EFFECT_RAINBOW)
        device.steam_writes(resting, -600.0)

        now, shown = 0.0, []
        watch.steam_has_it(device.read(), now)          # a desktop period
        for step in range(1, steps + 1):
            device.steam_writes(self._download(2 * step), now)
            for _ in range(int(gap * 4)):
                shown.append(watch.steam_has_it(device.read(), now))
                now += 0.25
        device.steam_writes(resting, now)               # the download is done
        after = [watch.steam_has_it(device.read(), now + tick)
                 for tick in (0.1, 1.0, 5.0)]
        return shown, after

    def test_the_desktop_does_not_break_through_between_two_writes(self):
        shown, _after = self._run(gap=7.0)
        self.assertTrue(all(shown),
                        "the desktop effect showed %d times during the "
                        "download" % shown.count(False))

    def test_a_slower_line_only_makes_the_gaps_longer(self):
        # Which is why the patience is not a slightly larger guess at how
        # often Steam writes: on a line a tenth as fast it writes a tenth as
        # often, and any fixed guess of that kind is wrong for somebody.
        shown, _after = self._run(gap=60.0)
        self.assertTrue(all(shown), shown.count(False))

    def test_the_download_still_ends_the_moment_steam_puts_it_back(self):
        # The patience is only a backstop. What actually ends a download is
        # Steam restoring what it was resting at, which is recognised exactly
        # - so a long patience costs nothing at the end of one.
        _shown, after = self._run(gap=7.0)
        self.assertEqual(after, [False, False, False])

    def test_one_stray_write_cannot_hold_the_bar_for_ever(self):
        # A write that is not the resting state and is never followed up. The
        # patience is what bounds it, and it is the only thing that does.
        device = self.Device()
        watch = desktop.Ownership(look=lambda: "", busy=30.0)
        device.steam_writes(shim.make_snapshot(shim.EFFECT_RAINBOW), -600.0)
        self.assertFalse(watch.steam_has_it(device.read(), 0.0))
        device.steam_writes(self._download(9), 10.0)
        self.assertTrue(watch.steam_has_it(device.read(), 39.0))
        self.assertFalse(watch.steam_has_it(device.read(), 41.0))

    def test_game_mode_does_not_leave_a_resting_state_behind_it(self):
        """Or the session after it would never get the bar back.

        What Steam rests at is learned on the desktop, and a Game Mode session
        can leave the bar showing something else entirely. Carried across, the
        old one would read as Steam showing something of its own for the whole
        of the patience - which is the handover taking two minutes instead of
        two seconds.
        """
        device = self.Device()
        watch = desktop.Ownership(look=lambda: sessions[0], interval=0.0)
        sessions = [""]

        device.steam_writes(shim.make_snapshot(shim.EFFECT_RAINBOW), -600.0)
        self.assertFalse(watch.steam_has_it(device.read(), 0.0))
        self.assertIsNotNone(watch.at_rest, "it should have learned one")

        sessions[0] = "gamescope"                       # into Game Mode
        device.steam_writes(shim.make_snapshot(shim.EFFECT_PATROL), 100.0)
        self.assertTrue(watch.steam_has_it(device.read(), 100.5))

        sessions[0] = ""                                # and back out of it
        self.assertTrue(watch.steam_has_it(device.read(), 101.0),
                        "the grace covers the handover")
        self.assertFalse(watch.steam_has_it(device.read(), 103.0),
                         "the scene waited out the whole patience")


class DumpColumnTest(unittest.TestCase):
    """How often Steam writes, which is the one thing --dump could not say.

    It matters because the grace has to outlast the gap between two of a
    download's writes, and nothing in this project can work that out from the
    outside - it is Steam's business how often it redraws a progress bar.
    """

    def _written(self, seq, at_seconds):
        snapshot = shim.make_snapshot(shim.EFFECT_MANUAL)
        snapshot.seq = seq
        snapshot.monotonic_ns = int(at_seconds * 1e9)
        return snapshot

    def test_the_gap_is_between_steam_s_writes_and_not_between_the_readings(self):
        # The loop can be a second behind a write, so timing it here would
        # measure this program rather than Steam.
        first = self._written(4, 100.0)
        second = self._written(5, 102.5)
        self.assertIn("+2.50s", service.dump_line(second, first.monotonic_ns,
                                                  first.seq))

    def test_the_first_line_has_nothing_to_be_a_gap_from(self):
        said = service.dump_line(self._written(4, 100.0), None, None)
        self.assertNotIn("+", said)
        self.assertIn("manual", said)

    def test_a_write_that_went_by_unseen_is_said_so(self):
        """Because a gap measured across one is not the gap anybody wanted.

        The shim hands out the current state rather than a queue, so two
        writes inside one wait come back as one - and the counter is the only
        thing that gives that away.
        """
        first = self._written(4, 100.0)
        said = service.dump_line(self._written(7, 100.2), first.monotonic_ns,
                                 first.seq)
        self.assertIn("2 write(s) not seen", said)

    def test_nothing_is_said_when_none_went_by(self):
        first = self._written(4, 100.0)
        self.assertNotIn("not seen",
                         service.dump_line(self._written(5, 100.2),
                                           first.monotonic_ns, first.seq))


class RecordedFadeTest(unittest.TestCase):
    """The fade Steam puts around a download, recorded on a Steam Machine.

    Reported as the Game Mode effect flashing for under a second at each end
    of a download. What --dump showed is that Steam dims its own effect to
    nothing before the progress bar appears and brings it back up afterwards,
    a step every thirty milliseconds - and every step of both fades differs
    from the state at rest in the brightness and in nothing else.

    The values below are that recording. Kept because it is a fact about
    somebody's machine rather than something reasoned out here, and the whole
    fix rests on it.
    """

    BLUE = (1, 90, 255, 255)
    DARK = (0, 0, 0, 255)

    def _state(self, effect, brightness, pixels):
        snapshot = shim.make_snapshot(effect, brightness=brightness, delay=10)
        snapshot.breath_offset, snapshot.breath_level = 4, 32
        snapshot.patrol_num, snapshot.color_shift = 3, 5
        snapshot.pixels = list(pixels)
        return snapshot

    def _recorded(self):
        """(what Steam wrote, seconds since its previous write)."""
        rest = [self.BLUE] * 17
        bar = [self.DARK] * 5 + [(0, 45, 127, 255)] + [self.BLUE] * 11
        rainbow, manual = shim.EFFECT_RAINBOW, shim.EFFECT_MANUAL
        return [(self._state(rainbow, 55, rest), 0.0)] + [
            (self._state(rainbow, level, rest), 0.03)
            for level in (43, 33, 23, 16, 10, 5, 2, 0)] + [
            (self._state(manual, 0, rest), 0.39),
            (self._state(manual, 55, rest), 0.0),
            (self._state(manual, 55, bar), 0.0),
            (self._state(manual, 55, bar), 0.72)] + [
            (self._state(manual, level, bar), 0.03)
            for level in (43, 33, 23, 16, 10, 5, 2, 0)] + [
            (self._state(manual, 0, rest), 0.10),
            (self._state(rainbow, 0, rest), 0.02)] + [
            (self._state(rainbow, level, rest), 0.03)
            for level in (1, 4, 8, 13, 21, 29, 40, 51, 55)]

    def _walk(self):
        """Replay it, and say what the bar showed at every step."""
        watch = desktop.Ownership(look=lambda: "")
        held, clock, seq, shown = [None], 0.0, 100, []

        def look(when):
            if held[0] is not None:
                shown.append((held[0], watch.steam_has_it(held[0], when)))

        for snapshot, gap in self._recorded():
            # The service looks between two of Steam's writes, which is what
            # lets the scene settle in and learn what Steam is resting at.
            for step in range(1, int(gap / 0.25) + 1):
                look(clock + step * 0.25)
            clock += gap
            seq += 1
            snapshot.seq, snapshot.monotonic_ns = seq, int(clock * 1e9)
            held[0] = snapshot
            look(clock + 0.01)
        return shown

    def test_the_game_mode_effect_never_reaches_the_bar(self):
        """The reported fault, as one sentence about the whole run.

        Not "the fade-out is the scene's and the fade-in is too", which would
        be the fix read back. Whatever else happens, the rainbow must not be
        what the bar is showing at any point - it is the desktop's bar, and
        the download is the only thing Steam has to say on it.
        """
        settled = False
        for snapshot, steams in self._walk():
            if not steams:
                settled = True          # the scene has had the bar at least once
                continue
            if not settled:
                continue                # before it ever settled, see the tests above
            self.assertNotEqual(snapshot.effect, shim.EFFECT_RAINBOW,
                                "the Game Mode effect flashed at brightness %d"
                                % snapshot.brightness_scale)

    def test_the_download_itself_still_gets_the_bar(self):
        # The control. A scene that simply never gave way would pass the test
        # above and lose the thing that test is protecting.
        lit = [steams for snapshot, steams in self._walk()
               if snapshot.effect == shim.EFFECT_MANUAL
               and snapshot.brightness_scale > 0]
        self.assertTrue(lit and all(lit), "the progress bar did not show")

    def test_half_a_lit_rainbow_is_the_same_rainbow(self):
        # The whole of it in one line: what makes two states the same thing
        # Steam is resting at, and what does not.
        rest = [self.BLUE] * 17
        bright = self._state(shim.EFFECT_RAINBOW, 55, rest)
        dimmed = self._state(shim.EFFECT_RAINBOW, 5, rest)
        other = self._state(shim.EFFECT_BREATH, 55, rest)
        self.assertEqual(desktop.resting_key(bright),
                         desktop.resting_key(dimmed))
        self.assertNotEqual(desktop.resting_key(bright),
                            desktop.resting_key(other))
        # And the brightness is still part of "has anything changed at all",
        # which is a different question and one the loop asks every turn.
        self.assertNotEqual(bright.key(), dimmed.key())


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

    def test_a_speed_outside_what_the_delay_field_carries_is_refused(self):
        for value in (0, -1.0, 0.39, 4.1):
            with self.assertRaises(config_module.ConfigError, msg=value):
                config_module.validate(self._config(DESKTOP_SPEED=value))

    def test_the_speed_the_slider_can_reach_is_accepted(self):
        for value in (config_module.DESKTOP_SPEED_FLOOR, 1.0,
                      config_module.DESKTOP_SPEED_CEILING):
            config_module.validate(self._config(DESKTOP_SPEED=value))

    def test_the_shipped_file_names_every_desktop_setting(self):
        path = os.path.join(HERE, "..", "server", "steamos-led-serial.conf")
        with open(path) as handle:
            text = handle.read()
        for key in ("DESKTOP_SCENE", "DESKTOP_COLOR", "DESKTOP_BRIGHTNESS",
                    "DESKTOP_SPEED"):
            self.assertIn("\n%s=" % key, text, key)
        # And names the scenes, because a menu of five words is exactly what
        # somebody editing the file by hand cannot guess.
        for scene in desktop.SCENES:
            self.assertIn(scene, text, scene)

    def test_the_service_builds_the_scene_the_settings_describe(self):
        self.assertIsNone(service.build_scene(self._config()))
        scene = service.build_scene(self._config(DESKTOP_SCENE="patrol",
                                                 DESKTOP_COLOR="#3a76f0",
                                                 DESKTOP_BRIGHTNESS=90,
                                                 DESKTOP_SPEED=2.0))
        self.assertEqual(scene.effect, shim.EFFECT_PATROL)
        self.assertEqual(scene.base_color(), (0x3a, 0x76, 0xf0))
        self.assertEqual(scene.brightness_scale, 90)
        self.assertEqual(scene.delay, desktop.delay_for(2.0))

    def test_every_setting_on_the_page_reaches_the_scene(self):
        """The check that would have caught a knob wired to nothing.

        Three of these are read in one place each, and a service that read
        three of the four would look exactly like one where a slider does
        nothing - which is a thing to hear about from a test rather than from
        somebody moving it.
        """
        plain = service.build_scene(self._config(DESKTOP_SCENE="breath"))
        for key, value, field in (("DESKTOP_COLOR", "#3a76f0", "pixels"),
                                  ("DESKTOP_BRIGHTNESS", 90,
                                   "brightness_scale"),
                                  ("DESKTOP_SPEED", 2.0, "delay")):
            moved = service.build_scene(
                self._config(DESKTOP_SCENE="breath", **{key: value}))
            self.assertNotEqual(getattr(moved, field), getattr(plain, field),
                                "%s changes nothing" % key)


if __name__ == "__main__":
    unittest.main()
