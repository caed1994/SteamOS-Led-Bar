# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The bar in Desktop Mode: what it shows, and whose it is.

There are two halves, and the tests here examine one of them. The look of a
scene is arithmetic, and the tests below prove it to the pixel. The second
half is the question whether the process table of *this* machine gives away a
Game Mode session. That question is about the Steam Machine of a user, and no
test can answer it. `steamos-utility-center --desktop` answers it.
"""

import os
import sys
import tempfile
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_utility_center import config as config_module      # noqa: E402
from steamos_utility_center import desktop, render, service, shim   # noqa: E402


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
        # One effect in one colour must breathe at the same rate in a game and
        # on the desktop. The delay gives that rate. Steam starts from the
        # default of the module, so speed 1 must give exactly that value.
        scene = desktop.scene_snapshot(desktop.SCENE_BREATH, "#ffffff", 128)
        self.assertEqual(scene.delay, render.DELAY_DEFAULT)
        self.assertEqual(desktop.delay_for(1.0), render.DELAY_DEFAULT)

    def test_asking_for_twice_the_speed_halves_the_cycle(self):
        """Which is what makes the number on the slider mean anything.

        delay is a position and not a duration, and the length of the cycle is
        linear in it. So the multiplier is the inverse of the delay. This test
        uses the renderer and not the field, because the field is only correct
        when the seconds are correct.
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

        This uses the renderer that draws the snapshots of Steam, and that is
        the purpose of a scene. An effect with a different look on the desktop
        and in a game is the fault that this design prevents.
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


class SteadyTemperature:
    """A sensor that always reads the same, for the same reason as above."""

    def celsius(self):
        return 61.0


