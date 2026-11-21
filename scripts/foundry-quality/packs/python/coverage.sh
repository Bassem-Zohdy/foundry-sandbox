#!/bin/sh
# python coverage, run by the `foundry / coverage` job: pytest with pytest-cov in the pack root,
# measuring foundry.json python.package, written as <out-dir>/python.lcov with repo-relative
# paths (lcov, like every pack: diff-cover cannot mix lcov and XML reports).
#
# The tests run in the Python on PATH (`python`, else `python3`): in CI the one the foundry-setup
# action filled with `setup.sh <root> --project`; locally the project's own environment.
#
# Usage: sh coverage.sh <root> <out-dir>
# Exit: 0 the report was written, 1 the tests failed or wrote no report, 2 usage, config or a
# missing pytest-cov.
set -eu
[ "$#" -eq 2 ] || { echo "usage: coverage.sh <root> <out-dir>" >&2; exit 2; }
here=$(cd "$(dirname "$0")" && pwd)
. "$here/../../lib.sh"

root=$1
mkdir -p "$2"
out=$(cd "$2" && pwd)
[ -d "$repo/$root" ] || fail "pack root $root is not a folder"
cd "$repo/$root"
pkg=$(python3 "$here/foundry_json.py" package .) || exit 2
pyexe=$(command -v python || command -v python3) || fail "no python or python3 on PATH"
if ! "$pyexe" -c 'import pytest_cov' >/dev/null 2>&1; then
  fail "pytest-cov is not installed in $pyexe; run: $pyexe -m pip install pytest-cov==7.1.0 (CI installs it with: sh scripts/foundry-quality/packs/python/setup.sh $root --project)"
fi

# coverage's data file goes to the scratch area, not into the project.
work="$(quality_dir)/tmp/python"
mkdir -p "$work"
rm -f "$out/python.lcov" "$work/.coverage"
status=0
COVERAGE_FILE="$work/.coverage" "$pyexe" -m pytest -p no:cacheprovider --cov="$pkg" \
  "--cov-report=lcov:$out/python.lcov" </dev/null || status=$?
if [ "$status" -ne 0 ]; then
  echo "::error::the python tests in $root failed (pytest exit $status)"
  exit 1
fi
if ! grep -q '^SF:' "$out/python.lcov" 2>/dev/null; then
  echo "::error::pytest wrote no coverage for $pkg in $out/python.lcov; check that the tests import $pkg"
  exit 1
fi

# coverage.py writes paths relative to the pack root; the gate reads them from the repo root.
lead=""
if [ "$root" != . ]; then lead="$root/"; fi
sed -e 's|^SF:\./|SF:|' -e "s|^SF:\([^/]\)|SF:$lead\1|" "$out/python.lcov" >"$work/python.lcov"
mv "$work/python.lcov" "$out/python.lcov"
