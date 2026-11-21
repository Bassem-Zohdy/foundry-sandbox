"""The python pack's dependency audit helpers (standard library only, Python 3.10+).

audit.sh runs it:

    python3 pip_audit_report.py uv-lock <uv.lock> <out.txt>   the registry packages, as name==version
    python3 pip_audit_report.py check <pip-audit.json>        the verdict on pip-audit's JSON report

`check` fails (exit 1) on every known vulnerability that has a fixed release, unless its ID or
one of its aliases is in PIP_AUDIT_IGNORE (spaces or commas). A vulnerability with no fixed
release, and a package pip-audit could not audit, are GitHub warnings: nobody can act on them yet.
Exit 2: the input cannot be read.
"""
import json
import os
import re
import sys


def ignored_ids() -> set:
    return {i for i in re.split(r"[\s,]+", os.environ.get("PIP_AUDIT_IGNORE", "")) if i}


def uv_lock(lock_path: str, out_path: str) -> int:
    """uv.lock's registry packages as exact pins; pip-audit then audits them without resolving."""
    try:
        import tomllib
    except ImportError:
        print("pip_audit_report: reading uv.lock needs Python 3.11 or newer", file=sys.stderr)
        return 2
    try:
        with open(lock_path, "rb") as f:
            lock = tomllib.load(f)
    except (OSError, ValueError) as e:
        print(f"pip_audit_report: cannot read {lock_path}: {e}", file=sys.stderr)
        return 2
    pins = []
    for pkg in lock.get("package") or []:
        source = pkg.get("source") or {}
        if "registry" in source and pkg.get("version"):
            pins.append(f"{pkg['name']}=={pkg['version']}")
        elif not ({"editable", "virtual"} & set(source)):  # the project itself is not audited
            kind = ", ".join(source) or "no source"
            print(f"::warning::uv.lock: {pkg.get('name')} comes from {kind}, not a registry; "
                  "it is not audited")
    if not pins:
        print(f"::error::{lock_path} lists no package from a registry; there is nothing to audit")
        return 1
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(pins) + "\n")
    return 0


def _verdicts(report: dict, ignore: set):
    """(failures, notes): one GitHub annotation line each."""
    failures, notes = [], []
    for dep in report.get("dependencies") or []:
        name, version = dep.get("name"), dep.get("version")
        if dep.get("skip_reason"):
            notes.append(f"::warning::{name}: not audited ({dep['skip_reason']})")
        for vuln in dep.get("vulns") or []:
            ids = {vuln.get("id")} | set(vuln.get("aliases") or [])
            fixes = ", ".join(vuln.get("fix_versions") or [])
            if ids & ignore:
                notes.append(f"{name} {version}: {vuln.get('id')} ignored (PIP_AUDIT_IGNORE)")
            elif fixes:
                failures.append(f"::error::{name} {version}: {vuln.get('id')} is fixed in "
                                f"{fixes}; upgrade {name}")
            else:
                notes.append(f"::warning::{name} {version}: {vuln.get('id')} has no fixed release yet")
    return failures, notes


def check(report_path: str) -> int:
    try:
        with open(report_path, encoding="utf-8") as f:
            report = json.load(f)
        if not isinstance(report, dict):
            raise ValueError("top level is not an object")
    except (OSError, ValueError) as e:
        print(f"pip_audit_report: cannot read {report_path}: {e}", file=sys.stderr)
        return 2
    failures, notes = _verdicts(report, ignored_ids())
    for line in notes + failures:
        print(line)
    count = len(report.get("dependencies") or [])
    if failures:
        print(f"pip-audit: {len(failures)} known vulnerabilities with a fix in {count} packages")
        return 1
    print(f"pip-audit: {count} packages, no known vulnerability with a fix")
    return 0


def main(argv: list) -> int:
    if len(argv) == 3 and argv[0] == "uv-lock":
        return uv_lock(argv[1], argv[2])
    if len(argv) == 2 and argv[0] == "check":
        return check(argv[1])
    print("usage: pip_audit_report.py uv-lock <uv.lock> <out> | check <report.json>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
