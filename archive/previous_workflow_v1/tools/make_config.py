#!/usr/bin/env python3
"""
Interactive Config Generator for the Opentrons Nanoparticle Workflow (v2.0)
==========================================================================

Produces a per-experiment YAML config that is directly consumable by
``protocols/dilution_protocol.py``. This version focuses on relative
dilution factors (DF) from a synthesized stock suspension.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

# =====================================================================
#  Project-level constants & defaults
# =====================================================================

SCHEMA_VERSION = "2.0"
CONFIGS_DIR = Path("configs")

PIPETTE_MIN_VOLUME_UL = 20.0  # P300 single-channel minimum

# Default labware map --------------------------------------------------
DEFAULT_LABWARE: Dict[int, Dict[str, str]] = {
    1: {
        "load_name": "opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap",
        "description": "Material/Ink tube rack",
    },
    2: {
        "load_name": "nest_96_wellplate_200ul_flat",
        "description": "Dispersion destination plate",
    },
    5: {
        "load_name": "nest_12_reservoir_15ml",
        "description": "Diluent/Solvent reservoir",
    },
    11: {
        "load_name": "opentrons_96_tiprack_300ul",
        "description": "Tip rack for P300",
    },
}

DEFAULT_PIPETTE: Dict[str, Any] = {
    "model": "p300_single_gen2",
    "mount": "right",
    "tip_rack_slot": 11,
}

# Other defaults -------------------------------------------------------
DEFAULT_MATERIAL = "AuNS"
DEFAULT_DISPLAY_NAME = "gold nanostars"
DEFAULT_STOCK_LABEL = "AuNS_stock"
DEFAULT_SOURCE_SLOT = 1
DEFAULT_SOURCE_WELL = "A1"
DEFAULT_AVAILABLE_VOLUME = 1000.0  # µL
DEFAULT_DILUENT_NAME = "water"
DEFAULT_DILUENT_SLOT = 5
DEFAULT_DILUENT_WELL = "A1"
DEFAULT_DILUENT_AVAILABLE = 10000.0  # µL
DEFAULT_FINAL_VOLUME = 100.0  # µL
DEFAULT_DILUTIONS = [1, 2, 5, 10]
DEFAULT_OPERATOR = ""
DEFAULT_NOTES = ""


# =====================================================================
#  Utility helpers
# =====================================================================

def _sanitize_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower())
    return s.strip("_")


def _prompt(label: str, default: str = "") -> str:
    if default:
        raw = input(f"{label} [{default}]: ").strip()
        return raw if raw else default
    else:
        return input(f"{label}: ").strip()


def _prompt_float(label: str, default: float) -> float:
    while True:
        raw = _prompt(label, str(default))
        try:
            return float(raw)
        except ValueError:
            print("  ✗ Please enter a number.")


def _prompt_required(label: str, default: str = "") -> str:
    while True:
        val = _prompt(label, default)
        if val:
            return val
        print("  ✗ This field is required.")


def _prompt_bool(label: str, default: bool) -> bool:
    while True:
        d_str = "Y/n" if default else "y/N"
        raw = _prompt(f"{label} ({d_str})", "").lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  ✗ Please enter Y or N.")


# =====================================================================
#  Config assembly
# =====================================================================

def build_config(
    *,
    experiment_name: str,
    project: str,
    date_str: str,
    operator: str,
    notes: str,
    material: str,
    display_name: str,
    stock_label: str,
    source_well: str,
    available_volume: float,
    uvvis: Dict[str, Any],
    diluent_name: str,
    diluent_well: str,
    diluent_available: float,
    requested_dilutions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    sanitized_material = _sanitize_name(material)
    sanitized_project = _sanitize_name(project)
    date_compact = date_str.replace("-", "") # YYYY-MM-DD -> YYYYMMDD
    
    stem = f"{sanitized_material}_{date_str}"

    cfg: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "experiment": {
            "name": experiment_name,
            "project": project,
            "date": date_str,
            "operator": operator,
            "notes": notes if notes else None,
        },
        "nanoparticle_stock": {
            "material": material,
            "display_name": display_name,
            "label": stock_label,
            "source_slot": DEFAULT_SOURCE_SLOT,
            "source_well": source_well,
            "available_volume_uL": available_volume,
            "concentration_basis": "relative_to_stock",
            "stock_relative_concentration": 1.0,
            "absolute_concentration": None,
            "characterization": uvvis,
        },
        "diluent": {
            "name": diluent_name,
            "source_slot": DEFAULT_DILUENT_SLOT,
            "source_well": diluent_well,
            "available_volume_uL": diluent_available,
        },
        "requested_dilutions": requested_dilutions,
        "labware": DEFAULT_LABWARE,
        "pipette": DEFAULT_PIPETTE,
        "outputs": {
            "log_file": f"logs/{date_compact}/{sanitized_project}/{stem}.log",
            "transcript": f"outputs/{date_compact}/{sanitized_project}/{stem}_transcript.txt",
        },
    }

    if cfg["experiment"]["notes"] is None:
        del cfg["experiment"]["notes"]

    return cfg


def interactive_mode(argv: List[str]) -> int:
    today = datetime.date.today().isoformat()

    print("=" * 60)
    print("  Opentrons Nanoparticle Config Generator (DF-based)")
    print("=" * 60)

    # 1. Metadata
    print("\n── Experiment Metadata ─────────────────────────────────")
    project = _prompt_required("Project name (e.g. nanoparticles)", "nanoparticles")
    operator = _prompt_required("Operator name", DEFAULT_OPERATOR)
    notes = _prompt("Experiment notes (optional)", DEFAULT_NOTES)

    # 2. Nanoparticle Stock
    print("\n── Stock Nanoparticle Suspension ───────────────────────")
    material = _prompt_required("Material identifier (e.g. AuNS)", DEFAULT_MATERIAL)
    display_name = _prompt("Descriptive name", DEFAULT_DISPLAY_NAME)
    stock_label = _prompt("Stock vial label", DEFAULT_STOCK_LABEL)
    source_well = _prompt("Source well", DEFAULT_SOURCE_WELL)
    available_vol = _prompt_float("Available volume (µL)", DEFAULT_AVAILABLE_VOLUME)

    # 3. UV-Vis Characterization
    print("\n── UV-Vis Characterization ─────────────────────────────")
    uvvis_measured = _prompt_bool("Was UV-Vis measured for this stock?", False)
    uvvis = {"uvvis_measured": uvvis_measured}
    if uvvis_measured:
        uvvis["uvvis_lambda_max_nm"] = _prompt_float("Lambda Max (nm)", 0.0)
        uvvis["absorbance_at_lambda_max"] = _prompt_float("Absorbance", 0.0)
        uvvis["notes"] = _prompt("Characterization notes", "")
    else:
        uvvis.update({
            "uvvis_lambda_max_nm": None,
            "absorbance_at_lambda_max": None,
            "notes": None
        })

    # 4. Diluent
    print("\n── Diluent ─────────────────────────────────────────────")
    diluent_name = _prompt("Diluent name", DEFAULT_DILUENT_NAME)
    diluent_well = _prompt("Source well", DEFAULT_DILUENT_WELL)
    diluent_avail = _prompt_float("Available diluent (µL)", DEFAULT_DILUENT_AVAILABLE)

    # 5. Dilutions
    print("\n── Requested Dilutions ─────────────────────────────────")
    final_vol = _prompt_float("Default final volume per well (µL)", DEFAULT_FINAL_VOLUME)
    factors_str = _prompt("Dilution factors (comma-separated, e.g. 1,2,5,10)", 
                        ",".join(map(str, DEFAULT_DILUTIONS)))
    factors = [float(x.strip()) for x in factors_str.split(",") if x.strip()]

    requested = []
    # 96-well plate wells in order
    WELL_ROWS = "ABCDEFGH"
    WELL_COLS = range(1, 13)
    wells = [f"{r}{c}" for c in WELL_COLS for r in WELL_ROWS]

    for i, df in enumerate(factors):
        requested.append({
            "dilution_factor": df,
            "final_volume_uL": final_vol,
            "destination_well": wells[i]
        })

    # 6. Build
    experiment_name = f"{material}_Relative_Dilutions"
    cfg = build_config(
        experiment_name=experiment_name,
        project=project,
        date_str=today,
        operator=operator,
        notes=notes,
        material=material,
        display_name=display_name,
        stock_label=stock_label,
        source_well=source_well,
        available_volume=available_vol,
        uvvis=uvvis,
        diluent_name=diluent_name,
        diluent_well=diluent_well,
        diluent_available=diluent_avail,
        requested_dilutions=requested
    )

    # 7. Summary
    print("\n" + "─" * 60)
    print("  SUMMARY (DF-based)")
    print("─" * 60)
    print(f"  Project    : {project}")
    print(f"  Material   : {display_name} ({material})")
    print(f"  Stock Well : {source_well} | Avail: {available_vol} µL")
    if uvvis_measured:
        print(f"  UV-Vis     : λmax={uvvis['uvvis_lambda_max_nm']}nm, Abs={uvvis['absorbance_at_lambda_max']}")
    print()
    print(f"  {'Label':<15} {'DF':<6} {'V_stock':<10} {'V_dil':<10} {'Well':<6}")
    print(f"  {'─────':<15} {'──':<6} {'───────':<10} {'─────':<10} {'────':<6}")
    
    total_stock = 0.0
    for entry in requested:
        df = entry["dilution_factor"]
        f_vol = entry["final_volume_uL"]
        vs = round(f_vol / df, 1)
        vd = round(f_vol - vs, 1)
        total_stock += vs
        label = f"{material}_DF{df}"
        print(f"  {label:<15} {df:<6} {vs:<10.1f} {vd:<10.1f} {entry['destination_well']:<6}")
    
    print(f"\n  Total stock consumed: {total_stock:.1f} µL")
    print("─" * 60)

    # 8. Confirm & write
    confirm = _prompt("\nWrite this config?", "Y/n")
    if confirm.lower() in ("n", "no"):
        print("Cancelled.")
        return 0

    sanitized = _sanitize_name(material)
    out_path = CONFIGS_DIR / f"exp_{today}_{sanitized}.yaml"
    
    # Simple collision handling
    if out_path.exists():
        out_path = CONFIGS_DIR / f"exp_{today}_{sanitized}_{datetime.datetime.now().strftime('%H%M%S')}.yaml"

    CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(f"# Nanoparticle Relative Dilution Config v2.0\n")
        fh.write(f"# Generated: {datetime.datetime.now().isoformat()}\n\n")
        yaml.dump(cfg, fh, sort_keys=False, default_flow_style=False)

    print(f"\n✓ Config written to: {out_path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(interactive_mode(sys.argv))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
