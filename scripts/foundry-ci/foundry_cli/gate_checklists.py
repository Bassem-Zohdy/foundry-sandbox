"""The G1 and G2 checklists (spec 5.3, 5.5) as data. IDs are stable; text may be refined."""
from pathlib import Path
from typing import Dict, List, Tuple

from .docs_ids import ARCHITECTURE_HEADINGS, _live_lines, is_plain_file_in

PRD_VALIDATION = "docs/foundry/gates/prd-validation.md"
ARCHITECTURE = "docs/architecture.md"

_HEADINGS = ", ".join(h.lstrip("# ") for h in ARCHITECTURE_HEADINGS)

CHECKLISTS: Dict[str, List[Tuple[str, str]]] = {
    "G1": [
        ("G1-01", "BRD goals are measurable: each BG has a measure and a target"),
        ("G1-02", "Every story traces to a goal (trace --gate G1)"),
        ("G1-03", "Scope and out of scope are stated (BRD '## Scope and out of scope', "
                  "PRD '## Out of scope')"),
        ("G1-04", "PRD validation was run (docs/foundry/gates/prd-validation.md exists)"),
    ],
    "G2": [
        ("G2-01", "Every FR traces to a story"),
        ("G2-02", "Every module in foundry.json has a bounded context with a boundary, "
                  "glossary entries and layer rules"),
        ("G2-03", f"The architecture has all eleven headings ({_HEADINGS}), with an ADR "
                  "for each choice between real alternatives"),
        ("G2-04", "Test cases cover every FR"),
        ("G2-05", "A threat model is written"),
        ("G2-06", "trace --gate G2 passes"),
    ],
}


def decided_evidence(gate: str, repo: Path) -> Dict[str, str]:
    """The Evidence cells `gate prepare` decides from the files (R29); gate-check fills the rest.

    G1-04 names the PRD validation report or says it is missing. G2-03 cites the
    published docs/architecture.md line of each heading (outside code fences),
    or `missing: <heading>`, in the order of ARCHITECTURE_HEADINGS.
    """
    if gate == "G1":
        present = is_plain_file_in(repo, Path(repo) / PRD_VALIDATION)
        return {"G1-04": PRD_VALIDATION if present else
                f"missing: {PRD_VALIDATION} (run the validate intent of foundry:prd-writer)"}
    found: Dict[str, int] = {}
    file = Path(repo) / ARCHITECTURE
    if is_plain_file_in(repo, file):
        for n, line, _ in _live_lines(file.read_text(encoding="utf-8")):
            if line.rstrip() in ARCHITECTURE_HEADINGS:
                found.setdefault(line.rstrip(), n)
    return {"G2-03": "; ".join(f"{ARCHITECTURE}:{found[h]}" if h in found else f"missing: {h}"
                               for h in ARCHITECTURE_HEADINGS)}
