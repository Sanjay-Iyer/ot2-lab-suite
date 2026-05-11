#!/usr/bin/env python3
"""
Script 1: Central Configuration & Validation
=============================================

Defines all experimental parameters for the nanoparticle printing
optimization workflow as a validated Pydantic model.  Writes the
config to JSON and YAML for downstream scripts.

Usage
-----
    python config.py                          # Interactive defaults
    python config.py --dry-run                # Validate only
    python config.py --stock-conc 100         # Override stock concentration
    python config.py --dilutions 1,10,50,100,500,1000
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import pathlib
import sys
import textwrap
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import yaml

# =====================================================================
#  Constants
# =====================================================================

SUITE_DIR = pathlib.Path(__file__).resolve().parent.parent
CONFIGS_DIR = SUITE_DIR / "configs"
OUTPUTS_DIR = SUITE_DIR / "outputs"
LOGS_DIR = SUITE_DIR / "logs"

P20_MIN_UL = 2.0
P20_MAX_UL = 20.0
P300_MIN_UL = 30.0
P300_MAX_UL = 300.0

logger = logging.getLogger("config")


# =====================================================================
#  Data structures
# =====================================================================

@dataclass
class DeckSlot:
    """A single slot on the OT-2 deck."""
    slot: int
    labware_type: str
    reagent_name: str
    description: str = ""


@dataclass
class ExperimentConfig:
    """Complete experiment configuration for the printing suite."""

    # ── Experiment metadata ───────────────────────────────────────────
    experiment_name: str = "AuNS_Printing_Optimization"
    project: str = "nanostars"
    date: str = field(default_factory=lambda: datetime.date.today().isoformat())
    operator: str = ""
    notes: str = ""

    # ── Nanoparticle stock ────────────────────────────────────────────
    stock_concentration_ugml: float = 100.0
    stock_available_volume_ul: float = 2000.0
    stock_material: str = "AuNS"
    stock_display_name: str = "gold nanostars"

    # ── Experimental design ───────────────────────────────────────────
    dilution_factors: List[int] = field(
        default_factory=lambda: [1, 10, 50, 100, 500, 1000]
    )
    size_variants: List[str] = field(
        default_factory=lambda: ["small", "medium", "large"]
    )
    print_volumes_nl: List[int] = field(
        default_factory=lambda: [50, 200, 500]
    )
    target_final_volume_ul: float = 200.0

    # ── Pipette parameters ────────────────────────────────────────────
    pipette_type: str = "p300_multi_gen2"
    pipette_mount: str = "right"
    pipette_min_ul: float = 20.0
    pipette_max_ul: float = 300.0

    # ── Deck layout ──────────────────────────────────────────────────
    deck_layout: List[DeckSlot] = field(default_factory=lambda: [
        DeckSlot(1,  "opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap",
                 "stock_ns", "Stock NS vial"),
        DeckSlot(2,  "nest_12_reservoir_15ml",
                 "water", "Water reservoir"),
        DeckSlot(3,  "nest_1_reservoir_195ml",
                 "waste", "Waste reservoir"),
        DeckSlot(4,  "opentrons_15_tuberack_falcon_15ml_conical",
                 "size_pool_A", "Size A pool (small)"),
        DeckSlot(5,  "opentrons_15_tuberack_falcon_15ml_conical",
                 "size_pool_B", "Size B pool (medium)"),
        DeckSlot(6,  "opentrons_15_tuberack_falcon_15ml_conical",
                 "size_pool_C", "Size C pool (large)"),
        DeckSlot(7,  "opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap",
                 "output_rack_1", "Concentration output rack 1"),
        DeckSlot(8,  "opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap",
                 "output_rack_2", "Concentration output rack 2"),
        DeckSlot(9,  "nest_96_wellplate_200ul_flat",
                 "printer_tray", "Printer input tray"),
        DeckSlot(10, "opentrons_96_tiprack_300ul",
                 "tips_main", "Main tip rack for 8-channel pipette"),
    ])

    # ── Robot ─────────────────────────────────────────────────────────
    robot_ip: str = "169.254.46.57"

    def total_samples(self) -> int:
        """Number of unique concentration × size combinations."""
        return len(self.dilution_factors) * len(self.size_variants)

    def total_print_positions(self) -> int:
        """Number of positions needed on the printer tray."""
        return self.total_samples() * len(self.print_volumes_nl)


# =====================================================================
#  Validation
# =====================================================================

def select_pipette(volume_ul: float) -> str:
    """Determine which pipette to use for a given volume.

    Returns 'p20', 'p300', or 'gap' if the volume falls in the
    20–30 µL dead zone where neither pipette is ideal.
    """
    if P20_MIN_UL <= volume_ul <= P20_MAX_UL:
        return "p20"
    elif P300_MIN_UL <= volume_ul <= P300_MAX_UL:
        return "p300"
    elif P20_MAX_UL < volume_ul < P300_MIN_UL:
        return "gap"
    elif volume_ul < P20_MIN_UL:
        return "below_min"
    else:
        return "above_max"


def validate_config(cfg: ExperimentConfig) -> Tuple[List[str], List[str]]:
    """Validate the experiment configuration.

    Returns:
        Tuple of (errors, warnings).  Empty errors = valid config.
    """
    errors: List[str] = []
    warnings: List[str] = []

    # ── Dilution factor checks ────────────────────────────────────────
    if not cfg.dilution_factors:
        errors.append("dilution_factors must not be empty.")

    prev = 0
    for df in cfg.dilution_factors:
        if df <= 0:
            errors.append(f"Dilution factor {df} must be > 0.")
        if df <= prev and prev > 0:
            warnings.append(
                f"Dilution factors not monotonically increasing: "
                f"{prev} → {df}."
            )
        prev = df

    # ── Volume checks per dilution ────────────────────────────────────
    total_stock_needed = 0.0
    for df in cfg.dilution_factors:
        if df == 0:
            continue
        v_stock = cfg.target_final_volume_ul / df
        v_water = cfg.target_final_volume_ul - v_stock

        total_stock_needed += v_stock * len(cfg.size_variants)

        pip = select_pipette(v_stock)
        if pip == "below_min":
            errors.append(
                f"DF={df}: stock volume {v_stock:.1f} µL is below the "
                f"minimum pipette range ({P20_MIN_UL} µL).  "
                f"Increase target_final_volume_ul or remove this factor."
            )
        elif pip == "gap":
            warnings.append(
                f"DF={df}: stock volume {v_stock:.1f} µL falls in the "
                f"20–30 µL gap between p20 and p300.  "
                f"The p20 will be used but accuracy may be reduced."
            )
        elif pip == "above_max":
            warnings.append(
                f"DF={df}: stock volume {v_stock:.1f} µL exceeds p300 max "
                f"({P300_MAX_UL} µL).  Multiple aspirations will be needed."
            )

        if v_water > 0:
            water_pip = select_pipette(v_water)
            if water_pip == "above_max":
                warnings.append(
                    f"DF={df}: water volume {v_water:.1f} µL exceeds p300 max.  "
                    f"Multiple aspirations needed."
                )

    # ── Stock availability ────────────────────────────────────────────
    if total_stock_needed > cfg.stock_available_volume_ul:
        errors.append(
            f"Total stock needed ({total_stock_needed:.1f} µL) exceeds "
            f"available ({cfg.stock_available_volume_ul:.1f} µL)."
        )

    # ── Deck space ────────────────────────────────────────────────────
    total_samples = cfg.total_samples()
    max_output_positions = 48  # 2 × 24-well racks (slots 7–8)
    if total_samples > max_output_positions:
        errors.append(
            f"Total samples ({total_samples}) exceeds output rack "
            f"capacity ({max_output_positions})."
        )

    total_print = cfg.total_print_positions()
    if total_print > 96:
        errors.append(
            f"Total print positions ({total_print}) exceeds 96-well "
            f"printer tray capacity."
        )

    # ── Size variants ─────────────────────────────────────────────────
    if not cfg.size_variants:
        errors.append("size_variants must not be empty.")

    # ── Print volumes ─────────────────────────────────────────────────
    if not cfg.print_volumes_nl:
        errors.append("print_volumes_nl must not be empty.")

    # ── Deck slot collisions ──────────────────────────────────────────
    slots_seen: Dict[int, str] = {}
    for ds in cfg.deck_layout:
        if ds.slot in slots_seen:
            errors.append(
                f"Deck slot {ds.slot} assigned twice: "
                f"'{slots_seen[ds.slot]}' and '{ds.reagent_name}'."
            )
        slots_seen[ds.slot] = ds.reagent_name

    # ── Final volume vs labware ───────────────────────────────────────
    if cfg.target_final_volume_ul > 1500:
        warnings.append(
            f"target_final_volume_ul ({cfg.target_final_volume_ul} µL) "
            f"may exceed 1.5 mL tube capacity."
        )

    return errors, warnings


# =====================================================================
#  Serialization
# =====================================================================

def config_to_dict(cfg: ExperimentConfig) -> Dict[str, Any]:
    """Convert config to a JSON-serializable dictionary."""
    d = asdict(cfg)
    # Convert DeckSlot dataclasses to plain dicts (already done by asdict)
    return d


def write_config(cfg: ExperimentConfig, output_dir: pathlib.Path) -> None:
    """Write config to JSON and YAML files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    d = config_to_dict(cfg)

    json_path = CONFIGS_DIR / "experiment_config.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    logger.info("Config written to %s", json_path)

    yaml_path = CONFIGS_DIR / "experiment_config.yaml"
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(d, f, sort_keys=False, default_flow_style=False)
    logger.info("Config written to %s", yaml_path)


