"""Quality baseline: count lint violations per file and rule; the counts may only shrink.

Lint tools emit SARIF. Only results whose effective level is `error` count (the
result's level, else its rule's default level, else SARIF's default `warning`);
warnings and notes are advisory and are only reported. A key is `<repo-relative uri>::<ruleId>`, never a line
number, so moving code does not change a count. Baselines live in
`quality/baseline/<tool>.json`. Raising a count needs `--raise "<why>"` and a
`Quality-Baseline-Raise:` commit trailer, which `quality raised` checks for CI.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set
from urllib.parse import unquote

from .cli import register
from .config import ConfigError, load_config
from .globs import matches
from .quality_report import render
from .scratch import scratch_dir

__all__ = ["QualityError", "read_sarif", "read_sarif_advisory", "compare", "load_baselines", "register_commands"]

BASELINE_DIR = "quality/baseline"
TRAILER = "Quality-Baseline-Raise"
Counts = Dict[str, Dict[str, int]]
_TOOL_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")


class QualityError(Exception):
    """Usage or malformed input (exit 2)."""


def _normalise_uri(uri: str, repo: Path, outside: Optional[Set[str]]) -> str:
    if uri.startswith("file://"):
        uri = uri[len("file://"):]
    uri = unquote(uri)
    if not uri.startswith("/"):
        return uri[2:] if uri.startswith("./") else uri
    try:
        return Path(uri).resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        if outside is not None:
            outside.add(uri)
        return uri


def _result_uri(result: dict) -> str:
    try:
        uri = result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
    except (KeyError, IndexError, TypeError):
        return "-"
    return uri if isinstance(uri, str) and uri else "-"


def _rule_levels(driver: dict) -> Dict[str, Optional[str]]:
    return {str(r.get("id")): (r.get("defaultConfiguration") or {}).get("level")
            for r in driver.get("rules") or [] if isinstance(r, dict)}


def _effective_level(result: dict, rules: list, by_id: Dict[str, Optional[str]]) -> str:
    level = result.get("level")
    if level is None:
        idx = result.get("ruleIndex")
        if isinstance(idx, int) and 0 <= idx < len(rules) and isinstance(rules[idx], dict):
            level = (rules[idx].get("defaultConfiguration") or {}).get("level")
    if level is None:
        level = by_id.get(str(result.get("ruleId")))
    return level if isinstance(level, str) else "warning"


def _scan(path: Path, exclude: List[str], repo: Path, outside: Optional[Set[str]],
          paths: Optional[Set[str]] = None) -> "tuple[Counts, Dict[str, int]]":
    """Return (error counts per key, advisory result count) per tool."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise QualityError(f"{path}: cannot read SARIF: {e}")
    out: Counts = {}
    advisory: Dict[str, int] = {}
    try:
        for run in data["runs"]:
            driver = run["tool"]["driver"]
            tool = str(driver["name"]).lower()
            if not _TOOL_NAME.match(tool) or tool in (".", ".."):
                raise QualityError(f"{path}: tool name {tool!r} is not usable as a "
                                   "baseline file name (need [a-z0-9][a-z0-9._-]*)")
            counts = out.setdefault(tool, {})
            advisory.setdefault(tool, 0)
            rules, by_id = driver.get("rules") or [], _rule_levels(driver)
            for result in run.get("results") or []:
                uri = _normalise_uri(_result_uri(result), repo, outside)
                if uri != "-" and matches(uri, exclude):
                    continue
                if paths is not None and uri not in paths:
                    continue
                if _effective_level(result, rules, by_id) != "error":
                    advisory[tool] += 1
                    continue
                key = f"{uri}::{result['ruleId']}"
                counts[key] = counts.get(key, 0) + 1
    except (KeyError, TypeError, AttributeError):
        raise QualityError(f"{path}: malformed SARIF (need runs[].tool.driver.name "
                           "and results[].ruleId)")
    return out, advisory


def read_sarif(path: Path, exclude: List[str], repo: Path = Path("."),
               outside: Optional[Set[str]] = None,
               paths: Optional[Set[str]] = None) -> Counts:
    """Return `{tool: {"<uri>::<ruleId>": count}}` for the error-level results of one SARIF file."""
    return _scan(path, exclude, repo, outside, paths)[0]


def read_sarif_advisory(path: Path, exclude: List[str], repo: Path = Path("."),
                        outside: Optional[Set[str]] = None,
                        paths: Optional[Set[str]] = None) -> Dict[str, int]:
    """Return `{tool: n}`: results that are not errors and so are not counted."""
    return _scan(path, exclude, repo, outside, paths)[1]


