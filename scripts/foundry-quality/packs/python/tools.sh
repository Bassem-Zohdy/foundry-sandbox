#!/bin/sh
# python quality hook, run by scripts/foundry-quality/run.sh: ruff, py_limits.py, py_arch.py,
# vulture and jscpd in the pack root, each written as <q>/python-<tool>.sarif with repo paths.
#
# Every tool reads the same files: the .py files under the root that git tracks or would track,
# less quality.exclude and the other packs' roots inside this one (FOUNDRY_OTHER_ROOTS).
#
# Usage: sh tools.sh <q> <root>
# Exit: 0 the tools ran (violations are not failures), 2 a tool is missing, crashed, wrote output
# that cannot be read, or looked at nothing.
set -eu
[ "$#" -eq 2 ] || { echo "usage: tools.sh <q> <root>" >&2; exit 2; }
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../lib.sh"
. "$here/python.sh"

# Route and task registration: called by the framework, never by name.
DECORATORS='@*.get,@*.post,@*.put,@*.patch,@*.delete,@*.websocket,@*.on_event,@*.exception_handler,@*.task,@shared_task,@*.middleware'

sources_exist() {
  # shellcheck disable=SC2046 # pack roots hold no spaces or glob characters
  [ -n "$(git ls-files -- '*.py' $(nested_pathspecs) ':(exclude,top).claude/' \
    ':(exclude,top)scripts/foundry-ci/' ':(exclude,top)scripts/foundry-quality/' | head -n 1)" ]
}

py_files() {
  "$py" "$here/foundry_json.py" files . >"$work/files.list" 2>"$work/files.err" ||
    { cat "$work/files.err" >&2; fail "the Python files could not be listed"; }
  if [ ! -s "$work/files.list" ]; then
    if sources_exist; then
      fail "python looked at no files although $root has .py files (check quality.exclude)"
    fi
    fail "pack python is enabled but $root has no .py files"
  fi
}

py_checker() { # name script
  status=0
  "$py" "$here/$2" . "$q/python-$1.sarif" >"$work/$1.log" 2>"$work/$1.err" || status=$?
  cat "$work/$1.log"
  finished "$1" "$status" 0 "$q/python-$1.sarif"
}

# vulture honours `# noqa` and jscpd honours `jscpd:ignore-start` / `-end`, and neither has a
# switch to turn that off, so both read copies of the files in the scratch area with every
# `noqa` and every jscpd marker neutralised (same paths, same line numbers); their output paths
# are then the real root-relative ones.
py_copies() { # file...
  rm -rf "$work/src"
  "$py" - "$work/src" "$@" <<'PY' 2>"$work/copy.err" ||
import re, sys
from pathlib import Path
noqa = re.compile(r"#(\s*)noqa", re.IGNORECASE)
marker = re.compile(r"(jscpd\s*):(\s*ignore-(?:start|end))", re.IGNORECASE)
for name in sys.argv[2:]:
    dest = Path(sys.argv[1]) / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = Path(name).read_bytes().decode("utf-8", "surrogateescape")
    text = marker.sub(r"\1-\2", noqa.sub(r"#\1no-qa", text))
    dest.write_bytes(text.encode("utf-8", "surrogateescape"))
PY
    { cat "$work/copy.err" >&2; fail "the files for vulture and jscpd could not be copied"; }
}

py_vulture() { # file...
  [ -f vulture_whitelist.py ] ||
    fail "$root/vulture_whitelist.py is missing; restore it with 'foundry template apply'"
  status=0
  (cd "$work/src" && "$venv/bin/vulture" --min-confidence 60 \
    --ignore-decorators "$DECORATORS" -- "$@") >"$work/vulture.txt" 2>"$work/vulture.err" ||
    status=$?
  # 0 nothing unused, 3 something unused; 1 and 2 are a bad file or bad arguments.
  case $status in
    0 | 3) ;;
    *) cat "$work/vulture.txt" "$work/vulture.err" >&2; fail "vulture crashed (exit $status)" ;;
  esac
  to_sarif vulture "$work/vulture.txt" "$q/python-vulture.sarif" "$root"
}

jscpd_run() { # config
  (cd "$work/src" && "$venv/node/node_modules/.bin/jscpd" --config "$1" --format python --silent .)
}

# jscpd cannot take a file list (it then reports bare file names), so it reads the copies (the
# files the other tools read, markers neutralised) with the exclude globs, the nested roots and
# the test files as ignores. The copies hold only listed files, so git's ignores are not needed.
py_jscpd() {
  "$py" "$here/foundry_json.py" exclude . >"$work/exclude.list" ||
    fail "quality.exclude could not be read from foundry.json"
  "$py" - "$work/exclude.list" "$work/jscpd" >"$work/jscpd-config.json" <<'PY' ||
import json, os, sys
globs = [g for g in open(sys.argv[1], encoding="utf-8").read().splitlines() if g]
nested = [r.strip("/") + "/**" for r in os.environ.get("FOUNDRY_OTHER_ROOTS", "").splitlines() if r.strip()]
common = ["**/.venv/**", "**/venv/**", "**/node_modules/**", "**/__pycache__/**",
          "**/tests/**", "**/test_*.py", "**/*_test.py", "**/conftest.py"]
print(json.dumps({"minTokens": 70, "minLines": 5, "reporters": ["json"], "gitignore": False,
                  "absolute": False, "output": sys.argv[2], "ignore": globs + nested + common}))
PY
    fail "jscpd config could not be built"
  status=0
  jscpd_run "$work/jscpd-config.json" >"$work/jscpd.log" 2>"$work/jscpd.err" || status=$?
  finished jscpd "$status" 0 "$work/jscpd/jscpd-report.json"
  require_jscpd_inspected "$work/jscpd/jscpd-report.json" "$work/jscpd-config.json"
  to_sarif jscpd "$work/jscpd/jscpd-report.json" "$q/python-jscpd.sarif" "$root"
}

py_enter "$1" "$2"
rm -rf "$work/jscpd"
py_files
set --
while IFS= read -r file; do set -- "$@" "$file"; done <"$work/files.list"
py_ruff "$q/python-ruff.sarif" "$@"
py_checker limits py_limits.py
require_inspected py_limits "$q/python-limits.sarif" 'sum(len(r.get("artifacts") or []) for r in d["runs"])'
py_checker arch py_arch.py
py_copies "$@"
py_vulture "$@"
py_jscpd
