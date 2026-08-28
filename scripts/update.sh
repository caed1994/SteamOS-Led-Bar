#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 caed1994
# SPDX-License-Identifier: GPL-3.0-or-later

# Bring the clone up to date with a branch.
#
#   update.sh [--check] [branch]
#
# Unprivileged on purpose: the clone belongs to whoever made it, and only
# installing what it brings needs rights - which is a separate step, run
# afterwards. Split out of the control panel so the whole of it can be read in
# one go rather than assembled by a GUI.
#
# It refuses rather than resolves. Local edits and local commits are somebody's
# work, and an updater that throws them away to succeed is worse than one that
# stops and says what it found.

set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE="${REMOTE:-origin}"

CHECK_ONLY=0
BRANCH=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --check) CHECK_ONLY=1; shift ;;
        -h|--help)
            sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
            exit 0 ;;
        -*) echo "unknown option: $1" >&2; exit 2 ;;
        *) BRANCH="$1"; shift ;;
    esac
done

cd "$SOURCE_DIR"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "$SOURCE_DIR is not a git clone, so there is nothing to update from." >&2
    echo "This happens when the code was downloaded as a zip. To get updates:" >&2
    echo "  git clone https://github.com/caed1994/SteamOS-Utility-Center" >&2
    exit 1
fi

if [[ -z "$BRANCH" ]]; then
    BRANCH="$(git symbolic-ref --quiet --short HEAD || true)"
    if [[ -z "$BRANCH" ]]; then
        echo "No branch given, and this clone is not on one (detached HEAD)." >&2
        echo "Pick a branch to update to." >&2
        exit 1
    fi
fi

echo "Fetching $REMOTE ..."
git fetch --prune "$REMOTE"

TARGET="$REMOTE/$BRANCH"
if ! git rev-parse --verify --quiet "$TARGET^{commit}" >/dev/null; then
    echo "$REMOTE has no branch called $BRANCH. It has:" >&2
    git for-each-ref --format='  %(refname:strip=3)' "refs/remotes/$REMOTE" >&2
    exit 1
fi

BEFORE="$(git rev-parse HEAD)"
BEHIND="$(git rev-list --count "HEAD..$TARGET")"
AHEAD="$(git rev-list --count "$TARGET..HEAD")"

# Only tracked files matter: an untracked file of your own cannot conflict with
# a fast-forward, and git says so itself in the one case where it can.
DIRTY="$(git status --porcelain --untracked-files=no)"

if [[ $CHECK_ONLY -eq 1 ]]; then
    if [[ "$BEHIND" == "0" ]]; then
        echo "Already up to date with $TARGET."
    else
        echo "$BEHIND commit(s) waiting on $TARGET:"
        git log --oneline --no-decorate -20 "HEAD..$TARGET" | sed 's/^/  /'
        [[ "$BEHIND" -gt 20 ]] && echo "  ... and $((BEHIND - 20)) more"
    fi
    [[ -n "$DIRTY" ]] && echo && echo "Note: there are local changes; updating would stop and list them."
    [[ "$AHEAD" != "0" ]] && echo && echo "Note: $AHEAD local commit(s) are not on $TARGET; updating would stop."
    exit 0
fi

if [[ -n "$DIRTY" ]]; then
    echo "There are local changes to files this update would replace:" >&2
    echo "$DIRTY" | sed 's/^/  /' >&2
    echo >&2
    echo "Nothing was changed. Keep them with 'git -C $SOURCE_DIR stash', or" >&2
    echo "throw them away with 'git -C $SOURCE_DIR checkout -- <file>'." >&2
    exit 1
fi

CURRENT="$(git symbolic-ref --quiet --short HEAD || true)"
if [[ "$CURRENT" != "$BRANCH" ]]; then
    echo "Switching to $BRANCH ..."
    if git rev-parse --verify --quiet "refs/heads/$BRANCH" >/dev/null; then
        git checkout "$BRANCH"
    else
        git checkout --track "$TARGET"
    fi
fi

# --ff-only: this is an update, not a merge. Local commits mean somebody is
# working here, and the honest answer is to say so rather than to invent a
# merge commit in their checkout.
if ! git merge --ff-only "$TARGET"; then
    echo >&2
    echo "$BRANCH has commits of its own that are not on $TARGET, so it cannot" >&2
    echo "simply be fast-forwarded. Nothing was changed." >&2
    echo "Look at them with: git -C $SOURCE_DIR log --oneline $TARGET..HEAD" >&2
    exit 1
fi

AFTER="$(git rev-parse HEAD)"
if [[ "$BEFORE" == "$AFTER" ]]; then
    echo "Already up to date with $TARGET."
    exit 0
fi

echo
echo "Updated $BRANCH to $(git rev-parse --short HEAD):"
git log --oneline --no-decorate -20 "$BEFORE..$AFTER" | sed 's/^/  /'
COUNT="$(git rev-list --count "$BEFORE..$AFTER")"
[[ "$COUNT" -gt 20 ]] && echo "  ... and $((COUNT - 20)) more"
exit 0
