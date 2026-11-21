"""Turn setup interview answers into foundry.json (`foundry config write`).

The answers file is a partial foundry.json: the same keys, holding only what
the user answered. Layers, later wins: Foundry's defaults, what `detect`
proposed, the repo's existing foundry.json (earlier choices beat detection),
the installed Foundry version, then the answers. Objects merge key by key;
lists and values replace.
"""
import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .cli import register
from .config import CONFIG_FILE, DEFAULT_STORY_BRANCH_REGEX, ConfigError, validate
from .detect import detect

DEFAULTS: Dict[str, Any] = {
    "foundryVersion": __version__,
    "github": {"baseBranch": "main"},
    "roles": {"developers": []},
    "storyBranchRegex": DEFAULT_STORY_BRANCH_REGEX,
    "packs": [],
    "stacks": [],
    "tracker": {"type": "none"},
    "modules": ["core"],
}
_DETECTION_ONLY = ("evidence",)
# Key order of schemas/foundry.example.json, so written files diff cleanly.
_ORDER = {
    "": ("foundryVersion", "project", "mode", "roles", "github", "storyBranchRegex", "packs",
         "stacks", "tracker", "modules", "riskyPaths", "paths", "environments", "verify"),
    "project": ("name", "kind"),
    "roles": ("techLead", "developers"),
    "github": ("repo", "baseBranch"),
    "tracker": ("type", "spaceId", "listId"),
    "paths": ("source", "tests"),
}


def _ordered(obj: Dict[str, Any], section: str = "") -> Dict[str, Any]:
    order = _ORDER.get(section, ())
    keys = [k for k in order if k in obj] + [k for k in obj if k not in order]
    return {k: _ordered(obj[k], k) if section == "" and isinstance(obj[k], dict) else obj[k]
            for k in keys}


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def answers_to_config(answers: Dict[str, Any], detected: Dict[str, Any],
                      existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build a valid foundry.json dict, or raise ConfigError naming every problem."""
    if not isinstance(answers, dict):
        raise ConfigError(["answers: top level must be an object"])
    proposed = {k: v for k, v in (detected or {}).items() if k not in _DETECTION_ONLY}
    config = DEFAULTS
    for layer in (proposed, existing or {}, {"foundryVersion": __version__}, answers):
        config = _merge(config, layer)
    problems = validate(config)
    if problems:
        raise ConfigError(problems)
    return _ordered(config)


def _leaves(obj: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in obj.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            out.update(_leaves(value, path + "."))
        else:
            out[path] = value
    return out


def key_diff(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    """Key-level changes, sorted: `+ added`, `- removed`, `~ changed` (dotted paths)."""
    a, b = _leaves(old), _leaves(new)
    lines = [f"+ {k}" for k in b if k not in a]
    lines += [f"- {k}" for k in a if k not in b]
    lines += [f"~ {k}" for k in a if k in b and a[k] != b[k]]
    return sorted(lines, key=lambda line: line[2:])


def _read_existing(target: Path) -> Optional[Dict[str, Any]]:
    """The current foundry.json as a dict, or None (with a note) if it cannot be used."""
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        data = None
    if not isinstance(data, dict):
        print(f"existing {CONFIG_FILE} is not valid JSON; not used as a base", file=sys.stderr)
        return None
    return data


def _configure(p: argparse.ArgumentParser) -> None:
    p.add_argument("--answers", required=True, help="JSON file with the interview answers")
    p.add_argument("--repo", default=".", help="repo to detect from and write foundry.json into")
    p.add_argument("--apply", action="store_true",
                   help=f"write {CONFIG_FILE} (default is a dry run that prints it)")


def _run(ns: argparse.Namespace) -> int:
    repo = Path(ns.repo)
    if not repo.is_dir():
        print(f"foundry: not a directory: {repo}", file=sys.stderr)
        return 2
    try:
        text = Path(ns.answers).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"foundry: cannot read answers file: {e}", file=sys.stderr)
        return 2
    try:
        answers = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"foundry: answers file is not valid JSON: {e}", file=sys.stderr)
        return 2
    target = repo / CONFIG_FILE
    existing = _read_existing(target)
    if ns.apply and existing is None and target.exists():
        print(f"foundry: refusing to overwrite an unreadable {CONFIG_FILE}; "
              "fix or move it, then run again", file=sys.stderr)
        return 2
    try:
        config = answers_to_config(answers, detect(repo), existing)
    except ConfigError as e:
        for problem in e.problems:
            print(problem, file=sys.stderr)
        return 2
    body = json.dumps(config, indent=2) + "\n"
    action = "update" if target.exists() else "create"
    # stdout carries only the JSON (dry run), so it can be redirected to a scratch file.
    print(f"{action} {CONFIG_FILE}", file=sys.stderr)
    if existing is not None:
        for line in key_diff(existing, config) or ["no key changes"]:
            print(f"    {line}", file=sys.stderr)
    if not ns.apply:
        print(body, end="")
        print("dry run: nothing written (use --apply)", file=sys.stderr)
        return 0
    target.write_text(body, encoding="utf-8")
    print(f"wrote {CONFIG_FILE} ({action})")
    return 0


def register_commands() -> None:
    register("config write", _run, _configure)
