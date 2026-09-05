<!--
SPDX-FileCopyrightText: 2026 caed1994
SPDX-License-Identifier: GPL-3.0-or-later
-->

# How to write in this project

All the text in this project follows ASD-STE100 Simplified Technical English.
This includes the README, the documents in this directory, and the comments and
docstrings in the code.

ASD-STE100 is a controlled language. The AeroSpace and Defence Industries
Association of Europe wrote it for maintenance documentation. It makes text
easy to read for persons who do not have English as their first language. It
also makes text easy to translate.

This page is the subset of the specification that applies here. Use it when you
write new text, and when you review a change.

`tools/ste-check.py` checks the rules that a program can count: the length of a
sentence, the length of a paragraph, the verb forms, and the punctuation. Run it
over the files that you changed, or over all of them:

```bash
python3 tools/ste-check.py README.md      # one file
python3 tools/ste-check.py                # each file the rules apply to
```

It cannot check the vocabulary. That part needs a person.

## Words

Use one word for one meaning. If you call it an adapter in one place, do not
call it a dongle in another place.

Use the technical name of a thing. A technical name is a word for a part, a
tool, a file, or a standard. These words are permitted, and you must not
replace them:

```text
adapter, daemon, socket, unit, service, kernel, module, firmware, strip,
logical address, physical address, effect, frame, checksum, governor
```

Do not use these:

- Idioms and figures of speech. Write "this is difficult", not "this is a bad
  afternoon".
- Metaphors. Write "the service does not start", not "the service is asleep".
- Jokes and rhetorical questions.
- Slang.
- Abbreviations that are not standard. CEC, USB, and LED are standard. "config"
  is not: write "configuration".

## Noun phrases

Do not put more than three nouns together. "CEC adapter permissions repair
helper" has five. Write "the helper that repairs the CEC adapter permissions".

Do not remove articles to make a sentence shorter. Write "the unit starts the
service", not "unit starts service".

## Verbs

Use these verb forms only:

- the infinitive: `to start`
- the imperative: `Start the service.`
- the simple present: `The service reads the configuration.`
- the simple past: `The service did not start.`
- the simple future: `The service will start at the next boot.`
- the past participle, as an adjective or in the passive: `the installed unit`

Do not use:

- the present perfect or the past perfect: not `has started`, not `had started`
- the continuous forms: not `is starting`
- the `-ing` form as a noun, unless it is a technical name

Use the active voice. The passive voice is permitted in descriptive text when
the person who does the action is not important, but prefer the active voice:

```text
Do not write:  The unit is installed by the installer.
Write:         The installer installs the unit.
```

Use `must` for an obligation and `can` for a possibility. Do not use `shall`,
`should`, or `may`.

## Sentences

A sentence in an instruction has 20 words or less. A sentence in descriptive
text has 25 words or less.

Give one instruction in one sentence. If two things must occur, write two
sentences.

Write one topic in one sentence. Do not join two topics with a dash or with a
semicolon.

Start with the main point. Then give the reason.

```text
Do not write:  Because the device can appear ten seconds after udev is up, and
               the helper returns with no error when there is no device, the
               unit repaired nothing.
Write:         The unit repaired nothing. The device can appear ten seconds
               after udev starts. The helper finds no device and returns with
               no error.
```

## Paragraphs

A paragraph has six sentences or less.

Write one topic in one paragraph. The first sentence gives the topic.

Use a vertical list when you write three or more parallel items. Give the list
an introduction that ends with a colon.

## Punctuation

Use the full stop, the comma, the colon, the question mark, the parenthesis,
and the hyphen.

Do not use the dash to add a remark to a sentence. Write a new sentence.

Do not use the semicolon to join two sentences. Write two sentences.

## Warnings and cautions

Put a warning or a caution before the step it applies to, not after it.

Start it with a command or with a short statement of the condition. Then give
the result.

```text
Caution: Do not remove the adapter while the service runs. The daemon keeps
the device open and the kernel can refuse the next attach.
```

## Code documentation

The rules above apply to comments and docstrings. Three additions:

A docstring starts with one sentence that says what the thing does. The
sentence is an instruction to the computer, so use the third person present:
`Returns the physical address of the adapter.` Then leave an empty line and
give the detail.

Record evidence as data, not as a story. A log extract, a measurement, or a
command output is permitted and is often the most useful part of a comment.
Put it in a block and introduce it with one sentence.

```python
# The permissions unit runs before the device exists. The log shows this:
#
#   [10.0] Starting Repair SteamOS CEC device permissions...
#   [12.4] kernel: Registered IR keymap rc-cec
#   [12.5] cecd: Could not add device /dev/cec0: EACCES
```

Say why, not what. The code says what it does. A comment that repeats the code
is not useful. A comment that gives the reason is useful.

## How much to write

Write the shortest text that stops the next reader from making the mistake.

Give the reason and stop. Do not write the history of the fault: who found it,
how long it took, or what each earlier attempt was. A reader repairs the code
and does not read a report of an old repair.

```text
Do not write:  A user reported this. The page said "Everything is in order"
               below its own title, and above a card with the text "Not
               installed yet". Both texts were correct. The sentence counts
               each part, and a part that is not installed is not a problem.
Write:         The line answers for the open page. A count over every part
               reads as an answer about the section it stands under.
```

Write one reason in one place. A docstring that gives it, a comment below that
repeats it, and a README that repeats it again are three copies to keep equal.

A docstring for a function whose name says what it does is one line, or none.
Write more only for a constraint that the code does not show.

When you delete code, delete the text that documents it.

## What this does not apply to

Two directories hold other persons' work:

- `cec-toolkit/` is a fork of the SteamOS CEC Toolkit. Only the files this
  project wrote follow this page: `README.md`, `ORIGIN`, and
  `bin/steamos-cec-register`. The other files keep their own text, because a
  rewrite makes every future comparison with the source project more difficult.
- `leds-valve-shim/` is a copy of a kernel module and is not changed at all.
