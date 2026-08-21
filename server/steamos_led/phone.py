# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Your phone's notifications, on the bar.

KDE Connect already carries them from the phone to the desktop; this turns one
of them into a line in the trigger pipe. Nothing here talks to WhatsApp, or to
any other app - there is no interface for that and there never was. What it
reads is the *notification*, which is why an app that is silenced on the phone
is silent here too.

No D-Bus library. `gdbus` ships with glib, so it is on every machine that has
a desktop at all, and this project does not add a dependency to save itself
some parsing. The parsing is the testable part anyway: everything below the
subprocess is a pure function from a line of text to "what to flash", and the
tests drive it with recorded lines rather than with a phone.

One place the notifications are read from: KDE Connect's own signals, which
carry what the phone sent and nothing else. There used to be a second - the
desktop's own notification bus - as a net for a machine where KDE Connect
answered nothing. It was withdrawn once KDE Connect was working, because it
was never really the same feature: that bus carries every notification on the
machine, so "your phone buzzed" and "a chat app on the Steam Machine buzzed"
came out as the same flash, and in Game Mode - where there is no notification
daemon at all - it could find nothing either way. A fallback that flashes at
the wrong things in Desktop Mode and at nothing in Game Mode is not a
fallback.
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

# What the bus says when there is a notification to look at. Everything else
# on that service is somebody else's business.
#
# Two on KDE Connect's, because Android does not post a second notification
# for the second message of a conversation - it updates the first. Listening
# only for "posted" meant exactly one flash per conversation, and none again
# until the notification was cleared off the machine, at which point the next
# message was new again. Which is precisely what was reported.
KDECONNECT_MEMBERS = ("notificationPosted", "notificationUpdated")
# And what it says when one goes away, which is not an arrival: it arrives for
# every notification dismissed on the phone, and flashing at those would light
# the bar a second time for every message, as you picked the phone up.
KDECONNECT_GONE = ("notificationRemoved", "allNotificationsRemoved")
# And what it says when something changed without saying what. Measured on a
# Steam Deck: this is how that KDE Connect reports the second message of a
# conversation - the notification keeps the object it always had and only its
# text moves, so the answer is to re-read the objects lately seen.
KDECONNECT_REFRESH = ("refreshed",)
# And what one notification says on its own object when it has something to
# show. Measured on a Steam Deck, and the plainest of the three: the signal
# arrives on ".../notifications/6", so the notification it is about is the
# object it came from - nothing has to be looked up or remembered.
KDECONNECT_ON_OBJECT = ("ready",)
# What such an object's path looks like, so a signal of the same name from
# somewhere else in KDE Connect is not read as a notification.
NOTIFICATION_PATH = "/notifications/"

# One notification, as an object of its own. KDE Connect's signal carries only
# an id - measured on a real machine, its own counter rather than the Android
# key - so the app it came from is a property to be read off this.
NOTIFICATION_INTERFACE = "org.kde.kdeconnect.device.notifications.notification"

# What separates one rule from the next in PHONE_APPS, and the fields inside
# one. The comma costs the "r,g,b" spelling of a colour inside a rule; #rrggbb
# is what anybody writes there anyway, and the validator says so plainly.
RULE_SEPARATOR = ","
FIELD_SEPARATOR = ":"

# How often to look up from the stream and check that KDE Connect is still
# there. Long, because it is one cheap call and nothing depends on noticing
# quickly - but not never, which is what checking only at startup amounts to
# for a process built to outlive the session it started in.
TICK_SECONDS = 60.0

# And how often while it is missing. A minute is right for "nothing is
# wrong, look in now and then"; it is far too long to wait when KDE Connect
# has just died with the session and the phone is sitting there trying to
# reconnect. Cheap enough at this rate - one short call - because it only
# runs while something is actually broken.
EAGER_SECONDS = 5.0

# How long to wait for an answer from the bus. Generous for the service,
# which has nothing else to do; a window asking the same question while
# somebody watches it draw wants a shorter one, and passes its own.
ASK_SECONDS = 5.0

# How long to let a freshly started KDE Connect claim its name before
# asking it anything. Short: getting it wrong costs one wasted question,
# and the next tick asks again a minute later.
SETTLE_SECONDS = 2.0

