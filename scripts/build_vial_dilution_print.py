#!/usr/bin/env python3
"""
scripts/build_vial_dilution_print.py
====================================
Turn configs/workflows/defaults/vial_dilution_print.yaml into a robot-ready copy of
src/protocols/printing/01_vial_dilution_paper_print.py with the YAML embedded as the CONFIG dict, then
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
import ast
import json
import math
import os
import pprint
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

REPO          = Path(__file__).resolve().parent.parent
PRINTING_DIR  = REPO / "src" / "protocols" / "printing"
BASE_PROTOCOL = PRINTING_DIR / "01_vial_dilution_paper_print.py"

# Protocol versions. v1 = P300-only dilution; v2 additionally routes dilution
# transfers at/below dilution.small_volume.threshold_ul to the P20. A config selects
# one with `protocol_version: 2` (or --protocol-version 2); each version generates
# under its own basename so the two never overwrite each other.
PROTOCOL_VERSIONS: dict[int, tuple[Path, str]] = {
    1: (BASE_PROTOCOL, "vial_dilution_print"),
    2: (PRINTING_DIR / "02_vial_dilution_paper_print_p20_dilution.py",
        "vial_dilution_print_v2"),
    3: (PRINTING_DIR / "03_vial_dilution_paper_print_v3.py",
        "vial_dilution_print_v3"),
}

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
    r"ProtocolCommandFailedError|InvalidProtocolData|FileNotFoundError|"
    r"KeyError|AttributeError",
    re.IGNORECASE,
)

_FLAG_SUBS = {
    "dry_run":     (re.compile(r"(?m)^DEFAULT_DRY_RUN\s*=.*$"),     "DEFAULT_DRY_RUN     = {}"),
    "do_dilution": (re.compile(r"(?m)^DEFAULT_DO_DILUTION\s*=.*$"), "DEFAULT_DO_DILUTION = {}"),
    "do_print":    (re.compile(r"(?m)^DEFAULT_DO_PRINT\s*=.*$"),    "DEFAULT_DO_PRINT    = {}"),
}

V3_API_LEVEL = "2.15"
V3_SIMULATOR_VERSION = "7.0.2"
V3_SIMULATOR_PYTHON = (
    REPO / ".venv" / "ot2-api-2.15-py310" / "python.exe"
)
_V3_FORBIDDEN_NOZZLE_NAMES = {
    "SINGLE",
    "PARTIAL_COLUMN",
    "COLUMN",
    "ROW",
    "ALL",
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


def _v3_tip_locations(config: dict) -> list[str]:
    p20 = config.get("tips", {}).get("p20", {})
    locations = [str(p20.get("water", "")), str(p20.get("stock", ""))]
    by_row = p20.get("print_by_row", {})
    locations.extend(str(by_row.get(row, "")) for row in "ABCDEFGH")
    if config.get("dilution", {}).get("stock_tip_policy") == "per_well":
        stock_by_row = p20.get("stock_by_row", {})
        locations = [str(p20.get("water", ""))]
        locations.extend(str(stock_by_row.get(row, "")) for row in "ABCDEFGH")
        locations.extend(str(by_row.get(row, "")) for row in "ABCDEFGH")
    return locations


def _v3_source_problems(source: str) -> list[str]:
    """Static checks for API-2.15 and P300 targeting invariants."""
    problems: list[str] = []
    if "configure_" + "nozzle_layout" in source:
        problems.append("v3 source contains a partial-nozzle configuration call")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"v3 source is not valid Python: {exc}"]

    api_level = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "requirements":
                    try:
                        req = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        req = {}
                    api_level = str(req.get("apiLevel", ""))
                    if req.get("robotType") != "OT-2":
                        problems.append("v3 requirements.robotType must be OT-2")
    if api_level != V3_API_LEVEL:
        problems.append(
            f"v3 emitted apiLevel must be {V3_API_LEVEL}, got {api_level or '(missing)'}"
        )

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imported: set[str] = set()
            if isinstance(node, ast.ImportFrom):
                imported.update(alias.name for alias in node.names)
            else:
                imported.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
            bad = imported & _V3_FORBIDDEN_NOZZLE_NAMES
            if bad:
                problems.append(
                    "v3 imports nozzle-layout constant(s): " + ", ".join(sorted(bad))
                )
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if not isinstance(owner, ast.Name) or owner.id != "p300":
            continue
        rendered_args = " ".join(
            ast.get_source_segment(source, arg) or "" for arg in node.args
        )
        rendered_args += " " + " ".join(
            ast.get_source_segment(source, kw.value) or "" for kw in node.keywords
        )
        if "tuberack" in rendered_args:
            problems.append(
                f"P300 command {node.func.attr} targets the slot-7 tuberack"
            )
    return problems


def validate_v3(config: dict, source: str) -> list[str]:
    """Validate v3's physical design before a generated file can be emitted."""
    errors = _v3_source_problems(source)
    deck = config.get("deck", {})
    expected_slots = {
        "tuberack": 7,
        "plate": 4,
        "paper": 5,
        "tiprack": 8,
        "tiprack_p20": 9,
    }
    for role, expected in expected_slots.items():
        actual = deck.get(role, {}).get("slot")
        if int(actual or -1) != expected:
            errors.append(f"v3 deck.{role}.slot must be {expected}, got {actual}")

    pipettes = {p.get("name"): p for p in config.get("pipettes", [])}
    if pipettes.get("p20_single_gen2", {}).get("mount") != "left":
        errors.append("v3 requires p20_single_gen2 on the left mount")
    if pipettes.get("p300_multi_gen2", {}).get("mount") != "right":
        errors.append("v3 requires p300_multi_gen2 on the right mount")

    dilution = config.get("dilution", {})
    factors = _resolve_factors(dilution)
    total = float(dilution.get("total_volume_ul", 0.0))
    max_transfer = float(dilution.get("max_transfer_ul", 0.0))
    dead_volume = float(dilution.get("dead_volume_ul", 0.0))
    if dilution.get("transfer_pipette") != "p20_single_gen2":
        errors.append("v3 dilution.transfer_pipette must be p20_single_gen2")
    if not (0 < max_transfer <= 20.0):
        errors.append(
            f"v3 dilution.max_transfer_ul must be in (0, 20], got {max_transfer}"
        )
    chunk_limit = max_transfer if max_transfer > 0 else 20.0
    if len(factors) != 8:
        errors.append(f"v3 requires exactly 8 dilution factors, got {len(factors)}")

    print_groups = config.get("print_groups", [])
    print_total = sum(float(g.get("volume_ul", 0.0)) for g in print_groups)
    if total > 340.0:
        errors.append(f"v3 V={total:g} uL exceeds the 340 uL safe well fill")
    if total + 0.01 < print_total + dead_volume:
        errors.append(
            f"v3 V={total:g} uL must be >= print consumption {print_total:g} "
            f"+ dead volume {dead_volume:g} uL"
        )
    for factor in factors:
        if factor <= 0:
            errors.append(f"dilution factor must be positive, got {factor}")
            continue
        stock = total / factor
        water = total - stock
        if abs((stock + water) - total) > 0.1:
            errors.append(
                f"{factor:g}x stock + water differs from V by more than 0.1 uL"
            )
        for name, volume in (("stock", stock), ("water", water)):
            remaining = volume
            while remaining > 0.01:
                chunk = min(chunk_limit, remaining)
                if chunk > 20.0:
                    errors.append(
                        f"{factor:g}x {name} chunk {chunk:g} uL exceeds P20 capacity"
                    )
                remaining = round(remaining - chunk, 6)

    expected_print = [(1, 20.0), (2, 15.0), (3, 10.0), (4, 5.0)]
    actual_print = [
        (int(g.get("destination", {}).get("paper_start_column", 0)),
         float(g.get("volume_ul", 0.0)))
        for g in print_groups
    ]
    if actual_print != expected_print:
        errors.append(
            f"v3 paper plan must be {expected_print}, got {actual_print}"
        )
    for group in print_groups:
        if group.get("pipette") != "p20_single_gen2":
            errors.append(f"{group.get('name')}: v3 printing must use the P20")
        if group.get("layout") != "single_spot":
            errors.append(f"{group.get('name')}: v3 printing must use single_spot")
        if float(group.get("volume_ul", 0.0)) > 20.0:
            errors.append(f"{group.get('name')}: P20 print volume exceeds 20 uL")
        group_tips = group.get("tips", {})
        if (
            group_tips.get("strategy") != "per_source_row"
            or group_tips.get("map_ref") != "print_by_row"
        ):
            errors.append(
                f"{group.get('name')}: v3 must reference the print_by_row tip map"
            )

    tip_locations = _v3_tip_locations(config)
    if any(not re.fullmatch(r"[A-H](?:[1-9]|1[0-2])", tip) for tip in tip_locations):
        errors.append(f"v3 P20 tip map has invalid locations: {tip_locations}")
    if len(tip_locations) != len(set(tip_locations)):
        errors.append(f"v3 P20 tip map must be unique, got {tip_locations}")
    if dilution.get("stock_tip_policy") == "single" and len(tip_locations) != 10:
        errors.append("v3 single-stock-tip plan must allocate exactly 10 P20 tips")

    mix_volume = float(dilution.get("mix_volume_ul", 0.0))
    if not (20.0 <= mix_volume < total):
        errors.append(
            f"v3 mix volume must be >=20 uL and below V; got {mix_volume:g}"
        )
    if int(config.get("tips", {}).get("p300", {}).get("mix_block_column", 0)) != 1:
        errors.append("v3 P300 mix tips must be full tiprack column 1")

    rack_json = _load_labware_json(
        deck.get("tuberack", {}).get("load_name", "")
    )
    if rack_json is None:
        errors.append("v3 custom tuberack JSON is missing")
    else:
        declared = float(rack_json.get("dimensions", {}).get("zDimension", 0.0))
        well_tops = [
            float(well.get("z", 0.0)) + float(well.get("depth", 0.0))
            for well in rack_json.get("wells", {}).values()
        ]
        physical_top = max(well_tops, default=0.0)
        expected = float(
            config.get("safety", {}).get("expected_tuberack_z_dimension_mm", 0.0)
        )
        clearance = float(
            config.get("safety", {}).get("p300_travel_clearance_mm", 0.0)
        )
        if abs(declared - physical_top) > 0.5:
            errors.append(
                f"tuberack zDimension {declared:g} mm does not cover well top "
                f"{physical_top:g} mm"
            )
        if abs(declared - expected) > 0.5:
            errors.append(
                f"tuberack zDimension {declared:g} mm differs from expected {expected:g}"
            )
        if declared + clearance <= physical_top:
            errors.append(
                "configured P300 travel envelope does not clear the slot-7 rack"
            )
    return errors


