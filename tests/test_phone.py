# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The phone bridge, without a phone.

Everything between the bus and the pipe is a pure function here, which is the
whole reason the module is shaped the way it is: the lines below are what
`gdbus monitor` actually prints, kept as text, so the parsing can be wrong in
a test rather than on somebody's desk.

What these cannot check is that the bus says exactly this on every machine -
no test can, and that is what `--watch-phone --print` is for.
"""

import os
import sys
import tempfile
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_led import config as config_module      # noqa: E402
from steamos_led import notify, phone                # noqa: E402
from shellvalues import shell_value                  # noqa: E402

KDECONNECT_LINES = [
    "Monitoring signals from all objects owned by org.kde.kdeconnect",
    "/modules/kdeconnect/devices/d33f/notifications: "
    "org.kde.kdeconnect.device.notifications.notificationPosted "
    "('0|com.whatsapp|1|null|10123',)",
    "/modules/kdeconnect/devices/d33f/notifications: "
    "org.kde.kdeconnect.device.notifications.notificationRemoved "
    "('0|com.whatsapp|1|null|10123',)",
]


def _posted(key):
    return ("/modules/kdeconnect/devices/d33f/notifications: "
            "org.kde.kdeconnect.device.notifications.notificationPosted "
            "('%s',)" % key)


# Three notifications arriving, with the two lines gdbus opens with in front of
# them. The ids are the Android spelling, so the app is in the line itself -
# which is what lets these drive the loop without a lookup standing in for the
# bus at every step.
POSTED_LINES = [
    "Monitoring signals from all objects owned by org.kde.kdeconnect",
    "The name org.kde.kdeconnect is owned by :1.42",
    _posted("0|com.whatsapp|1|null|10123"),
    _posted("0|org.thoughtcrime.securesms|2|null|10124"),
    _posted("0|com.android.calendar|3|null|10125"),
]

# What KDE Connect says when something moved and it will not say what. The
# answer is to re-read the notifications lately seen.
REFRESHED_LINE = ("/modules/kdeconnect/devices/d33f/notifications: "
                  "org.kde.kdeconnect.device.notifications.refreshed ()")

# And what a Steam Machine actually sent, which is not the same thing: the id
# is KDE Connect's own counter, and everything worth knowing is a property of
# the object it names.
COUNTED_LINE = ("/modules/kdeconnect/devices/d33f/notifications: "
                "org.kde.kdeconnect.device.notifications.notificationPosted "
                "('3',)")

# And the third shape, which is the one that carries a conversation: the
# signal arrives on the notification's own object, with no arguments at all.
# Recorded from a Steam Deck.
READY_PATH = ("/modules/kdeconnect/devices/"
              "1506bc06374045619d0548f48af0c3c8/notifications/6")
READY_LINE = ("%s: org.kde.kdeconnect.device.notifications.notification.ready "
              "()" % READY_PATH)

COUNTED_DETAILS = (
    "({'id': <'3'>, 'appName': <'WhatsApp'>, 'ticker': <'Anna: bist du da?'>,"
    " 'title': <'Anna'>, 'text': <'bist du da?'>, 'dismissable': <true>,"
    " 'hasIcon': <true>, 'silent': <false>},)\n")


class ReadingTheBusTest(unittest.TestCase):
    """One line of monitor output into "an app said something", or nothing."""

    def _sightings(self, lines):
        seen = [phone.read_line(line) for line in lines]
        return [one for one in seen if one is not None]

    def test_gdbus_own_chatter_is_not_a_notification(self):
        # It opens with two lines about what it is monitoring, and a machine
        # that flashed at those would flash once at every restart.
        self.assertEqual(len(self._sightings(POSTED_LINES)), 3)

    def test_a_notification_going_away_is_not_a_new_one(self):
        # notificationRemoved arrives for every notification that is dismissed,
        # so reading it as an arrival would flash twice for one message - the
        # second time when you picked the phone up.
        seen = self._sightings(KDECONNECT_LINES)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].app, "com.whatsapp")

    def test_an_id_that_is_only_a_number_says_where_to_ask(self):
        # What a real machine sent: KDE Connect's own counter, with the app
        # kept as a property of an object of its own. Reading the id as the
        # app was this bridge's first mistake, and it printed "3 -> phone".
        seen = phone.read_line(COUNTED_LINE)
        self.assertEqual(seen.app, "3")
        self.assertEqual(seen.where,
                         "/modules/kdeconnect/devices/d33f/notifications/3")

    def test_the_device_is_read_off_the_signal_rather_than_assumed(self):
        # More than one phone can be paired, and only one of them sent this.
        line = COUNTED_LINE.replace("d33f", "0ther")
        self.assertIn("/devices/0ther/", phone.read_line(line).where)

    def test_this_machine_s_own_notifications_are_not_the_phone_s(self):
        """The withdrawn fallback, kept as an assertion.

        There used to be a second source that read the desktop's own
        notification bus, for a machine where KDE Connect answered nothing.
        It carries everything, so a chat app on the Steam Machine itself
        flashed exactly like a message from the phone - and in Game Mode,
        where there is no notification daemon at all, it carried nothing.
        Only KDE Connect's own signals are read now.
        """
        line = ("/org/freedesktop/Notifications: "
                "org.freedesktop.Notifications.Notify ('Discord', uint32 0, "
                "'', 'guild', 'ping', @as [], @a{sv} {}, int32 -1)")
        self.assertIsNone(phone.read_line(line))

    def test_a_notification_being_updated_is_a_new_message(self):
        """Reported, and the last of this bug's three layers.

        Android does not post a second notification for the second message of
        a conversation - it updates the first. Listening only for "posted"
        meant one flash per conversation and nothing again until it was
        cleared off the machine, at which point the next message counted as
        new. The dry run showed one line where six messages had been sent.
        """
        updated = COUNTED_LINE.replace("notificationPosted",
                                       "notificationUpdated")
        seen = phone.read_line(updated)
        self.assertIsNotNone(seen)
        self.assertEqual(seen.where,
                         "/modules/kdeconnect/devices/d33f/notifications/3")

    def test_a_notification_going_away_is_still_not_one(self):
        # Both spellings of it: one notification dismissed, or the lot.
        for member in phone.KDECONNECT_GONE:
            line = COUNTED_LINE.replace("notificationPosted", member)
            self.assertIsNone(phone.read_line(line), member)

    def test_a_notification_saying_it_is_ready_is_that_notification(self):
        """Recorded from a Steam Deck, and the plainest of the three shapes.

        The signal arrives on the notification's own object -
        ".../notifications/6" - so there is no id to read and nothing to
        remember: what it is about is where it came from. This is the one
        that carries the second message of a conversation, which is why six
        messages had been coming out as one line.
        """
        seen = phone.read_line(READY_LINE)
        self.assertIsNotNone(seen)
        self.assertEqual(seen.where, READY_PATH)
        self.assertEqual(seen.app, "", "the object has to be asked")

    def test_the_same_signal_elsewhere_is_not_a_notification(self):
        # "ready" is not a rare name, and KDE Connect has more objects than
        # notifications. The path is what tells them apart.
        line = ("/modules/kdeconnect/devices/1506bc: "
                "org.kde.kdeconnect.device.ready ()")
        self.assertIsNone(phone.read_line(line))

    def test_the_name_of_a_signal_can_be_read_off_a_line(self):
        self.assertEqual(phone.member_of(COUNTED_LINE), "notificationPosted")
        self.assertEqual(phone.member_of("Monitoring signals from all"), "")
        self.assertEqual(phone.member_of(""), "")

    def test_a_truncated_line_is_dropped_rather_than_guessed_at(self):
        for line in ("", "nonsense",
                     "/x: org.kde.kdeconnect.device.notifications."
                     "notificationPosted ()",
                     "/x: org.kde.kdeconnect.device.notifications."
                     "notificationPosted ("):
            self.assertIsNone(phone.read_line(line), line)

    def test_the_app_is_found_in_either_android_key_spelling(self):
        self.assertEqual(phone.app_from_key("0|com.whatsapp|1|null|10123"),
                         "com.whatsapp")
        self.assertEqual(phone.app_from_key("org.thoughtcrime.securesms:7:tag"),
                         "org.thoughtcrime.securesms")

    def test_a_key_that_names_no_package_is_still_something_to_match_on(self):
        # Better a rule the reader can see in --print than a blank.
        self.assertEqual(phone.app_from_key("weird-key"), "weird-key")


class AskingAboutOneTest(unittest.TestCase):
    """The second call, which is what turns "3" into "WhatsApp"."""

    def setUp(self):
        self.seen = phone.read_line(COUNTED_LINE)

    def test_it_asks_the_object_the_id_names(self):
        command = phone.details_command(self.seen.where)
        self.assertEqual(command[:2], ["gdbus", "call"])
        self.assertIn(self.seen.where, command)
        self.assertIn(phone.NOTIFICATION_INTERFACE, command)

    def test_the_reply_carries_the_app_and_the_message(self):
        full = phone.read_details(COUNTED_DETAILS, self.seen)
        self.assertEqual(full.app, "WhatsApp")
        self.assertEqual(full.title, "Anna")
        self.assertEqual(full.body, "bist du da?")

    def test_a_colon_in_the_message_does_not_split_the_wrong_way(self):
        # "Bob: re: dinner" is a perfectly ordinary title, and the colon is
        # also what separates a property from its value.
        reply = ("({'appName': <'Signal'>, 'title': <'Bob: re: dinner'>,"
                 " 'text': <'8pm?'>},)")
        full = phone.read_details(reply, self.seen)
        self.assertEqual(full.app, "Signal")
        self.assertEqual(full.title, "Bob: re: dinner")

    def test_an_app_name_with_a_quote_in_it_survives(self):
        # The reply is one long quoted string after another, so an apostrophe
        # inside one of them is exactly what ends it in the wrong place.
        reply = "({'appName': <'Bob\\'s app'>, 'title': <'hi'>},)"
        self.assertEqual(phone.read_details(reply, self.seen).app, "Bob's app")

    def test_a_comma_in_the_message_does_not_shift_the_properties(self):
        # The entries are split on commas, so a message containing one is the
        # case that would read half a sentence as the next property.
        reply = ("({'appName': <'WhatsApp'>, 'title': <'Anna'>,"
                 " 'text': <'ja, gleich'>},)")
        full = phone.read_details(reply, self.seen)
        self.assertEqual(full.app, "WhatsApp")
        self.assertEqual(full.body, "ja, gleich")

    def test_nothing_to_say_leaves_the_sighting_as_it_was(self):
        # A notification dismissed on the phone before this asked is gone by
        # then, and that race is nobody's fault. It still flashes.
        for reply in ("", "()", "nonsense", None):
            self.assertEqual(phone.read_details(reply, self.seen), self.seen)

    def test_an_answer_without_a_name_does_not_erase_the_one_there_is(self):
        reply = "({'title': <'Anna'>},)"
        self.assertEqual(phone.read_details(reply, self.seen).app, "3")

    def test_a_bus_that_does_name_the_app_in_the_id_needs_no_lookup(self):
        # The two spellings coexist; app_from_key is right for one of them and
        # the lookup only improves on it.
        seen = phone.read_line(KDECONNECT_LINES[1])
        self.assertEqual(seen.app, "com.whatsapp")


class KdeConnectTest(unittest.TestCase):
    """Finding it, starting it, and asking it what it is paired with."""

    def test_the_phones_it_knows_are_read_out_of_the_reply(self):
        """Which tells "not running" from "running and nobody is talking".

        Both look the same from here otherwise - a bar that never flashes -
        and they need opposite things done about them. Reported from a real
        machine: the bridge said it was watching, and the phone said it had
        no connection to the PC.
        """
        # Two shapes, because KDE Connect versions differ on which they send.
        self.assertEqual(phone.device_names("(['Pixel 7'],)"), ["Pixel 7"])
        self.assertEqual(
            phone.device_names("({'d33f': 'Pixel 7', 'ab12': 'Tablet'},)"),
            ["Pixel 7", "Tablet"])

    def test_knowing_no_phone_is_not_the_same_as_saying_nothing(self):
        # An empty list is an answer: KDE Connect is up and has nobody paired.
        for empty in ("([],)", "({},)"):
            self.assertEqual(phone.device_names(empty), [], empty)

    def test_the_daemon_is_looked_for_where_daemons_are_kept(self):
        # Not on the PATH on most distributions - it is a daemon nobody is
        # meant to type - so the usual libexec places are tried by hand.
        for place in phone.KDECONNECTD_PLACES:
            self.assertTrue(place.startswith("/"), place)
            self.assertTrue(place.endswith("kdeconnectd"), place)

    def test_a_machine_without_it_is_not_an_error(self):
        # Plenty of them: this is somebody else's program, and the bridge has
        # to be installable on a machine that has never heard of KDE Connect.
        keep = phone.KDECONNECTD_PLACES
        phone.KDECONNECTD_PLACES = ("/nowhere/kdeconnectd",)
        self.addCleanup(setattr, phone, "KDECONNECTD_PLACES", keep)
        with unittest.mock.patch.object(phone.shutil, "which",
                                        return_value=None):
            self.assertIsNone(phone.kdeconnectd_path())
            self.assertIsNone(phone.start_kdeconnectd())

    def test_something_that_is_there_but_not_runnable_is_not_it(self):
        keep = phone.KDECONNECTD_PLACES
        phone.KDECONNECTD_PLACES = (__file__,)      # a file, and not runnable
        self.addCleanup(setattr, phone, "KDECONNECTD_PLACES", keep)
        with unittest.mock.patch.object(phone.shutil, "which",
                                        return_value=None):
            self.assertIsNone(phone.kdeconnectd_path())

    def test_it_is_started_without_a_display(self):
        """Because Qt will not start one at all without a plugin it can use.

        Measured on a Steam Deck: started from a service it inherited
        "wayland", found no compositor, and dumped core before it had done
        anything - "Failed to create wl_display". It has nothing to draw; the
        network and the bus are what is wanted from it, and neither needs a
        screen.
        """
        started = {}

        def remember(command, **kwargs):
            started.update(kwargs, command=command)
            return None

        with unittest.mock.patch.object(phone.subprocess, "Popen", remember):
            phone.start_kdeconnectd("/bin/true")
        self.assertEqual(started["env"]["QT_QPA_PLATFORM"], "offscreen")
        # And the rest of the environment intact: it still has a bus to find,
        # which is named in one of those variables.
        for name in os.environ:
            self.assertIn(name, started["env"], name)

    def test_it_is_started_in_a_session_of_its_own(self):
        """So it outlives the bridge, which restarts.

        A daemon that went down with this process would be started afresh on
        every restart, and each fresh start drops the phone's connection.
        """
        started = {}

        def remember(command, **kwargs):
            started.update(kwargs, command=command)
            return None

        with unittest.mock.patch.object(phone.subprocess, "Popen", remember):
            self.assertEqual(phone.start_kdeconnectd("/bin/true"), "/bin/true")
        self.assertEqual(started["command"], ["/bin/true"])
        self.assertTrue(started["start_new_session"])

    def test_nothing_is_started_when_the_caller_says_not_to(self):
        # --print must not have side effects on the machine it is reporting
        # about, and neither must a test.
        with unittest.mock.patch.object(phone, "_ask", return_value=""):
            with unittest.mock.patch.object(phone, "start_kdeconnectd") as never:
                self.assertIsNone(phone.wake_kdeconnect(revive=False))
        never.assert_not_called()

    def test_a_revived_daemon_is_asked_again_rather_than_assumed(self):
        # Starting it is not the same as it answering, and the answer is what
        # the caller wanted - "which phones", not "did something happen".
        replies = ["", "({'d33f': 'Pixel 9 Pro'},)"]
        with unittest.mock.patch.object(phone, "_ask",
                                        side_effect=lambda _c: replies.pop(0)):
            with unittest.mock.patch.object(phone, "start_kdeconnectd",
                                            return_value="/usr/lib/kdeconnectd"):
                found = phone.wake_kdeconnect(settle=0)
        self.assertEqual(found, ["Pixel 9 Pro"])
        self.assertEqual(replies, [], "it did not ask a second time")

    def test_waking_it_asks_something_that_changes_nothing(self):
        # Any call starts an activatable service, so the cheapest harmless
        # one is the right one - this must never be a call that does
        # something to somebody's phone.
        command = phone.wake_command()
        self.assertIn(phone.KDECONNECT_SERVICE, command)
        self.assertTrue(any(part.endswith("deviceNames") for part in command),
                        command)

    def test_the_monitor_reads_kde_connect_and_nothing_else(self):
        # Naming the service is what keeps this machine's own notifications
        # out: the session bus carries those too, and a monitor without a
        # --dest would read the lot.
        command = phone.monitor_command()
        self.assertEqual(command[:3], ["gdbus", "monitor", "--session"])
        self.assertIn("--dest", command)
        self.assertIn(phone.KDECONNECT_SERVICE, command)


class RulesTest(unittest.TestCase):
    """PHONE_APPS: what an app looks like when it is not the default."""

    def test_a_rule_may_name_a_colour_alone_or_a_shape_with_it(self):
        rules = phone.parse_rules("WhatsApp:#25d366:double_flash, Signal:#3a76f0")
        self.assertEqual(rules[0], phone.Rule("WhatsApp", "#25d366",
                                              "double_flash"))
        self.assertEqual(rules[1], phone.Rule("Signal", "#3a76f0", None))

    def test_an_empty_setting_is_no_rules_rather_than_an_error(self):
        for text in ("", "   ", None, ",,"):
            self.assertEqual(phone.parse_rules(text), ())

    def test_a_rule_that_says_nothing_is_a_typo_worth_hearing_about(self):
        for text in ("WhatsApp", "WhatsApp:", ":#25d366",
                     "WhatsApp:#25d366:bloom:extra"):
            with self.assertRaises(ValueError, msg=text):
                phone.parse_rules(text)

    def test_a_colour_that_is_not_one_is_refused(self):
        with self.assertRaises(ValueError):
            phone.parse_rules("WhatsApp:greenish")

    def test_a_shape_that_does_not_exist_is_refused(self):
        # Naming one that was never implemented would otherwise flash the
        # default and leave you looking for the reason.
        with self.assertRaises(ValueError):
            phone.parse_rules("WhatsApp:#25d366:shimmer")

    def test_every_shape_the_service_has_is_allowed_in_a_rule(self):
        for style in notify.STYLES:
            self.assertEqual(
                phone.parse_rules("App:#ffffff:%s" % style)[0].style, style)

    def test_an_app_matches_whichever_way_the_bus_names_it(self):
        rules = phone.parse_rules("whatsapp:#25d366")
        for name in ("WhatsApp", "com.whatsapp", "WHATSAPP"):
            self.assertIsNotNone(phone.match(rules, name), name)

    def test_a_different_app_does_not_match(self):
        rules = phone.parse_rules("WhatsApp:#25d366")
        for name in ("Signal", "", None, "Telegram"):
            self.assertIsNone(phone.match(rules, name), name)

    def test_the_first_rule_that_fits_is_the_one_used(self):
        rules = phone.parse_rules("WhatsApp:#25d366, WhatsApp:#ffffff")
        self.assertEqual(phone.match(rules, "WhatsApp").color, "#25d366")


class TriggerTest(unittest.TestCase):
    """What actually gets written into the pipe."""

    def setUp(self):
        self.rules = phone.parse_rules(
            "WhatsApp:#25d366:double_flash, Signal:#3a76f0")

    def _trigger(self, app, **kwargs):
        """The trigger, minus what makes this one notification distinct.

        The tag is checked on its own below; everywhere else it would only
        be a digest written out in an assertion, which says nothing about
        the colour or the shape that the assertion is really about.
        """
        written = phone.trigger_for(phone.Sighting(app, "", ""), self.rules,
                                    **kwargs)
        return None if written is None else notify.split_tag(written)[1]

    def test_an_app_with_a_shape_of_its_own_is_spelled_out(self):
        self.assertEqual(self._trigger("WhatsApp"), "double_flash:#25d366")

    def test_an_app_with_only_a_colour_keeps_the_general_shape(self):
        self.assertEqual(self._trigger("Signal"), "#3a76f0")

    def test_everything_else_is_written_as_the_kind_and_not_as_a_colour(self):
        # So PHONE_COLOR and PHONE_STYLE go on being whatever the panel says,
        # without this having to be restarted to hear about a change.
        self.assertEqual(self._trigger("Kalender"), notify.KIND_PHONE)

    def test_only_the_listed_apps_flash_when_that_is_what_was_asked(self):
        self.assertIsNone(self._trigger("Kalender", listed_only=True))
        self.assertEqual(self._trigger("WhatsApp", listed_only=True),
                         "double_flash:#25d366")

    def test_a_burst_from_one_person_is_one_flash(self):
        """What the repeat gap is for, keyed on who is talking.

        Keyed on the message instead, every message was its own conversation
        and a burst of twenty flashed twenty times - which is the opposite
        mistake to the one before it, where everything the phone sent was the
        word "phone" and the whole of it was one conversation.
        """
        said = [phone.trigger_for(
            phone.Sighting("WhatsApp", "Anna", body), ())
            for body in ("eins", "zwei", "drei")]
        self.assertEqual(len(set(said)), 1, said)
        # And it still names the kind, so the panel's colour and shape go on
        # applying to every one of them.
        self.assertEqual(notify.split_tag(said[0])[1], notify.KIND_PHONE)

    def test_somebody_else_writing_is_not_that_burst(self):
        # Another person, or another app: different news, and a gap that
        # swallowed it would be a gap that hides your messages.
        anna = phone.trigger_for(phone.Sighting("WhatsApp", "Anna", "hi"), ())
        bob = phone.trigger_for(phone.Sighting("WhatsApp", "Bob", "hi"), ())
        signal = phone.trigger_for(phone.Sighting("Signal", "Anna", "hi"), ())
        self.assertEqual(len({anna, bob, signal}), 3)

    def test_two_apps_seconds_apart_do_not_collide_either(self):
        # The same bug, and the one nobody would have noticed as a bug: a
        # WhatsApp message and a Signal one inside the same few seconds were
        # one flash, because both were the word "phone".
        first = phone.trigger_for(phone.Sighting("WhatsApp", "Anna", "hi"), ())
        second = phone.trigger_for(phone.Sighting("Signal", "Bob", "hi"), ())
        self.assertNotEqual(first, second)

    def test_the_same_notification_twice_is_still_one_flash(self):
        # Which is what the repeat gap is actually for. An app re-posting an
        # unchanged notification has said nothing new.
        same = phone.Sighting("Kalender", "Zahnarzt", "9:00")
        self.assertEqual(phone.trigger_for(same, ()),
                         phone.trigger_for(same, ()))

    def test_the_overlay_keeps_them_apart_for_real(self):
        """The two ends of it, checked against each other.

        A tag the service ignored would be a fix that changed nothing, and
        this is the assertion that would have caught either mistake.
        """
        overlay = notify.NotificationOverlay(duration=3.5, led_count=17,
                                             repeat_gap=10)
        shown = []

        def told(at, app, who, text):
            trigger = phone.trigger_for(phone.Sighting(app, who, text), ())
            shown.append(overlay.trigger(trigger, at))
            overlay.frame(at)

        # Ten in five seconds from one person: one flash. Two other people
        # inside the same window: theirs get through.
        for index in range(10):
            told(index * 0.5, "WhatsApp", "Anna", "spam %d" % index)
        told(2.0, "WhatsApp", "Bob", "hallo")
        told(2.5, "Signal", "Chris", "hey")
        # And once the gap has run out, she can reach you again.
        told(20.0, "WhatsApp", "Anna", "noch da?")

        self.assertEqual(shown, [True] + [False] * 9 + [True, True, True])

        # And the other way round: one notification posted three times over
        # is one flash, which is the behaviour this must not have broken.
        overlay = notify.NotificationOverlay(duration=3.5, led_count=17,
                                             repeat_gap=10)
        same = phone.Sighting("Kalender", "Zahnarzt", "9:00")
        again = []
        for at in (0.0, 2.0, 4.0):
            again.append(overlay.trigger(phone.trigger_for(same, ()), at))
            overlay.frame(at)
        self.assertEqual(again, [True, False, False])

    def test_the_service_reads_back_everything_this_writes(self):
        # The two ends of the pipe, checked against each other: a trigger this
        # spells one way and notify.py parses another is a flash that silently
        # does not happen.
        for app, wanted in (("WhatsApp", (0x25, 0xd3, 0x66)),
                            ("Signal", (0x3a, 0x76, 0xf0))):
            trigger = self._trigger(app)
            shape, rest = notify.split_shape(trigger)
            self.assertEqual(notify.parse_color(rest), wanted, app)
            if shape is not None:
                self.assertIn(shape, notify.STYLES)


class BridgeTest(unittest.TestCase):
    """The loop, driven by a list of strings instead of by a bus."""

    def setUp(self):
        self.rules = phone.parse_rules("WhatsApp:#25d366:double_flash")
        self.sent = []

    def _bridge(self, **kwargs):
        return phone.Bridge(self.rules, send=self.sent.append, **kwargs)

    def test_it_flashes_once_per_notification_and_not_per_line(self):
        bridge = self._bridge().run(POSTED_LINES)
        self.assertEqual(bridge.seen, 3)
        self.assertEqual(bridge.flashed, 3)
        self.assertEqual([notify.split_tag(one)[1] for one in self.sent],
                         ["double_flash:#25d366", notify.KIND_PHONE,
                          notify.KIND_PHONE])

    def test_the_service_being_down_is_not_this_process_s_failure(self):
        # The bar not flashing is a shame; the bridge exiting would mean the
        # next notification does not flash either.
        def refuse(_trigger):
            raise OSError("no such file")

        bridge = phone.Bridge(self.rules, send=refuse)
        with self.assertLogs("steamos_led.phone", "WARNING") as caught:
            bridge.run(POSTED_LINES)
        self.assertEqual(bridge.seen, 3)
        self.assertEqual(bridge.flashed, 0)
        # And it says so, once per notification: silence here would look
        # exactly like a phone that had stopped sending anything.
        self.assertEqual(len(caught.output), 3)

    def test_the_dry_run_reports_everything_and_writes_nothing(self):
        told = []
        bridge = phone.Bridge(self.rules, send=None,
                              report=lambda seen, trigger: told.append(
                                  (seen.app, trigger)))
        bridge.run(POSTED_LINES)
        self.assertEqual([(app, notify.split_tag(one)[1]) for app, one in told],
                         [("com.whatsapp", "double_flash:#25d366"),
                          ("org.thoughtcrime.securesms", notify.KIND_PHONE),
                          ("com.android.calendar", notify.KIND_PHONE)])
        self.assertEqual(self.sent, [])

    def test_it_says_what_it_ignored_as_well_as_what_it_flashed(self):
        # A dry run that only mentioned the apps it liked would be no help at
        # all in working out why the others do nothing.
        told = []
        bridge = phone.Bridge(self.rules, send=None, listed_only=True,
                              report=lambda seen, trigger: told.append(trigger))
        bridge.run(POSTED_LINES)
        self.assertEqual([one if one is None else notify.split_tag(one)[1]
                          for one in told],
                         ["double_flash:#25d366", None, None])

    def test_the_lookup_reaches_the_rule_that_matches_on_it(self):
        # The whole point of the second call: without it this is "3", which
        # matches no rule anybody would write.
        rules = phone.parse_rules("WhatsApp:#25d366:double_flash")
        sent = []
        bridge = phone.Bridge(
            rules, send=sent.append,
            details=lambda seen: phone.read_details(COUNTED_DETAILS, seen))
        bridge.run([COUNTED_LINE])
        self.assertEqual([notify.split_tag(one)[1] for one in sent],
                         ["double_flash:#25d366"])

    def test_what_has_just_been_read_is_not_read_again(self):
        # A refresh asks the objects lately seen and hands the answers to the
        # same code an arriving signal goes through. Asking about an answer
        # would be one subprocess per notification per refresh, for nothing.
        asked = []

        def ask(seen):
            asked.append(seen.where)
            return phone.read_details(COUNTED_DETAILS, seen)

        bridge = phone.Bridge(self.rules, send=self.sent.append, details=ask)
        bridge.run([COUNTED_LINE])
        self.assertEqual(len(asked), 1)
        bridge.line(REFRESHED_LINE)
        self.assertEqual(len(asked), 2, "the refresh has to ask once")
        # And it said the same thing, so nothing flashed a second time.
        self.assertEqual(bridge.flashed, 1)

    def test_it_looks_up_from_the_stream_between_lines(self):
        """Because a quiet phone sends nothing for hours.

        A loop that only wakes on a line never wakes at all, and this is the
        process that has to notice KDE Connect going away underneath it -
        which is what Game Mode looks like from here, the session it started
        in having been replaced.
        """
        import os

        read_fd, write_fd = os.pipe()
        os.write(write_fd, (POSTED_LINES[2] + "\n").encode())
        stream = os.fdopen(read_fd)
        self.addCleanup(stream.close)
        self.addCleanup(os.close, write_fd)

        # The line first, then nothing - so the read succeeds once and then
        # times out, which is the silence being tested. Ended by the tick
        # itself rather than by closing the pipe, so it cannot hang.
        ticks = []

        def tick():
            ticks.append(1)
            if len(ticks) >= 3:
                raise KeyboardInterrupt

        bridge = phone.Bridge(self.rules, send=self.sent.append)
        with self.assertRaises(KeyboardInterrupt):
            bridge.watch(stream, tick, every=0.01)
        self.assertEqual(bridge.flashed, 1, "the line was missed")
        self.assertEqual(len(ticks), 3)

    def test_how_long_to_wait_is_asked_each_time_round(self):
        """So it can look often while something is wrong and seldom while not.

        A fixed interval has to be either wasteful or slow to notice. The
        minute that suits looking in on a healthy KDE Connect is a long time
        to sit with the phone trying to reconnect after a switch to Game Mode.
        """
        import os

        read_fd, write_fd = os.pipe()
        stream = os.fdopen(read_fd)
        self.addCleanup(stream.close)
        self.addCleanup(os.close, write_fd)

        asked = []

        def interval():
            asked.append(len(asked))
            if len(asked) >= 3:
                raise KeyboardInterrupt
            return 0.01

        bridge = phone.Bridge(self.rules, send=self.sent.append)
        with self.assertRaises(KeyboardInterrupt):
            bridge.watch(stream, lambda: None, interval)
        self.assertEqual(len(asked), 3, "it was asked once and remembered")

    def test_a_plain_number_still_works(self):
        self.assertGreater(phone.TICK_SECONDS, phone.EAGER_SECONDS,
                           "looking in on something healthy should be the "
                           "rarer of the two")

    def test_a_monitor_that_ends_ends_the_watch(self):
        # The bus going away with the session, which is the other thing that
        # can happen while nothing is being said.
        import os

        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        stream = os.fdopen(read_fd)
        self.addCleanup(stream.close)
        bridge = phone.Bridge(self.rules, send=self.sent.append)
        self.assertIs(bridge.watch(stream, every=0.01), bridge)

    def test_the_interval_is_read_when_it_is_used(self):
        # It was a default argument, which binds once at import - so a test
        # could not shorten it, and neither could anything else.
        self.assertGreater(phone.TICK_SECONDS, 0)

    def _conversation(self, bodies):
        """Six messages in one conversation, the way a phone really sends it.

        The first is posted; each one after it arrives as "ready" on a
        notification object of its own. Recorded rather than invented - this
        is the shape that had six messages coming out as one line.
        """
        saying = {}
        sent = []
        bridge = phone.Bridge(
            (), send=sent.append,
            details=lambda seen: phone.Sighting(
                "Notification Test", "ufnrhdhgkfihh",
                saying[seen.where], where=seen.where))

        for index, body in enumerate(bodies):
            where = "%s/%d" % (READY_PATH.rsplit("/", 1)[0], index)
            saying[where] = body
            bridge.line("%s: org.kde.kdeconnect.device.notifications."
                        "notification.ready ()" % where)
        return bridge, sent

    def test_every_message_of_a_conversation_is_its_own_flash(self):
        """The last layer of the reported bug, and the measured shape of it.

        Six messages came out as one line in the dry run, three times over,
        because the signal that carries the second one was being passed over.
        """
        bridge, sent = self._conversation(
            ["rjxhenfbg", "zweite", "dritte", "vierte", "fuenfte", "sechste"])
        self.assertEqual(bridge.flashed, 6)
        # All six reach the pipe. They carry the same tag because they are
        # one conversation, and what to do about that is the service's
        # business - see the repeat gap. The bug was that five of them never
        # got this far at all.
        self.assertEqual(len(set(sent)), 1, sent)

    def test_the_same_notification_saying_the_same_thing_is_not(self):
        # A notification is announced again when its neighbours change. The
        # service would drop it as a repeat anyway, but a dry run printing a
        # line for it would be describing an event that did not happen.
        bridge, sent = self._conversation(["one"])
        for _ in range(4):
            bridge.line(READY_LINE.replace("/6:", "/0:"))
        self.assertEqual(bridge.flashed, 1, sent)

    def test_only_so_many_notifications_are_kept_to_re_read(self):
        # A phone left alone for a day should not leave a list of everything
        # it ever sent, and only the recent ones can still say anything new.
        bridge, _sent = self._conversation(
            ["message %d" % index
             for index in range(phone.RECENT_NOTIFICATIONS * 2)])
        self.assertEqual(bridge.flashed, phone.RECENT_NOTIFICATIONS * 2,
                         "every one of them should still have flashed")
        self.assertLessEqual(len(bridge.recent), phone.RECENT_NOTIFICATIONS)

    def test_the_dry_run_names_a_signal_it_did_not_act_on(self):
        """Because the names differ between KDE Connect versions.

        A bridge that quietly ignores the one carrying the messages looks
        exactly like a phone that has stopped sending them - which is how
        this bug read for two rounds. Saying what was passed over turns the
        next report into an answer.
        """
        odd = COUNTED_LINE.replace("notificationPosted", "notificationTicker")
        told = []
        bridge = phone.Bridge((), send=None,
                              report=lambda seen, _t: told.append(seen.app))
        bridge.run([odd, odd, odd])
        self.assertEqual(len(told), 1, "said once, not once a line")
        # The whole line, because twice now a signal this passed over turned
        # out to be the one carrying the messages, and what was wanted next
        # was its object and its arguments.
        self.assertIn("notificationTicker", told[0])
        self.assertIn("/modules/kdeconnect/devices/d33f", told[0])

    def test_the_ones_it_knows_about_are_not_worth_mentioning(self):
        # Arrivals are acted on and removals are meant to be ignored; neither
        # is news, and a dry run full of them would bury the notifications.
        told = []
        bridge = phone.Bridge((), send=None,
                              details=lambda seen: seen,
                              report=lambda seen, _t: told.append(seen.app))
        bridge.run([COUNTED_LINE.replace("notificationPosted", member)
                    for member in phone.KDECONNECT_GONE])
        self.assertEqual(told, [])

    def test_nothing_is_said_about_it_outside_a_dry_run(self):
        # The journal is for what happened, not for a survey of the bus.
        odd = COUNTED_LINE.replace("notificationPosted", "notificationTicker")
        bridge = phone.Bridge((), send=self.sent.append)
        bridge.run([odd])
        self.assertEqual(self.sent, [])

    def test_a_notification_describes_itself_for_the_dry_run(self):
        seen = phone.read_details(COUNTED_DETAILS, phone.read_line(COUNTED_LINE))
        self.assertEqual(seen.describe(), "WhatsApp: Anna - bist du da?")
        self.assertEqual(phone.Sighting("com.whatsapp", "", "").describe(),
                         "com.whatsapp")


class ObstacleTest(unittest.TestCase):
    """Why the bar stayed dark although every line looked right.

    Reported from a real machine: --print read the notifications perfectly,
    named the app, named the trigger - and nothing lit, because the feature
    was still switched off. The dry run has to read the bus whether or not it
    is on, or you could never look before committing; so it says instead.
    """

    def test_nothing_in_the_way_is_nothing_to_say(self):
        self.assertEqual(phone.obstacles(True, True, True), [])

    def test_the_switch_being_off_is_the_first_thing_to_hear(self):
        said = phone.obstacles(True, False, True)
        self.assertEqual(len(said), 1)
        self.assertIn("NOTIFY_PHONE", said[0])
        # And what to do about it, not only what is wrong.
        self.assertIn("systemctl --user restart steamos-led-phone", said[0])

    def test_the_master_switch_is_mentioned_before_the_one_under_it(self):
        # Turning NOTIFY_PHONE on while NOTIFY is off changes nothing, so
        # being told about the inner switch first would send you round twice.
        said = phone.obstacles(False, False, True)
        self.assertIn("NOTIFY is off", said[0])
        self.assertIn("NOTIFY_PHONE", said[1])

    def test_a_service_that_is_not_listening_is_worth_saying_too(self):
        said = phone.obstacles(True, True, False)
        self.assertEqual(len(said), 1)
        self.assertIn("steamos-led-serial", said[0])

    def test_every_complaint_names_something_to_run_or_press(self):
        for said in phone.obstacles(False, False, False):
            self.assertTrue(said.endswith(".") or ":" in said, said)


class ConfigurationTest(unittest.TestCase):
    """The settings this adds, and what the service will not accept."""

    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.stale = holder.name

    def _config(self, **overrides):
        settings = dict(config_module.DEFAULTS)
        settings.update(overrides)
        return settings

    def test_it_is_off_until_somebody_switches_it_on(self):
        # It is the one notification that needs a phone paired outside this
        # machine, so it cannot be on out of the box.
        self.assertFalse(config_module.DEFAULTS["NOTIFY_PHONE"])

    def test_the_phone_is_a_kind_the_service_can_be_told_about(self):
        self.assertIn((notify.KIND_PHONE, "PHONE"),
                      config_module.CONFIGURABLE_KINDS)
        self.assertIn(notify.KIND_PHONE, notify.KINDS)

    def test_its_colour_is_not_one_of_the_others(self):
        # Four notifications that look alike are four notifications you have
        # to read the log to tell apart.
        colours = [notify.KINDS[kind] for kind in notify.KINDS]
        self.assertEqual(len(set(colours)), len(colours))

    def test_the_withdrawn_source_setting_does_not_stop_the_service(self):
        """Because it is sitting in every config file already installed.

        PHONE_SOURCE chose between KDE Connect and the desktop's own
        notification bus, and there is only the one source now. An unknown
        key is fatal on purpose - LED_COUTN=60 must not be ignored - but a
        key *we* withdrew is not the reader's mistake, and a service that
        refused to start over one would be this change breaking every machine
        that had the feature working.
        """
        self.assertIn("PHONE_SOURCE", config_module.RETIRED)
        self.assertNotIn("PHONE_SOURCE", config_module.DEFAULTS)
        path = os.path.join(self.stale, "steamos-led-serial.conf")
        with open(path, "w") as handle:
            handle.write("NOTIFY_PHONE=1\nPHONE_SOURCE=auto\n")
        with self.assertLogs("steamos_led.config", "WARNING"):
            settings = config_module.load(path)
        self.assertTrue(settings["NOTIFY_PHONE"], "the rest of it still read")
        # And the panel takes the line out the next time it saves, or every
        # start would log the same complaint forever.
        with open(path) as handle:
            self.assertNotIn("PHONE_SOURCE",
                             config_module.update_text(handle.read(), settings))

    def test_a_broken_app_list_is_refused_at_load_rather_than_at_flash_time(self):
        # Otherwise the mistake surfaces as "it just does not flash", hours
        # later, with nothing to search for.
        with self.assertRaises(config_module.ConfigError) as caught:
            config_module.validate(self._config(PHONE_APPS="WhatsApp:pink"))
        self.assertIn("PHONE_APPS", str(caught.exception))

    def test_a_good_app_list_passes(self):
        config_module.validate(self._config(
            PHONE_APPS="WhatsApp:#25d366:double_flash, Signal:#3a76f0"))

    def test_the_shipped_file_names_every_phone_setting(self):
        # A setting nobody can find is a setting nobody uses, and this file is
        # where people look before they find the panel.
        path = os.path.join(HERE, "..", "server", "steamos-led-serial.conf")
        with open(path) as handle:
            text = handle.read()
        for key in ("NOTIFY_PHONE", "PHONE_COLOR", "PHONE_STYLE",
                    "PHONE_APPS", "PHONE_APPS_ONLY"):
            self.assertIn("\n%s=" % key, text, key)
        # And nothing it no longer accepts: a shipped file that sets a
        # withdrawn option would log a complaint on every fresh install.
        for key in config_module.RETIRED:
            self.assertNotIn("\n%s=" % key, text, key)


class EndToEndTest(unittest.TestCase):
    """A line off the bus, through the pipe, to a lit strip."""

    def _overlay(self, **overrides):
        from steamos_led import service
        settings = dict(config_module.DEFAULTS)
        settings.update(overrides)
        return notify.NotificationOverlay(
            duration=settings["NOTIFY_DURATION"], led_count=17,
            style=settings["NOTIFY_STYLE"],
            colors=service.notification_colors(settings),
            styles=service.notification_styles(settings))

    def _lit(self, overlay, trigger):
        """The brightest pixel of the whole flash.

        Walked rather than sampled at one moment: every shape spends most of
        its time part-way, and the bloom's breath dips to 8% in the middle -
        so a single reading says more about when it was taken than about
        which colour was flashed.
        """
        overlay.trigger(trigger, 0.0)
        brightest = (0, 0, 0)
        for tick in range(60):
            frame = overlay.frame(overlay.duration * tick / 60.0)
            if frame is None:
                continue
            for led in range(17):
                pixel = tuple(frame[led * 3:led * 3 + 3])
                if sum(pixel) > sum(brightest):
                    brightest = pixel
        return brightest

    def test_an_unlisted_app_flashes_what_the_panel_set(self):
        overlay = self._overlay(PHONE_COLOR="#00b0ff")
        trigger = phone.trigger_for(phone.Sighting("Kalender", "", ""), ())
        red, green, blue = self._lit(overlay, trigger)
        self.assertEqual((red > blue, green > blue), (False, False))
        self.assertGreater(blue, 100)

    def test_a_listed_app_flashes_its_own_colour_instead(self):
        overlay = self._overlay(PHONE_COLOR="#00b0ff")
        rules = phone.parse_rules("WhatsApp:#25d366")
        trigger = phone.trigger_for(phone.Sighting("WhatsApp", "", ""), rules)
        red, green, blue = self._lit(overlay, trigger)
        self.assertGreater(green, red)
        self.assertGreater(green, blue)

    def test_a_listed_app_s_shape_reaches_the_strip(self):
        # Two shapes differ at some point in the flash or one of them is not
        # being applied - which is the failure this whole path exists to avoid.
        rules = phone.parse_rules("WhatsApp:#25d366:comet")
        trigger = phone.trigger_for(phone.Sighting("WhatsApp", "", ""), rules)
        self.assertTrue(trigger.startswith("comet:"))
        shaped, plain = self._overlay(), self._overlay()
        shaped.trigger(trigger, 0.0)
        plain.trigger("#25d366", 0.0)
        self.assertNotEqual(shaped.frame(0.4), plain.frame(0.4))


class UnitFileTest(unittest.TestCase):
    """The user unit, and the exit codes it promises not to restart over."""

    def setUp(self):
        path = os.path.join(HERE, "..", "server", "steamos-led-phone.service")
        with open(path) as handle:
            self.text = handle.read()

    def test_it_runs_the_mode_it_says_it_does(self):
        self.assertIn("--watch-phone", self.text)
        self.assertIn("@INSTALL_DIR@/steamos-led-serial", self.text)

    def test_the_answers_it_gives_are_not_treated_as_failures(self):
        # "NOTIFY_PHONE is off" and "there is no gdbus here" are both settled
        # facts; restarting every ten seconds would fill the journal with
        # them and never change either.
        from steamos_led import service
        for code in (service.NOTHING_TO_WATCH_EXIT,
                     service.MONITOR_MISSING_EXIT):
            self.assertRegex(self.text,
                             r"RestartPreventExitStatus=.*\b%d\b" % code)
            self.assertRegex(self.text, r"SuccessExitStatus=.*\b%d\b" % code)

    def _directives(self):
        """The lines that are settings, so a comment saying "PartOf=" is not
        mistaken for one - which the unit's does, explaining why it has none."""
        return [line.strip() for line in self.text.splitlines()
                if line.strip() and not line.strip().startswith("#")]

    def test_it_survives_the_session_going_away(self):
        # Same reason as the achievement watcher: PartOf= would stop it when
        # the desktop session ends, and Game Mode never brings that back.
        self.assertEqual([line for line in self._directives()
                          if line.startswith("PartOf=")], [])
        self.assertIn("Restart=always", self._directives())

    def test_it_is_enabled_where_the_installer_writes_the_symlink(self):
        wants = shell_value("WATCHER_WANTS").replace(".wants", "")
        self.assertIn("WantedBy=%s" % wants, self.text)


class InstallerTest(unittest.TestCase):
    """Both user units are installed and removed by the same walk."""

    def test_every_unit_the_scripts_know_about_exists(self):
        for unit in shell_value("WATCHER_UNITS"):
            path = os.path.join(HERE, "..", "server", unit)
            self.assertTrue(os.path.exists(path), unit)

    def test_the_phone_bridge_is_one_of_them(self):
        # Installed but not started by anything until NOTIFY_PHONE goes on,
        # which is what the unit's own exit code 3 is for.
        self.assertIn("steamos-led-phone.service",
                      shell_value("WATCHER_UNITS"))

    def test_the_installer_and_the_uninstaller_walk_the_same_list(self):
        # The failure this prevents is quiet: a unit installed by one script
        # and unknown to the other is a file left in somebody's ~/.config
        # with nothing to remove it.
        for name in ("install.sh", "uninstall.sh"):
            with open(os.path.join(HERE, "..", name)) as handle:
                self.assertIn("WATCHER_UNITS[@]", handle.read(), name)


if __name__ == "__main__":
    unittest.main()
