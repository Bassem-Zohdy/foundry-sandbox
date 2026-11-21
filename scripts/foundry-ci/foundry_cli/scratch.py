"""Foundry's scratch area: `<git-common-dir>/foundry`, never in the work tree."""
import subprocess
from pathlib import Path


def scratch_dir(repo: Path) -> Path:
    """Return (creating it if needed) the scratch directory for `repo`.

    Uses the git common dir so every linked worktree shares the main repo's area.
    Raises ValueError outside a git repository.
    """
    repo = Path(repo)
    try:
        p = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=repo,
                           capture_output=True, text=True)
    except OSError as e:
        raise ValueError(f"cannot run git in {repo}: {e}")
    if p.returncode != 0:
        raise ValueError(f"not a git repository: {repo}")
    common = Path(p.stdout.strip())
    if not common.is_absolute():
        common = repo / common
    d = common.resolve() / "foundry"
    d.mkdir(parents=True, exist_ok=True)
    return d