def _merge(into: Counts, more: Counts) -> None:
    for tool, counts in more.items():
        dest = into.setdefault(tool, {})
        for k, n in counts.items():
            dest[k] = dest.get(k, 0) + n


def _split(key: str) -> List[str]:
    uri, _, rule = key.rpartition("::")
    return [rule, uri]


def compare(current: Counts, baseline: Counts) -> List[str]:
    """One line per key whose count went up. A tool with no baseline starts at 0."""
    lines = []
    for tool in sorted(current):
        allowed = baseline.get(tool, {})
        for key in sorted(current[tool]):
            now, was = current[tool][key], allowed.get(key, 0)
            if now > was:
                rule, uri = _split(key)
                lines.append(f"{tool} {rule} {uri}: {now} now, {was} allowed")
    return lines


def _baseline_path(repo: Path, tool: str) -> Path:
    path = repo / BASELINE_DIR / f"{tool}.json"
    base = (repo / BASELINE_DIR).resolve()
    if path.resolve().parent != base:
        raise QualityError(f"tool name {tool!r}: baseline path leaves {BASELINE_DIR}")
    return path


def _parse_baseline(text: str, where: str) -> Dict[str, int]:
    try:
        counts = json.loads(text)["counts"]
        if not all(isinstance(k, str) and isinstance(v, int) for k, v in counts.items()):
            raise TypeError
        return counts
    except (ValueError, KeyError, TypeError, AttributeError):
        raise QualityError(f"{where}: malformed baseline (need {{\"tool\", \"counts\"}})")


def _load_baselines(repo: Path, tools: List[str]) -> Counts:
    out: Counts = {}
    for tool in tools:
        p = _baseline_path(repo, tool)
        if p.is_file():
            out[tool] = _parse_baseline(p.read_text(encoding="utf-8"), str(p))
    return out


def load_baselines(repo: Path, tools: List[str]) -> Counts:
    """Public form of the baseline loader for other commands."""
    return _load_baselines(repo, tools)


def _paths(ns: argparse.Namespace) -> Optional[Set[str]]:
    given = getattr(ns, "paths", None)
    if not given:
        return None
    repo = Path(ns.repo)
    return {_normalise_uri(p, repo, None) for p in given}


def _collect(ns: argparse.Namespace, advisory: Optional[Dict[str, int]] = None) -> Counts:
    repo = Path(ns.repo)
    try:
        exclude = load_config(repo).quality_exclude
    except ConfigError as e:
        raise QualityError("; ".join(e.problems))
    if not ns.sarif:
        raise QualityError("give at least one --sarif <file>")
    outside: Set[str] = set()
    current: Counts = {}
    for f in ns.sarif:
        counts, adv = _scan(Path(f), exclude, repo, outside, _paths(ns))
        _merge(current, counts)
        for tool, n in adv.items():
            if advisory is not None:
                advisory[tool] = advisory.get(tool, 0) + n
    for uri in sorted(outside):
        print(f"warning: {uri} is outside the repo; counted as given", file=sys.stderr)
    return current


def _configure_sarif(p: argparse.ArgumentParser) -> None:
    p.add_argument("--sarif", action="append", default=[], help="SARIF file (repeatable)")
    p.add_argument("--repo", default=".", help="repo root containing foundry.json")


def _configure_check(p: argparse.ArgumentParser) -> None:
    _configure_sarif(p)
    p.add_argument("--paths", nargs="+", metavar="FILE",
                   help="compare only these repo-relative files (e.g. staged files)")


def _run_check(ns: argparse.Namespace) -> int:
    advisory: Dict[str, int] = {}
    current = _collect(ns, advisory)
    repo = Path(ns.repo)
    baselines = _load_baselines(repo, list(current))
    problems = compare(current, baselines)
    missing = [t for t in sorted(current) if t not in baselines and current[t]]
    for tool in sorted(advisory):
        if advisory[tool]:
            print(f"{tool}: {advisory[tool]} advisory results not counted (warning/note)")
    for line in problems:
        print(line, file=sys.stderr)
    for tool in missing:
        print(f"{tool}: no baseline; run `foundry quality baseline --apply`", file=sys.stderr)
    if problems:
        return 1
    print("quality ok: no new violations")
    return 0


def _shrunk(current: Dict[str, int]) -> Dict[str, int]:
    return {k: current[k] for k in sorted(current) if current[k] > 0}


def _configure_baseline(p: argparse.ArgumentParser) -> None:
    _configure_sarif(p)
    p.add_argument("--apply", action="store_true", help="write the baseline files")
    p.add_argument("--raise", dest="raise_why", metavar="WHY",
                   help="allow counts to rise, with the reason")


