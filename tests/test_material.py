# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The colour system the window is painted from.

The point of doing this in tones rather than in picked colours is that
readability stops being a matter of taste, so that is what is checked here: not
that a surface came out a particular blue, but that whatever the desktop is set
to, every label still stands off what it is written on.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "gui"))

import kdetheme  # noqa: E402
import material  # noqa: E402

# A spread of accents to seed from: Plasma's own blue, a warm one, a cold one,
# a very dark one, and a grey with no hue at all to speak of.
SEEDS = ("#3daee9", "#f67400", "#27ae60", "#7f3fbf", "#101820", "#808080")


class ColourSpaceTest(unittest.TestCase):
    """OKLab, and the one property the whole scheme rests on."""

    def test_a_colour_survives_the_round_trip(self):
        for colour in SEEDS + ("#000000", "#ffffff", "#ff0000"):
            self.assertEqual(material.from_oklab(*material.to_oklab(colour)),
                             colour, colour)

    def test_lightness_is_lightness_whatever_the_hue(self):
        # The reason for not using HSL: #0000ff and #ffff00 are both "50%"
        # there, and a tonal ladder built on that would give a blue scheme
        # dark surfaces and a yellow one bright ones from the same rung.
        blue = material.to_oklch("#0000ff")[0]
        yellow = material.to_oklch("#ffff00")[0]
        self.assertLess(blue, 0.5)
        self.assertGreater(yellow, 0.9)

    def test_a_tone_is_the_same_brightness_from_any_seed(self):
        for tone in (10, 40, 90):
            lightnesses = [material.to_oklch(material.Ladder(seed)(tone))[0]
                           for seed in SEEDS]
            self.assertLess(max(lightnesses) - min(lightnesses), 0.03,
                            "tone %d drifts between seeds" % tone)

    def test_out_of_gamut_chroma_is_taken_off_rather_than_clipped(self):
        # There is no vivid near-white. Clipping the channels instead would
        # drag the hue somewhere else, which is what would make a pale accent
        # come out the wrong colour rather than merely a faint one.
        vivid = material.from_oklch(0.97, 0.4, material.to_oklch("#3daee9")[2])
        self.assertRegex(vivid, r"^#[0-9a-f]{6}$")
        wanted = material.to_oklch("#3daee9")[2]
        self.assertAlmostEqual(material.to_oklch(vivid)[2], wanted, places=1)

    def test_the_tone_scale_matches_what_material_means_by_it(self):
        # Tone is CIE L*, not OKLab's L. Mid grey is tone 50-ish; taking the
        # tone straight would have put it at 0.5 in OKLab, which is darker.
        grey = material.Ladder("#808080", chroma=0.0)
        self.assertAlmostEqual(
            material.to_oklab(grey(50))[0], 0.569, places=2)
        # And the ends are the ends.
        self.assertEqual(grey(0), "#000000")
        self.assertEqual(grey(100), "#ffffff")

    def test_a_ladder_only_climbs(self):
        ladder = material.Ladder("#3daee9")
        tones = [material.to_oklch(ladder(tone))[0]
                 for tone in range(0, 101, 5)]
        self.assertEqual(tones, sorted(tones))