class ScenesOfTheirOwnTest(unittest.TestCase):
    """A user asked for desktop effects that do not use the rainbow slot.

    Game Mode has one slot for these four effects, because the LED menu is a
    part of the Steam client and nothing can add an entry to it. Desktop Mode
    does not use that menu, so the four are scenes there. Two facts must stay
    true: each scene draws itself, and no scene reads RAINBOW_SHOWS for its own
    identity.
    """

    def _renderer(self, **options):
        options.setdefault("rainbow_shows", render.SHOWS_RAINBOW)
        return render.Renderer(led_count=17, temperature=SteadyTemperature(),
                               load=SteadyLoad(), **options)

    def _lit(self, renderer, scene, elapsed=0.4):
        return renderer.render(
            desktop.scene_snapshot(scene, "#00b0ff", 128), elapsed,
            desktop.scene_shows(scene))

    def test_each_of_the_four_draws_a_different_strip(self):
        # The whole of the feature in one line: four scenes, four pictures.
        # Before this they were one scene showing whatever the slot held, so
        # picking a different one of them changed nothing at all.
        renderer = self._renderer()
        drawn = {scene: self._lit(renderer, scene)
                 for scene in (desktop.SCENE_RAINBOW, desktop.SCENE_FIRE,
                               desktop.SCENE_AURORA,
                               desktop.SCENE_TEMPERATURE, desktop.SCENE_LOAD)}
        self.assertEqual(len(set(drawn.values())), len(drawn), sorted(drawn))

    def test_the_scene_wins_over_the_slot(self):
        """A desktop scene draws itself whatever Game Mode's slot holds.

        Both ways round, because either alone would pass on an accident: a
        scene that ignored the setting entirely and one that read it would
        agree wherever the two happen to name the same effect.
        """
        for shows in render.RAINBOW_CHOICES:
            renderer = self._renderer(rainbow_shows=shows)
            for scene in (desktop.SCENE_RAINBOW, desktop.SCENE_FIRE,
                          desktop.SCENE_AURORA, desktop.SCENE_TEMPERATURE,
                          desktop.SCENE_LOAD):
                self.assertEqual(self._lit(renderer, scene),
                                 self._lit(self._renderer(), scene),
                                 "%s under a %s slot" % (scene, shows))

    def test_the_rainbow_scene_is_steams_rainbow(self):
        """And no other effect. That was the one correct part before.

        DESKTOP_SCENE=rainbow with RAINBOW_SHOWS=fire drew fire on the desktop.
        Fire now has a scene of its own, so the rainbow means the rainbow. The
        service reports that at the start. See warn_scene_split.
        """
        renderer = self._renderer(rainbow_shows=render.SHOWS_FIRE)
        self.assertEqual(self._lit(renderer, desktop.SCENE_RAINBOW),
                         renderer.render(shim.make_snapshot(
                             shim.EFFECT_RAINBOW, color=(0, 0xb0, 0xff),
                             brightness=128,
                             delay=desktop.delay_for(1.0)), 0.4,
                             render.SHOWS_RAINBOW))
        self.assertNotEqual(self._lit(renderer, desktop.SCENE_RAINBOW),
                            self._lit(renderer, desktop.SCENE_FIRE))

    def test_game_mode_still_follows_the_slot(self):
        """Nothing about the desktop reaches a snapshot Steam wrote.

        The slot is the whole reason RAINBOW_SHOWS exists, and a scene passing
        its own answer down must not become the answer for everything: what
        Steam calls a rainbow is still whatever the slot holds.
        """
        steam = shim.make_snapshot(shim.EFFECT_RAINBOW, brightness=128)
        plain = self._renderer().render(steam, 0.4)
        for shows in (render.SHOWS_FIRE, render.SHOWS_AURORA,
                      render.SHOWS_TEMPERATURE, render.SHOWS_LOAD):
            self.assertNotEqual(
                self._renderer(rainbow_shows=shows).render(steam, 0.4),
                plain, shows)

    def test_the_frame_rate_follows_the_scene_too(self):
        """is_animated is asked the same question, and has to hear the same.

        Without that agreement, the loop draws the temperature scene sixty times
        each second and sends the same bytes. It also moves the load gauge four
        times each second, and that gauge must glide.
        """
        renderer = self._renderer(rainbow_shows=render.SHOWS_TEMPERATURE)
        for scene, moving in ((desktop.SCENE_TEMPERATURE, False),
                              (desktop.SCENE_LOAD, True),
                              (desktop.SCENE_FIRE, True),
                              (desktop.SCENE_RAINBOW, True)):
            snapshot = desktop.scene_snapshot(scene, "#00b0ff", 128)
            self.assertEqual(
                renderer.is_animated(snapshot, desktop.scene_shows(scene)),
                moving, scene)

    def test_a_scene_that_draws_no_slot_asks_for_none(self):
        # None rather than a name, so the renderer is left on its own setting
        # for every snapshot that was never going to substitute anyway.
        for scene in (desktop.SCENE_STEAM, desktop.SCENE_OFF,
                      desktop.SCENE_COLOR, desktop.SCENE_BREATH,
                      desktop.SCENE_PATROL):
            self.assertIsNone(desktop.scene_shows(scene), scene)

    def test_every_scene_that_draws_one_names_an_effect_the_renderer_has(self):
        for scene, shows in desktop.SCENE_SHOWS.items():
            self.assertIn(scene, desktop.SCENES, scene)
            self.assertIn(shows, render.RAINBOW_CHOICES, shows)
            self.assertEqual(desktop.SCENE_EFFECTS[scene],
                             shim.EFFECT_RAINBOW, scene)

    def _warned(self, **overrides):
        settings = dict(config_module.DEFAULTS, **overrides)
        with self.assertLogs(service.LOG, level="INFO") as caught:
            service.warn_scene_split(settings)
            # assertLogs fails an empty block, so there is always one line.
            service.LOG.info("end")
        return [line for line in caught.output if "end" not in line]

    def test_a_file_that_used_to_mean_something_else_is_told_so(self):
        """The one config file this change reads differently than it did.

        DESKTOP_SCENE=rainbow with RAINBOW_SHOWS=fire drew fire on the desktop
        before, and it draws the rainbow now. A silent migration in each
        direction is a guess about the wish of the user. So the service gives
        the new meaning and names the setting for the old behaviour.
        """
        said = self._warned(DESKTOP_SCENE="rainbow", RAINBOW_SHOWS="fire")
        self.assertEqual(len(said), 1, said)
        self.assertIn("DESKTOP_SCENE=fire", said[0])

    def test_and_a_file_that_did_not_is_left_alone(self):
        # Every other pairing means today what it meant yesterday, and a
        # startup line about a setting nobody has to change is noise.
        for scene, shows in (("rainbow", "rainbow"), ("fire", "aurora"),
                             ("breath", "load"), ("steam", "temperature")):
            self.assertEqual(
                self._warned(DESKTOP_SCENE=scene, RAINBOW_SHOWS=shows), [],
                "%s / %s" % (scene, shows))


