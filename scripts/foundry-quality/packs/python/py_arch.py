"""Foundry's Python architecture rules, written as SARIF (tool `foundry-py-arch`).

The package is `python.package` in foundry.json (a folder in the pack root) and the modules are
`modules`; each module is `<pkg>/<module>/` with `domain/`, `application/`, `adapters/` and
`public/` inside. The rule names match the TypeScript packs' dependency-cruiser rules:

    context-public-interface   outside `<pkg>.<m>`, import only `<pkg>.<m>` itself (its
                               `__init__`) or `<pkg>.<m>.public.*`
    domain-is-pure             a `domain` folder imports no `application` or `adapters` code and
                               no fastapi, starlette, sqlalchemy, celery, redis or httpx
    application-not-adapters   an `application` folder imports no `adapters` code
    no-circular                an import cycle between files, reported once, on its first file;
                               imports under `if TYPE_CHECKING:` are not counted for cycles

Only files inside the package are checked. Without `python.package` or `modules` the rules are
off: it says so and writes a SARIF file with no results.

Usage: python3 py_arch.py <root> <out.sarif>
Exit: 0 the SARIF was written, 2 foundry.json, git or a scanned file could not be read.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import foundry_json as fj  # noqa: E402
from py_limits import parse  # noqa: E402

TOOL = "foundry-py-arch"
RULES = {"context-public-interface": "error", "domain-is-pure": "error",
         "application-not-adapters": "error", "no-circular": "error"}
FRAMEWORKS = {"fastapi", "starlette", "sqlalchemy", "celery", "redis", "httpx"}


def module_name(uri: str, lead: str) -> str:
    parts = uri[len(lead):-3].split("/")
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _type_checking(node) -> bool:
    if not isinstance(node, ast.If):
        return False
    return "TYPE_CHECKING" in (getattr(node.test, "id", None), getattr(node.test, "attr", None))


def _resolve_from(node: ast.ImportFrom, name: str, is_pkg: bool) -> str:
    if node.level == 0:
        return node.module or ""
    base = name.split(".") if is_pkg else name.split(".")[:-1]
    if node.level - 1 > len(base):
        return ""
    base = base[:len(base) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def imports_of(tree, name: str, is_pkg: bool, known: set) -> list:
    """(line, imported module, under TYPE_CHECKING) for every import in the file."""
    out = []

    def visit(node, guarded: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Import):
                out.extend((child.lineno, a.name, guarded) for a in child.names)
            elif isinstance(child, ast.ImportFrom):
                base = _resolve_from(child, name, is_pkg)
                for a in child.names:
                    sub = f"{base}.{a.name}"
                    out.append((child.lineno, sub if sub in known else base, guarded))
            visit(child, guarded or _type_checking(child))

    visit(tree, False)
    return [i for i in out if i[1]]


def _within(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _segments(name: str, pkg: str) -> list:
    return name.split(".")[1:] if _within(name, pkg) else []


def boundary_rule(src: str, target: str, pkg: str, modules: list) -> str:
    """The rule `src` importing `target` breaks, or ''."""
    for m in modules:
        ctx = f"{pkg}.{m}"
        if (_within(target, ctx) and not _within(src, ctx) and target != ctx
                and not _within(target, ctx + ".public")):
            return "context-public-interface"
    src_parts, target_parts = _segments(src, pkg), _segments(target, pkg)
    if "domain" in src_parts:
        if {"application", "adapters"} & set(target_parts) or target.split(".")[0] in FRAMEWORKS:
            return "domain-is-pure"
    if "application" in src_parts and "adapters" in target_parts:
        return "application-not-adapters"
    return ""


class _Tarjan:
    """Tarjan's strongly connected components, without recursion."""

    def __init__(self, graph: dict):
        self.graph, self.index, self.low = graph, {}, {}
        self.stack, self.on_stack, self.components = [], set(), []

    def _push(self, node: str, work: list) -> None:
        self.index[node] = self.low[node] = len(self.index)
        self.stack.append(node)
        self.on_stack.add(node)
        work.append((node, iter(sorted(self.graph[node]))))

    def _pop_component(self, node: str) -> None:
        component = []
        while not component or component[-1] != node:
            member = self.stack.pop()
            self.on_stack.discard(member)
            component.append(member)
        if len(component) > 1:
            self.components.append(component)

    def _step(self, work: list) -> None:
        node, edges = work[-1]
        for nxt in edges:
            if nxt not in self.index:
                self._push(nxt, work)
                return
            if nxt in self.on_stack:
                self.low[node] = min(self.low[node], self.index[nxt])
        work.pop()
        if work:
            parent = work[-1][0]
            self.low[parent] = min(self.low[parent], self.low[node])
        if self.low[node] == self.index[node]:
            self._pop_component(node)

    def run(self) -> list:
        """The components with more than one node."""
        for start in sorted(self.graph):
            if start not in self.index:
                work = []
                self._push(start, work)
                while work:
                    self._step(work)
        return self.components


