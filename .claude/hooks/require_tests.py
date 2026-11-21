#!/usr/bin/env python3
"""Stop hook: refuse to finish once while source changed without tests.

Compares against the merge-base with the base branch (plus uncommitted and
untracked files). If files matching `paths.source` changed and none matching
`paths.tests` did, it blocks once per session and records that in
`.git/foundry-require-tests-<session_id>`. Skipped when `stop_hook_active`
is true or foundry.json defines no source globs.
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _foundry_config  # noqa: E402


def git(cwd, *args):
    try:
        out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=15, cwd=cwd)
    except Exception:
        return None
    return out.stdout if out.returncode == 0 else None


def lines(text):
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def changed_files(cwd, base):
    files = set()
    merge_base = None
    for ref in ("origin/" + base, base):
        mb = git(cwd, "merge-base", "HEAD", ref)
        if mb and mb.strip():
            merge_base = mb.strip()
            break
    files.update(lines(git(cwd, "diff", "--name-only", merge_base or "HEAD")))
    files.update(lines(git(cwd, "ls-files", "--others", "--exclude-standard")))
    return files


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    if payload.get("stop_hook_active"):
        sys.exit(0)

    project = _foundry_config.project_dir(payload)
    cfg = _foundry_config.load(project)
    if not cfg["source_globs"]:
        sys.exit(0)

    git_dir = git(project, "rev-parse", "--absolute-git-dir")
    if not git_dir:
        sys.exit(0)
    session = re.sub(r"[^A-Za-z0-9_.-]", "_", str(payload.get("session_id") or "default"))
    marker = os.path.join(git_dir.strip(), "foundry-require-tests-" + session)
    if os.path.exists(marker):
        sys.exit(0)

    files = changed_files(project, cfg["base_branch"])
    sources = sorted(f for f in files if _foundry_config.matches(f, cfg["source_globs"]))
    tests = [f for f in files if _foundry_config.matches(f, cfg["test_globs"])]
    if not sources or tests:
        sys.exit(0)

    try:
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("blocked\n")
    except OSError:
        pass
    listed = "\n".join("  - %s" % f for f in sources[:15])
    more = "\n  ... and %d more" % (len(sources) - 15) if len(sources) > 15 else ""
    print("Source changed on this branch with no test changes:\n%s%s\n"
          "Add or update tests that fail if this behaviour breaks, and run them. "
          "If the change is genuinely untestable, say why in the PR description."
          % (listed, more), file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