def _v3_simulator_problem(python_executable: str) -> Optional[str]:
    check = subprocess.run(
        [
            python_executable,
            "-c",
            "import importlib.metadata; "
            "print(importlib.metadata.version('opentrons'))",
        ],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        return (
            f"{python_executable} cannot load opentrons package metadata; "
            f"install requirements-ot2-api-2.15.txt in the isolated simulator "
            f"environment ({check.stderr.strip()})"
        )
    installed = check.stdout.strip().splitlines()[-1]
    if installed != V3_SIMULATOR_VERSION:
        return (
            f"opentrons=={V3_SIMULATOR_VERSION} is required for v3 simulation; "
            f"{python_executable} reports {installed}. Set OT2_API_2_15_PYTHON or "
            "pass --simulator-python."
        )
    return None


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


def simulate(path: Path, python_executable: str | None = None) -> tuple:
    python_executable = python_executable or sys.executable
    simulator_config = REPO / ".test_tmp" / "opentrons-simulator"
    simulator_config.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["OT_API_CONFIG_DIR"] = str(simulator_config)
    proc = subprocess.run(
        [python_executable, "-c", _SHIM, "-L", str(LABWARE), path.name],
        cwd=str(path.parent), capture_output=True, text=True, env=env,
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
    ap.add_argument("--protocol-version", type=int, default=None,
                    choices=sorted(PROTOCOL_VERSIONS),
                    help="Base protocol to build from. Default: the config's "
                         "'protocol_version' key, else 1.")
    ap.add_argument(
        "--set-dry-run", choices=("true", "false"), default=None,
        help="Override the generated DEFAULT_DRY_RUN flag.",
    )
    ap.add_argument(
        "--set-do-dilution", choices=("true", "false"), default=None,
        help="Override the generated DEFAULT_DO_DILUTION flag.",
    )
    ap.add_argument(
        "--set-do-print", choices=("true", "false"), default=None,
        help="Override the generated DEFAULT_DO_PRINT flag.",
    )
    ap.add_argument(
        "--simulator-python",
        default=None,
        help="Python executable for simulation. v3 requires an isolated interpreter "
             "with opentrons==7.0.2. Default: OT2_API_2_15_PYTHON, then the current "
             "interpreter.",
    )
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"ERROR: config not found: {cfg_path}")
        return 1
    full      = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    run_modes = full.pop("run_modes", {})   # not part of CONFIG
    for key, value in (
        ("dry_run", args.set_dry_run),
        ("do_dilution", args.set_do_dilution),
        ("do_print", args.set_do_print),
    ):
        if value is not None:
            run_modes[key] = value == "true"

    # Protocol version: CLI wins, else the config's own declaration, else v1.
    version = args.protocol_version or int(full.pop("protocol_version", 1))
    if version not in PROTOCOL_VERSIONS:
        print(f"ERROR: unknown protocol_version {version} "
              f"(known: {sorted(PROTOCOL_VERSIONS)})")
        return 1
    base_protocol, generated_stem = PROTOCOL_VERSIONS[version]
    if not base_protocol.exists():
        print(f"ERROR: base protocol for version {version} not found: {base_protocol}")
        return 1
    print(f"Protocol version: v{version} ({base_protocol.relative_to(REPO)})")

    # Authoritative shared validation + normalization (pipette selection, materials,
    # print-group layout/destination rules). One implementation of each rule lives in
    # src/core/*; the build script never re-implements them. Produces the internal
    # CONFIG the protocol executes (resolved print_groups + normalized sections).
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from src.core.workflow_config import normalize_and_validate, WorkflowConfigError
    is_legacy = isinstance(full.get("printing"), dict) and "droplet_volume_ul" in full["printing"]
    try:
        nw = normalize_and_validate(full)
    except WorkflowConfigError as e:
        print("CONFIG VALIDATION FAILED (shared validators):")
        print(f"  {e}")
        return 1
    config = nw.config
    config["protocol_version"] = version
    for note in nw.notes:
        print(f"  [normalize] {note}")
    for w in [i for i in nw.issues if i.severity == "warning"]:
        print(f"  [warning] {w}")

    # Deck-slot uniqueness (structural check on the normalized deck).
    slots: dict = {}
    for role, spec in config.get("deck", {}).items():
        s = spec.get("slot")
        if s in slots:
            print(f"CONFIG VALIDATION FAILED: deck slot {s} shared by '{slots[s]}' and '{role}'.")
            return 1
        slots[s] = role

    # Legacy flat configs also run the historical structural validator (tip
    # allocation, geometry, factor sanity) written against the old printing schema.
    if is_legacy:
        problems = validate(config)
        if problems:
            print("CONFIG VALIDATION FAILED:")
            for p in problems:
                print(f"  - {p}")
            return 1
    base_text = base_protocol.read_text(encoding="utf-8")
    if version == 3:
        problems = validate_v3(config, base_text)
        if problems:
            print("CONFIG VALIDATION FAILED (v3 safety rules):")
            for problem in problems:
                print(f"  - {problem}")
            return 1
    print("Config validation passed.")

    generated = build_source(base_text, config, run_modes)
    if version == 3:
        source_problems = _v3_source_problems(generated)
        if source_problems:
            print("GENERATED SOURCE VALIDATION FAILED (v3 safety rules):")
            for problem in source_problems:
                print(f"  - {problem}")
            return 1

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    # Timestamp in local time with UTC offset suffix for unambiguous log correlation
    ts = datetime.now(tz=timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    run_path    = GENERATED_DIR / f"{generated_stem}_run_{ts}.py"
    latest_path = GENERATED_DIR / f"{generated_stem}_latest.py"
    for dest in (run_path, latest_path):
        dest.write_text(generated, encoding="utf-8")
    print(f"Generated: {run_path.relative_to(REPO)}")
    print(f"Generated: {latest_path.relative_to(REPO)}")

    if args.no_sim:
        return 0

    simulator_python = (
        args.simulator_python
        or os.environ.get("OT2_API_2_15_PYTHON")
        or (str(V3_SIMULATOR_PYTHON) if V3_SIMULATOR_PYTHON.exists() else None)
        or sys.executable
    )
    if version == 3:
        simulator_problem = _v3_simulator_problem(simulator_python)
        if simulator_problem:
            print(f"SIMULATION ENVIRONMENT INVALID: {simulator_problem}")
            return 1

    ok, output = simulate(run_path, simulator_python if version == 3 else sys.executable)
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
