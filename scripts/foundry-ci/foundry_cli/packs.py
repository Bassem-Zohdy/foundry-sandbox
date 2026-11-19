"""Stack packs: load `packs/<name>/pack.json` and decide which components are on.

Tests and the config validator can point at another packs directory with the
FOUNDRY_PACKS_DIR environment variable (see `default_packs_dir`).
"""
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .cli import register
from .config import Config, ConfigError, load_config
from .pack_layout import REPO_LEVEL, guarded_paths, pack_root, placed, placed_guarded  # noqa: F401

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
PACKS_DIR = ROOT_DIR / "packs"
SKILLS_DIR = ROOT_DIR / "skills"
PACK_FILE = "pack.json"

_NAME_RE = re.compile(r"[a-z][a-z0-9-]*")
_SKILL_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?")
_TOP_KEYS = {"name", "description", "components", "dependencies", "devDependencies",
             "guarded", "skills", "ecosystem"}
_COMPONENT_KEYS = {"whenStack", "template", "scaffold", "requires", "uses", "dependabot"}
ECOSYSTEMS = ("npm", "python")
DEPENDABOT_ECOSYSTEMS = ("npm", "pip", "docker")
LIB_DIR_NAME = "_lib"
SHARED = "_shared"
# A guarded path is written to CODEOWNERS as-is: anchored at the repo root, no glob classes,
# negation, whitespace or comment marker.
_GUARDED_RE = re.compile(r"/[A-Za-z0-9._/*-]+")


@dataclass
class Component:
    name: str
    when_stack: Optional[str]
    template_dir: Optional[Path]
    scaffold_dir: Optional[Path]
    scaffold_default: Optional[str]
    requires: Optional[str] = None  # e.g. "Nest 12": shown by `pack show`
    uses: List[str] = field(default_factory=list)  # packs/_lib/<name>/template fragments
    dependabot: List[str] = field(default_factory=list)  # ecosystems for dependabot.yml


@dataclass
class Pack:
    name: str
    description: str
    components: List[Component] = field(default_factory=list)
    dev_dependencies: Dict[str, Dict[str, str]] = field(default_factory=dict)
    skills: List[str] = field(default_factory=list)
    # Runtime packages: they must be in package.json `dependencies`, or an image built with
    # `npm prune --omit=dev` loses them.
    dependencies: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # Files that define the pack's gate; CODEOWNERS gives them to the tech lead.
    guarded: List[str] = field(default_factory=list)
    ecosystem: str = "npm"  # how `pack check` reads the project's dependencies


def default_packs_dir() -> Path:
    """FOUNDRY_PACKS_DIR when set (tests), otherwise the plugin's packs/."""
    env = os.environ.get("FOUNDRY_PACKS_DIR")
    return Path(env) if env else PACKS_DIR


def list_packs(packs_dir: Optional[Path] = None) -> List[str]:
    base = Path(packs_dir) if packs_dir is not None else default_packs_dir()
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir()
                  if not p.name.startswith("_") and (p / PACK_FILE).is_file())


def _repo_relative(path: str) -> bool:
    if path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", path):
        return False
    return ".." not in re.split(r"[/\\]", path)


def _choices(where: str, raw: Any, allowed: Tuple[str, ...], out: List[str]) -> List[str]:
    if not (isinstance(raw, list) and all(isinstance(x, str) for x in raw)):
        out.append(f"{where}: must be a list of strings")
        return []
    bad = [x for x in raw if x not in allowed]
    out.extend(f"{where}: {x!r} is not one of {', '.join(allowed)}" for x in bad)
    return [x for x in raw if x in allowed]


def _uses(where: str, raw: Any, pack_dir: Path, out: List[str]) -> List[str]:
    if not (isinstance(raw, list) and all(isinstance(x, str) for x in raw)):
        out.append(f"{where}: must be a list of strings")
        return []
    for lib in raw:
        if not _NAME_RE.fullmatch(lib) or not lib_template(pack_dir.parent, lib).is_dir():
            out.append(f"{where}: unknown lib '{lib}' (no {LIB_DIR_NAME}/{lib}/template)")
    return list(raw)


def lib_template(base: Path, lib: str) -> Path:
    return Path(base) / LIB_DIR_NAME / lib / "template"


