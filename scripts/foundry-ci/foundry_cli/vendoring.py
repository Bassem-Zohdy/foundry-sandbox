"""Copy records and `foundry upstream verify`.

Every file copied from a pinned upstream release has a record in
`upstream/sources.json` `files`. Verify proves each copy equals the pinned
upstream file plus its patch.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional

from .cli import register
from .config import ConfigError

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
_REQUIRED = ("source", "release", "upstream_path", "our_path")
_OPTIONAL = ("patch", "note")


@dataclass
class CopyRecord:
    source: str
    release: str
    upstream_path: str
    our_path: str
    patch: Optional[str] = None
    note: Optional[str] = None


def _unsafe(path: str) -> bool:
    p = PurePosixPath(path)
    return p.is_absolute() or ".." in p.parts or "\\" in path


def _field(raw: dict, key: str, prefix: str, required: bool,
           problems: List[str]) -> Optional[str]:
    v = raw.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v:
        problems.append(f"{prefix}.{key}: required non-empty string"
                        if required else f"{prefix}.{key}: must be a non-empty string")
        return None
    return v


def _check_record(raw: object, i: int, pinned: Dict[str, str], seen: set,
                  root: Path, problems: List[str]) -> Optional[CopyRecord]:
    prefix = f"files[{i}]"
    if not isinstance(raw, dict):
        problems.append(f"{prefix}: must be an object")
        return None
    before = len(problems)
    vals = {k: _field(raw, k, prefix, True, problems) for k in _REQUIRED}
    vals.update({k: _field(raw, k, prefix, False, problems) for k in _OPTIONAL})
    source, release = vals["source"], vals["release"]
    if source is not None and source not in pinned:
        problems.append(f"{prefix}.source: unknown source {source!r}")
    elif source is not None and release is not None and release != pinned[source]:
        problems.append(f"{prefix}.release: {release!r} is not the pinned "
                        f"release {pinned[source]} of {source}")
    for key in ("upstream_path", "our_path", "patch"):
        if vals[key] is not None and _unsafe(vals[key]):
            problems.append(f"{prefix}.{key}: must be a relative path inside the "
                            f"repo, got {vals[key]!r}")
            vals[key] = None
    ours = vals["our_path"]
    if ours is not None:
        if ours in seen:
            problems.append(f"{prefix}.our_path: duplicate {ours!r}")
        seen.add(ours)
    patch = vals["patch"]
    if patch is not None and not (root / patch).is_file():
        problems.append(f"{prefix}.patch: file not found {patch!r}")
    if len(problems) > before:
        return None
    return CopyRecord(**vals)  # type: ignore[arg-type]


def load_records(foundry_root: Path) -> List[CopyRecord]:
    """Validate `files` in upstream/sources.json; raise ConfigError per problem."""
    path = Path(foundry_root) / "upstream" / "sources.json"
    try:
        data = json.loads(path.read_text())
        pinned = {s["name"]: s["pinned"] for s in data["sources"]}
        files = data.get("files", [])
        if not isinstance(files, list):
            raise TypeError("files must be a list")
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ConfigError([f"cannot read {path}: {type(e).__name__}: {e}"])
    problems: List[str] = []
    seen: set = set()
    records = [_check_record(raw, i, pinned, seen, Path(foundry_root), problems)
               for i, raw in enumerate(files)]
    if problems:
        raise ConfigError(problems)
    return [r for r in records if r is not None]


def _apply_patch(content: bytes, rec: CopyRecord, patch: Path) -> Optional[bytes]:
    """Apply `patch` to `content` laid out at the upstream path; None on failure."""
    with tempfile.TemporaryDirectory() as tmp_work, tempfile.TemporaryDirectory() as cwd:
        work = str(Path(tmp_work).resolve())  # git refuses paths through symlinks
        target = Path(work) / rec.upstream_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        try:
            p = subprocess.run(
                ["git", "apply", "--unsafe-paths", "--whitespace=nowarn",
                 f"--directory={work}", str(patch.resolve())],
                cwd=cwd, capture_output=True)
        except OSError:
            return None
        if p.returncode != 0:
            return None
        return target.read_bytes()


def verify_copy(record: CopyRecord, checkout: Path, foundry_root: Path) -> Optional[str]:
    """None if upstream file (+ patch) is byte-identical to ours, else a reason."""
    what = f"{record.source}@{record.release} {record.upstream_path}"
    upstream = Path(checkout) / record.upstream_path
    if not upstream.is_file():
        return f"{record.our_path}: {what} not in the checkout"
    ours = Path(foundry_root) / record.our_path
    if not ours.is_file():
        return f"{record.our_path}: our file is missing"
    expected = upstream.read_bytes()
    if record.patch:
        expected = _apply_patch(expected, record, Path(foundry_root) / record.patch)
        if expected is None:
            return f"{record.our_path}: patch does not apply to {what}"
    if expected != ours.read_bytes():
        return f"{record.our_path}: differs from {what} plus patch"
    return None


def _source_names(foundry_root: Path) -> List[str]:
    path = foundry_root / "upstream" / "sources.json"
    try:
        return [s["name"] for s in json.loads(path.read_text())["sources"]]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ConfigError([f"cannot read {path}: {type(e).__name__}: {e}"])


def _parse_checkouts(items: List[str]) -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    for item in items:
        name, sep, directory = item.partition("=")
        if not sep or not name or not directory:
            raise ConfigError([f"--checkout must be <source>=<dir>, got {item!r}"])
        if not Path(directory).is_dir():
            raise ConfigError([f"--checkout {name}: not a directory {directory!r}"])
        out[name] = Path(directory)
    return out


def _configure(p: argparse.ArgumentParser) -> None:
    p.add_argument("--checkout", action="append", default=[], metavar="SOURCE=DIR",
                   help="pinned checkout of a source (repeatable)")
    p.add_argument("--require-all", action="store_true",
                   help="exit 1 when any record is skipped for lack of a checkout")
    p.add_argument("--repo", default=str(DEFAULT_ROOT), help="Foundry root")


def _run_verify(ns: argparse.Namespace) -> int:
    try:
        checkouts = _parse_checkouts(ns.checkout)
        records = load_records(Path(ns.repo))
        known = _source_names(Path(ns.repo))
        for name in checkouts:
            if name not in known:
                raise ConfigError([f"--checkout {name}: unknown source "
                                   f"(known: {', '.join(sorted(known))})"])
    except ConfigError as e:
        for problem in e.problems:
            print(f"foundry: {problem}", file=sys.stderr)
        return 2
    failed = 0
    for rec in records:
        if rec.source not in checkouts:
            print(f"skip {rec.our_path} (no checkout for {rec.source})")
            if ns.require_all:
                failed += 1
                print(f"{rec.our_path}: no checkout for {rec.source} (--require-all)",
                      file=sys.stderr)
            continue
        reason = verify_copy(rec, checkouts[rec.source], Path(ns.repo))
        if reason:
            failed += 1
            print(reason, file=sys.stderr)
        else:
            print(f"ok {rec.our_path}")
    return 1 if failed else 0


def register_commands() -> None:
    register("upstream verify", _run_verify, _configure)