class SourcesForTheSceneTest(unittest.TestCase):
    """Reported: DESKTOP_SCENE=load showed the rainbow.

    This is the second half of the class above, and that half was absent. Those
    tests give the renderer a sensor and a load source directly. So they prove
    that the scenes draw after another step builds them, and no step did that.
    The two builders read RAINBOW_SHOWS only, and that is the setting of the
    Steam menu. With the slot at the rainbow, the renderer had no counters and
    the load gauge had no values. _substitute then did its normal work for a
    gauge with no values: it gave the slot back to the rainbow of Steam.

    So the desktop showed a rainbow after a user selected the load gauge, and
    the rainbow is the effect that the user replaced. The same two lines caused
    this for both gauges. The fire and the aurora are arithmetic, and they never
    had this fault.
    """

    def _config(self, **overrides):
        return dict(config_module.DEFAULTS, **overrides)

    def test_the_desktop_scene_gets_the_counters_it_asks_for(self):
        # The report, in one line. RAINBOW_SHOWS keeps its default, and each
        # user with no change to it has that value.
        settings = self._config(DESKTOP_SCENE=desktop.SCENE_LOAD)
        self.assertEqual(settings["RAINBOW_SHOWS"], render.SHOWS_RAINBOW)
        self.assertIsNotNone(service.build_load_source(settings))

    def test_and_the_sensor_the_temperature_scene_asks_for(self):
        # The same two lines, so the same bug: DESKTOP_SCENE=temperature with
        # the slot elsewhere had no sensor either.
        settings = self._config(DESKTOP_SCENE=desktop.SCENE_TEMPERATURE)
        self.assertIsNotNone(service.build_temperature_source(settings))

    def test_game_mode_still_gets_them_on_its_own(self):
        # The half that always worked, which the fix must not trade away.
        settings = self._config(RAINBOW_SHOWS=render.SHOWS_LOAD,
                                DESKTOP_SCENE=desktop.SCENE_STEAM)
        self.assertIsNotNone(service.build_load_source(settings))
        settings = self._config(RAINBOW_SHOWS=render.SHOWS_TEMPERATURE,
                                DESKTOP_SCENE=desktop.SCENE_STEAM)
        self.assertIsNotNone(service.build_temperature_source(settings))

    def test_nothing_is_read_that_nothing_shows(self):
        """A machine showing neither gauge opens neither.

        This is not a style rule. The load source resolves a sysfs path and reads
        /proc/stat four times each second, for the complete session. The sensor is
        a file, and the settings can give each path for it. Neither is work for an
        effect that no user selected.
        """
        for scene in (desktop.SCENE_STEAM, desktop.SCENE_COLOR,
                      desktop.SCENE_FIRE, desktop.SCENE_RAINBOW):
            settings = self._config(DESKTOP_SCENE=scene)
            self.assertIsNone(service.build_load_source(settings), scene)
            self.assertIsNone(service.build_temperature_source(settings),
                              scene)

    def _drawn(self, scene, **overrides):
        """The frame the service's own renderer draws for a scene.

        Built by build_renderer rather than by hand: what was wrong was the
        wiring between the settings and the renderer, and a renderer handed
        its sources directly cannot show it.
        """
        settings = self._config(DESKTOP_SCENE=scene, **overrides)
        with unittest.mock.patch.object(service.load, "LoadSource",
                                        SteadyLoad), \
                unittest.mock.patch.object(service.temperature,
                                           "TemperatureSource",
                                           lambda path=None:
                                           SteadyTemperature()):
            renderer = service.build_renderer(settings)
        snapshot = service.build_scene(settings)
        renderer.reading(fresh=True)
        return renderer.render(snapshot, 0.4, desktop.scene_shows(scene))

    def test_the_load_scene_draws_the_gauge_and_not_a_rainbow(self):
        # End to end, through the wiring that was broken: settings in, bytes
        # out, and the bytes are the gauge's.
        self.assertNotEqual(self._drawn(desktop.SCENE_LOAD),
                            self._drawn(desktop.SCENE_RAINBOW),
                            "the load scene is still drawing the rainbow")

    def test_the_temperature_scene_does_too(self):
        self.assertNotEqual(self._drawn(desktop.SCENE_TEMPERATURE),
                            self._drawn(desktop.SCENE_RAINBOW),
                            "the temperature scene is still drawing the "
                            "rainbow")

    def test_every_pairing_of_the_two_settings_draws_the_scene(self):
        """All of them, because which pairing broke was not obvious.

        A user reported it with RAINBOW_SHOWS=temperature and DESKTOP_SCENE=load.
        That is one pair of ten, and it looked like a fault of those two effects
        together. It was not.

        Each pair was broken *except* the two pairs where both settings name the
        same effect. A person who tests this selects those two pairs first. So the
        fault covered the complete grid without its diagonal. A test of one or two
        cases can use that diagonal by accident. This test reads the complete grid.
        """
        for shows in render.RAINBOW_CHOICES:
            for scene in (desktop.SCENE_LOAD, desktop.SCENE_TEMPERATURE):
                self.assertNotEqual(
                    self._drawn(scene, RAINBOW_SHOWS=shows),
                    self._drawn(desktop.SCENE_RAINBOW, RAINBOW_SHOWS=shows),
                    "DESKTOP_SCENE=%s with RAINBOW_SHOWS=%s draws the rainbow"
                    % (scene, shows))

    def test_the_gauges_look_the_same_whichever_mode_asked_for_them(self):
        """And the fix did not make the desktop's gauge a different gauge.

        The setting of the slot must not reach a scene. See
        test_the_scene_wins_over_the_slot above, which proves that for the draw
        step. This test proves it for the build step: each question builds the
        same sources.
        """
        for scene, shows in ((desktop.SCENE_LOAD, render.SHOWS_LOAD),
                             (desktop.SCENE_TEMPERATURE,
                              render.SHOWS_TEMPERATURE)):
            self.assertEqual(self._drawn(scene),
                             self._drawn(scene, RAINBOW_SHOWS=shows), scene)

    def test_the_diagnostics_name_the_mode_that_is_showing_it(self):
        """--load and --temperature told half the truth, which misdirects.

        "Set RAINBOW_SHOWS=load to put this there" is the answer for Game Mode.
        A user who reads it can also ask because the desktop shows the wrong
        effect. That answer then puts the gauge in the other mode.
        """
        # A line each, and each says whether that mode has the gauge or what
        # to set for it. Checked as "tells me to set it" against "does not",
        # because the setting's own name appears either way round and an
        # assertion that only looked for it would pass on both.
        said = service.shown_where(
            self._config(DESKTOP_SCENE=desktop.SCENE_LOAD),
            render.SHOWS_LOAD, "load gauge")
        self.assertEqual(len(said), 2, said)
        self.assertIn("set RAINBOW_SHOWS=load", said[0])
        self.assertNotIn("set DESKTOP_SCENE", said[1])
        # And the other way round.
        said = service.shown_where(
            self._config(RAINBOW_SHOWS=render.SHOWS_LOAD),
            render.SHOWS_LOAD, "load gauge")
        self.assertNotIn("set RAINBOW_SHOWS", said[0])
        self.assertIn("set DESKTOP_SCENE=load", said[1])


