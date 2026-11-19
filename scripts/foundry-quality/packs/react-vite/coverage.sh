#!/bin/sh
# react-vite coverage, run by the `foundry / coverage` job: the root's `test:coverage` script
# (Vitest with the lcov reporter), whose coverage/lcov.info is copied to <out-dir>/react-vite.lcov
# with repo-relative paths.
#
# Usage: sh coverage.sh <root> <out-dir>
set -eu
[ "$#" -eq 2 ] || { echo "usage: coverage.sh <root> <out-dir>" >&2; exit 2; }
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../lib.sh"
. "$here/../../lib/typescript.sh"

ts_doc=react-vite.md
ts_coverage react-vite "$1" "$2"
