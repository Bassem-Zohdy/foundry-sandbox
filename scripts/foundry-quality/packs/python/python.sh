# Shared steps of the python pack's hooks (tools.sh, staged.sh). Source it after lib.sh.
#
# The tools live in the Foundry scratch area, not in the project's environment:
# <git-common-dir>/foundry/tools/python-venv, made by setup.sh from requirements-tools.txt, with
# jscpd (npm) under its node/ folder.

py_venv() {
  common=$(cd "$(git rev-parse --git-common-dir)" && pwd) || fail "git has no common dir"
  echo "$common/foundry/tools/python-venv"
}

# Changes into the pack root and sets venv, py and work; fails with the setup command when the
# tools are missing.
py_enter() { # q root
  q=$1 root=$2
  venv=$(py_venv)
  py="$venv/bin/python"
  work="$q/tmp/python"
  mkdir -p "$work"
  if [ ! -x "$venv/bin/ruff" ] || [ ! -x "$venv/bin/vulture" ] ||
    [ ! -x "$venv/node/node_modules/.bin/jscpd" ]; then
    fail "the python quality tools are not installed; run: sh scripts/foundry-quality/packs/python/setup.sh $root"
  fi
  cd "$repo/$root" || fail "pack python: its root $root is not a folder"
  [ -f ruff-foundry.toml ] ||
    fail "$root/ruff-foundry.toml is missing; restore it with 'foundry template apply'"
}

# ruff with Foundry's config only (a project's ruff.toml cannot weaken it) and --ignore-noqa
# (no inline `# noqa` bypass of the gate), on the given root-relative files. Bandit (S) results become warnings: advisory, listed and not counted.
py_ruff() { # out.sarif file...
  out=$1
  shift
  status=0
  "$venv/bin/ruff" check --config ruff-foundry.toml --ignore-noqa --no-cache --output-format sarif -- "$@" \
    >"$work/ruff.sarif" 2>"$work/ruff.err" || status=$?
  finished ruff "$status" 1 "$work/ruff.sarif"
  "$py" - "$work/ruff.sarif" "$out" <<'PY' 2>"$work/ruff-advisory.err" ||
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
for run in data["runs"]:
    for rule in run["tool"]["driver"].get("rules") or []:
        if str(rule.get("id", "")).startswith("S"):
            rule.setdefault("defaultConfiguration", {})["level"] = "warning"
    for result in run.get("results") or []:
        if str(result.get("ruleId") or "").startswith("S"):
            result["level"] = "warning"
json.dump(data, open(sys.argv[2], "w", encoding="utf-8"))
PY
    { cat "$work/ruff-advisory.err" >&2; fail "ruff output cannot be read ($work/ruff.sarif)"; }
}
