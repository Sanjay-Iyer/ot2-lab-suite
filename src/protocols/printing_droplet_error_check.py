#!/usr/bin/env python3
"""
OT-2 Protocol: 8-Channel Droplet Print Error Check (standalone diagnostic)
=========================================================================
Minimal print-ONLY protocol to debug intermittent droplet misses (e.g. one of the
8 channels not depositing on one replicate). It:
  1. picks up 8 tips from one tiprack column,
  2. aspirates a small droplet from a 96-well plate column (default 9, wells A..H),
  3. prints it onto the paper sheet at a chosen paper column,
  4. optionally repeats as replicates sweeping right,
  5. returns the 8 tips to the box.

No dilution, no vials, no camera — just the print step, isolated.

RUN FROM THE TERMINAL via opentrons_execute over SSH (apiLevel 2.15 -> no Opentrons
App / deck configuration needed):

    # copy this file onto the robot, then on the robot shell:
    opentrons_execute printing_droplet_error_check.py

Set the knobs WITHOUT editing the file using environment variables (or just edit
CONFIG below). Env vars win over CONFIG:

    PRINT_PAPER_COLUMN=5 PRINT_REPLICATES=3 opentrons_execute printing_droplet_error_check.py

  PRINT_PAPER_COLUMN  paper column to print on (1 = far left)
  PRINT_REPLICATES    how many prints (each = 8 droplets), sweeping right
  PRINT_DROPLET_UL    volume per droplet per channel
  PRINT_DISPENSE_Z    tip height above paper-proxy well bottom (lower = closer)
  PRINT_AIR_GAP_UL    anti-drip air below the droplet (0 disables)
  PRINT_BLOW_OUT      1/0 — blow out at the paper after each dispense
  PRINT_DRY_RUN       1/0 — load + pre-flight + comments only, no liquid

PREREQUISITE: the plate column you aspirate from (default 9) must already CONTAIN
liquid — run the dilution first, or hand-fill that column with dye/water.

WHY apiLevel 2.15: <=2.15 runs via opentrons_execute over SSH; >=2.16 requires the
App/deck-config. This protocol uses only a full 8-channel pickup (no partial nozzle),
so 2.15 is sufficient and keeps it terminal-runnable.
"""

import os
import subprocess
from datetime import datetime

from opentrons import protocol_api
from opentrons.types import Point

metadata = {
    "protocolName": "8-Channel Droplet Print Error Check",
    "author": "Antigravity AI Agent",
    "description": (
        "Print-only diagnostic: grab 8 tips, aspirate a plate column, print onto the "
        "paper at a chosen column, return tips. Knobs via env vars or CONFIG."
    ),
    "apiLevel": "2.15",  # <=2.15 so it runs via opentrons_execute over SSH (no App)
}

# ── Defaults (override per run with the PRINT_* env vars above) ───────────────────
CONFIG = {
    "deck": {
        "plate":   {"slot": 4, "load_name": "corning_96_wellplate_360ul_custom",
                    "namespace": "custom_beta", "version": 1},
        "paper":   {"slot": 5, "load_name": "corning_96_wellplate_360ul_custom",
                    "namespace": "custom_beta", "version": 1},
        "tiprack": {"slot": 9, "load_name": "opentrons_96_tiprack_300ul"},
    },
    "pipette": {"name": "p300_multi_gen2", "mount": "right"},
    "print": {
        "source_column": "9",          # plate column to aspirate from (wells A..H)
        "print_block_column": 1,       # tiprack column the 8 tips come from
        "paper_start_column": 1,       # default paper column to print on (1 = far left)
        "num_replicates": 3,           # default repeats (reproduce the triplicate case)
        "droplet_volume_ul": 15.0,
        "dispense_z_mm": 3.0,          # height above the paper-proxy well bottom
        "air_gap_ul": 10.0,            # anti-drip air pulled below the droplet
        "replicate_spacing_mm": {"x": 9.0, "y": 0.0, "z": 0.0},
        "blow_out": False,
        "return_tips": True,           # True = return tips to box; False = trash
    },
    "safety": {
        "expected_plate_well_count": 96,
        "pipette_max_volume_ul": 300.0,
    },
    # ── Camera: a per-run subfolder is appended at runtime (see _run_id) ──────────
    "camera": {
        "enabled": True,
        "robot_image_dir": "/data/vision/droplet_error_check",
        "robot_api_url": "http://localhost:31950/camera/picture",
        "capture_timeout_s": 5,
    },
}


