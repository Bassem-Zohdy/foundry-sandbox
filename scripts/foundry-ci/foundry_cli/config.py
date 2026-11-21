"""foundry.json schema, validation and loader (standard library only)."""
import argparse
import json
import keyword
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from .cli import register

DEFAULT_STORY_BRANCH_REGEX = r"^cu-[a-z0-9]+-[a-z0-9][a-z0-9-]*$"
CONFIG_FILE = "foundry.json"

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_KINDS = ("new-product", "takeover")
_MODES = ("solo", "team")
_TRACKERS = ("clickup", "none")
_ENVS = ("staging", "production")


class ConfigError(Exception):
    def __init__(self, problems: List[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class Config:
    foundry_version: str
    project_name: str
    project_kind: Literal["new-product", "takeover"]
    mode: Literal["solo", "team"]
    tech_lead: str
    developers: List[str]
    repo: str
    base_branch: str
    story_branch_regex: str
    packs: List[str]
    stacks: List[str]
    tracker: Dict[str, Any]
    modules: List[str]
    risky_paths: List[str]
    source_globs: List[str]
    test_globs: List[str]
    environments: Dict[str, Any]
    verify: List[Dict[str, Any]] = field(default_factory=list)
    risky_content: List[Dict[str, str]] = field(default_factory=list)
    quality_exclude: List[str] = field(default_factory=list)
    pack_roots: Dict[str, str] = field(default_factory=dict)  # pack name -> repo-relative dir
    python_package: Optional[str] = None  # the top-level package py_arch checks
    images: Dict[str, Dict[str, str]] = field(default_factory=dict)  # suffix -> build args


def _is_str(v: Any) -> bool:
    return isinstance(v, str) and v != ""


def _is_str_list(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) and x for x in v)


# (dotted path, required, checker, expectation text). Optional fields may be absent.
_FIELDS = [
    ("foundryVersion", True, _is_str, "a non-empty string"),
    ("project.name", True, _is_str, "a non-empty string"),
    ("project.kind", True, lambda v: v in _KINDS, "one of " + ", ".join(_KINDS)),
    ("mode", True, lambda v: v in _MODES, "one of " + ", ".join(_MODES)),
    ("roles.techLead", True, _is_str, "a non-empty GitHub handle"),
    ("roles.developers", False, _is_str_list, "a list of GitHub handles"),
    ("github.repo", True, lambda v: isinstance(v, str) and bool(_REPO_RE.match(v)),
     "owner/name"),
    ("github.baseBranch", False, _is_str, "a non-empty string"),
    ("storyBranchRegex", False, _is_str, "a regex string"),
    ("packs", False, _is_str_list, "a list of strings"),
    ("stacks", False, _is_str_list, "a list of strings"),
    ("modules", False, _is_str_list, "a list of strings"),
    ("riskyPaths", False, _is_str_list, "a list of glob strings"),
    ("paths.source", False, _is_str_list, "a list of glob strings"),
    ("paths.tests", False, _is_str_list, "a list of glob strings"),
    ("quality.exclude", False, _is_str_list, "a list of glob strings"),
]

_KNOWN = {
    "": {"foundryVersion", "project", "mode", "roles", "github", "storyBranchRegex",
         "packs", "stacks", "tracker", "modules", "riskyPaths", "paths",
         "environments", "verify", "riskyContent", "quality", "packRoots", "python",
         "images"},
    "project": {"name", "kind"},
    "roles": {"techLead", "developers"},
    "github": {"repo", "baseBranch"},
    "paths": {"source", "tests"},
    "quality": {"exclude"},
    "python": {"package"},
}


def _get(data: Dict[str, Any], dotted: str):
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def _validate_tracker(t: Any, out: List[str]) -> None:
    if not isinstance(t, dict):
        out.append("tracker: must be an object")
        return
    if t.get("type") not in _TRACKERS:
        out.append("tracker.type: must be one of " + ", ".join(_TRACKERS))
    for key in t:
        if key not in ("type", "spaceId", "listId"):
            out.append(f"tracker.{key}: unknown key")
    for key in ("spaceId", "listId"):
        if key in t and not _is_str(t[key]):
            out.append(f"tracker.{key}: must be a non-empty string")


def _validate_environments(envs: Any, out: List[str]) -> None:
    if not isinstance(envs, dict):
        out.append("environments: must be an object")
        return
    for name, env in envs.items():
        where = f"environments.{name}"
        if name not in _ENVS:
            out.append(f"{where}: unknown environment (use staging or production)")
        elif not isinstance(env, dict):
            out.append(f"{where}: must be an object")
        else:
            if not isinstance(env.get("deploySignal"), dict):
                out.append(f"{where}.deploySignal: must be an object")
            if not _is_str_list(env.get("healthProbes", [])):
                out.append(f"{where}.healthProbes: must be a list of strings")
            for key in env:
                if key not in ("deploySignal", "healthProbes"):
                    out.append(f"{where}.{key}: unknown key")


def _validate_risky_content(items: Any, out: List[str]) -> None:
    if not isinstance(items, list):
        out.append("riskyContent: must be a list")
        return
    for i, item in enumerate(items):
        where = f"riskyContent[{i}]"
        if not isinstance(item, dict):
            out.append(f"{where}: must be an object")
            continue
        for key in ("pattern", "reason"):
            if not _is_str(item.get(key)):
                out.append(f"{where}.{key}: must be a non-empty string")
        if _is_str(item.get("pattern")):
            try:
                re.compile(item["pattern"])
            except re.error as e:
                out.append(f"{where}.pattern: invalid regex ({e})")
        for key in item:
            if key not in ("pattern", "reason"):
                out.append(f"{where}.{key}: unknown key")


def _validate_verify(items: Any, out: List[str]) -> None:
    if not isinstance(items, list):
        out.append("verify: must be a list")
        return
    for i, item in enumerate(items):
        where = f"verify[{i}]"
        if not isinstance(item, dict):
            out.append(f"{where}: must be an object")
            continue
        for key in ("id", "run"):
            if not _is_str(item.get(key)):
                out.append(f"{where}.{key}: must be a non-empty string")
        vid = item.get("id")
        if _is_str(vid) and not re.fullmatch(r"[A-Za-z0-9._-]+", vid):
            out.append(f"{where}.id: may only contain letters, digits, '.', '_' and '-'")
        if not _is_str_list(item.get("stages", [])):
            out.append(f"{where}.stages: must be a list of strings")
        if "timeoutSeconds" in item:
            t = item["timeoutSeconds"]
            if isinstance(t, bool) or not isinstance(t, int) or not 1 <= t <= 86400:
                out.append(f"{where}.timeoutSeconds: must be an integer from 1 to 86400")
        for key in item:
            if key not in ("id", "run", "stages", "timeoutSeconds"):
                out.append(f"{where}.{key}: unknown key")


def _validate_pack_names(names: List[str], out: List[str]) -> None:
    """Each pack must have a valid pack.json (packs dir: FOUNDRY_PACKS_DIR or packs/)."""
    from . import packs  # lazy: packs imports this module
    if not packs.default_packs_dir().is_dir():
        return  # vendored CI copy: packs are not shipped, so names are not checked
    known = packs.list_packs()
    for i, name in enumerate(names):
        if name not in known:
            out.append(f"packs[{i}]: unknown pack '{name}' (known: {', '.join(known) or 'none'})")
            continue
        try:
            packs.load_pack(name)
        except ConfigError as e:
            out.extend(f"packs[{i}]: {p}" for p in e.problems)


_ROOT_SEGMENT_RE = re.compile(r"[A-Za-z0-9._-]+")


def _normal_root(value: Any) -> Optional[str]:
    """A clean repo-relative directory (`./web/` -> `web`), or None when it is unsafe.

    Every segment is limited to letters, digits, `.`, `_` and `-`: the root is written into
    CODEOWNERS, dependabot.yml and shell hooks.
    """
    if not isinstance(value, str) or not value or value.startswith("/"):
        return None
    parts = [p for p in value.split("/") if p not in ("", ".")]
    if any(p == ".." or not _ROOT_SEGMENT_RE.fullmatch(p) for p in parts):
        return None
    if value.strip(" ") == "" or (not parts and value not in (".", "./")):
        return None
    return "/".join(parts) or "."


def _validate_pack_roots(roots: Any, packs: Any, out: List[str]) -> None:
    if not isinstance(roots, dict):
        out.append("packRoots: must be an object")
        return
    listed = packs if isinstance(packs, list) else []
    for name, value in roots.items():
        if name not in listed:
            out.append(f"packRoots.{name}: unknown pack '{name}'")
        if _normal_root(value) is None:
            out.append(f"packRoots.{name}: must be a relative path inside the repo")


def _validate_python(section: Any, out: List[str]) -> None:
    if not isinstance(section, dict):
        out.append("python: must be an object")
        return
    pkg = section.get("package")
    if "package" in section and not (isinstance(pkg, str) and pkg.isidentifier()
                                     and not keyword.iskeyword(pkg)):
        out.append("python.package: must be a Python identifier")


_IMAGE_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")  # the suffix of Dockerfile.<suffix>
_ARG_RE = re.compile(r"[A-Z][A-Z0-9_]*")


def _validate_build_args(where: str, args: Any, out: List[str]) -> None:
    if not isinstance(args, dict):
        out.append(f"{where}: must be an object")
        return
    for name, value in args.items():
        if not _ARG_RE.fullmatch(name):
            out.append(f"{where}.{name}: must be an upper-case name ([A-Z][A-Z0-9_]*)")
        if not (_is_str(value) and "\n" not in value and "\r" not in value):
            out.append(f"{where}.{name}: must be a non-empty string without newlines")


def _validate_images(images: Any, out: List[str]) -> None:
    """`images`: image suffix -> {"buildArgs": {NAME: value}}, read by `foundry image args`."""
    if not isinstance(images, dict):
        out.append("images: must be an object")
        return
    for suffix, image in images.items():
        where = f"images.{suffix}"
        if not _IMAGE_RE.fullmatch(suffix):
            out.append(f"{where}: must be an image suffix (lowercase letters, digits, - or _)")
        if not isinstance(image, dict):
            out.append(f"{where}: must be an object")
            continue
        for key in image:
            if key != "buildArgs":
                out.append(f"{where}.{key}: unknown key")
        if "buildArgs" in image:
            _validate_build_args(f"{where}.buildArgs", image["buildArgs"], out)


def validate(data: Dict[str, Any], check_packs: bool = True) -> List[str]:
    """Return every problem found, each naming its field. Empty list means valid.

    `check_packs=False` skips the pack-name lookup, for commands that need the config alone.
    """
    if not isinstance(data, dict):
        return ["foundry.json: top level must be an object"]
    out: List[str] = []
    for prefix, known in _KNOWN.items():
        obj = data if prefix == "" else data.get(prefix)
        if not isinstance(obj, dict):
            continue
        for key in obj:
            if key not in known:
                path = key if prefix == "" else f"{prefix}.{key}"
                out.append(f"{path}: unknown key")
    for path, required, check, expect in _FIELDS:
        present, value = _get(data, path)
        if not present:
            if required:
                out.append(f"{path}: required")
        elif not check(value):
            out.append(f"{path}: must be {expect}")
    for section in ("project", "roles", "github", "paths", "quality"):
        if section in data and not isinstance(data[section], dict):
            out.append(f"{section}: must be an object")
    if data.get("mode") == "team":
        present, devs = _get(data, "roles.developers")
        if not present or not devs:
            out.append("roles.developers: required (non-empty) when mode is team")
    if isinstance(data.get("storyBranchRegex"), str):
        try:
            re.compile(data["storyBranchRegex"])
        except re.error as e:
            out.append(f"storyBranchRegex: invalid regex ({e})")
    if "tracker" in data:
        _validate_tracker(data["tracker"], out)
    if "environments" in data:
        _validate_environments(data["environments"], out)
    if "riskyContent" in data:
        _validate_risky_content(data["riskyContent"], out)
    if "verify" in data:
        _validate_verify(data["verify"], out)
    if check_packs and _is_str_list(data.get("packs")):
        _validate_pack_names(data["packs"], out)
    if "packRoots" in data:
        _validate_pack_roots(data["packRoots"], data.get("packs", []), out)
    if "python" in data:
        _validate_python(data["python"], out)
    if "images" in data:
        _validate_images(data["images"], out)
    return out


def load_config(repo: Path, check_packs: bool = True) -> Config:
    path = Path(repo) / CONFIG_FILE
    if not path.is_file():
        raise ConfigError([f"{CONFIG_FILE} not found in {repo}"])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ConfigError([f"{CONFIG_FILE} is not valid JSON: {e}"])
    problems = validate(data, check_packs)
    if problems:
        raise ConfigError(problems)
    gh, paths = data["github"], data.get("paths", {})
    return Config(
        foundry_version=data["foundryVersion"],
        project_name=data["project"]["name"],
        project_kind=data["project"]["kind"],
        mode=data["mode"],
        tech_lead=data["roles"]["techLead"],
        developers=list(data["roles"].get("developers", [])),
        repo=gh["repo"],
        base_branch=gh.get("baseBranch", "main"),
        story_branch_regex=data.get("storyBranchRegex", DEFAULT_STORY_BRANCH_REGEX),
        packs=list(data.get("packs", [])),
        stacks=list(data.get("stacks", [])),
        tracker=dict(data.get("tracker", {"type": "none"})),
        modules=list(data.get("modules", ["core"])),
        risky_paths=list(data.get("riskyPaths", [])),
        source_globs=list(paths.get("source", [])),
        test_globs=list(paths.get("tests", [])),
        environments=dict(data.get("environments", {})),
        verify=list(data.get("verify", [])),
        risky_content=[dict(r) for r in data.get("riskyContent", [])],
        quality_exclude=list(data.get("quality", {}).get("exclude", [])),
        pack_roots={k: _normal_root(v) or "." for k, v in data.get("packRoots", {}).items()},
        python_package=data.get("python", {}).get("package"),
        images={k: dict(v.get("buildArgs", {})) for k, v in data.get("images", {}).items()},
    )


def _configure_check(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", default=".", help="repo root containing foundry.json")


def _run_check(ns: argparse.Namespace) -> int:
    try:
        load_config(Path(ns.repo))
    except ConfigError as e:
        for problem in e.problems:
            print(problem, file=sys.stderr)
        return 2
    print("foundry.json OK")
    return 0


def register_commands() -> None:
    register("config check", _run_check, _configure_check)
