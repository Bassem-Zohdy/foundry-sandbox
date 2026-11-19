#!/bin/sh
# react-vite quality hook, run by scripts/foundry-quality/run.sh: ESLint, knip, jscpd and
# dependency-cruiser in the pack root, each written as <q>/react-vite-<tool>.sarif. When another
# TypeScript pack shares the root and sorts first, that pack runs them and this hook says so.
#
# Usage: sh tools.sh <q> <root>
# Exit: 0 the tools ran (violations are not failures), 2 a tool crashed or looked at nothing.
set -eu
[ "$#" -eq 2 ] || { echo "usage: tools.sh <q> <root>" >&2; exit 2; }
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../lib.sh"
. "$here/../../lib/typescript.sh"

# The pack ships this rule, so its absence is a broken install, not a reason to skip Semgrep
# (the runner skips it when .semgrep/ is gone).
[ -f "$repo/.semgrep/foundry-vite.yml" ] ||
  fail "semgrep config .semgrep/foundry-vite.yml is missing; restore it with 'foundry template apply'"
ts_doc=react-vite.md
ts_tools react-vite "$1" "$2"
