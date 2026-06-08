#!/usr/bin/env python3
"""
OT-2 Protocol: 20 mL Vial Dilution -> 8-Channel Paper Print Demo (config-driven)
================================================================================
Builds a food-coloring dilution series in one column of a 96-well plate, drawing
water and dye from two 20 mL scintillation vials in the custom v2 tube rack, then
picks up 8 tips at once and "prints" all 8 wells of that column onto paper as 8
simultaneous droplets. Tips are RETURNED to the box, not trashed. CV snapshots are
captured at the start, a few middle steps, and the end.

>>> EVERYTHING is driven by the CONFIG dict below. <<<
Edit CONFIG directly for a quick change, OR keep the canonical settings in
  configs/workflows/defaults/vial_dilution_print.yaml
and run  scripts/build_vial_dilution_print.py  to generate a robot-ready copy with
that YAML embedded between the CONFIG markers. See docs/vial_dilution_print_demo.md.

DECK / WHY SLOT 6 / WHY apiLevel 2.28 - see the demo guide. Short version:
  * tip box must NOT be directly behind the tuberack (slot 4) - idle nozzles in
    single-tip mode collide with it; slot 6 is clear.
  * apiLevel 2.28 is required for return_tip() in partial (single-nozzle) mode.

RUN-MODE FLAGS (App Runtime Parameters; DEFAULT_* mirror them for simulation):
  dry_run / do_dilution / do_print
"""

from opentrons import protocol_api
from opentrons.protocol_api import SINGLE, ALL
from opentrons.types import Point
import math
import os
import shutil
import subprocess

metadata = {
    "protocolName": "OT-2 Vial Dilution -> 8-Channel Paper Print Demo",
    "author": "Antigravity AI Agent",
    "description": (
        "Config-driven direct dilution series from two 20 mL vials into a 96-well "
        "column, then an 8-channel simultaneous print of that column onto paper."
    ),
    "apiLevel": "2.28",  # 2.28 REQUIRED: partial-mode return_tip() (blocked < 2.28)
}

# ── Runtime-parameter DEFAULTS (operator overrides in the App per run) ──────────
DEFAULT_DRY_RUN     = False
DEFAULT_DO_DILUTION = True
DEFAULT_DO_PRINT    = True

# ════════════════════════════════════════════════════════════════════════════════
# >>> CONFIG START >>> (auto-generated from YAML; edit the YAML, not this file)
CONFIG = { 'deck': { 'tuberack': { 'slot': 1,
                          'load_name': 'tuberack_3dprint_20ml_8vials_v2',
                          'namespace': 'custom_beta',
                          'version': 1},
            'plate': { 'slot': 2,
                       'load_name': 'corning_96_wellplate_360ul_custom',
                       'namespace': 'custom_beta',
                       'version': 1},
            'paper': { 'slot': 3,
                       'load_name': 'corning_96_wellplate_360ul_custom',
                       'namespace': 'custom_beta',
                       'version': 1},
            'tiprack': {'slot': 6, 'load_name': 'opentrons_96_tiprack_300ul'}},
  'pipette': {'name': 'p300_multi_gen2', 'mount': 'right', 'single_start': 'H1'},
  'sources': {'water_vial': 'A1', 'food_coloring_vial': 'A2'},
  'dilution': { 'enabled': True,
                'destination_column': '1',
                'total_volume_ul': 200.0,
                'factors': { 'mode': 'explicit',
                             'explicit': [1, 2, 5, 10, 20, 30, 40, 50],
                             'step_factor': 2,
                             'start': 1,
                             'end': 50,
                             'count': 8},
                'mix_reps': 3,
                'mix_volume_ul': 120.0,
                'single_tip_columns': [12, 11]},
  'printing': { 'enabled': True,
                'source_column': '1',
                'droplet_volume_ul': 15.0,
                'num_replicates': 1,
                'paper_start_well': 'A1',
                'dispense_z_mm': 3.0,
                'replicate_spacing_mm': {'x': 12.0, 'y': 0.0},
                'print_block_column': 1,
                'blow_out': False,
                'touch_tip': False},
  'tips': {'return_tips': True},
  'camera': { 'enabled': True,
              'capture_before': True,
              'capture_after': True,
              'capture_mid_wells': ['C1', 'E1', 'H1'],
              'robot_image_dir': '/data/vision/vial_dilution_print'},
  'flow_rates': {'aspirate': None, 'dispense': None, 'mix': None},
  'cv': { 'expected_droplets': 8,
          'min_circularity_ok': 0.6,
          'detection': {'threshold_method': 'otsu', 'min_area': 250, 'invert': True}},
  'safety': { 'expected_tuberack_load_name': 'tuberack_3dprint_20ml_8vials_v2',
              'expected_well_count': 8,
              'expected_diameter_mm': 28.0,
              'expected_depth_mm': 55.0,
              'expected_row_spacing_mm': 34.0,
              'expected_col_spacing_mm': 31.0,
              'geometry_tolerance_mm': 0.5,
              'pipette_min_accurate_ul': 20.0}}
