#!/usr/bin/env python3
"""
Script 4: Size Variant Planner
================================

Creates a concentration × size matrix, assigns unique sample IDs,
and maps each to a destination well in the output racks.

Usage
-----
    python size_variant_planner.py
    python size_variant_planner.py --config outputs/experiment_config.json
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import pathlib
import sys
from typing import Any, Dict, List, Tuple

from config import (
    ExperimentConfig, load_config, setup_logging,
    SUITE_DIR, OUTPUTS_DIR, LOGS_DIR,
)

logger = logging.getLogger("size_variant_planner")

# ── Size pool slot mapping ────────────────────────────────────────────
SIZE_POOL_SLOTS = {
    "small": 4,
    "medium": 5,
    "large": 6,
}

# ── Aggregation risk thresholds ───────────────────────────────────────
HIGH_CONC_THRESHOLD_UGML = 50.0  # Above this, large particles may aggregate


# =====================================================================
#  Well assignment for output racks
# =====================================================================

def generate_output_wells(max_per_rack: int = 24) -> List[Tuple[int, str]]:
    """Generate (slot, well) pairs for output racks 7 and 8.

    24-well racks: rows A–D, columns 1–6.
    """
    rows = "ABCD"
    cols = range(1, 7)
    wells: List[Tuple[int, str]] = []

    for slot in [7, 8]:
        for r in rows:
            for c in cols:
                wells.append((slot, f"{r}{c}"))

    return wells


# =====================================================================
#  Matrix generation
# =====================================================================

def build_sample_matrix(
    cfg: ExperimentConfig,
    dilution_plan: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Create the concentration × size matrix.

    Each entry gets a unique sample ID and a destination well.
    """
    matrix: List[Dict[str, Any]] = []
    output_wells = generate_output_wells()
    well_idx = 0

    sample_counter = 1

    for dil in dilution_plan:
        for size in cfg.size_variants:
            sample_id = f"S{sample_counter:03d}"

            if well_idx >= len(output_wells):
                logger.error(
                    "Ran out of output wells at sample %s.", sample_id
                )
                break

            slot, well = output_wells[well_idx]
            well_idx += 1

            # Source info
            size_pool_slot = SIZE_POOL_SLOTS.get(size, 0)

            entry = {
                "sample_id": sample_id,
                "concentration_level": dil["dilution_name"],
                "dilution_factor": dil["dilution_factor"],
                "final_concentration_ugml": dil["final_concentration_ugml"],
                "size_variant": size,
                "source_dilution_slot": dil["output_slot"],
                "source_dilution_well": dil["output_well"],
                "source_size_pool_slot": size_pool_slot,
                "destination_slot": slot,
                "destination_well": well,
                "volume_ul": cfg.target_final_volume_ul,
                "final_description": (
                    f"{dil['dilution_name']} conc. + {size} NS"
                ),
            }

            matrix.append(entry)
            sample_counter += 1

    return matrix


# =====================================================================
#  Validation
# =====================================================================

def validate_matrix(
    matrix: List[Dict[str, Any]],
    cfg: ExperimentConfig,
) -> Tuple[List[str], List[str]]:
    """Validate the sample matrix.  Returns (errors, warnings)."""
    errors: List[str] = []
    warnings: List[str] = []

    # ── Check well uniqueness ─────────────────────────────────────────
    wells_used: Dict[str, str] = {}
    for entry in matrix:
        key = f"{entry['destination_slot']}:{entry['destination_well']}"
        if key in wells_used:
            errors.append(
                f"Duplicate destination: {key} assigned to both "
                f"{wells_used[key]} and {entry['sample_id']}."
            )
        wells_used[key] = entry["sample_id"]

    # ── Check capacity ────────────────────────────────────────────────
    max_capacity = 48  # 2 × 24-well racks
    if len(matrix) > max_capacity:
        errors.append(
            f"Sample count ({len(matrix)}) exceeds output capacity "
            f"({max_capacity})."
        )

    # ── Aggregation warnings ──────────────────────────────────────────
    for entry in matrix:
        if (entry["final_concentration_ugml"] >= HIGH_CONC_THRESHOLD_UGML
                and entry["size_variant"] == "large"):
            warnings.append(
                f"{entry['sample_id']}: high concentration "
                f"({entry['final_concentration_ugml']} µg/mL) + large "
                f"particles — aggregation risk."
            )

    return errors, warnings


# =====================================================================
#  Output
# =====================================================================

def write_matrix_csv(
    matrix: List[Dict[str, Any]],
    output_dir: pathlib.Path,
) -> None:
    """Write sample matrix as CSV."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "sample_matrix.csv"

    fieldnames = [
        "sample_id", "concentration_level", "dilution_factor",
        "final_concentration_ugml", "size_variant",
        "source_dilution_slot", "source_dilution_well",
        "source_size_pool_slot", "destination_slot", "destination_well",
        "volume_ul", "final_description",
    ]

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(matrix)

    logger.info("Sample matrix CSV written to %s", path)


def write_matrix_json(
    matrix: List[Dict[str, Any]],
    output_dir: pathlib.Path,
) -> None:
    """Write sample matrix as JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "sample_matrix.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(matrix, f, indent=2)
    logger.info("Sample matrix JSON written to %s", path)


# =====================================================================
#  CLI
# =====================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate concentration × size sample matrix."
    )
    parser.add_argument(
        "--config", type=str,
        default=str(SUITE_DIR / "configs" / "experiment_config.json"),
    )
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    setup_logging(LOGS_DIR, args.log_level)

    # ── Load config ───────────────────────────────────────────────────
    config_path = pathlib.Path(args.config)
    if not config_path.is_file():
        logger.error("Config not found: %s", config_path)
        return 1
    cfg = load_config(config_path)

    # ── Load dilution plan ────────────────────────────────────────────
    from config import CONFIGS_DIR
    dilution_path = CONFIGS_DIR / "experiment_config.json"
    if not dilution_path.is_file():
        logger.error("Dilution plan not found: %s.  Run dilution_planner.py first.",
                      dilution_path)
        return 1

    with open(dilution_path, "r", encoding="utf-8") as f:
        dilution_plan = json.load(f)

    logger.info("Loaded %d dilution steps.", len(dilution_plan))

    # ── Build matrix ──────────────────────────────────────────────────
    matrix = build_sample_matrix(cfg, dilution_plan)
    logger.info("Sample matrix: %d samples.", len(matrix))

    # ── Validate ──────────────────────────────────────────────────────
    errors, warnings = validate_matrix(matrix, cfg)

    for w in warnings:
        logger.warning("⚠ %s", w)
    for e in errors:
        logger.error("✗ %s", e)

    if errors:
        logger.error("Matrix validation failed.")
        return 1

    # ── Summary ───────────────────────────────────────────────────────
    logger.info("─" * 60)
    logger.info("  %-6s %-12s %-8s %-5s %-5s",
                "ID", "Conc", "Size", "Slot", "Well")
    for entry in matrix:
        logger.info("  %-6s %-12s %-8s %-5d %-5s",
                     entry["sample_id"], entry["concentration_level"],
                     entry["size_variant"], entry["destination_slot"],
                     entry["destination_well"])
    logger.info("─" * 60)

    # ── Write outputs ─────────────────────────────────────────────────
    write_matrix_csv(matrix, CONFIGS_DIR)
    write_matrix_json(matrix, CONFIGS_DIR)

    logger.info("Size variant planning complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
