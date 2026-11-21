"""What plan approval watches in a story file, and what the file may not contain.

Approval watches the whole story from its first `## ` section (the Story) to
the first column-0 `## Ledger` heading outside a code fence: the story,
acceptance criteria, requirement IDs and Plan. Any edit there resets the
approval, however it is spelled. The title and header table above the first
section, and the Ledger and Test evidence below, are not watched.

So that text added below `## Ledger` can never render inside the Plan, and
so that `## Ledger` is where GitHub also sees it, a story file may not
contain (each fails closed):

- Unicode line or page separators (VT, FF, FS, GS, RS, NEL, U+2028, U+2029):
  Python splits lines on them, CommonMark does not;
- raw HTML blocks (a line starting with a tag, comment, `<!` or `<?`, also
  indented or inside a list item or quote), which can hide `## Ledger` on
  GitHub;
- code fences that are indented, in a list item or in a blockquote. This rule
  applies to every line, even inside a column-0 fence;
- before the first `## ` section: anything but one `# ` title line followed
  by at most one header table (header row, delimiter row, rows), or any
  HTML there (inline HTML in a title can open a fake heading and hide the
  real story);
- below `## Ledger`: any heading-like line (ATX `#` or a setext `===`/`---`
  underline, also inside a list item or quote) except an exact
  `## Test evidence`, inline HTML tags (even in backticks), and lines that
  start a link or footnote definition (`[label]: ...`).

Code fenced at column 0 may contain anything. Lines are split the CommonMark
way (split_lines).
"""
import re
from typing import Iterator, List, Optional, Tuple

_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")
_SEPARATORS = re.compile("[\x0b\x0c\x1c\x1d\x1e\x85  ]")

