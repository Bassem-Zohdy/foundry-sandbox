"""Workflow and Dockerfile line checks for /approve-deps (R35): one removed line for one added
line, only a ref, tag or digest changing, and no line inside a block scalar or heredoc."""
import difflib
import re
from typing import List, Optional, Set, Tuple

_INDICATOR = r"[|>][-+0-9]*\s*(#.*)?$"
_BLOCK_START_RE = re.compile(r"^(\s*)((?:- )*)(.*?)(?:^|[\s:])" + _INDICATOR)
_INDICATOR_ONLY_RE = re.compile(r"^\s*" + _INDICATOR)
_USES_RE = re.compile(
    r"^(\s*)(?:- )?uses:\s+([\"']?)(\./\S+?|docker://\S+?|[\w.-]+/[\w.-]+(?:/[\w./-]+?)?@[^\s\"']+)"
    r"\2(\s+#[^\"'`$;&|<>(){}\\]*)?$")
_FROM_RE = re.compile(r"^\s*FROM\s+(--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?\s*$", re.I)
_HEREDOC_RE = re.compile(r"<<(-?)\s*[\"']?(\w+)[\"']?")
_REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_TAG_RE = re.compile(r"^\w[\w.-]{0,127}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPO_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")


def pairs(a: List[str], b: List[str]) -> Optional[List[Tuple[int, int]]]:
    """(index in a, index in b) of each changed line, one removed line for one added line.

    None when any change is a pure insert or delete, or a replace of unequal length.
    """
    out: List[Tuple[int, int]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag != "replace" or i2 - i1 != j2 - j1:
            return None
        out.extend(zip(range(i1, i2), range(j1, j2)))
    return out


def _quote_open(value: str) -> Optional[str]:
    """The quote char of a flow scalar that starts in `value` and is not closed on the line."""
    value = value.strip()
    if not value or value[0] not in "\"'":
        return None
    q, i = value[0], 1
    while i < len(value):
        if value[i] == "\\" and q == '"':
            i += 2
            continue
        if value[i] == q:
            if q == "'" and value[i + 1:i + 2] == "'":
                i += 2
                continue
            return None
        i += 1
    return q


def _closes(line: str, q: str) -> bool:
    i = 0
    while i < len(line):
        if line[i] == "\\" and q == '"':
            i += 2
            continue
        if line[i] == q:
            if q == "'" and line[i + 1:i + 2] == "'":
                i += 2
                continue
            return True
        i += 1
    return False


def _block_lines(lines: List[str]) -> Set[int]:
    """Indexes of the lines inside a block scalar (`run: |`, `run: !!str |`, `run: &x |`, `- |`,
    an indicator on the next line) or a multi-line quoted scalar."""
    inside: Set[int] = set()
    key_col: Optional[int] = None
    quote: Optional[str] = None
    for n, line in enumerate(lines):
        if quote is not None:
            inside.add(n)
            if _closes(line, quote):
                quote = None
            continue
        if key_col is not None:
            if not line.strip() or len(line) - len(line.lstrip()) > key_col:
                inside.add(n)
                continue
            key_col = None
        indent = len(line) - len(line.lstrip())
        if _INDICATOR_ONLY_RE.match(line) and n > 0:  # `run:` then the indicator on its own line
            key_col = indent - 1
            continue
        m = _BLOCK_START_RE.match(line)
        if m:
            dashes = len(m.group(2)) // 2
            key_col = indent if not m.group(3).strip() and dashes else indent + 2 * dashes
            continue
        km = re.match(r"^\s*(?:- )*[^\s#][^#]*?:\s+(.*)$", line)
        quote = _quote_open(km.group(1)) if km else None
    return inside


def _heredoc_lines(lines: List[str]) -> Set[int]:
    inside: Set[int] = set()
    pending: List[Tuple[str, str]] = []
    for n, line in enumerate(lines):
        if pending:
            inside.add(n)
            dash, tag = pending[0]
            if (line.lstrip("\t") if dash else line) == tag:
                pending.pop(0)
            continue
        pending = _HEREDOC_RE.findall(line)
    return inside


def _continued(lines: List[str], i: int) -> bool:
    """True when line i continues the line before it (that one ends with a backslash)."""
    return i > 0 and lines[i - 1].rstrip().endswith("\\")


def _image_repo(image: str) -> Optional[str]:
    """The image repository ("node:22@sha256:.." -> "node"), None unless the reference is plain:
    a lower-case repository, an optional tag of word characters, an optional sha256 digest."""
    image, _, digest = image.partition("@")
    if digest and not _DIGEST_RE.match(digest):
        return None
    head, _, last = image.rpartition("/")
    name, _, tag = last.partition(":")
    repo = (head + "/" if head else "") + name
    if not _REPO_RE.match(repo) or (":" in image and not tag) or (tag and not _TAG_RE.match(tag)):
        return None
    return repo


def _uses_target(line: str) -> Optional[str]:
    """What a `uses:` line runs without its ref: "actions/checkout", "./x", "docker://img"."""
    m = _USES_RE.match(line)
    if not m:
        return None
    target = m.group(3)
    if target.startswith("./"):
        return target
    if target.startswith("docker://"):
        repo = _image_repo(target[len("docker://"):])
        return None if repo is None else "docker://" + repo
    path, _, ref = target.partition("@")
    if ".github/workflows/" in path or not _REF_RE.match(ref):
        return None
    return path


def _workflow(old: str, new: str) -> bool:
    a, b = old.splitlines(), new.splitlines()
    changed = pairs(a, b)
    if changed is None:
        return False
    skip_a, skip_b = _block_lines(a), _block_lines(b)
    for i, j in changed:
        if i in skip_a or j in skip_b or _continued(a, i) or _continued(b, j):
            return False
        target = _uses_target(a[i])
        if target is None or target != _uses_target(b[j]):
            return False
    return True


def _dockerfile(old: str, new: str) -> bool:
    a, b = old.splitlines(), new.splitlines()
    changed = pairs(a, b)
    if changed is None:
        return False
    skip_a, skip_b = _heredoc_lines(a), _heredoc_lines(b)
    for i, j in changed:
        if i in skip_a or j in skip_b or _continued(a, i) or _continued(b, j):
            return False
        x, y = _FROM_RE.match(a[i]), _FROM_RE.match(b[j])
        if not x or not y:
            return False
        repo = _image_repo(x.group(2))
        platform = lambda m: (m.group(1) or "").strip().casefold()
        stage = lambda m: (m.group(3) or "").casefold()
        if (repo is None or repo != _image_repo(y.group(2))
                or platform(x) != platform(y) or stage(x) != stage(y)):
            return False
    return True
