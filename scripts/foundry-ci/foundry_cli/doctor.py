"""`foundry doctor`: read-only prerequisite and drift checks for a repo."""
import argparse
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import __version__
from .cli import register
from .config import Config, ConfigError, load_config
from .gh import GhError, GhUnavailable, GitHub, get_github
from .github_settings import plan_settings
from .pack_tools import check_dependencies, install_command, is_warning, uv_roots
from .template import plan_template

HOOK_SCRIPT = "scripts/foundry-hooks/commit-msg"
STAGED_SCRIPT = "scripts/foundry-quality/staged.sh"
HOOK_INSTALL = ("lefthook install, or pre-commit install --hook-type commit-msg "
                "--hook-type pre-commit")
MIN_PYTHON = (3, 10)
SAMPLE_PATHS = 3
NODE_STACK_PREFIXES = ("ts", "typescript", "react", "js", "node")


@dataclass
class Finding:
    level: str  # "ok" | "warn" | "fail"
    item: str
    fix: str = ""


@dataclass
class DoctorEnv:
    """Everything doctor reads from the outside world, so tests can fake it."""
    which: Callable[[str], Optional[str]]
    run: Callable[..., Tuple[int, str, str]]  # (args, cwd=None) -> (rc, stdout, stderr)
    github: Callable[[], GitHub]


def _run(args: List[str], cwd: Optional[Path] = None) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, cwd=cwd)
    except OSError as e:
        return 127, "", str(e)
    return p.returncode, p.stdout, p.stderr


def default_env() -> DoctorEnv:
    return DoctorEnv(which=shutil.which, run=_run, github=get_github)


def _ok(item: str) -> Finding:
    return Finding("ok", item)


def check_tools(config: Optional[Config], env: DoctorEnv) -> Tuple[List[Finding], bool]:
    """Returns the findings and whether gh is usable."""
    out: List[Finding] = []
    out.append(_ok("git") if env.which("git") else
               Finding("fail", "git", "install git: https://git-scm.com/downloads"))
    gh_ok = False
    if not env.which("gh"):
        out.append(Finding("fail", "gh", "brew install gh, then gh auth login"))
    else:
        rc, _, _ = env.run(["gh", "auth", "status"])
        if rc == 0:
            gh_ok = True
            out.append(_ok("gh"))
        else:
            out.append(Finding("fail", "gh", "gh is not logged in: gh auth login"))
    fix = f"install Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer"
    if not env.which("python3"):
        out.append(Finding("fail", "python3", fix))
    else:
        rc, so, se = env.run(["python3", "--version"])
        m = re.search(r"(\d+)\.(\d+)", so + se)
        if rc != 0 or not m:
            out.append(Finding("fail", "python3", "could not read the python3 version; " + fix))
        elif (int(m.group(1)), int(m.group(2))) < MIN_PYTHON:
            out.append(Finding("fail", "python3", f"found {m.group(0)}; {fix}"))
        else:
            out.append(_ok("python3"))
    out.append(_ok("uv") if env.which("uv") else
               Finding("warn", "uv", "install uv: https://docs.astral.sh/uv/"))
    if config is not None and any(s.startswith(NODE_STACK_PREFIXES) for s in config.stacks):
        out.append(_ok("node") if env.which("node") else
                   Finding("fail", "node", "install Node.js: https://nodejs.org"))
    return out, gh_ok


def _summary(label: str, paths: List[str], fix: str) -> Finding:
    shown = ", ".join(paths[:SAMPLE_PATHS]) + (", ..." if len(paths) > SAMPLE_PATHS else "")
    return Finding("warn", f"template: {label} files", f"{len(paths)} {label} (e.g. {shown}); {fix}")


def check_template(config: Config, repo: Path) -> List[Finding]:
    changes = plan_template(config, repo)
    missing = [c.path.as_posix() for c in changes if c.action == "create"]
    differing = [c.path.as_posix() for c in changes if c.action == "conflict"]
    out: List[Finding] = []
    if missing:
        out.append(_summary("missing", missing, "run: foundry template apply --target . --apply"))
    if differing:
        out.append(_summary("differing", differing,
                            "review the differences; apply never overwrites"))
    return out or [_ok("template")]


def check_pack_dependencies(config: Config, repo: Path) -> List[Finding]:
    lines = check_dependencies(config, repo)
    if not lines:
        return [_ok("pack dependencies")]
    if all(map(is_warning, lines)):
        return [Finding("warn", "pack dependencies", "; ".join(lines))]
    return [Finding("fail", "pack dependencies",
                    "; ".join(lines) + "; run: "
                    + install_command(lines, uv_roots(config, repo)))]


def check_github_settings(config: Config, env: DoctorEnv, gh_ok: bool) -> List[Finding]:
    item = "github settings"
    if not gh_ok:
        return [Finding("warn", item, "skipped: gh is missing or not logged in")]
    try:
        changes = plan_settings(env.github(), config)
    except GhUnavailable as e:
        return [Finding("warn", item, f"skipped: {e}")]
    except GhError as e:
        return [Finding("warn", item, f"could not read settings: {e}")]
    out: List[Finding] = []
    for c in changes:
        label = f"{item}: {c.target}"
        if c.action == "blocked":
            out.append(Finding("warn", label, f"blocked: {c.reason}"))
        elif c.action in ("create", "update"):
            out.append(Finding("warn", label, f"{c.action} needed: foundry github plan, then apply"))
    return out or [_ok(item)]


