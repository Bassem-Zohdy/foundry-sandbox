"""Where a pack's files live in the repo: its root, and which paths ignore it."""
from pathlib import Path
from typing import List

from .config import Config, ConfigError

# Always at the repo root, whatever the pack root is: CI, docs, scripts and Semgrep rules.
REPO_LEVEL = (".github/", "docs/", "scripts/", ".semgrep/")


def pack_root(config: Config, pack_name: str) -> str:
    """The pack's directory relative to the repo (`"."` when `packRoots` does not set it)."""
    return config.pack_roots.get(pack_name, ".")


def _repo_level(rel: str) -> bool:
    return rel.startswith(REPO_LEVEL)


def placed(rel: Path, root: str) -> Path:
    """`rel` under `root`, unless it is a repo-level path."""
    if root in ("", ".") or _repo_level(rel.as_posix()):
        return rel
    return Path(root) / rel


def placed_guarded(path: str, root: str) -> str:
    """A guarded entry (`/eslint.config.mjs`) under the pack root, as written to CODEOWNERS."""
    if root in ("", ".") or _repo_level(path.lstrip("/")):
        return path
    return f"/{root}{path}"


def guarded_paths(config: Config) -> List[str]:
    """The shared layer's guarded paths, then each listed pack's (under its root): the quality gate.

    CODEOWNERS and `pack guarded` both read this. A pack that cannot be loaded fails it:
    guarded paths missing from CODEOWNERS or from the trusted restore would let a PR weaken
    the check that judges it.
    """
    from .packs import SHARED, default_packs_dir, load_pack  # lazy: packs imports this module
    if not config.packs:
        return []
    if not default_packs_dir().is_dir():
        raise ConfigError([f"packs {', '.join(config.packs)}: the packs directory is not "
                           "available, so their guarded paths cannot be listed"])
    return load_pack(SHARED).guarded + [placed_guarded(g, pack_root(config, name))
                                        for name in config.packs for g in load_pack(name).guarded]
