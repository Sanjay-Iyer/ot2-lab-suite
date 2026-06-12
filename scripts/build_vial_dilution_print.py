#!/usr/bin/env python3
"""
scripts/build_vial_dilution_print.py
====================================
Turn configs/workflows/defaults/vial_dilution_print.yaml into a robot-ready copy of
src/protocols/vial_dilution_print.py with the YAML embedded as the CONFIG dict, then
simulate it. This is how the YAML reaches the OT-2 (the robot can't read the repo).

  conda activate ai
  python scripts/build_vial_dilution_print.py
  python scripts/build_vial_dilution_print.py --config my.yaml --no-sim

Pipeline: load YAML -> validate -> embed CONFIG (between the markers in the base
protocol) + write run_modes into the DEFAULT_* flags -> generate into
src/protocols/generated/ -> simulate (scans output text for errors).
"""
from __future__ import annotations

import argparse
import json
import math
import pprint
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

REPO          = Path(__file__).resolve().parent.parent
BASE_PROTOCOL = REPO / "src" / "protocols" / "vial_dilution_print.py"
GENERATED_DIR = REPO / "src" / "protocols" / "generated"
LABWARE       = REPO / "labware"
DEFAULT_CONFIG = REPO / "configs" / "workflows" / "defaults" / "vial_dilution_print.yaml"

START_SENTINEL = "# >>> CONFIG START >>>"
END_SENTINEL   = "# <<< CONFIG END <<<"

# ── Known pipette max volumes (fallback when labware JSON not available) ──────────
_PIPETTE_MAX_UL: dict[str, float] = {
    "p20_single_gen2":   20.0,
    "p300_single_gen2":  300.0,
    "p300_multi_gen2":   300.0,
    "p1000_single_gen2": 1000.0,
    "p1000_multi_gen2":  1000.0,
}

_SHIM = (
    "import numpy as np; "
    "np.trapz = getattr(np, 'trapezoid', np.trapz if hasattr(np, 'trapz') else None); "
    "from opentrons.simulate import main; main()"
)
# Tightened: only genuine Python error markers, not user comment phrases like "not allowed"
_ERROR_RE = re.compile(
    r"Traceback \(most recent call last\)|RuntimeError|LabwareNotFoundError|"
    r"ProtocolCommandFailedError|InvalidProtocolData|KeyError|AttributeError",
    re.IGNORECASE,
)

_FLAG_SUBS = {
    "dry_run":     (re.compile(r"(?m)^DEFAULT_DRY_RUN\s*=.*$"),     "DEFAULT_DRY_RUN     = {}"),
    "do_dilution": (re.compile(r"(?m)^DEFAULT_DO_DILUTION\s*=.*$"), "DEFAULT_DO_DILUTION = {}"),
    "do_print":    (re.compile(r"(?m)^DEFAULT_DO_PRINT\s*=.*$"),    "DEFAULT_DO_PRINT    = {}"),
}


# ── Labware JSON helpers ──────────────────────────────────────────────────────────

def _load_labware_json(load_name: str) -> Optional[dict]:
    """Load a custom labware definition from the repo's labware/ directory.

    Standard Opentrons labware (e.g., opentrons_96_tiprack_300ul) are not stored
    in the custom labware folder; returns None for those, which triggers fallback
    to the safety config constants.
    """
    path = LABWARE / f"{load_name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: Could not read labware JSON {path}: {exc}")
        return None


def _labware_max_volume(lw_data: Optional[dict], fallback: float) -> float:
    """Return the minimum totalLiquidVolume across all wells (most conservative bound)."""
    if lw_data is None:
        return fallback
    volumes = [w.get("totalLiquidVolume", fallback) for w in lw_data["wells"].values()]
    return float(min(volumes)) if volumes else fallback


def _labware_rows_per_column(lw_data: Optional[dict], fallback: int) -> int:
    """Return the number of rows per column from the ordering array."""
    if lw_data is None:
        return fallback
    ordering = lw_data.get("ordering", [])
    if ordering and ordering[0]:
        return len(ordering[0])
    return fallback


