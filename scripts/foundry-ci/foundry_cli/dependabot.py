"""The `{{dependabot.updates}}` block of `.github/dependabot.yml`."""
from typing import List, Set, Tuple

from .config import Config
from .pack_layout import pack_root
from .packs import enabled_components

_ENTRY = """\
  - package-ecosystem: "{eco}"
    directory: "{directory}"
    schedule:
      interval: weekly
    groups:
      {group}-minor-patch:
        update-types:
          - "minor"
          - "patch\""""


def dependabot_updates(config: Config) -> str:
    """One weekly entry per (ecosystem, directory), sorted, plus github-actions at `/`."""
    pairs: Set[Tuple[str, str]] = {("/", "github-actions")}
    for pack, comp in enabled_components(config):
        root = pack_root(config, pack.name)
        directory = "/" if root == "." else "/" + root
        pairs.update((directory, eco) for eco in comp.dependabot)
    entries: List[str] = []
    for directory, eco in sorted(pairs):
        group = "actions" if eco == "github-actions" else eco
        entries.append(_ENTRY.format(eco=eco, directory=directory, group=group))
    return "\n".join(entries)
