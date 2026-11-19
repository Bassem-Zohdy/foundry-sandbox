"""Propose foundry.json values from what is already in a repo (read-only)."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .cli import register
from .config import ConfigError

Runner = Callable[..., Tuple[int, str, str]]  # (args, cwd=None) -> (rc, stdout, stderr)

TAKEOVER_MIN_COMMITS = 21  # "more than 20 commits"
MAX_DEPTH = 3
VERIFY_STAGES = ["pre-push", "ci"]
VERIFY_SCRIPTS = ("lint", "typecheck", "test", "build", "contract")
QUALITY_RUN = "sh scripts/foundry-quality/run.sh check"
# Pre-push only: in CI the `foundry / quality` job owns the run (it installs Semgrep), while
# the `foundry / verify` job has no Semgrep, so a ci-stage quality entry fails every PR.
QUALITY_STAGES = ["pre-push"]
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "dist", "build",
             ".next", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache", "coverage"}
# (dependency name, stack), in the order stacks are reported.
NODE_STACKS = (("@nestjs/core", "ts-nest"), ("next", "ts-next"), ("vite", "react-vite"))
PY_STACKS = (("fastapi", "python-fastapi"), ("celery", "python-celery"))
EXISTING = ("AGENTS.md", "CLAUDE.md", "REVIEW.md", "CODEOWNERS", ".github/CODEOWNERS",
            "lefthook.yml", ".pre-commit-config.yaml", ".husky",
            ".github/pull_request_template.md")
WORKFLOWS = Path(".github") / "workflows"
# git@github.com:o/n(.git), https://github.com/o/n(.git), ssh://git@github.com/o/n.git
_REMOTE_RE = re.compile(r"^(?:[a-z+]+://)?(?:[^@/]+@)?([^:/]+)(?::\d+)?[:/]"
                        r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def _run(args: List[str], cwd: Optional[Path] = None) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, cwd=cwd)
    except OSError as e:
        return 127, "", str(e)
    return p.returncode, p.stdout, p.stderr


def _walk(repo: Path, names: Callable[[str], bool]) -> List[Path]:
    """Files under repo (depth <= MAX_DEPTH) whose name passes `names`, sorted."""
    found: List[Path] = []

    def visit(d: Path, depth: int) -> None:
        try:
            entries = sorted(d.iterdir())
        except OSError:
            return
        for e in entries:
            if e.is_symlink():
                continue
            if e.is_dir():
                if depth < MAX_DEPTH and e.name not in SKIP_DIRS:
                    visit(e, depth + 1)
            elif names(e.name):
                found.append(e)

    visit(repo, 0)
    return sorted(found, key=lambda p: (len(p.relative_to(repo).parts), p.as_posix()))


def _package_manager(repo: Path) -> str:
    if (repo / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (repo / "yarn.lock").exists():
        return "yarn"
    return "npm"


def _script_command(pm: str, rel_dir: str, script: str) -> str:
    if rel_dir == ".":
        return f"{pm} run {script}"
    flag = {"npm": "--prefix", "pnpm": "--dir", "yarn": "--cwd"}[pm]
    return f"{pm} {flag} {rel_dir} run {script}"


Where = Dict[str, Set[str]]  # stack -> folders (repo-relative) of the manifests that name it


def _node(repo: Path, stacks: List[str], verify: List[Dict[str, Any]],
          warnings: List[str], where: Optional[Where] = None) -> None:
    pm = _package_manager(repo)
    for path in _walk(repo, lambda n: n == "package.json"):
        rel = path.relative_to(repo)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("top level is not an object")
        except (ValueError, OSError, UnicodeDecodeError) as e:
            warnings.append(f"{rel.as_posix()}: skipped ({e})")
            continue
        deps = set()
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            if isinstance(data.get(key), dict):
                deps.update(data[key])
        for dep, stack in NODE_STACKS:
            if dep in deps:
                if stack not in stacks:
                    stacks.append(stack)
                if where is not None:
                    where.setdefault(stack, set()).add(rel.parent.as_posix())
        scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
        rel_dir = rel.parent.as_posix()
        for script in VERIFY_SCRIPTS:
            if isinstance(scripts.get(script), str):
                vid = script if rel_dir == "." else f"{rel.parent.name}-{script}"
                if any(v["id"] == vid for v in verify):
                    vid = f"{rel_dir.replace('/', '-')}-{script}"
                verify.append({"id": vid, "run": _script_command(pm, rel_dir, script),
                               "stages": list(VERIFY_STAGES)})


def _mentions(text: str, package: str) -> bool:
    """True if `package` appears as a whole dependency name (not fastapi-users)."""
    return re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(package)}(?![A-Za-z0-9_-])",
                     text, re.IGNORECASE) is not None


def _has_pytest_config(repo: Path) -> bool:
    if (repo / "pytest.ini").is_file():
        return True
    for name, marker in (("pyproject.toml", "[tool.pytest.ini_options]"),
                         ("setup.cfg", "[tool:pytest]"), ("tox.ini", "[pytest]")):
        path = repo / name
        try:
            if path.is_file() and marker in path.read_text(encoding="utf-8"):
                return True
        except (OSError, UnicodeDecodeError):
            continue
    return False


def _python(repo: Path, stacks: List[str], verify: List[Dict[str, Any]],
            warnings: List[str], where: Optional[Where] = None) -> None:
    def is_manifest(n: str) -> bool:
        return n == "pyproject.toml" or (n.startswith("requirements") and n.endswith(".txt"))

    for path in _walk(repo, is_manifest):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            warnings.append(f"{path.relative_to(repo).as_posix()}: skipped ({e})")
            continue
        for package, stack in PY_STACKS:
            if _mentions(text, package):
                if stack not in stacks:
                    stacks.append(stack)
                if where is not None:
                    where.setdefault(stack, set()).add(path.parent.relative_to(repo).as_posix())
    if _has_pytest_config(repo):
        run = "uv run pytest" if (repo / "uv.lock").exists() else "python3 -m pytest"
        verify.append({"id": "pytest", "run": run, "stages": list(VERIFY_STAGES)})


def _github_repo(repo: Path, run: Runner, warnings: List[str]) -> Optional[str]:
    rc, out, _ = run(["git", "remote", "get-url", "origin"], cwd=repo)
    if rc != 0:
        return None
    url = out.strip()
    m = _REMOTE_RE.match(url)
    if not m:
        warnings.append(f"origin {url!r}: not a recognised remote URL; github.repo not proposed")
        return None
    if m.group(1).lower() != "github.com":
        warnings.append(f"origin {url!r}: not a github.com remote; github.repo not proposed")
        return None
    return f"{m.group(2)}/{m.group(3)}"


def _commit_count(repo: Path, run: Runner) -> Optional[int]:
    rc, out, _ = run(["git", "rev-list", "--count", "HEAD"], cwd=repo)
    if rc != 0:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def _existing(repo: Path) -> List[str]:
    """Process tooling already in the repo, which setup may replace or merge with."""
    found = [p for p in EXISTING if (repo / p).exists()]
    try:
        found += sorted((WORKFLOWS / f.name).as_posix()
                        for f in (repo / WORKFLOWS).iterdir() if f.is_file())
    except OSError:
        pass
    return found


def _pack_stacks(packs_dir: Optional[Path] = None) -> Dict[str, Set[str]]:
    """Pack -> the stacks its conditional components wait for (always-on ones never count)."""
    from . import packs  # lazy: packs imports config
    out: Dict[str, Set[str]] = {}
    for name in packs.list_packs(packs_dir):
        try:
            pack = packs.load_pack(name, packs_dir)
        except ConfigError:  # a broken pack is `foundry pack check`'s to report, not detect's
            continue
        out[name] = {c.when_stack for c in pack.components if c.when_stack}
    return out


def propose_packs(stacks: List[str], packs_dir: Optional[Path] = None) -> List[str]:
    """Packs with a component whose whenStack is detected (always-on components never count)."""
    return [name for name, waits in _pack_stacks(packs_dir).items() if waits & set(stacks)]


def _pack_roots(pack_names: List[str], where: Where,
                packs_dir: Optional[Path] = None) -> Dict[str, str]:
    """A pack whose triggering manifests all sit in one subfolder gets that folder as its root."""
    waits = _pack_stacks(packs_dir)
    roots: Dict[str, str] = {}
    for name in pack_names:
        folders: Set[str] = set()
        for stack in waits.get(name, ()):
            folders |= where.get(stack, set())
        if len(folders) == 1 and folders != {"."}:
            roots[name] = next(iter(folders))
    return roots


PY_APP_MARKERS = ("FastAPI(", "Celery(")
NOT_PACKAGES = {"tests", "test", "scripts", "migrations", "alembic", "docs"}


def _holds_app(folder: Path) -> bool:
    for path in sorted(folder.rglob("*.py"))[:200]:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(marker in text for marker in PY_APP_MARKERS):
            return True
    return False


def _python_package(repo: Path, root: str, warnings: List[str]) -> Optional[str]:
    """The one top-level package under the Python root that holds the app, else a warning."""
    base = repo / root
    try:
        folders = sorted(p for p in base.iterdir() if p.is_dir() and not p.is_symlink()
                         and p.name not in SKIP_DIRS and (p / "__init__.py").is_file())
    except OSError:
        folders = []
    names = [p for p in folders if p.name not in NOT_PACKAGES and not p.name.startswith(".")]
    if len(names) > 1:
        names = [p for p in names if _holds_app(p)]
    if len(names) == 1:
        return names[0].name
    where = "" if root == "." else f" under {root}"
    why = ("no top-level package with __init__.py" if not folders or not names
           else "several top-level packages hold the app")
    warnings.append(f"python.package not proposed: {why}{where}; set it in foundry.json")
    return None


# (stack, image suffix, build arg, entry module, value from the package)
PY_IMAGES = (("python-fastapi", "api", "APP_MODULE", "main", "{}.main:app"),
             ("python-celery", "worker", "CELERY_APP", "worker", "{}.worker"))


def _python_images(repo: Path, root: str, package: str, stacks: List[str],
                   warnings: List[str]) -> Dict[str, Any]:
    """Build args for Dockerfile.api and Dockerfile.worker, from the package's entry modules."""
    images: Dict[str, Any] = {}
    for stack, suffix, arg, module, value in PY_IMAGES:
        if stack not in stacks:
            continue
        rel = f"{package}/{module}.py" if root == "." else f"{root}/{package}/{module}.py"
        if (repo / rel).is_file():
            images[suffix] = {"buildArgs": {arg: value.format(package)}}
        else:
            warnings.append(f"images.{suffix}.buildArgs.{arg} not proposed: {rel} not found; "
                            "set it by hand in foundry.json")
    return images


