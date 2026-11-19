"""`foundry gate prepare|sync|checklist`: gate PRs and the state moves they carry (D6).

`prepare` puts everything a gate changes (the findings file and the module's
state move, plus any gate records synced first) in one change, before the tech
lead is asked: a commit added to the gate PR afterwards would void the approval
bound to its head. `sync` writes a record for a merged, approved gate PR; those
records enter the repo through the next gate PR, never as a commit on a merged one.
"""
import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .approval import GATE_LABELS, check_pr
from .cli import register
from .config import Config, ConfigError, load_config
from .docs_ids import parse_docs
from .gate_checklists import CHECKLISTS, decided_evidence
from .gh import GhError, GitHub, get_github
from .state import GateRecord, ModuleState, advance, load_state, record_gate, save_state
from .trace import _md as trace_md
from .trace import _path_of, trace_report

GATES_DIR = "docs/foundry/gates"
SUPPORTED = ("G1", "G2")
_MODULE = r"([A-Za-z0-9][A-Za-z0-9_.-]*)"


class UsageError(Exception):
    """A usage, config or IO problem: one stderr line, exit 2."""


@dataclass
class Pending:
    module: str
    gate: str
    record: GateRecord


@dataclass
class SyncPlan:
    pending: List[Pending] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _git_user(repo: Path) -> str:
    try:
        p = subprocess.run(["git", "-C", str(repo), "config", "user.name"],
                           capture_output=True, text=True)
    except OSError:
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def findings_path(gate: str, module: str) -> str:
    return f"{GATES_DIR}/{gate}-{module}.md"


def plan_sync(gh: GitHub, config: Config, repo: Path) -> SyncPlan:
    """Records to write for merged, approved gate PRs. Writes nothing.

    One decision per (module, gate): the latest merged, approved PR. A gate that
    already has a record is never rewritten (a differing candidate is only
    noted), so a second run plans nothing. A merged gate PR without a passing
    approval check, or whose head differs from the one it merged with, is an
    error and gets no record.
    """
    plan = SyncPlan()
    candidates: Dict[Tuple[str, str], GateRecord] = {}
    prs = sorted(gh.merged_prs(config.repo), key=lambda m: (m.merged_at, m.number))
    for pr in prs:
        for label in [l for l in pr.labels if l in GATE_LABELS]:
            gate = label.split(":", 1)[1]
            where = f"PR #{pr.number} ({label})"
            if gate not in SUPPORTED:
                plan.notes.append(f"skipped {where}: {gate} records are not synced yet")
                continue
            rx = re.compile(r"^%s/%s-%s\.md$" % (re.escape(GATES_DIR), gate, _MODULE))
            modules = sorted({m.group(1) for f in gh.pr_files(config.repo, pr.number)
                              for m in [rx.match(f)] if m})
            if len(modules) != 1:
                plan.errors.append(f"{where}: expected one {findings_path(gate, '<module>')} "
                                   f"in the PR, found {len(modules)}")
                continue
            module = modules[0]
            if module not in config.modules:
                plan.errors.append(f"{where}: module {module!r} is not in foundry.json")
                continue
            live = gh.pr_head(config.repo, pr.number)
            if live.lower() != pr.head_sha.lower():
                plan.errors.append(f"{where}: head {live[:7]} is not the head it merged "
                                   f"with ({pr.head_sha[:7]}), no record written")
                continue
            result = check_pr(gh, config, pr.number, f"gate:{gate}")
            if not result.ok:
                plan.errors.append(f"{where}: merged without approval, no record written "
                                   f"({result.reason})")
                continue
            candidates[(module, gate)] = GateRecord(
                config.tech_lead, pr.merged_at, pr.number, pr.head_sha)
    for (module, gate), rec in sorted(candidates.items()):
        have = load_state(repo, module).gates.get(gate)
        if have is None:
            plan.pending.append(Pending(module, gate, rec))
        elif have != rec:
            plan.notes.append(f"{module} {gate} already recorded from PR #{have.pr}; "
                              f"PR #{rec.pr} not recorded")
    return plan


def apply_plan(repo: Path, plan: SyncPlan,
               states: Optional[Dict[str, ModuleState]] = None) -> Dict[str, ModuleState]:
    """Add the planned records to the states (in memory); `save_states` writes them."""
    states = {} if states is None else states
    for p in plan.pending:
        if p.module not in states:
            states[p.module] = load_state(repo, p.module)
        record_gate(states[p.module], p.gate, p.record)
    return states


def save_states(repo: Path, states: Dict[str, ModuleState]) -> None:
    for s in states.values():
        save_state(repo, s)


def _describe(p: Pending) -> str:
    r = p.record
    return (f"{p.module} {p.gate}: PR #{r.pr}, head {r.commit[:7]}, merged {r.at}, "
            f"approved by {r.approved_by}")


def _print_plan(plan: SyncPlan, apply: bool, prefix: str = "", file=None) -> None:
    file = file or sys.stdout
    for note in plan.notes:
        print(f"{prefix}note: {note}", file=file)
    for p in plan.pending:
        print(f"{prefix}{'recorded' if apply else 'would record'} {_describe(p)}", file=file)


def _fence(text: str) -> str:
    longest = max([len(m) for m in re.findall(r"`+", text)] + [2])
    return "`" * (longest + 1)


def _cell(text: str) -> str:
    return text.replace("|", chr(92) + "|")


