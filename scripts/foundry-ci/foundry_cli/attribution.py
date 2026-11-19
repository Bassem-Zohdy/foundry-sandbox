"""Human-only attribution check (standard library only).

The same rules are duplicated in template/scripts/foundry-hooks/commit-msg so
a target repo needs no plugin install; tests/test_attribution.py keeps the two
in sync.
"""
import argparse
import re
import sys
from pathlib import Path
from typing import List

from .approval import add_context_options, context_problem
from .cli import register
from .config import ConfigError, load_config
from .gh import GitHub, get_github

MAX_COMMITS = 250  # GitHub's pulls/{n}/commits cap: beyond it commits are unseen

_PATTERNS = [
    re.compile(r"co-authored-by:.*(claude|anthropic)", re.IGNORECASE),
    re.compile(r"generated with .*claude", re.IGNORECASE),
    re.compile("\U0001F916 generated", re.IGNORECASE),
]
# The only tokens that may contain the word: the file name `CLAUDE.md` (exact
# case) and the directory `.claude` as a path segment (preceded by start,
# whitespace, `/`, a quote, backtick or paren; followed by end, `/`,
# whitespace, a quote, backtick, paren, `:` or `,`). So `code.claude.com`,
# `x.claude.ai` and `thanks.Claude` still fail.
_ALLOWED = re.compile(r"(?<![A-Za-z0-9_.])CLAUDE\.md(?![A-Za-z0-9_])"
                      r"|(?<![^\s/'\"`()])\.claude(?![^/\s'\"`():,])")
_WORD = re.compile(r"claude|anthropic", re.IGNORECASE)
# `git commit -v` writes this line, git's hint comments, then the staged diff.
SCISSORS = "# ------------------------ >8 ------------------------"


def _line_offends(line: str) -> bool:
    if any(p.search(line) for p in _PATTERNS):
        return True
    return bool(_WORD.search(_ALLOWED.sub(" ", line)))


def find_attribution(text: str) -> List[str]:
    """Return the lines of `text` that carry AI attribution.

    Every line counts, including any below a scissors line: stored commit
    messages and PR bodies keep that text verbatim.
    """
    return [line for line in text.splitlines() if _line_offends(line)]


def strip_verbose_diff(text: str) -> str:
    """The commit-msg hook's view of a message file: the text above the
    scissors line when `git commit -v` put a diff below it, else all of it.

    Only the local hook applies this (git drops that text after the editor
    session). CI and the CLI never do; tests keep the hook copy in step.
    """
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.rstrip("\r\n") != SCISSORS:
            continue
        rest = [ln for ln in lines[i + 1:] if not ln.startswith("#")]
        if rest and rest[0].startswith("diff --git "):
            return "".join(lines[:i])
    return text


def check_author(name: str, email: str) -> List[str]:
    """Return one problem string per offending author field."""
    problems: List[str] = []
    if email.strip().lower().endswith("@anthropic.com"):
        problems.append(f"author email {email!r} is an AI vendor address")
    if "claude" in name.lower():
        problems.append(f"author name {name!r} names an AI tool")
    return problems


def check_pr(gh: GitHub, repo: str, pr: int) -> List[str]:
    """Problems in the PR title, body, every commit message and every commit author."""
    info = gh.pr_info(repo, pr)
    problems = [f"PR title: {line}" for line in find_attribution(info.title)]
    problems += [f"PR body: {line}" for line in find_attribution(info.body)]
    commits = gh.pr_commits(repo, pr)
    if len(commits) >= MAX_COMMITS:
        problems.append(f"PR too large to check every commit ({len(commits)} commits, "
                        f"limit {MAX_COMMITS - 1})")
    for c in commits:
        short = c.sha[:8]
        problems += [f"commit {short} message: {line}" for line in find_attribution(c.message)]
        problems += [f"commit {short}: {p}"
                     for p in check_author(c.author_name, c.author_email)]
    return problems


def _configure(p: argparse.ArgumentParser) -> None:
    p.add_argument("--file", help="read text from this file")
    p.add_argument("--stdin", action="store_true", help="read text from stdin")
    p.add_argument("--pr", type=int, help="check this pull request (title, body, commits)")
    p.add_argument("--repo", default=".", help="repo root containing foundry.json (with --pr)")
    add_context_options(p)
    p.add_argument("--author-name", help="also check this author name")
    p.add_argument("--author-email", help="also check this author email")


def _run(ns: argparse.Namespace) -> int:
    if sum([bool(ns.file), bool(ns.stdin), ns.pr is not None]) != 1:
        print("foundry: give exactly one of --file, --stdin or --pr", file=sys.stderr)
        return 2
    if ns.pr is not None:
        try:
            config = load_config(Path(ns.repo))
        except ConfigError as e:
            for problem in e.problems:
                print(problem, file=sys.stderr)
            return 2
        gh = get_github()
        problem = context_problem(gh, config, ns.pr, ns.expect_head, ns.repo_slug)
        if problem:
            print(problem, file=sys.stderr)
            return 1
        findings = check_pr(gh, config.repo, ns.pr)
        for item in findings:
            print(item, file=sys.stderr)
        return 1 if findings else 0
    if ns.file:
        try:
            with open(ns.file, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError) as e:
            print(f"foundry: cannot read {ns.file}: {e}", file=sys.stderr)
            return 2
    else:
        text = sys.stdin.read()
    findings = find_attribution(text)
    if ns.author_name is not None or ns.author_email is not None:
        findings += check_author(ns.author_name or "", ns.author_email or "")
    for item in findings:
        print(item, file=sys.stderr)
    return 1 if findings else 0


def register_commands() -> None:
    register("check attribution", _run, _configure)