class DescribeTest(unittest.TestCase):
    """The report of --desktop about a scene. A user trusts a control by it."""

    def _said(self, scene):
        return desktop.describe(scene, "#00b0ff", 90, 2.0)

    def test_every_setting_that_applies_is_named(self):
        said = self._said(desktop.SCENE_BREATH)
        for part in ("#00b0ff", "90", "2"):
            self.assertIn(part, said, said)

    def test_a_rainbow_still_names_its_brightness(self):
        """The report that started this.

        A rainbow makes its own colours, so the line gave the colour and the
        brightness together. A report with neither of the two reads as a
        brightness with no result. A user reported exactly that: "the slider
        does nothing".
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
        # Not "manual", which is the name of that effect in the shim. The word
        # in the report must be the word in the file.
        self.assertTrue(self._said(desktop.SCENE_COLOR).startswith(
            desktop.SCENE_COLOR))

    def _named_when_it_matters(self, **renderer_options):
        """Each setting moved, the strip re-rendered, the line checked.

        This does not ask "does the table say so", because that reads back the
        code. Both directions are important, for one reason. A setting with a
        result and no line in the report is the fault behind "the slider does
        nothing". A setting with a line and no result makes a user move it and
        see no change.

        This draws each scene as the service draws it: the `shows` value of the
        scene goes down with the snapshot. For the four effects of this project
        that value is the complete difference between them. Both sensors are here,
        so each scene draws itself and does not use the rainbow. The rainbow is a
        different effect with different settings.
        """
        renderer = render.Renderer(led_count=17, temperature=SteadyTemperature(),
                                   load=SteadyLoad(), **renderer_options)

        def lit(scene, color="#00b0ff", brightness=90, speed=2.0):
            return renderer.render(
                desktop.scene_snapshot(scene, color, brightness, speed), 0.4,
                desktop.scene_shows(scene))

        for name in desktop.SCENES:
            if name == desktop.SCENE_STEAM:
                continue
            said = desktop.describe(name, "#00b0ff", 90, 2.0)
            for word, other in (("colour", lit(name, color="#ff0000")),
                                ("brightness", lit(name, brightness=30)),
                                ("speed", lit(name, speed=0.5))):
                self.assertEqual(word in said, other != lit(name),
                                 "%s: %s" % (name, word))

    def test_a_setting_is_named_exactly_when_it_changes_the_bar(self):
        self._named_when_it_matters()

    def test_the_gauge_answers_to_neither_slider(self):
        """Reported: the two sliders must not reach the CPU and GPU gauge.

        A load scene draws a value, so the brightness and the speed change the
        value and not the look. The report must stop naming them at the moment
        they stop working. The loop above proves that for each scene at one
        time, and it now also reads the load gauge. This test runs that loop
        with the rainbow slot at another value, and it proves that the scene
        does not read that setting.
        """
        self._named_when_it_matters(rainbow_shows=render.SHOWS_FIRE)


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
        # Steam runs in both modes, and a compositor runs in both modes.
        # gamescope runs in Game Mode only.
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
        # comm is the name of the executable and not a command line, so only a
        # program with that name gives this. But a startswith on the names
        # accepts "gamescopereaper" as the session. The result is a bar that
        # holds the last state of Steam.
        _proc(self.root, {1: "systemd", 700: "not-gamescope"})
        self.assertEqual(desktop.running_game_mode(self.root), "")

    def test_a_process_that_exits_while_being_read_is_not_an_error(self):
        # /proc lists processes, and a process can end at each moment. There is
        # a race between the listing and the read, and no program can win it. A
        # loss of that race must not stop the service.
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

        `running` is the result of one look, or a list with one result for each
        look. The watch keeps the last entry for each later look. A test
        therefore gives the changes and not the number of calls.
        """
        answers = [running] if isinstance(running, str) else list(running)
        self.looked = []

        def look():
            self.looked.append(1)
            return answers[min(len(self.looked) - 1, len(answers) - 1)]

        # A machine with a long uptime, and a test can give another value. A
        # read from /proc gives the uptime of the test machine, and a build
        # machine that started a moment before fails each of these tests. See
        # BootTest for the boot itself.
        kwargs.setdefault("uptime", lambda: 10000.0)
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
        # The module writes the time at its load step. A device with no write
        # therefore carries a timestamp of a moment before, and that reads as a
        # recent write. The grace then holds the scene back for a write that
        # never occurred. This test uses a machine with a long uptime. The boot
        # is the one time that the code holds the scene back on purpose. See
        # BootTest.
        untouched = self._snapshot(seq=shim.UNTOUCHED_SEQ, wrote_at=999.0)
        self.assertIsNone(desktop.steam_wrote_ago(untouched, 1000.0))
        self.assertFalse(self._watch("").steam_has_it(untouched, 1000.0))

    def test_nothing_to_look_at_is_not_a_recent_write_either(self):
        self.assertIsNone(desktop.steam_wrote_ago(None, 1000.0))

    def test_a_download_on_the_desktop_keeps_the_bar_steam_s(self):
        """Which is why the grace is worth having at all.

        Steam writes the progress bar at each step, and each write moves the end
        of the grace. The bar therefore stays with Steam for the complete
        download. Nobody designed that behaviour. It is a result of the grace,
        and it is the half of the behaviour to keep.
        """
        watch = self._watch("", grace=2.0)
        for when in (10.0, 11.0, 12.0):
            filling = self._snapshot(wrote_at=when)
            filling.pixels = [(0, 120, 255, 255)] * 17
            self.assertTrue(watch.steam_has_it(filling, when + 0.5), when)

    def test_steam_putting_back_what_it_found_is_steam_letting_go(self):
        """Reported: a few seconds of the Game Mode effect after a download.

        At the end of a download, Steam writes the effect of Game Mode again. Its
        last write is therefore the state from before the download, and the grace
        showed that state for its complete length. The result is some seconds of
        an effect that no user selected, between the download and the desktop
        scene.
        """
        watch = self._watch("", grace=2.0)
        resting = self._snapshot(wrote_at=-60.0)

        # At rest, and the scene reads that rest state of Steam.
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
        # The value on the shim during a Steam session is not the rest state of
        # Steam. It is one step of a download. A read there makes the next
        # progress write look like the end of the download.
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
        with self.assertLogs("steamos_utility_center.desktop", "INFO") as caught:
            watch.game_mode(1002.0)
        self.assertIn("Game Mode", caught.output[0])

    def test_it_does_not_say_it_again_every_time_it_looks(self):
        watch = self._watch("gamescope", interval=0.0)
        with self.assertLogs("steamos_utility_center.desktop", "INFO") as caught:
            for tick in range(5):
                watch.game_mode(1000.0 + tick)
        self.assertEqual(len(caught.output), 1)

    def test_the_first_answer_is_said_even_when_it_is_the_dull_one(self):
        """Because the journal is the only way to see any of this.

        Game Mode has no terminal, so a user reads the decisions of the service
        after the session. A log with a line for a change only leaves a service
        that starts on the desktop with no line, and that looks the same as a
        check that did not run.
        """
        with self.assertLogs("steamos_utility_center.desktop", "INFO") as caught:
            self._watch("").game_mode(1000.0)
        self.assertEqual(len(caught.output), 1)
        self.assertIn(desktop.GAME_MODE_MARK, caught.output[0])