def _cycle_path(graph: dict, members: set, first: str) -> list:
    """The shortest cycle through `first` inside one strongly connected component."""
    paths, seen = [[first]], {first}
    while paths:
        path = paths.pop(0)
        for nxt in sorted(graph[path[-1]]):
            if nxt == first:
                return path + [first]
            if nxt in members and nxt not in seen:
                seen.add(nxt)
                paths.append(path + [nxt])
    return [first, first]


def cycle_results(graph: dict, lines: dict, uris: dict) -> list:
    out = []
    for component in _Tarjan(graph).run():
        first = min(component, key=lambda n: uris[n])
        path = _cycle_path(graph, set(component), first)
        out.append(("no-circular", uris[first], lines[(first, path[1])],
                    "import cycle: " + " -> ".join(path)))
    return out


def analyse(repo: Path, lead: str, files: list, arch: tuple) -> list:
    pkg, modules = arch
    names = {f: module_name(f, lead) for f in files}
    known = {".".join(n.split(".")[:i]) for n in names.values() for i in range(1, n.count(".") + 2)}
    by_name = {n: f for f, n in names.items()}
    graph, lines, results = {n: set() for n in by_name}, {}, []
    for f, src in names.items():
        _, tree = parse(repo, f)
        for line, target, guarded in imports_of(tree, src, f.endswith("__init__.py"), known):
            rule = boundary_rule(src, target, pkg, modules)
            if rule:
                results.append((rule, f, line, f"{src} imports {target}"))
            if target in by_name and target != src and not guarded:
                graph[src].add(target)
                lines.setdefault((src, target), line)
    return results + cycle_results(graph, lines, {n: f for n, f in by_name.items()})


def main(argv: list) -> int:
    if len(argv) != 2:
        print("usage: py_arch.py <root> <out.sarif>", file=sys.stderr)
        return 2
    try:
        root = Path(argv[0]).resolve()
        repo = fj.repo_of(root)
        config = fj.load(repo)
        pkg = (config.get("python") or {}).get("package")
        modules = [m for m in config.get("modules") or [] if isinstance(m, str)]
        if not pkg or not modules:
            print("py_arch: the architecture rules are off (set python.package and modules in foundry.json)")
            fj.write_sarif(argv[1], (TOOL, RULES), [], [])
            return 0
        lead = fj.root_prefix(repo, root)
        if not (root / pkg).is_dir():
            raise fj.ConfigError(f"python.package '{pkg}' is not a folder in {lead or './'}")
        files = [f for f in fj.python_files(repo, root, fj.other_roots()) if f.startswith(f"{lead}{pkg}/")]
        results = analyse(repo, lead, files, (pkg, modules))
    except (fj.ConfigError, ValueError) as e:
        print(f"py_arch: {e}", file=sys.stderr)
        return 2
    fj.write_sarif(argv[1], (TOOL, RULES), results, files)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
