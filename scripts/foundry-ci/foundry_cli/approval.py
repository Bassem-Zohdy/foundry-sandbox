"""CI check: the tech lead approved a story plan or a gate for the PR head content."""
import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .cli import register
from .codeowners import TECH_LEAD_DOCS
from .config import Config, ConfigError, load_config
from .dep_update import commit_problem, dependency_problem, is_dependabot
from .gh import GitHub, PrInfo, get_github
from .story_rules import story_problem, watched_story

_STORY_RE = re.compile(r"^docs/stories/[^/]+\.md$")
GATE_LABELS = ("gate:G1", "gate:G2", "gate:G2-short", "gate:TAKEOVER", "gate:G4")
_KIND_RE = re.compile(r"^(plan|gate:[A-Za-z0-9_-]+)$")
# check_approval also takes "deps": the /approve-deps route for a dependency-only Dependabot PR.
# It is not a CLI kind; check_pr picks it for the "plan" kind.
_APPROVAL_KIND_RE = re.compile(r"^(plan|deps|gate:[A-Za-z0-9_-]+)$")


@dataclass
class ApprovalResult:
    ok: bool
    reason: str


MAX_COMMITS = 250  # GitHub's pulls/{n}/commits cap
MAX_FILES = 3000  # GitHub's pulls/{n}/files cap
_SHA_RE = r"([0-9a-fA-F]{7,40})"


def _command_sha(body: str, prefix: str) -> Optional[str]:
    """The sha in a first line `<prefix> <sha>` (trailing whitespace only), else None."""
    first = body.split("\n", 1)[0].rstrip()
    m = re.fullmatch(re.escape(prefix) + " " + _SHA_RE, first)
    return m.group(1).lower() if m else None


