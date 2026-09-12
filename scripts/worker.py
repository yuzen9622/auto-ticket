#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
src_dir = str(REPO_ROOT / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

# Remove scripts directory from sys.path to avoid shadowing the 'worker' package
script_dir = str(Path(__file__).resolve().parent)
while script_dir in sys.path:
    sys.path.remove(script_dir)

if __name__ == "__main__":
    import worker.__main__

    worker.__main__.main()
