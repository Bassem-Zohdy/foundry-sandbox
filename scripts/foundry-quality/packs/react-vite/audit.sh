#!/bin/sh
# react-vite dependency audit, run by the `foundry / security` job: `npm audit` (or
# `pnpm audit`) in the root; a high or critical advisory fails it.
#
# Usage: sh audit.sh <root>
set -eu
[ "$#" -eq 1 ] || { echo "usage: audit.sh <root>" >&2; exit 2; }
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../lib.sh"
. "$here/../../lib/typescript.sh"

ts_audit "$1"