# <<< CONFIG END <<<
# ════════════════════════════════════════════════════════════════════════════════

_ROWS = "ABCDEFGH"


def add_parameters(parameters: protocol_api.ParameterContext):
    parameters.add_bool(
        variable_name="dry_run", display_name="Dry run (no liquid)",
        description="Load labware, run pre-flight and comments only - no liquid motion.",
        default=DEFAULT_DRY_RUN)
    parameters.add_bool(
        variable_name="do_dilution", display_name="Run dilution phase",
        description="Build the dilution series in the plate column.",
        default=DEFAULT_DO_DILUTION)
    parameters.add_bool(
        variable_name="do_print", display_name="Run print phase",
        description="Pick up 8 tips and print the plate column onto paper.",
        default=DEFAULT_DO_PRINT)


# ── Config resolvers ─────────────────────────────────────────────────────────────

def resolve_factors(dilution_cfg: dict) -> list:
    """Return the ordered list of fold factors from the factors config."""
    fc = dilution_cfg["factors"]
    mode = fc.get("mode", "explicit")
    if mode == "explicit":
        return [float(x) for x in fc["explicit"]]
    count = int(fc.get("count", 8))
    start = float(fc.get("start", 1))
    if mode == "geometric":
        step = float(fc.get("step_factor", 2))
        return [round(start * (step ** i), 4) for i in range(count)]
    end = float(fc.get("end", 50))
    if mode == "linear":
        if count == 1:
            return [start]
        return [round(start + (end - start) * i / (count - 1), 4) for i in range(count)]
    if mode == "log":
        if count == 1:
            return [start]
        lo, hi = math.log(start), math.log(end)
        return [round(math.exp(lo + (hi - lo) * i / (count - 1)), 4) for i in range(count)]
    raise ValueError(f"Unknown dilution factors mode: {mode!r}")


def resolve_dilution_wells(dilution_cfg: dict, n: int) -> list:
    """First n wells of the destination column, top (A) to bottom (H)."""
    col = str(dilution_cfg["destination_column"])
    return [f"{_ROWS[i]}{col}" for i in range(n)]


def resolve_single_tips(dilution_cfg: dict, printing_cfg: dict, n_needed: int) -> list:
    """Auto-allocate n_needed single tips from single_tip_columns, skipping the
    tiprack column reserved for the 8-channel print block."""
    reserved = int(printing_cfg["print_block_column"])
    wells = []
    for col in dilution_cfg["single_tip_columns"]:
        if int(col) == reserved:
            continue
        for r in _ROWS:
            wells.append(f"{r}{int(col)}")
    if len(wells) < n_needed:
        raise RuntimeError(
            f"Need {n_needed} single tips but single_tip_columns only provide "
            f"{len(wells)} (excluding reserved print column {reserved})."
        )
    return wells[:n_needed]


def dilution_volumes(total: float, fold: float) -> tuple:
    """(stock_uL, water_uL) for a fold dilution at `total` uL."""
    stock = round(total / fold, 2)
    water = round(total - stock, 2)
    return stock, water


# ── Pre-flight ───────────────────────────────────────────────────────────────────

