"""Pack helpers: check pinned dependencies (`pack check`) and copy scaffolds (`pack scaffold`)."""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from . import pack_deps_python
from .cli import register
from .config import Config, ConfigError, load_config
from .pack_layout import pack_root
from .packs import Component, Pack, enabled_components, load_pack
from .template import Change, _classify, _SKIP_DIRS, _SKIP_NAMES, write_files

NO_PACKAGE_JSON = "package.json not found"
RUNTIME, DEV = "dependencies", "devDependencies"
_ROOT = r"(?:, root (\S+))?"
_NPM_RE = re.compile(r"missing (\S+@\S+) \(\S+, (dependencies|devDependencies)" + _ROOT + r"\)"
                     r"|misplaced (\S+@\S+): .*?\(\S+" + _ROOT + r"\)$")
_PY_RE = re.compile(r"missing (\S+==\S+) \(\S+, dependencies" + _ROOT + r"\)")
NPM_FLAGS = {RUNTIME: "--save-prod --save-exact", DEV: "--save-dev --save-exact"}


def is_warning(line: str) -> bool:
    """A `version ...` or `warning ...` line does not fail the check."""
    return line.startswith((pack_deps_python.WARN_VERSION, pack_deps_python.WARN_NOTE))


def _read_deps(repo: Path, root: str = ".") -> "Dict[str, Dict[str, str]] | str":
    """package.json `dependencies` and `devDependencies` by section, or a problem line."""
    path = Path(repo) / root / "package.json"
    if not path.is_file():
        return NO_PACKAGE_JSON if root == "." else f"{root}/package.json not found"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "package.json is not valid JSON"
    if not isinstance(data, dict):
        return "package.json is not valid JSON"
    out: Dict[str, Dict[str, str]] = {}
    for key in (RUNTIME, DEV):
        section = data.get(key)
        out[key] = ({k: v for k, v in section.items() if isinstance(v, str)}
                    if isinstance(section, dict) else {})
    return out


def _wanted(config: Config, ecosystem: str = "npm",
            root: "str | None" = None) -> Dict[str, Tuple[str, str, str]]:
    """Package -> (version, section, owner) for the packs of one ecosystem (and root, when
    given). A runtime need wins over a dev one; Python checks runtime packages only."""
    wanted: Dict[str, Tuple[str, str, str]] = {}
    for pack, comp in enabled_components(config):
        if pack.ecosystem != ecosystem or (
                root is not None and pack_root(config, pack.name) != root):
            continue
        owner = f"{pack.name}/{comp.name}"
        tables = ((DEV, pack.dev_dependencies),) if ecosystem == "npm" else ()
        for section, table in (*tables, (RUNTIME, pack.dependencies)):
            for pkg, ver in sorted(table.get(comp.name, {}).items()):
                if section == RUNTIME or wanted.get(pkg, ("", DEV))[1] == DEV:
                    wanted[pkg] = (ver, section, owner)
    return dict(sorted(wanted.items()))


def _roots(config: Config) -> List[Tuple[str, str]]:
    """(ecosystem, root) of every enabled pack, in first-seen order."""
    seen: List[Tuple[str, str]] = []
    for pack, _ in enabled_components(config):
        key = (pack.ecosystem, pack_root(config, pack.name))
        if key not in seen:
            seen.append(key)
    return seen


def _check_npm(wanted: Dict[str, Tuple[str, str, str]], repo: Path, root: str) -> List[str]:
    found = _read_deps(repo, root)
    if isinstance(found, str):
        return [found]
    suffix = "" if root == "." else f", root {root}"
    lines: List[str] = []
    for pkg, (ver, section, owner) in wanted.items():
        have = found[RUNTIME].get(pkg, found[DEV].get(pkg))
        if have is None:
            lines.append(f"missing {pkg}@{ver} ({owner}, {section}{suffix})")
        elif section == RUNTIME and pkg not in found[RUNTIME]:
            lines.append(f"misplaced {pkg}@{ver}: in devDependencies, must be in "
                         f"dependencies ({owner}{suffix})")
        elif have != ver:
            lines.append(f"version {pkg}: {have} (pack pins {ver})")
    return lines


def check_dependencies(config: Config, repo: Path) -> List[str]:
    """One line per problem; a `version ...` or `warning ...` line is only a warning.

    Each pack's manifests are read at its root (`packRoots`). An npm runtime package declared
    only in devDependencies is a problem: `npm prune --omit=dev` in the image build would
    remove it. Python checks `requirements*.txt` and `pyproject.toml` (PEP 503 names).
    """
    lines: List[str] = []
    for ecosystem, root in _roots(config):
        wanted = _wanted(config, ecosystem, root)
        if not wanted:
            continue
        if ecosystem == "python":
            lines += pack_deps_python.check(wanted, repo, root)
        else:
            lines += _check_npm(wanted, repo, root)
    return lines


def uv_roots(config: Config, repo: Path) -> Set[str]:
    """Python pack roots that have a uv.lock (their install hint is `uv add`)."""
    return {root for eco, root in _roots(config)
            if eco == "python" and (Path(repo) / root / "uv.lock").is_file()}


def _npm_command(section: str, root: str, specs: List[str]) -> str:
    prefix = "" if root == "." else f" --prefix {root}"
    return f"npm install{prefix} {NPM_FLAGS[section]} {' '.join(specs)}"


def _python_command(root: str, specs: List[str], uv: bool) -> str:
    if uv:
        where = "" if root == "." else f" --directory {root}"
        return f"uv add{where} {' '.join(specs)}"
    target = "requirements.txt" if root == "." else f"{root}/requirements.txt"
    return f"add to {target}: {' '.join(specs)}"


