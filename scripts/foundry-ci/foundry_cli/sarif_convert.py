"""Convert dependency-cruiser, knip, jscpd and Semgrep JSON, and vulture text, to SARIF 2.1.0.

These tools have no SARIF output of their own (Semgrep has, but not in a shape the baseline can
use: its tool name has spaces and its rule ids carry the config folder). Every converted violation is an
`error` result (the quality engine counts only errors), except a dependency-cruiser
`warn` violation, which becomes a `warning` and so stays advisory.

`--prefix <dir>` is for a tool that ran in a pack's folder: it reports paths relative to that
folder, so every relative result URI gets `<dir>/` in front. Absolute paths and `file://` URIs are
left as they are; the quality engine makes them repo-relative.

vulture prints text, not JSON: `path:line: unused <kind> '<name>' (<n>% confidence)`. Every
result is an `error` (ruling R7): the run's `--min-confidence` decides what is reported, and
vulture scores unused functions, classes and variables at 60%, so a warning band would leave stale
code uncounted. False positives go in the guarded `vulture_whitelist.py`. Its rule ids are `vulture/unused-<kind>`
(a space in the kind becomes `-`), `vulture/unreachable-code` and `vulture/unsatisfiable-condition`.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Tuple

from .cli import register

__all__ = ["ConvertError", "to_sarif", "register_commands"]

KINDS = ("depcruise", "knip", "jscpd", "semgrep", "vulture")
Item = Tuple[str, str, str, str]  # ruleId, level, uri, message


class ConvertError(Exception):
    """The tool JSON does not have the expected shape (exit 2)."""


def _uri(path: Any) -> str:
    if not isinstance(path, str) or not path:
        raise ConvertError("missing file path")
    return path[2:] if path.startswith("./") else path


def _cycle_text(cycle: Any, to: str) -> str:
    names = [c.get("name") if isinstance(c, dict) else c for c in cycle]
    return "-> " + " -> ".join(str(n) for n in names) if names else f"-> {to}"


def _need(value: Any, kind: type, what: str) -> Any:
    if not isinstance(value, kind):
        raise ConvertError(f"expected {what} to be a {kind.__name__}")
    return value


def _depcruise(data: Any) -> Iterator[Item]:
    summary = _need(data.get("summary"), dict, "summary")
    for v in _need(summary.get("violations"), list, "summary.violations"):
        severity = v["rule"]["severity"]
        if severity in ("info", "ignore"):  # documented non-violations
            continue
        if severity not in ("error", "warn"):
            raise ConvertError(f"unknown rule severity {severity!r}")
        to = str(v.get("to", ""))
        text = _cycle_text(v["cycle"], to) if v.get("cycle") else f"-> {to}"
        yield str(v["rule"]["name"]), "error" if severity == "error" else "warning", \
            _uri(v["from"]), text


def _knip_names(kind: str, items: Any) -> Iterator[str]:
    """Names in one issue category: a list of items, or an object of lists (members)."""
    if isinstance(items, dict):
        for owner, members in items.items():
            for name in _knip_names(kind, _need(members, list, f"{kind}.{owner}")):
                yield f"{owner}.{name}"
        return
    for item in _need(items, list, kind):
        if isinstance(item, list):  # duplicates and cycles: one list of symbols per finding
            yield " = ".join(_knip_item(kind, symbol) for symbol in item)
        else:
            yield _knip_item(kind, item)


def _knip_item(kind: str, item: Any) -> str:
    if isinstance(item, dict):
        name = str(item.get("name") or kind)
        return f"{item['namespace']}.{name}" if item.get("namespace") else name
    if isinstance(item, str):
        return item
    raise ConvertError(f"unexpected {kind} item {item!r}")


def _knip(data: Any) -> Iterator[Item]:
    if "files" not in data and "issues" not in data:
        raise ConvertError("not knip output (need files or issues)")
    for f in _need(data.get("files", []), list, "files"):
        yield "knip/files", "error", _uri(f), str(f)
    for issue in _need(data.get("issues", []), list, "issues"):
        uri = _uri(issue["file"])
        for kind, items in issue.items():
            if kind != "file":
                for name in _knip_names(kind, items):
                    yield f"knip/{kind}", "error", uri, name


def _jscpd(data: Any) -> Iterator[Item]:
    for d in _need(data.get("duplicates"), list, "duplicates"):
        a, b = d["firstFile"], d["secondFile"]
        text = (f"duplicates {_uri(b['name'])} lines {b['start']}-{b['end']} "
                f"({_uri(a['name'])} lines {a['start']}-{a['end']})")
        yield "jscpd/duplicate", "error", _uri(a["name"]), text


_SEMGREP_LEVELS = {"ERROR": "error", "WARNING": "warning", "INFO": "note"}
_SEMGREP_PREFIX = "semgrep."  # Semgrep prefixes rule ids with the config folder, `.semgrep`
_FOUNDRY_ID = re.compile(r"(?:^|\.)(foundry\..+)$")


def _semgrep(data: Any) -> Iterator[Item]:
    for r in _need(data.get("results"), list, "results"):
        rule = str(r["check_id"])
        found = _FOUNDRY_ID.search(rule)  # robust to any config folder depth
        if found:
            rule = found.group(1)
        elif rule.startswith(_SEMGREP_PREFIX):
            rule = rule[len(_SEMGREP_PREFIX):]
        severity = r["extra"]["severity"]
        if severity not in _SEMGREP_LEVELS:
            raise ConvertError(f"unknown severity {severity!r}")
        yield rule, _SEMGREP_LEVELS[severity], _uri(r["path"]), str(r["extra"]["message"])


_VULTURE_LINE = re.compile(r"^(.+?):(\d+): (.+) \((\d{1,3})% confidence(?:, \d+ lines?)?\)$")
_VULTURE_BODY = (
    (re.compile(r"^unused ([a-z]+(?: [a-z]+)*) '(.+)'$"), lambda m: f"unused-{m.group(1)}".replace(" ", "-")),
    (re.compile(r"^unreachable code after '(.+)'$"), lambda m: "unreachable-code"),
    (re.compile(r"^unsatisfiable '(.+)' condition$"), lambda m: "unsatisfiable-condition"),
)


def _vulture_item(line: str) -> Item:
    m = _VULTURE_LINE.match(line)
    if not m:
        raise ConvertError(f"malformed vulture line: {line!r}")
    for body, rule in _VULTURE_BODY:
        found = body.match(m.group(3))
        if found:
            text = f"{m.group(3)} ({m.group(4)}% confidence)"
            return f"vulture/{rule(found)}", "error", _uri(m.group(1)), text
    raise ConvertError(f"malformed vulture line: {line!r}")


def _vulture(text: Any) -> Iterator[Item]:
    for line in str(text).splitlines():
        if line.strip():
            yield _vulture_item(line.strip())


_CONVERTERS: Dict[str, Callable[[Any], Iterator[Item]]] = {
    "depcruise": _depcruise, "knip": _knip, "jscpd": _jscpd, "semgrep": _semgrep,
    "vulture": _vulture}


def _prefix(prefix: str) -> str:
    """`dashboard/` for `dashboard`, `` for `.`; absolute or `..` prefixes are refused."""
    clean = prefix.strip().strip("/")
    if prefix.strip().startswith("/") or ".." in clean.split("/"):
        raise ConvertError(f"--prefix {prefix!r} must be a repo-relative folder without '..'")
    parts = [p for p in clean.split("/") if p not in ("", ".")]
    return "/".join(parts) + "/" if parts else ""


def _prefixed(uri: str, prefix: str) -> str:
    if not prefix or uri.startswith("/") or "://" in uri:
        return uri
    return prefix + uri


def to_sarif(kind: str, data: Any, tool_version: str = "", prefix: str = "") -> Dict:
    """Return a SARIF 2.1.0 log with one run for the given tool JSON."""
    lead = _prefix(prefix)
    if kind not in _CONVERTERS:
        raise ConvertError(f"unknown tool {kind!r}; use one of {', '.join(KINDS)}")
    if kind != "vulture" and not isinstance(data, dict):
        raise ConvertError(f"{kind} output must be a JSON object")
    try:
        items = list(_CONVERTERS[kind](data))
    except ConvertError as e:
        if kind == "vulture":
            raise
        raise ConvertError(f"malformed {kind} output ({type(e).__name__}: {e})")
    except (KeyError, TypeError, AttributeError) as e:
        raise ConvertError(f"malformed {kind} output ({type(e).__name__}: {e})")
    driver: Dict[str, Any] = {"name": kind}
    if tool_version:
        driver["version"] = tool_version
    results: List[Dict] = [
        {"ruleId": rule, "level": level, "message": {"text": text},
         "locations": [{"physicalLocation": {"artifactLocation": {"uri": _prefixed(uri, lead)}}}]}
        for rule, level, uri, text in items]
    return {"version": "2.1.0", "runs": [{"tool": {"driver": driver}, "results": results}]}


def _configure(p: argparse.ArgumentParser) -> None:
    p.add_argument("--from", dest="kind", required=True, choices=KINDS, help="tool that wrote the output")
    p.add_argument("--input", required=True, help="the tool's output (JSON; text for vulture)")
    p.add_argument("--output", help="SARIF file to write (default: stdout)")
    p.add_argument("--tool-version", default="", help="recorded in the SARIF driver")
    p.add_argument("--prefix", default="", help="folder the tool ran in; put in front of relative paths")


def _run(ns: argparse.Namespace) -> int:
    try:
        try:
            raw = Path(ns.input).read_text(encoding="utf-8")
            data = raw if ns.kind == "vulture" else json.loads(raw)
        except (OSError, ValueError) as e:
            raise ConvertError(f"{ns.input}: cannot read {ns.kind} JSON: {e}")
        try:
            log = to_sarif(ns.kind, data, ns.tool_version, ns.prefix)
        except ConvertError as e:
            raise ConvertError(f"{ns.input}: {e}")
        text = json.dumps(log, indent=2) + "\n"
        if ns.output:
            Path(ns.output).write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
    except (ConvertError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 2
    return 0


def register_commands() -> None:
    register("quality sarif", _run, _configure)
