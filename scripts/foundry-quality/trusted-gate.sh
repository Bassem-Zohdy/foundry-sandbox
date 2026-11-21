#!/bin/sh
# Trusted gate: make the checked-out PR run the base branch's gate.
#
# Usage: sh scripts/foundry-quality/trusted-gate.sh <base-ref>     (e.g. origin/main)
#
# foundry.json and every guarded path (the shared layer's and each enabled pack's, from `run.py pack guarded`)
# is replaced with the base ref's copy. A path the base does not have is removed, so a PR cannot
# add a .semgrepignore or a rule file that switches a check off. quality/baseline/ is the one
# exception: the PR's counts stay, and `quality raised` judges them against the base's.
# Before restoring it prints one ::notice:: per guarded file the PR changed.
#
# The workflow restores scripts/foundry-ci/ and scripts/foundry-quality/ from the base first, so
# this script and the `pack guarded` it calls are the base's, never the PR's.
# Exit: 0 restored, 1 the gate cannot be trusted (one ::error:: line), 2 usage.
set -eu

die() {
  echo "::error::trusted-gate: $*"
  exit 1
}

base=${1:-}
if [ -z "$base" ]; then
  echo "usage: trusted-gate.sh <base-ref>" >&2
  exit 2
fi
top=$(git rev-parse --show-toplevel) || die "not inside a git repo"
cd "$top"
git rev-parse --verify --quiet "$base^{commit}" >/dev/null ||
  die "the base ref $base is not available in this checkout (fetch it with fetch-depth: 0)"
# foundry.json (packs, packRoots, quality.exclude, verify) is read by the gate, so it is the base's
# too, and it is restored before `pack guarded` reads it.
restore() { # rel
  git rm -rqf --cached --ignore-unmatch -- "$1"
  rm -rf -- "$1"
  if git cat-file -e "$base:$1" 2>/dev/null; then
    git checkout "$base" -- "$1" || die "cannot restore $1 from $base"
  fi
}
[ ! -L foundry.json ] || die "foundry.json is a symlink; the gate cannot be restored"
restore foundry.json
list=$(python3 scripts/foundry-ci/run.py pack guarded --repo .) || die "pack guarded failed"
[ -n "$list" ] || die "pack guarded listed no guarded paths"

tmp=$(mktemp -d) || die "no scratch folder"
trap 'rm -rf "$tmp"' EXIT

# A path with a symlink anywhere on it would be followed by rm and checkout.
no_symlink() { # rel
  cur=
  old=$IFS
  IFS=/
  for part in $1; do
    cur=${cur:+$cur/}$part
    if [ -L "$cur" ]; then
      IFS=$old
      die "$cur is a symlink; a guarded path must be a real file or folder"
    fi
  done
  IFS=$old
}

set -f
set --
for entry in $list; do
  rel=${entry#/}
  rel=${rel%/}
  case $rel in
    "" | .. | ../* | */../* | */.. | .git | .git/* | -*) die "guarded path '$entry' is not a path inside the repo" ;;
    *\** | *\?* | *\[*) die "guarded path '$entry' has a glob; it cannot be restored" ;;
  esac
  no_symlink "$rel"
  set -- "$@" "$rel"
done
no_symlink quality/baseline

# The guarded files this PR changed (the baseline is the PR's to change). File names are
# PR-controlled: % CR and LF are escaped so each name stays inside one ::notice:: line.
# Notices once per job: a marker under RUNNER_TEMP says an earlier run already printed them.
marker=
[ -z "${RUNNER_TEMP:-}" ] || marker=$RUNNER_TEMP/foundry-trusted-gate.done
if [ -z "$marker" ] || [ ! -e "$marker" ]; then
  git diff --name-only -z "$base...HEAD" -- foundry.json "$@" ':(exclude)quality/baseline' >"$tmp/changed" ||
    die "cannot diff $base...HEAD (is the history fetched?)"
  python3 -c '
import sys
def esc(t):
    return t.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
for name in open(sys.argv[1], "rb").read().decode("utf-8", "replace").split("\0"):
    if name:
        print("::notice::" + esc(name) + " changed in this PR; the gate uses the base copy until it merges")
' "$tmp/changed" || die "cannot list the changed guarded files"
fi

for rel in "$@"; do
  restore "$rel"
done

# quality/baseline/ is the PR's committed copy: HEAD's, not the working tree's, which an install
# script or a test may have rewritten since.
rm -rf quality/baseline
if git cat-file -e HEAD:quality/baseline 2>/dev/null; then
  git checkout HEAD -- quality/baseline || die "cannot restore quality/baseline from HEAD"
  find quality/baseline -type l -exec rm -f {} +
fi
[ -z "$marker" ] || : >"$marker"
echo "trusted-gate: foundry.json and $# guarded paths now come from $base"