# ── Env-var knob readers (terminal overrides; fall back to CONFIG) ────────────────

def _env_int(name: str, default) -> int:
    v = os.getenv(name)
    return int(v) if v not in (None, "") else int(default)


def _env_float(name: str, default) -> float:
    v = os.getenv(name)
    return float(v) if v not in (None, "") else float(default)


def _env_bool(name: str, default) -> bool:
    v = os.getenv(name)
    if v in (None, ""):
        return bool(default)
    return v.strip().lower() in ("1", "true", "yes", "on")


def _run_id() -> str:
    """Per-run identifier: PRINT_RUN_ID if the host set one (so it can pull the exact
    folder), otherwise a local timestamp. Used to give every run its own image folder."""
    return os.getenv("PRINT_RUN_ID") or datetime.now().strftime("run_%Y%m%d_%H%M%S")


def _capture_image(protocol, run_dir: str, filename: str) -> None:
    """Capture a JPEG from the OT-2 camera into run_dir/filename. No-op while simulating."""
    cam = CONFIG["camera"]
    if not cam.get("enabled", True):
        return
    if protocol.is_simulating():
        protocol.comment(f"[SIMULATION] Mock photo -> {run_dir}/{filename}")
        return
    import shutil
    api_url   = cam.get("robot_api_url", "http://localhost:31950/camera/picture")
    timeout_s = int(cam.get("capture_timeout_s", 5))
    try:
        os.makedirs(run_dir, exist_ok=True)
    except Exception as exc:
        protocol.comment(f"Warning: could not create {run_dir}: {exc}")
    if shutil.which("curl") is None:
        protocol.comment(f"Warning: 'curl' not found on robot; cannot capture {filename}.")
        return
    out_path = os.path.join(run_dir, filename)
    cmd = ["curl", "-s", "-X", "POST", "-H", "opentrons-version: *",
           "--max-time", str(timeout_s), api_url, "--output", out_path]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=False)
        if os.path.exists(out_path):
            sz = os.path.getsize(out_path)
            protocol.comment(f"Captured {filename} ({sz} bytes) -> {out_path}.")
            if sz < 1000:
                protocol.comment(f"Warning: {filename} suspiciously small ({sz} bytes).")
        else:
            protocol.comment(f"Warning: {filename} not created (camera capture failed).")
    except Exception as exc:
        protocol.comment(f"Warning: camera capture error for {filename}: {exc}")


