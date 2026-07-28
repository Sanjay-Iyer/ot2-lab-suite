#!/usr/bin/env python
"""Run the Raman analysis described entirely by configs/raman_analysis.yaml."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

RAMAN_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(RAMAN_ROOT / "src"))

from raman_lib.analysis_workflow import run_analysis  # noqa: E402
from raman_lib.workflow_config import load_analysis_config  # noqa: E402

DEFAULT_CONFIG = RAMAN_ROOT / "configs" / "raman_analysis.yaml"


def main() -> int:
    """Load the fixed YAML path and run without command-line options."""
    try:
        config = load_analysis_config(DEFAULT_CONFIG)
        run_dir, summary = run_analysis(config, RAMAN_ROOT)
    except Exception as exc:
        logging.basicConfig(level=logging.ERROR, format="%(levelname)s: %(message)s")
        logging.exception("Raman analysis failed: %s", exc)
        return 1
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logging.info("Raman results: %s", run_dir)
    return 1 if summary["n_failed"] or summary["batch_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
