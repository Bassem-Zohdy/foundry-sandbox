"""`foundry upstream check`: compare pinned upstream releases with the remote tags."""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .cli import register

SOURCES_PATH = Path(__file__).resolve().parents[2] / "upstream" / "sources.json"
LS_REMOTE_TIMEOUT = 60
# Tag names come from remote repos: accept only plain dotted versions, nothing else.
TAG_RE = re.compile(r"v\d+(?:\.\d+){0,3}")
NAME_RE = re.compile(r"[a-z][a-z0-9-]*")

Runner = Callable[[str], Tuple[int, str, str]]  # repo slug -> (rc, stdout, stderr)
_runner_override: Optional[Runner] = None


class UpstreamError(Exception):
    pass


def _version(tag: str) -> Tuple[int, ...]:
    return tuple(int(p) for p in tag[1:].split("."))


def newer_releases(tags: List[str], line: str, pinned: str) -> List[str]:
    """Stable tags on the release line that are newer than `pinned`, oldest first."""
    floor = _version(pinned)
    line_re = re.compile(line)
    found = {t for t in tags
             if TAG_RE.fullmatch(t) and line_re.search(t) and _version(t) > floor}
    return sorted(found, key=_version)


def issue_title(name: str, tag: str) -> str:
    """The exact title of the issue that announces a new release."""
    if not NAME_RE.fullmatch(name):
        raise ValueError(f"unsafe source name: {name!r}")
    if not TAG_RE.fullmatch(tag):
        raise ValueError(f"unsafe release tag: {tag!r}")
    return f"upstream: {name} {tag}"


def parse_ls_remote(stdout: str) -> List[str]:
    tags = set()
    for line in stdout.splitlines():
        _, _, ref = line.partition("\t")
        if ref.startswith("refs/tags/"):
            tags.add(ref[len("refs/tags/"):].removesuffix("^{}"))
    return sorted((t for t in tags if TAG_RE.fullmatch(t)), key=_version)


def _git_ls_remote(repo: str) -> Tuple[int, str, str]:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        p = subprocess.run(["git", "ls-remote", "--tags", f"https://github.com/{repo}"],
                           capture_output=True, text=True, env=env, timeout=LS_REMOTE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, "", f"{type(e).__name__}: {e}"
    return p.returncode, p.stdout, p.stderr


def set_runner_for_tests(runner: Optional[Runner]) -> None:
    global _runner_override
    _runner_override = runner


def load_sources(path: Path) -> List[Dict[str, str]]:
    try:
        data = json.loads(path.read_text())
        sources = data["sources"]
        for s in sources:
            for key in ("name", "repo", "releaseLine", "pinned"):
                if not isinstance(s[key], str):
                    raise TypeError(key)
            re.compile(s["releaseLine"])
            issue_title(s["name"], s["pinned"])
    except (OSError, ValueError, KeyError, TypeError, re.error) as e:
        raise UpstreamError(f"cannot read upstream sources {path}: {type(e).__name__}: {e}")
    return sources


def check_source(source: Dict[str, str], runner: Runner) -> Dict[str, object]:
    out: Dict[str, object] = {"name": source["name"], "repo": source["repo"],
                              "pinned": source["pinned"], "tags": [], "newer": [],
                              "issues": [], "error": None}
    rc, stdout, stderr = runner(source["repo"])
    if rc != 0:
        detail = " ".join((stderr.strip() or stdout.strip() or f"exit {rc}").split())
        out["error"] = f"git ls-remote failed: {detail}"
        return out
    tags = parse_ls_remote(stdout)
    newer = newer_releases(tags, source["releaseLine"], source["pinned"])
    out["tags"] = tags
    out["newer"] = newer
    out["issues"] = [{"tag": t, "title": issue_title(source["name"], t)} for t in newer]
    return out


def check_all(sources: List[Dict[str, str]], runner: Runner) -> List[Dict[str, object]]:
    return [check_source(s, runner) for s in sources]


def _configure(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--sources", default=str(SOURCES_PATH), help="path to sources.json")


def _run_check(ns: argparse.Namespace) -> int:
    results = check_all(load_sources(Path(ns.sources)), _runner_override or _git_ls_remote)
    if ns.json:
        print(json.dumps({"sources": results}, indent=2))
        return 0
    for r in results:
        if r["error"]:
            print(f"{r['name']}: error: {r['error']}")
            continue
        tags = ", ".join(r["tags"][-3:]) or "none"
        news = ", ".join(r["newer"]) if r["newer"] else "no newer release"
        print(f"{r['name']}: pinned {r['pinned']}; latest tags: {tags}; {news}")
    return 0


def register_commands() -> None:
    register("upstream check", _run_check, _configure)
