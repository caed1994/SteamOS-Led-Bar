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

Two places the notifications can be read from, because the two fail
differently:

  kdeconnect  KDE Connect's own signals. Only the phone, which is the point -
              a Discord ping on the desktop is not a phone notification and
              should not pretend to be one.
  desktop     The desktop's notification bus. Broader, and the net for a
              machine where the first one answers nothing: KDE Connect shows
              its notifications there too, along with everything else.

`auto` takes the first if KDE Connect is on the bus and the second otherwise,
which is what somebody who has not thought about it wants.
"""

from __future__ import annotations

import collections
import logging
import subprocess

from . import notify

LOG = logging.getLogger(__name__)

SOURCE_AUTO = "auto"
SOURCE_KDECONNECT = "kdeconnect"
SOURCE_DESKTOP = "desktop"
SOURCES = (SOURCE_AUTO, SOURCE_KDECONNECT, SOURCE_DESKTOP)

KDECONNECT_SERVICE = "org.kde.kdeconnect"
DESKTOP_SERVICE = "org.freedesktop.Notifications"

# The one member on each bus that means "a notification has just arrived".
# Everything else on those services is somebody else's business.
KDECONNECT_MEMBER = "notificationPosted"
DESKTOP_MEMBER = "Notify"

# Which arguments of org.freedesktop.Notifications.Notify carry what. Fixed by
# the specification, so they are named here rather than counted at the call.
DESKTOP_APP = 0
DESKTOP_SUMMARY = 3
DESKTOP_BODY = 4

# What separates one rule from the next in PHONE_APPS, and the fields inside
# one. The comma costs the "r,g,b" spelling of a colour inside a rule; #rrggbb
# is what anybody writes there anyway, and the validator says so plainly.
RULE_SEPARATOR = ","
FIELD_SEPARATOR = ":"


class Sighting(collections.namedtuple("Sighting", "app title body")):
    """One notification, as much of it as the bus was willing to say."""

    __slots__ = ()

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

def monitor_command(source):
    """`gdbus` reading one bus, as an argv."""
    service = (KDECONNECT_SERVICE if source == SOURCE_KDECONNECT
               else DESKTOP_SERVICE)
    return ["gdbus", "monitor", "--session", "--dest", service]


def names_command():
    """Ask the bus who is on it, to settle `auto`."""
    return ["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
            "--object-path", "/org/freedesktop/DBus",
            "--method", "org.freedesktop.DBus.ListNames"]


def pick_source(configured, names_text):
    """Which bus to read, given the setting and who is on the bus.

    Only `auto` looks: naming a source is an instruction, and answering it
    with the other one would be this deciding it knows better.
    """
    if configured != SOURCE_AUTO:
        return configured
    if KDECONNECT_SERVICE in (names_text or ""):
        return SOURCE_KDECONNECT
    return SOURCE_DESKTOP


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


def read_line(line, source):
    """One line of `gdbus monitor` as a Sighting, or None if it is not one.

    Most lines are not: gdbus opens with a couple about what it is monitoring,
    and the notification services carry traffic of their own.
    """
    if source == SOURCE_KDECONNECT:
        arguments = _arguments(line, KDECONNECT_MEMBER)
        if not arguments:
            return None
        key = _as_string(arguments[0])
        if not key:
            return None
        # The key and nothing else. Reading the notification's own appName
        # means a second call, on an object path built out of a key that
        # contains characters a path cannot hold - so the one thing that is
        # certainly here is used, and --print shows what it came to.
        return Sighting(app_from_key(key), "", "")

    arguments = _arguments(line, DESKTOP_MEMBER)
    if not arguments or len(arguments) <= DESKTOP_BODY:
        return None
    app = _as_string(arguments[DESKTOP_APP])
    if app is None:
        return None
    return Sighting(app,
                    _as_string(arguments[DESKTOP_SUMMARY]) or "",
                    _as_string(arguments[DESKTOP_BODY]) or "")


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

    Either way round and either case: the bus may name an app "WhatsApp" or
    "com.whatsapp" depending on which of the two sources it came from, and a
    rule that only worked on one of them would be a rule that worked on the
    machine it was written on.
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
        return None if listed_only else kind
    if rule.style:
        return "%s%s%s" % (rule.style, FIELD_SEPARATOR, rule.color)
    return rule.color


# -- the loop ----------------------------------------------------------------

class Bridge:
    """Reads a monitor's output and writes triggers, one line at a time.

    Given the lines rather than opening anything itself, which is what lets
    the whole thing be tested without a bus - `run` takes any iterable of
    strings, and the process is only how the service gets one.
    """

    def __init__(self, source, rules, kind=notify.KIND_PHONE,
                 listed_only=False, send=None, report=None):
        self.source = source
        self.rules = rules
        self.kind = kind
        self.listed_only = listed_only
        self.send = send
        self.report = report
        self.seen = 0
        self.flashed = 0

    def line(self, text):
        """Handle one line; returns the trigger written, if any."""
        sighting = read_line(text, self.source)
        if sighting is None:
            return None
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

    def run(self, lines):
        for text in lines:
            self.line(text.rstrip("\n"))
        return self


def open_monitor(source):
    """Start `gdbus monitor` on the chosen bus."""
    return subprocess.Popen(monitor_command(source), stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, bufsize=1)


def bus_names():
    """Everything on the session bus, as one blob of text, or "" if unknown."""
    try:
        done = subprocess.run(names_command(), stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""
