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
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_led import config as config_module      # noqa: E402
from steamos_led import notify, phone                # noqa: E402
from shellvalues import shell_value                  # noqa: E402

# What the two buses put out, recorded rather than invented. The desktop one
# is the freedesktop specification's own argument order; the KDE Connect one
# carries an Android notification key and nothing else.
DESKTOP_LINES = [
    "Monitoring signals from all objects owned by org.freedesktop.Notifications",
    "The name org.freedesktop.Notifications is owned by :1.42",
    "/org/freedesktop/Notifications: org.freedesktop.Notifications.Notify "
    "('WhatsApp', uint32 0, 'kdeconnect', 'Anna', 'Bist du da?', @as [], "
    "{'x-kde-eventId': <'a-b-c'>}, int32 -1)",
    "/org/freedesktop/Notifications: org.freedesktop.Notifications.Notify "
    "('Signal', uint32 0, '', 'Bob', 'yo', @as [], @a{sv} {}, int32 -1)",
    "/org/freedesktop/Notifications: org.freedesktop.Notifications.Notify "
    "('Discord', uint32 0, '', 'guild', 'ping', @as [], @a{sv} {}, int32 -1)",
]

KDECONNECT_LINES = [
    "Monitoring signals from all objects owned by org.kde.kdeconnect",
    "/modules/kdeconnect/devices/d33f/notifications: "
    "org.kde.kdeconnect.device.notifications.notificationPosted "
    "('0|com.whatsapp|1|null|10123',)",
    "/modules/kdeconnect/devices/d33f/notifications: "
    "org.kde.kdeconnect.device.notifications.notificationRemoved "
    "('0|com.whatsapp|1|null|10123',)",
]

# And what a Steam Machine actually sent, which is not the same thing: the id
# is KDE Connect's own counter, and everything worth knowing is a property of
# the object it names.
COUNTED_LINE = ("/modules/kdeconnect/devices/d33f/notifications: "
                "org.kde.kdeconnect.device.notifications.notificationPosted "
                "('3',)")

COUNTED_DETAILS = (
    "({'id': <'3'>, 'appName': <'WhatsApp'>, 'ticker': <'Anna: bist du da?'>,"
    " 'title': <'Anna'>, 'text': <'bist du da?'>, 'dismissable': <true>,"
    " 'hasIcon': <true>, 'silent': <false>},)\n")


class ReadingTheBusTest(unittest.TestCase):
    """One line of monitor output into "an app said something", or nothing."""

    def _sightings(self, lines, source):
        seen = [phone.read_line(line, source) for line in lines]
        return [one for one in seen if one is not None]

    def test_a_desktop_notification_carries_its_app_and_its_words(self):
        first = self._sightings(DESKTOP_LINES, phone.SOURCE_DESKTOP)[0]
        self.assertEqual(first.app, "WhatsApp")
        self.assertEqual(first.title, "Anna")
        self.assertEqual(first.body, "Bist du da?")

    def test_gdbus_own_chatter_is_not_a_notification(self):
        # It opens with two lines about what it is monitoring, and a machine
        # that flashed at those would flash once at every restart.
        self.assertEqual(len(self._sightings(DESKTOP_LINES,
                                             phone.SOURCE_DESKTOP)), 3)

    def test_a_notification_going_away_is_not_a_new_one(self):
        # notificationRemoved arrives for every notification that is dismissed,
        # so reading it as an arrival would flash twice for one message - the
        # second time when you picked the phone up.
        seen = self._sightings(KDECONNECT_LINES, phone.SOURCE_KDECONNECT)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].app, "com.whatsapp")

    def test_an_id_that_is_only_a_number_says_where_to_ask(self):
        # What a real machine sent: KDE Connect's own counter, with the app
        # kept as a property of an object of its own. Reading the id as the
        # app was this bridge's first mistake, and it printed "3 -> phone".
        seen = phone.read_line(COUNTED_LINE, phone.SOURCE_KDECONNECT)
        self.assertEqual(seen.app, "3")
        self.assertEqual(seen.where,
                         "/modules/kdeconnect/devices/d33f/notifications/3")

    def test_the_device_is_read_off_the_signal_rather_than_assumed(self):
        # More than one phone can be paired, and only one of them sent this.
        line = COUNTED_LINE.replace("d33f", "0ther")
        self.assertIn("/devices/0ther/",
                      phone.read_line(line, phone.SOURCE_KDECONNECT).where)

    def test_the_desktop_bus_needs_nothing_asked(self):
        # Which is why it is the fallback that always works.
        for seen in self._sightings(DESKTOP_LINES, phone.SOURCE_DESKTOP):
            self.assertEqual(seen.where, "")

    def test_the_two_sources_do_not_read_each_other_s_lines(self):
        self.assertEqual(self._sightings(KDECONNECT_LINES,
                                         phone.SOURCE_DESKTOP), [])
        self.assertEqual(self._sightings(DESKTOP_LINES,
                                         phone.SOURCE_KDECONNECT), [])

    def test_an_app_name_with_a_quote_in_it_survives(self):
        line = ("/org/freedesktop/Notifications: "
                "org.freedesktop.Notifications.Notify ('Bob\\'s app', uint32 0,"
                " '', 'hi', 'there', @as [], @a{sv} {}, int32 -1)")
        seen = phone.read_line(line, phone.SOURCE_DESKTOP)
        self.assertEqual(seen.app, "Bob's app")

    def test_a_comma_in_the_message_does_not_shift_the_arguments(self):
        # The arguments are split on commas, so a message containing one is
        # exactly the case that would read the body as the timeout.
        line = ("/org/freedesktop/Notifications: "
                "org.freedesktop.Notifications.Notify ('WhatsApp', uint32 0, "
                "'', 'Anna', 'ja, gleich', @as [], @a{sv} {}, int32 -1)")
        seen = phone.read_line(line, phone.SOURCE_DESKTOP)
        self.assertEqual(seen.app, "WhatsApp")
        self.assertEqual(seen.body, "ja, gleich")

    def test_a_truncated_line_is_dropped_rather_than_guessed_at(self):
        for line in ("", "nonsense",
                     "/x: org.freedesktop.Notifications.Notify ('only-one')",
                     "/x: org.freedesktop.Notifications.Notify ("):
            self.assertIsNone(phone.read_line(line, phone.SOURCE_DESKTOP),
                              line)

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
        self.seen = phone.read_line(COUNTED_LINE, phone.SOURCE_KDECONNECT)

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
        seen = phone.read_line(KDECONNECT_LINES[1], phone.SOURCE_KDECONNECT)
        self.assertEqual(seen.app, "com.whatsapp")


