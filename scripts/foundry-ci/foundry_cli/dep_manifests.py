"""Manifest, lockfile and requirements checks for /approve-deps (R35): the same package set,
only versions moving, and no source outside the normal registries."""
import json
import re
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from .dep_ci_files import pairs

_NPM_MAPS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
_REQUIREMENTS_RE = re.compile(r"^requirements[^/]*\.txt$")
REGISTRY_HOSTS = ("registry.npmjs.org", "registry.yarnpkg.com", "pypi.org",
                  "files.pythonhosted.org")
_URL_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*)://([^/\s\"'\\]*)")
_SOURCE_RE = re.compile(r"(?:git\+|\bgit:|\bgithub:|\bgitlab:|\bbitbucket:|\bfile:|\blink:"
                        r"|\bworkspace:)", re.I)
_HASH_RE = re.compile(r"^\s*--hash=sha256:[0-9a-f]+(\s*\\)?\s*$")
_LOCAL_SOURCE_RE = re.compile(
    r"\bpath\s*=|directory|editable|\"link\"\s*:\s*true|portal:|patch:|exec:", re.I)
_RESOLVED_RE = re.compile(r'"resolved"\s*:\s*"([^"]*)"')


def _added_lines(old: str, new: str) -> List[str]:
    old_lines = set(old.splitlines())
    return [l for l in new.splitlines() if l not in old_lines]


def _normal_source(line: str) -> bool:
    """No URL outside the normal registries and no git, file, link or workspace source."""
    if _SOURCE_RE.search(line):
        return False
    return all(scheme.lower() in ("http", "https") and host.lower() in REGISTRY_HOSTS
               for scheme, host in _URL_RE.findall(line))


def _lockfile(old: str, new: str) -> bool:
    """Guard rail on the text of the changed lines; a lockfile is not parsed beyond this."""
    for line in _added_lines(old, new):
        if "\\" in line or _LOCAL_SOURCE_RE.search(line) or not _normal_source(line):
            return False  # a backslash can hide a URL (\/, \u003a, \x2f)
    return True


def _resolved_ok(name: str, entry: Dict[str, Any]) -> bool:
    """The tarball URL is this package's own, at this version, on the npm registry."""
    url = entry.get("resolved")
    version = entry.get("version")
    if not isinstance(url, str) or not isinstance(version, str) or entry.get("link"):
        return False
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc not in ("registry.npmjs.org", "registry.yarnpkg.com") \
            or parts.query or parts.fragment:
        return False
    return parts.path == f"/{name}/-/{name.rsplit('/', 1)[-1]}-{version}.tgz"


def _entry_ok(name: str, entry: Any) -> bool:
    """A changed lockfile entry is this package at a registry tarball, with an integrity hash."""
    if not isinstance(entry, dict) or entry.get("link"):
        return False
    if entry.get("inBundle") is True:
        return True
    if entry.get("name", name) != name or not isinstance(entry.get("integrity"), str):
        return False  # an npm alias (`name` differs) or a missing hash
    return _resolved_ok(name, entry)


def _tree_ok(a: Any, b: Any) -> bool:
    """The lockfile v1 `dependencies` tree: every changed entry is checked, nested ones too."""
    a, b = a if isinstance(a, dict) else {}, b if isinstance(b, dict) else {}
    for name, entry in b.items():
        if not isinstance(entry, dict):
            return False
        before = a.get(name) if isinstance(a.get(name), dict) else {}
        if before != entry:
            own = {k: v for k, v in entry.items() if k != "dependencies"}
            if {k: v for k, v in before.items() if k != "dependencies"} != own \
                    and not (entry.get("inBundle") is True or _resolved_ok(name, entry)):
                return False
            if not _tree_ok(before.get("dependencies"), entry.get("dependencies")):
                return False
    return True


