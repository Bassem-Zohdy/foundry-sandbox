#!/usr/bin/env python3
"""PreToolUse (Bash): block git operations the working agreement forbids.

Blocks: commit on the base branch; push to the base branch; force pushes
(--force, -f, --force-with-lease, +refspec); push --mirror; --no-verify on
commit or push; `gh pr merge --admin`; agent-posted approvals (`gh pr review
--approve`, and `gh pr|issue comment`, `gh pr review` or `gh api` carrying
`/approve-plan` or `/approve-gate`). Best effort: a token holder can still
post approvals outside this hook; the server cannot tell the two apart.

Reads the hook JSON on stdin. Exit 2 blocks, with the reason on stderr.
The base branch comes from foundry.json (default main). FOUNDRY_TEST_BRANCH
overrides the current-branch lookup (tests).
"""
import json
import os
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _foundry_config  # noqa: E402

SEPARATORS = ("&&", "||", ";", "|", "&", "\n")
SHELLS = ("sh", "bash", "zsh", "dash")
# git global options that take a separate value
GIT_GLOBAL_VALUE = ("-C", "-c", "--git-dir", "--work-tree", "--namespace")
COMMIT_VALUE_FLAGS = ("-m", "-F", "-C", "-c", "-t", "--message", "--file", "--author", "--date", "--template")
COMMIT_VALUE_CHARS = "mFCctSu"
PUSH_VALUE_FLAGS = ("--repo", "--receive-pack", "--exec", "-o", "--push-option")
PUSH_VALUE_CHARS = "o"


class Refuse(Exception):
    pass


def split_segments(command):
    """Split on && || ; | & and newlines outside quotes."""
    segments, cur, quote, i = [], [], None, 0
    while i < len(command):
        c = command[i]
        if quote:
            cur.append(c)
            if c == "\\" and quote == '"' and i + 1 < len(command):
                cur.append(command[i + 1])
                i += 1
            elif c == quote:
                quote = None
        elif c in "'\"":
            quote = c
            cur.append(c)
        elif c == "\\" and i + 1 < len(command):
            cur.append(c)
            cur.append(command[i + 1])
            i += 1
        else:
            sep = next((s for s in SEPARATORS if command.startswith(s, i)), None)
            if sep:
                segments.append("".join(cur))
                cur = []
                i += len(sep)
                continue
            cur.append(c)
        i += 1
    segments.append("".join(cur))
    return [s for s in segments if s.strip()]


def tokenize(segment):
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def current_branch(cwd):
    forced = os.environ.get("FOUNDRY_TEST_BRANCH")
    if forced:
        return forced
    # rev-parse fails on an unborn branch (no commits yet); symbolic-ref still names it.
    for args in (["rev-parse", "--abbrev-ref", "HEAD"], ["symbolic-ref", "--short", "HEAD"]):
        try:
            out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=5, cwd=cwd)
        except Exception:
            return ""
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    return ""


def git_invocation(tokens):
    """Return (subcommand, args, -C path, hooks_overridden) for the first `git` in tokens, else None."""
    for idx, tok in enumerate(tokens):
        if os.path.basename(tok) not in ("git", "git.exe"):
            continue
        i, path, hooks = idx + 1, None, False
        while i < len(tokens) and tokens[i].startswith("-"):
            if tokens[i] in GIT_GLOBAL_VALUE:
                if tokens[i] == "-C" and i + 1 < len(tokens):
                    path = tokens[i + 1]
                if tokens[i] == "-c" and i + 1 < len(tokens) and \
                        tokens[i + 1].lower().startswith("core.hookspath"):
                    hooks = True
                i += 2
            else:
                i += 1
        if i < len(tokens):
            return tokens[i], tokens[i + 1:], path, hooks
    return None


def short_flags(token, value_chars):
    """Letters of a short-flag cluster, up to the first letter that takes a value."""
    letters = []
    for ch in token[1:]:
        letters.append(ch)
        if ch in value_chars:
            break
    return letters


def check_commit(args, branch, base):
    skip = False
    for tok in args:
        if skip:
            skip = False
            continue
        if tok == "--":
            break
        if tok == "--no-verify":
            raise Refuse("--no-verify skips the commit hooks. Fix what the hook reports instead.")
        if tok in COMMIT_VALUE_FLAGS:
            skip = True
        elif tok.startswith("-") and not tok.startswith("--") and len(tok) > 1:
            letters = short_flags(tok, COMMIT_VALUE_CHARS)
            if "n" in letters:
                raise Refuse("-n (--no-verify) skips the commit hooks. Fix what the hook reports instead.")
            if letters[-1] in COMMIT_VALUE_CHARS and len(letters) == len(tok) - 1:
                skip = True
    if branch == base:
        raise Refuse("you are on '%s'. Create a story branch first: git switch -c <branch>" % base)