class SourceTest(unittest.TestCase):
    def test_automatic_prefers_the_phone_only_bus(self):
        names = "('org.freedesktop.DBus', 'org.kde.kdeconnect', ':1.7')"
        self.assertEqual(phone.pick_source(phone.SOURCE_AUTO, names),
                         phone.SOURCE_KDECONNECT)

    def test_automatic_falls_back_to_the_desktop_s_own(self):
        self.assertEqual(phone.pick_source(phone.SOURCE_AUTO, "('org.kde.kwin',)"),
                         phone.SOURCE_DESKTOP)

    def test_a_kde_connect_that_is_only_activatable_still_counts(self):
        """Which is the whole of Game Mode.

        There is no desktop session there to autostart KDE Connect, so it is
        not on the bus - but it is installed, the bus can start it, and one
        call does. The other source cannot help there at all: it reads the
        notification daemon, and Game Mode has not got one either.
        """
        running = "('org.freedesktop.DBus', 'org.freedesktop.systemd1')"
        can_start = "('org.freedesktop.DBus', 'org.kde.kdeconnect')"
        self.assertEqual(
            phone.pick_source(phone.SOURCE_AUTO, running, can_start),
            phone.SOURCE_KDECONNECT)

    def test_with_neither_it_still_falls_back(self):
        self.assertEqual(
            phone.pick_source(phone.SOURCE_AUTO, "('org.kde.kwin',)", "()"),
            phone.SOURCE_DESKTOP)

    def test_the_two_questions_are_different_questions(self):
        # Who is running, and who the bus would start if asked. Asking the
        # first one twice would report Game Mode as having nothing at all.
        running = phone.names_command()
        startable = phone.names_command(activatable=True)
        self.assertNotEqual(running, startable)
        self.assertIn("org.freedesktop.DBus.ListNames", running)
        self.assertIn("org.freedesktop.DBus.ListActivatableNames", startable)

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

    def test_waking_it_asks_something_that_changes_nothing(self):
        # Any call starts an activatable service, so the cheapest harmless
        # one is the right one - this must never be a call that does
        # something to somebody's phone.
        command = phone.wake_command()
        self.assertIn(phone.KDECONNECT_SERVICE, command)
        self.assertTrue(any(part.endswith("deviceNames") for part in command),
                        command)

    def test_naming_one_is_an_instruction_rather_than_a_suggestion(self):
        # Including the case where the other one is right there on the bus.
        names = "('org.kde.kdeconnect',)"
        self.assertEqual(phone.pick_source(phone.SOURCE_DESKTOP, names),
                         phone.SOURCE_DESKTOP)

    def test_every_source_has_a_bus_to_read(self):
        for source in (phone.SOURCE_KDECONNECT, phone.SOURCE_DESKTOP):
            command = phone.monitor_command(source)
            self.assertEqual(command[:3], ["gdbus", "monitor", "--session"])
            self.assertIn("--dest", command)


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
        return phone.trigger_for(phone.Sighting(app, "", ""), self.rules,
                                 **kwargs)

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
        return phone.Bridge(phone.SOURCE_DESKTOP, self.rules,
                            send=self.sent.append, **kwargs)

    def test_it_flashes_once_per_notification_and_not_per_line(self):
        bridge = self._bridge().run(DESKTOP_LINES)
        self.assertEqual(bridge.seen, 3)
        self.assertEqual(bridge.flashed, 3)
        self.assertEqual(self.sent,
                         ["double_flash:#25d366", notify.KIND_PHONE,
                          notify.KIND_PHONE])

    def test_the_service_being_down_is_not_this_process_s_failure(self):
        # The bar not flashing is a shame; the bridge exiting would mean the
        # next notification does not flash either.
        def refuse(_trigger):
            raise OSError("no such file")

        bridge = phone.Bridge(phone.SOURCE_DESKTOP, self.rules, send=refuse)
        with self.assertLogs("steamos_led.phone", "WARNING") as caught:
            bridge.run(DESKTOP_LINES)
        self.assertEqual(bridge.seen, 3)
        self.assertEqual(bridge.flashed, 0)
        # And it says so, once per notification: silence here would look
        # exactly like a phone that had stopped sending anything.
        self.assertEqual(len(caught.output), 3)

    def test_the_dry_run_reports_everything_and_writes_nothing(self):
        told = []
        bridge = phone.Bridge(phone.SOURCE_DESKTOP, self.rules, send=None,
                              report=lambda seen, trigger: told.append(
                                  (seen.app, trigger)))
        bridge.run(DESKTOP_LINES)
        self.assertEqual(told, [("WhatsApp", "double_flash:#25d366"),
                                ("Signal", notify.KIND_PHONE),
                                ("Discord", notify.KIND_PHONE)])
        self.assertEqual(self.sent, [])

    def test_it_says_what_it_ignored_as_well_as_what_it_flashed(self):
        # A dry run that only mentioned the apps it liked would be no help at
        # all in working out why the others do nothing.
        told = []
        bridge = phone.Bridge(phone.SOURCE_DESKTOP, self.rules, send=None,
                              listed_only=True,
                              report=lambda seen, trigger: told.append(trigger))
        bridge.run(DESKTOP_LINES)
        self.assertEqual(told, ["double_flash:#25d366", None, None])

    def test_the_lookup_reaches_the_rule_that_matches_on_it(self):
        # The whole point of the second call: without it this is "3", which
        # matches no rule anybody would write.
        rules = phone.parse_rules("WhatsApp:#25d366:double_flash")
        sent = []
        bridge = phone.Bridge(
            phone.SOURCE_KDECONNECT, rules, send=sent.append,
            details=lambda seen: phone.read_details(COUNTED_DETAILS, seen))
        bridge.run([COUNTED_LINE])
        self.assertEqual(sent, ["double_flash:#25d366"])

    def test_it_is_only_asked_where_there_is_something_to_ask(self):
        # The desktop bus said everything already; a call per notification
        # there would be one subprocess for nothing.
        asked = []
        bridge = phone.Bridge(phone.SOURCE_DESKTOP, self.rules,
                              send=self.sent.append,
                              details=lambda seen: asked.append(seen) or seen)
        bridge.run(DESKTOP_LINES)
        self.assertEqual(asked, [])
        self.assertEqual(bridge.flashed, 3)

    def test_it_looks_up_from_the_stream_between_lines(self):
        """Because a quiet phone sends nothing for hours.

        A loop that only wakes on a line never wakes at all, and this is the
        process that has to notice KDE Connect going away underneath it -
        which is what Game Mode looks like from here, the session it started
        in having been replaced.
        """
        import os

        read_fd, write_fd = os.pipe()
        os.write(write_fd, (DESKTOP_LINES[2] + "\n").encode())
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

        bridge = phone.Bridge(phone.SOURCE_DESKTOP, self.rules,
                              send=self.sent.append)
        with self.assertRaises(KeyboardInterrupt):
            bridge.watch(stream, tick, every=0.01)
        self.assertEqual(bridge.flashed, 1, "the line was missed")
        self.assertEqual(len(ticks), 3)

    def test_a_monitor_that_ends_ends_the_watch(self):
        # The bus going away with the session, which is the other thing that
        # can happen while nothing is being said.
        import os

        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        stream = os.fdopen(read_fd)
        self.addCleanup(stream.close)
        bridge = phone.Bridge(phone.SOURCE_DESKTOP, self.rules,
                              send=self.sent.append)
        self.assertIs(bridge.watch(stream, every=0.01), bridge)

    def test_the_interval_is_read_when_it_is_used(self):
        # It was a default argument, which binds once at import - so a test
        # could not shorten it, and neither could anything else.
        self.assertGreater(phone.TICK_SECONDS, 0)

    def test_a_notification_describes_itself_for_the_dry_run(self):
        seen = phone.read_line(DESKTOP_LINES[2], phone.SOURCE_DESKTOP)
        self.assertEqual(seen.describe(), "WhatsApp: Anna - Bist du da?")
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

    def test_a_source_that_does_not_exist_is_refused(self):
        with self.assertRaises(config_module.ConfigError):
            config_module.validate(self._config(PHONE_SOURCE="carrier-pigeon"))

    def test_every_offered_source_is_accepted(self):
        for source in phone.SOURCES:
            config_module.validate(self._config(PHONE_SOURCE=source))

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
                    "PHONE_SOURCE", "PHONE_APPS", "PHONE_APPS_ONLY"):
            self.assertIn("\n%s=" % key, text, key)


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
