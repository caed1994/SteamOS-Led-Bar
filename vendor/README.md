<!--
SPDX-FileCopyrightText: 2026 caed1994
SPDX-License-Identifier: GPL-3.0-or-later
-->

# vendor/

Other people's code, kept here rather than fetched at install time.

Fetching would mean the panel could only install HDMI CEC support with a
network connection and would install whatever upstream happened to be that
day. Keeping it here means one known tree, installable offline, that can be
read and changed like the rest of this repository — which is the point: CEC
behaviour is the kind of thing you debug against your own television, and
code you cannot edit is code you cannot debug.

The price is that upstream fixes have to be brought over by hand. Each
subtree therefore carries an `UPSTREAM` file naming the URL, tag and commit
it was taken from, which is what turns that job into a three-way diff.

| Subtree | Upstream | Licence |
| ------- | -------- | ------- |
| `steamos-cec-toolkit/` | [Twsts/steamos-cec-toolkit](https://github.com/Twsts/steamos-cec-toolkit) | MIT |

Each subtree keeps its own licence file and its own copyright notice. MIT is
compatible with this project's GPL-3.0-or-later, and the notices stay as they
are: the copyright is not ours to move.

**These trees are not written in this project's style, and should not be
rewritten into it.** Reformatting a vendored file makes every future upstream
diff conflict on lines nobody meant to change. Fix what is broken, leave the
rest, and prefer sending a fix upstream over carrying it here.
