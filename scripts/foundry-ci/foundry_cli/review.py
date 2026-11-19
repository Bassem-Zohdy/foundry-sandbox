"""Decide the review verdict in code from reviewer result files.

The gate fails closed: anything it cannot verify is a problem, never approval.
"""
import argparse
import json
import re
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .cli import register
from .config import Config, ConfigError, load_config
from .diffinfo import BadRef, ChangedFile, changed_files
from .risky import default_base, risky_reasons
from .scratch import scratch_dir

__all__ = ["GateResult", "expected_reviewers", "review_dir", "gate", "register_commands"]

_SHA = re.compile(r"^[0-9a-f]{40}$")
_FASTAPI = re.compile(r"^\s*(from|import)\s+fastapi\b")
_TS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_VERDICTS = ("confirmed", "refuted", "uncertain")
_FINDING_STR = ("id", "title", "file", "lines", "evidence", "fix")


@dataclass
class GateResult:
    ok: bool
    problems: List[str] = field(default_factory=list)
    downgraded: List[str] = field(default_factory=list)
    open_blocking: List[str] = field(default_factory=list)


def expected_reviewers(config: Config, files: List[ChangedFile]) -> List[str]:
    out = {"code-reviewer"}
    paths = [f.path for f in files]
    if any(p.endswith(_TS_EXT) for p in paths):
        out.add("typescript-reviewer")
    if any(p.endswith((".tsx", ".jsx")) for p in paths):
        out.add("react-reviewer")
    if any(p.endswith(".py") for p in paths):
        out.add("python-reviewer")
    if any(_FASTAPI.search(line) for f in files for line in f.added_lines):
        out.add("fastapi-reviewer")
    reasons = risky_reasons(config, files)
    if reasons:
        out.update(("security-reviewer", "silent-failure-hunter"))
    if any(r.reason.startswith("migration: ") for r in reasons):
        out.add("database-reviewer")
    return sorted(out)


def review_dir(repo: Path, branch: str) -> Path:
    return scratch_dir(Path(repo)) / "review" / branch


def _run_git(repo: Path, *args: str) -> "subprocess.CompletedProcess[str]":
    """Run git in the C locale: callers read its English stderr."""
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          env={**os.environ, "LC_ALL": "C"})


def _git_out(repo: Path, *args: str) -> Optional[str]:
    r = _run_git(repo, *args)
    return r.stdout.strip() if r.returncode == 0 else None


def _is_ancestor(repo: Path, commit: str, head: str) -> bool:
    """True only when git positively says `commit` is an ancestor of `head`."""
    if not _SHA.match(commit):
        return False
    r = _run_git(repo, "merge-base", "--is-ancestor", commit, head)
    return r.returncode == 0


