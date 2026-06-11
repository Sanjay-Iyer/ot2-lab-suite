#!/usr/bin/env python3
"""
OT-2 Protocol: 20 mL Vial Dilution -> Single-Tip Paper Print Demo (config-driven)
================================================================================
Builds a food-coloring dilution series in one column of a 96-well plate, drawing
water and dye from two 20 mL scintillation vials in the custom v2 tube rack with
exactly one SINGLE-nozzle setup tip, then switches to the full 8-channel head to
print one 8-droplet row onto paper. Tips are RETURNED to the box, not trashed.
CV snapshots are captured at the start, a few middle steps, and the end.

>>> EVERYTHING is driven by the CONFIG dict below. <<<
Edit CONFIG directly for a quick change, OR keep the canonical settings in
  configs/workflows/defaults/vial_dilution_print.yaml
and run  scripts/build_vial_dilution_print.py  to generate a robot-ready copy with
that YAML embedded between the CONFIG markers. See docs/vial_dilution_print_demo.md.

DECK / WHY apiLevel 2.28 / HOW TO RUN - see the demo guide + skills/ot2-robot-profile.
Short version:
  * Deck slots are config-driven (see CONFIG["deck"]). Single-nozzle setup uses
    active nozzle A1 and picks an H-row tip so only one tip engages, matching the
    3d_print_labware_validate protocol. Verified: rack=7, plate=4, paper=5,
    tips=9, single_start=A1, setup_tip=H12.
  * apiLevel 2.28 is required for return_tip() in partial (single-nozzle) mode.
  * RUN PATH: 2.28 uses the new protocol engine, so on the real OT-2 run this from
    the Opentrons App (it provides the deck configuration). Bare `opentrons_execute`
    over SSH fails with AreaNotInDeckConfigurationError - that is expected, not a bug.

RUN-MODE FLAGS (App Runtime Parameters; DEFAULT_* mirror them for simulation):
  dry_run / do_dilution / do_print
"""

from opentrons import protocol_api
from opentrons.protocol_api import ALL, SINGLE
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
        "column using one setup tip, then one 8-channel print of that column onto paper."
    ),
    "apiLevel": "2.28",  # 2.28 REQUIRED: partial-mode return_tip() (blocked < 2.28)
}

# ── Runtime-parameter DEFAULTS (operator overrides in the App per run) ──────────
DEFAULT_DRY_RUN     = False
DEFAULT_DO_DILUTION = True
DEFAULT_DO_PRINT    = True

# ════════════════════════════════════════════════════════════════════════════════
# >>> CONFIG START >>> (auto-generated from YAML; edit the YAML, not this file)
CONFIG = { 'deck': { 'tuberack': { 'slot': 7,
                          'load_name': 'tuberack_3dprint_20ml_8vials_v2',
                          'namespace': 'custom_beta',
                          'version': 1},
            'plate': { 'slot': 4,
                       'load_name': 'corning_96_wellplate_360ul_custom',
                       'namespace': 'custom_beta',
                       'version': 1},
            'paper': { 'slot': 5,
                       'load_name': 'corning_96_wellplate_360ul_custom',
                       'namespace': 'custom_beta',
                       'version': 1},
            'tiprack': {'slot': 9, 'load_name': 'opentrons_96_tiprack_300ul'}},
  'pipette': {'name': 'p300_multi_gen2', 'mount': 'right', 'single_start': 'A1'},
  'sources': {'water_vial': 'A1', 'food_coloring_vial': 'A2', 'vial_aspirate_height_mm': 2.0},
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
                'setup_tip': 'H12',
                'single_tip_columns': [12]},
  'printing': { 'enabled': True,
                'source_column': '9',
                'droplet_volume_ul': 15.0,
                'num_replicates': 3,
                'paper_start_column': 1,
                'dispense_z_mm': 3.0,
                'air_gap_ul': 10.0,
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
        description="Pick up 8 tips and print the plate column onto paper once.",
        default=DEFAULT_DO_PRINT)
    parameters.add_int(
        variable_name="print_start_column", display_name="Paper start column",
        description="Leftmost paper column to start on (1=far left); raise to skip already-printed columns.",
        default=int(CONFIG["printing"].get("paper_start_column", 1)),
        minimum=1, maximum=12)


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


def _well_row(well_name: str) -> str:
    return "".join(ch for ch in str(well_name).upper() if ch.isalpha())


def _well_column(well_name: str) -> int:
    digits = "".join(ch for ch in str(well_name) if ch.isdigit())
    if not digits:
        raise ValueError(f"Well name {well_name!r} does not include a column number.")
    return int(digits)


