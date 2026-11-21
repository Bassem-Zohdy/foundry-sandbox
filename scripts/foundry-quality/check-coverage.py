#!/usr/bin/env python3
"""Fail closed when the coverage reports cannot back a diff-coverage gate.

Usage: check-coverage.py <report>... --base <git ref>
       check-coverage.py --count-changed --base <git ref>

A report is lcov (`*.lcov`, `*.info`) or Cobertura XML (`*.xml`); each pack's coverage.sh writes
one. Together they must pass:
1. At least half of the covered paths must name files that exist under the repo root.
2. foundry.json `paths.source` must list globs (`*`, `**`, `?`); empty fails.
3. Every added, modified or renamed (target path) source file (foundry.json `paths.source`, minus
   `quality.exclude`, tests and declaration files) must be in some report.
Exit 0 ok, 1 a check failed (GitHub `::error::` lines), 2 usage or unreadable input.

`--count-changed` prints the number of changed source files (the files of check 3, Python and
TypeScript alike) and reads no report: the coverage job's "the gate checked nothing" guard.
"""
import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

CODE = re.compile(r"\.(?:[cm]?[jt]s|[jt]sx|py)$")
TEST = re.compile(r"(?:\.(?:spec|test)\.[^/]+$)|(?:\.d\.[cm]?ts$)|(?:^|/)e2e/"
                  r"|(?:(?:^|/)test_[^/]*\.py$)|(?:_test\.py$)|(?:(?:^|/)conftest\.py$)"
                  r"|(?:(?:^|/)tests/.+\.py$)")


class Unreadable(Exception):
    """A report that cannot be read (exit 2)."""


def glob_re(pattern):
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


def matches(path, patterns):
    return any(glob_re(p).match(path) for p in patterns)


def normalize(raw, root):
    p = Path(raw)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(root).as_posix()
        except ValueError:
            return raw
    return p.as_posix().removeprefix("./")


def lcov_paths(path, root):
    text = Path(path).read_text(encoding="utf-8")
    return {normalize(m, root) for m in re.findall(r"(?m)^SF:(.+)$", text)}


def cobertura_paths(path, root):
    """Class file names, resolved against the report's <source> folders when that names a file."""
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        raise Unreadable(f"{path}: not Cobertura XML: {e}")
    sources = [Path(s.text.strip()) for s in tree.iter("source") if s.text and s.text.strip()]
    out = set()
    for cls in tree.iter("class"):
        name = cls.get("filename")
        if not name:
            continue
        candidates = [s / name for s in sources] + [Path(name)]
        found = next((c for c in candidates if (root / c).is_file()), None)
        out.add(normalize(str(found if found is not None else name), root))
    return out


def covered_paths(reports, root):
    covered = set()
    for report in reports:
        try:
            reader = cobertura_paths if report.endswith(".xml") else lcov_paths
            covered |= reader(report, root)
        except OSError as e:
            raise Unreadable(f"{report}: {e}")
    return covered


def changed_files(base):
    # -z and quotepath=off: non-ASCII (Arabic) and spaced paths come back whole and unquoted.
    r = subprocess.run(["git", "-c", "core.quotepath=off", "diff", "-z", "--name-only",
                        "--diff-filter=AMR", f"{base}...HEAD"], capture_output=True)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").strip()
        print(f"check-coverage: git diff against {base} failed: {err}", file=sys.stderr)
        sys.exit(2)
    return [f for f in r.stdout.decode("utf-8", "surrogateescape").split("\0") if f]


def changed_sources(base, cfg):
    """Added, modified or renamed source files: paths.source, less excludes, tests, declarations."""
    source = cfg.get("paths", {}).get("source", [])
    exclude = cfg.get("quality", {}).get("exclude", [])
    return [f for f in changed_files(base)
            if CODE.search(f) and matches(f, source) and not TEST.search(f)
            and not matches(f, exclude)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reports", nargs="*")
    ap.add_argument("--base", required=True)
    ap.add_argument("--count-changed", action="store_true")
    ns = ap.parse_args()
    if not ns.reports and not ns.count_changed:
        ap.error("give at least one coverage report")
    root = Path.cwd().resolve()
    try:
        cfg = json.loads((root / "foundry.json").read_text(encoding="utf-8"))
        if ns.count_changed:
            print(len(changed_sources(ns.base, cfg)))
            return 0
        covered = covered_paths(ns.reports, root)
    except (OSError, ValueError, Unreadable) as e:
        print(f"check-coverage: {e}", file=sys.stderr)
        return 2
    real = {p for p in covered if (root / p).is_file()}
    if not covered or len(real) * 2 < len(covered):
        print("::error::coverage paths must be relative to the repo root: "
              f"{len(real)} of {len(covered)} covered paths name a file here. "
              "Each pack's coverage.sh writes them relative to the repo root; for Jest set rootDir "
              "to the pack root (not src) and collectCoverageFrom to src/**, for Vitest keep the "
              "default root; then rerun test:coverage")
        return 1
    source = cfg.get("paths", {}).get("source", [])
    if not source:
        print('::error::foundry.json paths.source is empty; set it to the globs of your source files (e.g. ["src/**"])')
        return 1
    missing = [f for f in changed_sources(ns.base, cfg) if f not in covered]
    for f in missing:
        print(f"::error file={f}::changed source file is in no coverage report; "
              "add it to the coverage include (collectCoverageFrom, coverage.include or --cov) "
              "or exclude it in quality.exclude")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