# What to tell Qt when starting kdeconnectd ourselves. It is a Qt application
# and will not run without a platform plugin it can initialise; started from a
# service it inherits "wayland", finds no compositor, and dumps core. A daemon
# has nothing to draw, and this is the plugin for having nothing to draw on.
QT_PLATFORM = "offscreen"

# How much of the digest to put on the wire. Long enough that two different
# notifications will not collide inside one repeat gap, short enough to read
# in a log line.
TAG_LENGTH = 8

# How many notification objects to keep on hand for a "refreshed" to re-read.
# Only the recent ones can still have anything new to say, and a phone left
# alone all day should not leave a list of everything it ever sent.
RECENT_NOTIFICATIONS = 8


class Sighting(collections.namedtuple("Sighting", "app title body where")):
    """One notification, as much of it as the bus was willing to say.

    `where` is the object it can be asked about. The bus announces a
    notification by an id and keeps the rest - the app it came from included -
    as properties of an object of its own, so a sighting straight off a signal
    is mostly a place to go and ask.
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
# Three commands, none of them run from here: the caller owns the subprocess,
# so a test can drive the whole bridge from a list of strings.

def monitor_command():
    """`gdbus` reading KDE Connect's signals, as an argv."""
    return ["gdbus", "monitor", "--session", "--dest", KDECONNECT_SERVICE]


def wake_command():
    """A harmless question, asked of KDE Connect to make the bus start it.

    Any call to a name the bus can activate starts the service behind it, so
    what the call actually asks does not matter - only that it is cheap and
    cannot change anything. Listing the paired devices is both.
    """
    return ["gdbus", "call", "--session", "--dest", KDECONNECT_SERVICE,
            "--object-path", "/modules/kdeconnect", "--method",
            "org.kde.kdeconnect.daemon.deviceNames"]


# -- reading what it says ----------------------------------------------------