def check_version(config: Config) -> Finding:
    if config.foundry_version == __version__:
        return _ok("foundry version")
    return Finding("warn", "foundry version",
                   f"foundry.json says {config.foundry_version}, installed is {__version__}")


def _hook_file(repo: Path, env: DoctorEnv, name: str = "commit-msg") -> Path:
    """Where git looks for hook `name` (honours core.hooksPath, worktrees, `~`)."""
    rc, out, _ = env.run(["git", "rev-parse", "--git-path", "hooks"], cwd=repo)
    hooks = Path(out.strip()).expanduser() if rc == 0 and out.strip() else Path(".git/hooks")
    return repo / hooks / name  # an absolute path wins in pathlib


def _hook_text(path: Path) -> str:
    """The hook's text, or "" when git would not run it (missing or not executable)."""
    return _read(path) if os.access(path, os.X_OK) else ""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def check_commit_hook(repo: Path, env: DoctorEnv) -> Finding:
    item = "commit-msg hook"
    text = _hook_text(_hook_file(repo, env))
    if HOOK_SCRIPT in text:
        return _ok(item)
    for tool, cfg in (("lefthook", "lefthook.yml"), ("pre-commit", ".pre-commit-config.yaml")):
        if tool in text and HOOK_SCRIPT in _read(repo / cfg):
            return _ok(item)
    return Finding("warn", item, f"install it (and make it executable): {HOOK_INSTALL}, "
                   "or copy scripts/foundry-hooks/commit-msg to .git/hooks/")


def check_staged_hook(repo: Path, env: DoctorEnv) -> List[Finding]:
    """When a pack ships the staged-file check, git must run it on commit."""
    if not (repo / STAGED_SCRIPT).is_file():
        return []
    item = "pre-commit hook"
    text = _hook_text(_hook_file(repo, env, "pre-commit"))
    if STAGED_SCRIPT in text:
        return [_ok(item)]
    for marker, cfg in (("lefthook", "lefthook.yml"),
                        ("generated by pre-commit", ".pre-commit-config.yaml"),
                        ("pre-commit.com", ".pre-commit-config.yaml")):
        if marker in text and STAGED_SCRIPT in _read(repo / cfg):
            return [_ok(item)]
    return [Finding("warn", item, f"install it (and make it executable): {HOOK_INSTALL}; it runs "
                    f"{STAGED_SCRIPT} on the staged files")]


def check_attribution(repo: Path) -> Finding:
    item = "attribution setting"
    fix = ('set "attribution": {"commit": "", "pr": "", "sessionUrl": false} '
           'in .claude/settings.json')
    try:
        data = json.loads(_read(repo / ".claude" / "settings.json"))
        attr = data.get("attribution") if isinstance(data, dict) else None
    except json.JSONDecodeError:
        attr = None
    if isinstance(attr, dict) and attr.get("commit") == "" and attr.get("pr") == "":
        return _ok(item)
    return Finding("warn", item, fix)


def run_doctor(repo: Path, env: DoctorEnv) -> List[Finding]:
    """Read-only: never writes, never calls a mutating GitHub API."""
    repo = Path(repo)
    config: Optional[Config] = None
    cfg_finding = _ok("foundry.json")
    try:
        config = load_config(repo)
    except ConfigError as e:
        cfg_finding = Finding("fail", "foundry.json", "; ".join(e.problems))
    gh_ok = False
    findings: List[Finding] = []
    try:
        findings, gh_ok = check_tools(config, env)
    except Exception as e:  # noqa: BLE001 - one broken check must not hide the rest
        findings = [_crash("tools", e)]
    findings.append(cfg_finding)
    if config is not None:
        findings += _guard("template", lambda: check_template(config, repo))
        findings += _guard("pack dependencies", lambda: check_pack_dependencies(config, repo))
        findings += _guard("github settings",
                           lambda: check_github_settings(config, env, gh_ok))
        findings += _guard("foundry version", lambda: [check_version(config)])
    findings += _guard("commit-msg hook", lambda: [check_commit_hook(repo, env)])
    findings += _guard("pre-commit hook", lambda: check_staged_hook(repo, env))
    findings += _guard("attribution setting", lambda: [check_attribution(repo)])
    return findings


def _crash(item: str, e: Exception) -> Finding:
    return Finding("fail", item, f"check crashed: {type(e).__name__}: {e}")


def _guard(item: str, check: Callable[[], List[Finding]]) -> List[Finding]:
    try:
        return check()
    except Exception as e:  # noqa: BLE001 - one broken check must not hide the rest
        return [_crash(item, e)]


def format_finding(f: Finding) -> str:
    line = f"{f.level:<4}  {f.item}"
    return f"{line}  — fix: {f.fix}" if f.fix else line


def _configure(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", default=".", help="repo root containing foundry.json")


def _run_doctor(ns: argparse.Namespace) -> int:
    findings = run_doctor(Path(ns.repo), default_env())
    for f in findings:
        print(format_finding(f))
    return 1 if any(f.level == "fail" for f in findings) else 0


def register_commands() -> None:
    register("doctor", _run_doctor, _configure)