def _preflight(protocol, lw, pipette, factors, dil_wells, single_tips):
    """Validate config + loaded-labware geometry BEFORE any motion. Raise to abort."""
    errors = []
    deck = CONFIG["deck"]
    safety = CONFIG["safety"]
    dil = CONFIG["dilution"]
    pr = CONFIG["printing"]
    tol = safety["geometry_tolerance_mm"]

    slots = [deck["tuberack"]["slot"], deck["plate"]["slot"],
             deck["paper"]["slot"], deck["tiprack"]["slot"]]
    if len(slots) != len(set(slots)):
        errors.append(f"Duplicate deck slot assignments: {slots}.")

    tuberack = lw["tuberack"]
    if tuberack.load_name != safety["expected_tuberack_load_name"]:
        errors.append(
            f"Loaded tube rack '{tuberack.load_name}' != expected "
            f"'{safety['expected_tuberack_load_name']}'."
        )
    if len(tuberack.wells()) != safety["expected_well_count"]:
        errors.append(
            f"Tube rack has {len(tuberack.wells())} wells, expected "
            f"{safety['expected_well_count']}."
        )
    a1 = tuberack["A1"]
    if a1.diameter is None or abs(a1.diameter - safety["expected_diameter_mm"]) > tol:
        errors.append(f"Vial A1 diameter {a1.diameter} != {safety['expected_diameter_mm']} mm.")
    if abs(a1.depth - safety["expected_depth_mm"]) > tol:
        errors.append(f"Vial A1 depth {a1.depth} != {safety['expected_depth_mm']} mm.")
    row_spacing = round(tuberack["A1"].center().point.y - tuberack["B1"].center().point.y, 2)
    col_spacing = round(tuberack["A2"].center().point.x - tuberack["A1"].center().point.x, 2)
    if abs(row_spacing - safety["expected_row_spacing_mm"]) > tol:
        errors.append(f"Vial row spacing {row_spacing} != {safety['expected_row_spacing_mm']} mm.")
    if abs(col_spacing - safety["expected_col_spacing_mm"]) > tol:
        errors.append(f"Vial col spacing {col_spacing} != {safety['expected_col_spacing_mm']} mm.")

    if len(lw["plate"].wells()) != 96:
        errors.append(f"Plate has {len(lw['plate'].wells())} wells, expected 96.")
    if len(lw["paper"].wells()) != 96:
        errors.append(f"Paper reference has {len(lw['paper'].wells())} wells, expected 96.")

    if pipette.name != CONFIG["pipette"]["name"]:
        errors.append(f"Pipette '{pipette.name}' != expected '{CONFIG['pipette']['name']}'.")
    if pipette.mount != CONFIG["pipette"]["mount"]:
        errors.append(f"Pipette mount '{pipette.mount}' != '{CONFIG['pipette']['mount']}'.")

    # Geometry / count consistency
    if len(factors) != len(dil_wells):
        errors.append(f"{len(factors)} factors but {len(dil_wells)} destination wells.")
    if len(dil_wells) > 8:
        errors.append(f"Dilution needs {len(dil_wells)} wells; a column has only 8.")

    # Volume sanity
    total = dil["total_volume_ul"]
    well_max = lw["plate"]["A1"].max_volume
    if total > well_max:
        errors.append(f"total_volume_ul {total} > well max {well_max} uL.")
    for well, fold in zip(dil_wells, factors):
        stock, water = dilution_volumes(total, fold)
        if stock < 0 or water < 0:
            errors.append(f"{well} ({fold}x): negative volume (stock={stock}, water={water}).")
        if stock > pipette.max_volume:
            errors.append(f"{well} ({fold}x): stock {stock} uL > pipette max {pipette.max_volume}.")
    if pr["droplet_volume_ul"] > pipette.max_volume:
        errors.append(f"droplet {pr['droplet_volume_ul']} uL > pipette max {pipette.max_volume}.")
    if dil["mix_volume_ul"] > pipette.max_volume:
        errors.append(f"mix volume {dil['mix_volume_ul']} uL > pipette max {pipette.max_volume}.")

    if int(pr["print_block_column"]) in [int(c) for c in dil["single_tip_columns"]]:
        errors.append(
            f"print_block_column {pr['print_block_column']} overlaps single_tip_columns "
            f"{dil['single_tip_columns']} - dilution tips would clobber the print block."
        )

    if errors:
        raise RuntimeError(
            "PRE-FLIGHT VALIDATION FAILED - no motion performed:\n  - " + "\n  - ".join(errors)
        )
    protocol.comment("Pre-flight validation passed: config + labware geometry OK.")

    # Non-fatal accuracy warnings (sub-minimum stock volumes)
    min_ok = safety["pipette_min_accurate_ul"]
    for well, fold in zip(dil_wells, factors):
        stock, _ = dilution_volumes(total, fold)
        if 0 < stock < min_ok:
            protocol.comment(
                f"WARNING: {well} ({fold}x) stock {stock} uL is below the p300 "
                f"~{min_ok:.0f} uL accurate minimum (visual demo only)."
            )