def _component(pack_dir: Path, name: str, raw: Any, out: List[str]) -> Optional[Component]:
    where = f"components.{name}"
    if not _NAME_RE.fullmatch(name):
        out.append(f"{where}: name must match [a-z][a-z0-9-]*")
    if not isinstance(raw, dict):
        out.append(f"{where}: must be an object")
        return None
    for key in raw:
        if key not in _COMPONENT_KEYS:
            out.append(f"{where}.{key}: unknown key")
    when = raw.get("whenStack")
    if when is not None and not (isinstance(when, str) and when):
        out.append(f"{where}.whenStack: must be a string or null")
        when = None
    template: Optional[Path] = None
    if raw.get("template", False) is True:
        template = pack_dir / "template" / name
        if not template.is_dir():
            out.append(f"{where}.template: directory missing: {template}")
    elif raw.get("template", False) is not False:
        out.append(f"{where}.template: must be true or false")
    scaffold, default = raw.get("scaffold"), None
    scaffold_dir: Optional[Path] = None
    if scaffold is not None:
        if not isinstance(scaffold, dict) or not (
                isinstance(scaffold.get("default"), str) and scaffold["default"]):
            out.append(f"{where}.scaffold: must be null or {{\"default\": \"<path>\"}}")
        elif not _repo_relative(scaffold["default"]):
            out.append(f"{where}.scaffold.default: must be a repo-relative path "
                       "without '..' segments")
        else:
            extra = set(scaffold) - {"default"}
            out.extend(f"{where}.scaffold.{k}: unknown key" for k in sorted(extra))
            default = scaffold["default"]
            scaffold_dir = pack_dir / "scaffold" / name
            if not scaffold_dir.is_dir():
                out.append(f"{where}.scaffold: directory missing: {scaffold_dir}")
    requires = raw.get("requires")
    if requires is not None and not (isinstance(requires, str) and requires.strip()):
        out.append(f"{where}.requires: must be a non-empty string")
        requires = None
    uses = _uses(f"{where}.uses", raw.get("uses", []), pack_dir, out)
    dependabot = _choices(f"{where}.dependabot", raw.get("dependabot", []),
                          DEPENDABOT_ECOSYSTEMS, out)
    return Component(name, when, template, scaffold_dir, default, requires, uses, dependabot)


def _deps(section: str, raw: Any, names: List[str],
          out: List[str]) -> Dict[str, Dict[str, str]]:
    result: Dict[str, Dict[str, str]] = {}
    if not isinstance(raw, dict):
        out.append(f"{section}: must be an object")
        return result
    for comp, pkgs in raw.items():
        where = f"{section}.{comp}"
        if comp not in names:
            out.append(f"{where}: unknown component")
        if not isinstance(pkgs, dict):
            out.append(f"{where}: must be an object")
            continue
        for pkg, version in pkgs.items():
            if not (isinstance(version, str) and _VERSION_RE.fullmatch(version)):
                out.append(f"{where}.{pkg}: version must be exact x.y.z, got {version!r}")
        result[comp] = {k: v for k, v in pkgs.items() if isinstance(v, str)}
    return result


def _overlap(runtime: Dict[str, Dict[str, str]], dev: Dict[str, Dict[str, str]],
             out: List[str]) -> None:
    for comp in sorted(set(runtime) & set(dev)):
        for pkg in sorted(set(runtime[comp]) & set(dev[comp])):
            out.append(f"{comp}: {pkg} is in both dependencies and devDependencies")


def _guarded(raw: Any, out: List[str]) -> List[str]:
    if not (isinstance(raw, list) and all(isinstance(g, str) for g in raw)):
        out.append("guarded: must be a list of strings")
        return []
    for g in raw:
        if not _GUARDED_RE.fullmatch(g) or ".." in g.split("/"):
            out.append(f"guarded: {g!r} must be a repo-root path starting with '/' "
                       "(letters, digits, . _ - / *; no '..')")
    return list(raw)


def _skills(raw: Any, out: List[str]) -> List[str]:
    if not (isinstance(raw, list) and all(isinstance(s, str) for s in raw)):
        out.append("skills: must be a list of strings")
        return []
    for s in raw:
        if not _SKILL_RE.fullmatch(s):
            out.append(f"skills: invalid skill name {s!r}")
        elif not (SKILLS_DIR / s / "SKILL.md").is_file():
            out.append(f"skills: unknown skill '{s}' (no skills/{s}/SKILL.md)")
    return list(raw)


def load_pack(name: str, packs_dir: Optional[Path] = None) -> Pack:
    base = Path(packs_dir) if packs_dir is not None else default_packs_dir()
    pack_dir = base / name
    path = pack_dir / PACK_FILE
    if not path.is_file():
        raise ConfigError([f"unknown pack '{name}' (known: {', '.join(list_packs(base)) or 'none'})"])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ConfigError([f"{path} is not valid JSON: {e}"])
    if not isinstance(data, dict):
        raise ConfigError([f"{path}: top level must be an object"])
    out = [f"{k}: unknown key" for k in data if k not in _TOP_KEYS]
    if data.get("name") != name:
        out.append(f"name: must equal the directory name '{name}'")
    if not isinstance(data.get("description"), str):
        out.append("description: must be a string")
    comps_raw = data.get("components")
    comps: List[Component] = []
    if name == SHARED and not comps_raw:
        comps_raw = {}  # the shared layer has files and guarded paths but no components
    elif not isinstance(comps_raw, dict) or not comps_raw:
        out.append("components: must be a non-empty object")
        comps_raw = {}
    for cname, raw in comps_raw.items():
        c = _component(pack_dir, cname, raw, out)
        if c:
            comps.append(c)
    dev = _deps("devDependencies", data.get("devDependencies", {}), list(comps_raw), out)
    runtime = _deps("dependencies", data.get("dependencies", {}), list(comps_raw), out)
    _overlap(runtime, dev, out)
    guarded = _guarded(data.get("guarded", []), out)
    skills = _skills(data.get("skills", []), out)
    ecosystem = data.get("ecosystem", "npm")
    if ecosystem not in ECOSYSTEMS:
        out.append(f"ecosystem: must be one of {', '.join(ECOSYSTEMS)}")
    if out:
        raise ConfigError(out)
    return Pack(name, data["description"], comps, dev, skills, runtime, guarded, ecosystem)


