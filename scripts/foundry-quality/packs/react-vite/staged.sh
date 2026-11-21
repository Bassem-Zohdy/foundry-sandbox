#!/bin/sh
# react-vite pre-commit hook, run by scripts/foundry-quality/staged.sh: ESLint on the staged
# .ts, .tsx, .js, .jsx, .mjs and .cjs files under the pack root, written as
# <q>/react-vite-staged-eslint.sarif.
#
# Usage: sh staged.sh <q> <root> <file>...   (files relative to the repo root)
# Exit: 0 linted (or nothing to lint), 2 ESLint crashed.
set -eu
[ "$#" -ge 2 ] || { echo "usage: staged.sh <q> <root> <file>..." >&2; exit 2; }
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../lib.sh"
. "$here/../../lib/typescript.sh"

ts_staged react-vite "$@"
