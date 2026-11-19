"""`foundry image args <suffix>`: the build args foundry.json `images` gives one image.

foundry-image.yml passes each printed line to `docker build` as one argument. It reads
foundry.json alone, so it works in the vendored CI copy, which ships no packs.
"""
import argparse
import sys
from pathlib import Path

from .cli import register
from .config import ConfigError, load_config


def _configure(p: argparse.ArgumentParser) -> None:
    p.add_argument("suffix", help="the image suffix: <suffix> in Dockerfile.<suffix>")
    p.add_argument("--repo", default=".", help="repo root containing foundry.json")


def _run_args(ns: argparse.Namespace) -> int:
    """One `--build-arg=NAME=VALUE` per line; nothing when the image has none."""
    try:
        config = load_config(Path(ns.repo), check_packs=False)
    except ConfigError as e:
        for problem in e.problems:
            print(problem, file=sys.stderr)
        return 2
    for name, value in config.images.get(ns.suffix, {}).items():
        print(f"--build-arg={name}={value}")
    return 0


def register_commands() -> None:
    register("image args", _run_args, _configure)