def _capture_image(protocol, filename: str) -> None:
    """Capture a JPEG from the OT-2 camera over HTTP. No-op while simulating."""
    if not CONFIG["camera"]["enabled"]:
        return
    if protocol.is_simulating():
        protocol.comment(f"[SIMULATION] Mock photo: {filename}")
        return
    remote_dir = CONFIG["camera"]["robot_image_dir"]
    protocol.comment(f"--- CV CAPTURE START: {filename} ---")
    try:
        os.makedirs(remote_dir, exist_ok=True)
    except Exception as _e:
        protocol.comment(f"Warning: could not create {remote_dir}: {_e}")
    if shutil.which("curl") is None:
        protocol.comment(f"Warning: 'curl' not found on robot; cannot capture {filename}.")
        return
    output_path = os.path.join(remote_dir, filename)
    cmd = ["curl", "-s", "-X", "POST", "-H", "opentrons-version: *", "--max-time", "5",
           "http://localhost:31950/camera/picture", "--output", output_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if os.path.exists(output_path):
            sz = os.path.getsize(output_path)
            protocol.comment(f"Captured {filename} ({sz} bytes).")
            if sz < 1000:
                protocol.comment(f"Warning: {filename} suspiciously small ({sz} bytes).")
        else:
            protocol.comment(f"Warning: {filename} not created (curl rc={result.returncode}).")
    except Exception as _e:
        protocol.comment(f"Warning: camera capture error for {filename}: {_e}")
    protocol.comment(f"--- CV CAPTURE END: {filename} ---")


def _apply_flow_rates(pipette):
    fr = CONFIG["flow_rates"]
    if fr.get("aspirate"):
        pipette.flow_rate.aspirate = fr["aspirate"]
    if fr.get("dispense"):
        pipette.flow_rate.dispense = fr["dispense"]


def run(protocol: protocol_api.ProtocolContext):
    params = protocol.params
    deck = CONFIG["deck"]
    dil = CONFIG["dilution"]
    pr = CONFIG["printing"]
    cam = CONFIG["camera"]
    return_tips = CONFIG["tips"]["return_tips"]

    def _return_or_drop():
        if pipette.has_tip:
            pipette.return_tip() if return_tips else pipette.drop_tip()

    # ── Resolve the experiment from config ───────────────────────────────────────
    factors = resolve_factors(dil)
    dil_wells = resolve_dilution_wells(dil, len(factors))
    single_tips = resolve_single_tips(dil, pr, 1 + len(dil_wells))  # 1 water + per-well
    water_tip, stock_tips = single_tips[0], single_tips[1:]
    total = dil["total_volume_ul"]

    # ── 1. Load labware + pipette ────────────────────────────────────────────────
    def _load(spec):
        kw = {}
        if spec.get("namespace"):
            kw = {"namespace": spec["namespace"], "version": spec.get("version", 1)}
        return protocol.load_labware(spec["load_name"], spec["slot"], **kw)

    lw = {
        "tuberack": _load(deck["tuberack"]),
        "plate": _load(deck["plate"]),
        "paper": _load(deck["paper"]),
        "tiprack": _load(deck["tiprack"]),
    }
    pipette = protocol.load_instrument(
        CONFIG["pipette"]["name"], CONFIG["pipette"]["mount"], tip_racks=[lw["tiprack"]])

    # ── 2. PRE-FLIGHT ────────────────────────────────────────────────────────────
    _preflight(protocol, lw, pipette, factors, dil_wells, single_tips)

    protocol.comment("=== Vial Dilution -> Paper Print Demo Started ===")
    protocol.comment(
        f"Flags: dry_run={params.dry_run}, do_dilution={params.do_dilution}, "
        f"do_print={params.do_print}"
    )
    water_vial = lw["tuberack"][CONFIG["sources"]["water_vial"]]
    fc_vial = lw["tuberack"][CONFIG["sources"]["food_coloring_vial"]]
    protocol.comment(
        f"Sources: water=vial {CONFIG['sources']['water_vial']}, "
        f"food colouring=vial {CONFIG['sources']['food_coloring_vial']} "
        f"(slot {deck['tuberack']['slot']}). Series: "
        + ", ".join(f"{w}={f:g}x" for w, f in zip(dil_wells, factors))
    )

    if params.dry_run:
        protocol.comment("DRY RUN: labware + pipette loaded, pre-flight passed. No liquid motion.")
        protocol.comment("=== Vial Dilution -> Paper Print Demo Completed (dry run) ===")
        return

    _apply_flow_rates(pipette)

    # ── 3. CV: before ────────────────────────────────────────────────────────────
    if cam["capture_before"]:
        _capture_image(protocol, "before_deck.jpg")
        _capture_image(protocol, "before_plate.jpg")

    # ── 4. Phase A: dilution (single nozzle) ─────────────────────────────────────
    if params.do_dilution and dil["enabled"]:
        pipette.configure_nozzle_layout(
            style=SINGLE, start=CONFIG["pipette"]["single_start"], tip_racks=[lw["tiprack"]])
        protocol.comment(f"Nozzle layout: SINGLE ({CONFIG['pipette']['single_start']}) for dilution.")

        # 4a. Water pass - one clean tip, water only (keeps the water vial pure).
        water_steps = [(w, dilution_volumes(total, f)[1]) for w, f in zip(dil_wells, factors)]
        water_steps = [(w, v) for (w, v) in water_steps if v > 0]
        if water_steps:
            pipette.pick_up_tip(lw["tiprack"][water_tip])
            protocol.comment(f"Water pass: picked tip {water_tip}.")
            for well_name, vol in water_steps:
                protocol.comment(f"Dispensing {vol} uL water -> {well_name}.")
                pipette.aspirate(vol, water_vial)
                pipette.dispense(vol, lw["plate"][well_name])
            _return_or_drop()
            protocol.comment(f"Water pass done; {'returned' if return_tips else 'dropped'} tip {water_tip}.")

        # 4b. Stock + mix pass - fresh tip per well (no carry-over; FC vial stays clean).
        for i, (well_name, fold) in enumerate(zip(dil_wells, factors)):
            stock, _water = dilution_volumes(total, fold)
            tip = stock_tips[i]
            pipette.pick_up_tip(lw["tiprack"][tip])
            protocol.comment(f"Diluting well {well_name} to {fold:g}x (stock {stock} uL); tip {tip}.")
            if stock > 0:
                pipette.aspirate(stock, fc_vial)
                pipette.dispense(stock, lw["plate"][well_name])
            mix_vol = min(dil["mix_volume_ul"], total)
            protocol.comment(f"Mixing {well_name} ({dil['mix_reps']} x {mix_vol} uL).")
            if dil["mix_reps"] > 0:
                pipette.mix(dil["mix_reps"], mix_vol, lw["plate"][well_name])
            _return_or_drop()
            if well_name in cam["capture_mid_wells"]:
                _capture_image(protocol, f"plate_dilution_{well_name}.jpg")
        protocol.comment(f"Dilution series complete in plate column {dil['destination_column']}.")
        _capture_image(protocol, "plate_after_dilution.jpg")

    # ── 5. Phase B: 8-channel print ──────────────────────────────────────────────
    if params.do_print and pr["enabled"]:
        _return_or_drop()
        pipette.configure_nozzle_layout(style=ALL, tip_racks=[lw["tiprack"]])
        protocol.comment("Nozzle layout: ALL (8 channels) for the column print.")
        block_tip = f"A{int(pr['print_block_column'])}"
        protocol.comment(f"8-channel block pickup of tiprack column {pr['print_block_column']} ({block_tip}).")
        pipette.pick_up_tip(lw["tiprack"][block_tip])

        src = f"A{pr['source_column']}"
        paper0 = lw["paper"][pr["paper_start_well"]]
        sp = pr["replicate_spacing_mm"]
        for rep in range(int(pr["num_replicates"])):
            protocol.comment(
                f"Aspirating {pr['droplet_volume_ul']} uL from plate column "
                f"{pr['source_column']} (8 channels) [replicate {rep + 1}]."
            )
            pipette.aspirate(pr["droplet_volume_ul"], lw["plate"][src])
            dest = paper0.bottom(pr["dispense_z_mm"]).move(
                Point(x=rep * sp["x"], y=rep * sp["y"], z=0))
            protocol.comment(
                f"Printing 8 droplets onto paper (slot {deck['paper']['slot']}) "
                f"replicate {rep + 1} at z={pr['dispense_z_mm']} mm."
            )
            pipette.dispense(pr["droplet_volume_ul"], dest)
            if pr.get("blow_out"):
                pipette.blow_out(dest)
            if pr.get("touch_tip"):
                pipette.touch_tip()
            _capture_image(protocol, f"paper_print_{rep + 1:02d}.jpg")

        _return_or_drop()
        protocol.comment(
            f"{'Returned' if return_tips else 'Dropped'} the 8 print tips "
            f"({'none disposed' if return_tips else 'disposed to trash'})."
        )

    # ── 6. CV: after ─────────────────────────────────────────────────────────────
    if cam["capture_after"]:
        _capture_image(protocol, "after_deck.jpg")
        _capture_image(protocol, "after_plate.jpg")

    protocol.comment("=== Vial Dilution -> Paper Print Demo Completed ===")