class SchemeTest(unittest.TestCase):
    """Every role, from every seed, light and dark."""

    def _schemes(self):
        for seed in SEEDS:
            for dark in (False, True):
                yield seed, dark, material.scheme(seed, dark=dark)

    def test_every_role_is_a_colour(self):
        for seed, dark, roles in self._schemes():
            self.assertEqual(set(roles), set(material.ROLES))
            for name, colour in roles.items():
                self.assertRegex(colour, r"^#[0-9a-f]{6}$",
                                 "%s from %s" % (name, seed))

    def test_text_can_be_read_on_what_it_is_written_on(self):
        # 4.5:1 is what WCAG asks of body text. This is the whole reason the
        # tones are taken from the spec's table rather than chosen: it holds
        # for any accent the desktop might be set to, without anyone looking.
        pairs = (("on_surface", "surface"),
                 ("on_surface_variant", "surface"),
                 ("on_surface", "surface_container_high"),
                 ("on_surface_variant", "surface_container_highest"),
                 ("on_primary", "primary"),
                 ("on_primary_container", "primary_container"),
                 ("on_secondary_container", "secondary_container"),
                 ("on_error_container", "error_container"),
                 ("on_positive_container", "positive_container"))
        for seed, dark, roles in self._schemes():
            for text, ground in pairs:
                self.assertGreaterEqual(
                    material.contrast(roles[text], roles[ground]), 4.5,
                    "%s on %s is unreadable in the %s scheme from %s"
                    % (text, ground, "dark" if dark else "light", seed))

    def test_an_outline_can_be_seen_against_its_surface(self):
        # 3:1, which is what the guidelines ask of a control's own edge - it
        # has to be found, not read.
        for seed, dark, roles in self._schemes():
            self.assertGreaterEqual(
                material.contrast(roles["outline"], roles["surface"]), 3.0,
                "the outline vanishes in the scheme from %s" % seed)

    def test_the_containers_climb_away_from_the_page(self):
        # Elevation is tone: each container is one step further from the
        # surface than the last, which is what lets the boxes go away.
        ladder = ("surface_container_lowest", "surface_container_low",
                  "surface_container", "surface_container_high",
                  "surface_container_highest")
        for seed, dark, roles in self._schemes():
            steps = [material.to_oklch(roles[name])[0] for name in ladder]
            self.assertEqual(steps, sorted(steps, reverse=not dark),
                             "the containers do not climb in the %s scheme "
                             "from %s" % ("dark" if dark else "light", seed))

    def test_a_dark_scheme_is_dark_and_a_light_one_is_not(self):
        for seed in SEEDS:
            light = material.to_oklch(material.scheme(seed)["surface"])[0]
            dark = material.to_oklch(
                material.scheme(seed, dark=True)["surface"])[0]
            self.assertGreater(light, 0.9, seed)
            self.assertLess(dark, 0.3, seed)

    def test_the_window_is_tinted_by_the_accent_rather_than_grey(self):
        # What makes a Material window look calm rather than drab: the
        # surfaces are not grey, they are a few per cent of the accent.
        warm = material.scheme("#f67400")["surface"]
        cool = material.scheme("#3daee9")["surface"]
        self.assertNotEqual(warm, cool)
        # But only a few per cent - a tinted surface is not a coloured one.
        self.assertLess(material.to_oklch(warm)[1], 0.03)

    def test_a_grey_accent_does_not_pick_up_a_hue_from_nowhere(self):
        # atan2 on a colour with no chroma returns whatever rounding left
        # behind, and a grey desktop must not come out faintly green. Error and
        # positive are seeded separately and stay red and green, which is the
        # point of them - a warning is not a matter of taste.
        roles = material.scheme("#808080")
        for name, colour in roles.items():
            if name.split("_")[-1] in ("error", "positive") \
                    or "error" in name or "positive" in name:
                continue
            self.assertLess(material.to_oklch(colour)[1], 0.01, name)

    def test_the_status_colours_follow_the_ones_the_desktop_names(self):
        # Good and bad have to be the desktop's own, not a hardcoded green and
        # red that vanish in someone's scheme.
        roles = material.scheme("#3daee9", error="#0000ff", positive="#ff00ff")
        for role, seed in (("error", "#0000ff"), ("positive", "#ff00ff")):
            self.assertAlmostEqual(material.to_oklch(roles[role])[2],
                                   material.to_oklch(seed)[2], places=1, msg=role)
        self.assertNotEqual(roles["error"], roles["positive"])

    def test_the_shipped_default_scheme_holds_up(self):
        # Breeze, which is what a machine with no kdeglobals gets.
        roles = material.scheme(kdetheme.BREEZE_LIGHT["selection"],
                                error=kdetheme.BREEZE_LIGHT["negative"],
                                positive=kdetheme.BREEZE_LIGHT["positive"])
        self.assertGreaterEqual(
            material.contrast(roles["on_surface"], roles["surface"]), 4.5)


class StateLayerTest(unittest.TestCase):
    """Hover, press and disabled, which are one mechanism for every widget."""

    def setUp(self):
        self.roles = material.scheme("#3daee9")

    def test_a_state_layer_moves_the_colour_without_replacing_it(self):
        lit = material.layer(self.roles, "primary", "on_primary",
                             material.STATE_HOVER)
        self.assertNotEqual(lit, self.roles["primary"])
        self.assertLess(
            abs(material.to_oklch(lit)[0]
                - material.to_oklch(self.roles["primary"])[0]), 0.1)

    def test_pressed_is_further_than_hovered(self):
        hover = material.layer(self.roles, "primary", "on_primary",
                               material.STATE_HOVER)
        pressed = material.layer(self.roles, "primary", "on_primary",
                                 material.STATE_PRESSED)
        self.assertNotEqual(hover, pressed)

    def test_disabled_content_keeps_its_shape_and_loses_its_weight(self):
        faded = material.disabled(self.roles, self.roles["on_surface"])
        self.assertLess(material.contrast(faded, self.roles["surface"]),
                        material.contrast(self.roles["on_surface"],
                                          self.roles["surface"]))
        # Still visible, though: a disabled control that has vanished is worse
        # than one that was never greyed out.
        self.assertNotEqual(faded, self.roles["surface"])

    def test_blending_ends_where_it_should(self):
        self.assertEqual(material.blend("#000000", "#ffffff", 0), "#000000")
        self.assertEqual(material.blend("#000000", "#ffffff", 1), "#ffffff")
        self.assertEqual(material.blend("#000000", "#ffffff", 0.5), "#808080")


if __name__ == "__main__":
    unittest.main()