def _run_baseline(ns: argparse.Namespace) -> int:
    current = _collect(ns)
    repo = Path(ns.repo)
    old = _load_baselines(repo, list(current))
    rises = compare({t: c for t, c in current.items() if t in old}, old)
    if rises and not ns.raise_why:
        for line in rises:
            print(line, file=sys.stderr)
        print("counts would rise; fix the violations or pass --raise \"<why>\"",
              file=sys.stderr)
        return 1
    for tool in sorted(current):
        counts = _shrunk(current[tool])
        text = json.dumps({"tool": tool, "counts": counts}, indent=2, sort_keys=True) + "\n"
        if ns.apply:
            path = _baseline_path(repo, tool)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(repo).as_posix()} ({sum(counts.values())} violations)")
        else:
            print(f"would write {BASELINE_DIR}/{tool}.json "
                  f"({sum(counts.values())} violations); pass --apply to write")
    if rises and ns.apply:
        print(f"Use this commit trailer: {TRAILER}: {ns.raise_why}")
    return 0


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _head_tools(repo: Path) -> List[str]:
    r = _git(repo, "ls-tree", "--name-only", "HEAD", BASELINE_DIR + "/")
    return sorted(Path(n).stem for n in r.stdout.split() if n.endswith(".json"))


def _show(repo: Path, ref: str, tool: str) -> Optional[Dict[str, int]]:
    r = _git(repo, "show", f"{ref}:{BASELINE_DIR}/{tool}.json")
    if r.returncode != 0:
        return None
    return _parse_baseline(r.stdout, f"{ref}:{BASELINE_DIR}/{tool}.json")


def _configure_raised(p: argparse.ArgumentParser) -> None:
    p.add_argument("--base", required=True, help="ref to compare against")
    p.add_argument("--repo", default=".", help="repo root")


def _run_raised(ns: argparse.Namespace) -> int:
    repo = Path(ns.repo)
    if _git(repo, "rev-parse", "--verify", "--quiet", ns.base + "^{commit}").returncode != 0:
        raise QualityError(f"bad ref: {ns.base}")
    now: Counts = {}
    before: Counts = {}
    for tool in _head_tools(repo):
        head = _show(repo, "HEAD", tool)
        base = _show(repo, ns.base, tool)
        if head is not None and base is not None:  # a tool absent at base is new, not raised
            now[tool], before[tool] = head, base
    rises = compare(now, before)
    if not rises:
        print("no baseline count raised")
        return 0
    log = _git(repo, "log", f"--format=%(trailers:key={TRAILER},valueonly)",
               f"{ns.base}..HEAD")
    if log.stdout.strip():
        print(f"baseline raised, with a {TRAILER} trailer")
        return 0
    for line in rises:
        print(line, file=sys.stderr)
    print(f"baseline raised without a {TRAILER} trailer in {ns.base}..HEAD", file=sys.stderr)
    return 1


def _configure_report(p: argparse.ArgumentParser) -> None:
    _configure_sarif(p)
    p.add_argument("--top", type=int, default=10, help="rows in the top lists (default 10)")


def _run_report(ns: argparse.Namespace) -> int:
    if ns.top < 1:
        raise QualityError("--top must be at least 1")
    repo = Path(ns.repo)
    advisory: Optional[Dict[str, int]] = None
    current: Optional[Counts] = None
    if ns.sarif:
        advisory = {}
        current = _collect(ns, advisory)
    stored = sorted(p.stem for p in (repo / BASELINE_DIR).glob("*.json"))
    baseline = _load_baselines(repo, sorted(set(stored) | set(current or {})))
    if current is None and not baseline:
        raise QualityError("nothing to report: no baseline files and no --sarif given")
    text = render(current, baseline, advisory, ns.top)
    try:
        target = scratch_dir(repo) / "quality"
        target.mkdir(parents=True, exist_ok=True)
        (target / "report.md").write_text(text, encoding="utf-8")
    except (ValueError, OSError) as e:
        raise QualityError(f"cannot write the report: {e}")
    print(text, end="")
    return 0


def _guard(fn):
    def run(ns: argparse.Namespace) -> int:
        try:
            return fn(ns)
        except QualityError as e:
            print(str(e), file=sys.stderr)
            return 2
    return run


def register_commands() -> None:
    register("quality check", _guard(_run_check), _configure_check)
    register("quality baseline", _guard(_run_baseline), _configure_baseline)
    register("quality report", _guard(_run_report), _configure_report)
    register("quality raised", _guard(_run_raised), _configure_raised)
