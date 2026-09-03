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
        # WCAG asks for 4.5:1 for body text. That is the reason the tones come
        # from the table of the specification and not from a selection. The
        # ratio then holds for each accent colour of the desktop, and no person
        # must check it.
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
        # 3:1, which is the value that the guidelines give for the edge of a
        # control. A user must find the edge and does not read it.
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
        # But only some per cent. A surface with a tint is not a coloured
        # surface.
        self.assertLess(material.to_oklch(warm)[1], 0.03)

    def test_a_grey_accent_does_not_pick_up_a_hue_from_nowhere(self):
        # atan2 on a colour with no chroma returns the rounding error, and a
        # grey desktop must not become green. The error role and the positive
        # role have their own seed colours and stay red and green. That is
        # their purpose, because a warning must always look the same.
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


class ControlSizeTest(unittest.TestCase):
    """How big a control is, for a desktop set to any font.

    A user also holds this machine in the hand and uses a trackpad, and not a
    mouse on a desk. Desktop guidelines give approximately twenty pixels for the
    smallest target. The defaults of ttk are much smaller, and that made the
    first version difficult to use. A fixed size is also not sufficient. A size
    for a ten point font is too small for a thirteen point font. So this code
    calculates the sizes, and the arithmetic must be correct.
    """

    MINIMUM = 20
    # Ten point through about twenty, which is the range a desktop font and a
    # scaling factor between them can plausibly produce.
    FONTS = range(12, 40)

    def _sizes(self):
        return [(linespace, material.control_sizes(linespace))
                for linespace in self.FONTS]

    def test_every_target_is_big_enough_to_hit(self):
        for linespace, sizes in self._sizes():
            for name in ("switch_width", "switch_height", "knob", "radio",
                         "control"):
                self.assertGreaterEqual(sizes[name], self.MINIMUM,
                                        "%s at linespace %d" % (name,
                                                                linespace))

    def test_a_switch_is_wider_than_it_is_tall(self):
        for linespace, sizes in self._sizes():
            self.assertGreater(sizes["switch_width"], sizes["switch_height"],
                               linespace)

    def test_the_thumb_grows_when_the_switch_goes_on(self):
        # The state has to be readable without relying on colour alone.
        for linespace, sizes in self._sizes():
            self.assertGreater(sizes["thumb_on"], sizes["thumb_off"], linespace)
            # The thumb must also fit into its track. These values are radii, so
            # two times one of them is the thumb.
            self.assertLess(sizes["thumb_on"] * 2, sizes["switch_height"],
                            linespace)

    def test_the_knob_stands_proud_of_its_groove(self):
        # A knob no bigger than the groove is invisible as a handle.
        for linespace, sizes in self._sizes():
            self.assertGreater(sizes["knob"], sizes["track"], linespace)
            self.assertGreaterEqual(sizes["track"], 8, linespace)

    def test_the_groove_is_centred_evenly_in_the_knob_sized_image(self):
        # The groove is drawn inside an image as tall as the knob, so an odd
        # difference would put it half a pixel off centre.
        for linespace, sizes in self._sizes():
            self.assertEqual((sizes["knob"] - sizes["track"]) % 2, 0, linespace)

    def test_everything_grows_with_the_font(self):
        # The point of measuring rather than fixing: a control that stays put
        # while the text around it grows is the thing that looked wrong.
        small = material.control_sizes(14)
        large = material.control_sizes(30)
        for name in ("control", "switch_width", "switch_height", "knob",
                     "radio"):
            self.assertGreater(large[name], small[name], name)

    def test_a_switch_and_a_drop_down_stand_the_same_height(self):
        # Which is what lets a column of settings have a rhythm at all: a page
        # alternating between a 32 pixel control and a 42 pixel one has none,
        # whatever the spacing between the rows.
        for linespace, sizes in self._sizes():
            field = linespace + 2 * sizes["field_padding"] + 8   # ttk's border
            self.assertLessEqual(abs(field - sizes["switch_height"]), 6,
                                 "at linespace %d a switch is %d and a "
                                 "drop-down %d" % (linespace,
                                                   sizes["switch_height"],
                                                   field))

    def test_no_control_is_taller_than_the_row_it_sits_in(self):
        for linespace, sizes in self._sizes():
            for name in ("switch_height", "knob", "radio"):
                self.assertLessEqual(sizes[name], sizes["control"],
                                     "%s at linespace %d" % (name, linespace))


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
        # But it stays visible. A disabled control that a user cannot see is
        # worse than a control with no grey state.
        self.assertNotEqual(faded, self.roles["surface"])

    def test_blending_ends_where_it_should(self):
        self.assertEqual(material.blend("#000000", "#ffffff", 0), "#000000")
        self.assertEqual(material.blend("#000000", "#ffffff", 1), "#ffffff")
        self.assertEqual(material.blend("#000000", "#ffffff", 0.5), "#808080")


if __name__ == "__main__":
    unittest.main()