def _preflight(protocol, lw, pipette, source_col, paper_start_column, num_replicates,
               droplet_volume, air_gap):
    """Validate config + loaded labware BEFORE any motion. Raise to abort."""
    errors = []
    deck   = CONFIG["deck"]
    safety = CONFIG["safety"]
    pr     = CONFIG["print"]

    slots = [deck["plate"]["slot"], deck["paper"]["slot"], deck["tiprack"]["slot"]]
    if len(slots) != len(set(slots)):
        errors.append(f"Duplicate deck slots: {slots}.")

    expected_wc = int(safety["expected_plate_well_count"])
    if len(lw["plate"].wells()) != expected_wc:
        errors.append(f"Plate has {len(lw['plate'].wells())} wells, expected {expected_wc}.")
    if len(lw["paper"].wells()) != expected_wc:
        errors.append(f"Paper has {len(lw['paper'].wells())} wells, expected {expected_wc}.")

    plate_rows = list(lw["plate"].rows_by_name().keys())
    present = set(lw["plate"].wells_by_name().keys())
    for r in plate_rows:
        w = f"{r}{source_col}"
        if w not in present:
            errors.append(f"Source well {w} not present in plate.")

    n_paper_cols = len(lw["paper"].columns())
    if not (1 <= paper_start_column <= n_paper_cols):
        errors.append(f"paper column {paper_start_column} out of range (1..{n_paper_cols}).")
    elif paper_start_column + num_replicates - 1 > n_paper_cols:
        last = paper_start_column + num_replicates - 1
        errors.append(
            f"print sweep runs off the paper: start {paper_start_column} + "
            f"{num_replicates} replicate(s) reaches column {last} > {n_paper_cols}."
        )

    if num_replicates < 1:
        errors.append(f"num_replicates {num_replicates} must be >= 1.")
    if droplet_volume <= 0:
        errors.append(f"droplet volume {droplet_volume} must be > 0.")
    if droplet_volume + air_gap > pipette.max_volume:
        errors.append(
            f"droplet {droplet_volume} + air gap {air_gap} = {droplet_volume + air_gap} uL "
            f"> pipette max {pipette.max_volume} uL."
        )

    tiprack_rows = list(lw["tiprack"].rows_by_name().keys())
    tip_anchor = f"{tiprack_rows[0]}{int(pr['print_block_column'])}"
    if tip_anchor not in set(lw["tiprack"].wells_by_name().keys()):
        errors.append(f"print tip anchor {tip_anchor} not in tiprack.")

    if pipette.name != CONFIG["pipette"]["name"]:
        errors.append(f"Pipette '{pipette.name}' != '{CONFIG['pipette']['name']}'.")
    if pipette.mount != CONFIG["pipette"]["mount"]:
        errors.append(f"Pipette mount '{pipette.mount}' != '{CONFIG['pipette']['mount']}'.")

    if errors:
        raise RuntimeError(
            "PRE-FLIGHT VALIDATION FAILED - no motion performed:\n  - " + "\n  - ".join(errors)
        )
    protocol.comment("Pre-flight validation passed: config + labware OK.")


