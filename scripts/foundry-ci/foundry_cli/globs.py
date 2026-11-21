"""Glob matching with the same rules as template/.claude/hooks/_foundry_config.py.

`**` crosses directories, `*` does not, `?` is one non-slash character.
There is no brace expansion. The hook stays self-contained because it runs in
repos without the CLI; tests/test_risky.py checks the two agree.
"""
import re
from typing import List


def _glob_to_regex(pattern: str):
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def matching_pattern(path: str, patterns: List[str]):
    """Return the first pattern that matches `path`, or None."""
    path = path.replace("\\", "/")
    if path.startswith("./"):
        path = path[2:]
    for p in patterns:
        if _glob_to_regex(p).match(path):
            return p
    return None


def matches(path: str, patterns: List[str]) -> bool:
    return matching_pattern(path, patterns) is not None
