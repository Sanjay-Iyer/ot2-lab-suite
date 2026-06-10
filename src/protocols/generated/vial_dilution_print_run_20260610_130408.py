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
CONFIG = { 'deck': { 'tuberack': { 'slot': 10,
                          'load_name': 'tuberack_3dprint_20ml_8vials_v2',
                          'namespace': 'custom_beta',
                          'version': 1},
            'plate': { 'slot': 1,
                       'load_name': 'corning_96_wellplate_360ul_custom',
                       'namespace': 'custom_beta',
                       'version': 1},
            'paper': { 'slot': 3,
                       'load_name': 'corning_96_wellplate_360ul_custom',
                       'namespace': 'custom_beta',
                       'version': 1},
            'tiprack': {'slot': 9, 'load_name': 'opentrons_96_tiprack_300ul'}},
  'pipette': {'name': 'p300_multi_gen2', 'mount': 'right', 'single_start': 'H1'},
  'sources': {'water_vial': 'A1', 'food_coloring_vial': 'B1'},
  'dilution': { 'enabled': True,
                'destination_column': '9',
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
                'source_column': '9',
                'droplet_volume_ul': 15.0,
                'num_replicates': 4,
                'paper_start_well': 'A9',
                'dispense_z_mm': 3.0,
                'replicate_spacing_mm': {'x': 9.0, 'y': 0.0, 'z': 0.0},
                'print_block_column': 1,
                'blow_out': False,
                'touch_tip': False},
  'tips': {'return_tips': True},
  'camera': { 'enabled': True,
              'capture_before': True,
              'capture_after': True,
              'capture_mid_rows': ['C', 'E', 'H'],
              'robot_image_dir': '/data/vision/vial_dilution_print',
              'robot_api_url': 'http://localhost:31950/camera/picture',
              'capture_timeout_s': 5},
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
              'pipette_min_accurate_ul': 20.0,
              'expected_plate_well_count': 96,
              'tiprack_rows_per_column': 8,
              'pipette_max_volume_ul': 300.0}}
# <<< CONFIG END <<<
# ════════════════════════════════════════════════════════════════════════════════


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


def resolve_dilution_wells(dilution_cfg: dict, n: int, plate_rows: list) -> list:
    """First n wells of the destination column, using plate row order from the labware.

    Parameters
    ----------
    dilution_cfg : CONFIG["dilution"]
    n            : number of wells needed (= len(factors))
    plate_rows   : ordered row labels derived from the loaded plate labware,
                   e.g. ['A','B','C','D','E','F','G','H'] for a 96-well plate.
    """
    col = str(dilution_cfg["destination_column"])
    if n > len(plate_rows):
        raise ValueError(
            f"Cannot resolve {n} dilution wells: plate only has "
            f"{len(plate_rows)} rows ({plate_rows})."
        )
    return [f"{plate_rows[i]}{col}" for i in range(n)]


def resolve_single_tips(dilution_cfg: dict, printing_cfg: dict,
                         n_needed: int, tiprack_rows: list) -> list:
    """Auto-allocate n_needed single tips from single_tip_columns, skipping the
    tiprack column reserved for the 8-channel print block.

    Parameters
    ----------
    tiprack_rows : ordered row labels from the loaded tiprack, e.g. ['A'..'H'].
                   Used instead of a hardcoded string so the count adapts if the
                   tiprack labware changes (e.g. 384-tip = 16 rows).
    """
    reserved = int(printing_cfg["print_block_column"])
    wells = []
    for col in dilution_cfg["single_tip_columns"]:
        if int(col) == reserved:
            continue
        for r in tiprack_rows:
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