# CommonMark fences: up to 3 spaces of indent, then 3 or more ` or ~. A
# backtick opener's info string may not contain a backtick. A closer uses the
# same character, is at least as long as the opener and has only whitespace
# after it.
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}(?=[^`]*$)|~{3,})")
_FENCE_CLOSE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")

# A fence marker not at column 0, or after any run of list markers or `>`.
_CONTAINER = r"(?:[ \t]*(?:[-*+][ \t]+|\d{1,9}[.)][ \t]+|>[ \t]*))+"
_NESTED_FENCE = re.compile(r"^(?:[ \t]+|" + _CONTAINER + r")(`{3,}|~{3,})")
_CONTAINER_PREFIX = re.compile(r"^(?:[ \t]*(?:[-*+][ \t]+|\d{1,9}[.)][ \t]+|>[ \t]?))*")
# An HTML block start (CommonMark types 1-7), but not an autolink like <https://x>.
_HTML_START = re.compile(r"^ {0,3}<(?:!|\?|/?[A-Za-z][A-Za-z0-9-]*(?:[\s/>]|$))")
_INLINE_TAG = re.compile(r"<(?:!|\?|/?[A-Za-z][A-Za-z0-9-]*(?:[\s/>]|$))")
_HEADING_LIKE = re.compile(r"^[ \t]*(?:#{1,6}(?:[ \t]|$)|(?:=+|-+)[ \t]*$)")
_TITLE = re.compile(r"^# \S")
_TABLE_ROW = re.compile(r"^[ \t]*\|")
_DELIMITER_ROW = re.compile(r"^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")

SECTION_PREFIX = "## "
LEDGER = "## Ledger"
TEST_EVIDENCE = "## Test evidence"


def split_lines(text: str, keepends: bool = False) -> List[str]:
    """Lines split on \\r\\n, \\r and \\n only, as CommonMark does. A leading BOM is dropped."""
    if text.startswith("﻿"):
        text = text[1:]
    lines = _LINE.findall(text)
    return lines if keepends else [ln.rstrip("\r\n") for ln in lines]


def _regions(markdown: str) -> Iterator[Tuple[str, str, bool]]:
    """(raw line with ending, region, in_fence) for each line.

    Regions: "preamble" before the first column-0 `## ` heading, "watched"
    from it up to `## Ledger`, "ledger" for that heading, "tail" after it.
    Fence lines (openers, content, closers) are in_fence.
    """
    region = "preamble"
    fence: Optional[str] = None  # the open fence's run, e.g. "````"
    for raw in split_lines(markdown, keepends=True):
        line = raw.rstrip("\r\n")
        if fence is not None:
            m = _FENCE_CLOSE.match(line)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
            yield raw, region, True
            continue
        opener = _FENCE_OPEN.match(line)
        if opener:
            fence = opener.group(1)
            yield raw, region, True
            continue
        if region in ("preamble", "watched") and line.rstrip() == LEDGER:
            region = "tail"
            yield raw, "ledger", False
            continue
        if region == "preamble" and line.startswith(SECTION_PREFIX):
            region = "watched"
        yield raw, region, False


def watched_story(markdown: str) -> Optional[str]:
    """The text plan approval compares: from the first `## ` section up to
    `## Ledger` (or the end). None when that text has no `## Plan` heading."""
    body = [raw for raw, region, _ in _regions(markdown) if region == "watched"]
    if not any(raw.rstrip() == "## Plan" for raw in body):
        return None
    return "".join(body)


def _line_problem(line: str, region: str, in_fence: bool) -> Optional[str]:
    if _NESTED_FENCE.match(line):
        return ("a code block that is indented or inside a list or quote; "
                "start code fences at column 0")
    if in_fence or region in ("ledger", "preamble"):
        return None
    inner = _CONTAINER_PREFIX.sub("", line)
    if _HTML_START.match(line.lstrip()) or _HTML_START.match(inner.lstrip()):
        return ("a raw HTML block (indented code too); remove it or put it "
                "inside a column-0 code fence")
    if region != "tail":
        return None
    if _HEADING_LIKE.match(inner) and line.rstrip() != TEST_EVIDENCE:
        return (f"a heading below '{LEDGER}' (only an exact '{TEST_EVIDENCE}' may follow it; "
                "'---' and '===' lines count as headings)")
    if _INLINE_TAG.search(line):
        return (f"inline HTML below '{LEDGER}' (backticks do not help); "
                "put it in a fenced code block")
    return None


def _definition_lines(markdown: str) -> Optional[str]:
    """A link or footnote definition below `## Ledger`. A paragraph that starts
    with `[` fails if any of its lines has `]:`, since a label may span lines
    and hold escaped brackets."""
    label_open = False
    for raw, region, in_fence in _regions(markdown):
        line = raw.rstrip("\r\n")
        if region != "tail" or in_fence or not line.strip():
            label_open = False
            continue
        starts = _CONTAINER_PREFIX.sub("", line).lstrip().startswith("[")
        label_open = label_open or starts
        if label_open and ("]:" in line or (starts and "]" not in line)):
            return f"a link or footnote definition below '{LEDGER}'"
    return None


def _cells(row: str) -> int:
    row = row.strip()
    row = row[1:] if row.startswith("|") else row
    row = row[:-1] if row.endswith("|") and not row.endswith("\\|") else row
    return len(re.split(r"(?<!\\)\|", row))


def _preamble_problem(lines: List[str]) -> Optional[str]:
    """Before the first section: one `# ` title, then at most one header table
    (header row, delimiter row, rows), with no HTML anywhere."""
    why = ("text before the first '## ' section; only one '# ' title "
           "and one header table, without HTML, go there")
    if any(_INLINE_TAG.search(ln) for ln in lines):
        return why
    body = [ln for ln in lines if ln.strip()]
    if not body:
        return None
    if not _TITLE.match(body[0]):
        return why
    rest = lines[lines.index(body[0]) + 1:]
    while rest and not rest[0].strip():
        rest.pop(0)
    while rest and not rest[-1].strip():
        rest.pop()
    if not rest:
        return None
    if (len(rest) < 2 or not all(_TABLE_ROW.match(ln) for ln in rest)
            or not _DELIMITER_ROW.match(rest[1]) or _cells(rest[0]) != _cells(rest[1])):
        return why
    return None


def story_problem(markdown: str) -> Optional[str]:
    """Why this story file cannot be trusted by the plan check, or None."""
    if _SEPARATORS.search(markdown):
        return ("a Unicode line or page separator character (form feed, U+2028 and similar); "
                "use ordinary line breaks")
    preamble: List[str] = []
    for raw, region, in_fence in _regions(markdown):
        line = raw.rstrip("\r\n")
        problem = _line_problem(line, region, in_fence)
        if problem:
            return problem
        if region == "preamble":
            preamble.append(line)
    return _preamble_problem(preamble) or _definition_lines(markdown)
