#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

"""Checks the text of this project against the rules in docs/STYLE.md.

The rules that a program can check are a subset of ASD-STE100. Vocabulary is
not one of them: the specification has a dictionary of approximately 900 words,
and a project that adds technical names to it needs a person to decide what is
a technical name. Sentence length, verb form, and punctuation are countable,
and they are also the rules that a writer breaks without knowing it.

Usage:

    python3 tools/ste-check.py                 # each file the rules apply to
    python3 tools/ste-check.py README.md       # one file
    python3 tools/ste-check.py --quiet         # the count only

It reads prose. It does not read code: an indented block, a fenced block, a
table row, a URL, and a shell command are all data, and the rules do not apply
to data.
"""

from __future__ import annotations

import ast
import io
import os
import re
import subprocess
import sys
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))

# The directories that hold other persons' work. See docs/STYLE.md.
OTHERS = ("leds-valve-shim", "cec-toolkit")

# The files in cec-toolkit/ that this project wrote, and that the rules
# therefore do apply to.
OURS_IN_OTHERS = ("cec-toolkit/README.md", "cec-toolkit/ORIGIN",
                  "cec-toolkit/bin/steamos-cec-register")

SUFFIXES = (".py", ".sh", ".md")
SCRIPTS = ("server/steamos-utility-center", "server/steamos-utility-center-power",
           "server/steamos-utility-centerctl",
           "gui/steamos-utility-center-panel",
           "systemd-sleep/steamos-utility-center",
           "cec-toolkit/bin/steamos-cec-register")

# A sentence in descriptive text. An instruction is shorter, but a program
# cannot tell the two apart, so this counts the longer limit.
MAX_WORDS = 25
MAX_SENTENCES = 6

# Words that end in -ing and are not verbs. The specification permits each of
# them: some are nouns, some are adjectives, and "following" is in its
# dictionary. Without this list, "there is nothing to show" reads as a
# continuous verb.
NOT_A_VERB = ("nothing", "something", "anything", "everything", "thing",
              "things", "during", "warning", "warnings", "setting",
              "settings", "string", "missing", "following", "remaining",
              "wiring", "timing", "spring", "ring", "the")

# Verb forms that ASD-STE100 does not permit.
CONTINUOUS = re.compile(r"\b(?:is|are|was|were|be|been|being)\s+"
                        r"(?!(?:%s)\b)\w+ing\b" % "|".join(NOT_A_VERB), re.I)
PERFECT = re.compile(r"\b(?:has|have|had)\s+(?:not\s+|never\s+|already\s+)?"
                     r"(?:been\s+)?\w+(?:ed|en)\b", re.I)

# Words the specification replaces. must for an obligation, can for a
# possibility.
FORBIDDEN = re.compile(r"\b(shall|should|ought|may|might)\b", re.I)

# The dash used to add a remark to a sentence.
DASHES = re.compile(r"&mdash;|—|\s--\s|\s-\s")


def tracked():
    listing = subprocess.run(["git", "-C", REPO, "ls-files", "-z"],
                             capture_output=True, text=True, check=True)
    return [name for name in listing.stdout.split("\0") if name]


def ours():
    """Every file whose text these rules apply to."""
    for name in tracked():
        if name in OURS_IN_OTHERS:
            yield name
            continue
        if name.split("/")[0] in OTHERS:
            continue
        if name.endswith(SUFFIXES) or name in SCRIPTS:
            yield name


def prose_of_markdown(text):
    """The paragraphs of a Markdown file, without its data.

    A fenced block, a table, a heading and a link target are data. So is a line
    that is only a command. What is left is the text a reader reads.
    """
    out, fenced = [], False
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or not line.strip():
            continue
        if line.startswith(("#", "|", ">", "    ", "\t")):
            continue
        out.append((number, line))
    return out


LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s")


