"""GitHub access. Every GitHub call in Foundry goes through this module."""
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Protocol, Tuple
from urllib.parse import quote

FAKE_ENV = "FOUNDRY_FAKE_GH"
ALLOW_FAKE_IN_CI_ENV = "FOUNDRY_ALLOW_FAKE_GH_IN_CI"


class GhError(Exception):
    """A GitHub call failed or a replay fixture has no answer.

    `status` is the HTTP status gh reported (None if unknown) and `message` the
    response text after `failed:`, without the request path. Classify errors on
    those two fields, never on the full text, which contains the repo name.
    """

    def __init__(self, text: str = "", status: Optional[int] = None,
                 message: Optional[str] = None):
        super().__init__(text)
        self.status = status
        self.message = text if message is None else message


_HTTP_STATUS_RE = re.compile(r"HTTP (\d{3})")


def _failure(what: str, err: str, out: str) -> "GhError":
    detail = err.strip() or out.strip()
    m = _HTTP_STATUS_RE.search(detail)
    return GhError(f"{what} failed: {detail}", int(m.group(1)) if m else None, detail)


class GhUnavailable(GhError):
    """gh is not installed or not logged in."""


@dataclass(frozen=True)
class Commit:
    sha: str
    parent: Optional[str]  # first parent, None for a root commit
    date: str  # ISO 8601 committer date
    message: str = ""  # full commit message
    author_name: str = ""
    author_email: str = ""


@dataclass(frozen=True)
class PrInfo:
    title: str
    body: str  # "" when the PR has no description
    head_sha: str
    head_ref: str  # head branch name
    labels: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Comment:
    author: str
    body: str
    created_at: str
    updated_at: str  # differs from created_at once the comment was edited


@dataclass(frozen=True)
class Review:
    author: str
    state: str  # APPROVED, COMMENTED, CHANGES_REQUESTED, ...
    submitted_at: str
    commit: str  # sha the review was made on


@dataclass(frozen=True)
class MergedPr:
    number: int
    head_sha: str  # the head the PR was merged with
    merged_at: str  # ISO 8601
    labels: Tuple[str, ...] = ()


class GitHub(Protocol):
    def pr_head(self, repo: str, pr: int) -> str: ...
    def pr_info(self, repo: str, pr: int) -> PrInfo: ...
    def pr_commits(self, repo: str, pr: int) -> List[Commit]: ...
    def file_at(self, repo: str, sha: str, path: str) -> Optional[str]: ...
    def pr_comments(self, repo: str, pr: int) -> List[Comment]: ...
    def pr_reviews(self, repo: str, pr: int) -> List[Review]: ...
    def pr_files(self, repo: str, pr: int) -> List[str]: ...
    def merged_prs(self, repo: str) -> List[MergedPr]: ...
    def api(self, method: str, path: str, body: Optional[dict] = None) -> dict: ...


Runner = Callable[..., Tuple[int, str, str]]