def _labware_column_count(lw_data: Optional[dict], fallback: int) -> int:
    """Return the number of columns from the ordering array."""
    if lw_data is None:
        return fallback
    ordering = lw_data.get("ordering", [])
    return len(ordering) if ordering else fallback


# ── Validation (mirrors the protocol's resolvers so errors surface early) ──────────

def _resolve_factors(d: dict) -> list:
    fc = d["factors"]
    mode = fc.get("mode", "explicit")
    if mode == "explicit":
        return [float(x) for x in fc["explicit"]]
    count = int(fc.get("count", 8))
    start = float(fc.get("start", 1))
    if mode == "geometric":
        step = float(fc.get("step_factor", 2))
        return [round(start * (step ** i), 4) for i in range(count)]
    end = float(fc.get("end", 50))
    if count == 1:
        return [start]
    if mode == "linear":
        return [round(start + (end - start) * i / (count - 1), 4) for i in range(count)]
    if mode == "log":
        lo, hi = math.log(start), math.log(end)
        return [round(math.exp(lo + (hi - lo) * i / (count - 1)), 4) for i in range(count)]
    raise ValueError(f"factors.mode {mode!r} invalid (explicit|geometric|linear|log).")


def validate(cfg: dict) -> list:
    """Return a list of validation error strings (empty = OK)."""
    errors: list = []
    for key in ("deck", "pipette", "sources", "dilution", "printing", "tips", "camera", "safety"):
        if key not in cfg:
            errors.append(f"missing top-level section: {key}")
    if errors:
        return errors

    deck   = cfg["deck"]
    safety = cfg.get("safety", {})

    # ── Deck slot presence + uniqueness ──────────────────────────────────────────
    slots = []
    for pos in ("tuberack", "plate", "paper", "tiprack"):
        if pos not in deck:
            errors.append(f"deck.{pos} missing")
            continue
        if "slot" not in deck[pos] or "load_name" not in deck[pos]:
            errors.append(f"deck.{pos} needs slot + load_name")
        else:
            slots.append(deck[pos]["slot"])
    if len(slots) != len(set(slots)):
        errors.append(f"deck slots must be distinct, got {slots}")

    # ── Dynamic limits from labware JSON + safety config ──────────────────────────
    plate_lw_data  = _load_labware_json(deck.get("plate", {}).get("load_name", ""))
    tiprack_lw_data = _load_labware_json(deck.get("tiprack", {}).get("load_name", ""))

    # Max well volume: from plate labware JSON, falling back to safety config
    max_well_vol = _labware_max_volume(
        plate_lw_data,
        fallback=float(safety.get("max_well_volume_ul",
                       safety.get("expected_plate_well_count", 360)))  # last-resort 360
    )
    # For the safety block we added max_well_volume_ul; but the plate JSON is authoritative
    if plate_lw_data is not None:
        max_well_vol = _labware_max_volume(plate_lw_data, max_well_vol)

    # Rows per column: from tiprack labware JSON, falling back to safety config
    tiprack_rows_per_col = _labware_rows_per_column(
        tiprack_lw_data,
        fallback=int(safety.get("tiprack_rows_per_column", 8)),
    )

    # Max column length (plate): from plate labware JSON, falling back to safety config
    max_col_len = _labware_rows_per_column(
        plate_lw_data,
        fallback=int(safety.get("expected_plate_well_count", 96) / 12),
    )

    # Pipette max volume: prefer safety config (set by operator), then known map, then 300
    pip_name = cfg["pipette"].get("name", "")
    pip_max  = float(safety.get("pipette_max_volume_ul",
                     _PIPETTE_MAX_UL.get(pip_name, 300.0)))

    # Source vial aspiration height: measured above the modeled vial bottom.
    source_height = float(cfg["sources"].get("vial_aspirate_height_mm", 1.0))
    vial_depth = float(safety.get("expected_depth_mm", 55.0))
    if not (0 < source_height < vial_depth):
        errors.append(
            f"sources.vial_aspirate_height_mm {source_height} must be > 0 and "
            f"< expected vial depth {vial_depth} mm")

    # ── Dilution plan ─────────────────────────────────────────────────────────────
    dil = cfg["dilution"]
    pr  = cfg["printing"]
    try:
        droplet_volume = float(pr.get("droplet_volume_ul", 0))
    except (TypeError, ValueError):
        droplet_volume = 0.0
        errors.append(f"printing.droplet_volume_ul must be numeric, got {pr.get('droplet_volume_ul')!r}")
    try:
        air_gap = float(pr.get("air_gap_ul", 0.0))
    except (TypeError, ValueError):
        air_gap = -1.0
        errors.append(f"printing.air_gap_ul must be numeric, got {pr.get('air_gap_ul')!r}")
    try:
        air_gap_height = float(pr.get("air_gap_height_mm", 10.0))
    except (TypeError, ValueError):
        air_gap_height = -1.0
        errors.append(f"printing.air_gap_height_mm must be numeric, got {pr.get('air_gap_height_mm')!r}")
    try:
        dwell_s = float(pr.get("post_dispense_delay_s", 0.0) or 0.0)
    except (TypeError, ValueError):
        dwell_s = -1.0
        errors.append(
            f"printing.post_dispense_delay_s must be numeric, got "
            f"{pr.get('post_dispense_delay_s')!r}")
    move_speed = pr.get("move_speed_mm_per_s")
    if move_speed is not None:
        try:
            move_speed = float(move_speed)
        except (TypeError, ValueError):
            errors.append(
                f"printing.move_speed_mm_per_s must be numeric or null, got "
                f"{pr.get('move_speed_mm_per_s')!r}")
            move_speed = 0.0
    if droplet_volume <= 0:
        errors.append(f"printing.droplet_volume_ul must be > 0, got {droplet_volume}")
    if air_gap < 0:
        errors.append(f"printing.air_gap_ul must be >= 0, got {air_gap}")
    if air_gap_height < 0:
        errors.append(f"printing.air_gap_height_mm must be >= 0, got {air_gap_height}")
    if dwell_s < 0:
        errors.append(f"printing.post_dispense_delay_s must be >= 0, got {dwell_s}")
    if move_speed is not None and move_speed <= 0:
        errors.append(f"printing.move_speed_mm_per_s must be > 0 or null, got {move_speed}")
    if droplet_volume + max(air_gap, 0.0) > pip_max:
        errors.append(
            f"printing droplet {droplet_volume} uL + air gap {air_gap} uL exceeds "
            f"pipette max {pip_max} uL")
    try:
        factors = _resolve_factors(dil)
    except (ValueError, KeyError) as e:
        errors.append(f"dilution.factors: {e}")
        factors = []
    if factors:
        if len(factors) > max_col_len:
            errors.append(
                f"{len(factors)} dilutions but plate column has only "
                f"{max_col_len} rows (from labware JSON or safety config)")
        total = float(dil.get("total_volume_ul", 0))
        if not (0 < total <= max_well_vol):
            errors.append(
                f"dilution.total_volume_ul {total} must be in (0, {max_well_vol}] "
                f"(from plate labware or safety config)")
        for f in factors:
            if f <= 0:
                errors.append(f"fold {f} must be > 0")
            elif round(total / f, 2) > pip_max:
                errors.append(
                    f"fold {f}: stock {round(total/f,2)} uL > pipette max "
                    f"{pip_max} uL (from safety config or known pipette map)")

    # ── Tip allocation feasibility ────────────────────────────────────────────────
    reserved = int(pr.get("print_block_column", 1))
    if not (1 <= reserved <= 12):
        errors.append(f"printing.print_block_column must be 1-12, got {reserved}")

    setup_tip = str(dil.get("setup_tip", "")).upper()
    if not setup_tip:
        single_cols = [int(c) for c in dil.get("single_tip_columns", [])]
        setup_cols = [c for c in single_cols if c != reserved]
        if not setup_cols:
            errors.append("dilution.setup_tip is required when no non-print single_tip_columns exist")
        else:
            setup_tip = f"H{setup_cols[0]}"

    if setup_tip:
        m = re.fullmatch(r"([A-Z]+)(\d+)", setup_tip)
        if not m:
            errors.append(f"dilution.setup_tip must look like H12, got {setup_tip!r}")
        else:
            setup_row, setup_col_s = m.groups()
            setup_col = int(setup_col_s)
            if not (1 <= setup_col <= 12):
                errors.append(f"dilution.setup_tip column must be 1-12, got {setup_tip}")
            if setup_col == reserved:
                errors.append(
                    f"dilution.setup_tip {setup_tip} overlaps full 8-tip print column "
                    f"{reserved}")
            single_start = str(cfg["pipette"].get("single_start", "A1")).upper()
            expected_setup_row = "H" if single_start.startswith("A") else "A"
            if setup_row != expected_setup_row:
                errors.append(
                    f"dilution.setup_tip must be in row {expected_setup_row} when "
                    f"pipette.single_start={single_start}; got {setup_tip}")

    # ── Pipette mount ─────────────────────────────────────────────────────────────
    if cfg["pipette"].get("mount") not in ("left", "right"):
        errors.append("pipette.mount must be left or right")

    # ── print_block_column required ───────────────────────────────────────────────
    if "print_block_column" not in pr:
        errors.append("printing.print_block_column is required")

    return errors


