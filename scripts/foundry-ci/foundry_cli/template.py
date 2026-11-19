"""Plan and apply the repo template (`foundry template apply`)."""
import argparse
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Literal, NamedTuple, Optional, Tuple

from . import __version__
from .cli import register
from .codeowners import build_codeowners
from .config import Config, ConfigError, load_config
from .pack_layout import guarded_paths, pack_root, placed
from .packs import SHARED, enabled_components, default_packs_dir, lib_template

TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "template"
CODEOWNERS_PATH = Path(".github") / "CODEOWNERS"
DEPENDABOT_PATH = Path(".github") / "dependabot.yml"
LIB_DIR = Path(__file__).resolve().parent
CI_DIR = Path("scripts") / "foundry-ci"
VENDOR_DIR = CI_DIR / "foundry_cli"
# Workflow files are full of ${{ ... }} GitHub expressions, which are not ours.
VERBATIM_DIRS = (Path(".github") / "workflows",)
RUN_PY = """\
#!/usr/bin/env python3
\"\"\"Foundry CI entry point. Vendored by `foundry template apply`; do not edit.\"\"\"
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from foundry_cli import commands  # noqa: E402
from foundry_cli.cli import main  # noqa: E402

commands.register_all()

sys.exit(main(sys.argv[1:]))
"""
_SKIP_NAMES = {".DS_Store"}
_SKIP_DIRS = {"__pycache__"}

Action = Literal["create", "conflict", "skip"]


@dataclass
class Change:
    path: Path  # relative to the target repo
    action: Action


_PLACEHOLDER_RE = re.compile(r"\{\{([^{}]*)\}\}")


def render(text: str, config: Config, extra: Optional[Dict[str, str]] = None) -> str:
    values = {
        "project.name": config.project_name,
        "roles.techLead": config.tech_lead,
        "github.baseBranch": config.base_branch,
        "foundryVersion": config.foundry_version,
        **(extra or {}),
    }

    def sub(m: "re.Match[str]") -> str:
        key = m.group(1).strip()
        if key not in values:
            raise ValueError(f"unknown template placeholder {{{{{key}}}}}")
        return values[key]

    return _PLACEHOLDER_RE.sub(sub, text)


def _extra(config: Config, rel: Path) -> Optional[Dict[str, str]]:
    """Placeholders only one file may use."""
    if rel != DEPENDABOT_PATH:
        return None
    from .dependabot import dependabot_updates  # lazy: dependabot imports packs
    return {"dependabot.updates": dependabot_updates(config)}


def _files(config: Config, source: Path) -> Dict[Path, Tuple[bytes, int]]:
    """Rendered content and file mode for every template file, by relative path."""
    out: Dict[Path, Tuple[bytes, int]] = {}
    for root, dirs, names in os.walk(source):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for name in sorted(names):
            if name in _SKIP_NAMES:
                continue
            full = Path(root) / name
            data = full.read_bytes()
            rel = full.relative_to(source)
            if not any(d in rel.parents for d in VERBATIM_DIRS):
                try:
                    data = render(data.decode("utf-8"), config, _extra(config, rel)).encode("utf-8")
                except UnicodeDecodeError:
                    pass  # not text: copied byte-for-byte
            out[rel] = (data, stat.S_IMODE(full.stat().st_mode))
    if source == TEMPLATE_DIR:
        if CODEOWNERS_PATH in out:
            raise ValueError(f"template must not contain {CODEOWNERS_PATH}: it is generated")
        out[CODEOWNERS_PATH] = (build_codeowners(config).encode("utf-8"), 0o644)
        out[CI_DIR / "guarded.txt"] = (  # what `pack guarded` answers from the vendored copy
            "".join(f"{g}\n" for g in guarded_paths(config)).encode("utf-8"), 0o644)
        for rel, entry in _vendored().items():
            if rel in out:
                raise ValueError(f"template must not contain {rel}: it is generated")
            out[rel] = entry
    return out


def _vendored() -> Dict[Path, Tuple[bytes, int]]:
    """The CLI copied verbatim into the target, plus its entry point and manifest."""
    out: Dict[Path, Tuple[bytes, int]] = {}
    for src in sorted(LIB_DIR.glob("*.py")):
        out[VENDOR_DIR / src.name] = (src.read_bytes(), 0o644)
    manifest = [f"foundry {__version__}"] + [rel.relative_to(CI_DIR).as_posix()
                                             for rel in out]
    out[CI_DIR / "run.py"] = (RUN_PY.encode("utf-8"), 0o755)
    out[CI_DIR / "vendor.txt"] = (("\n".join(manifest) + "\n").encode("utf-8"), 0o644)
    return out


def _blocked(target: Path, rel: Path) -> bool:
    """True when a symlink or a non-directory sits on the way to `rel`."""
    cur = target
    for part in rel.parts[:-1]:
        cur = cur / part
        if os.path.islink(cur) or (os.path.lexists(cur) and not os.path.isdir(cur)):
            return True
    return False