def component_state(config: Config, comp: Component) -> Tuple[bool, str]:
    if comp.when_stack is None:
        return True, "always"
    if comp.when_stack in config.stacks:
        return True, f"stack {comp.when_stack}"
    return False, f"no {comp.when_stack} in stacks"


def enabled_components(config: Config, packs_dir: Optional[Path] = None
                       ) -> List[Tuple[Pack, Component]]:
    result: List[Tuple[Pack, Component]] = []
    base = Path(packs_dir) if packs_dir is not None else default_packs_dir()
    if not base.is_dir():
        return result  # packs are not shipped (vendored CI copy): core template only
    for name in config.packs:
        pack = load_pack(name, packs_dir)
        result.extend((pack, c) for c in pack.components if component_state(config, c)[0])
    return result


def _run_list(ns: argparse.Namespace) -> int:
    for name in list_packs():
        print(f"{name}: {load_pack(name).description}")
    return 0


def _configure_show(p: argparse.ArgumentParser) -> None:
    p.add_argument("name", help="pack name")
    p.add_argument("--repo", default=".", help="repo root containing foundry.json")


def _run_show(ns: argparse.Namespace) -> int:
    try:
        pack = load_pack(ns.name)
        config = load_config(Path(ns.repo))
    except ConfigError as e:
        for problem in e.problems:
            print(problem, file=sys.stderr)
        return 2
    print(f"{pack.name}: {pack.description}")
    print(f"  root {pack_root(config, pack.name)}, ecosystem {pack.ecosystem}")
    for comp in pack.components:
        on, why = component_state(config, comp)
        needs = f", {comp.requires} required" if comp.requires else ""
        print(f"  {comp.name} {'on' if on else 'off'} ({why}){needs}")
        for pkg, ver in sorted(pack.dependencies.get(comp.name, {}).items()):
            print(f"    dependency {pkg} {ver}")
        for pkg, ver in sorted(pack.dev_dependencies.get(comp.name, {}).items()):
            print(f"    dev dependency {pkg} {ver}")
    if pack.guarded:
        print("guarded (tech lead in CODEOWNERS): " + ", ".join(pack.guarded))
    if pack.skills:
        print("skills: " + ", ".join(pack.skills))
    return 0


def _run_roots(ns: argparse.Namespace) -> int:
    """`<pack>\\t<root>` per listed pack, from foundry.json alone (no packs directory needed)."""
    try:
        config = load_config(Path(ns.repo), check_packs=False)
    except ConfigError as e:
        for problem in e.problems:
            print(problem, file=sys.stderr)
        return 2
    for name in config.packs:
        print(f"{name}\t{pack_root(config, name)}")
    return 0


GUARDED_LIST = Path("scripts") / "foundry-ci" / "guarded.txt"


def _vendored_copy() -> bool:
    """True when this CLI is the copy `template apply` put at scripts/foundry-ci/ (run.py beside it)."""
    return (Path(__file__).resolve().parent.parent / "run.py").is_file()


def _run_guarded(ns: argparse.Namespace) -> int:
    """The placed guarded paths, one per line: CODEOWNERS' gate files without the repo's own.

    The vendored CI copy reads only the list `template apply` wrote to scripts/foundry-ci/guarded.txt
    (from the same code), never a packs folder a PR could add beside it. The plugin's own CLI
    reads the pack.json files.
    """
    repo = Path(ns.repo)
    try:
        config = load_config(repo, check_packs=False)
        if not config.packs:
            return 0
        listed = repo / GUARDED_LIST
        if _vendored_copy() or not default_packs_dir().is_dir():
            if not listed.is_file():
                raise ConfigError([f"{GUARDED_LIST.as_posix()} is missing; run foundry template apply"])
            lines = listed.read_text(encoding="utf-8").splitlines()
        else:
            lines = guarded_paths(config)
    except (ConfigError, OSError, UnicodeDecodeError) as e:
        for problem in (e.problems if isinstance(e, ConfigError) else [f"{GUARDED_LIST}: {e}"]):
            print(problem, file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 0


def _guard_list(ns: argparse.Namespace) -> int:
    try:
        return _run_list(ns)
    except ConfigError as e:
        for problem in e.problems:
            print(problem, file=sys.stderr)
        return 2


def register_commands() -> None:
    register("pack list", _guard_list, lambda p: None)
    register("pack show", _run_show, _configure_show)
    register("pack roots", _run_roots, lambda p: p.add_argument(
        "--repo", default=".", help="repo root containing foundry.json"))
    register("pack guarded", _run_guarded, lambda p: p.add_argument(
        "--repo", default=".", help="repo root containing foundry.json"))
