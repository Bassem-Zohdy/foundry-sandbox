"""Python dependency check for `pack check`: requirements*.txt (with `-r` includes) and
pyproject.toml `[project].dependencies`, read at the pack root. Names follow PEP 503."""
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover - exercised by patching the name to None
    tomllib = None  # type: ignore[assignment]

WARN_VERSION = "version "
WARN_NOTE = "warning "
_NAME = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(\[[^\]]*\])?\s*(.*)$")
_INCLUDE = re.compile(r"^(?:-r|--requirement)(?:\s+|=)(\S.*)$")
Declared = Dict[str, str]  # normalised name -> version spec ("" when none)


def normalise(name: str) -> str:
    """PEP 503: runs of `-`, `_` and `.` are one `-`, lower case."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirement(text: str) -> Optional[Tuple[str, str]]:
    """(normalised name, spec) of one PEP 508 line; None for options, URLs and blanks."""
    line = text.split(";", 1)[0].strip()
    if not line or line.startswith(("-", "git+", "http:", "https:", "file:", ".", "/")):
        return None
    m = _NAME.match(line)
    if not m:
        return None
    return normalise(m.group(1)), m.group(3).replace(" ", "")


def _logical_lines(text: str) -> List[str]:
    """Lines with comments dropped and backslash continuations joined."""
    out: List[str] = []
    pending = ""
    for raw in text.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).rstrip()
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        out.append((pending + line).strip())
        pending = ""
    if pending.strip():
        out.append(pending.strip())
    return [ln for ln in out if ln]


class _Reader:
    """Reads requirements files, following includes once each."""

    def __init__(self, repo: Path, root: str) -> None:
        self.base = (Path(repo) / root).resolve()
        self.declared: Declared = {}
        self.lines: List[str] = []
        self._done: Set[Path] = set()
        self._looped: Set[Tuple[Path, Path]] = set()

    def _name(self, path: Path) -> str:
        try:
            return path.relative_to(self.base).as_posix()
        except ValueError:
            return path.as_posix()

    def read(self, path: Path, via: Tuple[Path, ...] = ()) -> None:
        path = path.resolve()
        if path in via:
            if (via[-1], path) not in self._looped:
                self._looped.add((via[-1], path))
                chain = " -> ".join(self._name(p) for p in (*via[via.index(path):], path))
                self.lines.append(f"{WARN_NOTE}requirements include loop: {chain}")
            return
        if path in self._done:
            return
        self._done.add(path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            self.lines.append(f"{self._name(path)} cannot be read"
                              + (f" (included from {self._name(via[-1])})" if via else ""))
            return
        for line in _logical_lines(text):
            inc = _INCLUDE.match(line)
            if inc:
                self.read(path.parent / inc.group(1).strip(), (*via, path))
                continue
            parsed = parse_requirement(line)
            if parsed:
                self.declared.setdefault(parsed[0], parsed[1])


def _pyproject(path: Path, declared: Declared, lines: List[str]) -> None:
    if tomllib is None:
        lines.append(f"{WARN_NOTE}pyproject.toml was not read: Python 3.10 has no tomllib")
        return
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        lines.append("pyproject.toml is not valid TOML")
        return
    project = data.get("project")
    deps = project.get("dependencies") if isinstance(project, dict) else None
    for item in deps if isinstance(deps, list) else []:
        parsed = parse_requirement(item) if isinstance(item, str) else None
        if parsed:
            declared.setdefault(parsed[0], parsed[1])


def read_declared(repo: Path, root: str) -> "Tuple[Declared, List[str]] | str":
    """Runtime requirements at the pack root, plus warning/problem lines; or one problem line
    when there is no manifest at all."""
    reader = _Reader(repo, root)
    manifests = sorted(reader.base.glob("requirements*.txt")) if reader.base.is_dir() else []
    pyproject = reader.base / "pyproject.toml"
    if not manifests and not pyproject.is_file():
        where = "" if root in ("", ".") else f" in {root}"
        return f"no requirements*.txt or pyproject.toml found{where}"
    for path in manifests:
        if path.is_file():
            reader.read(path)
    if pyproject.is_file():
        _pyproject(pyproject, reader.declared, reader.lines)
    return reader.declared, reader.lines


def _exact(spec: str) -> str:
    m = re.match(r"^===?([^,;\s]+)$", spec)
    return m.group(1) if m else ""


def check(wanted: Dict[str, Tuple[str, str, str]], repo: Path, root: str) -> List[str]:
    """Lines for `wanted` (package -> (version, section, owner)) against the manifests."""
    found = read_declared(repo, root)
    if isinstance(found, str):
        return [found]
    declared, lines = found
    suffix = "" if root in ("", ".") else f", root {root}"
    for pkg, (ver, section, owner) in wanted.items():
        spec = declared.get(normalise(pkg))
        if spec is None:
            lines.append(f"missing {pkg}=={ver} ({owner}, {section}{suffix})")
        elif _exact(spec) != ver:
            lines.append(f"{WARN_VERSION}{pkg}: {spec or 'unpinned'} (pack pins {ver})")
    return lines


def uses_uv(repo: Path, roots: List[str]) -> bool:
    return any((Path(repo) / r / "uv.lock").is_file() for r in roots)