def _edge_pickup_row(single_start: str, tiprack_rows: list) -> str:
    """Tip row that leaves idle nozzles off the rack during SINGLE pickup."""
    start_row = _well_row(single_start)
    if start_row == tiprack_rows[0]:
        return tiprack_rows[-1]
    if start_row == tiprack_rows[-1]:
        return tiprack_rows[0]
    raise ValueError(
        f"single_start must use an edge nozzle row ({tiprack_rows[0]} or "
        f"{tiprack_rows[-1]}), got {single_start!r}."
    )


def resolve_setup_tip(dilution_cfg: dict, printing_cfg: dict,
                      pipette_cfg: dict, tiprack_rows: list) -> str:
    """Return the one SINGLE-nozzle setup tip."""
    if dilution_cfg.get("setup_tip"):
        return str(dilution_cfg["setup_tip"]).upper()
    cols = dilution_cfg.get("single_tip_columns", [])
    if not cols:
        raise RuntimeError("dilution.setup_tip or dilution.single_tip_columns is required.")
    reserved = int(printing_cfg["print_block_column"])
    for col in cols:
        if int(col) != reserved:
            return f"{_edge_pickup_row(pipette_cfg['single_start'], tiprack_rows)}{int(col)}"
    raise RuntimeError(
        f"No setup tip column available: dilution.single_tip_columns {cols} only "
        f"contains the reserved print column {reserved}."
    )


def resolve_print_tip(printing_cfg: dict, plate_rows: list) -> str:
    """Return the top well of the 8-tip print column."""
    return f"{plate_rows[0]}{int(printing_cfg['print_block_column'])}"


def resolve_paper_start_column(printing_cfg: dict, runtime_start) -> int:
    """Leftmost paper column to print on.

    Precedence: runtime parameter (operator flag in the App) > config
    paper_start_column > 1 (far-left default). Always starts at the left edge unless
    explicitly told otherwise, decoupled from where the liquid is drawn.
    """
    if runtime_start is not None:
        return int(runtime_start)
    return int(printing_cfg.get("paper_start_column", 1))


def resolve_print_wells(printing_cfg: dict, n: int, plate_rows: list,
                        paper_start_column: int) -> tuple[list, list]:
    """Return source plate wells and paper reference wells for 8-channel printing.

    Source wells come from source_column (where the liquid is). Paper wells use
    paper_start_column (where droplets land) — independent of the source column, so
    the print always starts at the chosen paper column (default 1 = far left).
    """
    src_col   = str(printing_cfg["source_column"])
    paper_col = int(paper_start_column)
    rows = plate_rows[:n]
    return ([f"{row}{src_col}" for row in rows], [f"{row}{paper_col}" for row in rows])


def dilution_volumes(total: float, fold: float) -> tuple:
    """(stock_uL, water_uL) for a fold dilution at `total` uL."""
    stock = round(total / fold, 2)
    water = round(total - stock, 2)
    return stock, water


# ── Pre-flight ───────────────────────────────────────────────────────────────────