def check_push(args, branch, base):
    positional, skip = [], False
    for tok in args:
        if skip:
            skip = False
            continue
        if tok == "--":
            continue
        if tok in ("--force", "-f") or tok.startswith("--force-with-lease") or tok.startswith("--force-if-includes"):
            raise Refuse("force-push rewrites published history. Add a commit instead.")
        if tok == "--mirror":
            raise Refuse("push --mirror overwrites the remote. Push the branch instead.")
        if tok == "--no-verify":
            raise Refuse("--no-verify skips the push hooks. Fix what the hook reports instead.")
        if tok in PUSH_VALUE_FLAGS:
            skip = True
        elif tok.startswith("--"):
            continue
        elif tok.startswith("-") and len(tok) > 1:
            letters = short_flags(tok, PUSH_VALUE_CHARS)
            if "f" in letters:
                raise Refuse("force-push rewrites published history. Add a commit instead.")
            if letters[-1] in PUSH_VALUE_CHARS and len(letters) == len(tok) - 1:
                skip = True
        else:
            positional.append(tok)

    refspecs = positional[1:]  # first positional is the remote
    if not refspecs and branch == base:
        raise Refuse("you are on '%s'. Push a story branch and open a PR." % base)
    for spec in refspecs:  # an explicit refspec is judged by its destination only
        if spec.startswith("+"):
            raise Refuse("a +refspec is a force-push. Add a commit instead.")
        dst = spec.rsplit(":", 1)[-1]
        if dst == "HEAD":
            dst = branch
        if dst.startswith("refs/heads/"):
            dst = dst[len("refs/heads/"):]
        if dst == base:
            raise Refuse("never push to '%s'. Push a story branch and open a PR." % base)


APPROVAL_COMMANDS = ("/approve-plan", "/approve-gate")
APPROVAL_REFUSAL = ("agents never post approvals; only the human tech lead does, on GitHub. "
                    "Tell the user the exact comment to post.")


def check_gh(tokens):
    for idx, tok in enumerate(tokens):
        if os.path.basename(tok) in ("gh", "gh.exe"):
            rest = tokens[idx + 1:]
            words = [t for t in rest if not t.startswith("-")][:2]
            if "pr" in rest and "merge" in rest and any(t == "--admin" for t in rest):
                raise Refuse("`gh pr merge --admin` bypasses branch protection. Merge through the normal PR checks.")
            if words == ["pr", "review"] and any(t in ("--approve", "-a") for t in rest):
                raise Refuse("`gh pr review --approve`: " + APPROVAL_REFUSAL)
            posts = words[:1] == ["api"] or words in (["pr", "comment"], ["issue", "comment"],
                                                       ["pr", "review"])
            if posts and any(c in t for t in rest for c in APPROVAL_COMMANDS):
                raise Refuse("an /approve-* comment: " + APPROVAL_REFUSAL)
            return


def track_switch(sub, args, base, where, state):
    """Update state['branch'] after `git checkout|switch`. Path checkouts leave it alone."""
    if "--" in args and sub == "checkout":
        return
    create = False
    name = None
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in ("-c", "-C", "-b", "-B", "--create", "--force-create", "--orphan"):
            create = True
            name = args[i + 1] if i + 1 < len(args) else None
            break
        if tok.startswith("-"):
            i += 1
            continue
        name = tok
        break
    if not name or name == "-":
        return
    if not create and (sub == "checkout" and os.path.exists(os.path.join(where, name))
                       and name != base):
        return  # a file or directory, not a branch
    state["branch"] = name


def check_segment(segment, base, cwd, state, depth=0):
    tokens = tokenize(segment)
    # sh -c '<command>': check the inner command too
    for idx, tok in enumerate(tokens[:-1]):
        if os.path.basename(tok) in SHELLS and tokens[idx + 1] in ("-c", "-lc", "-ic") and idx + 2 < len(tokens):
            if depth < 3:
                check_command(tokens[idx + 2], base, cwd, depth + 1, state)
    check_gh(tokens)
    inv = git_invocation(tokens)
    if not inv:
        return
    sub, args, path, hooks = inv
    where = os.path.join(cwd, path) if path and not os.path.isabs(path) else (path or cwd)
    if sub in ("checkout", "switch"):
        track_switch(sub, args, base, where, state)
        return
    if sub not in ("commit", "push"):
        return
    if hooks:
        raise Refuse("`-c core.hooksPath` replaces the repo hooks. Do not override them.")
    branch = state["branch"] or current_branch(where)
    if sub == "commit":
        check_commit(args, branch, base)
    else:
        check_push(args, branch, base)


def check_command(command, base, cwd, depth=0, state=None):
    state = state if state is not None else {"branch": None}
    for segment in split_segments(command):
        check_segment(segment, base, cwd, state, depth)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # an unparseable payload is not grounds to block work
    if payload.get("tool_name") != "Bash":
        sys.exit(0)
    command = (payload.get("tool_input") or {}).get("command") or ""
    try:
        project = _foundry_config.project_dir(payload)
        base = _foundry_config.load(project)["base_branch"]
        check_command(command, base, project)
    except Refuse as refusal:
        print("BLOCKED: %s" % refusal, file=sys.stderr)
        sys.exit(2)
    except Exception as error:  # a broken guard must fail closed
        print("BLOCKED: guard_git.py failed (%r). Fix the hook; do not work around it." % (error,), file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
