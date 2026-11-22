"""Is a Dependabot PR a dependency-only change? (R35)

The route covers one thing: the same dependency, action or image moved to another version from
the normal registry. `dependency_problem` compares each changed file at the PR's base and head
and returns why the PR is not that, or None. It reads files through `GitHub.file_at` only.
"""
import re
from typing import Any, Callable, List, Optional

from .dep_ci_files import _dockerfile, _workflow
from .dep_manifests import (_lockfile, _package_json, _package_lock, _pyproject,
                            _requirements)
from .gh import GitHub, PrInfo

DEPENDABOT_LOGIN = "dependabot[bot]"
DEPENDABOT_EMAIL = "49699333+dependabot[bot]@users.noreply.github.com"
LOCKFILES = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "uv.lock", "poetry.lock")
_REQUIREMENTS_RE = re.compile(r"^requirements[^/]*\.txt$")
_WORKFLOW_RE = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")


def is_dependabot(info: PrInfo) -> bool:
    return info.author.casefold() == DEPENDABOT_LOGIN and info.author_type == "Bot"


def _checker(path: str) -> Optional[Callable[[str, str], bool]]:
    """The test that decides whether a change to `path` is dependency-only, or None."""
    name = path.rsplit("/", 1)[-1]
    if name == "package-lock.json":
        return _package_lock
    if name in LOCKFILES:
        return _lockfile
    if _REQUIREMENTS_RE.match(name):
        return _requirements
    if name == "package.json":
        return _package_json
    if name == "pyproject.toml":
        return _pyproject
    if name.startswith("Dockerfile"):
        return _dockerfile
    if _WORKFLOW_RE.match(path):
        return _workflow
    return None


def commit_problem(commits: List[Any]) -> Optional[str]:
    """The first PR commit not authored by Dependabot's own noreply address, named."""
    for c in commits:
        if c.author_email.casefold() != DEPENDABOT_EMAIL:
            return (f"commit {c.sha[:7]} is authored by {c.author_email or 'an unknown address'}, "
                    "not Dependabot")
    return None


def dependency_problem(gh: GitHub, repo: str, info: PrInfo, files: List[str],
                       head: Optional[str] = None) -> Optional[str]:
    """Why the PR is not a dependency-only change, naming the first file, or None."""
    head = head or info.head_sha
    if not info.base_sha:
        return "the PR's base commit is unknown"
    for path in files:
        check = _checker(path)
        if check is None:
            return f"{path} is not a dependency file"
        old = gh.file_at(repo, info.base_sha, path)
        new = gh.file_at(repo, head, path)
        if old is None or new is None:
            return f"{path} is missing at the base or the head, so it is not dependency-only"
        if not check(old, new):
            return f"{path} changes more than one dependency moving to another version, " \
                   "so it is not dependency-only"
    return None
