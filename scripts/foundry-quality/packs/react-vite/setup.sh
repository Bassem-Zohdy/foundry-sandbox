#!/bin/sh
# react-vite CI setup, run by .github/actions/foundry-setup: installs the root's packages with
# npm or pnpm, chosen by its single lockfile (yarn is not supported).
#
# Usage: sh setup.sh <root> [--project]
# CI passes --project; the install here is always the project's own, so it changes nothing.
set -eu
case $#:${2:-} in
  1: | 2:--project) ;;
  *) echo "usage: setup.sh <root> [--project]" >&2; exit 2 ;;
esac
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../lib.sh"
. "$here/../../lib/typescript.sh"

ts_setup "$1"
