#!/usr/bin/env python3
"""
Script 8: Analysis Template Generator
======================================

Creates a blank CSV form for recording experimental results (droplet diameter,
coverage, etc.) after printing and imaging.

Usage
-----
    python analysis_planner.py
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import pathlib
import sys
from typing import Any, Dict, List

from config import (
    ExperimentConfig, load_config, setup_logging,
    SUITE_DIR, OUTPUTS_DIR, LOGS_DIR,
)

logger = logging.getLogger("analysis_planner")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate analysis template.")
    parser.add_argument("--config", type=str, default=str(SUITE_DIR / "configs" / "experiment_config.json"))
    args = parser.parse_args()

    setup_logging(LOGS_DIR)

    manifest_path = OUTPUTS_DIR / "printer_manifest.json"
    if not manifest_path.is_file():
        logger.error("Manifest not found")
        return 1
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    path = OUTPUTS_DIR / "analysis_template.csv"
    fieldnames = [
        "sample_id", "tray_position", "replicate_number",
        "droplet_diameter_um", "coverage_percent", "color_intensity",
        "edge_uniformity", "substrate_adhesion", "notes"
    ]

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for item in manifest:
            for r in range(1, item["replicates"] + 1):
                writer.writerow({
                    "sample_id": item["sample_id"],
                    "tray_position": item["tray_position"],
                    "replicate_number": r,
                })

    logger.info("Analysis template written to %s", path)

    # Write metadata
    from config import CONFIGS_DIR
    matrix_path = CONFIGS_DIR / "sample_matrix.json"
    meta_path = CONFIGS_DIR / "analysis_metadata.json"
    metadata = {
        "metrics": {
            "droplet_diameter_um": "Measured in microns via microscopy",
            "coverage_percent": "0-100% area coverage",
            "color_intensity": "0-255 grayscale value",
            "edge_uniformity": "1-5 rating (5 is best)",
            "substrate_adhesion": "Pass/Fail"
        }
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