def _preflight(protocol, lw, pipette, factors, dil_wells, single_tips,
               plate_rows: list, tiprack_rows: list):
    """Validate config + loaded-labware geometry BEFORE any motion. Raise to abort.

    Parameters
    ----------
    plate_rows   : row labels derived from lw["plate"] (e.g. ['A'..'H'])
    tiprack_rows : row labels derived from lw["tiprack"] (e.g. ['A'..'H'])
    """
    errors = []
    deck   = CONFIG["deck"]
    safety = CONFIG["safety"]
    dil    = CONFIG["dilution"]
    pr     = CONFIG["printing"]
    cam    = CONFIG["camera"]
    tol    = safety["geometry_tolerance_mm"]

    # ── Deck slot uniqueness ──────────────────────────────────────────────────────
    slots = [deck["tuberack"]["slot"], deck["plate"]["slot"],
             deck["paper"]["slot"], deck["tiprack"]["slot"]]
    if len(slots) != len(set(slots)):
        errors.append(f"Duplicate deck slot assignments: {slots}.")

    # ── Tube rack identity + geometry ─────────────────────────────────────────────
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

    # Diameter / depth: read from first well (A1) by well name
    a1 = tuberack["A1"]
    if a1.diameter is None or abs(a1.diameter - safety["expected_diameter_mm"]) > tol:
        errors.append(f"Vial A1 diameter {a1.diameter} != {safety['expected_diameter_mm']} mm.")
    if abs(a1.depth - safety["expected_depth_mm"]) > tol:
        errors.append(f"Vial A1 depth {a1.depth} != {safety['expected_depth_mm']} mm.")

    # Row/col spacing: derived from canonical column ordering — no hardcoded well names.
    tuberack_cols = tuberack.columns()   # list of columns; each column = list of wells
    if len(tuberack_cols) >= 1 and len(tuberack_cols[0]) >= 2:
        row_spacing = round(
            tuberack_cols[0][0].center().point.y - tuberack_cols[0][1].center().point.y, 2)
    else:
        row_spacing = 0.0
    if len(tuberack_cols) >= 2:
        col_spacing = round(
            tuberack_cols[1][0].center().point.x - tuberack_cols[0][0].center().point.x, 2)
    else:
        col_spacing = 0.0

    if abs(row_spacing - safety["expected_row_spacing_mm"]) > tol:
        errors.append(
            f"Vial row spacing {row_spacing} != {safety['expected_row_spacing_mm']} mm.")
    if abs(col_spacing - safety["expected_col_spacing_mm"]) > tol:
        errors.append(
            f"Vial col spacing {col_spacing} != {safety['expected_col_spacing_mm']} mm.")

    # ── Plate + paper well count (from safety config, not hardcoded) ──────────────
    expected_plate_wc = int(safety.get("expected_plate_well_count", 96))
    if len(lw["plate"].wells()) != expected_plate_wc:
        errors.append(
            f"Plate has {len(lw['plate'].wells())} wells, expected {expected_plate_wc}.")
    if len(lw["paper"].wells()) != expected_plate_wc:
        errors.append(
            f"Paper reference has {len(lw['paper'].wells())} wells, expected {expected_plate_wc}.")

    # ── Tiprack row count vs safety config ────────────────────────────────────────
    expected_tiprack_rows = int(safety.get("tiprack_rows_per_column", len(tiprack_rows)))
    if len(tiprack_rows) != expected_tiprack_rows:
        errors.append(
            f"Loaded tiprack has {len(tiprack_rows)} rows/column; "
            f"safety.tiprack_rows_per_column expected {expected_tiprack_rows}."
        )

    # ── Pipette identity ──────────────────────────────────────────────────────────
    if pipette.name != CONFIG["pipette"]["name"]:
        errors.append(f"Pipette '{pipette.name}' != expected '{CONFIG['pipette']['name']}'.")
    if pipette.mount != CONFIG["pipette"]["mount"]:
        errors.append(f"Pipette mount '{pipette.mount}' != '{CONFIG['pipette']['mount']}'.")

    # ── Geometry / count consistency ──────────────────────────────────────────────
    if len(factors) != len(dil_wells):
        errors.append(f"{len(factors)} factors but {len(dil_wells)} destination wells.")
    if len(dil_wells) > len(plate_rows):
        errors.append(
            f"Dilution needs {len(dil_wells)} wells; plate column only has "
            f"{len(plate_rows)} rows.")

    # ── Volume sanity ─────────────────────────────────────────────────────────────
    total = dil["total_volume_ul"]
    # Max volume: use the most conservative well in the plate (not hardcoded)
    well_max = min(w.max_volume for w in lw["plate"].wells())
    if total > well_max:
        errors.append(f"total_volume_ul {total} > plate well max {well_max} uL.")
    for well, fold in zip(dil_wells, factors):
        stock, water = dilution_volumes(total, fold)
        if stock < 0 or water < 0:
            errors.append(f"{well} ({fold}x): negative volume (stock={stock}, water={water}).")
        if stock > pipette.max_volume:
            errors.append(
                f"{well} ({fold}x): stock {stock} uL > pipette max {pipette.max_volume} uL.")
    if pr["droplet_volume_ul"] > pipette.max_volume:
        errors.append(
            f"droplet {pr['droplet_volume_ul']} uL > pipette max {pipette.max_volume} uL.")
    if dil["mix_volume_ul"] > pipette.max_volume:
        errors.append(
            f"mix volume {dil['mix_volume_ul']} uL > pipette max {pipette.max_volume} uL.")

    # ── Tip column overlap ────────────────────────────────────────────────────────
    print_col = int(pr["print_block_column"])
    if print_col in [int(c) for c in dil["single_tip_columns"]]:
        errors.append(
            f"print_block_column {pr['print_block_column']} overlaps single_tip_columns "
            f"{dil['single_tip_columns']} - dilution tips would clobber the print block."
        )

    # ── Camera: validate capture_mid_rows entries against actual plate rows ───────
    dest_col = str(dil["destination_column"])
    for row_letter in cam.get("capture_mid_rows", []):
        if row_letter not in plate_rows:
            errors.append(
                f"camera.capture_mid_rows entry '{row_letter}' is not a valid plate "
                f"row (valid rows: {plate_rows})."
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
    api_url    = CONFIG["camera"].get("robot_api_url", "http://localhost:31950/camera/picture")
    timeout_s  = int(CONFIG["camera"].get("capture_timeout_s", 5))
    protocol.comment(f"--- CV CAPTURE START: {filename} ---")
    try:
        os.makedirs(remote_dir, exist_ok=True)
    except Exception as _e:
        protocol.comment(f"Warning: could not create {remote_dir}: {_e}")
    if shutil.which("curl") is None:
        protocol.comment(f"Warning: 'curl' not found on robot; cannot capture {filename}.")
        return
    output_path = os.path.join(remote_dir, filename)
    cmd = ["curl", "-s", "-X", "POST", "-H", "opentrons-version: *",
           "--max-time", str(timeout_s), api_url, "--output", output_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if os.path.exists(output_path):
            sz = os.path.getsize(output_path)
            protocol.comment(f"Captured {filename} ({sz} bytes).")
            if sz < 1000:
                protocol.comment(f"Warning: {filename} suspiciously small ({sz} bytes).")
        else:
            protocol.comment(
                f"Warning: {filename} not created (curl rc={result.returncode}).")
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
    deck   = CONFIG["deck"]
    dil    = CONFIG["dilution"]
    pr     = CONFIG["printing"]
    cam    = CONFIG["camera"]
    return_tips = CONFIG["tips"]["return_tips"]

    def _return_or_drop():
        if pipette.has_tip:
            pipette.return_tip() if return_tips else pipette.drop_tip()

    # ── 1. Load labware + pipette FIRST ──────────────────────────────────────────
    # Labware is loaded before resolvers so we can derive the row/column structure
    # from the actual labware objects rather than hardcoding "ABCDEFGH".
    def _load(spec):
        kw = {}
        if spec.get("namespace"):
            kw = {"namespace": spec["namespace"], "version": spec.get("version", 1)}
        return protocol.load_labware(spec["load_name"], spec["slot"], **kw)

    lw = {
        "tuberack": _load(deck["tuberack"]),
        "plate":    _load(deck["plate"]),
        "paper":    _load(deck["paper"]),
        "tiprack":  _load(deck["tiprack"]),
    }
    pipette = protocol.load_instrument(
        CONFIG["pipette"]["name"], CONFIG["pipette"]["mount"], tip_racks=[lw["tiprack"]])

    # ── 2. Derive row structure from loaded labware (no hardcoded "ABCDEFGH") ─────
    # rows_by_name() preserves the labware's canonical row order (A, B, ... H for
    # standard 96-well; different for custom or 384-well labware).
    _plate_rows   = list(lw["plate"].rows_by_name().keys())
    _tiprack_rows = list(lw["tiprack"].rows_by_name().keys())

    # ── 3. Resolve the experiment from config + labware structure ─────────────────
    factors     = resolve_factors(dil)
    dil_wells   = resolve_dilution_wells(dil, len(factors), _plate_rows)
    single_tips = resolve_single_tips(dil, pr, 1 + len(dil_wells), _tiprack_rows)
    water_tip, stock_tips = single_tips[0], single_tips[1:]
    total = dil["total_volume_ul"]

    # Build mid-well capture set: row letter + destination column.
    # This decouples camera_mid_rows from column number — both can change independently.
    dest_col = str(dil["destination_column"])
    mid_wells_set = {f"{r}{dest_col}" for r in cam.get("capture_mid_rows", [])}

    # ── 4. PRE-FLIGHT ─────────────────────────────────────────────────────────────
    _preflight(protocol, lw, pipette, factors, dil_wells, single_tips,
               _plate_rows, _tiprack_rows)

    protocol.comment("=== Vial Dilution -> Paper Print Demo Started ===")
    protocol.comment(
        f"Flags: dry_run={params.dry_run}, do_dilution={params.do_dilution}, "
        f"do_print={params.do_print}"
    )
    water_vial = lw["tuberack"][CONFIG["sources"]["water_vial"]]
    fc_vial    = lw["tuberack"][CONFIG["sources"]["food_coloring_vial"]]
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

    # ── 5. CV: before ─────────────────────────────────────────────────────────────
    if cam["capture_before"]:
        _capture_image(protocol, "before_deck.jpg")
        _capture_image(protocol, "before_plate.jpg")

    # ── 6. Phase A: dilution (single nozzle) ──────────────────────────────────────
    if params.do_dilution and dil["enabled"]:
        pipette.configure_nozzle_layout(
            style=SINGLE, start=CONFIG["pipette"]["single_start"], tip_racks=[lw["tiprack"]])
        protocol.comment(
            f"Nozzle layout: SINGLE ({CONFIG['pipette']['single_start']}) for dilution.")

        # 6a. Water pass — one clean tip, water only (keeps the water vial pure).
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
            protocol.comment(
                f"Water pass done; "
                f"{'returned' if return_tips else 'dropped'} tip {water_tip}.")

        # 6b. Stock + mix pass — fresh tip per well (no carry-over; FC vial stays clean).
        for i, (well_name, fold) in enumerate(zip(dil_wells, factors)):
            stock, water_vol = dilution_volumes(total, fold)
            tip = stock_tips[i]
            pipette.pick_up_tip(lw["tiprack"][tip])
            protocol.comment(
                f"Diluting well {well_name} to {fold:g}x (stock {stock} uL); tip {tip}.")
            if stock > 0:
                pipette.aspirate(stock, fc_vial)
                pipette.dispense(stock, lw["plate"][well_name])
            # After dispensing, the tip is empty. Cap mix volume against:
            #   (a) configured mix_volume_ul,
            #   (b) the actual well fill (stock + water_vol = total, by construction),
            #   (c) pipette max_volume (defensive guard if config changes in future).
            well_fill = stock + water_vol   # == total by construction
            mix_vol   = min(dil["mix_volume_ul"], well_fill, float(pipette.max_volume))
            protocol.comment(f"Mixing {well_name} ({dil['mix_reps']} x {mix_vol} uL).")
            if dil["mix_reps"] > 0 and mix_vol > 0:
                pipette.mix(dil["mix_reps"], mix_vol, lw["plate"][well_name])
            _return_or_drop()
            if well_name in mid_wells_set:
                _capture_image(protocol, f"plate_dilution_{well_name}.jpg")
        protocol.comment(
            f"Dilution series complete in plate column {dil['destination_column']}.")
        _capture_image(protocol, "plate_after_dilution.jpg")

    # ── 7. Phase B: 8-channel print ───────────────────────────────────────────────
    if params.do_print and pr["enabled"]:
        _return_or_drop()
        pipette.configure_nozzle_layout(style=ALL, tip_racks=[lw["tiprack"]])
        protocol.comment("Nozzle layout: ALL (8 channels) for the column print.")

        # Derive block tip from labware column API — avoids hardcoding row "A".
        print_col_str = str(pr["print_block_column"])
        block_tip = lw["tiprack"].columns_by_name()[print_col_str][0]
        protocol.comment(
            f"8-channel block pickup of tiprack column {pr['print_block_column']} "
            f"({block_tip.well_name}).")
        pipette.pick_up_tip(block_tip)

        # Source well: top of the plate column (index 0 = A row for standard plates).
        src_col_str = str(pr["source_column"])
        src_well = lw["plate"].columns_by_name()[src_col_str][0]
        paper0 = lw["paper"][pr["paper_start_well"]]
        sp = pr["replicate_spacing_mm"]
        for rep in range(int(pr["num_replicates"])):
            protocol.comment(
                f"Aspirating {pr['droplet_volume_ul']} uL from plate column "
                f"{pr['source_column']} (8 channels) [replicate {rep + 1}]."
            )
            pipette.aspirate(pr["droplet_volume_ul"], src_well)
            # Use all three offsets from config; z defaults to 0.0 for flat paper.
            dest = paper0.bottom(pr["dispense_z_mm"]).move(
                Point(
                    x=rep * sp["x"],
                    y=rep * sp.get("y", 0.0),
                    z=rep * sp.get("z", 0.0),
                ))
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

    # ── 8. CV: after ──────────────────────────────────────────────────────────────
    if cam["capture_after"]:
        _capture_image(protocol, "after_deck.jpg")
        _capture_image(protocol, "after_plate.jpg")

    protocol.comment("=== Vial Dilution -> Paper Print Demo Completed ===")