# ── Generation ────────────────────────────────────────────────────────────────────

def build_source(base_text: str, cfg: dict, run_modes: dict) -> str:
    # 1. Rewrite DEFAULT_* run-mode flags
    out = base_text
    for key, (pattern, template) in _FLAG_SUBS.items():
        if key in run_modes:
            out = pattern.sub(template.format(bool(run_modes[key])), out)

    # 2. Replace the CONFIG block between the sentinels
    i_start   = out.index(START_SENTINEL)
    line_start = out.rfind("\n", 0, i_start) + 1
    i_end     = out.index(END_SENTINEL)
    line_end  = out.index("\n", i_end)

    config_repr = pprint.pformat(cfg, indent=2, sort_dicts=False, width=100)
    new_region  = (
        f"{START_SENTINEL} (auto-generated from YAML; edit the YAML, not this file)\n"
        f"CONFIG = {config_repr}\n"
        f"{END_SENTINEL}"
    )
    return out[:line_start] + new_region + out[line_end:]


def simulate(path: Path) -> tuple:
    proc = subprocess.run(
        [sys.executable, "-c", _SHIM, "-L", str(LABWARE), path.name],
        cwd=str(path.parent), capture_output=True, text=True,
    )
    output = proc.stdout + "\n" + proc.stderr
    errors = [ln.strip() for ln in output.splitlines() if _ERROR_RE.search(ln)]
    return (proc.returncode == 0 and not errors), output


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build + simulate the vial-dilution-print protocol from YAML.")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG),
                    help="Path to the YAML config.")
    ap.add_argument("--no-sim", action="store_true",
                    help="Generate only; skip simulation.")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"ERROR: config not found: {cfg_path}")
        return 1
    full      = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    run_modes = full.pop("run_modes", {})   # not part of CONFIG
    config    = full

    problems = validate(config)
    if problems:
        print("CONFIG VALIDATION FAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("Config validation passed.")

    base_text = BASE_PROTOCOL.read_text(encoding="utf-8")
    generated = build_source(base_text, config, run_modes)

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    # Timestamp in local time with UTC offset suffix for unambiguous log correlation
    ts = datetime.now(tz=timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    run_path    = GENERATED_DIR / f"vial_dilution_print_run_{ts}.py"
    latest_path = GENERATED_DIR / "vial_dilution_print_latest.py"
    for dest in (run_path, latest_path):
        dest.write_text(generated, encoding="utf-8")
    print(f"Generated: {run_path.relative_to(REPO)}")
    print(f"Generated: {latest_path.relative_to(REPO)}")

    if args.no_sim:
        return 0

    ok, output = simulate(run_path)
    tail = "\n".join(line for line in output.splitlines()
                     if any(k in line for k in ("Pre-flight", "Series:", "Printing 8",
                                                "Returned", "Completed ===", "WARNING")))
    print("\n--- simulation key lines ---")
    print(tail)
    print("--- end ---")
    if ok:
        print("\nSIMULATION OK")
        return 0
    print("\nSIMULATION FAILED")
    print(output[-2000:])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
