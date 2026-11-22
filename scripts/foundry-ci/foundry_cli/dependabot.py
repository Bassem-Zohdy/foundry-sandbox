"""The `{{dependabot.updates}}` block of `.github/dependabot.yml`.

Scope (R34): libraries and actions get minor and patch updates grouped weekly and no major
PRs; the language and runtime base images (the `docker` ecosystem) get patch updates only.
Dependabot `ignore` rules do that; they may also suppress a security update that needs a
major (or an image minor) version (R34).
"""
from typing import List, Set, Tuple

from .config import Config
from .pack_layout import pack_root
from .packs import enabled_components

_MAJOR = "version-update:semver-major"
_MINOR = "version-update:semver-minor"
_LIBRARY_TYPES = ("minor", "patch")
_IMAGE_TYPES = ("patch",)

_ENTRY = """\
  - package-ecosystem: "{eco}"
    directory: "{directory}"
    schedule:
      interval: weekly
    groups:
      {group}:
        update-types:
{types}
    ignore:
      - dependency-name: "*"
        update-types:
{ignored}"""


def _items(values: Tuple[str, ...], indent: int) -> str:
    return "\n".join(f'{" " * indent}- "{v}"' for v in values)


def dependabot_updates(config: Config) -> str:
    """One weekly entry per (ecosystem, directory), sorted, plus github-actions at `/`."""
    pairs: Set[Tuple[str, str]] = {("/", "github-actions")}
    for pack, comp in enabled_components(config):
        root = pack_root(config, pack.name)
        directory = "/" if root == "." else "/" + root
        pairs.update((directory, eco) for eco in comp.dependabot)
    entries: List[str] = []
    for directory, eco in sorted(pairs):
        image = eco == "docker"
        group = "actions" if eco == "github-actions" else eco
        entries.append(_ENTRY.format(
            eco=eco, directory=directory,
            group=f"{group}-patch" if image else f"{group}-minor-patch",
            types=_items(_IMAGE_TYPES if image else _LIBRARY_TYPES, 10),
            ignored=_items((_MAJOR, _MINOR) if image else (_MAJOR,), 10)))
    return "\n".join(entries)
