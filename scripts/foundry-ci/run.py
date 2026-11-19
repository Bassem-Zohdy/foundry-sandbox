#!/usr/bin/env python3
"""Foundry CI entry point. Vendored by `foundry template apply`; do not edit."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from foundry_cli import commands  # noqa: E402
from foundry_cli.cli import main  # noqa: E402

commands.register_all()

sys.exit(main(sys.argv[1:]))