def _split_arguments(text):
    """The top-level items of a GVariant tuple, still in their own spelling.

    Not a GVariant parser, and it does not want to be one: it splits on the
    commas that are not inside a string or a nested bracket, which is all that
    is needed to count arguments. The items are handed on as written so that
    _as_string can tell a string from a number that happens to look like one.
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
    """A GVariant item's text, or None if it is not a string at all.

    A type annotation may come first ("<'x'>", "@s 'x'"), so the quote is
    found rather than expected at the front.
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
    """The argument tuple of this line, if the line is that member at all.

    gdbus writes one event per line as "<path>: <interface>.<member> (args)",
    and the exact shape of the front has changed between glib versions - so
    the member is found rather than the line taken apart from the left.
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
    """The app out of an Android notification key.

    KDE Connect passes the key through as it got it, and Android spells it
    "user|package|id|tag|uid" - older ones "package:id:tag". Either way the
    package is the piece with dots in it, which is also the piece worth
    matching on: "whatsapp" is in "com.whatsapp".

    The whole key when none of it looks like a package, because a rule the
    reader can see in --print and match on by hand beats a blank.
    """
    pieces = [piece for piece in key.replace("|", ":").split(":")
              if piece and piece not in ("null", "0")]
    dotted = [piece for piece in pieces if "." in piece and not
              piece.replace(".", "").isdigit()]
    if dotted:
        return dotted[0]
    return pieces[0] if pieces else key


def read_line(line):
    """One line of `gdbus monitor` as a Sighting, or None if it is not one.

    Most lines are not: gdbus opens with a couple about what it is monitoring,
    and KDE Connect carries traffic of its own.
    """
    if member_of(line) in KDECONNECT_ON_OBJECT:
        where = line.split(":", 1)[0].strip()
        if NOTIFICATION_PATH not in where:
            return None                 # some other part of KDE Connect
        # No id to read: the object it arrived on is the notification, and
        # everything about it is a property of that object.
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
    # What arrives here is an id and nothing else, and on a real machine that
    # id turned out to be KDE Connect's own counter - "3" - rather than the
    # Android key with the package name in it. So the app is asked for
    # separately, off the object the id names; app_from_key is what is left
    # when that cannot be reached, and is right for the machines whose ids are
    # the Android spelling.
    return Sighting(app_from_key(key), where=_notification_path(line, key))


def member_of(line):
    """The signal's name, without its interface. "" if this is not one.

    Only for saying what was passed over. The names differ between KDE
    Connect versions, and a bridge that silently ignores the one carrying the
    messages looks exactly like a phone that has stopped sending them - so
    the dry run lists what it did not act on rather than leaving that to be
    guessed at.
    """
    head, separator, rest = line.partition(": ")
    if not separator or not head.startswith("/"):
        return ""
    name = rest.split("(")[0].strip()
    return name.rsplit(".", 1)[-1] if "." in name else ""


def _notification_path(line, key):
    """Where to ask about this notification: the plugin's object, then the id.

    gdbus prints the object the signal came from at the front of the line, so
    the device is read off it rather than guessed - there may be more than one
    phone paired, and only one of them sent this.
    """
    path = line.split(":", 1)[0].strip()
    if not path.startswith("/"):
        return ""
    return "%s/%s" % (path.rstrip("/"), key)


def details_command(path):
    """Everything KDE Connect knows about one notification, in one call."""
    return ["gdbus", "call", "--session", "--dest", KDECONNECT_SERVICE,
            "--object-path", path, "--method",
            "org.freedesktop.DBus.Properties.GetAll", NOTIFICATION_INTERFACE]


def read_details(text, fallback):
    """A GetAll reply as a Sighting, or `fallback` if it says nothing useful.

    The reply is a dictionary of variants - {'appName': <'WhatsApp'>, ...} -
    and only three of its entries matter here.
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
    """"'key': <value>" into its two halves, on the colon that separates them.

    Found by scanning rather than by partition: a title with a colon in it -
    "Anna: bist du da?" - is exactly the message somebody sends.
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


# -- deciding what it should look like ---------------------------------------

def parse_rules(text):
    """PHONE_APPS as rules: "WhatsApp:#25d366:double_flash, Signal:#3a76f0".

    The shape is optional and the colour is not, because a rule exists to say
    what an app looks like and a rule that says nothing is a typo worth
    hearing about.
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
    """The rule for this app, or None.

    Either way round and either case: KDE Connect may name an app "WhatsApp"
    or "com.whatsapp" depending on its version and on whether the object could
    be asked, and a rule that only worked on one of those spellings would be a
    rule that worked on the machine it was written on.
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
    """The line to write into the pipe, or None to let this one pass.

    A sighting with no rule is written as the kind name rather than as a
    colour, so PHONE_COLOR and PHONE_STYLE go on being what the panel sets
    without this having to be restarted to hear about it. Only an app with a
    rule of its own is spelled out.
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
    """Everything about a notification, for "has this one changed at all".

    App, title and text, because those are what change when there is
    something new to say. Used by the bridge to tell a notification that was
    re-announced because its neighbours moved from one that is actually
    carrying a new message.
    """
    return _digest(sighting.app, sighting.title, sighting.body)


def whose(sighting):
    """Who this is from, for the service's repeat gap to be keyed on.

    The app and the title, which together are the conversation - not the
    text, which is the message. That difference is the whole of it: the gap
    exists so that somebody typing at you once a second does not hold the
    bar, and keying it on the text made every message its own conversation,
    so a burst of twenty flashed twenty times.

    Not the trigger word either, which is what it used to be. Everything the
    phone sends is the word "phone", so that made all of it one conversation
    - a WhatsApp message and a Signal one seconds apart were a single flash.
    Between those two mistakes is the thing worth keying on: who is talking.
    """
    return _digest(sighting.app, sighting.title)


def _digest(*parts):
    said = "\0".join(parts)
    return hashlib.sha1(said.encode("utf-8", "replace")).hexdigest()[:TAG_LENGTH]


# -- the loop ----------------------------------------------------------------

