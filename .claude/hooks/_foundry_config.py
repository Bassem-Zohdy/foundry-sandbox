"""Minimal foundry.json reader for the repo-guard hooks (standard library only).

Deliberately does not import foundry_cli: a product repo runs these hooks
without the Foundry plugin installed.
"""
import json
import os
import re

DEFAULT_BASE_BRANCH = "main"


def project_dir(payload=None):
    return (os.environ.get("CLAUDE_PROJECT_DIR")
            or (payload or {}).get("cwd")
            or os.getcwd())


def load(directory):
    """Return {base_branch, source_globs, test_globs}; defaults when foundry.json is absent or unreadable."""
    data = {}
    try:
        with open(os.path.join(directory, "foundry.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        pass
    if not isinstance(data, dict):
        data = {}
    github = data.get("github") if isinstance(data.get("github"), dict) else {}
    paths = data.get("paths") if isinstance(data.get("paths"), dict) else {}

    def strings(v):
        return [x for x in v if isinstance(x, str) and x] if isinstance(v, list) else []

    base = github.get("baseBranch")
    return {
        "base_branch": base if isinstance(base, str) and base else DEFAULT_BASE_BRANCH,
        "source_globs": strings(paths.get("source")),
        "test_globs": strings(paths.get("tests")),
    }


def _glob_to_regex(pattern):
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


def matches(path, patterns):
    """True when a repo-relative path matches any glob (`**` crosses directories, `*` does not)."""
    path = path.replace("\\", "/")
    if path.startswith("./"):
        path = path[2:]
    return any(_glob_to_regex(p).match(path) for p in patterns)
