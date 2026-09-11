#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

# Remove scripts directory from sys.path to avoid shadowing the 'worker' package
script_dir = str(Path(__file__).resolve().parent)
while script_dir in sys.path:
    sys.path.remove(script_dir)

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from worker.__main__ import main

if __name__ == "__main__":
    main()
