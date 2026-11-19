#!/bin/sh
# python pre-commit hook, run by scripts/foundry-quality/staged.sh: ruff and py_limits.py on the
# staged .py files under the pack root, written as <q>/python-staged-ruff.sarif and
# <q>/python-staged-limits.sarif. The repo-wide checks (architecture, dead code, duplication)
# run in run.sh and CI.
#
# Usage: sh staged.sh <q> <root> <file>...   (files relative to the repo root)
# Files the full run skips (quality.exclude, nested pack roots, Foundry's vendored files) are
# not checked here either.
#
# Exit: 0 checked (or nothing to check), 2 a tool is missing or crashed.
set -eu
[ "$#" -ge 2 ] || { echo "usage: staged.sh <q> <root> <file>..." >&2; exit 2; }
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../lib.sh"
. "$here/python.sh"

stage_q=$1
stage_root=$2
shift 2
lead=""
if [ "$stage_root" != . ]; then lead="$stage_root/"; fi
# Keep the .py files, as repo paths for py_limits.py and as root paths for ruff.
for file do
  shift
  case $file in *.py) set -- "$@" "$file" ;; esac
done
[ "$#" -gt 0 ] || exit 0
py_enter "$stage_q" "$stage_root"

status=0
"$py" "$here/py_limits.py" . "$q/python-staged-limits.sarif" --files "$@" \
  2>"$work/staged-limits.err" || status=$?
finished staged-limits "$status" 0 "$q/python-staged-limits.sarif"

# ruff gets the staged files the full run reads too: not quality.exclude, the nested pack roots
# or the files Foundry vendors, which the baseline never sees.
"$py" "$here/foundry_json.py" files . >"$work/staged-files.list" 2>"$work/staged-files.err" ||
  { cat "$work/staged-files.err" >&2; fail "the Python files could not be listed"; }
for file do
  shift
  if grep -Fxq -- "${file#"$lead"}" "$work/staged-files.list"; then set -- "$@" "${file#"$lead"}"; fi
done
[ "$#" -gt 0 ] || exit 0
py_ruff "$q/python-staged-ruff.sarif" "$@"
