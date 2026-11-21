"""One grammar for the IDs in the BRD, PRD, SRS and test plan.

An ID is defined by the first cell of a table row in its owning document, under
the owning `## ` section, when the row's second cell has text. A row with an
empty second cell is a placeholder ("not written yet"). Everywhere else an ID is
only a reference. Fenced blocks, inline code spans and blockquotes are ignored.
Text is kept exactly as written (Arabic and right-to-left marks included).
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

KIND_PATTERNS: Dict[str, str] = {
    "BG": r"BG-\d{2}",
    "ST": r"ST-\d{3}",
    "FR": r"FR-[A-Z][A-Z0-9]*-\d{3}",
    "NFR": r"NFR-\d{3}",
    "TC": r"TC-\d{3}",
}

# kind -> (owning document, owning section heading)
OWNERS: Dict[str, Tuple[str, str]] = {
    "BG": ("docs/brd.md", "## Business goals"),
    "ST": ("docs/prd.md", "## Stories"),
    "FR": ("docs/srs.md", "## Functional requirements"),
    "NFR": ("docs/srs.md", "## Non-functional requirements"),
    "TC": ("docs/test-plan.md", "## Test cases"),
}

# The documents scanned for definitions and for references.
DOCUMENTS = ("docs/brd.md", "docs/prd.md", "docs/srs.md", "docs/test-plan.md",
             "docs/architecture.md")

# Gate G2 checks these by exact text; the architecture-writer skill lists the same.
ARCHITECTURE_HEADINGS: Tuple[str, ...] = (
    "## Bounded contexts", "## Context map", "## Layers", "## Identity model",
    "## Trust boundaries", "## CORS", "## Rate limiting", "## Security headers",
    "## Threat model", "## Delivery and edge", "## Runtime contract",
)

# BG and NFR rows link to nothing.
_LINKING = ("ST", "FR", "TC")

_ANY_ID = re.compile(
    r"(?<![A-Za-z0-9-])(?:%s)(?![A-Za-z0-9-])" % "|".join(KIND_PATTERNS.values()))
_FULL = {k: re.compile(p) for k, p in KIND_PATTERNS.items()}
_CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)*?\1")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# Bidi format characters editors put around ASCII tokens such as an ID.
_BIDI = "\u200e\u200f\u061c\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
_SEPARATOR = re.compile(r"^:?-+:?$")


@dataclass
class Item:
    id: str
    kind: str
    text: str
    links: List[str]
    path: str
    line: int


@dataclass
class Docs:
    items: Dict[str, Item] = field(default_factory=dict)
    placeholders: List[Item] = field(default_factory=list)
    duplicates: List[Tuple[Item, Item]] = field(default_factory=list)
    references: List[Tuple[str, str, int]] = field(default_factory=list)


def kind_of(item_id: str) -> Optional[str]:
    """The kind an ID belongs to, or None when it is not a well-formed ID."""
    for kind, pattern in KIND_PATTERNS.items():
        if re.fullmatch(pattern, item_id):
            return kind
    return None


def module_code(name: str) -> str:
    """The FR middle part a module name maps to: upper case, only A-Z and 0-9 kept."""
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def _blank_code(line: str) -> str:
    return _CODE_SPAN.sub(lambda m: " " * len(m.group(0)), line)


def _split_cells(line: str) -> List[str]:
    """Cells of a table row, split on unescaped pipes; `\\|` stays as written."""
    body = line.strip().lstrip(_BIDI + " \t")
    if body.startswith("|"):
        body = body[1:]
    cells, cur, i = [], [], 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body) and body[i + 1] == "|":
            cur.append("\\|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    if "".join(cur).strip():
        cells.append("".join(cur))
    return [c.strip() for c in cells]


def _ids_in(text: str) -> List[str]:
    return _ANY_ID.findall(_blank_code(text))


def _live_lines(text: str):
    """(line number, line, section) for lines outside fences and blockquotes."""
    fence = None
    section = None
    for n, line in enumerate(text.split("\n"), 1):
        line = line.rstrip("\r")
        m = _FENCE.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) \
                    and not line.strip()[len(m.group(1)):].strip():
                fence = None
            continue
        if m:
            fence = m.group(1)
            continue
        if line.lstrip(_BIDI + " \t").startswith(">"):
            continue
        if line.startswith("# "):
            section = None
        elif line.startswith("## "):
            section = line.rstrip()
        yield n, line, section


def _row_item(path: str, n: int, line: str, section: Optional[str]):
    """The (item, is_placeholder) a defining-position table row yields, or None."""
    if not line.lstrip(_BIDI + " \t").startswith("|"):
        return None
    cells = _split_cells(line)
    if not cells or all(_SEPARATOR.match(c) for c in cells if c):
        return None
    first = cells[0].strip(_BIDI + " \t")
    for kind, (doc, heading) in OWNERS.items():
        if doc != path or heading != section or not _FULL[kind].fullmatch(first):
            continue
        text = cells[1] if len(cells) > 1 else ""
        links = _ids_in(cells[2]) if kind in _LINKING and len(cells) > 2 else []
        return Item(first, kind, text, links, path, n), not text
    return None


def is_plain_file_in(repo: Path, file: Path) -> bool:
    """A regular file that is not a symlink and really lives under `repo`.

    A PR can point a document or test at any file on the runner, or at
    /dev/zero, so links and anything resolving outside the repo are skipped.
    """
    if file.is_symlink() or not file.is_file():
        return False
    try:
        file.resolve().relative_to(Path(repo).resolve())
    except ValueError:
        return False
    return True


def parse_docs(repo: Path) -> Docs:
    docs = Docs()
    for path in DOCUMENTS:
        file = Path(repo) / path
        if not is_plain_file_in(repo, file):
            continue
        text = file.read_text(encoding="utf-8")
        for n, line, section in _live_lines(text):
            row = _row_item(path, n, line, section)
            mention = _blank_code(line)
            if row:
                item, placeholder = row
                if placeholder:
                    docs.placeholders.append(item)
                elif item.id in docs.items:
                    docs.duplicates.append((docs.items[item.id], item))
                else:
                    docs.items[item.id] = item
                # the defining cell is not a reference to itself
                mention = mention.replace(item.id, " " * len(item.id), 1)
            for found in _ANY_ID.findall(mention):
                docs.references.append((found, path, n))
    return docs