def _run(args: List[str], stdin: Optional[str] = None) -> Tuple[int, str, str]:
    p = subprocess.run(args, input=stdin, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def _loads_concatenated(text: str) -> List[Any]:
    """Parse the output of `gh api --paginate`: one JSON document per page."""
    dec, i, out = json.JSONDecoder(), 0, []
    text = text.strip()
    while i < len(text):
        value, i = dec.raw_decode(text, i)
        out.append(value)
        while i < len(text) and text[i].isspace():
            i += 1
    return out


def _flatten(pages: List[Any]) -> List[Any]:
    items: List[Any] = []
    for page in pages:
        items.extend(page if isinstance(page, list) else [page])
    return items


class GhCli:
    """GitHub over the `gh` CLI. Raises GhUnavailable if gh is missing or logged out."""

    def __init__(self, runner: Optional[Runner] = None):
        self._runner = runner
        self._checked = runner is not None

    def _exec(self, args: List[str], stdin: Optional[str] = None) -> Tuple[int, str, str]:
        if not self._checked:
            if shutil.which("gh") is None:
                raise GhUnavailable(
                    "gh is not installed. Install it from https://cli.github.com, "
                    "then run: gh auth login")
            rc, _, _ = _run(["gh", "auth", "status"])
            if rc != 0:
                raise GhUnavailable("gh is not logged in. Run: gh auth login")
            self._checked = True
        return (self._runner or _run)(["gh"] + args, stdin)

    def _get(self, path: str, paginate: bool = False) -> Any:
        args = ["api"] + (["--paginate"] if paginate else []) + [path]
        rc, out, err = self._exec(args)
        if rc != 0:
            raise _failure(f"gh api {path}", err, out)
        pages = _loads_concatenated(out)
        return _flatten(pages) if paginate else (pages[0] if pages else None)

    def pr_head(self, repo: str, pr: int) -> str:
        return self._get(f"repos/{repo}/pulls/{pr}")["head"]["sha"]

    def pr_info(self, repo: str, pr: int) -> PrInfo:
        d = self._get(f"repos/{repo}/pulls/{pr}")
        return PrInfo(d.get("title") or "", d.get("body") or "", d["head"]["sha"],
                      d["head"]["ref"], tuple(l["name"] for l in d.get("labels") or []))

    def pr_commits(self, repo: str, pr: int) -> List[Commit]:
        return [
            Commit(c["sha"],
                   c["parents"][0]["sha"] if c.get("parents") else None,
                   c["commit"]["committer"]["date"],
                   c["commit"].get("message") or "",
                   (c["commit"].get("author") or {}).get("name") or "",
                   (c["commit"].get("author") or {}).get("email") or "")
            for c in self._get(f"repos/{repo}/pulls/{pr}/commits", paginate=True)
        ]

    def file_at(self, repo: str, sha: str, path: str) -> Optional[str]:
        url = f"repos/{repo}/contents/{quote(path)}?ref={quote(sha)}"
        rc, out, err = self._exec(
            ["api", "-H", "Accept: application/vnd.github.raw", url])
        if rc == 0:
            return out
        if "HTTP 404" in err or "Not Found" in err:
            return None
        raise GhError(f"gh api {url} failed: {err.strip()}")

    def pr_comments(self, repo: str, pr: int) -> List[Comment]:
        return [Comment(c["user"]["login"], c.get("body") or "", c["created_at"],
                        c["updated_at"])
                for c in self._get(f"repos/{repo}/issues/{pr}/comments", paginate=True)]

    def pr_reviews(self, repo: str, pr: int) -> List[Review]:
        return [Review(r["user"]["login"], r["state"], r.get("submitted_at") or "",
                       r.get("commit_id") or "")
                for r in self._get(f"repos/{repo}/pulls/{pr}/reviews", paginate=True)
                if r.get("submitted_at")]

    def pr_files(self, repo: str, pr: int) -> List[str]:
        return [f["filename"]
                for f in self._get(f"repos/{repo}/pulls/{pr}/files", paginate=True)]

    def merged_prs(self, repo: str) -> List[MergedPr]:
        """Every merged PR with its labels, oldest merge first (one paginated listing)."""
        prs = self._get(f"repos/{repo}/pulls?state=closed&per_page=100", paginate=True)
        found = [
            MergedPr(p["number"], p["head"]["sha"], p["merged_at"],
                     tuple(l["name"] for l in p.get("labels") or []))
            for p in prs if p.get("merged_at")
        ]
        return sorted(found, key=lambda m: (m.merged_at, m.number))

    def api(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        args = ["api", "-X", method.upper(), path]
        if body is not None:
            args += ["--input", "-"]
        rc, out, err = self._exec(args, json.dumps(body) if body is not None else None)
        if rc != 0:
            raise _failure(f"gh api {method} {path}", err, out)
        return json.loads(out) if out.strip() else {}


class ReplayGitHub:
    """Serves recorded responses from a JSON fixture (env FOUNDRY_FAKE_GH).

    Fixture format: an object with one key per method name. Each value is an
    object mapping the call's arguments, joined with "|", to the response:

        "pr_head":     {"owner/repo|7": "c1"}
        "pr_info":     {"owner/repo|7": {"title": "t", "body": "b", "head_sha": "c1",
                                         "head_ref": "cu-1-x", "labels": ["gate:G1"]}}
        "pr_commits":  {"owner/repo|7": [{"sha": "c1", "parent": null,
                                          "date": "2026-01-01T08:00:00Z",
                                          "message": "m", "author_name": "n",
                                          "author_email": "e"}]}
                       (message and the author fields are optional)
        "file_at":     {"owner/repo|c1|docs/stories/x.md": "text" | null}
        "pr_comments": {"owner/repo|7": [{"author": "a", "body": "b",
                                          "created_at": "2026-01-01T09:00:00Z",
                                          "updated_at": "2026-01-01T09:00:00Z"}]}
        "pr_reviews":  {"owner/repo|7": [{"author": "a", "state": "APPROVED",
                                          "submitted_at": "...", "commit": "c1"}]}
        "pr_files":    {"owner/repo|7": ["docs/stories/x.md"]}
        "merged_prs":  {"owner/repo": [{"number": 7, "head_sha": "c1",
                                        "merged_at": "2026-01-02T09:00:00Z",
                                        "labels": ["gate:G1"]}]}
        "api":         {"POST|repos/owner/repo/issues": {"number": 1}}

    A response of {"__error": {"status": 404, "message": "Not Found"}} makes that
    call raise a GhError with that status and message (a recorded failure).
    A call with no recorded entry raises GhError naming the missing key.
    """

    def __init__(self, path: str):
        with open(path, encoding="utf-8") as fh:
            self._data = json.load(fh)

    def _lookup(self, method: str, *args: Any) -> Any:
        key = "|".join(str(a) for a in args)
        table = self._data.get(method, {})
        if key not in table:
            raise GhError(f"replay fixture has no {method!r} entry for {key!r}")
        value = table[key]
        if isinstance(value, dict) and isinstance(value.get("__error"), dict):
            err = value["__error"]
            raise GhError(f"{method} {key} failed: HTTP {err.get('status')}",
                          err.get("status"), err.get("message"))
        return value

    def pr_head(self, repo: str, pr: int) -> str:
        return self._lookup("pr_head", repo, pr)

    def pr_info(self, repo: str, pr: int) -> PrInfo:
        d = self._lookup("pr_info", repo, pr)
        return PrInfo(d["title"], d.get("body") or "", d["head_sha"], d["head_ref"],
                      tuple(d.get("labels", [])))

    def pr_commits(self, repo: str, pr: int) -> List[Commit]:
        return [Commit(c["sha"], c.get("parent"), c["date"], c.get("message", ""),
                       c.get("author_name", ""), c.get("author_email", ""))
                for c in self._lookup("pr_commits", repo, pr)]

    def file_at(self, repo: str, sha: str, path: str) -> Optional[str]:
        return self._lookup("file_at", repo, sha, path)

    def pr_comments(self, repo: str, pr: int) -> List[Comment]:
        return [Comment(c["author"], c["body"], c["created_at"], c["updated_at"])
                for c in self._lookup("pr_comments", repo, pr)]

    def pr_reviews(self, repo: str, pr: int) -> List[Review]:
        return [Review(r["author"], r["state"], r["submitted_at"], r["commit"])
                for r in self._lookup("pr_reviews", repo, pr)]

    def pr_files(self, repo: str, pr: int) -> List[str]:
        return list(self._lookup("pr_files", repo, pr))

    def merged_prs(self, repo: str) -> List[MergedPr]:
        return [MergedPr(m["number"], m["head_sha"], m["merged_at"],
                         tuple(m.get("labels", [])))
                for m in self._lookup("merged_prs", repo)]

    def api(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        return self._lookup("api", method.upper(), path)


def get_github() -> GitHub:
    """GhCli, or a ReplayGitHub when FOUNDRY_FAKE_GH names a fixture file.

    The fixture is refused on a GitHub Actions runner, where it would replace
    the real checks with canned answers, unless FOUNDRY_ALLOW_FAKE_GH_IN_CI=1
    (only Foundry's own tests set that).
    """
    fixture = os.environ.get(FAKE_ENV)
    if (fixture and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get(ALLOW_FAKE_IN_CI_ENV) != "1"):
        raise GhError(f"{FAKE_ENV} is not allowed on GitHub Actions")
    return ReplayGitHub(fixture) if fixture else GhCli()
