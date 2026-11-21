"""`foundry status`: render per-module lifecycle state as STATUS.md."""
import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional

from .cli import register
from .config import Config, ConfigError, load_config
from .gates import plan_sync
from .gh import GhError, get_github
from .state import ModuleState, load_state

STATUS_FILE = Path("docs") / "foundry" / "STATUS.md"

_NEXT_GATE = {
    "discover": "G1", "define": "G1", "design": "G2",
    "takeover-1": "TAKEOVER", "takeover-2": "TAKEOVER",
    "build": "none", "verify": "none", "release": "G4",
}


def next_gate(stage: str) -> str:
    return _NEXT_GATE.get(stage, "none")


def _last_gate(state: ModuleState, unsynced: Optional[List[str]] = None) -> str:
    if unsynced:
        return f"{unsynced[-1]} (approved, not synced)"
    if not state.gates:
        return "none"
    return max(state.gates.items(), key=lambda kv: kv[1].at)[0]


def render_status(config: Config, states: List[ModuleState],
                  unsynced: Optional[Dict[str, List[str]]] = None) -> str:
    """`unsynced` maps a module to gates merged with approval but not yet recorded."""
    unsynced = unsynced or {}
    lines = [
        f"# {config.project_name} status",
        "",
        "| Module | Stage | Last gate | Next gate |",
        "| --- | --- | --- | --- |",
    ]
    for s in states:
        lines.append(f"| {s.module} | {s.stage} | {_last_gate(s, unsynced.get(s.module))} | {next_gate(s.stage)} |")
    return "\n".join(lines) + "\n"


def _configure(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", default=".", help="repo root containing foundry.json")
    p.add_argument("--write", action="store_true",
                   help=f"write {STATUS_FILE.as_posix()} instead of printing")
    p.add_argument("--remote", action="store_true",
                   help="ask GitHub for approved gate PRs not yet synced (needs gh)")


def _run(ns: argparse.Namespace) -> int:
    repo = Path(ns.repo)
    try:
        config = load_config(repo)
    except ConfigError as e:
        for problem in e.problems:
            print(problem, file=sys.stderr)
        return 2
    try:
        states = [load_state(repo, m) for m in config.modules]
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    unsynced: Dict[str, List[str]] = {}
    if ns.remote:
        try:
            plan = plan_sync(get_github(), config, repo)
        except GhError as e:
            print(str(e).splitlines()[0] if str(e) else "GitHub call failed",
                  file=sys.stderr)
            return 2
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        for p in plan.pending:
            unsynced.setdefault(p.module, []).append(p.gate)
        for problem in plan.errors:
            print(f"warning: {problem}", file=sys.stderr)
    text = render_status(config, states, unsynced)
    if ns.write:
        target = repo / STATUS_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"wrote {STATUS_FILE.as_posix()}")
    else:
        print(text, end="")
    return 0


def register_commands() -> None:
    register("status", _run, _configure)
