#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "markdownify>=0.13,<2",
# ]
# ///
from __future__ import annotations

import importlib
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent))

main = importlib.import_module("°dllink_lib.cli").main


if __name__ == "__main__":
    raise SystemExit(main())