def _classify(target: Path, rel: Path, data: bytes, mode: int) -> Action:
    dest = target / rel
    if _blocked(target, rel):
        return "conflict"
    if not os.path.lexists(dest):
        return "create"
    if os.path.islink(dest) or not os.path.isfile(dest):
        return "conflict"
    same_exec = bool(stat.S_IMODE(dest.stat().st_mode) & stat.S_IXUSR) == bool(mode & stat.S_IXUSR)
    return "skip" if dest.read_bytes() == data and same_exec else "conflict"


class Source(NamedTuple):
    name: str  # "core", "_shared", "_lib/<lib>" or "<pack>/<component>"
    directory: Path  # the template folder
    root: str  # the pack root its files are placed under


def template_sources(config: Config) -> List[Source]:
    """Core, then the shared layer, then per enabled component its libs and itself."""
    out = [Source("core", TEMPLATE_DIR, ".")]
    enabled = enabled_components(config)
    shared = default_packs_dir() / SHARED / "template"
    if config.packs and shared.is_dir():
        out.append(Source(SHARED, shared, "."))
    seen = set()
    for pack, comp in enabled:
        root = pack_root(config, pack.name)
        for lib in comp.uses:
            if (lib, root) not in seen:
                seen.add((lib, root))
                out.append(Source(f"_lib/{lib}", lib_template(default_packs_dir(), lib), root))
        if comp.template_dir is not None:
            out.append(Source(f"{pack.name}/{comp.name}", comp.template_dir, root))
    return out


def _all_files(config: Config, source: Optional[Path]) -> Dict[Path, Tuple[bytes, int]]:
    """Files of one explicit source, or of every source placed and merged.

    Identical entries (same bytes and mode) from two sources are one file; different ones
    are a collision that names both sources.
    """
    if source is not None:
        return _files(config, Path(source))
    merged: Dict[Path, Tuple[bytes, int]] = {}
    owner: Dict[Path, str] = {}
    for src in template_sources(config):
        for rel, entry in _files(config, src.directory).items():
            dest = placed(rel, src.root)
            if dest in merged and merged[dest] != entry:
                raise ValueError(f"{dest.as_posix()}: both {owner[dest]} and {src.name} provide it")
            if dest not in merged:
                merged[dest], owner[dest] = entry, src.name
    return merged


def plan_template(config: Config, target: Path, source: Optional[Path] = None) -> List[Change]:
    target = Path(target)
    files = _all_files(config, source)
    return [Change(rel, _classify(target, rel, data, mode))
            for rel, (data, mode) in sorted(files.items())]


def apply_template(config: Config, target: Path, changes: List[Change],
                   source: Optional[Path] = None) -> None:
    """Write `create` changes only; conflicts and skips are never touched.

    Parents are created first, files are opened O_EXCL|O_NOFOLLOW, and a
    failure reports which files were already written.
    """
    files = _all_files(config, source)
    target = Path(target)
    for change in changes:
        if change.path.is_absolute() or ".." in change.path.parts or change.path not in files:
            raise ValueError(f"refusing unsafe or unknown template path: {change.path}")
    todo = [c for c in changes if c.action == "create"]
    write_files(target, [(c.path, *files[c.path]) for c in todo])


def write_files(target: Path, items: List[Tuple[Path, bytes, int]]) -> None:
    """Create each (relative path, data, mode) exclusively, never through a symlink."""
    for rel, _, _ in items:  # create every parent before writing anything
        if _blocked(target, rel):
            raise OSError(f"{rel}: a symlink or file is in the way")
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
    root = target.resolve()
    written: List[str] = []
    try:
        for rel, data, mode in items:
            dest = target / rel
            if root not in dest.parent.resolve().parents and dest.parent.resolve() != root:
                raise OSError(f"{rel}: resolves outside the target")
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.chmod(dest, mode)
            written.append(rel.as_posix())
    except OSError as e:
        raise OSError(f"{e}; already written: {', '.join(written) or 'nothing'}") from e


def _configure(p: argparse.ArgumentParser) -> None:
    p.add_argument("--target", required=True, help="directory to install the template into")
    p.add_argument("--repo", default=".", help="repo root containing foundry.json")
    p.add_argument("--apply", action="store_true", help="write files (default is a dry run)")


def _run(ns: argparse.Namespace) -> int:
    try:
        config = load_config(Path(ns.repo))
    except ConfigError as e:
        for problem in e.problems:
            print(problem, file=sys.stderr)
        return 2
    target = Path(ns.target)
    if target.exists() and not target.is_dir():
        print(f"foundry: target is not a directory: {target}", file=sys.stderr)
        return 2
    try:
        changes = plan_template(config, target)
    except ConfigError as e:
        for problem in e.problems:
            print(problem, file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"foundry: {e}", file=sys.stderr)
        return 2
    for c in changes:
        print(f"{c.action} {c.path.as_posix()}")
    if ns.apply:
        apply_template(config, target, changes)
    else:
        print("dry run: nothing written (use --apply)")
    return 0


def register_commands() -> None:
    register("template apply", _run, _configure)