def _root_ok(a: Any, b: Any) -> bool:
    """The root entry may change only in its dependency maps (same packages, version ranges)."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    if not all(_same_map(a.get(k), b.get(k)) for k in _NPM_MAPS):
        return False
    for key in _NPM_MAPS:
        a, b = _without(a, key), _without(b, key)
    return a == b


def _package_lock(old: str, new: str) -> bool:
    if not _lockfile(old, new):
        return False
    for line in _added_lines(old, new):
        m = _RESOLVED_RE.search(line)
        if m and not re.match(r"^https://registry\.(npmjs\.org|yarnpkg\.com)/", m.group(1)):
            return False
    try:
        da, db = json.loads(old), json.loads(new)
        a, b = da.get("packages") or {}, db.get("packages") or {}
    except (ValueError, AttributeError):
        return False
    for key, entry in b.items():
        if a.get(key) == entry:
            continue
        if key == "":
            if not _root_ok(a.get(""), entry):
                return False
        elif "node_modules/" not in key or not _entry_ok(key.rsplit("node_modules/", 1)[-1], entry):
            return False
    return _tree_ok(da.get("dependencies"), db.get("dependencies"))


def _requirements(old: str, new: str) -> bool:
    """Each changed line is the same requirement name moving to another version."""
    a, b = old.splitlines(), new.splitlines()
    changed = pairs(a, b)
    if changed is None:
        return False
    for i, j in changed:
        x, y = a[i], b[j]
        if _HASH_RE.match(x) and _HASH_RE.match(y):  # a hash pin cannot name another source
            continue
        if _HASH_RE.match(x) or _HASH_RE.match(y):
            return False
        if y.rstrip().endswith("\\") and not (j + 1 < len(b) and _HASH_RE.match(b[j + 1])):
            return False  # a continuation may only lead into a hash line
        if (_req_name(x) is None or _req_name(x) != _req_name(y) or "@" in y
                or y.lstrip()[:1] in (".", "/", "~", "-") or "/" in y or re.search(r"\s--", y)
                or not _normal_source(y)):
            return False
    return True


def _version_value(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not re.search(r"[:/@]", value)


def _same_map(a: Any, b: Any) -> bool:
    """Same keys; every changed value is a plain version range."""
    a, b = a or {}, b or {}
    if not isinstance(a, dict) or not isinstance(b, dict) or set(a) != set(b):
        return False
    return all(a[k] == b[k] or _version_value(b[k]) for k in a)


def _without(data: Any, *path: str) -> Any:
    """`data` with the nested key at `path` removed (nothing happens if it is absent)."""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    if len(path) == 1:
        out.pop(path[0], None)
    elif path[0] in out:
        out[path[0]] = _without(out[path[0]], *path[1:])
    return out


def _get(data: Any, *path: str) -> Any:
    for key in path:
        data = data.get(key) if isinstance(data, dict) else None
    return data


def _package_json(old: str, new: str) -> bool:
    try:
        a, b = json.loads(old), json.loads(new)
    except ValueError:
        return False
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    if not all(_same_map(a.get(k), b.get(k)) for k in _NPM_MAPS):
        return False
    for key in _NPM_MAPS:
        a, b = _without(a, key), _without(b, key)
    return a == b


def _req_name(spec: Any) -> Optional[str]:
    m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", spec) if isinstance(spec, str) else None
    return re.sub(r"[-_.]+", "-", m.group(1).lower()) if m else None


def _same_requirements(a: Any, b: Any) -> bool:
    """Same requirement names (lists of PEP 508 strings); a changed one has no source form."""
    a, b = a or [], b or []
    if not isinstance(a, list) or not isinstance(b, list) or len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if x == y:
            continue
        if not isinstance(x, str) or not isinstance(y, str) or _req_name(x) != _req_name(y) \
                or _req_name(y) is None or re.search(r"[@:/]", y):
            return False
    return True


def _same_requirement_groups(a: Any, b: Any) -> bool:
    a, b = a or {}, b or {}
    if not isinstance(a, dict) or not isinstance(b, dict) or set(a) != set(b):
        return False
    return all(_same_requirements(a[k], b[k]) for k in a)


def _pyproject(old: str, new: str) -> bool:
    if sys.version_info < (3, 11):  # no tomllib: cannot tell, so not dependency-only
        return False
    import tomllib
    try:
        a, b = tomllib.loads(old), tomllib.loads(new)
    except ValueError:
        return False
    if not (_same_requirements(_get(a, "project", "dependencies"),
                               _get(b, "project", "dependencies"))
            and _same_requirement_groups(_get(a, "project", "optional-dependencies"),
                                         _get(b, "project", "optional-dependencies"))
            and _same_requirement_groups(a.get("dependency-groups"), b.get("dependency-groups"))):
        return False
    for path in (("project", "dependencies"), ("project", "optional-dependencies"),
                 ("dependency-groups",)):
        a, b = _without(a, *path), _without(b, *path)
    return a == b