class Bridge:
    """Reads a monitor's output and writes triggers, one line at a time.

    Given the lines rather than opening anything itself, which is what lets
    the whole thing be tested without a bus - `run` takes any iterable of
    strings, and the process is only how the service gets one.
    """

    def __init__(self, rules, kind=notify.KIND_PHONE,
                 listed_only=False, send=None, report=None, details=None):
        self.rules = rules
        self.kind = kind
        self.listed_only = listed_only
        self.send = send
        self.report = report
        # Given rather than called for: it is the one step that talks to the
        # bus, so a test drives the whole loop by handing over a function.
        self.details = details
        self.seen = 0
        self.flashed = 0
        # The notification objects lately seen and what each last said, for a
        # "refreshed" to re-read and compare against.
        self.recent = {}
        # Signal names already mentioned, so a busy bus does not repeat one
        # line a hundred times.
        self.passed_over = set()

    def line(self, text):
        """Handle one line; returns the trigger written, if any."""
        sighting = read_line(text)
        if sighting is None:
            if member_of(text) in KDECONNECT_REFRESH:
                return self._look_again()
            self._passed_over(text)
            return None
        return self._handle(sighting)

    def _remember(self, sighting):
        """Keep the notification objects lately seen, and what they said.

        Newest last and bounded: a phone left alone for a day would otherwise
        leave a list of every notification it ever sent, and only the recent
        ones can still have anything new to say.
        """
        where = sighting.where
        self.recent.pop(where, None)
        self.recent[where] = fingerprint(sighting)
        for old in list(self.recent)[:-RECENT_NOTIFICATIONS]:
            del self.recent[old]

    def _look_again(self):
        """Re-read the notifications lately seen, because one has changed.

        Measured on a real machine: this KDE Connect announces a changed
        notification as "refreshed", which says that something moved and not
        what - while the notification itself keeps the object it always had.
        So the objects are what gets asked, and the fingerprint decides
        whether there is anything new in the answer.
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
        """One notification, however it came to light."""
        if sighting.where and self.details is not None and not sighting.title:
            sighting = self.details(sighting)
        if sighting.where and not sighting.app:
            # Asked about, and it had nothing to say - dismissed on the phone
            # between the signal and the question, most likely. Not a
            # notification, so not a line in the dry run either.
            return None
        if sighting.where:
            if self.recent.get(sighting.where) == fingerprint(sighting):
                # Still saying what it said last time. Something else on the
                # phone moved: the same notification is announced again when
                # its neighbours change, and the service would drop this as a
                # repeat anyway - but a dry run printing a line for it would
                # be describing an event that did not happen.
                return None
            self._remember(sighting)
        self.seen += 1
        trigger = trigger_for(sighting, self.rules, self.kind,
                              self.listed_only)
        if self.report is not None:
            self.report(sighting, trigger)
        if trigger is None or self.send is None:
            return trigger
        try:
            self.send(trigger)
        except OSError as exc:
            # The service being down is not this process's failure, and a
            # notification is not worth exiting over: the next one may well
            # land. Said once per notification and no louder.
            LOG.warning("cannot flash %s: %s", trigger, exc)
            return trigger
        self.flashed += 1
        return trigger

    def _passed_over(self, text):
        """Mention a signal this did not act on, once per name.

        Only in a dry run - `report` is what a dry run sets - because this is
        for finding out what a bus calls things, not for the journal.
        """
        if self.report is None:
            return
        member = member_of(text)
        if (not member or member in KDECONNECT_MEMBERS
                or member in KDECONNECT_GONE or member in self.passed_over):
            return
        self.passed_over.add(member)
        # The whole line, not just the name. Twice now a signal this did not
        # act on turned out to be the one carrying the messages, and what was
        # needed next was its object and its arguments - which a name alone
        # does not give.
        self.report(Sighting("(not acted on) " + text.strip()), None)

    def run(self, lines):
        for text in lines:
            self.line(text.rstrip("\n"))
        return self

    def watch(self, stream, tick=None, every=None):
        """Read a monitor's output, and let something happen between lines.

        A phone that is quiet sends nothing for hours, so a loop that only
        wakes on a line never wakes at all - and this is the process that has
        to notice KDE Connect going away underneath it. Hence the timeout:
        every so often the read gives up for a moment and `tick` runs.

        select() rather than a thread. There is one thing to wait on and one
        thing to do about it, and a thread would need a way to be stopped
        that this loop already has by ending.

        `every` may be a function rather than a number, asked before each
        wait. That is what lets the caller look often while something is
        wrong and seldom while nothing is: a fixed interval has to be either
        wasteful or slow to notice, and neither is necessary.
        """
        # Read when it is called rather than when this was defined, so the
        # interval is one a test can shorten.
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
    """What stands between a notification and a lit bar, in plain sentences.

    Empty when nothing does. The dry run flashes nothing by design and reads
    the bus whether or not the feature is switched on - which is right, you
    have to be able to look before you commit to it, but on its own it is a
    trap: a machine where every line comes out perfectly and the bar stays
    dark, with nothing anywhere connecting the two. So it says.
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
            "Apply, then: systemctl --user restart steamos-led-phone")
    if not fifo_ready:
        complaints.append(
            "the service is not listening - no notification pipe. Check it "
            "with: systemctl status steamos-led-serial")
    return complaints


