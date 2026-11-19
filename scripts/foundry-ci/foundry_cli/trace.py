"""`foundry trace`: goal-to-test traceability report and the G1/G2 gate rules (D7).

Reads the documents through `docs_ids.parse_docs` and scans the files matching
`paths.tests` for FR and NFR IDs. Placeholder rows (an ID with no text) are not
items; they are listed as "not written yet" and never count toward coverage.
"""
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

from .cli import register
from .config import Config, ConfigError, load_config
from .docs_ids import KIND_PATTERNS, Docs, is_plain_file_in, module_code, parse_docs
from .globs import matches

_SKIP_DIRS = {".git", "node_modules"}
_CODE_ID = re.compile(
    r"(?<![A-Za-z0-9_-])(?:%s|%s)(?![A-Za-z0-9_-])"
    % (KIND_PATTERNS["FR"], KIND_PATTERNS["NFR"]), re.ASCII)
_GATES = ("G1", "G2", "pr")


@dataclass
class Report:
    # {"goals": {BG: [ST]}, "stories": {ST: [FR]},
    #  "requirements": {FR|NFR: {"cases": [TC], "code": ["path:line"]}}}
    chains: Dict[str, Dict] = field(default_factory=dict)
    orphans: List[str] = field(default_factory=list)     # items missing a link
    dangling: List[str] = field(default_factory=list)    # code IDs with no definition
    placeholders: List[str] = field(default_factory=list)
    # (first gate that fails on it, line); unknown links and duplicates included
    findings: List[Tuple[str, str]] = field(default_factory=list)

    def failures(self, gate: str) -> List[str]:
        """Lines that fail `gate`: G1 its own rules, G2 and pr every rule."""
        if gate not in _GATES:
            raise ValueError(f"unknown gate: {gate}")
        return [line for g, line in self.findings if gate != "G1" or g == "G1"]


def _loc_key(loc: str):
    path, _, n = loc.rpartition(":")
    return path, int(n)


def _raise(e: OSError) -> None:
    raise e


def _code_hits(config: Config, repo: Path) -> Dict[str, List[str]]:
    """{requirement ID: ["path:line", ...]} found in files matching paths.tests.

    Unreadable files and directories raise OSError; `_run` catches it around
    `trace_report` and prints `cannot read <path>`, exit 2.
    """
    found_at: Dict[str, set] = {}
    for root, dirs, files in os.walk(repo, onerror=_raise):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for name in sorted(files):
            full = Path(root) / name
            rel = full.relative_to(repo).as_posix()
            if not matches(rel, config.test_globs) or not is_plain_file_in(repo, full):
                continue
            text = full.read_text(encoding="utf-8", errors="replace")
            for n, line in enumerate(text.splitlines(), 1):
                for found in _CODE_ID.findall(line):
                    found_at.setdefault(found, set()).add((rel, n))
    return {i: [f"{p}:{n}" for p, n in sorted(at)] for i, at in found_at.items()}


def _kinds(docs: Docs, *kinds: str) -> List[str]:
    return sorted(i for i, it in docs.items.items() if it.kind in kinds)


def _known(docs: Docs, link: str, kind: str) -> bool:
    return link in docs.items and docs.items[link].kind == kind


