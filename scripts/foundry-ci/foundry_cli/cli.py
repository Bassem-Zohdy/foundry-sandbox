"""Foundry CLI entry point and subcommand registry."""
import argparse
import sys
import traceback
from typing import Callable, Dict, List, Tuple

from . import __version__

Handler = Callable[[argparse.Namespace], int]
Configure = Callable[[argparse.ArgumentParser], None]

_REGISTRY: Dict[str, Tuple[Handler, Configure]] = {}


def register(name: str, handler: Handler, configure: Configure) -> None:
    """Register a command. `name` may be space-separated ("config check").

    Raises ValueError if the name clashes with an existing command or with
    an existing group prefix (e.g. "demo" vs "demo a").
    """
    key = " ".join(name.split())
    if not key:
        raise ValueError("command name must not be empty")
    words = key.split()
    for existing in _REGISTRY:
        other = existing.split()
        if words == other:
            raise ValueError(f"command already registered: {key!r}")
        n = min(len(words), len(other))
        if words[:n] == other[:n]:
            raise ValueError(
                f"command {key!r} conflicts with {existing!r}: "
                "a name cannot be both a command and a command group"
            )
    _REGISTRY[key] = (handler, configure)


def is_registered(name: str) -> bool:
    return " ".join(name.split()) in _REGISTRY


def _build_parser() -> argparse.ArgumentParser:
    # Shared by every leaf parser so `foundry config check --debug` works.
    # SUPPRESS keeps a missing flag from overwriting the top-level value.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--debug", action="store_true", default=argparse.SUPPRESS,
        help="show tracebacks",
    )
    parser = argparse.ArgumentParser(prog="foundry")
    parser.add_argument("--version", action="version", version=f"foundry {__version__}")
    parser.add_argument("--debug", action="store_true", help="show tracebacks")
    top = parser.add_subparsers(dest="_cmd0", metavar="<command>")
    top.required = True
    groups: Dict[Tuple[str, ...], argparse._SubParsersAction] = {(): top}
    parsers: Dict[Tuple[str, ...], argparse.ArgumentParser] = {}
    for name in sorted(_REGISTRY):
        handler, configure = _REGISTRY[name]
        words = tuple(name.split())
        for i in range(len(words)):
            prefix = words[: i + 1]
            if prefix in parsers:
                continue
            parent = groups[words[:i]]
            is_leaf = i == len(words) - 1
            sub = parent.add_parser(
                words[i], parents=[common] if is_leaf else []
            )
            parsers[prefix] = sub
            if i < len(words) - 1:
                nxt = sub.add_subparsers(dest=f"_cmd{i + 1}", metavar="<command>")
                nxt.required = True
                groups[prefix] = nxt
        leaf = parsers[words]
        configure(leaf)
        leaf.set_defaults(_handler=handler)
    return parser


def main(argv: List[str]) -> int:
    parser = _build_parser()
    try:
        ns = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    try:
        return int(ns._handler(ns))
    except Exception as e:  # noqa: BLE001 - last-resort boundary for all handlers
        if getattr(ns, "debug", False):
            traceback.print_exc()
        else:
            print(f"foundry: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
