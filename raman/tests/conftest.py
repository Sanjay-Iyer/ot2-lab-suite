"""Test-path setup for the standalone Raman package."""
from __future__ import annotations

import sys
from pathlib import Path

RAMAN_ROOT = Path(__file__).resolve().parents[1]
SRC = RAMAN_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