def _preflight(protocol, lw, pipette, factors, dil_wells, setup_tip,
               plate_rows: list, tiprack_rows: list, paper_start_column: int):
    """Validate config + loaded-labware geometry BEFORE any motion. Raise to abort.

    Parameters
    ----------
    plate_rows         : row labels derived from lw["plate"] (e.g. ['A'..'H'])
    tiprack_rows       : row labels derived from lw["tiprack"] (e.g. ['A'..'H'])
    paper_start_column : resolved leftmost paper column the print starts on
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
    source_height = float(CONFIG["sources"].get("vial_aspirate_height_mm", 1.0))
    if not (0 < source_height < a1.depth):
        errors.append(
            f"sources.vial_aspirate_height_mm {source_height} must be > 0 and "
            f"< vial depth {a1.depth} mm."
        )

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
    droplet_plus_air = pr["droplet_volume_ul"] + float(pr.get("air_gap_ul", 0.0))
    if droplet_plus_air > pipette.max_volume:
        errors.append(
            f"droplet {pr['droplet_volume_ul']} uL + air gap {pr.get('air_gap_ul', 0.0)} uL "
            f"= {droplet_plus_air} uL > pipette max {pipette.max_volume} uL.")
    if dil["mix_volume_ul"] > pipette.max_volume:
        errors.append(
            f"mix volume {dil['mix_volume_ul']} uL > pipette max {pipette.max_volume} uL.")

    # ── Tip column overlap ────────────────────────────────────────────────────────
    print_col = int(pr["print_block_column"])
    tip_names = set(lw["tiprack"].wells_by_name().keys())
    if setup_tip not in tip_names:
        errors.append(f"dilution.setup_tip {setup_tip!r} is not in the loaded tiprack.")
    if _well_column(setup_tip) == print_col:
        errors.append(
            f"dilution.setup_tip {setup_tip} overlaps full 8-tip print column "
            f"{print_col}; setup and print tips must be separate."
        )
    expected_row = _edge_pickup_row(CONFIG["pipette"]["single_start"], tiprack_rows)
    if _well_row(setup_tip) != expected_row:
        errors.append(
            f"dilution.setup_tip must be in row {expected_row} when "
            f"single_start={CONFIG['pipette']['single_start']} so only one tip engages; "
            f"got {setup_tip}."
        )

    # ── Paper print start column + replicate sweep must fit on the sheet ───────────
    n_paper_cols = len(lw["paper"].columns())
    if not (1 <= paper_start_column <= n_paper_cols):
        errors.append(
            f"print start column {paper_start_column} is out of range (1..{n_paper_cols})."
        )
    else:
        last_col = paper_start_column + int(pr["num_replicates"]) - 1
        if last_col > n_paper_cols:
            errors.append(
                f"print sweep runs off the paper: start column {paper_start_column} + "
                f"{pr['num_replicates']} replicate(s) reaches column {last_col} > "
                f"{n_paper_cols}. Lower printing.num_replicates or the start column."
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


def _legacy_run_single_tip_print(protocol: protocol_api.ProtocolContext):
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
    setup_tip   = resolve_setup_tip(dil, pr, CONFIG["pipette"], _tiprack_rows)
    print_tip   = resolve_print_tip(pr, _plate_rows)
    _paper_start_column = resolve_paper_start_column(pr, None)
    print_src_wells, print_paper_wells = resolve_print_wells(
        pr, len(dil_wells), _plate_rows, _paper_start_column)
    total = dil["total_volume_ul"]

    # Build mid-well capture set: row letter + destination column.
    # This decouples camera_mid_rows from column number — both can change independently.
    dest_col = str(dil["destination_column"])
    mid_wells_set = {f"{r}{dest_col}" for r in cam.get("capture_mid_rows", [])}

    # ── 4. PRE-FLIGHT ─────────────────────────────────────────────────────────────
    _preflight(protocol, lw, pipette, factors, dil_wells, setup_tip,
               _plate_rows, _tiprack_rows, _paper_start_column)

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

    # ── 7. Phase B: single-tip print ─────────────────────────────────────────────
    if params.do_print and pr["enabled"]:
        _return_or_drop()
        pipette.configure_nozzle_layout(
            style=SINGLE, start=CONFIG["pipette"]["single_start"], tip_racks=[lw["tiprack"]])
        protocol.comment(
            f"Nozzle layout: SINGLE ({CONFIG['pipette']['single_start']}) for paper print.")

        sp = pr["replicate_spacing_mm"]
        for well_idx, (src_name, paper_name, tip) in enumerate(
                zip(print_src_wells, print_paper_wells, print_tips), start=1):
            pipette.pick_up_tip(lw["tiprack"][tip])
            protocol.comment(
                f"Single-tip print source {src_name} -> paper {paper_name}; "
                f"picked tip {tip} ({well_idx}/{len(print_src_wells)})."
            )
            paper_well = lw["paper"][paper_name]
            for rep in range(int(pr["num_replicates"])):
                protocol.comment(
                    f"Aspirating {pr['droplet_volume_ul']} uL from {src_name}; "
                    f"single-tip paper replicate {rep + 1}."
                )
                pipette.aspirate(pr["droplet_volume_ul"], lw["plate"][src_name])
                dest = paper_well.bottom(pr["dispense_z_mm"]).move(
                    Point(
                        x=rep * sp["x"],
                        y=rep * sp.get("y", 0.0),
                        z=rep * sp.get("z", 0.0),
                    ))
                protocol.comment(
                    f"Printing 1 droplet onto paper (slot {deck['paper']['slot']}) "
                    f"from {src_name}, replicate {rep + 1}, z={pr['dispense_z_mm']} mm."
                )
                pipette.dispense(pr["droplet_volume_ul"], dest)
                if pr.get("blow_out"):
                    pipette.blow_out(dest)
                if pr.get("touch_tip"):
                    pipette.touch_tip()
            _return_or_drop()

        _capture_image(protocol, "paper_print_single_tip.jpg")
        protocol.comment(
            f"{'Returned' if return_tips else 'Dropped'} {len(print_tips)} single print tips "
            f"({'none disposed' if return_tips else 'disposed to trash'})."
        )

    # ── 8. CV: after ──────────────────────────────────────────────────────────────
    if cam["capture_after"]:
        _capture_image(protocol, "after_deck.jpg")
        _capture_image(protocol, "after_plate.jpg")

    protocol.comment("=== Vial Dilution -> Paper Print Demo Completed ===")


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
        CONFIG["pipette"]["name"], CONFIG["pipette"]["mount"], tip_racks=[lw["tiprack"]]
    )

    plate_rows = list(lw["plate"].rows_by_name().keys())
    tiprack_rows = list(lw["tiprack"].rows_by_name().keys())
    factors = resolve_factors(dil)
    dil_wells = resolve_dilution_wells(dil, len(factors), plate_rows)
    setup_tip = resolve_setup_tip(dil, pr, CONFIG["pipette"], tiprack_rows)
    print_tip = resolve_print_tip(pr, plate_rows)
    paper_start_column = resolve_paper_start_column(
        pr, getattr(params, "print_start_column", None))
    print_src_wells, print_paper_wells = resolve_print_wells(
        pr, len(dil_wells), plate_rows, paper_start_column)
    total = dil["total_volume_ul"]
    dest_col = str(dil["destination_column"])
    mid_wells_set = {f"{row}{dest_col}" for row in cam.get("capture_mid_rows", [])}

    _preflight(protocol, lw, pipette, factors, dil_wells, setup_tip,
               plate_rows, tiprack_rows, paper_start_column)

    protocol.comment("=== Vial Dilution -> Paper Print Demo Started ===")
    protocol.comment(
        f"Flags: dry_run={params.dry_run}, do_dilution={params.do_dilution}, "
        f"do_print={params.do_print}"
    )
    protocol.comment(
        f"Sources: water=vial {CONFIG['sources']['water_vial']}, "
        f"food colouring=vial {CONFIG['sources']['food_coloring_vial']} "
        f"(slot {deck['tuberack']['slot']}). Series: "
        + ", ".join(f"{w}={f:g}x" for w, f in zip(dil_wells, factors))
    )
    protocol.comment(
        f"Tip plan: one setup tip {setup_tip} for all 20 mL vial work; "
        f"8-channel print tips from column {pr['print_block_column']}."
    )

    if params.dry_run:
        protocol.comment("DRY RUN: labware + pipette loaded, pre-flight passed. No liquid motion.")
        protocol.comment("=== Vial Dilution -> Paper Print Demo Completed (dry run) ===")
        return

    _apply_flow_rates(pipette)

    if cam["capture_before"]:
        _capture_image(protocol, "before_deck.jpg")
        _capture_image(protocol, "before_plate.jpg")

    water_vial = lw["tuberack"][CONFIG["sources"]["water_vial"]]
    fc_vial = lw["tuberack"][CONFIG["sources"]["food_coloring_vial"]]
    vial_aspirate_height = float(CONFIG["sources"].get("vial_aspirate_height_mm", 3.0))
    water_vial_aspirate = water_vial.bottom(vial_aspirate_height)
    fc_vial_aspirate = fc_vial.bottom(vial_aspirate_height)

    if params.do_dilution and dil["enabled"]:
        pipette.configure_nozzle_layout(
            style=SINGLE, start=CONFIG["pipette"]["single_start"], tip_racks=[lw["tiprack"]]
        )
        protocol.comment(
            f"Nozzle layout: SINGLE ({CONFIG['pipette']['single_start']}) for one-tip setup."
        )
        pipette.pick_up_tip(lw["tiprack"][setup_tip])
        protocol.comment(
            f"One-tip setup: picked tip {setup_tip}. This is the only tip used "
            "while working with the 20 mL vial rack."
        )
        protocol.comment(
            f"Vial aspiration height: {vial_aspirate_height} mm above modeled vial bottom."
        )

        for well_name, fold in zip(dil_wells, factors):
            _stock, water_vol = dilution_volumes(total, fold)
            if water_vol <= 0:
                continue
            protocol.comment(f"Dispensing {water_vol} uL water -> {well_name}.")
            pipette.aspirate(water_vol, water_vial_aspirate)
            pipette.dispense(water_vol, lw["plate"][well_name])

        for well_name, fold in zip(dil_wells, factors):
            stock, _water_vol = dilution_volumes(total, fold)
            if stock <= 0:
                continue
            protocol.comment(
                f"Diluting well {well_name} to {fold:g}x (stock {stock} uL); "
                f"same setup tip {setup_tip}."
            )
            pipette.aspirate(stock, fc_vial_aspirate)
            pipette.dispense(stock, lw["plate"][well_name])

        _return_or_drop()
        protocol.comment(
            f"One-tip setup transfers done; {'returned' if return_tips else 'dropped'} tip {setup_tip}."
        )

        # Switch nozzle layout to ALL for 8-channel dilution mixing
        pipette.configure_nozzle_layout(style=ALL, tip_racks=[lw["tiprack"]])
        protocol.comment("Nozzle layout: ALL for 8-channel dilution mixing.")

        pipette.pick_up_tip(lw["tiprack"][print_tip])
        protocol.comment(
            f"8-channel print: picked tips from column {pr['print_block_column']} "
            f"starting at {print_tip}."
        )

        src_anchor = print_src_wells[0]
        mix_vol = min(dil["mix_volume_ul"], total, float(pipette.max_volume))
        protocol.comment(f"Mixing plate column {dil['destination_column']} (8 channels) ({dil['mix_reps']} x {mix_vol} uL).")
        if dil["mix_reps"] > 0 and mix_vol > 0:
            pipette.mix(dil["mix_reps"], mix_vol, lw["plate"][src_anchor])

        protocol.comment(f"Dilution series complete in plate column {dil['destination_column']}.")
        _capture_image(protocol, "plate_after_dilution.jpg")

        if not (params.do_print and pr["enabled"]):
            _return_or_drop()
            protocol.comment(
                f"Returned 8-channel print tips from column {pr['print_block_column']} (none disposed)."
            )

    if params.do_print and pr["enabled"]:
        if not pipette.has_tip:
            pipette.configure_nozzle_layout(style=ALL, tip_racks=[lw["tiprack"]])
            protocol.comment("Nozzle layout: ALL for 8-channel paper print.")

            pipette.pick_up_tip(lw["tiprack"][print_tip])
            protocol.comment(
                f"8-channel print: picked tips from column {pr['print_block_column']} "
                f"starting at {print_tip}."
            )

        src_anchor = print_src_wells[0]
        paper_anchor = print_paper_wells[0]
        paper_well = lw["paper"][paper_anchor]
        spacing = pr["replicate_spacing_mm"]

        air_gap = float(pr.get("air_gap_ul", 0.0))
        protocol.comment(
            f"Paper print: starting at paper column {paper_start_column} (1 = far left); "
            f"{pr['num_replicates']} replicate(s) sweeping right by {spacing['x']} mm each"
            + (f"; {air_gap:g} uL anti-drip air gap." if air_gap > 0 else ".")
        )

        for rep in range(int(pr["num_replicates"])):
            protocol.comment(
                f"Aspirating {pr['droplet_volume_ul']} uL from plate column "
                f"{pr['source_column']}; 8-channel paper replicate {rep + 1}."
            )
            pipette.aspirate(pr["droplet_volume_ul"], lw["plate"][src_anchor])
            # Anti-drip air gap: pull air in AFTER the liquid so it sits at the tip
            # opening and holds the droplet in during the move to the paper.
            if air_gap > 0:
                pipette.air_gap(air_gap)
                protocol.comment(f"Pulled {air_gap:g} uL air gap below the droplet (anti-drip).")
            dest = paper_well.bottom(pr["dispense_z_mm"]).move(
                Point(
                    x=rep * spacing["x"],
                    y=rep * spacing.get("y", 0.0),
                    z=rep * spacing.get("z", 0.0),
                )
            )
            protocol.comment(
                f"Printing 8 droplets onto paper (slot {deck['paper']['slot']}) "
                f"from plate column {pr['source_column']} -> paper column "
                f"~{paper_start_column + rep}, replicate {rep + 1}, "
                f"z={pr['dispense_z_mm']} mm."
            )
            # Dispense the droplet AND the air gap (air exits first, then the liquid).
            pipette.dispense(pr["droplet_volume_ul"] + air_gap, dest)
            if pr.get("blow_out"):
                pipette.blow_out(dest)
            if pr.get("touch_tip"):
                pipette.touch_tip()
        _return_or_drop()

        _capture_image(protocol, "paper_print_8_channel.jpg")
        protocol.comment(
            f"{'Returned' if return_tips else 'Dropped'} 8-channel print tips from "
            f"column {pr['print_block_column']} "
            f"({'none disposed' if return_tips else 'disposed to trash'})."
        )

    if cam["capture_after"]:
        _capture_image(protocol, "after_deck.jpg")
        _capture_image(protocol, "after_plate.jpg")

    protocol.comment("=== Vial Dilution -> Paper Print Demo Completed ===")
