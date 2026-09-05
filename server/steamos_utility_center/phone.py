# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""The notifications of your phone, on the bar.

KDE Connect carries them from the phone to the desktop. This module turns one
of them into a line in the trigger pipe.

Nothing here communicates with WhatsApp or with another app. It reads the
*notification*, so an app that is silent on the phone is silent here also.

There is no D-Bus library. `gdbus` is part of glib and is on each machine with
a desktop. Each step below the subprocess is a pure function from a line of
text to "what to flash", which the tests drive with recorded lines.

The signals of KDE Connect are the one source. The notification bus of the
desktop was a second one, and it carried each notification on the machine
rather than each notification from the phone. Game Mode has no notification
daemon at all.
"""

from __future__ import annotations

import collections
import hashlib
import logging
import os
import select
import shutil
import subprocess
import time

from . import notify

LOG = logging.getLogger(__name__)

KDECONNECT_SERVICE = "org.kde.kdeconnect"

# What the bus says when there is a notification to read. Each other signal of
# that service belongs to another program.
#
# There are two, because Android updates the first notification of a
# conversation rather than post a second one. With "posted" alone there was one
# flash for each conversation and none again until somebody cleared it.
KDECONNECT_MEMBERS = ("notificationPosted", "notificationUpdated")
# And what it says when one goes away, which is not an arrival. It arrives for
# each notification that a person removes on the phone. A flash for those
# lights the bar a second time for each message, when a person picks the phone
# up.
KDECONNECT_GONE = ("notificationRemoved", "allNotificationsRemoved")
# And what it says when something changed with no detail. The measurement on a
# Steam Deck: that KDE Connect reports the second message of a conversation in
# this way. The notification keeps its object and only its text changes. The
# answer is thus to read the recent objects again.
KDECONNECT_REFRESH = ("refreshed",)
# And what one notification says on its own object. The measurement on a Steam
# Deck, and the simplest of the three: the signal arrives on
# ".../notifications/6", so the notification is the object that sent it.
# Nothing must be found or remembered.
KDECONNECT_ON_OBJECT = ("ready",)
# The form of the path of such an object, so that a signal with the same name
# from another part of KDE Connect is not a notification.
NOTIFICATION_PATH = "/notifications/"

# One notification, as an object of its own. The signal of KDE Connect carries
# an id only. The measurement on a real machine: that id is its own counter and
# not the Android key. The app is thus a property of this object.
NOTIFICATION_INTERFACE = "org.kde.kdeconnect.device.notifications.notification"

# What separates one rule from the next in PHONE_APPS, and the fields in one
# rule. The comma removes the "r,g,b" form of a colour inside a rule. People
# write #rrggbb there, and the validator says so.
RULE_SEPARATOR = ","
FIELD_SEPARATOR = ":"

# How often to stop the read of the stream and verify that KDE Connect is
# present.
#
# The interval is long, because it is one cheap call and nothing needs a fast
# answer. It is not "never": a check at the start only is a check that never
# occurs again, and this process outlives the session that started it.
TICK_SECONDS = 60.0

# And how often while it is absent. One minute is correct for "nothing is
# wrong, look sometimes". It is much too long after KDE Connect stops with
# the session, while the phone tries to connect again.
#
# This rate is cheap: it is one short call, and it runs only while something
# is defective.
EAGER_SECONDS = 5.0

# How long to wait for an answer from the bus. It is long for the service,
# which has no other work. A window that asks the same question while a
# person watches it needs a shorter time, and it gives its own.
ASK_SECONDS = 5.0

# How long to let a new KDE Connect take its name before this asks it a
# question. It is short: a wrong value costs one question, and the next
# check asks again one minute later.
SETTLE_SECONDS = 2.0

# What to give Qt when this module starts kdeconnectd.
#
# kdeconnectd is a Qt application and needs a platform plugin it can start.
# From a service it takes "wayland", finds no compositor, and stops. This
# plugin is the one for a program that draws nothing.
QT_PLATFORM = "offscreen"

# How much of the digest to write. It is long enough that two different
# notifications do not collide inside one repeat gap, and short enough to read
# in a log line.
TAG_LENGTH = 8

# How many notification objects to keep for a "refreshed" signal to read
# again. Only a recent object can have something new. A phone that nobody uses
# for a day must not leave a list of each notification that it sent.
RECENT_NOTIFICATIONS = 8

# A phone that connects gives KDE Connect each notification it holds, one
# notificationPosted for each, so a machine after a boot flashed once for every
# unread message.
#
# Nothing on a notification gives its age, so these two values are the only way
# to separate that group from a conversation.
#
# The settling time runs from the start of the bridge. It must be longer than
# the time for a phone to find a machine that started, and each second of it is
# a second in which a new message does not flash. The bridge restarts at Apply
# as well as at the boot.
BACKLOG_SETTLE = 30.0           # seconds after the bridge starts
# The flood is the same group at a later time, when the phone joins the network
# again. That occurs after a suspend, and after a person leaves the range of
# the network and returns.
#
# It counts conversations and not notifications. Six messages from one person
# are six flashes, and that was a reported defect. Six people in four seconds
# are one group.
BACKLOG_FLOOD = 3               # conversations
BACKLOG_WINDOW = 4.0            # seconds they arrive within


class Sighting(collections.namedtuple("Sighting", "app title body where")):
    """One notification, with what the bus reported about it.

    The bus reports a notification by an id and keeps the other values, the app
    among them, as properties of an object. `where` is that object, so a record
    from a signal alone is mostly a position to ask at.
    """

    __slots__ = ()

    def __new__(cls, app, title="", body="", where=""):
        return super().__new__(cls, app, title, body, where)

    def describe(self):
        line = self.app or "(no app)"
        if self.title:
            line += ": " + self.title
        if self.body:
            line += " - " + self.body
        return line


Rule = collections.namedtuple("Rule", "app color style")


# -- talking to the bus ------------------------------------------------------
#
# Three commands. This module runs none of them: the caller owns the
# subprocess, so that a test can drive the full bridge from a list of
# strings.

def monitor_command():
    """Returns the argv for `gdbus` to read the signals of KDE Connect."""
    return ["gdbus", "monitor", "--session", "--dest", KDECONNECT_SERVICE]


def wake_command():
    """Returns a question for KDE Connect, so that the bus starts it.

    A call to a name that the bus can activate starts the service behind that
    name. The content of the call is thus not important. The call must be cheap
    and must change nothing. A list of the paired devices is both.
    """
    return ["gdbus", "call", "--session", "--dest", KDECONNECT_SERVICE,
            "--object-path", "/modules/kdeconnect", "--method",
            "org.kde.kdeconnect.daemon.deviceNames"]


# -- reading what it says ----------------------------------------------------

def _split_arguments(text):
    """Returns the top-level items of a GVariant tuple, in their own form.

    Not a GVariant parser: it splits at each comma that is outside a string and
    outside a bracket, which is enough to count the arguments. Each item stays
    as written, so _as_string can tell a string from a number that looks like
    one.
    """
    depth = 0
    quote = ""
    item = ""
    items = []
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\" and index + 1 < len(text):
                item += text[index:index + 2]
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth -= 1
        elif char == "," and depth == 0:
            items.append(item.strip())
            item = ""
            index += 1
            continue
        item += char
        index += 1
    if item.strip():
        items.append(item.strip())
    return items


def _as_string(item):
    """Returns the text of a GVariant item, or None if it is not a string.

    A type annotation can come first: "<'x'>" or "@s 'x'". This function thus
    searches for the quote and does not expect it at the start.
    """
    if item is None:
        return None
    text = item.strip()
    for index, char in enumerate(text):
        if char in "'\"":
            closing = text.rfind(char)
            if closing <= index:
                return None
            return _unescape(text[index + 1:closing])
        if char in "([{":                       # a container, not a string
            return None
    return None


def _unescape(text):
    out = ""
    index = 0
    while index < len(text):
        if text[index] == "\\" and index + 1 < len(text):
            out += text[index + 1]
            index += 2
            continue
        out += text[index]
        index += 1
    return out


def _arguments(line, member):
    """Returns the argument tuple of this line, if the line is that member.

    gdbus writes "<path>: <interface>.<member> (args)", and the front part is
    different between glib versions. This searches for the member rather than
    read the line from the left.
    """
    marker = "." + member
    at = line.find(marker + " ")
    if at < 0:
        at = line.find(marker + "(")
        if at < 0:
            return None
    opening = line.find("(", at)
    closing = line.rfind(")")
    if opening < 0 or closing <= opening:
        return None
    return _split_arguments(line[opening + 1:closing])


def app_from_key(key):
    """Returns the app from an Android notification key.

    Android writes "user|package|id|tag|uid", or "package:id:tag" on an older
    release, and KDE Connect passes it through. In both forms the package is
    the part with full stops in it.

    It returns the full key when no part looks like a package: a value a person
    can see in --print and match by hand is better than an empty one.
    """
    pieces = [piece for piece in key.replace("|", ":").split(":")
              if piece and piece not in ("null", "0")]
    dotted = [piece for piece in pieces if "." in piece and not
              piece.replace(".", "").isdigit()]
    if dotted:
        return dotted[0]
    return pieces[0] if pieces else key


def read_line(line):
    """Returns one line of `gdbus monitor` as a Sighting, or None.

    Most lines are not a Sighting. gdbus starts with some lines about what it
    monitors, and KDE Connect sends messages of its own.
    """
    if member_of(line) in KDECONNECT_ON_OBJECT:
        where = line.split(":", 1)[0].strip()
        if NOTIFICATION_PATH not in where:
            return None                 # some other part of KDE Connect
        # There is no id to read. The object that sent the signal is the
        # notification, and each value is a property of that object.
        return Sighting("", where=where)
    arguments = None
    for member in KDECONNECT_MEMBERS:
        arguments = _arguments(line, member)
        if arguments:
            break
    if not arguments:
        return None
    key = _as_string(arguments[0])
    if not key:
        return None
    # This signal carries an id and nothing else. On a real machine that id is
    # the counter of KDE Connect, such as "3". It is not the Android key with
    # the package name.
    #
    # This module thus asks for the app separately, at the object that the id
    # names. app_from_key is the answer when that object is not available, and
    # it is correct on a machine whose ids are the Android form.
    return Sighting(app_from_key(key), where=_notification_path(line, key))


def member_of(line):
    """Returns the name of the signal, with no interface. "" for other lines.

    Only for the dry run, which lists what this module did not act on. A bridge
    that ignores the signal with the messages looks like a phone that stopped.
    """
    head, separator, rest = line.partition(": ")
    if not separator or not head.startswith("/"):
        return ""
    name = rest.split("(")[0].strip()
    return name.rsplit(".", 1)[-1] if "." in name else ""


def _notification_path(line, key):
    """Returns where to ask about this notification: the object and the id.

    gdbus prints the object that sent the signal at the front of the line. This
    function thus reads the device from that object and does not guess it. There
    can be more than one paired phone, and one of them sent this.
    """
    path = line.split(":", 1)[0].strip()
    if not path.startswith("/"):
        return ""
    return "%s/%s" % (path.rstrip("/"), key)


def details_command(path):
    """Returns what KDE Connect knows about one notification, in one call."""
    return ["gdbus", "call", "--session", "--dest", KDECONNECT_SERVICE,
            "--object-path", path, "--method",
            "org.freedesktop.DBus.Properties.GetAll", NOTIFICATION_INTERFACE]


def read_details(text, fallback):
    """Returns a GetAll reply as a Sighting, or `fallback` for an empty reply.

    The reply is a dictionary of variants: {'appName': <'WhatsApp'>, ...}. Three
    of its entries are important here.
    """
    opening = (text or "").find("{")
    closing = text.rfind("}") if opening >= 0 else -1
    if closing <= opening:
        return fallback
    properties = {}
    for entry in _split_arguments(text[opening + 1:closing]):
        name, _, value = _split_pair(entry)
        if name is not None:
            properties[name] = _as_string(value) or ""
    app = properties.get("appName") or fallback.app
    return Sighting(app, properties.get("title", ""),
                    properties.get("text", ""), fallback.where)


def _split_pair(entry):
    """Splits "'key': <value>" into its two halves at the correct colon.

    It searches for the colon and does not use partition. A title can contain a
    colon: "Anna: bist du da?" is a message that a person sends.
    """
    depth = 0
    quote = ""
    index = 0
    while index < len(entry):
        char = entry[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth -= 1
        elif char == ":" and depth == 0:
            return _as_string(entry[:index]), ":", entry[index + 1:].strip()
        index += 1
    return None, "", entry


# -- the appearance of a notification ----------------------------------------

def parse_rules(text):
    """Reads PHONE_APPS as rules: "WhatsApp:#25d366:double_flash, Signal:#3a76f0".

    The shape is optional and the colour is not. A rule exists to give the
    appearance of an app. A rule with no appearance is a spelling mistake, and a
    person must hear about it.
    """
    rules = []
    for entry in str(text or "").split(RULE_SEPARATOR):
        entry = entry.strip()
        if not entry:
            continue
        fields = [field.strip() for field in entry.split(FIELD_SEPARATOR)]
        if len(fields) < 2 or len(fields) > 3:
            raise ValueError(
                "%r is not App:colour or App:colour:shape" % entry)
        app, color = fields[0], fields[1]
        style = fields[2] if len(fields) == 3 else None
        if not app:
            raise ValueError("%r names no app" % entry)
        notify.parse_color(color)               # raises if it is not one
        if style is not None and style not in notify.STYLES:
            raise ValueError("%r is not a shape: %s"
                             % (style, ", ".join(notify.STYLES)))
        rules.append(Rule(app, color, style))
    return tuple(rules)


def match(rules, app):
    """Returns the rule for this app, or None.

    It matches in both directions and ignores the case. KDE Connect names an
    app "WhatsApp" or "com.whatsapp", by its version and by whether this module
    could ask the object.
    """
    wanted = (app or "").strip().lower()
    if not wanted:
        return None
    for rule in rules:
        name = rule.app.strip().lower()
        if name and (name in wanted or wanted in name):
            return rule
    return None


def trigger_for(sighting, rules, kind=notify.KIND_PHONE, listed_only=False):
    """Returns the line to write into the pipe, or None to ignore this one.

    A record with no rule goes as the kind name and not as a colour, so
    PHONE_COLOR and PHONE_STYLE stay the panel's and this process needs no
    restart to read a change. Only an app with its own rule carries a colour.
    """
    rule = match(rules, sighting.app)
    if rule is None:
        if listed_only:
            return None
        trigger = kind
    elif rule.style:
        trigger = "%s%s%s" % (rule.style, FIELD_SEPARATOR, rule.color)
    else:
        trigger = rule.color
    return trigger + notify.TAG_SEPARATOR + whose(sighting)


def fingerprint(sighting):
    """Returns a digest of a notification, for "did this one change".

    The app, the title and the text. It separates a notification that KDE
    Connect reported again from one with a new message.
    """
    return _digest(sighting.app, sighting.title, sighting.body)


def whose(sighting):
    """Returns the source of this notification, as the key of the repeat gap.

    The app and the title, which together are the conversation. The text is
    the message: with the text in the key, twenty messages are twenty
    conversations. With the trigger word alone, every phone notification is
    one conversation and two apps seconds apart give one flash.

    The correct key is who writes.
    """
    return _digest(sighting.app, sighting.title)


def _digest(*parts):
    said = "\0".join(parts)
    return hashlib.sha1(said.encode("utf-8", "replace")).hexdigest()[:TAG_LENGTH]


# -- the loop ----------------------------------------------------------------

class Bridge:
    """Reads the output of a monitor and writes triggers, one line at a time.

    The caller gives the lines. This class opens nothing. The full class is thus
    testable with no bus: `run` takes any iterable of strings, and the process
    is only how the service makes one.
    """

    def __init__(self, rules, kind=notify.KIND_PHONE,
                 listed_only=False, send=None, report=None, details=None,
                 settle=0.0, clock=None):
        self.rules = rules
        self.kind = kind
        self.listed_only = listed_only
        self.send = send
        self.report = report
        # The caller gives this function. It is the one step that speaks to
        # the bus, so a test drives the full loop with a function of its own.
        self.details = details
        # The clock is a parameter for the same reason. The settling time and
        # the flood are the only parts that use it, and a test must not wait
        # for either of them.
        self.clock = time.monotonic if clock is None else clock
        self.seen = 0
        self.flashed = 0
        # The recent notification objects and the last content of each, for
        # a "refreshed" signal to read again and compare.
        self.recent = {}
        # The arrival time of each recent conversation, for the flood.
        self.arrivals = collections.deque()
        self._settled_at = 0.0
        self.expect_backlog(settle)
        # The signal names that this reported. A busy bus thus does not
        # repeat one line one hundred times.
        self.passed_over = set()

    def expect_backlog(self, seconds=BACKLOG_SETTLE):
        """Stops the flashes for a moment, before a phone sends its group.

        The caller calls this at the start, and again after KDE Connect returns.
        Both events mean the same thing: a connection starts, and the first data
        on it is each notification that the phone holds.
        """
        self._settled_at = self.clock() + seconds

    def line(self, text):
        """Reads one line. Returns the trigger that it wrote, or None."""
        sighting = read_line(text)
        if sighting is None:
            if member_of(text) in KDECONNECT_REFRESH:
                return self._look_again()
            self._passed_over(text)
            return None
        return self._handle(sighting)

    def _remember(self, sighting):
        """Keeps the recent notification objects and their content.

        The newest object is last, and the list has a limit. Without the limit, a
        phone that nobody uses for a day leaves a list of each notification that
        it sent. Only a recent object can have something new.
        """
        where = sighting.where
        self.recent.pop(where, None)
        self.recent[where] = fingerprint(sighting)
        for old in list(self.recent)[:-RECENT_NOTIFICATIONS]:
            del self.recent[old]

    def _look_again(self):
        """Reads the recent notifications again, because one of them changed.

        KDE Connect reports a change as "refreshed", which says that something
        changed and not what. This asks the objects, and the digest decides
        whether the answer holds something new.
        """
        if self.details is None:
            return None
        written = None
        for where in list(self.recent):
            fresh = self.details(Sighting("", where=where))
            if fresh is None or not fresh.app:
                continue
            written = self._handle(fresh) or written
        return written

    def _handle(self, sighting):
        """Reads one notification, from any of the signals."""
        if sighting.where and self.details is not None and not sighting.title:
            sighting = self.details(sighting)
        if sighting.where and not sighting.app:
            # This module asked, and the object gave no answer. A person most
            # probably removed it on the phone between the signal and the
            # question. It is not a notification, so the dry run also omits
            # it.
            return None
        if sighting.where:
            if self.recent.get(sighting.where) == fingerprint(sighting):
                # The content is the last content. Something else on the phone
                # changed: KDE Connect reports the same notification again when
                # the notifications beside it change.
                #
                # The service discards this as a repeat. A dry run with a line
                # for it describes an event that did not occur.
                return None
            self._remember(sighting)
        self.seen += 1
        trigger = trigger_for(sighting, self.rules, self.kind,
                              self.listed_only)
        if self.report is not None:
            self.report(sighting, trigger)
        if trigger is None or self.send is None:
            return trigger
        if self._catching_up(sighting):
            # This module records it, so that a later "refreshed" does not
            # read it as new. It does not flash it.
            #
            # The question is here and not before the report, so that --print
            # continues to show each signal from the bus. A person starts
            # --print by hand, and its first line is the test message that
            # the person sent.
            return None
        try:
            self.send(trigger)
        except OSError as exc:
            # A service that does not run is not a failure of this process,
            # and one notification is not a reason to exit. The next one can
            # arrive. This writes one line for each notification.
            LOG.warning("cannot flash %s: %s", trigger, exc)
            return trigger
        self.flashed += 1
        return trigger

    def _catching_up(self, sighting):
        """Returns whether this is the group from the phone and not a person.

        KDE Connect gives no age for a notification, so this uses the arrival
        time and the number that arrived with it. See BACKLOG_SETTLE and
        BACKLOG_FLOOD.
        """
        now = self.clock()
        if now < self._settled_at:
            LOG.debug("not flashing %s: still settling after start",
                      sighting.app)
            return True
        while self.arrivals and now - self.arrivals[0][0] > BACKLOG_WINDOW:
            self.arrivals.popleft()
        self.arrivals.append((now, whose(sighting)))
        talking = {who for _when, who in self.arrivals}
        if len(talking) > BACKLOG_FLOOD:
            LOG.info("%d conversations inside %gs - taking that as the phone "
                     "catching up rather than people", len(talking),
                     BACKLOG_WINDOW)
            return True
        return False

    def _passed_over(self, text):
        """Reports a signal that this did not use, one time for each name.

        It reports only in a dry run. A dry run sets `report`. This is for the
        names that a bus uses, and not for the journal.
        """
        if self.report is None:
            return
        member = member_of(text)
        if (not member or member in KDECONNECT_MEMBERS
                or member in KDECONNECT_GONE or member in self.passed_over):
            return
        self.passed_over.add(member)
        # The full line, and not the name alone. Two times, a signal that
        # this module did not use was the signal with the messages. The next
        # step needed its object and its arguments, and a name gives
        # neither.
        self.report(Sighting("(not acted on) " + text.strip()), None)

    def run(self, lines):
        for text in lines:
            self.line(text.rstrip("\n"))
        return self

    def watch(self, stream, tick=None, every=None):
        """Reads the output of a monitor, and runs `tick` between the lines.

        A quiet phone sends nothing for hours, and this process must still
        detect the exit of KDE Connect. The read thus stops at intervals and
        runs `tick`.

        select() and not a thread: one thing to wait for, and a thread needs a
        way to stop. `every` can be a function, so the caller can look often
        while something is wrong and seldom while nothing is.
        """
        # This reads `every` at the call and not at the definition, so
        # that a test can make the interval shorter.
        every = TICK_SECONDS if every is None else every
        while True:
            wait = every() if callable(every) else every
            ready, _write, _bad = select.select([stream], [], [], wait)
            if not ready:
                if tick is not None:
                    tick()
                continue
            text = stream.readline()
            if not text:
                return self                     # the monitor ended
            self.line(text.rstrip("\n"))


def obstacles(notify_on, phone_on, fifo_ready):
    """Returns what stops a notification from reaching the bar, in sentences.

    Empty when nothing stops it. The dry run reads the bus whether or not the
    feature is on, so each line of it can be correct while the bar stays dark.
    This is what connects the two.
    """
    complaints = []
    if not notify_on:
        complaints.append(
            "NOTIFY is off, so nothing flashes at all - it is the master "
            "switch, at the top of the panel's Notifications page.")
    if not phone_on:
        complaints.append(
            "NOTIFY_PHONE is off, so the bar will not do this for real yet. "
            "Switch on 'Flash on phone notifications' in the panel, press "
            "Apply, then: systemctl --user restart steamos-utility-center-phone")
    if not fifo_ready:
        complaints.append(
            "the service is not listening - no notification pipe. Check it "
            "with: systemctl status steamos-utility-center")
    return complaints


def open_monitor():
    """Starts `gdbus monitor` on the signals of KDE Connect."""
    return subprocess.Popen(monitor_command(), stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, bufsize=1)


# Where kdeconnectd is. It is not on the PATH on most distributions, because
# it is a daemon that nobody types. This module thus tries the usual
# directories.
KDECONNECTD_PLACES = (
    "/usr/lib/kdeconnectd",
    "/usr/libexec/kdeconnectd",
    "/usr/lib/kde4/libexec/kdeconnectd",
    "/usr/bin/kdeconnectd",
)


def kdeconnectd_path():
    """Returns the program of the daemon, or None if this machine lacks it."""
    found = shutil.which("kdeconnectd")
    if found:
        return found
    for place in KDECONNECTD_PLACES:
        if os.path.isfile(place) and os.access(place, os.X_OK):
            return place
    return None


def start_kdeconnectd(path=None):
    """Starts KDE Connect, because the bus does not start it.

    The daemon stops with the Plasma session that started it, and the bus has
    no activation file for it. In Game Mode it is thus absent.

    The caller calls this only when nothing owns the name, and a second
    instance loses the name and exits, so a start that was not necessary costs
    a process that stops again.

    It starts in a session of its own, so the daemon outlives this process. A
    daemon that stopped with the bridge would restart at each Apply, and each
    start stops the connection to the phone.

    It starts with no display. kdeconnectd is a Qt application, and from here
    it takes "wayland" and finds no compositor:

        Failed to create wl_display (No such file or directory)
        qt.qpa.plugin: Could not load the Qt platform plugin "wayland"

    This module needs its network and its bus, and both operate with no screen.
    """
    path = path or kdeconnectd_path()
    if path is None:
        return None
    try:
        subprocess.Popen([path], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True,
                         env=dict(os.environ, QT_QPA_PLATFORM=QT_PLATFORM))
    except OSError as exc:
        LOG.warning("cannot start %s: %s", path, exc)
        return None
    LOG.info("started %s with no display, which nothing else was going to",
             path)
    return path


def wake_kdeconnect(revive=True, settle=SETTLE_SECONDS, timeout=ASK_SECONDS):
    """Starts KDE Connect if it does not run, and reports what it knows.

    Before the monitor starts: a monitor attaches to a name and does not
    request it, so with nothing behind that name it waits without a limit.

    None when the daemon gave no answer, and the paired phones in each other
    case. That separates "KDE Connect does not run" from "it runs and your
    phone does not speak to it".
    """
    reply = _ask(wake_command(), timeout)
    if not reply.strip() and revive and start_kdeconnectd() is not None:
        # The daemon must take the name before this can ask it a question. A
        # daemon that starts is not yet a daemon that answers.
        #
        # This is one short wait and not a loop. If the daemon is not ready
        # then, the next check asks again one minute later.
        time.sleep(settle)
        reply = _ask(wake_command(), timeout)
    if not reply.strip():
        return None
    return device_names(reply)


def device_names(reply):
    """Returns the phones from a deviceNames reply.

    It reads each string in the reply. The shape is different between KDE
    Connect versions: some give a list of names and others give a map of id to
    name. The difference has no meaning for a person who reads the log.
    """
    found = []
    for item in _split_arguments(reply.strip().lstrip("(").rstrip(")")):
        for piece in _split_arguments(item.strip("[]{} ")):
            name = _as_string(_split_pair(piece)[2] or piece)
            if name:
                found.append(name)
    return found


def look_up(sighting):
    """Adds what KDE Connect keeps beside the id, if it answers.

    Never a failure: an id this cannot ask about still flashes, with the app
    that app_from_key found. A notification that goes away during the question
    is a race nothing can win.
    """
    reply = _ask(details_command(sighting.where))
    return read_details(reply, sighting)


def _ask(command, timeout=ASK_SECONDS):
    """Runs one short gdbus call. Returns "" after each kind of failure."""
    try:
        done = subprocess.run(command, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""
