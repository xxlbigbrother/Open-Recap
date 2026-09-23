#!/usr/bin/env python3
"""Start or resume OpenRecap from any working directory."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from recap_pipeline import main

if __name__ == "__main__":
    raise SystemExit(main())