def _read_json(path: Path) -> Tuple[Any, Optional[str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError) as e:
        return None, f"cannot read: {e}"
    except (ValueError, RecursionError) as e:
        return None, f"not valid JSON: {e}"


def _check_finding(f: Any, i: int) -> Optional[str]:
    if not isinstance(f, dict):
        return f"findings[{i}] is not an object"
    for k in _FINDING_STR:
        if not isinstance(f.get(k), str):
            return f"findings[{i}] key '{k}' missing or not a string"
    if not f["id"].strip():
        return f"findings[{i}] has an empty id"
    if f.get("severity") not in ("blocking", "suggestion"):
        return f"findings[{i}] severity must be 'blocking' or 'suggestion'"
    if "principle" in f and not isinstance(f["principle"], str):
        return f"findings[{i}] principle is not a string"
    return None


def _check_result(d: Any, reviewer: str) -> Optional[str]:
    if not isinstance(d, dict):
        return "top level is not an object"
    if d.get("reviewer") != reviewer:
        return f"key 'reviewer' must be '{reviewer}'"
    if not isinstance(d.get("head"), str) or not _SHA.match(d["head"]):
        return "key 'head' missing or not a 40-hex sha"
    if d.get("scope") not in ("full", "fix"):
        return "key 'scope' must be 'full' or 'fix'"
    if d["scope"] == "fix" and (not isinstance(d.get("since"), str) or not _SHA.match(d["since"])):
        return "key 'since' missing or not a 40-hex sha (required for scope 'fix')"
    if not isinstance(d.get("findings"), list):
        return "key 'findings' missing or not a list"
    seen = set()
    for i, f in enumerate(d["findings"]):
        err = _check_finding(f, i)
        if err:
            return err
        if f["id"] in seen:
            return f"duplicate finding id '{f['id']}'"
        seen.add(f["id"])
    res = d.get("resolved", [])
    if not isinstance(res, list):
        return "key 'resolved' is not a list"
    for i, r in enumerate(res):
        if not (isinstance(r, dict) and isinstance(r.get("id"), str)
                and isinstance(r.get("commit"), str) and _SHA.match(r["commit"])):
            return f"resolved[{i}] needs a string 'id' and a 40-hex 'commit'"
    return None


def _check_verify(d: Any) -> Optional[str]:
    if not isinstance(d, dict) or not isinstance(d.get("verdicts"), list):
        return "key 'verdicts' missing or not a list"
    for i, v in enumerate(d["verdicts"]):
        if not isinstance(v, dict) or any(not isinstance(v.get(k), str)
                                          for k in ("reviewer", "id", "verdict", "reason")):
            return f"verdicts[{i}] needs string reviewer, id, verdict and reason"
        if v["verdict"] not in _VERDICTS:
            return f"verdicts[{i}] verdict must be one of {', '.join(_VERDICTS)}"
    return None


def _walk_chain(repo: Path, rdir: Path, reviewer: str, head: str,
                problems: List[str]) -> Optional[List[Dict[str, Any]]]:
    """Return the chain oldest-first, or None after recording a problem."""
    name = f"{head[:7]}-{reviewer}.json"
    if not (rdir / name).is_file():
        problems.append(f"{reviewer}: no result for head {head[:7]} (re-review needed)")
        return None
    chain: List[Dict[str, Any]] = []
    seen = set()
    want_head = head
    pointed_by = None
    while True:
        name = f"{want_head[:7]}-{reviewer}.json"
        tail = f" (pointed to by since in {pointed_by})" if pointed_by else ""
        d, err = _read_json(rdir / name)
        err = err or _check_result(d, reviewer)
        if err:
            kind = "broken chain at" if chain else "invalid result"
            problems.append(f"{reviewer}: {kind} {name}: {err}{tail}")
            return None
        if d["head"] != want_head:
            what = "broken chain at" if chain else "invalid result"
            problems.append(f"{reviewer}: {what} {name}: head {d['head']} "
                            f"is not {want_head}{tail}")
            return None
        if want_head in seen:
            problems.append(f"{reviewer}: broken chain at {name}: cycle")
            return None
        seen.add(want_head)
        chain.append(d)
        if d["scope"] == "full":
            return chain[::-1]
        since = d["since"]
        if (since == want_head or not _is_ancestor(repo, since, want_head)
                or not _is_ancestor(repo, since, head)):
            problems.append(f"{reviewer}: broken chain at {name}: since {since} "
                            "is not an earlier ancestor of this result's head")
            return None
        skipped = _skipped_round(repo, rdir, reviewer, since, want_head)
        if skipped and skipped.startswith("ambiguous:"):
            problems.append(f"{reviewer}: {skipped[10:]}: ambiguous short sha prefix")
            return None
        if skipped:
            problems.append(f"{reviewer}: {name} skips the review at {skipped[:7]} "
                            "(since must be the previous reviewed head)")
            return None
        pointed_by, want_head = name, since


def _git_resolve(repo: Path, h7: str) -> Tuple[str, Optional[str]]:
    """Resolve a short sha: ("ok", sha), ("ambiguous", None) or ("none", None)."""
    r = _run_git(repo, "rev-parse", "--verify", f"{h7}^{{commit}}")
    if r.returncode == 0 and _SHA.match(r.stdout.strip()):
        return "ok", r.stdout.strip()
    return ("ambiguous" if "ambiguous" in r.stderr else "none"), None


def _skipped_round(repo: Path, rdir: Path, reviewer: str, since: str, own: str) -> Optional[str]:
    """Problem text if a result file sits strictly between `since` and `own`.

    Keyed off the file NAME only, so a corrupt file or a lying `head` field
    cannot hide an intermediate round. Returns the skipped head7 or a message.
    """
    suffix = f"-{reviewer}.json"
    for p in sorted(rdir.glob(f"*{suffix}")):
        h7 = p.name[:-len(suffix)]
        if not re.fullmatch(r"[0-9a-f]{7}", h7):
            continue
        kind, h = _git_resolve(repo, h7)
        if kind == "ambiguous":
            return f"ambiguous:{p.name}"
        if (h and h not in (since, own) and _is_ancestor(repo, since, h)
                and _is_ancestor(repo, h, own)):
            return h
    return None


def _load_verdicts(rdir: Path, heads: List[str], problems: List[str]
                   ) -> Dict[Tuple[str, str], Tuple[int, str]]:
    """(reviewer, id) -> (chain index of newest verdict, verdict). `heads` oldest-first."""
    out: Dict[Tuple[str, str], Tuple[int, str]] = {}
    for idx, h in enumerate(heads):
        name = f"{h[:7]}-verify.json"
        if not (rdir / name).is_file():
            continue
        d, err = _read_json(rdir / name)
        err = err or _check_verify(d)
        if err:
            problems.append(f"verifier: invalid {name}: {err}")
            continue
        for v in d["verdicts"]:
            out[(v["reviewer"], v["id"])] = (idx, v["verdict"])
    return out


def _review_one(repo: Path, head: str, chain: List[Dict[str, Any]],
                verdicts: Dict[Tuple[str, str], Tuple[int, str]], res: GateResult) -> None:
    reviewer = chain[0]["reviewer"]
    raised: Dict[str, Tuple[int, Dict[str, Any]]] = {}
    was_blocking = set()
    for idx, r in enumerate(chain):
        for f in r["findings"]:
            if f["severity"] == "suggestion" and f["id"] in was_blocking:
                res.downgraded.append(f"{reviewer} {f['id']} re-listed as suggestion "
                                      f"at {r['head'][:7]}")
            if f["severity"] == "blocking":
                was_blocking.add(f["id"])
            raised[f["id"]] = (idx, f)   # newest raising result wins
    for fid, (idx, f) in sorted(raised.items()):
        if f["severity"] != "blocking":
            continue
        empty = [k for k in ("evidence", "fix") if not f[k].strip()]
        if empty:
            res.downgraded.append(f"{reviewer} {fid}: blocking finding has empty "
                                  f"{' and '.join(empty)}; treated as a suggestion")
            continue
        if _closed(repo, head, chain, idx, reviewer, fid, verdicts):
            continue
        res.open_blocking.append(f"{reviewer} {fid} {f['title']} ({f['file']}:{f['lines']})")


def _closed(repo: Path, head: str, chain: List[Dict[str, Any]], raised_idx: int,
            reviewer: str, fid: str, verdicts: Dict[Tuple[str, str], Tuple[int, str]]) -> bool:
    v = verdicts.get((reviewer, fid))
    if v is not None and v[0] >= raised_idx and v[1] == "refuted":
        return True
    raised_head = chain[raised_idx]["head"]
    for r in chain[raised_idx + 1:]:
        for e in r.get("resolved", []):
            if (e["id"] == fid and _is_ancestor(repo, e["commit"], head)
                    and not _is_ancestor(repo, e["commit"], raised_head)):
                return True
    return False


def gate(config: Config, repo: Path, base: str) -> GateResult:
    repo = Path(repo)
    res = GateResult(ok=False)
    head = _git_out(repo, "rev-parse", "HEAD")
    branch = _git_out(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if not head or not _SHA.match(head) or not branch or branch == "HEAD":
        res.problems.append("cannot determine HEAD and branch (detached or not a repo)")
        return res
    reviewers = expected_reviewers(config, changed_files(repo, base, head))
    rdir = review_dir(repo, branch)
    for reviewer in reviewers:
        chain = _walk_chain(repo, rdir, reviewer, head, res.problems)
        if chain is None:
            continue
        heads = [r["head"] for r in chain]
        verdicts = _load_verdicts(rdir, heads, res.problems)
        _review_one(repo, head, chain, verdicts, res)
    res.ok = not res.problems and not res.open_blocking
    return res


def _configure(p: argparse.ArgumentParser) -> None:
    p.add_argument("--base", help="base ref (default: origin/<baseBranch> or <baseBranch>)")
    p.add_argument("--repo", default=".", help="repo root containing foundry.json")


def _setup(ns: argparse.Namespace) -> Optional[Tuple[Config, Path, str, str]]:
    repo = Path(ns.repo)
    config = load_config(repo)
    branch = _git_out(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if not branch or branch == "HEAD":
        print("detached HEAD or not a git repository: check out a branch", file=sys.stderr)
        return None
    return config, repo, branch, ns.base or default_base(config, repo)


def _run_plan(ns: argparse.Namespace) -> int:
    try:
        setup = _setup(ns)
        if setup is None:
            return 2
        config, repo, branch, base = setup
        files = changed_files(repo, base)
        out = {"head": _git_out(repo, "rev-parse", "HEAD"), "base": base, "branch": branch,
               "dir": str(review_dir(repo, branch)),
               "reviewers": expected_reviewers(config, files),
               "risky": [{"path": r.path, "reason": r.reason}
                         for r in risky_reasons(config, files)]}
    except ConfigError as e:
        for problem in e.problems:
            print(problem, file=sys.stderr)
        return 2
    except (BadRef, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2))
    return 0


def _run_gate(ns: argparse.Namespace) -> int:
    try:
        setup = _setup(ns)
        if setup is None:
            return 2
        config, repo, _branch, base = setup
        res = gate(config, repo, base)
    except ConfigError as e:
        for problem in e.problems:
            print(problem, file=sys.stderr)
        return 2
    except (BadRef, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    for line in res.problems:
        print(f"problem: {line}")
    for line in res.open_blocking:
        print(f"open: {line}")
    for line in res.downgraded:
        print(f"downgraded: {line}")
    if res.ok:
        print("pass")
    return 0 if res.ok else 1


def register_commands() -> None:
    register("review plan", _run_plan, _configure)
    register("review gate", _run_gate, _configure)
