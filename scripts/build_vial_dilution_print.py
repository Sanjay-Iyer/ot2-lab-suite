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
import math
import pprint
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
BASE_PROTOCOL = REPO / "src" / "protocols" / "vial_dilution_print.py"
GENERATED_DIR = REPO / "src" / "protocols" / "generated"
LABWARE = REPO / "labware"
DEFAULT_CONFIG = REPO / "configs" / "workflows" / "defaults" / "vial_dilution_print.yaml"

START_SENTINEL = "# >>> CONFIG START >>>"
END_SENTINEL = "# <<< CONFIG END <<<"
_ROWS = "ABCDEFGH"

_SHIM = (
    "import numpy as np; "
    "np.trapz = getattr(np, 'trapezoid', np.trapz if hasattr(np, 'trapz') else None); "
    "from opentrons.simulate import main; main()"
)
_ERROR_RE = re.compile(r"Error|Traceback|Exception|not allowed", re.IGNORECASE)

_FLAG_SUBS = {
    "dry_run":     (re.compile(r"(?m)^DEFAULT_DRY_RUN\s*=.*$"),     "DEFAULT_DRY_RUN     = {}"),
    "do_dilution": (re.compile(r"(?m)^DEFAULT_DO_DILUTION\s*=.*$"), "DEFAULT_DO_DILUTION = {}"),
    "do_print":    (re.compile(r"(?m)^DEFAULT_DO_PRINT\s*=.*$"),    "DEFAULT_DO_PRINT    = {}"),
}


# ── Validation (mirrors the protocol's resolvers so errors surface early) ────────

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
    errors: list = []
    for key in ("deck", "pipette", "sources", "dilution", "printing", "tips", "camera", "safety"):
        if key not in cfg:
            errors.append(f"missing top-level section: {key}")
    if errors:
        return errors

    deck = cfg["deck"]
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

    dil = cfg["dilution"]
    pr = cfg["printing"]
    try:
        factors = _resolve_factors(dil)
    except (ValueError, KeyError) as e:
        errors.append(f"dilution.factors: {e}")
        factors = []
    if factors:
        if len(factors) > 8:
            errors.append(f"{len(factors)} dilutions but a column has only 8 wells")
        total = float(dil.get("total_volume_ul", 0))
        if not (0 < total <= 360):
            errors.append(f"dilution.total_volume_ul {total} must be in (0, 360]")
        for f in factors:
            if f <= 0:
                errors.append(f"fold {f} must be > 0")
            elif round(total / f, 2) > 300:
                errors.append(f"fold {f}: stock {round(total/f,2)} uL > 300 uL tip max")

    # tip allocation feasibility
    reserved = int(pr.get("print_block_column", 1))
    single_cols = [int(c) for c in dil.get("single_tip_columns", [])]
    if reserved in single_cols:
        errors.append(f"printing.print_block_column {reserved} overlaps single_tip_columns {single_cols}")
    n_single = len([f"{r}{c}" for c in single_cols if c != reserved for r in _ROWS])
    if factors and n_single < 1 + len(factors):
        errors.append(f"single_tip_columns give {n_single} tips; need {1 + len(factors)}")

    if cfg["pipette"].get("mount") not in ("left", "right"):
        errors.append("pipette.mount must be left or right")
    return errors


# ── Generation ───────────────────────────────────────────────────────────────────

def build_source(base_text: str, cfg: dict, run_modes: dict) -> str:
    # 1. rewrite DEFAULT_* run-mode flags
    out = base_text
    for key, (pattern, template) in _FLAG_SUBS.items():
        if key in run_modes:
            out = pattern.sub(template.format(bool(run_modes[key])), out)

    # 2. replace the CONFIG block between the sentinels
    i_start = out.index(START_SENTINEL)
    line_start = out.rfind("\n", 0, i_start) + 1
    i_end = out.index(END_SENTINEL)
    line_end = out.index("\n", i_end)

    config_repr = pprint.pformat(cfg, indent=2, sort_dicts=False, width=100)
    new_region = (
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
    ap = argparse.ArgumentParser(description="Build + simulate the vial-dilution-print protocol from YAML.")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to the YAML config.")
    ap.add_argument("--no-sim", action="store_true", help="Generate only; skip simulation.")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"ERROR: config not found: {cfg_path}")
        return 1
    full = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    run_modes = full.pop("run_modes", {})  # not part of CONFIG
    config = full

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
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    run_path = GENERATED_DIR / f"vial_dilution_print_run_{ts}.py"
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
