#!/bin/sh
# Foundry quality run. Each enabled pack's hook (scripts/foundry-quality/packs/<pack>/tools.sh)
# runs that pack's tools in its root and writes SARIF to the scratch area; Semgrep then runs
# once over the whole repo; the results are compared with the committed baseline.
#
# Usage: sh scripts/foundry-quality/run.sh [check|baseline|report] [extra args]
# Exit: 0 ok, 1 new violations, 2 a tool crashed, wrote output that cannot be read, or looked
# at nothing.
set -eu

mode=${1:-check}
case $mode in
  check | baseline | report) shift $(($# > 0 ? 1 : 0)) ;;
  *) echo "usage: run.sh [check|baseline|report]" >&2; exit 2 ;;
esac

here=$(cd "$(dirname "$0")" && pwd)
. "$here/lib.sh"
cd "$repo"
q=$(quality_dir)
mkdir -p "$q"

if [ "$mode" = report ]; then
  # Report on the SARIF of the last run when there is any, else on the baselines alone. It
  # runs before the clean-up below, which deletes that SARIF.
  for f in "$q"/*.sarif; do
    if [ -f "$f" ]; then set -- "$@" --sarif "$f"; fi
  done
  foundry quality report "$@"
  exit $?
fi

rm -rf "$q/tmp"
rm -f "$q"/*.sarif
work="$q/tmp"
mkdir -p "$work"
tab=$(printf '\t')

foundry pack roots >"$work/roots.tsv" || fail "the pack roots could not be read from foundry.json"
[ -s "$work/roots.tsv" ] ||
  fail "no stack pack is enabled in foundry.json (packs is empty), so there is nothing to check"

# The roots of the other packs that sit inside `root`, relative to it.
roots_inside() { # root
  while IFS="$tab" read -r _ other; do
    if [ "$other" = "$1" ]; then continue; fi
    if [ "$1" = . ]; then
      printf '%s\n' "$other"
    else
      case $other in "$1"/*) printf '%s\n' "${other#"$1"/}" ;; esac
    fi
  done <"$work/roots.tsv"
}

run_packs() {
  while IFS="$tab" read -r pack root; do
    hook="scripts/foundry-quality/packs/$pack/tools.sh"
    [ -f "$hook" ] || fail "pack $pack is enabled but $hook is missing; run foundry template apply"
    FOUNDRY_OTHER_ROOTS=$(roots_inside "$root")
    export FOUNDRY_OTHER_ROOTS
    status=0
    sh "$hook" "$q" "$root" </dev/null || status=$?
    [ "$status" -eq 0 ] || fail "pack $pack: $hook exited $status"
  done <"$work/roots.tsv"
}

run_semgrep() {
  bin=${FOUNDRY_SEMGREP:-semgrep}
  command -v "$bin" >/dev/null 2>&1 ||
    fail "semgrep not found ('$bin'); install it with 'pip install semgrep==1.183.0' or set FOUNDRY_SEMGREP to its path"
  # No --error: Semgrep then exits 0 whether or not it found anything, so any other status is a
  # crash. Metrics and the version check stay off so the run makes no network calls of its own.
  # --disable-nosem: a `nosemgrep` comment must not hide a finding, the same as an inline
  # eslint-disable does not change the count. A false positive is handled by a baseline raise.
  status=0
  "$bin" scan --config .semgrep --metrics=off --disable-version-check --disable-nosem --quiet \
    --json --json-output "$work/semgrep.json" >"$work/semgrep.log" 2>"$work/semgrep.err" || status=$?
  finished semgrep "$status" 0 "$work/semgrep.json"
  # Rule or config problems come back as errors in the JSON; a file it could not parse is only a
  # warning and does not stop the run.
  fatal=$(count "$work/semgrep.json" \
    'len([e for e in d.get("errors") or [] if e.get("level") == "error"])') ||
    fail "semgrep report cannot be read ($work/semgrep.json)"
  [ "$fatal" -eq 0 ] || fail "semgrep reported $fatal errors (see $work/semgrep.json)"
  # Files Semgrep could not parse or finished late are not findings, but they are not checked
  # either, so say so.
  partial=$(count "$work/semgrep.json" \
    'len([e for e in d.get("errors") or [] if re.search("PartialParsing|Timeout", json.dumps(e.get("type")))])') ||
    fail "semgrep report cannot be read ($work/semgrep.json)"
  if [ "$partial" -gt 0 ]; then
    echo "foundry-quality: semgrep: $partial parse problems or timeouts (files not fully checked; see $work/semgrep.json)" >&2
  fi
  require_inspected semgrep "$work/semgrep.json" 'len((d.get("paths") or {}).get("scanned") or [])'
  to_sarif semgrep "$work/semgrep.json" "$q/semgrep.sarif" .
}

run_packs
# Semgrep runs once, over the whole repo, with every rule file in .semgrep/. A pack that ships
# rules there checks in its own hook that they are still present.
if [ -d .semgrep ]; then run_semgrep; fi

for f in "$q"/*.sarif; do
  if [ -f "$f" ]; then set -- "$@" --sarif "$f"; fi
done
if [ "$mode" = baseline ]; then
  foundry quality baseline "$@" --apply
else
  foundry quality check "$@"
fi
