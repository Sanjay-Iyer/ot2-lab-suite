#!/usr/bin/env python3
"""
Script 2: Deck Setup & Labware Initialization
==============================================

Reads the experiment config and produces a detailed deck map that
maps logical reagent names to physical OT-2 slot/labware/well
locations.  Validates labware compatibility and tip sufficiency.

Usage
-----
    python deck_setup.py
    python deck_setup.py --config outputs/experiment_config.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import pathlib
import sys
from typing import Any, Dict, List, Tuple

from config import (
    ExperimentConfig, load_config, setup_logging,
    SUITE_DIR, OUTPUTS_DIR, LOGS_DIR,
)

logger = logging.getLogger("deck_setup")

# =====================================================================
#  Known OT-2 labware (subset for validation)
# =====================================================================

KNOWN_LABWARE = {
    "opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap",
    "opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap",
    "opentrons_15_tuberack_falcon_15ml_conical",
    "nest_12_reservoir_15ml",
    "nest_1_reservoir_195ml",
    "nest_96_wellplate_200ul_flat",
    "opentrons_96_tiprack_300ul",
    "opentrons_96_tiprack_20ul",
    "corning_96_wellplate_360ul_flat",
}

# Wells per labware type
LABWARE_WELL_COUNT = {
    "opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap": 24,
    "opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap": 24,
    "opentrons_15_tuberack_falcon_15ml_conical": 15,
    "nest_12_reservoir_15ml": 12,
    "nest_1_reservoir_195ml": 1,
    "nest_96_wellplate_200ul_flat": 96,
    "opentrons_96_tiprack_300ul": 96,
    "opentrons_96_tiprack_20ul": 96,
    "corning_96_wellplate_360ul_flat": 96,
}


# =====================================================================
#  Deck map generation
# =====================================================================

def generate_deck_map(cfg: ExperimentConfig) -> Dict[str, Any]:
    """Build a structured deck map from the experiment config.

    Returns a dict suitable for JSON serialization with full provenance.
    """
    deck_map: Dict[str, Any] = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "experiment_name": cfg.experiment_name,
        "slots": {},
    }

    for ds in cfg.deck_layout:
        well_count = LABWARE_WELL_COUNT.get(ds.labware_type, 0)
        deck_map["slots"][str(ds.slot)] = {
            "labware_type": ds.labware_type,
            "reagent_name": ds.reagent_name,
            "description": ds.description,
            "well_count": well_count,
        }

    # Add pipette info
    deck_map["pipettes"] = {
        "main": {"mount": cfg.pipette_mount, "tip_rack_slot": 10, "type": cfg.pipette_type},
    }

    return deck_map


# =====================================================================
#  Validation
# =====================================================================

def validate_deck(
    cfg: ExperimentConfig,
    deck_map: Dict[str, Any],
) -> Tuple[List[str], List[str]]:
    """Validate deck setup.  Returns (errors, warnings)."""
    errors: List[str] = []
    warnings: List[str] = []

    # ── Check labware in known library ────────────────────────────────
    for ds in cfg.deck_layout:
        if ds.labware_type not in KNOWN_LABWARE:
            warnings.append(
                f"Slot {ds.slot}: labware '{ds.labware_type}' is not in the "
                f"standard library.  Ensure it is a valid custom definition."
            )

    # ── Check tip sufficiency ─────────────────────────────────────────
    # Estimate tip usage:
    #   Dilution stage: 2 tips per dilution (one for stock, one for water)
    #   Size mixing stage: 1 tip per sample
    #   Print prep: 1 tip per print position
    n_dilutions = len(cfg.dilution_factors)
    n_samples = cfg.total_samples()
    n_print = cfg.total_print_positions()

    # p300 tips (dilution water + some stock transfers)
    p300_tips_needed = n_dilutions * 2 + n_samples
    p300_tips_available = 96  # One full rack in slot 10
    if p300_tips_needed > p300_tips_available:
        errors.append(
            f"p300 tips needed ({p300_tips_needed}) exceeds available "
            f"({p300_tips_available}).  Add another tip rack."
        )
    elif p300_tips_needed > p300_tips_available * 0.8:
        warnings.append(
            f"p300 tip usage is tight: {p300_tips_needed}/{p300_tips_available} "
            f"— scheduling may be constrained."
        )

    # p20 tips (small stock volumes)
    p20_tips_needed = n_dilutions  # Worst case: all stock volumes use p20
    p20_tips_available = 96
    if p20_tips_needed > p20_tips_available:
        warnings.append(
            f"p20 tips may be insufficient: {p20_tips_needed} needed, "
            f"{p20_tips_available} available."
        )

    # ── Check output capacity ─────────────────────────────────────────
    output_capacity = 0
    for ds in cfg.deck_layout:
        if ds.reagent_name.startswith("output_rack"):
            output_capacity += LABWARE_WELL_COUNT.get(ds.labware_type, 0)

    if n_samples > output_capacity:
        errors.append(
            f"Samples ({n_samples}) exceed output rack capacity "
            f"({output_capacity})."
        )

    # ── Check printer tray ────────────────────────────────────────────
    for ds in cfg.deck_layout:
        if ds.reagent_name == "printer_tray":
            tray_capacity = LABWARE_WELL_COUNT.get(ds.labware_type, 0)
            if n_print > tray_capacity:
                errors.append(
                    f"Print positions ({n_print}) exceed printer tray "
                    f"capacity ({tray_capacity})."
                )
            break

    # ── Slot 12 is fixed trash ────────────────────────────────────────
    for ds in cfg.deck_layout:
        if ds.slot == 12:
            warnings.append(
                "Slot 12 is the fixed trash on OT-2; custom labware "
                "cannot be placed there."
            )

    return errors, warnings


# =====================================================================
#  Output
# =====================================================================

def write_deck_map(deck_map: Dict[str, Any], output_dir: pathlib.Path) -> None:
    """Write deck map to JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "deck_map.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(deck_map, f, indent=2)
    logger.info("Deck map written to %s", path)