def open_monitor():
    """Start `gdbus monitor` on KDE Connect's signals."""
    return subprocess.Popen(monitor_command(), stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, bufsize=1)


# Where kdeconnectd lives. Not on the PATH on most distributions - it is a
# daemon nobody is meant to type - so the usual places are tried by hand.
KDECONNECTD_PLACES = (
    "/usr/lib/kdeconnectd",
    "/usr/libexec/kdeconnectd",
    "/usr/lib/kde4/libexec/kdeconnectd",
    "/usr/bin/kdeconnectd",
)


def kdeconnectd_path():
    """The daemon's own binary, or None if this machine has not got it."""
    found = shutil.which("kdeconnectd")
    if found:
        return found
    for place in KDECONNECTD_PLACES:
        if os.path.isfile(place) and os.access(place, os.X_OK):
            return place
    return None


def start_kdeconnectd(path=None):
    """Start KDE Connect ourselves, because the bus would not.

    Measured on a real machine: the daemon dies with the Plasma session that
    started it, and asking the bus for it afterwards brings nothing back -
    there is no activation file for it there. So in Game Mode it is simply
    gone, and nothing else is going to start it.

    Only ever called when the name is unowned, which is what keeps this from
    being a second instance racing the first: whoever owns the name wins and
    the loser exits, so starting one that is already running is at worst a
    process that stops again.

    In a session of its own, so it outlives the bridge - and it must: this
    process restarts, and a daemon that went with it would be started afresh
    every time, dropping the phone's connection each time it did.

    Started without a display, which is not a detail. kdeconnectd is a Qt
    application, and Qt insists on a working platform plugin before it will
    run at all - so started from here it inherited "wayland", found no
    compositor to talk to, and dumped core before it had done anything:

        Failed to create wl_display (No such file or directory)
        qt.qpa.plugin: Could not load the Qt platform plugin "wayland"

    It has nothing to draw. What is wanted from it is the network and the
    bus, both of which work perfectly well with no screen at all.
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
    """Start KDE Connect if it is not up, and report what it knows.

    Called before the monitor starts, because a monitor attaches to a name
    rather than asking for it: with nothing running behind that name it would
    sit there quietly forever. In a desktop session this finds it already up
    and costs one call; in Game Mode it is what starts it.

    Returns None when it did not answer at all, otherwise the phones it is
    paired with - which is the difference between "KDE Connect is not
    running" and "KDE Connect is running and your phone is not talking to
    it". Both look identical from here otherwise: a bar that never flashes.
    """
    reply = _ask(wake_command(), timeout)
    if not reply.strip() and revive and start_kdeconnectd() is not None:
        # It has to finish claiming the name before it can be asked anything,
        # and a daemon that is starting is not yet a daemon that answers. One
        # short wait rather than a retry loop: if it is not up by then the
        # next tick asks again anyway, a minute later.
        time.sleep(settle)
        reply = _ask(wake_command(), timeout)
    if not reply.strip():
        return None
    return device_names(reply)


def device_names(reply):
    """The phones out of a deviceNames reply.

    Read as "every string in it", because the shape has changed between KDE
    Connect versions - a list of names in some, a map of id to name in others
    - and which it is does not matter to anybody reading the log.
    """
    found = []
    for item in _split_arguments(reply.strip().lstrip("(").rstrip(")")):
        for piece in _split_arguments(item.strip("[]{} ")):
            name = _as_string(_split_pair(piece)[2] or piece)
            if name:
                found.append(name)
    return found


def look_up(sighting):
    """Fill in what KDE Connect keeps beside the id, if it will say.

    Never fatal: an id that cannot be asked about still flashes, under
    whatever app_from_key made of it. A notification that arrives while the
    phone is being answered is gone by the time this asks, and that is a race
    nobody can win - it just flashes the general colour.
    """
    reply = _ask(details_command(sighting.where))
    return read_details(reply, sighting)


def _ask(command, timeout=ASK_SECONDS):
    """Run one short gdbus call; "" when it fails, however it fails."""
    try:
        done = subprocess.run(command, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""
