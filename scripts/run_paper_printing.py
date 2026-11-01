#!/usr/bin/env python3
"""Preview, build or simulate YAML-driven printing using NanoDrop's existing engine."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agents.dye_demo.manual import main

if __name__ == "__main__":
    raise SystemExit(main("printing"))