def load_config(config_path: pathlib.Path) -> ExperimentConfig:
    """Load config from JSON file."""
    with open(config_path, "r", encoding="utf-8") as f:
        d = json.load(f)

    # Reconstruct DeckSlot objects
    deck_layout_data = d.pop("deck_layout", [])
    deck_slots = [DeckSlot(**slot) for slot in deck_layout_data]
    cfg = ExperimentConfig(**d, deck_layout=deck_slots)
    return cfg


# =====================================================================
#  Logging setup
# =====================================================================

def setup_logging(log_dir: pathlib.Path, level: str = "INFO") -> None:
    """Configure logging for the suite."""
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"config_{ts}.log"

    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(name)-18s %(levelname)-5s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file),
        ],
    )


# =====================================================================
#  CLI
# =====================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate and validate the experiment configuration.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python config.py
              python config.py --dry-run
              python config.py --stock-conc 150 --dilutions 1,5,25,100,500
        """),
    )
    p.add_argument("--stock-conc", type=float, default=100.0,
                    help="Stock concentration in µg/mL (default: 100)")
    p.add_argument("--dilutions", type=str, default="1,10,50,100,500,1000",
                    help="Comma-separated dilution factors")
    p.add_argument("--sizes", type=str, default="small,medium,large",
                    help="Comma-separated size variant names")
    p.add_argument("--print-vols", type=str, default="50,200,500",
                    help="Comma-separated print volumes in nL")
    p.add_argument("--final-vol", type=float, default=200.0,
                    help="Final volume per sample in µL (default: 200)")
    p.add_argument("--operator", type=str, default="",
                    help="Operator name")
    p.add_argument("--dry-run", action="store_true",
                    help="Validate only, do not write files")
    p.add_argument("--log-level", type=str, default="INFO",
                    choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    setup_logging(LOGS_DIR, args.log_level)

    # ── Build config ──────────────────────────────────────────────────
    dilution_factors = [int(x.strip()) for x in args.dilutions.split(",")]
    size_variants = [x.strip() for x in args.sizes.split(",")]
    print_volumes = [int(x.strip()) for x in args.print_vols.split(",")]

    cfg = ExperimentConfig(
        stock_concentration_ugml=args.stock_conc,
        dilution_factors=dilution_factors,
        size_variants=size_variants,
        print_volumes_nl=print_volumes,
        target_final_volume_ul=args.final_vol,
        operator=args.operator,
    )

    # ── Validate ──────────────────────────────────────────────────────
    errors, warnings = validate_config(cfg)

    for w in warnings:
        logger.warning("⚠ %s", w)
    for e in errors:
        logger.error("✗ %s", e)

    if errors:
        logger.error("Configuration invalid (%d error(s)).", len(errors))
        return 1

    logger.info("Configuration valid.")
    logger.info("  Samples:         %d", cfg.total_samples())
    logger.info("  Print positions: %d", cfg.total_print_positions())
    logger.info("  Dilution factors: %s", cfg.dilution_factors)
    logger.info("  Size variants:   %s", cfg.size_variants)

    # ── Write ─────────────────────────────────────────────────────────
    if args.dry_run:
        logger.info("Dry run — no files written.")
        return 0

    write_config(cfg, CONFIGS_DIR)
    logger.info("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
