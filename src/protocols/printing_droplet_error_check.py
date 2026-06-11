#!/usr/bin/env python3
"""
OT-2 Protocol: 8-Channel Droplet Print Error Check (standalone diagnostic)
=========================================================================
Print-ONLY diagnostic to debug intermittent droplet misses (one of the 8 channels not
depositing on a replicate). It:
  1. picks up 8 tips from a tiprack column,
  2. aspirates a small droplet from a 96-well plate column (default 9, wells A..H),
  3. snaps a BEFORE image of the blank paper,
  4. prints onto the paper at a chosen column (optionally several replicates),
  5. snaps an AFTER image,
  6. returns the 8 tips.

RUN VIA THE HTTP API runner: scripts/run_droplet_error_check.py — the SAME transport as
run_vial_print_robot.py (port 31950). The runner uploads this file, sets the knobs as
runtime parameters, plays the run, then SCP-pulls the per-run image folder to the laptop.

WHY apiLevel 2.28 + HTTP API: >= 2.14 runs through the Protocol Engine, which needs a
deck configuration. The App / HTTP API supply it; bare `opentrons_execute` does NOT
(that path fails with AreaNotInDeckConfigurationError). Runtime parameters need >= 2.18.

PREREQUISITE: the plate column you aspirate from (default 9) must already CONTAIN liquid.

RUNTIME PARAMETERS (set per run by the runner or in the App):
  dry_run · run_id · paper_start_column · num_replicates · droplet_volume_ul ·
  dispense_z_mm · air_gap_ul · blow_out
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
        "Print-only diagnostic: grab 8 tips, aspirate a plate column, photograph + print "
        "onto the paper at a chosen column, return tips. Knobs are runtime parameters."
    ),
    "apiLevel": "2.28",  # 2.28: engine path; deck config from App/HTTP API (NOT opentrons_execute)
}

# ── Defaults (runtime parameters below override these per run) ────────────────────
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
    "camera": {
        "enabled": True,
        "robot_image_dir": "/data/vision/droplet_error_check",   # per-run subfolder added at runtime
        "robot_api_url": "http://localhost:31950/camera/picture",
        "capture_timeout_s": 5,
    },
}


def add_parameters(parameters: protocol_api.ParameterContext):
    pr = CONFIG["print"]
    parameters.add_bool(
        variable_name="dry_run", display_name="Dry run (no liquid)",
        description="Load + pre-flight + comments only; no liquid motion or images.",
        default=False)
    parameters.add_int(
        variable_name="run_tag", display_name="Run tag",
        description="Numeric tag for this run's image folder (host passes a timestamp). 0 = auto.",
        default=0, minimum=0, maximum=4102444800)
    parameters.add_int(
        variable_name="paper_start_column", display_name="Paper column",
        description="Paper column to print on (1 = far left).",
        default=int(pr["paper_start_column"]), minimum=1, maximum=12)
    parameters.add_int(
        variable_name="num_replicates", display_name="Replicates",
        description="How many prints (each = 8 droplets), sweeping right.",
        default=int(pr["num_replicates"]), minimum=1, maximum=12)
    parameters.add_float(
        variable_name="droplet_volume_ul", display_name="Droplet volume uL",
        description="Volume dispensed per droplet per channel.",
        default=float(pr["droplet_volume_ul"]), minimum=1.0, maximum=300.0)
    parameters.add_float(
        variable_name="dispense_z_mm", display_name="Dispense height mm",
        description="Tip height above paper-proxy well bottom. Lower = closer to paper.",
        default=float(pr["dispense_z_mm"]), minimum=0.0, maximum=30.0)
    parameters.add_float(
        variable_name="air_gap_ul", display_name="Air gap uL",
        description="Anti-drip air pulled below the droplet. 0 disables it.",
        default=float(pr["air_gap_ul"]), minimum=0.0, maximum=50.0)
    parameters.add_bool(
        variable_name="blow_out", display_name="Blow out",
        description="Blow out at the paper after each dispense.",
        default=bool(pr["blow_out"]))


def _resolve_run_id(params) -> str:
    """Image-folder name. The host passes a numeric run_tag (a unix timestamp) so it can
    pull the exact folder; 0 -> a local timestamp for manual App runs."""
    tag = int(getattr(params, "run_tag", 0) or 0)
    if tag > 0:
        return f"run_{tag}"
    return datetime.now().strftime("run_%Y%m%d_%H%M%S")


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
    params = protocol.params
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

    # ── Resolve knobs from runtime parameters ─────────────────────────────────────
    source_col         = str(pr["source_column"])
    paper_start_column = int(params.paper_start_column)
    num_replicates     = int(params.num_replicates)
    droplet_volume     = float(params.droplet_volume_ul)
    dispense_z         = float(params.dispense_z_mm)
    air_gap            = float(params.air_gap_ul)
    do_blow_out        = bool(params.blow_out)
    dry_run            = bool(params.dry_run)
    run_id             = _resolve_run_id(params)
    run_dir            = f"{CONFIG['camera']['robot_image_dir']}/{run_id}"

    plate_rows   = list(lw["plate"].rows_by_name().keys())
    tiprack_rows = list(lw["tiprack"].rows_by_name().keys())
    print_col    = int(pr["print_block_column"])
    print_tip    = f"{tiprack_rows[0]}{print_col}"
    src_anchor   = f"{plate_rows[0]}{source_col}"
    paper_anchor = f"{plate_rows[0]}{paper_start_column}"

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
        # Anti-drip air gap: pull air in AFTER the liquid so it sits at the tip opening.
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
