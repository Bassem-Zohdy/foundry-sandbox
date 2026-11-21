"""Foundry's Python code limits, written as SARIF (tool `foundry-py-limits`).

ruff has no file-length or function-length rule, so these come from the `ast` module:

    max-lines                400 lines per file (error)
    foundry-soft/max-lines   300 lines per file (warning: listed, not counted)
    max-lines-per-function   40 lines, from `def` to the function's last line
    max-depth                3 nested `if`, `for`, `while`, `with`, `try` or `match` blocks; an
                             `elif` is not a new block, and a nested function starts again at 0
    max-params               4, not counting a leading `self` or `cls`, nor a parameter that
                             FastAPI injects: a `Depends(...)` default or an
                             `Annotated[..., Depends(...)]` annotation
    no-grab-bag              no module or package named `utils`, `helpers` or `misc`

Usage: python3 py_limits.py <root> <out.sarif> [--files <file>...]
`--files` (repo-relative, as the pre-commit hook passes them) limits the scan to those files.
Exit: 0 the SARIF was written, 2 foundry.json, git or a scanned file could not be read (a syntax
error names the file and line).
"""
import ast
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import foundry_json as fj  # noqa: E402

TOOL = "foundry-py-limits"
RULES = {"max-lines": "error", "foundry-soft/max-lines": "warning",
         "max-lines-per-function": "error", "max-depth": "error", "max-params": "error",
         "no-grab-bag": "error"}
MAX_LINES, SOFT_LINES, MAX_FUNCTION, MAX_DEPTH, MAX_PARAMS = 400, 300, 40, 3, 4
GRAB_BAGS = {"utils", "helpers", "misc"}
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
BLOCKS = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try,
          getattr(ast, "TryStar", ast.Try), ast.Match)


def _name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else ""


def _is_depends(node) -> bool:
    return isinstance(node, ast.Call) and _name(node.func) == "Depends"


def _injected(arg: ast.arg, default) -> bool:
    if _is_depends(default):
        return True
    ann = arg.annotation
    if not (isinstance(ann, ast.Subscript) and _name(ann.value) == "Annotated"):
        return False
    items = ann.slice.elts if isinstance(ann.slice, ast.Tuple) else [ann.slice]
    return any(_is_depends(item) for item in items[1:])


def count_params(fn) -> int:
    a = fn.args
    positional = a.posonlyargs + a.args
    defaults = [None] * (len(positional) - len(a.defaults)) + list(a.defaults)
    pairs = list(zip(positional, defaults, strict=True)) + list(zip(a.kwonlyargs, a.kw_defaults, strict=True))
    if positional and positional[0].arg in ("self", "cls"):
        pairs = pairs[1:]
    n = sum(1 for arg, default in pairs if not _injected(arg, default))
    return n + (a.vararg is not None) + (a.kwarg is not None)


def _is_elif(parent, child) -> bool:
    return (isinstance(parent, ast.If) and isinstance(child, ast.If)
            and parent.orelse == [child] and child.col_offset == parent.col_offset)


def deep_blocks(tree) -> list:
    """The blocks nested deeper than MAX_DEPTH inside their function (or the module)."""
    found = []

    def walk(node, depth: int) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, FUNCTIONS):
                walk(child, 0)
            elif isinstance(child, BLOCKS) and not _is_elif(node, child):
                if depth + 1 > MAX_DEPTH:
                    found.append(child)
                walk(child, depth + 1)
            else:
                walk(child, depth)

    walk(tree, 0)
    return found


def _function_results(tree, uri: str) -> list:
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        length = node.end_lineno - node.lineno + 1
        if length > MAX_FUNCTION:
            out.append(("max-lines-per-function", uri, node.lineno,
                        f"function '{node.name}' has {length} lines (max {MAX_FUNCTION})"))
        params = count_params(node)
        if params > MAX_PARAMS:
            out.append(("max-params", uri, node.lineno,
                        f"function '{node.name}' has {params} parameters (max {MAX_PARAMS})"))
    return out


def _file_results(uri: str, lines: int, rel_to_root: str) -> list:
    out = []
    if lines > MAX_LINES:
        out.append(("max-lines", uri, MAX_LINES + 1, f"file has {lines} lines (max {MAX_LINES})"))
    if lines > SOFT_LINES:
        out.append(("foundry-soft/max-lines", uri, SOFT_LINES + 1,
                    f"file has {lines} lines (soft limit {SOFT_LINES})"))
    parts = rel_to_root.split("/")
    names = parts[:-1] + ([] if parts[-1] == "__init__.py" else [parts[-1][:-3]])
    bag = next((n for n in names if n in GRAB_BAGS), None)
    if bag:
        out.append(("no-grab-bag", uri, 1, f"'{bag}' is a grab bag; name the module after what it does"))
    return out


def parse(repo: Path, uri: str):
    """(source, tree) of a repo-relative file; SyntaxError names the file and line."""
    try:
        text = (repo / uri).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise fj.ConfigError(f"cannot read {uri}: {e}") from None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return text, ast.parse(text, filename=uri)
        except (SyntaxError, ValueError) as e:
            line = getattr(e, "lineno", None) or 1
            raise fj.ConfigError(f"{uri}:{line}: syntax error: {getattr(e, 'msg', e)}") from None


def check_file(repo: Path, lead: str, uri: str) -> list:
    text, tree = parse(repo, uri)
    results = _file_results(uri, len(text.splitlines()), uri[len(lead):])
    results += _function_results(tree, uri)
    results += [("max-depth", uri, node.lineno, f"blocks nested more than {MAX_DEPTH} deep")
                for node in deep_blocks(tree)]
    return results


def select(repo: Path, root: Path, given) -> list:
    files = fj.python_files(repo, root, fj.other_roots())
    if given is None:
        return files
    wanted = {g[2:] if g.startswith("./") else g for g in given}
    return [f for f in files if f in wanted]


def main(argv: list) -> int:
    if len(argv) < 2 or (len(argv) > 2 and argv[2] != "--files"):
        print("usage: py_limits.py <root> <out.sarif> [--files <file>...]", file=sys.stderr)
        return 2
    try:
        root = Path(argv[0]).resolve()
        repo = fj.repo_of(root)
        lead = fj.root_prefix(repo, root)
        files = select(repo, root, argv[3:] if len(argv) > 2 else None)
        results = [r for f in files for r in check_file(repo, lead, f)]
    except (fj.ConfigError, ValueError) as e:
        print(f"py_limits: {e}", file=sys.stderr)
        return 2
    fj.write_sarif(argv[1], (TOOL, RULES), results, files)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
