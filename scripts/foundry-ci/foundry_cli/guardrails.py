"""`foundry guardrails check`: read-only report of missing or drifted template pieces."""
import argparse
import sys
from pathlib import Path
from typing import List

from .cli import register
from .config import ConfigError, load_config
from .doctor import (DoctorEnv, check_attribution, check_commit_hook, check_staged_hook,
                     default_env)
from .template import plan_template

_LABEL = {"create": "missing", "conflict": "drift", "skip": "ok"}


def check_guardrails(repo: Path, env: DoctorEnv) -> List[str]:
    """One line per item. Never writes. Raises ConfigError on a bad foundry.json."""
    repo = Path(repo)
    config = load_config(repo)
    lines = [f"{_LABEL[c.action]} {c.path.as_posix()}" for c in plan_template(config, repo)]
    lines.append("ok commit-msg hook" if check_commit_hook(repo, env).level == "ok"
                 else "missing commit-msg hook")
    lines += [f"{'ok' if f.level == 'ok' else 'missing'} pre-commit hook"
              for f in check_staged_hook(repo, env)]
    lines.append("ok attribution setting" if check_attribution(repo).level == "ok"
                 else "missing attribution setting")
    return lines


def _configure(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", default=".", help="repo root containing foundry.json")


def _run(ns: argparse.Namespace) -> int:
    try:
        lines = check_guardrails(Path(ns.repo), default_env())
    except ConfigError as e:
        for problem in e.problems:
            print(problem, file=sys.stderr)
        return 2
    except ValueError as e:  # two template sources provide the same file
        print(f"foundry: {e}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 0 if all(line.startswith("ok ") for line in lines) else 1


def register_commands() -> None:
    register("guardrails check", _run, _configure)
