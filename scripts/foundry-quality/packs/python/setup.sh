#!/bin/sh
# python setup: makes the tools venv in the Foundry scratch area
# (<git-common-dir>/foundry/tools/python-venv) from requirements-tools.txt and installs jscpd
# (npm) under its node/ folder. With --project (CI, the foundry-setup action) it also installs
# the project's requirements*.txt from the pack root (or the pyproject.toml package) and
# requirements-tools.txt into the CI Python (`python3` on PATH), where coverage.sh runs the tests.
#
# Usage: sh setup.sh [<root>] [--project]
# With no root it uses the python pack's root from foundry.json (`foundry pack roots`).
# Needs python3 (3.10+) and npm.
set -eu
JSCPD_VERSION=5.4.2 # exact pin, at least 14 days old when pinned
case $#:${1:-}:${2:-} in
  0::) root='' project='' ;;
  1:--project:) root='' project=--project ;;
  1:-*:) echo "usage: setup.sh [<root>] [--project]" >&2; exit 2 ;;
  1:*:) root=$1 project='' ;;
  2:*:--project) root=$1 project=--project ;;
  *) echo "usage: setup.sh [<root>] [--project]" >&2; exit 2 ;;
esac
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../lib.sh"
. "$here/python.sh"

if [ -z "$root" ]; then
  roots=$(cd "$repo" && foundry pack roots) || fail "the pack roots could not be read from foundry.json"
  root=$(printf '%s\n' "$roots" | awk -F '\t' '$1 == "python" { print $2 }')
  [ -n "$root" ] ||
    fail "foundry.json does not list the python pack; pass its root: sh setup.sh <root> [--project]"
fi
[ -d "$repo/$root" ] || fail "pack root $root is not a folder"
venv=$(py_venv)
mkdir -p "$(dirname "$venv")"
[ -x "$venv/bin/python" ] || python3 -m venv "$venv" || fail "python3 -m venv failed"
"$venv/bin/python" -m pip install --quiet --disable-pip-version-check \
  -r "$here/requirements-tools.txt" </dev/null || fail "the python tools could not be installed"
npm install --prefix "$venv/node" --no-audit --no-fund --no-save --silent \
  "jscpd@$JSCPD_VERSION" </dev/null || fail "jscpd could not be installed (npm)"

[ -n "$project" ] || exit 0
cd "$repo/$root"
set -- -r "$here/requirements-tools.txt"
for req in requirements*.txt; do
  if [ -f "$req" ]; then set -- "$@" -r "$req"; fi
done
if [ "$#" -eq 2 ] && [ -f pyproject.toml ]; then set -- "$@" .; fi
[ "$#" -gt 2 ] || echo "setup: $root has no requirements*.txt or pyproject.toml; installing the CI tools only"
python3 -m pip install --quiet --disable-pip-version-check "$@" </dev/null ||
  fail "the project's requirements and the CI tools could not be installed into $(command -v python3)"
