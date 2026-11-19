"""Changed files of a branch: path, rename origin, status and added lines."""
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


class BadRef(Exception):
    """A base or head ref git cannot resolve (or a diff git refuses)."""


@dataclass
class ChangedFile:
    path: str
    old_path: Optional[str]
    status: str
    added_lines: List[str] = field(default_factory=list)


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "LC_ALL": "C"})
    if r.returncode != 0:
        raise BadRef((r.stderr.strip().splitlines() or ["git failed"])[-1])
    return r.stdout


def _parse_name_status(out: str) -> List[ChangedFile]:
    tokens = out.split("\0")
    files, i = [], 0
    while i < len(tokens) and tokens[i]:
        status = tokens[i]
        if status[0] in "RC":
            files.append(ChangedFile(tokens[i + 2], tokens[i + 1], status))
            i += 3
        else:
            files.append(ChangedFile(tokens[i + 1], None, status))
            i += 2
    return files


def _parse_added_lines(diff: str) -> Dict[str, List[str]]:
    added: Dict[str, List[str]] = {}
    current: Optional[str] = None
    in_hunk = False
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            current, in_hunk = None, False
        elif in_hunk:
            if line.startswith("+") and current is not None:
                added[current].append(line[1:])
        elif line.startswith("+++ "):
            name = line[4:].rstrip("\t")
            if name.startswith('"b/') and name.endswith('"'):
                name = name[1:-1]
            if name.startswith("b/"):
                current = name[2:]
                added.setdefault(current, [])
        elif line.startswith("@@"):
            in_hunk = True
    return added


def changed_files(repo: Path, base: str, head: str = "HEAD") -> List[ChangedFile]:
    repo = Path(repo)
    span = f"{base}...{head}"
    files = _parse_name_status(_git(repo, "diff", "--name-status", "-z", "-M", span))
    added = _parse_added_lines(_git(repo, "diff", "-U0", "-M", "--no-color", "--no-ext-diff", "--no-textconv",
                                   "--src-prefix=a/", "--dst-prefix=b/", span))
    for f in files:
        f.added_lines = added.get(f.path, [])
    return files
