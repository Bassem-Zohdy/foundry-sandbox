"""Markdown for `foundry quality report`: totals per tool, top files, top rules, advisory counts."""
from typing import Dict, List, Optional

Counts = Dict[str, Dict[str, int]]


def _cell(text: str) -> str:
    """A Markdown table cell: a `|` or a line break in a path or rule would split the row."""
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _sum(counts: Dict[str, int]) -> int:
    return sum(counts.values())


def _totals(current: Optional[Counts], baseline: Counts) -> List[str]:
    lines = ["| Tool | Baseline | Current | Difference |", "|---|---|---|---|"]
    for tool in sorted(set(baseline) | set(current or {})):
        was = _sum(baseline.get(tool, {}))
        if current is None:
            lines.append(f"| {_cell(tool)} | {was} | - | - |")
        else:
            now = _sum(current.get(tool, {}))
            lines.append(f"| {_cell(tool)} | {was} | {now} | {now - was:+d} |")
    return lines


def _split(key: str) -> "tuple[str, str]":
    uri, _, rule = key.rpartition("::")
    return uri, rule


def _by_file(counts: Counts) -> Dict[str, Dict[str, int]]:
    files: Dict[str, Dict[str, int]] = {}
    for tool, per_key in counts.items():
        for key, n in per_key.items():
            uri, rule = _split(key)
            rules = files.setdefault(uri, {})
            rules[f"{tool} {rule}"] = rules.get(f"{tool} {rule}", 0) + n
    return files


def _by_rule(counts: Counts) -> Dict[str, int]:
    rules: Dict[str, int] = {}
    for tool, per_key in counts.items():
        for key, n in per_key.items():
            name = f"{tool} {_split(key)[1]}"
            rules[name] = rules.get(name, 0) + n
    return rules


def _ranked(totals: Dict[str, int], top: int) -> List["tuple[str, int]"]:
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))[:top]


def render(current: Optional[Counts], baseline: Counts, advisory: Optional[Dict[str, int]],
           top: int) -> str:
    """Report on `current` when SARIF was given, else on the committed baselines alone."""
    shown = baseline if current is None else current
    out = ["# Quality report", ""]
    if current is None:
        out += ["_No SARIF given: counts are from the committed baselines._", ""]
    out += ["## Totals per tool", ""] + _totals(current, baseline)
    files = _by_file(shown)
    totals = {uri: _sum(rules) for uri, rules in files.items()}
    out += ["", f"## Top {top} files by counted violations", "", "| File | Violations | Rules |",
            "|---|---|---|"]
    for uri, n in _ranked(totals, top):
        rules = ", ".join(f"{r} ({c})" for r, c in _ranked(files[uri], len(files[uri])))
        out.append(f"| {_cell(uri)} | {n} | {_cell(rules)} |")
    out += ["", f"## Top {top} rules", "", "| Rule | Violations |", "|---|---|"]
    out += [f"| {_cell(r)} | {n} |" for r, n in _ranked(_by_rule(shown), top)]
    out += ["", "## Advisory (warnings and notes, not counted)", ""]
    if advisory is None:
        out.append("_Not available: no SARIF given._")
    else:
        out += [f"- {t}: {advisory[t]} not counted" for t in sorted(advisory) if advisory[t]]
        if not any(advisory.values()):
            out.append("_None._")
    return "\n".join(out) + "\n"
