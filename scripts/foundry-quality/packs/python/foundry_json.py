"""foundry.json for the python pack's checkers and hooks (standard library only, Python 3.10+).

py_limits.py and py_arch.py import it to read the config, list the Python files of a pack root
and write SARIF. tools.sh runs it:

    python3 foundry_json.py exclude <root>   quality.exclude, relative to <root>, one per line
    python3 foundry_json.py files <root>     the .py files the checkers read, relative to <root>
    python3 foundry_json.py package <root>   python.package (coverage.sh measures it)

The files are the tracked and untracked (not ignored) `.py` files under the root, less
`quality.exclude` and the other packs' roots inside it (FOUNDRY_OTHER_ROOTS, relative to the root,
one per line), and less the files Foundry itself vendors at the repo root (FOUNDRY_OWNED: the
Claude Code hooks, the vendored CLI and these hooks), which are Foundry's code, not the project's.
Globs follow Foundry's rules: `**` crosses folders, `*` and `?` do not.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path


FOUNDRY_OWNED = [".claude/**", "scripts/foundry-ci/**", "scripts/foundry-quality/**"]


class ConfigError(Exception):
    """foundry.json or the repo cannot be read (exit 2)."""


def repo_of(root: Path) -> Path:
    """The git work tree that holds `root`."""
    proc = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise ConfigError(f"{root} is not inside a git repo")
    return Path(proc.stdout.strip()).resolve()


def load(repo: Path) -> dict:
    path = repo / "foundry.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: must be a JSON object")
    return data


def exclude(config: dict) -> list:
    """quality.exclude plus the Foundry-owned paths, repo-relative."""
    globs = (config.get("quality") or {}).get("exclude") or []
    return [g for g in globs if isinstance(g, str)] + FOUNDRY_OWNED


def _glob_to_regex(pattern: str):
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        else:
            out.append({"*": "[^/]*", "?": "[^/]"}.get(pattern[i], re.escape(pattern[i])))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def matches(path: str, globs: list) -> bool:
    return any(_glob_to_regex(g).match(path) for g in globs)


def root_prefix(repo: Path, root: Path) -> str:
    """`api/` for the root `api`, `` for the repo root."""
    rel = root.resolve().relative_to(repo.resolve()).as_posix()
    return "" if rel == "." else rel + "/"


def other_roots() -> list:
    return [r.strip("/") for r in os.environ.get("FOUNDRY_OTHER_ROOTS", "").splitlines() if r.strip()]


def python_files(repo: Path, root: Path, other_roots: list = ()) -> list:
    """Repo-relative `.py` files under `root`, less quality.exclude and the nested pack roots."""
    proc = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off", "ls-files", "-z",
                           "--cached", "--others", "--exclude-standard"],
                          capture_output=True)
    if proc.returncode != 0:
        raise ConfigError("git could not list the files")
    lead = root_prefix(repo, root)
    nested = [lead + r + "/" for r in other_roots]
    globs = exclude(load(repo))
    names = sorted(set(proc.stdout.decode("utf-8", "surrogateescape").split("\0")))
    return [f for f in names
            if f.endswith(".py") and f.startswith(lead) and (repo / f).is_file()
            and not any(f.startswith(n) for n in nested) and not matches(f, globs)]


def relative_globs(globs: list, lead: str) -> list:
    """The globs that can match under the root `lead`, made relative to it."""
    if not lead:
        return list(globs)
    return [g[len(lead):] if g.startswith(lead) else g for g in globs
            if g.startswith(lead) or g.startswith("**")]


def write_sarif(path: str, driver: tuple, results: list, files: list) -> None:
    """driver: (tool, {ruleId: level}); results: (ruleId, uri, line, message); files: the scanned uris."""
    tool, rules = driver
    run = {
        "tool": {"driver": {"name": tool, "rules": [
            {"id": rule, "defaultConfiguration": {"level": level}} for rule, level in rules.items()]}},
        "artifacts": [{"location": {"uri": f}} for f in files],
        "results": [{"ruleId": rule, "level": rules[rule], "message": {"text": text},
                     "locations": [{"physicalLocation": {"artifactLocation": {"uri": uri},
                                                         "region": {"startLine": max(line, 1)}}}]}
                    for rule, uri, line, text in results],
    }
    Path(path).write_text(json.dumps({"version": "2.1.0", "runs": [run]}, indent=1) + "\n",
                          encoding="utf-8")


def package(config: dict) -> str:
    name = (config.get("python") or {}).get("package")
    if not isinstance(name, str) or not name.isidentifier():
        raise ConfigError("foundry.json python.package is not set; set it to the package the "
                          "tests cover (for example app)")
    return name


def main(argv: list) -> int:
    if len(argv) != 2 or argv[0] not in ("exclude", "files", "package"):
        print("usage: foundry_json.py exclude|files|package <root>", file=sys.stderr)
        return 2
    try:
        root = Path(argv[1]).resolve()
        repo = repo_of(root)
        lead = root_prefix(repo, root)
        if argv[0] == "exclude":
            lines = relative_globs(exclude(load(repo)), lead)
        elif argv[0] == "package":
            lines = [package(load(repo))]
        else:
            lines = [f[len(lead):] for f in python_files(repo, root, other_roots())]
    except (ConfigError, ValueError) as e:
        print(f"foundry_json: {e}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
