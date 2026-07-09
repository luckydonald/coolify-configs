#!/usr/bin/env python3
from __future__ import annotations

import importlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

main = importlib.import_module("°split_lib.cli").main

if __name__ == "__main__":
    raise SystemExit(main())