def run(protocol: protocol_api.ProtocolContext):
    deck = CONFIG["deck"]
    pr   = CONFIG["print"]
    return_tips = pr["return_tips"]

    def _load(spec):
        kw = {}
        if spec.get("namespace"):
            kw = {"namespace": spec["namespace"], "version": spec.get("version", 1)}
        return protocol.load_labware(spec["load_name"], spec["slot"], **kw)

    lw = {
        "plate":   _load(deck["plate"]),
        "paper":   _load(deck["paper"]),
        "tiprack": _load(deck["tiprack"]),
    }
    pipette = protocol.load_instrument(
        CONFIG["pipette"]["name"], CONFIG["pipette"]["mount"], tip_racks=[lw["tiprack"]]
    )

    # ── Resolve knobs: env var overrides CONFIG ───────────────────────────────────
    source_col         = str(pr["source_column"])
    paper_start_column = _env_int("PRINT_PAPER_COLUMN", pr["paper_start_column"])
    num_replicates     = _env_int("PRINT_REPLICATES", pr["num_replicates"])
    droplet_volume     = _env_float("PRINT_DROPLET_UL", pr["droplet_volume_ul"])
    dispense_z         = _env_float("PRINT_DISPENSE_Z", pr["dispense_z_mm"])
    air_gap            = _env_float("PRINT_AIR_GAP_UL", pr["air_gap_ul"])
    do_blow_out        = _env_bool("PRINT_BLOW_OUT", pr["blow_out"])
    dry_run            = _env_bool("PRINT_DRY_RUN", False)

    plate_rows   = list(lw["plate"].rows_by_name().keys())
    tiprack_rows = list(lw["tiprack"].rows_by_name().keys())
    print_col    = int(pr["print_block_column"])
    print_tip    = f"{tiprack_rows[0]}{print_col}"
    src_anchor   = f"{plate_rows[0]}{source_col}"
    paper_anchor = f"{plate_rows[0]}{paper_start_column}"

    run_id  = _run_id()
    run_dir = f"{CONFIG['camera']['robot_image_dir']}/{run_id}"

    _preflight(protocol, lw, pipette, source_col, paper_start_column, num_replicates,
               droplet_volume, air_gap)

    protocol.comment("=== Droplet Print Error Check Started ===")
    protocol.comment(f"Run ID: {run_id}  ·  images -> {run_dir}")
    protocol.comment(
        f"Knobs: paper_column={paper_start_column}, replicates={num_replicates}, "
        f"droplet={droplet_volume:g} uL, dispense_z={dispense_z:g} mm, "
        f"air_gap={air_gap:g} uL, blow_out={do_blow_out}, dry_run={dry_run}"
    )
    protocol.comment(
        f"Plan: grab 8 tips (tiprack col {print_col}) -> aspirate plate column {source_col} "
        f"(wells {plate_rows[0]}{source_col}..{plate_rows[-1]}{source_col}) -> print onto paper "
        f"columns {paper_start_column}..{paper_start_column + num_replicates - 1} -> "
        f"{'return' if return_tips else 'drop'} tips."
    )

    if dry_run:
        protocol.comment("DRY RUN: loaded + pre-flight passed. No liquid motion.")
        protocol.comment("=== Droplet Print Error Check Completed (dry run) ===")
        return

    pipette.pick_up_tip(lw["tiprack"][print_tip])
    protocol.comment(f"Picked up 8 tips from tiprack column {print_col} (anchor {print_tip}).")

    # BEFORE image: blank paper, tips loaded, prior to the first droplet.
    protocol.comment("Capturing BEFORE-print image (blank paper).")
    _capture_image(protocol, run_dir, "print_before.jpg")

    paper_well = lw["paper"][paper_anchor]
    spacing = pr["replicate_spacing_mm"]

    for rep in range(num_replicates):
        protocol.comment(
            f"Replicate {rep + 1}/{num_replicates}: aspirating {droplet_volume:g} uL from "
            f"plate column {source_col}."
        )
        pipette.aspirate(droplet_volume, lw["plate"][src_anchor])
        # Anti-drip air gap: pull air in AFTER the liquid so it sits at the tip opening
        # and holds the droplet in during the move to the paper.
        if air_gap > 0:
            pipette.air_gap(air_gap)
            protocol.comment(f"Pulled {air_gap:g} uL air gap below the droplet (anti-drip).")
        dest = paper_well.bottom(dispense_z).move(
            Point(
                x=rep * spacing["x"],
                y=rep * spacing.get("y", 0.0),
                z=rep * spacing.get("z", 0.0),
            )
        )
        protocol.comment(
            f"Printing 8 droplets onto paper (slot {deck['paper']['slot']}) -> paper column "
            f"~{paper_start_column + rep}, replicate {rep + 1}, z={dispense_z:g} mm."
        )
        # Dispense the droplet AND the air gap (air exits first, then the liquid).
        pipette.dispense(droplet_volume + air_gap, dest)
        if do_blow_out:
            pipette.blow_out(dest)

    # AFTER image: the finished print, before returning the tips.
    protocol.comment("Capturing AFTER-print image (finished droplets).")
    _capture_image(protocol, run_dir, "print_after.jpg")

    if pipette.has_tip:
        pipette.return_tip() if return_tips else pipette.drop_tip()
    protocol.comment(
        f"{'Returned' if return_tips else 'Dropped'} the 8 tips. "
        f"{num_replicates} replicate(s) printed at paper columns "
        f"{paper_start_column}..{paper_start_column + num_replicates - 1}."
    )
    protocol.comment(f"Images for this run are in {run_dir} (print_before.jpg, print_after.jpg).")
    protocol.comment("=== Droplet Print Error Check Completed ===")
