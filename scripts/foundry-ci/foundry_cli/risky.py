"""Decide in code whether a branch's change is risky, and say why."""
import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set, Tuple

from .cli import register
from .config import Config, ConfigError, load_config
from .diffinfo import BadRef, ChangedFile, changed_files
from .globs import matching_pattern

__all__ = ["BadRef", "ChangedFile", "RiskReason", "changed_files", "default_base",
           "risky_reasons", "register_commands"]

# (category, glob). Order here is only a tie-breaker; output is sorted.
_PATH_RULES: List[Tuple[str, str]] = [
    ("migration", "**/migrations/**"), ("migration", "**/alembic/**"),
    ("migration", "**/*.sql"), ("migration", "**/prisma/schema.prisma"),
    ("auth", "**/auth/**"), ("auth", "**/*auth*.*"),
    ("tenant", "**/tenant*/**"),
    ("money", "**/billing/**"), ("money", "**/payments/**"),
    ("ai-model", "**/prompts/**"),
    ("api-contract", "**/openapi*.yaml"), ("api-contract", "**/openapi*.yml"),
    ("api-contract", "**/openapi*.json"),
    ("infra", "Dockerfile*"), ("infra", "**/docker-compose*.y*ml"),
    ("infra", ".github/workflows/**"), ("infra", "infra/**"), ("infra", "**/*.tf"),
    ("secrets", ".env*"),
]

_CONTENT_RULES: List[Tuple[str, str]] = [
    ("migration", r"\b(create|alter|drop)\s+(table|index|column)\b"),
    ("auth", r"\b(password|passwd|jwt|bcrypt|argon2|session[_-]?secret)\b"),
    ("tenant", r"\btenant[_-]?id\b"),
    ("money", r"\b(amount|price|currency|invoice|vat)\b"),
    ("ai-model", r"\b(openai|anthropic|gemini|bedrock|model\s*[:=])"),
    ("personal-data", r"\b(national[_-]?id|iqama|passport|date[_-]?of[_-]?birth)\b"),
]
_COMPILED = [(cat, pat, re.compile(pat, re.IGNORECASE)) for cat, pat in _CONTENT_RULES]


@dataclass
class RiskReason:
    path: str
    reason: str


def _ref_exists(repo: Path, ref: str) -> bool:
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet",
                        ref + "^{commit}"], capture_output=True, text=True)
    return r.returncode == 0


def default_base(config: Config, repo: Path = Path(".")) -> str:
    """`origin/<baseBranch>` when that ref exists, otherwise `<baseBranch>`."""
    remote = f"origin/{config.base_branch}"
    return remote if _ref_exists(Path(repo), remote) else config.base_branch


def _path_reasons(config: Config, f: ChangedFile) -> Set[str]:
    rules = [("riskyPaths", g) for g in config.risky_paths] + _PATH_RULES
    found: Set[str] = set()
    for category, glob in rules:
        if matching_pattern(f.path, [glob]):
            found.add(f"{category}: {glob}")
        elif f.old_path and matching_pattern(f.old_path, [glob]):
            found.add(f"{category}: {glob} (old path {f.old_path})")
    return found


def _content_reasons(config: Config, f: ChangedFile) -> Set[str]:
    rules = list(_COMPILED) + [
        ("riskyContent", r["reason"], re.compile(r["pattern"], re.IGNORECASE))
        for r in config.risky_content]
    found: Set[str] = set()
    for category, label, rx in rules:
        if any(rx.search(line) for line in f.added_lines):
            found.add(f"{category}: {label}")
    return found


def risky_reasons(config: Config, files: List[ChangedFile]) -> List[RiskReason]:
    pairs: Set[Tuple[str, str]] = set()
    for f in files:
        for reason in _path_reasons(config, f) | _content_reasons(config, f):
            pairs.add((f.path, reason))
    return [RiskReason(p, r) for p, r in sorted(pairs)]


def _configure(p: argparse.ArgumentParser) -> None:
    p.add_argument("--base", help="base ref (default: origin/<baseBranch> or <baseBranch>)")
    p.add_argument("--repo", default=".", help="repo root containing foundry.json")


def _run(ns: argparse.Namespace) -> int:
    repo = Path(ns.repo)
    try:
        config = load_config(repo)
        base: Optional[str] = ns.base or default_base(config, repo)
        reasons = risky_reasons(config, changed_files(repo, base))
    except ConfigError as e:
        for problem in e.problems:
            print(problem, file=sys.stderr)
        return 2
    except BadRef as e:
        print(f"bad ref: {e}", file=sys.stderr)
        return 2
    if not reasons:
        print("not risky")
    for r in reasons:
        print(f"risky {r.path}: {r.reason}")
    return 0


def register_commands() -> None:
    register("risky", _run, _configure)
