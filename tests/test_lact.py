# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Talking to LACT, tested against a socket rather than a mock.

No machine here runs lactd, and it is not needed: the protocol is one JSON
line in and one out over a unix socket, which a test can be on the other end
of. So the client is exercised for real - it connects, sends, reads a reply
that arrives in pieces, and handles a daemon that hangs up, refuses, or
answers rubbish.

Mocking the socket would have tested that the code calls the functions it
calls. What is interesting here is the other end.
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

from steamos_utility_center import lact                                 # noqa: E402


class FakeDaemon:
    """A unix socket that answers like lactd does.

    `answers` maps a command name to what its data should be; anything else
    gets an error response, which is what the real daemon does with a command
    it does not know.
    """

    def __init__(self, answers=None, hang=False, split=False, refuse=False):
        self.answers = answers or {}
        self.hang = hang                # accept and never reply
        self.split = split              # reply in two packets
        self.refuse = refuse            # answer status=error
        self.asked = []
        self.holder = tempfile.mkdtemp()
        self.path = os.path.join(self.holder, "lactd.sock")
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.path)
        self._server.listen(4)
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stop:
            try:
                link, _ = self._server.accept()
            except OSError:
                return
            with link:
                try:
                    self._answer(link)
                except OSError:                              # pragma: no cover
                    pass

    def _answer(self, link):
        link.settimeout(5)
        got = b""
        while b"\n" not in got:
            block = link.recv(4096)
            if not block:
                return
            got += block
        self.asked.append(json.loads(got.decode()))
        if self.hang:
            import time
            time.sleep(5)
            return
        name = self.asked[-1]["command"]
        if self.refuse:
            said = json.dumps({"status": "error",
                               "data": "no such device"}) + "\n"
        elif name in self.answers:
            said = json.dumps({"status": "ok",
                               "data": self.answers[name]}) + "\n"
        else:
            said = json.dumps({
                "status": "error",
                "data": "Failed to deserialize request: unknown variant"}) + "\n"
        raw = said.encode()
        if self.split:
            # The device list of a machine with two cards already exceeds one
            # packet. Read as if a recv were a message, the JSON is truncated
            # and the error blames the daemon.
            link.sendall(raw[:len(raw) // 2])
            import time
            time.sleep(0.05)
            link.sendall(raw[len(raw) // 2:])
        else:
            link.sendall(raw)

    def close(self):
        self._stop = True
        try:
            self._server.close()
        except OSError:                                      # pragma: no cover
            pass
        try:
            os.remove(self.path)
        except OSError:                                      # pragma: no cover
            pass
        try:
            os.rmdir(self.holder)
        except OSError:                                      # pragma: no cover
            pass


DEVICES = [{"id": "1002:163F-0000:04:00.0", "name": "VanGogh [AMD Custom GPU]"}]

STATS = {"power": {"cap_current": 15.0, "cap_max": 25.0, "cap_min": 4.0,
                   "cap_default": 15.0}}

CLOCKS = {
    "max_sclk": 1600, "max_mclk": 1400, "max_voltage": 1200,
    "table": {"type": "amd", "value": {
        "od_range": {"sclk": {"min": 200, "max": 1600},
                     "mclk": {"min": 400, "max": 1400},
                     "vddc": {"min": 700, "max": 1200}}}},
}

CONFIG = {"fan_control_enabled": False,
          "fan_control_settings": {"mode": "curve", "static_speed": 0.5,
                                   "temperature_key": "edge",
                                   "interval_ms": 500,
                                   "curve": {"40": 0.3, "80": 1.0}},
          "power_cap": 15.0,
          "clocks_configuration": {}}


# The same three documents off a newer card - an RX 9070 XT, recorded from
# its own daemon. Kept beside the older one rather than replacing it: the two
# shapes are both live, one on the Steam Deck's own iGPU and one on a card
# somebody put in a Steam Machine, and every rule here has to hold for both.
NEW_DEVICES = [{"id": "1002:7550-148C:2435-0000:03:00.0",
                "name": "AMD Radeon RX 9070 XT"}]

NEW_STATS = {"power": {"average": 6.0, "cap_current": 373.0, "cap_max": 374.0,
                       "cap_min": 212.0, "cap_default": 304.0}}

NEW_CLOCKS = {
    "max_mclk": 1259,
    "table": {"type": "amd", "value": {"kind": "rdna", "data": {
        "current_sclk_range": {"min": None, "max": None},
        "sclk_offset": 0,
        "current_mclk_range": {"min": 97, "max": 1259},
        "vddc_curve": [],
        "voltage_offset": -20,
        "od_range": {
            "sclk": None,
            "sclk_offset": {"min": -500, "max": 1000},
            "mclk": {"min": 97, "max": 1500},
            "curve_sclk_points": [],
            "curve_voltage_points": [],
            "voltage_offset": {"min": -200, "max": 0}}}}},
}

# And a third, from an RX 6400 - RDNA2, and the reason the voltage offset had
# to stop depending on a range. Its od_range is nothing but nulls: it publishes
# no window for anything, while reporting the offset it is set to and taking a
# new one. A knob gated on having a range is a knob this card never gets.
BARE_CLOCKS = {
    "table": {"type": "amd", "value": {"kind": "rdna", "data": {
        "current_sclk_range": {"min": None, "max": None},
        "sclk_offset": None,
        "current_mclk_range": {"min": None, "max": None},
        "vddc_curve": [],
        "voltage_offset": -50,
        "od_range": {
            "sclk": None,
            "sclk_offset": None,
            "mclk": None,
            "curve_sclk_points": [],
            "curve_voltage_points": [],
            "voltage_offset": None}}}},
}

BARE_CONFIG = {"fan_control_enabled": False, "performance_level": "auto",
               "voltage_offset": -50}

NEW_CONFIG = {
    "fan_control_enabled": False,
    "fan_control_settings": {"mode": "curve", "static_speed": 0.5,
                             "temperature_key": "edge", "interval_ms": 500,
                             "curve": {"40": 0.3, "80": 1.0}},
    "pmfw_options": {"acoustic_limit": 3500, "acoustic_target": 1500,
                     "minimum_pwm": 30, "target_temperature": 85,
                     "zero_rpm": True},
    "power_cap": 373.0,
    "performance_level": "auto",
    "voltage_offset": -20,
}


class DaemonTest(unittest.TestCase):
    """The client, against something on the other end of the socket."""

    def _daemon(self, **kwargs):
        made = FakeDaemon(**kwargs)
        self.addCleanup(made.close)
        return made

    def test_a_round_trip_comes_back_parsed(self):
        daemon = self._daemon(answers={"list_devices": DEVICES})
        self.assertEqual(lact.devices(daemon.path), DEVICES)
        self.assertEqual(daemon.asked[-1], {"command": "list_devices"})

    def test_a_command_with_arguments_sends_them_under_args(self):
        daemon = self._daemon(answers={"device_stats": STATS})
        lact.stats("gpu-1", daemon.path)
        self.assertEqual(daemon.asked[-1],
                         {"command": "device_stats", "args": {"id": "gpu-1"}})

    def test_a_command_without_arguments_sends_no_args_key(self):
        # The daemon rejects an empty args object on commands that take none.
        daemon = self._daemon(answers={"list_devices": DEVICES})
        lact.devices(daemon.path)
        self.assertNotIn("args", daemon.asked[-1])

    def test_an_answer_that_arrives_in_pieces_is_still_one_answer(self):
        """A recv is not a message.

        Two graphics cards already put the device list over one packet, and
        read as if a single recv were the whole reply, the JSON is truncated
        and the error blames the daemon for the client's mistake.
        """
        daemon = self._daemon(answers={"list_devices": DEVICES}, split=True)
        self.assertEqual(lact.devices(daemon.path), DEVICES)

    def test_a_daemon_that_refuses_says_what_it_said(self):
        daemon = self._daemon(refuse=True)
        with self.assertRaises(lact.LactError) as caught:
            lact.devices(daemon.path)
        self.assertIn("no such device", str(caught.exception))

    def test_a_command_it_does_not_know_is_an_error_not_an_empty_answer(self):
        daemon = self._daemon(answers={})
        with self.assertRaises(lact.LactError):
            lact.devices(daemon.path)

    def test_a_daemon_that_never_answers_times_out_rather_than_hanging(self):
        """The window's own thread is what calls this.

        A daemon mid-apply that never replies would otherwise freeze the
        panel, which is a worse failure than not showing the GPU page.
        """
        daemon = self._daemon(answers={}, hang=True)
        started = time.monotonic()
        with self.assertRaises(lact.LactError) as caught:
            lact.talk("list_devices", daemon.path, timeout=0.3)
        # The seconds, not just "did not answer" - which is also how the
        # not-JSON error begins, so the loose version of this passed with the
        # timeout taken out entirely: the socket blocked until the daemon hung
        # up, and an empty answer raised the other error.
        self.assertIn("within 0.3 seconds", str(caught.exception))
        self.assertLess(time.monotonic() - started, 2.0,
                        "it waited for the daemon rather than timing out")

    def test_no_socket_at_all_is_an_error_naming_the_path(self):
        with self.assertRaises(lact.LactError) as caught:
            lact.devices("/nonexistent/lactd.sock")
        self.assertIn("/nonexistent/lactd.sock", str(caught.exception))

    def test_available_is_a_file_test_and_not_a_connection(self):
        # Asked on every visit to the page, so it has to be cheap - and a
        # socket file outlives a daemon that was killed, which is why ping
        # exists and this is not the whole answer.
        daemon = self._daemon(answers={"ping": None})
        self.assertTrue(lact.available(daemon.path))
        self.assertFalse(lact.available("/nonexistent/lactd.sock"))

    def test_the_confirm_window_is_the_daemon_s_number(self):
        daemon = self._daemon(answers={"set_gpu_config": 9})
        self.assertEqual(lact.set_gpu_config("gpu-1", {}, daemon.path), 9)

    def test_an_answer_with_no_number_falls_back_rather_than_crashing(self):
        # Guessed low on purpose: too high leaves the window claiming a
        # setting is pending after the daemon has already put it back.
        daemon = self._daemon(answers={"set_gpu_config": None})
        self.assertEqual(lact.set_gpu_config("gpu-1", {}, daemon.path),
                         lact.CONFIRM_SECONDS)

    def test_keeping_and_reverting_are_the_same_command_with_two_words(self):
        daemon = self._daemon(answers={"confirm_pending_config": None})
        lact.confirm(daemon.path, keep=True)
        self.assertEqual(daemon.asked[-1]["args"], {"command": "confirm"})
        lact.confirm(daemon.path, keep=False)
        self.assertEqual(daemon.asked[-1]["args"], {"command": "revert"})


class SocketPathTest(unittest.TestCase):
    """Where it looks, and when it decides.

    Every function here takes `path=None` and falls back to SOCKET_PATH at
    call time rather than naming the constant in its signature. A default in a
    signature is bound once, when the module is imported - so with
    `path=SOCKET_PATH` the constant could not be changed afterwards by
    anything, and the change was accepted and ignored rather than refused.

    That is not only a test's problem. It cost a stack dump to find, because
    the panel's error path opens a modal dialog: the suite hung instead of
    failing.
    """

    def test_setting_the_constant_moves_where_it_looks(self):
        daemon = FakeDaemon(answers={"list_devices": DEVICES})
        self.addCleanup(daemon.close)
        was = lact.SOCKET_PATH
        lact.SOCKET_PATH = daemon.path
        self.addCleanup(lambda: setattr(lact, "SOCKET_PATH", was))
        # No path given: it has to pick up the constant as it is now.
        self.assertEqual(lact.devices(), DEVICES)
        self.assertTrue(lact.available())

    def test_a_path_given_outright_still_wins(self):
        daemon = FakeDaemon(answers={"list_devices": DEVICES})
        self.addCleanup(daemon.close)
        was = lact.SOCKET_PATH
        lact.SOCKET_PATH = "/nonexistent/lactd.sock"
        self.addCleanup(lambda: setattr(lact, "SOCKET_PATH", was))
        self.assertEqual(lact.devices(daemon.path), DEVICES)

    def test_every_entry_point_honours_it(self):
        # One left with a frozen default would be one call in the page that
        # quietly talks to the wrong socket - or to none.
        import inspect
        for name in ("available", "talk", "devices", "first_device", "stats",
                     "clocks_info", "gpu_config", "set_gpu_config", "confirm",
                     "profiles", "set_profile"):
            found = inspect.signature(getattr(lact, name))
            self.assertIsNone(found.parameters["path"].default,
                              "%s binds its socket path at import" % name)


class AnswerTest(unittest.TestCase):
    """Unwrapping, apart from the socket."""

    def test_ok_gives_the_data(self):
        self.assertEqual(
            lact.read_answer('{"status":"ok","data":{"a":1}}'), {"a": 1})

    def test_an_error_carries_the_daemon_s_own_words(self):
        with self.assertRaises(lact.LactError) as caught:
            lact.read_answer('{"status":"error","data":"overdrive disabled"}')
        self.assertIn("overdrive disabled", str(caught.exception))

    def test_output_that_is_not_json_is_an_error(self):
        with self.assertRaises(lact.LactError):
            lact.read_answer("thread 'main' panicked")

    def test_nothing_at_all_is_an_error_too(self):
        # What a daemon that hung up mid-answer leaves behind.
        with self.assertRaises(lact.LactError):
            lact.read_answer("")


class ProfileTest(unittest.TestCase):

    def _daemon(self, answer):
        made = FakeDaemon(answers={"list_profiles": answer})
        self.addCleanup(made.close)
        return made

    def test_a_document_naming_the_current_one_is_read(self):
        daemon = self._daemon({"profiles": ["quiet", "loud"],
                               "current_profile": "quiet"})
        self.assertEqual(lact.profiles(daemon.path), (["quiet", "loud"],
                                                      "quiet"))

    def test_a_bare_list_from_an_older_daemon_is_read_too(self):
        """Two shapes across versions, and neither is assumed.

        This is somebody else's daemon on somebody else's machine, updated on
        its own schedule - so the older shape is not a thing to drop support
        for on the day it stops being current.
        """
        daemon = self._daemon(["quiet", "loud"])
        self.assertEqual(lact.profiles(daemon.path), (["quiet", "loud"], ""))

    def test_the_default_profile_has_no_name_and_is_sent_as_none(self):
        daemon = FakeDaemon(answers={"set_profile": None})
        self.addCleanup(daemon.close)
        lact.set_profile("", daemon.path)
        self.assertNotIn("args", daemon.asked[-1])
        lact.set_profile("quiet", daemon.path)
        self.assertEqual(daemon.asked[-1]["args"], {"name": "quiet"})


class RangeTest(unittest.TestCase):
    """What a card will accept, read out of the daemon's own report."""

    def test_the_ranges_are_found_wherever_the_table_puts_them(self):
        """The table is a tagged union, one shape per vendor and generation.

        Tracking every one would be tracking somebody else's schema version by
        version, and being wrong means a slider that writes a clock the card
        refuses. So the ranges are looked for rather than read from a path.
        """
        found = lact.ranges(CLOCKS)
        self.assertEqual(found["sclk"], (200, 1600))
        self.assertEqual(found["mclk"], (400, 1400))
        self.assertEqual(found["vddc"], (700, 1200))

    def test_a_table_nested_deeper_is_still_found(self):
        found = lact.ranges({"table": {"type": "amd", "value": {"gcn": {
            "od_range": {"sclk": {"min": 300, "max": 2000}}}}}})
        self.assertEqual(found["sclk"], (300, 2000))

    def test_a_card_with_no_table_offers_nothing_rather_than_zero(self):
        # Which is most integrated graphics. A range of (0, 0) would be a
        # slider with no travel where there should be no slider.
        self.assertEqual(lact.ranges({}), {})
        self.assertEqual(lact.ranges({"table": None}), {})

    def test_a_range_that_is_not_a_range_is_ignored(self):
        for bad in ({"min": 100}, {"max": 100}, {"min": 100, "max": 100},
                    {"min": 500, "max": 100}, "nonsense", None):
            found = lact.ranges({"table": {"od_range": {"sclk": bad}}})
            self.assertNotIn("sclk", found, bad)

    def test_a_document_that_loops_does_not_hang_the_window(self):
        # The daemon's, not ours, and a window drawing a slider should not be
        # the thing that discovers a malformed one the hard way.
        looping = {"table": {}}
        looping["table"]["self"] = looping
        lact.ranges(looping)

    def test_the_plain_maxima_are_a_fallback_for_a_table_with_no_range(self):
        self.assertEqual(lact.ranges({"max_sclk": 2400})["sclk"], (0, 2400))

    def test_a_table_range_wins_over_the_plain_maximum(self):
        # The table is the specific answer; max_sclk is what LACT reports
        # beside it for cards whose table has nothing to say.
        self.assertEqual(lact.ranges(CLOCKS)["sclk"], (200, 1600))


class PowerRangeTest(unittest.TestCase):

    def test_the_watts_come_from_the_card_s_own_stats(self):
        found = lact.power_range(STATS)
        self.assertEqual((found["min"], found["max"]), (4.0, 25.0))
        self.assertEqual(found["current"], 15.0)

    def test_a_card_that_reports_no_cap_reports_none(self):
        for empty in ({}, {"power": {}}, None):
            self.assertIsNone(lact.power_range(empty)["max"])


class OfferedTest(unittest.TestCase):
    """Only the knobs this card actually has."""

    def test_a_card_is_offered_what_it_reports_and_not_the_whole_table(self):
        """No card has every knob, and two of them are alternatives.

        This one reports an absolute core-clock range and an absolute voltage
        range, which is the older AMD shape - so it gets a maximum GPU clock
        and the offset window either side of nothing, and no GPU clock
        offset, which is the thing newer cards report *instead* of that
        maximum.
        """
        found = lact.offered(CONFIG, CLOCKS, STATS)
        self.assertEqual([knob["key"] for knob in found],
                         [lact.POWER_CAP, "max_core_clock", "voltage_offset",
                          "max_memory_clock", "min_memory_clock"])

    def test_a_card_with_no_clocks_table_offers_only_the_power_limit(self):
        """Which is most integrated graphics, and the Steam Machine may be one.

        Drawing the clock sliders anyway would be offering settings that write
        nowhere and report success - the same rule the governor page follows:
        the machine is asked, not remembered.
        """
        found = lact.offered(CONFIG, {}, STATS)
        self.assertEqual([knob["key"] for knob in found], [lact.POWER_CAP])

    def test_a_card_with_no_power_cap_does_not_get_a_power_slider(self):
        found = lact.offered(CONFIG, CLOCKS, {})
        self.assertNotIn(lact.POWER_CAP, [knob["key"] for knob in found])

    def test_each_knob_carries_its_range_and_its_unit(self):
        for knob in lact.offered(CONFIG, CLOCKS, STATS):
            self.assertLess(knob["min"], knob["max"], knob["key"])
            self.assertTrue(knob["unit"], knob["key"])
            self.assertTrue(knob["label"], knob["key"])

    def test_a_knob_nobody_has_set_has_no_value_but_still_has_a_start(self):
        """Two questions, and the page needs both separately.

        `value` is "LACT has been told this"; `start` is where the slider
        goes when it has not been. Zero is a setting and "not set" is the
        card's own default, so on a voltage offset the two must not look the
        same - and a slider has to sit somewhere either way.
        """
        found = {knob["key"]: knob for knob in
                 lact.offered({}, CLOCKS, STATS)}
        self.assertIsNone(found["max_core_clock"]["value"])
        # The card's own maximum. At the bottom of the range it would draw an
        # untouched card as one clamped to its lowest clock.
        self.assertEqual(found["max_core_clock"]["start"], 1600)
        self.assertEqual(found["voltage_offset"]["start"], 0)
        # And the power cap, which the card reports whether or not LACT wrote
        # it, starts at what it is actually running.
        self.assertEqual(found[lact.POWER_CAP]["start"], 15.0)

    def test_a_knob_that_is_set_starts_where_it_is_set(self):
        found = {knob["key"]: knob for knob in
                 lact.offered({"clocks_configuration": {
                     "max_core_clock": 1200}}, CLOCKS, STATS)}
        self.assertEqual(found["max_core_clock"]["value"], 1200)
        self.assertEqual(found["max_core_clock"]["start"], 1200)


class FanTest(unittest.TestCase):

    def test_an_untouched_card_reads_as_firmware_driven(self):
        found = lact.fan(CONFIG)
        self.assertFalse(found["enabled"])

    def test_the_curve_comes_back_as_numbers(self):
        # LACT keys it by temperature as a string, which sorts as text: 100
        # would come before 40 in any list built from it directly.
        found = lact.fan(CONFIG)
        self.assertEqual(found["curve"], {40: 0.3, 80: 1.0})
        self.assertTrue(all(isinstance(at, int) for at in found["curve"]))

    def test_a_card_with_no_curve_gets_points_to_move_rather_than_a_blank(self):
        self.assertEqual(lact.fan({})["curve"], lact.STARTING_CURVE)

    def test_switching_it_on_keeps_everything_else(self):
        """set_gpu_config replaces the document rather than patching it.

        So anything dropped on the way through is a setting silently turned
        off on somebody's card - which is why every caller reads the current
        config and hands it back changed.
        """
        made = lact.with_fan(CONFIG, enabled=True)
        self.assertTrue(made["fan_control_enabled"])
        self.assertEqual(made["power_cap"], CONFIG["power_cap"])
        self.assertEqual(made["fan_control_settings"]["curve"],
                         CONFIG["fan_control_settings"]["curve"])

    def test_a_settings_block_always_has_the_keys_lact_requires(self):
        # One missing and the daemon refuses the whole document.
        made = lact.with_fan({}, enabled=True)
        for needed in ("mode", "static_speed", "temperature_key",
                       "interval_ms", "curve"):
            self.assertIn(needed, made["fan_control_settings"])

    def test_a_curve_goes_back_as_strings_in_order(self):
        made = lact.with_fan(CONFIG, curve={80: 1.0, 40: 0.25, 60: 0.5})
        curve = made["fan_control_settings"]["curve"]
        self.assertEqual(list(curve), ["40", "60", "80"])
        self.assertEqual(curve["40"], 0.25)

    def test_a_speed_outside_the_range_is_brought_back_into_it(self):
        # The daemon takes a fraction. A slider that sent 150% would be
        # refused, and one that sent -1 is a fan told to stop.
        for asked, wanted in ((1.5, 1.0), (-0.2, 0.0), (0.4, 0.4)):
            made = lact.with_fan(CONFIG, static_speed=asked)
            self.assertEqual(made["fan_control_settings"]["static_speed"],
                             wanted)

    def test_a_curve_point_outside_the_range_is_too(self):
        made = lact.with_fan(CONFIG, curve={40: 2.0, 80: -1.0})
        self.assertEqual(made["fan_control_settings"]["curve"],
                         {"40": 1.0, "80": 0.0})

    def test_both_modes_are_the_toolkit_s_own_words(self):
        for mode in (lact.FAN_STATIC, lact.FAN_CURVE):
            made = lact.with_fan(CONFIG, mode=mode)
            self.assertEqual(made["fan_control_settings"]["mode"], mode)


# What an RDNA3+ card reports beside its fan stats. Older cards report none of
# it: the daemon reads each out of sysfs and keeps the ones whose file exists.
RDNA3_FAN = {"fan": {"pmfw_info": {
    "zero_rpm_enable": True,
    "zero_rpm_temperature": {"current": 50, "allowed_range": [0, 100]},
    "target_temp": {"current": 83, "allowed_range": [25, 105]},
    "acoustic_limit": {"current": 3200, "allowed_range": [500, 3200]},
    "acoustic_target": {"current": 1450, "allowed_range": [500, 3200]},
    "minimum_pwm": {"current": 15, "allowed_range": [0, 100]},
}}}


class FirmwareFanTest(unittest.TestCase):
    """The settings that live in the card's firmware, RDNA3 and newer."""

    def test_an_older_card_reports_none_of_it_and_gets_nothing(self):
        """Which is the whole detection, and it is upstream's own.

        The daemon reads each of these out of sysfs and keeps the ones whose
        file exists, so a 6000-series card reports an empty block. Drawing
        them anyway would be six controls that write nowhere.
        """
        for empty in ({}, {"fan": {}}, {"fan": {"pmfw_info": {}}}, None):
            self.assertEqual(lact.firmware(empty), [], empty)

    def test_a_newer_card_gets_all_six(self):
        found = lact.firmware(RDNA3_FAN)
        self.assertEqual(len(found), len(lact.FIRMWARE))

    def test_zero_rpm_is_a_switch_and_the_rest_are_numbers(self):
        found = {one["key"]: one for one in lact.firmware(RDNA3_FAN)}
        self.assertTrue(found["zero_rpm"]["switch"])
        self.assertTrue(found["zero_rpm"]["value"])
        self.assertFalse(found["target_temperature"]["switch"])

    def test_each_number_carries_the_range_the_card_allows(self):
        found = {one["key"]: one for one in lact.firmware(RDNA3_FAN)}
        self.assertEqual((found["acoustic_limit"]["min"],
                          found["acoustic_limit"]["max"]), (500, 3200))
        self.assertEqual(found["acoustic_limit"]["value"], 3200)

    def test_a_card_reporting_only_some_of_them_gets_only_those(self):
        # Not all of RDNA3 exposes all six, and a partial answer is the
        # ordinary case rather than a broken one.
        found = lact.firmware({"fan": {"pmfw_info": {
            "zero_rpm_enable": False,
            "target_temp": {"current": 83, "allowed_range": [25, 105]}}}})
        self.assertEqual([one["key"] for one in found],
                         ["zero_rpm", "target_temperature"])

    def test_a_value_with_no_range_gets_one_from_nothing_up_to_itself(self):
        # What upstream falls back to, so the two windows agree.
        found = lact.firmware({"fan": {"pmfw_info": {
            "acoustic_limit": {"current": 3000}}}})
        self.assertEqual((found[0]["min"], found[0]["max"]), (0, 3000))

    def test_a_range_that_cannot_be_a_range_is_left_out(self):
        # Nothing to move a slider through, so nothing to draw. A value of
        # zero with no range is the case that matters: 0 to 0 is not a range.
        for bad in ({"current": 0}, {"current": 50, "allowed_range": [80, 20]},
                    "nonsense", 5):
            found = lact.firmware({"fan": {"pmfw_info": {
                "acoustic_limit": bad}}})
            self.assertEqual(found, [], bad)

    def test_a_range_of_the_wrong_shape_falls_back_like_a_missing_one(self):
        """Rather than dropping the control.

        The value is real either way - only the range is not what was
        expected - and 0 up to where it is now is both upstream's own fallback
        and a range somebody can use. Refusing would hide a setting the card
        genuinely has because of a field this panel misread.
        """
        found = lact.firmware({"fan": {"pmfw_info": {
            "acoustic_limit": {"current": 50, "allowed_range": "nonsense"}}}})
        self.assertEqual((found[0]["min"], found[0]["max"]), (0, 50))

    def test_it_is_reported_whether_or_not_lact_drives_the_fan(self):
        """These are the firmware's settings, not LACT's control loop.

        They apply while the card is looking after its own fan, which is the
        state most people leave it in - so gating them on fan control being
        switched on would hide them from exactly the people they are for.
        """
        self.assertTrue(lact.firmware(RDNA3_FAN))

    def test_the_two_names_for_each_setting_are_kept_apart(self):
        """Four of the six are reported under one name and written under
        another, which is the thing to get wrong here.
        """
        reported = {key for key, _w, _l, _u in lact.FIRMWARE}
        written = {writes for _k, writes, _l, _u in lact.FIRMWARE}
        self.assertIn("zero_rpm_enable", reported)
        self.assertIn("zero_rpm", written)
        self.assertNotIn("zero_rpm_enable", written)
        self.assertEqual(len(written), len(lact.FIRMWARE),
                         "two settings write to the same key")

    def test_every_setting_we_name_is_one_a_card_actually_reports(self):
        # LACT is not vendored, so there is no schema here to read the names
        # off. What can be checked is that the recorded shape of a real card's
        # answer covers every one of them - a name invented here would show up
        # as a control that never appears on any hardware.
        for key, _writes, _label, _unit in lact.FIRMWARE:
            self.assertIn(key, RDNA3_FAN["fan"]["pmfw_info"], key)

    def test_each_one_is_labelled_and_carries_its_unit(self):
        for key, writes, label, unit in lact.FIRMWARE:
            self.assertTrue(label.strip(), key)
            self.assertNotEqual(label, writes, key)
            # Zero RPM is a switch and has no unit; everything else does.
            self.assertEqual(bool(unit), writes != "zero_rpm", key)


class FirmwareWritingTest(unittest.TestCase):

    def test_they_go_into_their_own_block(self):
        """A third place, neither the top level nor the fan settings.

        Which is the only reason this function exists: everything else in the
        page can then write a firmware setting without knowing where it lives.
        """
        made = lact.with_firmware(CONFIG, {"zero_rpm": True,
                                           "acoustic_limit": 2000})
        self.assertEqual(made[lact.FIRMWARE_CONFIG],
                         {"zero_rpm": True, "acoustic_limit": 2000})
        self.assertNotIn("zero_rpm", made)

    def test_the_rest_of_the_config_survives(self):
        made = lact.with_firmware(CONFIG, {"zero_rpm": True})
        self.assertEqual(made["fan_control_settings"],
                         CONFIG["fan_control_settings"])
        self.assertEqual(made["power_cap"], CONFIG["power_cap"])

    def test_a_switch_stays_a_boolean_and_a_number_becomes_one(self):
        # The daemon refuses the document if a boolean field carries 1.
        made = lact.with_firmware({}, {"zero_rpm": True,
                                       "acoustic_limit": 2000.7})
        self.assertIs(made[lact.FIRMWARE_CONFIG]["zero_rpm"], True)
        self.assertEqual(made[lact.FIRMWARE_CONFIG]["acoustic_limit"], 2000)

    def test_clearing_one_takes_the_key_out_rather_than_writing_zero(self):
        made = lact.with_firmware(
            lact.with_firmware({}, {"acoustic_limit": 2000}),
            {"acoustic_limit": None})
        self.assertNotIn("acoustic_limit", made[lact.FIRMWARE_CONFIG])

    def test_the_original_is_not_changed(self):
        before = json.dumps(CONFIG, sort_keys=True)
        lact.with_firmware(CONFIG, {"zero_rpm": True})
        self.assertEqual(json.dumps(CONFIG, sort_keys=True), before)


class KnobWritingTest(unittest.TestCase):

    def test_the_power_cap_is_a_field_of_its_own(self):
        made = lact.with_knob(CONFIG, lact.POWER_CAP, 20)
        self.assertEqual(made["power_cap"], 20.0)
        self.assertEqual(made["clocks_configuration"], {})

    def test_a_clock_goes_into_the_clocks_block(self):
        """Two places, and only this function knows which.

        A page that guessed would write a clock into a key nothing reads, and
        the daemon would accept the document and report success.
        """
        made = lact.with_knob(CONFIG, "max_core_clock", 1500)
        self.assertEqual(made["clocks_configuration"]["max_core_clock"], 1500)
        self.assertNotIn("max_core_clock", set(made) - {"clocks_configuration"})

    def test_clearing_a_clock_takes_the_key_out(self):
        # Rather than writing zero, which is a clock of zero.
        made = lact.with_knob(
            lact.with_knob(CONFIG, "max_core_clock", 1500),
            "max_core_clock", None)
        self.assertNotIn("max_core_clock", made["clocks_configuration"])

    def test_the_rest_of_the_config_survives(self):
        made = lact.with_knob(CONFIG, "max_core_clock", 1500)
        self.assertEqual(made["fan_control_settings"],
                         CONFIG["fan_control_settings"])

    def test_the_original_is_not_changed(self):
        # Every caller reads, changes and sends. A function that edited in
        # place would make "what is on the card" and "what is on screen" the
        # same object, and the revert would have nothing to go back to.
        before = json.dumps(CONFIG, sort_keys=True)
        lact.with_knob(CONFIG, "max_core_clock", 1500)
        lact.with_fan(CONFIG, enabled=True)
        self.assertEqual(json.dumps(CONFIG, sort_keys=True), before)


class NewerCardTest(unittest.TestCase):
    """A card reporting the newer shape, recorded off a real one.

    An RX 9070 XT as its daemon answered. Four things about it are not what
    the older shape does, and this panel got all four wrong: there is no
    absolute core-clock range at all, only an offset; the voltage offset has
    a window of its own rather than the one every older card takes; the
    clocks sit at the top of the config document instead of in a block; and
    the memory clock is shown at twice what the table holds. Two sliders were
    drawn where there should have been five, and the VRAM one read 1500 while
    LACT read 2518 for the same card at the same moment.
    """

    CLOCKS = NEW_CLOCKS
    STATS = NEW_STATS
    CONFIG = NEW_CONFIG

    def knobs(self, config=None):
        return {knob["key"]: knob for knob in lact.offered(
            self.CONFIG if config is None else config, self.CLOCKS,
            self.STATS)}

    def test_every_knob_this_card_reports_is_offered(self):
        self.assertEqual(
            sorted(self.knobs()),
            sorted([lact.POWER_CAP, "gpu_clock_offset", "voltage_offset",
                    "max_memory_clock", "min_memory_clock"]))

    def test_no_maximum_core_clock_where_the_card_reports_no_range(self):
        # od_range.sclk is null on this one: it takes an offset instead, and
        # a slider for a range that is not there writes nowhere.
        self.assertNotIn("max_core_clock", self.knobs())

    def test_the_memory_clock_is_shown_the_way_the_other_window_shows_it(self):
        """The numbers standing in LACT beside this card, for the same card.

        Somebody with both windows open is looking at one machine, and a
        panel that halves the number is a panel they have to translate.
        """
        found = self.knobs()
        self.assertEqual(found["max_memory_clock"]["start"], 2518)
        self.assertEqual(found["min_memory_clock"]["start"], 194)

    def test_a_maximum_starts_where_the_card_runs_not_where_it_would_go(self):
        """The one that was visible: 1500 against LACT's 2518.

        od_range says this card would accept 1500; current_mclk_range says it
        is set to 1259. Starting at the first drew an untouched card as one
        clocked a fifth higher than it runs - and it is the ceiling, so the
        slider sat at the far end looking like a maximum somebody had chosen.
        """
        found = self.knobs()["max_memory_clock"]
        self.assertEqual(found["start"], 1259 * 2)
        self.assertEqual(found["max"], 1500 * 2)

    def test_the_voltage_window_is_the_one_this_card_gave(self):
        # Not the +-250 the older shape gets: this card takes -200 to 0, and
        # offering more is offering a voltage it will refuse.
        found = self.knobs()["voltage_offset"]
        self.assertEqual((found["min"], found["max"]), (-200, 0))
        self.assertEqual(found["start"], -20)

    def test_the_gpu_clock_offset_is_read_out_of_its_table(self):
        # One number in the window, a table of them per power state in the
        # document.
        found = self.knobs(dict(self.CONFIG, gpu_clock_offsets={"0": 15}))
        self.assertEqual(found["gpu_clock_offset"]["value"], 15)

    def test_what_is_written_is_what_lact_writes(self):
        """The three settings made in LACT's window, read back off the daemon.

        This is the whole point of the exercise: the panel that writes
        anything else is one whose sliders move, whose apply reports success,
        and whose card does not change - clocks_configuration is not in this
        document, and a key nothing reads is taken without complaint.
        """
        made = lact.with_knob(self.CONFIG, "gpu_clock_offset", 15)
        made = lact.with_knob(made, "max_memory_clock", 2400, 2)
        made = lact.with_knob(made, "min_memory_clock", 400, 2)
        self.assertNotIn(lact.CLOCKS_BLOCK, made)
        self.assertEqual(
            {key: made[key] for key in
             ("min_memory_clock", "max_memory_clock", "gpu_clock_offsets",
              "voltage_offset", "power_cap")},
            {"min_memory_clock": 200, "max_memory_clock": 1200,
             "gpu_clock_offsets": {"0": 15}, "voltage_offset": -20,
             "power_cap": 373.0})

    def test_the_rest_of_the_document_survives_a_clock(self):
        made = lact.with_knob(self.CONFIG, "max_memory_clock", 2400, 2)
        self.assertEqual(made["pmfw_options"], self.CONFIG["pmfw_options"])
        self.assertEqual(made["fan_control_settings"],
                         self.CONFIG["fan_control_settings"])

    def test_clearing_the_offset_takes_its_table_with_it(self):
        # Rather than an empty table, which is a card told to hold no offsets
        # rather than one nobody has given any.
        made = lact.with_knob(dict(self.CONFIG, gpu_clock_offsets={"0": 15}),
                              "gpu_clock_offset", None)
        self.assertNotIn("gpu_clock_offsets", made)


class CardWithNoRangesTest(unittest.TestCase):
    """A card that publishes no window for anything.

    RDNA2 answers with an od_range of nulls. It still reports the voltage
    offset it is set to, LACT still has one written for it, and it still takes
    a new one - so "no range" is not the same question as "no such knob", and
    treating them the same hid the one setting this card actually has.
    """

    def knobs(self, config=None):
        return {knob["key"]: knob for knob in lact.offered(
            BARE_CONFIG if config is None else config, BARE_CLOCKS, {})}

    def test_the_voltage_offset_is_offered_anyway(self):
        self.assertIn("voltage_offset", self.knobs())

    def test_it_opens_on_what_the_card_reports(self):
        found = self.knobs()["voltage_offset"]
        self.assertEqual(found["value"], -50)
        self.assertEqual(found["start"], -50)

    def test_it_gets_the_window_a_card_with_no_window_gets(self):
        found = self.knobs()["voltage_offset"]
        self.assertEqual((found["min"], found["max"]), (-250, 250))

    def test_nothing_else_is_offered(self):
        """Only the voltage offset. A clock with no range is a clock this
        card does not take, and a slider for one would write nowhere.
        """
        self.assertEqual(sorted(self.knobs()), ["voltage_offset"])

    def test_a_card_reporting_no_offset_at_all_gets_none(self):
        # The other side of it: without a range and without a reported value
        # there is nothing to say the card has one.
        import copy
        clocks = copy.deepcopy(BARE_CLOCKS)
        clocks["table"]["value"]["data"]["voltage_offset"] = None
        self.assertEqual(lact.offered({}, clocks, {}), [])

    def test_what_is_written_goes_where_this_daemon_keeps_it(self):
        made = lact.with_knob(BARE_CONFIG, "voltage_offset", -60)
        self.assertEqual(made["voltage_offset"], -60)
        self.assertNotIn(lact.CLOCKS_BLOCK, made)


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