def build_findings(gate: str, module: str, report_md: str, failures: List[str],
                   evidence: Optional[Dict[str, str]] = None) -> str:
    """The findings file; `evidence` pre-fills the cells prepare can decide (R29)."""
    evidence = evidence or {}
    rows = ["| ID | Item | Evidence |", "| --- | --- | --- |"]
    rows += [f"| {i} | {_cell(t)} | {_cell(evidence.get(i, ''))} |"
             for i, t in CHECKLISTS[gate]]
    block = report_md.rstrip("\n") + "\n\n" + (
        "\n".join(f"failure: {f}" for f in failures) if failures
        else f"no failures from trace --gate {gate}")
    fence = _fence(block)
    return "\n".join([f"# {gate} findings: {module}", "", *rows, "",
                      "## Traceability", "",
                      f"`trace --gate {gate}` output:", "",
                      fence, block, fence, ""])


def _load(ns: argparse.Namespace) -> Config:
    try:
        return load_config(Path(ns.repo))
    except ConfigError as e:
        raise UsageError("; ".join(e.problems)) from e


def _guard(fn):
    def wrapper(ns: argparse.Namespace) -> int:
        try:
            return fn(ns)
        except UsageError as e:
            print(f"foundry: {e}", file=sys.stderr)
            return 2
        except GhError as e:
            print(str(e).splitlines()[0] if str(e) else "GitHub call failed", file=sys.stderr)
            return 2
        except ValueError as e:  # invalid state file or module name
            print(f"foundry: {e}", file=sys.stderr)
            return 2
    return wrapper


@_guard
def _sync(ns: argparse.Namespace) -> int:
    repo, config = Path(ns.repo), _load(ns)
    plan = plan_sync(get_github(), config, repo)
    states = apply_plan(repo, plan)
    if ns.apply:
        save_states(repo, states)
    _print_plan(plan, ns.apply)
    if not plan.pending and not plan.errors:
        print("nothing to sync")
    for e in plan.errors:
        print(e, file=sys.stderr)
    return 1 if plan.errors else 0


def _trace(config: Config, repo: Path):
    try:
        docs = parse_docs(repo)
        return trace_report(docs, config, repo), docs
    except UnicodeDecodeError as e:
        raise UsageError(f"cannot read {_path_of(e, repo)}: not valid UTF-8") from e
    except OSError as e:
        raise UsageError(f"cannot read {e.filename or repo}: {e.strerror or e}") from e


@_guard
def _prepare(ns: argparse.Namespace) -> int:
    repo, config, gate, module = Path(ns.repo), _load(ns), ns.gate, ns.module
    if module not in config.modules:
        raise UsageError(f"module {module!r} is not in foundry.json "
                         f"(modules: {', '.join(config.modules)})")
    by = _git_user(repo)
    if not by:
        raise UsageError("git config user.name is not set; the state history needs it")
    plan = plan_sync(get_github(), config, repo)
    states = apply_plan(repo, plan)
    state = states.setdefault(module, load_state(repo, module))
    try:
        advance(state, gate, by, _now())
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    if gate == "G2" and "G1" not in state.gates:
        print(f"gate G2 cannot be requested: module {module} has no approved G1 record "
              f"(merge an approved G1 PR first)", file=sys.stderr)
        return 1
    rep, docs = _trace(config, repo)
    try:
        evidence = decided_evidence(gate, repo)
    except UnicodeDecodeError as e:
        raise UsageError(f"cannot read {_path_of(e, repo)}: not valid UTF-8") from e
    except OSError as e:
        raise UsageError(f"cannot read {e.filename or repo}: {e.strerror or e}") from e
    text = build_findings(gate, module, trace_md(rep, docs), rep.failures(gate), evidence)
    path = findings_path(gate, module)
    if ns.apply:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        save_states(repo, states)
    else:
        print("dry run: nothing written; use --apply", file=sys.stderr)
    _print_plan(plan, ns.apply, "sync: ", sys.stderr)
    for e in plan.errors:
        print(f"warning: {e}", file=sys.stderr)
    print(f"branch: gate-{gate.lower()}-{module}")
    print(f"label: gate:{gate}")
    print(f"findings: {path}")
    print()
    print(text, end="")
    return 0


def _checklist(ns: argparse.Namespace) -> int:
    items = CHECKLISTS[ns.gate]
    if ns.format == "json":
        print(json.dumps([{"id": i, "text": t} for i, t in items], indent=2,
                         ensure_ascii=False))
    else:
        for i, t in items:
            print(f"{i} {t}")
    return 0


def _repo(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", default=".", help="repo root containing foundry.json")


def _configure_prepare(p: argparse.ArgumentParser) -> None:
    p.add_argument("gate", choices=list(SUPPORTED))
    p.add_argument("--module", required=True)
    p.add_argument("--apply", action="store_true", help="write the findings and state")
    _repo(p)


def _configure_sync(p: argparse.ArgumentParser) -> None:
    p.add_argument("--apply", action="store_true", help="write the records")
    _repo(p)


def _configure_checklist(p: argparse.ArgumentParser) -> None:
    p.add_argument("gate", choices=list(SUPPORTED))
    p.add_argument("--format", choices=["md", "json"], default="md")


def register_commands() -> None:
    register("gate prepare", _prepare, _configure_prepare)
    register("gate sync", _sync, _configure_sync)
    register("gate checklist", _checklist, _configure_checklist)