class JournalTest(unittest.TestCase):
    """Reading back what the service saw while nobody could watch."""

    SAMPLE = "\n".join((
        "2026-08-21T18:02:11+0200 fractal steamos-utility-center[901]: INFO "
        "steamos-utility-center: reading LED state from /dev/valve-leds-shim",
        "2026-08-21T18:02:11+0200 fractal steamos-utility-center[901]: INFO "
        "steamos-utility-center: " + desktop.ON_THE_DESKTOP,
        "2026-08-21T19:41:03+0200 fractal steamos-utility-center[901]: INFO "
        "steamos-utility-center: " + desktop.IN_GAME_MODE % "gamescope",
        "2026-08-21T21:05:55+0200 fractal steamos-utility-center[901]: INFO "
        "steamos-utility-center: " + desktop.ON_THE_DESKTOP,
    ))

    def test_the_lines_it_wants_are_the_ones_it_wrote(self):
        """The two ends of it, which is the whole reason this can be trusted.

        A report that searches for old text of the log always answers "nothing
        here". That answer looks the same as a machine where the service never
        found Game Mode, and that is the fault that the report must find.
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

    This is one test and not a set of tests. The fault was not one answer,
    because each answer was correct alone. The fault was the sequence of the
    run: the scene, the download, and then some seconds of an effect that no
    user selected.
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

        The grace is short now, and that length is a guess about the write rate
        of Steam during a download. No code here can know that rate. A wrong
        guess makes the grace longer again, and this test proves that the
        reported fault does not return with a longer grace.
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


class BootTest(unittest.TestCase):
    """The seconds before the machine gives its mode.

    A user reported this: at the start the bar ran the startup breath for some
    seconds, then the desktop scene, then the startup breath again, and then
    Steam took control. The two middle steps come from the boot. This service
    starts at multi-user.target, and that is before the session that gives the
    mode. So for some seconds a machine that starts in Game Mode looks the same
    as a desktop: gamescope does not run yet, and no program wrote to the LEDs.
    The scene started there, and the start of gamescope ended it.
    """

    def _watch(self, up, running="", **kwargs):
        self.up = [up]
        return desktop.Ownership(look=lambda: running,
                                 uptime=lambda: self.up[0], **kwargs)

    def _untouched(self):
        snapshot = shim.make_snapshot(effect=shim.EFFECT_OFF)
        snapshot.seq = shim.UNTOUCHED_SEQ
        snapshot.monotonic_ns = 0
        return snapshot

    def test_the_scene_does_not_come_up_in_the_middle_of_the_boot(self):
        watch = self._watch(up=8.0)
        self.assertTrue(watch.steam_has_it(self._untouched(), 1000.0),
                        "the scene took the bar while the machine was still "
                        "coming up")

    def test_and_does_once_the_machine_is_up_with_no_game_mode_in_sight(self):
        """Which is the whole reason the scene exists on such a machine.

        A bar that waits for a Game Mode session is a setting with no result on
        a machine with a desktop only. Such a machine can have no Game Mode
        session.
        """
        watch = self._watch(up=8.0)
        untouched = self._untouched()
        self.assertTrue(watch.steam_has_it(untouched, 1000.0))
        self.assertFalse(watch.steam_has_it(untouched,
                                            1000.0 + desktop.BOOT_SETTLE))

    def test_a_machine_that_is_long_since_up_waits_for_nothing(self):
        # Apply restarts the service, and that is not a boot: the scene has to
        # come back the moment it does.
        watch = self._watch(up=9000.0)
        self.assertFalse(watch.steam_has_it(self._untouched(), 1000.0))

    def test_steam_writing_ends_it_whatever_the_clock_says(self):
        # The window applies while neither mode gave an answer. Here one mode
        # gave one.
        watch = self._watch(up=8.0)
        written = shim.make_snapshot(effect=shim.EFFECT_RAINBOW)
        written.seq, written.monotonic_ns = 9, int(990.0 * 1e9)
        self.assertFalse(watch.steam_has_it(written, 1000.0))

    def test_game_mode_arriving_ends_it_the_other_way(self):
        watch = self._watch(up=8.0, running="gamescope")
        self.assertTrue(watch.steam_has_it(self._untouched(), 1000.0))

    def test_the_uptime_is_read_once_and_not_once_a_frame(self):
        # Sixty times a second, for an answer that only ever goes one way.
        asked = []
        watch = desktop.Ownership(look=lambda: "",
                                  uptime=lambda: asked.append(1) or 8.0)
        for tick in range(5):
            watch.steam_has_it(self._untouched(), 1000.0 + tick)
        self.assertEqual(len(asked), 1)

    def test_a_machine_that_will_not_say_is_taken_as_up(self):
        # /proc/uptime is not there, or says something unreadable. Holding the
        # bar on that would be a scene that never appears on such a machine,
        # which is worse than the few seconds this exists to fix.
        watch = self._watch(up=None)
        self.assertFalse(watch.steam_has_it(self._untouched(), 1000.0))

    def test_an_unreadable_uptime_is_none_rather_than_an_error(self):
        self.assertIsNone(desktop.machine_uptime("/does/not/exist"))
        with tempfile.NamedTemporaryFile("w", suffix=".uptime") as handle:
            handle.write("not a number at all\n")
            handle.flush()
            self.assertIsNone(desktop.machine_uptime(handle.name))

    def test_the_real_one_reads_this_machine(self):
        up = desktop.machine_uptime()
        self.assertIsNotNone(up, "/proc/uptime should be readable here")
        self.assertGreater(up, 0.0)


class DownloadGapTest(unittest.TestCase):
    """The gap between Steam's writes, which the grace was shorter than.

    A real machine gave this report during a download of a 100 GB game over a
    fast line: approximately two seconds of the download bar, then
    approximately five seconds of the desktop effect, then the download bar
    again, for the complete download. Steam writes the progress bar one time
    for each step that it can show, and the grace ended in each gap between two
    writes. The bar therefore changed owner twelve times each minute, for
    fifteen minutes.

    The gap is also not a constant. The bar has the same number of steps for
    each connection, so the gap is the length of the download divided by that
    number. It was seven seconds here, and one minute on a line that is ten
    times slower.
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
        # The patience is a fallback only. The write of the rest state by Steam
        # ends a download, and this code finds that write exactly. So a long
        # patience costs nothing at the end of a download.
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

        The service reads the rest state of Steam on the desktop, and a Game
        Mode session can leave a different effect on the bar. With the old
        value, the service reads that as an effect of Steam for the complete
        patience. The handover then takes two minutes and not two seconds.
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

    This is important because the grace must be longer than the gap between
    two writes of a download. No code in this project can calculate that gap.
    Steam decides the redraw rate of a progress bar.
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

        The shim gives the current state and not a queue. Two writes inside one
        wait therefore give one read. Only the counter reports the second write.
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

    A user reported a flash of the Game Mode effect at each end of a download,
    each one shorter than one second. --dump showed the cause: Steam reduces
    the brightness of its own effect to zero before the progress bar, and it
    raises the brightness again at the end. Each step takes thirty
    milliseconds, and each step of both fades is different from the rest state
    in the brightness only.

    The values below are that recording. They stay here because they are a fact
    about a real machine and not a calculation. The complete correction uses
    them.
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
            # The service reads between two writes of Steam. The scene then starts
            # and reads the rest state of Steam.
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

        This does not test "the scene owns the fade-out and the fade-in",
        because that reads back the code. For each other behaviour, the bar must
        never show the rainbow. The bar belongs to the desktop, and the download
        is the one message of Steam on it.
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
        # The control case. A scene that never gives the bar to Steam passes
        # the test above and breaks the behaviour that the test keeps.
        lit = [steams for snapshot, steams in self._walk()
               if snapshot.effect == shim.EFFECT_MANUAL
               and snapshot.brightness_scale > 0]
        self.assertTrue(lit and all(lit), "the progress bar did not show")

    def test_half_a_lit_rainbow_is_the_same_rainbow(self):
        # The complete rule in one line: the properties that make two states
        # one rest state of Steam, and the properties that do not.
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
        path = os.path.join(HERE, "..", "server", "steamos-utility-center.conf")
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

        One place reads each of three settings. A service that reads three of
        the four looks the same as a service where one slider has no result. A
        test must report that, and not a user who moves the slider.
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