def trace_report(docs: Docs, config: Config, repo: Path) -> Report:
    items = docs.items
    rep = Report()
    code = _code_hits(config, Path(repo))

    story_goals = {s: [b for b in items[s].links if _known(docs, b, "BG")]
                   for s in _kinds(docs, "ST")}
    goals = {b: sorted(s for s, bs in story_goals.items() if b in bs)
             for b in _kinds(docs, "BG")}
    stories = {s: sorted(f for f in _kinds(docs, "FR") if s in items[f].links)
               for s in story_goals}
    reqs = {}
    for r in _kinds(docs, "FR", "NFR"):
        cases = sorted(t for t in _kinds(docs, "TC") if r in items[t].links)
        reqs[r] = {"cases": cases, "code": code.get(r, [])}
    rep.chains = {"goals": goals, "stories": stories, "requirements": reqs}

    def add(gate: str, line: str, orphan: bool = False) -> None:
        rep.findings.append((gate, line))
        if orphan:
            rep.orphans.append(line)

    for kind, gate, what, doc in (("BG", "G1", "business goals", "docs/brd.md"),
                                  ("ST", "G1", "stories", "docs/prd.md"),
                                  ("FR", "G2", "functional requirements",
                                   "docs/srs.md")):
        if not _kinds(docs, kind):
            add(gate, f"no {what} defined in {doc}")
    for a, b in docs.duplicates:
        add("G1", f"{a.id}: duplicate ID ({a.path}:{a.line} and {b.path}:{b.line})")
    for b, sts in goals.items():
        if not sts:
            add("G1", f"{b}: no story reaches this goal", True)
    for s, item in ((s, items[s]) for s in story_goals):
        if not item.links:
            add("G1", f"{s}: has no goal", True)
        for link in item.links:
            if not _known(docs, link, "BG"):
                add("G1", f"{s}: links unknown goal {link}")
    for f in _kinds(docs, "FR"):
        links = items[f].links
        if not links:
            add("G2", f"{f}: has no story", True)
        for link in links:
            if not _known(docs, link, "ST"):
                add("G2", f"{f}: links unknown story {link}")
        if not reqs[f]["cases"]:
            add("G2", f"{f}: has no test case", True)
    in_modules = {f.split("-")[1] for f in _kinds(docs, "FR")}
    for module in sorted(config.modules):
        code_ = module_code(module)
        if not code_ or not code_[0].isalpha():
            add("G2", f"module {module}: name cannot form an FR id (FR-<CODE>-###)")
        elif code_ not in in_modules:
            add("G2", f"module {module}: has no functional requirement", True)

    seen: Dict[str, List[str]] = {}
    for ref, hits in code.items():
        if ref not in items:
            seen.setdefault(ref, []).extend(hits)
    for ref, path, n in docs.references:
        if ref not in items:
            seen.setdefault(ref, []).append(f"{path}:{n}")
    for ref in sorted(seen):
        rep.dangling.append(f"{ref}: referenced in {', '.join(sorted(set(seen[ref]), key=_loc_key))}"
                            " but not defined")
    rep.placeholders = sorted(i.id for i in docs.placeholders)
    return rep


def _json(rep: Report) -> str:
    return json.dumps({
        "chains": rep.chains, "dangling": sorted(rep.dangling),
        "orphans": sorted(rep.orphans), "placeholders": rep.placeholders,
    }, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _md(rep: Report, docs: Docs) -> str:
    def text(i: str) -> str:
        return docs.items[i].text if i in docs.items else ""

    out = ["# Traceability report", "", "## Goals to stories"]
    out += [f"- {b} {text(b)}: " + (", ".join(s) or "(none)")
            for b, s in rep.chains["goals"].items()]
    out += ["", "## Stories to functional requirements"]
    out += [f"- {s} {text(s)}: " + (", ".join(f) or "(none)")
            for s, f in rep.chains["stories"].items()]
    out += ["", "## Requirements to tests"]
    for r, t in rep.chains["requirements"].items():
        out.append(f"- {r} {text(r)}: cases " + (", ".join(t["cases"]) or "(none)")
                   + "; code " + (", ".join(t["code"]) or "(none)"))
    for title, rows in (("Orphans", rep.orphans), ("Dangling references", rep.dangling),
                        ("Not written yet", rep.placeholders)):
        out += ["", f"## {title}"] + ([f"- {r}" for r in sorted(rows)] or ["- none"])
    return "\n".join(out) + "\n"


def _configure(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", default=".", help="repo root containing foundry.json")
    p.add_argument("--gate", choices=list(_GATES), help="fail (G1, G2) or warn (pr)")
    p.add_argument("--format", choices=["md", "json"], default="md")


def _run(ns: argparse.Namespace) -> int:
    repo = Path(ns.repo)
    try:
        config = load_config(repo)
        docs = parse_docs(repo)
        rep = trace_report(docs, config, repo)
    except ConfigError as e:
        for problem in e.problems:
            print(problem, file=sys.stderr)
        return 2
    except UnicodeDecodeError as e:
        print(f"cannot read {_path_of(e, repo)}: not valid UTF-8", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"cannot read {e.filename or repo}: {e.strerror or e}", file=sys.stderr)
        return 2
    print(_json(rep) if ns.format == "json" else _md(rep, docs), end="")
    if not ns.gate:
        return 0
    lines = rep.failures(ns.gate)
    for line in lines:
        print(f"warning: {line}" if ns.gate == "pr" else line, file=sys.stderr)
    return 1 if lines and ns.gate != "pr" else 0


def _path_of(e: UnicodeDecodeError, repo: Path) -> str:
    """The document that failed to decode (parse_docs hides which one)."""
    from .docs_ids import DOCUMENTS
    for rel in DOCUMENTS:
        if not is_plain_file_in(repo, Path(repo) / rel):
            continue
        try:
            (Path(repo) / rel).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return rel
        except OSError:
            continue
    return str(repo)


def register_commands() -> None:
    register("trace", _run, _configure)