def write_warnings_log(
    warnings: List[str],
    log_dir: pathlib.Path,
) -> None:
    """Write warnings to a dedicated log file."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "warnings.log"
    with open(path, "w", encoding="utf-8") as f:
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        for w in warnings:
            f.write(f"{ts} WARNING {w}\n")
    logger.info("Warnings log written to %s", path)


# =====================================================================
#  CLI
# =====================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize OT-2 deck and generate deck map."
    )
    parser.add_argument(
        "--config", type=str,
        default=str(SUITE_DIR / "configs" / "experiment_config.json"),
        help="Path to experiment config JSON.",
    )
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    setup_logging(LOGS_DIR, args.log_level)

    # ── Load config ───────────────────────────────────────────────────
    config_path = pathlib.Path(args.config)
    if not config_path.is_file():
        logger.error("Config not found: %s.  Run config.py first.", config_path)
        return 1

    cfg = load_config(config_path)
    logger.info("Config loaded: %s", config_path)

    # ── Generate deck map ─────────────────────────────────────────────
    deck_map = generate_deck_map(cfg)
    logger.info("Deck map generated with %d slots.", len(deck_map["slots"]))

    # ── Validate ──────────────────────────────────────────────────────
    errors, warnings = validate_deck(cfg, deck_map)

    for w in warnings:
        logger.warning("⚠ %s", w)
    for e in errors:
        logger.error("✗ %s", e)

    if errors:
        logger.error("Deck validation failed (%d error(s)).", len(errors))
        return 1

    # ── Write outputs ─────────────────────────────────────────────────
    from config import CONFIGS_DIR
    write_deck_map(deck_map, CONFIGS_DIR)
    if warnings:
        write_warnings_log(warnings, LOGS_DIR)

    logger.info("Deck setup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
