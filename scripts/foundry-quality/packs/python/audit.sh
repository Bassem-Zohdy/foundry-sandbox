#!/bin/sh
# python dependency audit, run by the `foundry / security` job: pinned pip-audit (from the tools
# venv setup.sh makes) on the pack root's dependencies. Any known vulnerability with a fixed
# release fails; one with no fix yet is a warning.
#
# The dependencies: the registry packages of uv.lock when it exists (as exact pins, not
# resolved again), else every requirements*.txt (resolved by pip-audit), else pyproject.toml.
#
# PIP_AUDIT_IGNORE lists advisory IDs to skip (spaces or commas). Each needs a line
# `<ID>  # <reason>` in quality/audit-ignore.txt, which CODEOWNERS gives to the tech lead.
#
# Usage: sh audit.sh <root>
# Exit: 0 clean, 1 a fixable vulnerability or nothing to audit, 2 usage, an ignore without a
# reason, missing tools or a pip-audit crash.
set -eu
[ "$#" -eq 1 ] || { echo "usage: audit.sh <root>" >&2; exit 2; }
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../lib.sh"
. "$here/python.sh"

REASONS=quality/audit-ignore.txt

# Every ignored ID must carry its reason; all missing ones are named, one per line.
audit_reasons() {
  missing=0
  set -f # an ID is a word, never a glob
  for id in $(printf '%s' "${PIP_AUDIT_IGNORE:-}" | tr ',' ' '); do
    if [ ! -f "$repo/$REASONS" ] || ! awk -v id="$id" '
      $1 == id { i = index($0, "#"); if (i && substr($0, i + 1) ~ /[^[:space:]]/) found = 1 }
      END { exit !found }' "$repo/$REASONS"; then
      echo "foundry-quality: PIP_AUDIT_IGNORE lists $id without a reason; add '$id  # <why>' to $REASONS" >&2
      missing=1
    fi
  done
  set +f
  [ "$missing" -eq 0 ] || exit 2
}

# Sets the pip-audit arguments for the root's dependencies, or fails the job when it has none.
audit_sources() {
  if [ -f uv.lock ]; then
    status=0
    "$py" "$here/pip_audit_report.py" uv-lock uv.lock "$work/uv-lock.txt" || status=$?
    [ "$status" -eq 0 ] || exit "$status"
    audit_args="-r $work/uv-lock.txt --no-deps --disable-pip"
    return 0
  fi
  audit_args=""
  for req in requirements*.txt; do
    if [ -f "$req" ]; then audit_args="$audit_args -r $req"; fi
  done
  if [ -z "$audit_args" ] && [ -f pyproject.toml ]; then audit_args="."; fi
  if [ -z "$audit_args" ]; then
    echo "::error::$root has no uv.lock, requirements*.txt or pyproject.toml; the python audit has nothing to check"
    exit 1
  fi
}

root=$1
[ -d "$repo/$root" ] || fail "pack root $root is not a folder"
audit_reasons
venv=$(py_venv)
py="$venv/bin/python"
[ -x "$venv/bin/pip-audit" ] ||
  fail "pip-audit is not installed; run: sh scripts/foundry-quality/packs/python/setup.sh $root"
work="$(quality_dir)/tmp/python"
mkdir -p "$work"
cd "$repo/$root"
audit_sources
rm -f "$work/pip-audit.json"
status=0
# shellcheck disable=SC2086 # requirement file names and the scratch path hold no spaces
"$venv/bin/pip-audit" $audit_args --progress-spinner off -f json -o "$work/pip-audit.json" \
  </dev/null 2>"$work/pip-audit.err" || status=$?
if [ "$status" -gt 1 ] || [ ! -s "$work/pip-audit.json" ]; then
  cat "$work/pip-audit.err" >&2
  fail "pip-audit crashed or wrote no report (exit $status)"
fi
"$py" "$here/pip_audit_report.py" check "$work/pip-audit.json"
