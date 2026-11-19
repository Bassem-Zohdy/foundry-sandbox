# Shared helpers for the Foundry quality runner (run.sh, staged.sh) and the pack hooks under
# scripts/foundry-quality/packs/<pack>/. Source it; it is not run on its own.
#
# A helper that finds a problem exits 2: a tool crashed, wrote output that cannot be read, or
# looked at nothing. Violations are never failures here; `foundry quality` counts them.
# A hook sets `work` (its scratch folder) before calling `finished`.

repo=$(git rev-parse --show-toplevel) || {
  echo "foundry-quality: not inside a git repo" >&2
  exit 2
}

# The vendored CLI when the repo has it (CI and a clone without Foundry), else the plugin's.
foundry() {
  if [ -f "$repo/scripts/foundry-ci/run.py" ]; then
    python3 "$repo/scripts/foundry-ci/run.py" "$@"
  else
    command foundry "$@"
  fi
}

# The scratch area for SARIF and tool output: never committed, shared by every worktree.
quality_dir() {
  common=$(cd "$(git rev-parse --git-common-dir)" && pwd) || fail "git has no common dir"
  echo "$common/foundry/quality"
}

fail() {
  echo "foundry-quality: $*" >&2
  exit 2
}

# A tool that finds violations exits non-zero; that is not a crash. A crash leaves no output
# or exits above `limit`.
finished() { # name status limit output
  if [ "$2" -gt "$3" ] || [ ! -s "$4" ]; then
    if [ -f "${work:-.}/$1.err" ]; then cat "${work:-.}/$1.err" >&2; fi
    fail "$1 crashed or wrote no output"
  fi
}

# A number read from a JSON report: a Python expression over `d` (the parsed JSON). A report
# that cannot be read, or an expression that does not fit it, stops the run.
count() { # json-file expression
  python3 -c 'import json, re, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
v = eval(sys.argv[2], {"json": json, "re": re, "d": d})
print(int(v) if isinstance(v, (int, float)) else 0)' "$1" "$2" 2>/dev/null ||
    fail "cannot read $1"
}

# Whether the folder the tool looked at has files it should have read. A pack hook replaces this
# with its own file types; the default (any tracked file) fails closed.
sources_exist() {
  [ -n "$(git ls-files | head -n 1)" ]
}

# A run that looked at nothing must not pass as clean (everything excluded, sources moved).
require_inspected() { # tool json-file expression
  # Assign first: a count that cannot be read must stop the run, not turn into "false" inside
  # an `if`.
  n=$(count "$2" "$3") || fail "$1 report cannot be read ($2)"
  if sources_exist && [ "$n" -eq 0 ]; then
    fail "$1 looked at no files although the repo has source files (check quality.exclude)"
  fi
}

# jscpd's guard. jscpd leaves a file out of statistics.total.sources while it is below minTokens
# or minLines, so a project of only small files reports 0 although jscpd was given files. Before
# failing, the same config runs again with both limits at 1: sources there mean the files were
# only too small (pass); none mean the ignores hid every file (fail). Tiny files duplicate a lot,
# so the probe also drops the project's `exitCode` and sets `threshold` to 100: it must not exit
# non-zero over duplication. The hook defines
# `jscpd_run <config>` (jscpd in the folder and with the arguments it normally uses).
require_jscpd_inspected() { # report-json config-json
  require_inspected_n=$(count "$1" '(d["statistics"].get("total") or {}).get("sources")') ||
    fail "jscpd report cannot be read ($1)"
  if [ "$require_inspected_n" -ne 0 ] || ! sources_exist; then return 0; fi
  python3 -c 'import json, sys
c = json.load(open(sys.argv[1], encoding="utf-8"))
c.update(minTokens=1, minLines=1, threshold=100, output=sys.argv[2])
c.pop("exitCode", None)
print(json.dumps(c))' "$2" "$work/jscpd-probe" >"$work/jscpd-probe-config.json" ||
    fail "jscpd config could not be read ($2)"
  rm -rf "$work/jscpd-probe"
  status=0
  jscpd_run "$work/jscpd-probe-config.json" >"$work/jscpd-probe.log" 2>"$work/jscpd.err" || status=$?
  finished jscpd "$status" 0 "$work/jscpd-probe/jscpd-report.json"
  require_inspected jscpd "$work/jscpd-probe/jscpd-report.json" \
    '(d["statistics"].get("total") or {}).get("sources")'
}

# Tools that ran in a pack's folder report paths relative to it; `prefix` makes them repo paths.
to_sarif() { # kind input output prefix
  foundry quality sarif --from "$1" --input "$2" --output "$3" --prefix "${4:-.}" ||
    fail "$1 output could not be converted to SARIF"
}

# Another pack's root inside this one belongs to that pack: its files are not counted here.
# FOUNDRY_OTHER_ROOTS holds those roots, relative to this pack's root, one per line.
nested_roots() {
  printf '%s\n' "${FOUNDRY_OTHER_ROOTS:-}" | while IFS= read -r nested; do
    if [ -n "$nested" ]; then printf '%s\n' "$nested"; fi
  done
}

# The nested roots as git pathspecs that leave them out (pack roots never hold spaces).
nested_pathspecs() {
  nested_roots | while IFS= read -r nested; do printf ':(exclude)%s/\n' "$nested"; done
}