def prose_of_code(text):
    """The comments and the docstrings of a program, without its code.

    A Python file is read with tokenize and ast, because a scan of the lines
    cannot tell a docstring from a triple-quoted string of test data. A shell
    script has no docstrings, so a scan of the lines is sufficient there.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return _comment_lines(text)
    out = _python_comments(text)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = node.body
        if not (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            continue
        out.extend(_docstring_lines(text, body[0].value))
    return sorted(out)


def _python_comments(text):
    """Every comment of a Python file, as (line, text)."""
    out = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        found = [(token.start[0], token.line, token.string)
                 for token in tokens if token.type == tokenize.COMMENT]
    except (tokenize.TokenError, IndentationError):        # pragma: no cover
        return _comment_lines(text)
    for number, whole, said in found:
        if whole.strip().startswith("#"):
            body = said[1:]
            # An indented comment line is a block: a log extract, a table, a
            # command. docs/STYLE.md permits those and calls them data.
            if body[:2] in ("  ", "\t "):
                continue
        else:
            body = said[1:]                     # a comment after code
        body = body.strip()
        if body and not body.startswith(("!", "SPDX", "-*-")):
            out.append((number, body))
    return out


def _docstring_lines(text, node):
    """The prose lines of one docstring, as (line, text).

    A line indented against the first line of the docstring is data, the same
    way an indented comment line is.
    """
    lines = text.splitlines()
    first = node.lineno
    last = node.end_lineno or first
    said = lines[first - 1:last]
    if not said:                                            # pragma: no cover
        return []
    said[0] = re.sub(r'^\s*[a-zA-Z]?("""|\'\'\')', "", said[0])
    if said:
        said[-1] = re.sub(r'("""|\'\'\')\s*$', "", said[-1])
    margin = None
    out = []
    for step, line in enumerate(said):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if step == 0:
            out.append((first, line.strip()))
            continue
        if margin is None:
            margin = indent
        if indent > margin:
            continue                            # a block of data
        out.append((first + step, line.strip()))
    return out


def _comment_lines(text):
    """Every `#` comment of a file that is not Python."""
    out = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        body = stripped[1:]
        if body[:2] in ("  ", "\t "):
            continue
        said = body.strip()
        if said and not said.startswith(("!", "SPDX", "-*-")):
            out.append((number, said))
    return out


def sentences(line):
    """The sentences of one line, with the obvious abbreviations kept whole."""
    line = re.sub(r"`[^`\n]*`", "CODE", line)      # a command is not prose
    line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)     # keep link text
    line = re.sub(r"\b(e\.g|i\.e|etc|Mr|Dr|vs|approx)\.", r"\1", line)
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", line)
            if part.strip()]


def words(sentence):
    return [word for word in re.split(r"\s+", sentence) if word]


def check(name, text):
    """Every rule this can count, as (line, rule, what) for one file."""
    reader = prose_of_markdown if name.endswith(".md") else prose_of_code
    lines = reader(text)
    found = []
    for number, line in lines:
        for sentence in sentences(line):
            if len(words(sentence)) > MAX_WORDS:
                found.append((number, "long sentence",
                              "%d words" % len(words(sentence))))
            for rule, pattern in (("continuous verb", CONTINUOUS),
                                  ("perfect verb", PERFECT),
                                  ("word to replace", FORBIDDEN)):
                match = pattern.search(sentence)
                if match:
                    found.append((number, rule, match.group(0)))
        match = DASHES.search(line)
        if match:
            found.append((number, "dash", line.strip()[:60]))

    # Six sentences in a paragraph. A paragraph is a run of adjacent lines.
    # A list is not a paragraph: its items are parallel and the rule that
    # limits a paragraph is about one topic, not about a count of items.
    lines = [(number, line) for number, line in lines
             if not LIST_ITEM.match(line)]
    run, start = [], None
    for number, line in lines + [(None, None)]:
        if line is not None and (start is None or number == run[-1] + 1):
            run.append(number)
            start = start if start is not None else number
            continue
        if start is not None and len(run) > 1:
            whole = " ".join(said for at, said in lines if at in run)
            count = len(sentences(whole))
            if count > MAX_SENTENCES:
                found.append((start, "long paragraph",
                              "%d sentences" % count))
        run, start = ([number], number) if line is not None else ([], None)
    return sorted(found)


def main(argv):
    quiet = "--quiet" in argv
    wanted = [name for name in argv[1:] if not name.startswith("-")]
    files = wanted or sorted(ours())
    total = 0
    for name in files:
        path = os.path.join(REPO, name)
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError as error:
            print("%s: %s" % (name, error), file=sys.stderr)
            continue
        found = check(name, text)
        total += len(found)
        if found and not quiet:
            print("%s: %d" % (name, len(found)))
            for number, rule, what in found:
                print("  %5d  %-16s %s" % (number, rule, what))
    print("%d to look at in %d files" % (total, len(files)))
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    raise SystemExit(main(sys.argv))