def _through_quality_runner(verify: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """In a pack repo lint runs only via the baseline-aware quality runner: a raw lint step
    fails on legacy code the baseline already covers."""
    kept = [v for v in verify if not (v["id"] == "lint" or v["id"].endswith("-lint"))]
    return [{"id": "quality", "run": QUALITY_RUN, "stages": list(QUALITY_STAGES)}] + kept


def _source_globs(repo: Path, roots: Dict[str, str], package: Optional[str]) -> List[str]:
    """`paths.source` for the coverage gate: `src/**`, plus `app/**` when the repo has one, and
    the same two under each pack root in a subfolder (a changed dashboard file needs coverage),
    plus the Python package under the python root (coverage.sh measures `--cov=<package>`)."""
    globs: List[str] = []
    for base in ["."] + sorted(set(roots.values()) - {"."}):
        lead = "" if base == "." else f"{base}/"
        globs.append(f"{lead}src/**")
        if (repo / base / "app").is_dir():
            globs.append(f"{lead}app/**")
    if package:
        base = roots.get("python", ".")
        glob = f"{package}/**" if base == "." else f"{base}/{package}/**"
        if glob not in globs:
            globs.append(glob)
    return globs


def detect(repo: Path, run: Optional[Runner] = None) -> Dict[str, Any]:
    """Propose config values. Keys mirror foundry.json; `evidence` is information only.

    Only keys detection actually settled are present, so setup knows what to ask.
    """
    repo = Path(repo)
    run = run or _run
    stacks: List[str] = []
    verify: List[Dict[str, Any]] = []
    warnings: List[str] = []
    where: Where = {}
    _node(repo, stacks, verify, warnings, where)
    _python(repo, stacks, verify, warnings, where)
    commits = _commit_count(repo, run)
    pack_names = propose_packs(stacks)
    if pack_names:
        verify = _through_quality_runner(verify)
    found: Dict[str, Any] = {"stacks": stacks, "packs": pack_names, "verify": verify}
    if pack_names:
        roots = _pack_roots(pack_names, where)
        found["paths"] = {"source": []}  # filled once the Python package is known
        if roots:
            found["packRoots"] = roots
        package = None
        if any(st in stacks for _, st in PY_STACKS):
            package = _python_package(repo, roots.get("python", "."), warnings)
            if package:
                found["python"] = {"package": package}
                images = _python_images(repo, roots.get("python", "."), package, stacks, warnings)
                if images:
                    found["images"] = images
        found["paths"]["source"] = _source_globs(repo, roots, package)
    slug = _github_repo(repo, run, warnings)
    if slug:
        found["github"] = {"repo": slug}
    if commits is not None and commits >= TAKEOVER_MIN_COMMITS:
        found["project"] = {"kind": "takeover"}
    found["evidence"] = {
        "commits": commits,
        "existing": _existing(repo),
        "warnings": warnings,
    }
    return found


def _configure(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", default=".", help="repo to inspect")


def _run_detect(ns: argparse.Namespace) -> int:
    if not Path(ns.repo).is_dir():
        print(f"foundry: not a directory: {ns.repo}", file=sys.stderr)
        return 2
    print(json.dumps(detect(Path(ns.repo)), indent=2))
    return 0


def register_commands() -> None:
    register("detect", _run_detect, _configure)
