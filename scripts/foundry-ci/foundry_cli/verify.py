"""`foundry verify`: run the configured checks fresh and write a PASS/FAIL report.

Each step's output goes to `<scratch>/verify/<head7>-<id>.log`; the report is
`<scratch>/verify/<head7>.md`. "It passes" is only ever backed by this run.
"""
import argparse
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .cli import register
from .config import Config, ConfigError, load_config
from .scratch import scratch_dir

__all__ = ["StepResult", "VerifyReport", "VerifyError", "run_verify", "register_commands"]

DEFAULT_TIMEOUT = 1800
TAIL_LINES = 200


class VerifyError(Exception):
    """Usage or config problem (exit 2)."""


@dataclass
class StepResult:
    id: str
    command: str
    exit_code: Optional[int]
    seconds: float
    timed_out: bool
    log: Path

    @property
    def passed(self) -> bool:
        return not self.timed_out and self.exit_code == 0


@dataclass
class VerifyReport:
    head: str
    dirty: bool
    results: List[StepResult] = field(default_factory=list)
    report_path: Optional[Path] = None
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.results) and all(r.passed for r in self.results)


def _git(repo: Path, *args: str) -> str:
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    except OSError as e:
        raise VerifyError(f"cannot run git: {e}")
    if p.returncode != 0:
        raise VerifyError(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def _run_step(item: dict, repo: Path, log: Path) -> StepResult:
    timeout = item.get("timeoutSeconds", DEFAULT_TIMEOUT)
    start = time.monotonic()
    timed_out = False
    code: Optional[int] = None
    with open(log, "wb") as out:
        proc = subprocess.Popen(item["run"], shell=True, cwd=repo, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.wait()
    return StepResult(item["id"], item["run"], code, time.monotonic() - start, timed_out, log)


def _quality_step(repo: Path, config: Config, sarif: List[str], log: Path) -> Optional[StepResult]:
    from . import quality
    if not sarif or not (repo / quality.BASELINE_DIR).is_dir():
        return None
    start = time.monotonic()
    try:
        current: quality.Counts = {}
        for f in sarif:
            for tool, counts in quality.read_sarif(Path(f), config.quality_exclude, repo).items():
                dest = current.setdefault(tool, {})
                for k, n in counts.items():
                    dest[k] = dest.get(k, 0) + n
        problems = quality.compare(current, quality.load_baselines(repo, list(current)))
        text = "\n".join(problems) or "quality ok: no new violations"
        code = 1 if problems else 0
    except quality.QualityError as e:
        text, code = str(e), 1
    log.write_text(text + "\n", encoding="utf-8")
    return StepResult("quality", "foundry quality check", code, time.monotonic() - start,
                      False, log)


def _longest_run(text: str) -> int:
    return max((len(m) for m in re.findall(r"`+", text)), default=0)


def _code_span(text: str) -> str:
    ticks = "`" * (_longest_run(text) + 1)
    return f"{ticks} {text} {ticks}" if len(ticks) > 1 else f"`{text}`"


def _tail(log: Path) -> str:
    text = log.read_bytes().decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-TAIL_LINES:])


def _status(r: StepResult) -> str:
    return "FAIL (timed out)" if r.timed_out else ("PASS" if r.passed else "FAIL")


def _table(report: VerifyReport) -> str:
    rows = ["| Step | Result | Exit | Seconds | Command |", "|---|---|---|---|---|"]
    for r in report.results:
        code = "-" if r.exit_code is None else str(r.exit_code)
        cmd = r.command.replace("|", "\\|").replace("\n", " ")
        rows.append(f"| {r.id} | {_status(r)} | {code} | {r.seconds:.1f} | {_code_span(cmd)} |")
    return "\n".join(rows)


def _render(report: VerifyReport) -> str:
    h7 = report.head[:7]
    lines = [f"# Verify {h7}", "", f"Head: {report.head}",
             f"Overall: {'PASS' if report.ok else 'FAIL'}", ""]
    if report.dirty:
        lines += [f"Working tree has uncommitted changes: results apply to the working tree, "
                  f"not {h7}.", ""]
    lines += [_table(report), ""]
    lines += [n for note in report.notes for n in (note, "")]
    for r in report.results:
        if r.passed:
            continue
        why = "timed out" if r.timed_out else f"exit {r.exit_code}"
        tail = _tail(r.log)
        fence = "`" * max(3, _longest_run(tail) + 1)
        lines += [f"## {r.id} ({why}): last {TAIL_LINES} log lines", "", fence,
                  tail, fence, "", f"Full log: {r.log}", ""]
    return "\n".join(lines)


def run_verify(config: Config, repo: Path, stage: Optional[str],
               sarif: Optional[List[str]] = None) -> VerifyReport:
    """Run the matching verify steps and write the report. Raises VerifyError (exit 2)."""
    repo = Path(repo)
    items = [i for i in config.verify if stage is None or stage in i.get("stages", [])]
    if not items:
        raise VerifyError("no verify entries" + (f" for stage {stage}" if stage else "")
                          + " in foundry.json")
    head = _git(repo, "rev-parse", "HEAD").strip()
    dirty = bool(_git(repo, "status", "--porcelain").strip())
    vdir = scratch_dir(repo) / "verify"
    vdir.mkdir(parents=True, exist_ok=True)
    report = VerifyReport(head, dirty)
    for item in items:
        report.results.append(_run_step(item, repo, vdir / f"{head[:7]}-{item['id']}.log"))
    if sarif and not (repo / "quality" / "baseline").is_dir():
        report.notes.append("quality step skipped: no quality/baseline/ directory "
                            "(run `foundry quality baseline --apply` first)")
    q = _quality_step(repo, config, sarif or [], vdir / f"{head[:7]}-quality.log")
    if q:
        report.results.append(q)
    report.report_path = vdir / f"{head[:7]}.md"
    report.report_path.write_text(_render(report), encoding="utf-8")
    return report


def _configure(p: argparse.ArgumentParser) -> None:
    p.add_argument("--stage", choices=["pre-push", "ci"], help="run only steps for this stage")
    p.add_argument("--sarif", action="append", default=[],
                   help="SARIF file for the quality step (repeatable)")
    p.add_argument("--repo", default=".", help="repo root containing foundry.json")


def _run(ns: argparse.Namespace) -> int:
    try:
        config = load_config(Path(ns.repo))
        report = run_verify(config, Path(ns.repo), ns.stage, ns.sarif)
    except ConfigError as e:
        for problem in e.problems:
            print(problem, file=sys.stderr)
        return 2
    except (VerifyError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 2
    print(_table(report))
    for note in report.notes:
        print(note)
    print(f"report: {report.report_path}")
    return 0 if report.ok else 1


def register_commands() -> None:
    register("verify", _run, _configure)
