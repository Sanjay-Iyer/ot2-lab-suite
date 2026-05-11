#!/usr/bin/env python3
"""
Script 9: Experiment Log Aggregator
====================================

Collects all data (configs, plans, manifests, and analysis results)
into a single comprehensive experiment record.

Usage
-----
    python log_aggregator.py
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import pandas as pd
from typing import Any, Dict, List

from config import (
    ExperimentConfig, load_config, setup_logging,
    SUITE_DIR, OUTPUTS_DIR, LOGS_DIR,
)

logger = logging.getLogger("log_aggregator")


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate experiment data.")
    parser.add_argument("--config", type=str, default=str(SUITE_DIR / "configs" / "experiment_config.json"))
    args = parser.parse_args()

    setup_logging(LOGS_DIR)

    # 1. Load configuration
    config_path = pathlib.Path(args.config)
    cfg = load_config(config_path)

    # 2. Load other components
    from dataclasses import asdict
    record: Dict[str, Any] = {
        "metadata": {
            "experiment": cfg.experiment_name,
            "date": cfg.date,
            "operator": cfg.operator
        },
        "configuration": asdict(cfg),
        "data": {}
    }

    # Load matrix
    from config import CONFIGS_DIR
    matrix_path = CONFIGS_DIR / "sample_matrix.json"
    if matrix_path.exists():
        with open(matrix_path, "r") as f:
            record["data"]["samples"] = json.load(f)

    # Load manifest
    manifest_path = OUTPUTS_DIR / "printer_manifest.json"
    if manifest_path.exists():
        with open(manifest_path, "r") as f:
            record["data"]["manifest"] = json.load(f)

    # Load analysis if available
    analysis_path = OUTPUTS_DIR / "analysis_results.csv" # User-filled file
    if analysis_path.exists():
        df = pd.read_csv(analysis_path)
        record["data"]["analysis"] = df.to_dict(orient="records")
    else:
        logger.warning("Analysis results CSV not found.  Creating summary with plan only.")

    # Write full record
    out_path = OUTPUTS_DIR / "experiment_record.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)

    # Generate Markdown Summary
    summary_path = OUTPUTS_DIR / "experiment_summary.md"
    with open(summary_path, "w") as f:
        f.write(f"# Experiment Summary: {cfg.experiment_name}\n\n")
        f.write(f"- **Date**: {cfg.date}\n")
        f.write(f"- **Operator**: {cfg.operator}\n")
        f.write(f"- **Total Samples**: {cfg.total_samples()}\n\n")
        f.write("## Sample Inventory\n\n")
        f.write("| ID | Concentration | Size | Slot:Well |\n")
        f.write("|----|---------------|------|-----------|\n")
        for s in record["data"].get("samples", []):
            f.write(f"| {s['sample_id']} | {s['concentration_level']} | {s['size_variant']} | {s['destination_slot']}:{s['destination_well']} |\n")

    logger.info("Experiment record and summary generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
