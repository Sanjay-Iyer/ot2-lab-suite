#!/usr/bin/env python3
"""
Script 7: Printer Manifest Builder
===================================

Assigns prepared samples to positions on the printer input tray (slot 9)
and generates a manifest for the external printer.

Usage
-----
    python print_prep.py
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

logger = logging.getLogger("print_prep")


# =====================================================================
#  Tray layout
# =====================================================================

def generate_tray_wells() -> List[str]:
    """Generate 96-well plate names (A1–H12)."""
    rows = "ABCDEFGH"
    return [f"{r}{c}" for c in range(1, 13) for r in rows]


# =====================================================================
#  Manifest generation
# =====================================================================

def build_printer_manifest(
    cfg: ExperimentConfig,
    sample_matrix: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Map samples to printer tray positions."""
    manifest: List[Dict[str, Any]] = []
    wells = generate_tray_wells()
    well_idx = 0

    for sample in sample_matrix:
        for vol_nl in cfg.print_volumes_nl:
            if well_idx >= len(wells):
                logger.error("Ran out of tray wells!")
                break

            tray_pos = wells[well_idx]
            well_idx += 1

            manifest.append({
                "sample_id": sample["sample_id"],
                "concentration_level": sample["concentration_level"],
                "size_variant": sample["size_variant"],
                "source_slot": sample["destination_slot"],
                "source_well": sample["destination_well"],
                "tray_position": tray_pos,
                "print_volume_nl": vol_nl,
                "replicates": 3,  # Default per spec
                "expected_diameter_um": round(vol_nl * 0.5, 1), # Heuristic
            })

    return manifest


# =====================================================================
#  Output
# =====================================================================

def write_manifest_csv(manifest: List[Dict[str, Any]], output_dir: pathlib.Path) -> None:
    path = output_dir / "printer_manifest.csv"
    fieldnames = [
        "sample_id", "concentration_level", "size_variant",
        "tray_position", "print_volume_nl", "replicates",
        "expected_diameter_um"
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(manifest)
    logger.info("Manifest CSV written to %s", path)


def write_manifest_json(manifest: List[Dict[str, Any]], output_dir: pathlib.Path) -> None:
    path = output_dir / "printer_manifest.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    logger.info("Manifest JSON written to %s", path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate printer manifest.")
    parser.add_argument("--config", type=str, default=str(SUITE_DIR / "configs" / "experiment_config.json"))
    args = parser.parse_args()

    setup_logging(LOGS_DIR)

    config_path = pathlib.Path(args.config)
    if not config_path.is_file():
        logger.error("Config not found")
        return 1
    cfg = load_config(config_path)

    from config import CONFIGS_DIR
    matrix_path = CONFIGS_DIR / "sample_matrix.json"
    if not matrix_path.is_file():
        logger.error("Matrix not found")
        return 1
    with open(matrix_path, "r", encoding="utf-8") as f:
        sample_matrix = json.load(f)

    manifest = build_printer_manifest(cfg, sample_matrix)
    logger.info("Manifest built with %d entries.", len(manifest))

    write_manifest_csv(manifest, OUTPUTS_DIR)
    write_manifest_json(manifest, OUTPUTS_DIR)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
