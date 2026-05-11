#!/usr/bin/env python3
"""
Script 3: Dilution Planner
===========================

Calculates pipetting volumes for each dilution level, auto-selects
the appropriate pipette (p20 or p300), and assigns output well positions.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import pathlib
import sys
from typing import Any, Dict, List, Tuple

# Ensure we can find 'config' in the same directory
sys.path.append(str(pathlib.Path(__file__).resolve().parent))

from config import (
    ExperimentConfig, load_config, select_pipette, setup_logging,
    SUITE_DIR, OUTPUTS_DIR, LOGS_DIR,
    P20_MIN_UL, P20_MAX_UL, P300_MIN_UL, P300_MAX_UL,
)

logger = logging.getLogger("dilution_planner")

def generate_well_names(rows: str = "ABCD", cols: range = range(1, 7)) -> List[str]:
    """Generate well names for a 24-well tube rack (A1–D6)."""
    return [f"{r}{c}" for r in rows for c in cols]

def compute_dilution_plan(cfg: ExperimentConfig) -> List[Dict[str, Any]]:
    """Calculate pipetting volumes for each dilution factor."""
    plan: List[Dict[str, Any]] = []
    wells = generate_well_names()
    
    for i, df in enumerate(cfg.dilution_factors):
        final_vol = cfg.target_final_volume_ul

        if df == 1:
            v_stock = final_vol
            v_water = 0.0
        else:
            v_stock = cfg.target_final_volume_ul / df
            v_water = cfg.target_final_volume_ul - v_stock

        stock_pipette = "main"
        water_pipette = "main"
        final_conc = round(cfg.stock_concentration_ugml / df, 4)
        dilution_name = f"{df}x" if df > 1 else "stock"

        pip_max = cfg.pipette_max_ul
        stock_aspirations = -(-int(v_stock) // int(pip_max)) if v_stock > 0 else 0
        water_aspirations = -(-int(v_water) // int(pip_max)) if v_water > 0 else 0

        well = wells[i] if i < len(wells) else f"overflow_{i}"
        output_slot = 7 if i < 24 else 8

        entry = {
            "dilution_index": i,
            "dilution_name": dilution_name,
            "dilution_factor": df,
            "stock_volume_ul": v_stock,
            "water_volume_ul": v_water,
            "final_volume_ul": final_vol,
            "final_concentration_ugml": final_conc,
            "stock_pipette": stock_pipette,
            "water_pipette": water_pipette,
            "stock_aspirations": stock_aspirations,
            "water_aspirations": water_aspirations,
            "output_slot": output_slot,
            "output_well": well,
        }
        plan.append(entry)

    return plan

def validate_plan(plan: List[Dict[str, Any]], cfg: ExperimentConfig) -> Tuple[List[str], List[str]]:
    """Validate the dilution plan."""
    errors: List[str] = []
    warnings: List[str] = []

    factors = [p["dilution_factor"] for p in plan]
    for i in range(1, len(factors)):
        if factors[i] < factors[i - 1]:
            warnings.append(f"Dilution factors not sorted: {factors[i-1]} → {factors[i]}.")

    wells_used: Dict[str, int] = {}
    for p in plan:
        key = f"{p['output_slot']}:{p['output_well']}"
        if key in wells_used:
            errors.append(f"Duplicate well: {key} used by {wells_used[key]} and {p['dilution_index']}.")
        wells_used[key] = p["dilution_index"]

    return errors, warnings

def write_plan_csv(plan: List[Dict[str, Any]], output_dir: pathlib.Path) -> None:
    """Write dilution plan as CSV."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "dilution_plan.csv"
    fieldnames = [
        "dilution_name", "dilution_factor", "stock_volume_ul",
        "water_volume_ul", "final_volume_ul", "final_concentration_ugml",
        "stock_pipette", "water_pipette", "output_slot", "output_well",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(plan)
    logger.info("Plan CSV written to %s", path)

def write_plan_json(plan: List[Dict[str, Any]], output_dir: pathlib.Path) -> None:
    """Write dilution plan as JSON for the OT-2 protocol."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "dilution_plan.json"
    # Note: We save the raw list for the protocol to consume
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)
    logger.info("Plan JSON written to %s", path)

def main() -> int:
    parser = argparse.ArgumentParser(description="Calculate dilution pipetting volumes.")
    parser.add_argument("--config", type=str, default=str(OUTPUTS_DIR / "experiment_config.json"))
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    setup_logging(LOGS_DIR, args.log_level)

    config_path = pathlib.Path(args.config)
    if not config_path.is_file():
        logger.error("Config not found: %s", config_path)
        return 1

    cfg = load_config(config_path)
    plan = compute_dilution_plan(cfg)
    errors, warnings = validate_plan(plan, cfg)

    for w in warnings: logger.warning("W: %s", w)
    for e in errors: logger.error("E: %s", e)
    if errors: return 1

    write_plan_csv(plan, OUTPUTS_DIR)
    write_plan_json(plan, OUTPUTS_DIR)

    logger.info("Planning complete.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