def install_commands(lines: List[str], uv: "Set[str] | None" = None) -> List[str]:
    """One command per section and root that has something to add or move.

    `uv` is the set of Python roots with a uv.lock (`uv_roots`).
    """
    npm: Dict[Tuple[str, str], List[str]] = {}
    py: Dict[str, List[str]] = {}
    for line in lines:
        m = _PY_RE.match(line)
        if m:
            py.setdefault(m.group(2) or ".", []).append(m.group(1))
            continue
        m = _NPM_RE.match(line)
        if m and m.group(1):
            npm.setdefault((m.group(2), m.group(3) or "."), []).append(m.group(1))
        elif m:
            npm.setdefault((RUNTIME, m.group(5) or "."), []).append(m.group(4))
    out = [_npm_command(s, r, npm[(s, r)]) for s in (RUNTIME, DEV)
           for r in sorted({r for (sec, r) in npm if sec == s})]
    return out + [_python_command(r, py[r], r in (uv or set())) for r in sorted(py)]


def install_command(lines: List[str], uv: "Set[str] | None" = None) -> str:
    """All install commands on one line (doctor's fix text)."""
    return " && ".join(install_commands(lines, uv))


def _problems(e: ConfigError) -> int:
    for problem in e.problems:
        print(problem, file=sys.stderr)
    return 2


def _run_check(ns: argparse.Namespace) -> int:
    try:
        config = load_config(Path(ns.repo))
        lines = check_dependencies(config, Path(ns.repo))
    except ConfigError as e:
        return _problems(e)
    for line in lines:
        print(line)
    if not all(map(is_warning, lines)):
        for command in install_commands(lines, uv_roots(config, Path(ns.repo))):
            print("install: " + command)
        return 1
    return 0


def _dest_problem(dest: str) -> str:
    """Why `dest` cannot be used, or an empty string when it is fine."""
    if not dest.strip("/\\."):
        return "is empty"
    if dest.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", dest):
        return "is absolute"
    if ".." in re.split(r"[/\\]", dest):
        return "contains '..'"
    return ""


def _scaffold_files(source: Path, dest: str) -> Dict[Path, "tuple[bytes, int]"]:
    out = {}
    for root, dirs, names in os.walk(source):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for name in sorted(n for n in names if n not in _SKIP_NAMES):
            full = Path(root) / name
            out[Path(dest) / full.relative_to(source)] = (
                full.read_bytes(), full.stat().st_mode & 0o777)
    return out


def scaffold_plan(pack: Pack, component: Component, dest: str, repo: Path) -> List[Change]:
    """Changes for copying the component's scaffold into `<repo>/<dest>`."""
    if component.scaffold_dir is None:
        raise ConfigError([f"{pack.name}/{component.name} has no scaffold"])
    why = _dest_problem(dest)
    if why:
        raise ConfigError([f"--to {dest!r} {why}: use a repo-relative path without '..'"])
    files = _scaffold_files(component.scaffold_dir, dest)
    return [Change(rel, _classify(Path(repo), rel, data, mode))
            for rel, (data, mode) in sorted(files.items())]


def _resolve_default(default: Optional[str], config, root: str = ".") -> Optional[str]:
    """The scaffold default under the pack root, with `{python.package}` filled in."""
    if default is not None and "{python.package}" in default:
        if not config.python_package:
            raise ConfigError(["python.package is not set in foundry.json"])
        default = default.replace("{python.package}", config.python_package)
    if default is None or root in ("", "."):
        return default
    return f"{root.rstrip('/')}/{default}"


def _configure_scaffold(p: argparse.ArgumentParser) -> None:
    p.add_argument("pack", help="pack name")
    p.add_argument("component", help="component name")
    p.add_argument("--to", default=None, help="repo-relative directory (default: the pack's, under its root)")
    p.add_argument("--repo", default=".", help="repo root containing foundry.json")
    p.add_argument("--apply", action="store_true", help="write files (default is a dry run)")


def _run_scaffold(ns: argparse.Namespace) -> int:
    repo = Path(ns.repo)
    try:
        config = load_config(repo)
        pack = load_pack(ns.pack)
        comp = next((c for c in pack.components if c.name == ns.component), None)
        if comp is None:
            raise ConfigError([f"unknown component '{ns.component}' in pack '{ns.pack}'"])
        if (pack.name, comp.name) not in [(p.name, c.name)
                                          for p, c in enabled_components(config)]:
            raise ConfigError([f"{ns.pack}/{ns.component} is not enabled by foundry.json"])
        dest = ns.to if ns.to is not None else _resolve_default(
            comp.scaffold_default, config, pack_root(config, pack.name))
        changes = scaffold_plan(pack, comp, dest or "", repo)
        files = _scaffold_files(comp.scaffold_dir, dest or "")
    except ConfigError as e:
        return _problems(e)
    for c in changes:
        print(f"{c.action} {c.path.as_posix()}")
    if not ns.apply:
        print("dry run: nothing written (use --apply)")
        return 0
    try:
        write_files(repo, [(c.path, *files[c.path]) for c in changes if c.action == "create"])
    except OSError as e:
        print(f"foundry: {e}", file=sys.stderr)
        return 1
    return 0


def register_commands() -> None:
    register("pack check", _run_check, lambda p: p.add_argument(
        "--repo", default=".", help="repo root containing foundry.json and package.json"))
    register("pack scaffold", _run_scaffold, _configure_scaffold)