def _same_user(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


def check_approval(gh: GitHub, config: Config, pr: int, kind: str,
                   watched: Callable[[str], Optional[str]],
                   paths: List[str], head: Optional[str] = None) -> ApprovalResult:
    """Did config.tech_lead approve `kind` for the PR's current head content?

    Approval is content-addressed: commit dates are never read. A plan approval
    names a commit sha whose watched content equals the head's; a gate approval
    is an approving review on the head or a comment naming the head.
    """
    if not _APPROVAL_KIND_RE.match(kind):
        raise ValueError(f"unknown kind {kind!r}; use plan or gate:<id>, e.g. gate:G1")
    # A gate and a dependency approval share one rule: a comment naming the head, or an
    # approving review on the head. Only the plan approval is content-addressed.
    is_gate = kind != "plan"
    prefix = {"plan": "/approve-plan", "deps": "/approve-deps"}.get(kind) \
        or "/approve-gate " + kind.split(":", 1)[1]
    repo, lead = config.repo, config.tech_lead

    commits = gh.pr_commits(repo, pr)
    if len(commits) >= MAX_COMMITS:
        return ApprovalResult(False, "PR too large for the approval check "
                                     f"({len(commits)} commits, limit {MAX_COMMITS - 1})")
    head = (head or gh.pr_head(repo, pr)).lower()
    shas = [c.sha.lower() for c in commits]
    if head not in shas:
        return ApprovalResult(False, f"PR head {head[:7]} is not among the PR's commits")
    command = f"{prefix} {head[:7]}"
    fix = f"; to approve the current state post a new comment starting with: {command}"

    cache: Dict[Tuple[str, str], Optional[str]] = {}

    def view(sha: str, path: str) -> Optional[str]:
        if (sha, path) not in cache:
            text = gh.file_at(repo, sha, path)
            cache[(sha, path)] = None if text is None else watched(text)
        return cache[(sha, path)]

    def unsafe(sha: str, path: str) -> Optional[str]:
        """A story-file construct the approval check cannot read reliably (story_rules)."""
        problem = story_problem(gh.file_at(repo, sha, path) or "")
        return f"{path} at {sha[:7]} has {problem}" if problem else None

    if not is_gate:
        for p in paths:
            if gh.file_at(repo, head, p) is None:
                return ApprovalResult(False, f"{p} could not be read at head {head[:7]} (404)")
            problem = unsafe(head, p)
            if problem:
                return ApprovalResult(False, problem)
            if view(head, p) is None:
                return ApprovalResult(False, f"{p} has no '## Plan' section above '## Ledger' at head")

    def sha_ok(sha: str) -> Tuple[bool, str]:
        full = [c for c in shas if c.startswith(sha)]
        if not full:
            return False, f"commit {sha} is not in the PR"
        if len(set(full)) > 1:
            return False, f"ambiguous sha {sha}; use a longer sha"
        if is_gate:
            return (head.startswith(sha), f"{sha} is not the head commit")
        for p in paths:
            if gh.file_at(repo, full[0], p) is None:
                return False, f"{p} could not be read at {sha} (404)"
            problem = unsafe(full[0], p)
            if problem:
                return False, problem
            if view(full[0], p) != view(head, p):
                return False, f"{p} changed above '## Ledger' since {sha}"
        return True, ""

    why = f"no {prefix} <sha> comment from {lead}"
    for cm in reversed(gh.pr_comments(repo, pr)):
        sha = _command_sha(cm.body, prefix)
        if sha is None or not _same_user(cm.author, lead):
            continue
        if cm.updated_at != cm.created_at:
            why = "the approval comment was edited after posting"
            continue
        ok, detail = sha_ok(sha)
        if ok:
            return ApprovalResult(True, f"{prefix} {sha} by {lead}")
        why = detail
    if is_gate:
        mine = [r for r in gh.pr_reviews(repo, pr)
                if _same_user(r.author, lead) and r.state != "COMMENTED"]
        if mine and mine[-1].state == "APPROVED" and mine[-1].commit.lower() == head:
            return ApprovalResult(True, f"approving review by {lead} on head {head[:7]}")
        if mine and mine[-1].state == "APPROVED":
            why = "the approving review is not on the head commit"
        elif mine:
            why = f"the latest review by {lead} is {mine[-1].state}"
    return ApprovalResult(False, f"not approved ({why}){fix}")


def _tech_lead_doc(path: str) -> bool:
    """A path CODEOWNERS gives the tech lead as a document (never docs/stories/)."""
    return any(path.startswith(d) if d.endswith("/") else path == d for d in TECH_LEAD_DOCS)


def check_pr(gh: GitHub, config: Config, pr: int, kind: str) -> ApprovalResult:
    """Resolve the watched files for `kind` from the PR, then run check_approval."""
    if not _KIND_RE.match(kind):
        raise ValueError(f"unknown kind {kind!r}; use plan or gate:<id>, e.g. gate:G1")
    head0 = gh.pr_head(config.repo, pr)  # read before the file list; a dependency PR re-checks it
    files = gh.pr_files(config.repo, pr)
    if len(files) >= MAX_FILES:
        return ApprovalResult(False, "PR too large for the approval check "
                                     f"({len(files)} files, limit {MAX_FILES - 1})")
    if kind == "plan":
        stories = [f for f in files if _STORY_RE.match(f)]
        if not stories:
            if files:
                info = gh.pr_info(config.repo, pr)
                if is_dependabot(info):
                    return _dependency_pr(gh, config, pr, info, files, head0)
            if files and all(_tech_lead_doc(f) for f in files):
                gate = next((l for l in gh.pr_info(config.repo, pr).labels
                             if l in GATE_LABELS), None)
                if gate:
                    return ApprovalResult(True, f"not applicable: gate PR ({gate}); the tech "
                                                "lead approves it through foundry / gate-approved")
            return ApprovalResult(False, "no story file in PR")
        return check_approval(gh, config, pr, kind, watched_story, stories)
    return check_approval(gh, config, pr, kind, lambda t: t, files)


def _dependency_pr(gh: GitHub, config: Config, pr: int, info: PrInfo,
                   files: List[str], head: str) -> ApprovalResult:
    """The /approve-deps route: a Dependabot PR whose every change is dependency-only."""
    if gh.pr_head(config.repo, pr).lower() != head.lower():
        return ApprovalResult(False, "head moved while the PR was being checked; a newer run will report")
    problem = (commit_problem(gh.pr_commits(config.repo, pr))
               or dependency_problem(gh, config.repo, info, files, head))
    if problem:
        return ApprovalResult(False, f"no story file in PR; it is a Dependabot PR but {problem}")
    result = check_approval(gh, config, pr, "deps", lambda t: t, files, head)
    if result.ok:
        return ApprovalResult(True, f"{result.reason} (dependency-only Dependabot PR)")
    return ApprovalResult(False, result.reason.replace(
        "not approved (", "not approved: dependency-only Dependabot PR (", 1))


def context_problem(gh: GitHub, config: Config, pr: int, expect_head: Optional[str],
                    repo_slug: Optional[str]) -> Optional[str]:
    """Why this run should not report on the PR, or None.

    CI passes the repo and head sha its event was about; a different live
    answer means foundry.json names another repo or the head moved on.
    """
    if repo_slug is not None and repo_slug.casefold() != config.repo.casefold():
        return f"foundry.json repo {config.repo!r} is not the running repo {repo_slug!r}"
    if expect_head is not None and gh.pr_head(config.repo, pr).lower() != expect_head.lower():
        return "head moved; a newer run will report"
    return None


def add_context_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--expect-head", help="fail if the PR head is not this sha")
    p.add_argument("--repo-slug", help="fail if foundry.json github.repo differs (owner/repo)")


def _configure(p: argparse.ArgumentParser) -> None:
    p.add_argument("--pr", type=int, required=True, help="pull request number")
    p.add_argument("--kind", required=True,
                   help="plan, gate:G1, gate:G2, ... or gates (every gate label on the PR)")
    add_context_options(p)
    p.add_argument("--repo", default=".", help="repo root containing foundry.json")


def _run(ns: argparse.Namespace) -> int:
    try:
        config = load_config(Path(ns.repo))
    except ConfigError as e:
        for problem in e.problems:
            print(problem, file=sys.stderr)
        return 2
    if ns.kind != "gates" and not _KIND_RE.match(ns.kind):
        print(f"foundry: unknown kind {ns.kind!r}; use plan, gate:<id> or gates",
              file=sys.stderr)
        return 2
    gh = get_github()
    problem = context_problem(gh, config, ns.pr, ns.expect_head, ns.repo_slug)
    if problem:
        print(f"not approved: {problem}", file=sys.stderr)
        return 1
    kinds = [ns.kind]
    if ns.kind == "gates":
        labels = gh.pr_info(config.repo, ns.pr).labels
        unknown = [l for l in labels if l.lower().startswith("gate:") and l not in GATE_LABELS]
        if unknown:
            for l in unknown:
                print(f"not approved: unknown gate label {l!r}; use one of "
                      f"{', '.join(GATE_LABELS)}", file=sys.stderr)
            return 1
        kinds = [l for l in labels if l in GATE_LABELS]
        if not kinds:
            print("not applicable: no gate label on the PR")
            return 0
    failed = False
    for kind in kinds:
        result = check_pr(gh, config, ns.pr, kind)
        if result.ok:
            print(f"approved ({kind}): {result.reason}")
        else:
            print(f"not approved ({kind}): {result.reason}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


def register_commands() -> None:
    register("check approval", _run, _configure)
