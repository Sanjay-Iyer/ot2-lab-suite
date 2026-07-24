#!/usr/bin/env python3
"""
Workflow 01 — Vial Dilution -> Paper Print (OT-2 protocol, config-driven)
================================================================================
FILE ROLE
  * ENTRY POINT: this is the robot protocol the OT-2 executes (via the Opentrons App /
    engine). It runs entirely from the embedded ``CONFIG`` dict.
  * Build it from a YAML with ``scripts/build_vial_dilution_print.py`` (which embeds the
    CONFIG and simulates); the generated copies live in ``src/protocols/generated/``.
  * EDIT the Python freely for motion/logic. Do NOT hand-edit the ``CONFIG`` block
    (it is regenerated) — edit a YAML in ``configs/printing/`` instead.

INPUTS  : embedded ``CONFIG`` (deck, pipettes, sources, dilution, color_series,
          print_groups, camera, tips, flow_rates, safety) + App runtime params
          (dry_run / do_dilution / do_print / print_start_column).
OUTPUTS : liquid handling on the OT-2; two camera JPEGs (one before, one after) written
          to ``camera.robot_image_dir`` on the robot; protocol comments (the simulation log).
HARDWARE: right = ``p300_multi_gen2`` (8-up printing + dilution); left =
          ``p20_single_gen2`` (single-spot printing). Needs a 20 µL rack for the P20.
          apiLevel 2.28 (partial-mode ``return_tip()``).
SIDE EFFECTS: on the real robot, curl to the camera API and mkdir of the image dir;
          both are no-ops while ``protocol.is_simulating()``.
SAFETY  : dispense heights / paper standoff / vial depths are NOT calibrated here —
          verify on the physical robot (see docs/printing/WORK_LAPTOP_PHYSICAL_VALIDATION.md).

WHAT IT DOES
  Builds dye dilution series in 96-well plate columns (water + dye from 20 mL vials via
  SINGLE-nozzle setup tips), mixes them, then prints per ``print_groups`` — the P300
  8-up (``column_8up``) and/or the P20 one spot at a time (``single_spot``). Tips are
  RETURNED to their racks, not trashed. One image is captured before and one after.

>>> EVERYTHING is driven by the CONFIG dict below. <<<
Keep canonical settings in a YAML under configs/printing/ (or the legacy
  configs/workflows/defaults/vial_dilution_print.yaml) and run
  scripts/build_vial_dilution_print.py to generate a robot-ready copy with that YAML
  embedded between the CONFIG markers. See docs/printing/01_vial_dilution_paper_print.md.

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
        "Config-driven blue/orange dilution series from 20 mL vials into 96-well "
        "plate columns, then ordered 8-channel paper prints."
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
  'sources': { 'water_vial': 'A1',
               'blue_dye_vial': 'A2',
               'orange_dye_vial': 'A3',
               'food_coloring_vial': 'A2',
               'vial_aspirate_height_mm': 4.0},
  'dilution': { 'enabled': True,
                'destination_column': '9',
                'total_volume_ul': 200.0,
                'factors': { 'mode': 'explicit',
                             'explicit': [1.0, 2.0, 5.0, 10.0],
                             'step_factor': 2,
                             'start': 1,
                             'end': 50,
                             'count': 8},
                'mix_reps': 3,
                'mix_volume_ul': 120.0,
                'water_setup_tip': 'H12',
                'setup_tip': 'H12',
                'single_tip_columns': [12]},
  'color_series': [ { 'name': 'orange',
                      'dye_vial': 'A3',
                      'destination_column': '11',
                      'setup_tip': 'H11',
                      'print_block_column': 1,
                      'paper_start_column': 1,
                      'num_replicates': 2},
                    { 'name': 'blue',
                      'dye_vial': 'A2',
                      'destination_column': '9',
                      'setup_tip': 'H10',
                      'print_block_column': 2,
                      'paper_start_column': 4,
                      'num_replicates': 2}],
  'printing': { 'enabled': True,
                'source_column': '9',
                'droplet_volume_ul': 30.0,
                'num_replicates': 2,
                'paper_start_column': 1,
                'dispense_z_mm': 1.0,
                'air_gap_ul': 5.0,
                'air_gap_height_mm': 20.0,
                'post_dispense_delay_s': 0.5,
                'move_speed_mm_per_s': 50.0,
                'replicate_spacing_mm': {'x': 9.0, 'y': 0.0, 'z': 0.0},
                'print_block_column': 1,
                'blow_out': True,
                'touch_tip': False},
  'tips': {'return_tips': True},
  'camera': { 'enabled': True,
              'capture_before': True,
              'capture_after': True,
              'capture_mid_rows': ['C', 'E', 'H'],
              'robot_image_dir': '/data/vision/vial_dilution_print',
              'robot_api_url': 'http://localhost:31950/camera/picture',
              'capture_timeout_s': 5},
  'flow_rates': {'aspirate': 20.0, 'dispense': 80.0, 'mix': None},
  'cv': { 'expected_droplets': 4,
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
              'pipette_max_volume_ul': 300.0},
  'print_groups': [ { 'name': 'orange',
                      'volume_ul': 30.0,
                      'pipette': 'p300_multi_gen2',
                      'layout': 'column_8up',
                      'source': { 'plate_column': '11',
                                  'wells': None,
                                  'vial': None,
                                  'aspirate_height_mm': None},
                      'replicates': 2,
                      'droplets_per_spot': 1,
                      'mix_before': True,
                      'destination': { 'paper_start_column': 1,
                                       'spacing_mm': {'x': 9.0, 'y': 0.0, 'z': 0.0}},
                      'dispense': { 'z_mm': 1.0,
                                    'air_gap_ul': 5.0,
                                    'air_gap_height_mm': 20.0,
                                    'blow_out': True,
                                    'touch_tip': False,
                                    'post_dispense_delay_s': 0.5,
                                    'move_speed_mm_per_s': 50.0},
                      'tips': { 'well': None,
                                'block_column': 1,
                                'strategy': None,
                                'map_ref': None,
                                'reuse': True,
                                'return': True}},
                    { 'name': 'blue',
                      'volume_ul': 30.0,
                      'pipette': 'p300_multi_gen2',
                      'layout': 'column_8up',
                      'source': { 'plate_column': '9',
                                  'wells': None,
                                  'vial': None,
                                  'aspirate_height_mm': None},
                      'replicates': 2,
                      'droplets_per_spot': 1,
                      'mix_before': True,
                      'destination': { 'paper_start_column': 4,
                                       'spacing_mm': {'x': 9.0, 'y': 0.0, 'z': 0.0}},
                      'dispense': { 'z_mm': 1.0,
                                    'air_gap_ul': 5.0,
                                    'air_gap_height_mm': 20.0,
                                    'blow_out': True,
                                    'touch_tip': False,
                                    'post_dispense_delay_s': 0.5,
                                    'move_speed_mm_per_s': 50.0},
                      'tips': { 'well': None,
                                'block_column': 2,
                                'strategy': None,
                                'map_ref': None,
                                'reuse': True,
                                'return': True}}],
  'protocol_version': 1}
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
    return resolve_print_tip_for_column(printing_cfg["print_block_column"], plate_rows)


def resolve_print_tip_for_column(print_block_column, plate_rows: list) -> str:
    """Return the top well of a specific 8-tip print column."""
    return f"{plate_rows[0]}{int(print_block_column)}"


def _derive_print_groups(color_series: list, printing_cfg: dict) -> list:
    """Backward-compat only: build column_8up P300 groups from legacy color_series.

    Used when an OLD embedded CONFIG has no `print_groups`. New/built configs always
    carry resolved `print_groups`; this involves no pipette selection (legacy prints
    are always the P300), so it does not duplicate the selection service.
    """
    spacing = printing_cfg.get("replicate_spacing_mm", {"x": 9.0, "y": 0.0, "z": 0.0})
    dispense = {
        "z_mm": printing_cfg.get("dispense_z_mm", 1.0),
        "air_gap_ul": printing_cfg.get("air_gap_ul", 0.0),
        "air_gap_height_mm": printing_cfg.get("air_gap_height_mm", 20.0),
        "blow_out": printing_cfg.get("blow_out", True),
        "touch_tip": printing_cfg.get("touch_tip", False),
        "post_dispense_delay_s": printing_cfg.get("post_dispense_delay_s", 0.5),
        "move_speed_mm_per_s": printing_cfg.get("move_speed_mm_per_s"),
    }
    groups = []
    for s in color_series:
        groups.append({
            "name": s["name"],
            "volume_ul": printing_cfg.get("droplet_volume_ul", 20.0),
            "pipette": "p300_multi_gen2", "layout": "column_8up",
            "source": {"plate_column": str(s.get("destination_column",
                                                 printing_cfg.get("source_column", "9")))},
            "replicates": s.get("num_replicates", printing_cfg.get("num_replicates", 1)),
            "destination": {"paper_start_column": s.get("paper_start_column", 1),
                            "spacing_mm": spacing},
            "dispense": dispense,
            "tips": {"block_column": s.get("print_block_column", 1),
                     "reuse": True, "return": True},
        })
    return groups


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


def _resolve_source_vial(sources_cfg: dict, source_name: str) -> str:
    """Resolve either a sources key (blue_dye_vial) or a literal vial well (A2)."""
    if source_name in sources_cfg:
        source_name = sources_cfg[source_name]
    return str(source_name).upper()


def resolve_water_setup_tip(dilution_cfg: dict) -> str:
    """Return the single-nozzle tip used for water-only setup transfers."""
    tip = dilution_cfg.get("water_setup_tip") or dilution_cfg.get("setup_tip")
    if not tip:
        raise RuntimeError("dilution.water_setup_tip or dilution.setup_tip is required.")
    return str(tip).upper()


def resolve_color_series(config: dict, runtime_start=None) -> list:
    """Return the ordered color series plan, preserving legacy single-color configs."""
    sources_cfg = config["sources"]
    dilution_cfg = config["dilution"]
    printing_cfg = config["printing"]
    configured = config.get("color_series") or []

    if not configured:
        configured = [{
            "name": "dye",
            "dye_vial": sources_cfg.get("food_coloring_vial", sources_cfg.get("blue_dye_vial")),
            "destination_column": dilution_cfg["destination_column"],
            "setup_tip": dilution_cfg.get("setup_tip"),
            "print_block_column": printing_cfg["print_block_column"],
            "paper_start_column": printing_cfg.get("paper_start_column", 1),
            "num_replicates": printing_cfg.get("num_replicates", 1),
        }]

    series = []
    for index, item in enumerate(configured, start=1):
        if not item.get("enabled", True):
            continue
        name = str(item.get("name", f"series_{index}")).lower()
        dye_vial = item.get("dye_vial") or item.get("source_vial") or item.get("source")
        if not dye_vial:
            raise RuntimeError(f"color_series[{index}] is missing dye_vial/source_vial.")
        dest_col = item.get("destination_column", item.get("plate_column", item.get("source_column")))
        if dest_col is None:
            raise RuntimeError(f"color_series[{index}] is missing destination_column.")
        setup_tip = item.get("setup_tip")
        if not setup_tip:
            raise RuntimeError(f"color_series[{index}] is missing setup_tip.")
        series.append({
            "name": name,
            "dye_vial": _resolve_source_vial(sources_cfg, dye_vial),
            "destination_column": str(dest_col),
            "setup_tip": str(setup_tip).upper(),
            "print_block_column": int(item.get(
                "print_block_column", printing_cfg.get("print_block_column", index))),
            "paper_start_column": int(item.get(
                "paper_start_column", printing_cfg.get("paper_start_column", 1))),
            "num_replicates": int(item.get(
                "num_replicates", printing_cfg.get("num_replicates", 1))),
        })

    if not series:
        raise RuntimeError("At least one enabled color series is required.")

    if runtime_start is not None:
        offset = int(runtime_start) - int(series[0]["paper_start_column"])
        for item in series:
            item["paper_start_column"] += offset

    return series


def resolve_series_dilution_wells(series_cfg: dict, n: int, plate_rows: list) -> list:
    return resolve_dilution_wells(
        {"destination_column": series_cfg["destination_column"]}, n, plate_rows)


def format_paper_columns(start_column: int, num_replicates: int) -> str:
    end_column = int(start_column) + int(num_replicates) - 1
    if end_column == int(start_column):
        return str(start_column)
    return f"{start_column}-{end_column}"


def dilution_volumes(total: float, fold: float) -> tuple:
    """(stock_uL, water_uL) for a fold dilution at `total` uL."""
    stock = round(total / fold, 2)
    water = round(total - stock, 2)
    return stock, water


# ── Pre-flight ───────────────────────────────────────────────────────────────────

def _preflight(protocol, lw, pipette, factors, dil_wells, setup_tip,
               plate_rows: list, tiprack_rows: list, paper_start_column: int,
               color_series=None, water_setup_tip=None):
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
    color_series = color_series or resolve_color_series(CONFIG, paper_start_column)
    water_setup_tip = (water_setup_tip or setup_tip).upper()
    series_wells = [
        (series, resolve_series_dilution_wells(series, len(factors), plate_rows))
        for series in color_series
    ]

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
    tuberack_well_names = set(tuberack.wells_by_name().keys())
    source_vials = [("water", CONFIG["sources"]["water_vial"])]
    source_vials.extend((series["name"], series["dye_vial"]) for series in color_series)
    for label, vial_name in source_vials:
        if vial_name not in tuberack_well_names:
            errors.append(f"{label} source vial {vial_name!r} is not in the loaded tube rack.")

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
    for series, wells in series_wells:
        if len(factors) != len(wells):
            errors.append(
                f"{series['name']} has {len(factors)} factors but {len(wells)} "
                "destination wells."
            )
        if len(wells) > len(plate_rows):
            errors.append(
                f"{series['name']} dilution needs {len(wells)} wells; plate column only "
                f"has {len(plate_rows)} rows."
            )
    destination_cols = [series["destination_column"] for series in color_series]
    if len(destination_cols) != len(set(destination_cols)):
        errors.append(f"color_series destination columns must be unique, got {destination_cols}.")

    # ── Volume sanity ─────────────────────────────────────────────────────────────
    total = dil["total_volume_ul"]
    # Max volume: use the most conservative well in the plate (not hardcoded)
    well_max = min(w.max_volume for w in lw["plate"].wells())
    if total > well_max:
        errors.append(f"total_volume_ul {total} > plate well max {well_max} uL.")
    for series, wells in series_wells:
        for well, fold in zip(wells, factors):
            stock, water = dilution_volumes(total, fold)
            if stock < 0 or water < 0:
                errors.append(
                    f"{series['name']} {well} ({fold}x): negative volume "
                    f"(stock={stock}, water={water})."
                )
            if stock > pipette.max_volume:
                errors.append(
                    f"{series['name']} {well} ({fold}x): stock {stock} uL > "
                    f"pipette max {pipette.max_volume} uL."
                )
    # Legacy flat-print droplet sanity. New-schema configs express per-group volumes
    # in print_groups (validated at build time by src/core/print_groups.py), so this
    # block only runs when a legacy `printing.droplet_volume_ul` is present.
    if pr.get("droplet_volume_ul") is not None:
        droplet_volume = float(pr["droplet_volume_ul"])
        air_gap = float(pr.get("air_gap_ul", 0.0))
        dwell_s = float(pr.get("post_dispense_delay_s", 0.0) or 0.0)
        move_speed = pr.get("move_speed_mm_per_s")
        if droplet_volume <= 0:
            errors.append(f"droplet_volume_ul must be > 0, got {droplet_volume}.")
        if air_gap < 0:
            errors.append(f"air_gap_ul must be >= 0, got {air_gap}.")
        if dwell_s < 0:
            errors.append(f"post_dispense_delay_s must be >= 0, got {dwell_s}.")
        if move_speed is not None and float(move_speed) <= 0:
            errors.append(f"move_speed_mm_per_s must be > 0 or null, got {move_speed}.")
        droplet_plus_air = droplet_volume + air_gap
        if droplet_plus_air > pipette.max_volume:
            errors.append(
                f"droplet {droplet_volume} uL + air gap {air_gap} uL "
                f"= {droplet_plus_air} uL > pipette max {pipette.max_volume} uL.")
    if dil["mix_volume_ul"] > pipette.max_volume:
        errors.append(
            f"mix volume {dil['mix_volume_ul']} uL > pipette max {pipette.max_volume} uL.")

    # ── Tip column overlap ────────────────────────────────────────────────────────
    tip_names = set(lw["tiprack"].wells_by_name().keys())
    print_cols = [int(series["print_block_column"]) for series in color_series]
    if len(print_cols) != len(set(print_cols)):
        errors.append(f"color_series print_block_column values must be unique, got {print_cols}.")
    for print_col in print_cols:
        print_tip = resolve_print_tip_for_column(print_col, tiprack_rows)
        if print_tip not in tip_names:
            errors.append(f"print block starting tip {print_tip!r} is not in the loaded tiprack.")
    setup_tips = [water_setup_tip] + [series["setup_tip"] for series in color_series]
    if len(setup_tips) != len(set(setup_tips)):
        errors.append(f"Setup tips must be unique to avoid color carryover, got {setup_tips}.")
    expected_row = _edge_pickup_row(CONFIG["pipette"]["single_start"], tiprack_rows)
    for tip in setup_tips:
        if tip not in tip_names:
            errors.append(f"setup tip {tip!r} is not in the loaded tiprack.")
            continue
        if _well_column(tip) in print_cols:
            errors.append(
                f"setup tip {tip} overlaps an 8-tip print column {print_cols}; "
                "setup and print tips must be separate."
            )
        if _well_row(tip) != expected_row:
            errors.append(
                f"setup tip {tip} must be in row {expected_row} when "
                f"single_start={CONFIG['pipette']['single_start']} so only one tip engages."
            )

    # ── Paper print start column + replicate sweep must fit on the sheet ───────────
    n_paper_cols = len(lw["paper"].columns())
    for series in color_series:
        start_col = int(series["paper_start_column"])
        reps = int(series["num_replicates"])
        if not (1 <= start_col <= n_paper_cols):
            errors.append(
                f"{series['name']} print start column {start_col} is out of range "
                f"(1..{n_paper_cols})."
            )
            continue
        last_col = start_col + reps - 1
        if last_col > n_paper_cols:
            errors.append(
                f"{series['name']} print sweep runs off the paper: start column {start_col} + "
                f"{reps} replicate(s) reaches column {last_col} > "
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
    for series, wells in series_wells:
        for well, fold in zip(wells, factors):
            stock, _ = dilution_volumes(total, fold)
            if 0 < stock < min_ok:
                protocol.comment(
                    f"WARNING: {series['name']} {well} ({fold}x) stock {stock} uL "
                    f"is below the p300 ~{min_ok:.0f} uL accurate minimum "
                    "(visual demo only)."
                )
    if pr.get("droplet_volume_ul") is not None:
        droplet_volume = float(pr["droplet_volume_ul"])
        if 0 < droplet_volume < min_ok:
            protocol.comment(
                f"WARNING: print droplet {droplet_volume:g} uL is below the p300 "
                f"~{min_ok:.0f} uL accurate minimum; start around 30 uL for food-coloring prints."
            )


def _droplets_per_spot(group: dict) -> int:
    """How many droplets a print group stacks on each paper spot (default 1).

    Each droplet is a full aspirate -> dispense cycle at the SAME destination, so a
    spot ends up with ``volume_ul * droplets_per_spot``. Validated at build time by
    src/core/print_groups.py (integer >= 1).
    """
    try:
        n = int(group.get("droplets_per_spot", 1) or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, n)


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
    pr = CONFIG.get("printing", {})
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
    # Optional dedicated 20 uL rack for the single-channel P20 (mixed/P20 runs need
    # it; the P20 cannot use 300 uL tips). Absent for P300-only runs.
    if deck.get("tiprack_p20"):
        lw["tiprack_p20"] = _load(deck["tiprack_p20"])

    def _rack_for(name: str):
        """A P20 draws from the 20 uL rack; every other pipette from the 300 uL rack."""
        if "p20" in name and "tiprack_p20" in lw:
            return lw["tiprack_p20"]
        return lw["tiprack"]

    # Load every mounted pipette. Separate InstrumentContext objects keep the P20 and
    # P300 tip state fully independent. `pipette` is the primary P300 (dilution +
    # column_8up printing); the P20, when mounted, runs single_spot groups.
    pipette_cfgs = CONFIG.get("pipettes") or [CONFIG["pipette"]]
    instruments = {
        pc["name"]: protocol.load_instrument(
            pc["name"], pc["mount"], tip_racks=[_rack_for(pc["name"])])
        for pc in pipette_cfgs
    }
    primary_name = CONFIG["pipette"]["name"]
    pipette = instruments.get(primary_name) or next(iter(instruments.values()))

    plate_rows = list(lw["plate"].rows_by_name().keys())
    tiprack_rows = list(lw["tiprack"].rows_by_name().keys())
    factors = resolve_factors(dil)
    dil_wells = resolve_dilution_wells(dil, len(factors), plate_rows)
    paper_start_column = resolve_paper_start_column(
        pr, getattr(params, "print_start_column", None))
    color_series = resolve_color_series(CONFIG, paper_start_column)
    water_setup_tip = resolve_water_setup_tip(dil)
    series_wells = {
        series["name"]: resolve_series_dilution_wells(series, len(factors), plate_rows)
        for series in color_series
    }
    total = dil["total_volume_ul"]

    _preflight(
        protocol, lw, pipette, factors, dil_wells, water_setup_tip,
        plate_rows, tiprack_rows, paper_start_column,
        color_series=color_series, water_setup_tip=water_setup_tip,
    )

    protocol.comment("=== Vial Dilution -> Paper Print Demo Started ===")
    protocol.comment(
        f"Flags: dry_run={params.dry_run}, do_dilution={params.do_dilution}, "
        f"do_print={params.do_print}"
    )
    blue_vial = CONFIG["sources"].get("blue_dye_vial", CONFIG["sources"].get("food_coloring_vial"))
    orange_vial = CONFIG["sources"].get("orange_dye_vial")
    protocol.comment(
        f"Sources: water=vial {CONFIG['sources']['water_vial']}, "
        f"blue dye=vial {blue_vial}, orange dye=vial {orange_vial} "
        f"(slot {deck['tuberack']['slot']})."
    )
    protocol.comment(
        "Series: " + "; ".join(
            f"{series['name']} vial {series['dye_vial']} -> plate column "
            f"{series['destination_column']} -> paper columns "
            f"{format_paper_columns(series['paper_start_column'], series['num_replicates'])}"
            for series in color_series
        )
    )
    protocol.comment(
        f"Tip plan: water setup tip {water_setup_tip}; "
        + "; ".join(
            f"{series['name']} stock tip {series['setup_tip']}, 8-channel tips "
            f"from column {series['print_block_column']}"
            for series in color_series
        )
    )

    if params.dry_run:
        protocol.comment("DRY RUN: labware + pipette loaded, pre-flight passed. No liquid motion.")
        protocol.comment("=== Vial Dilution -> Paper Print Demo Completed (dry run) ===")
        return

    _apply_flow_rates(pipette)

    # One beginning-of-run image (CV kept minimal: one before + one after only).
    if cam.get("capture_before", True):
        _capture_image(protocol, "before.jpg")

    water_vial = lw["tuberack"][CONFIG["sources"]["water_vial"]]
    vial_aspirate_height = float(CONFIG["sources"].get("vial_aspirate_height_mm", 4.0))
    water_vial_aspirate = water_vial.bottom(vial_aspirate_height)
    dye_vial_aspirates = {
        series["name"]: lw["tuberack"][series["dye_vial"]].bottom(vial_aspirate_height)
        for series in color_series
    }

    # ── Print-group dispatch helpers (unified schema) ────────────────────────────
    # Printing is driven entirely by CONFIG["print_groups"]; each group carries its
    # resolved pipette + layout. column_8up runs on the P300 (existing motion),
    # single_spot on the single-channel P20 (one droplet at a time). Selection was
    # done at build time by src/core/pipette_selection.py — the protocol never
    # chooses a pipette, it only executes the resolved plan.
    print_groups = CONFIG.get("print_groups") or _derive_print_groups(color_series, pr)

    def _return_or_drop_inst(inst) -> None:
        if inst.has_tip:
            inst.return_tip() if return_tips else inst.drop_tip()

    def _group_source_wells(group: dict) -> list:
        """Plate wells this group aspirates from: explicit `source.wells`, else the
        first N rows of `source.plate_column` (N = number of dilution factors)."""
        src = group.get("source", {})
        if src.get("wells"):
            return [str(w).upper() for w in src["wells"]]
        col = str(src.get("plate_column"))
        n = min(len(factors) if factors else len(plate_rows), len(plate_rows))
        return [f"{plate_rows[i]}{col}" for i in range(n)]

    def _mix_source_column(group: dict) -> None:
        """8-channel P300 mix of a group's source column, ANCHORED AT ROW A so all 8
        nozzles stay on the plate (tips already picked up). Anchoring at the group's
        first source well would put nozzles off the front edge when that well is not in
        row A (e.g. a single_spot group sourcing D11/E11/F11)."""
        src = group.get("source", {})
        col = src.get("plate_column") or _well_column(_group_source_wells(group)[0])
        anchor = f"{plate_rows[0]}{col}"
        mix_vol = min(dil.get("mix_volume_ul", 0.0), total, float(pipette.max_volume))
        if dil.get("mix_reps", 0) > 0 and mix_vol > 0:
            protocol.comment(
                f"Mixing source column {col} 8-up ({dil['mix_reps']} x {mix_vol:g} uL).")
            pipette.mix(dil["mix_reps"], mix_vol, lw["plate"][anchor])

    def _dispense_settings(group: dict) -> dict:
        d = group.get("dispense", {})
        return {
            "z": float(d.get("z_mm", 1.0)),
            "air_gap": float(d.get("air_gap_ul", 0.0)),
            "air_gap_height": float(d.get("air_gap_height_mm", 10.0)),
            "dwell": float(d.get("post_dispense_delay_s", 0.0) or 0.0),
            "move_speed": (float(d["move_speed_mm_per_s"])
                           if d.get("move_speed_mm_per_s") is not None else None),
            "blow_out": bool(d.get("blow_out", False)),
            "touch_tip": bool(d.get("touch_tip", False)),
        }

    def _print_column_8up(group: dict, inst, do_mix: bool) -> None:
        """P300 8-channel print: pick an 8-tip block, optionally mix, print, return."""
        block = int(group["tips"]["block_column"])
        block_tip = resolve_print_tip_for_column(block, tiprack_rows)
        inst.configure_nozzle_layout(style=ALL, tip_racks=[lw["tiprack"]])
        inst.pick_up_tip(lw["tiprack"][block_tip])
        protocol.comment(
            f"[{group['name']}] column_8up on {inst.name}: picked 8 tips from column {block}.")
        if do_mix:
            _mix_source_column(group)
        wells = _group_source_wells(group)
        s = _dispense_settings(group)
        vol = float(group["volume_ul"])
        spacing = group["destination"].get("spacing_mm", {"x": 9.0, "y": 0.0, "z": 0.0})
        start_col = int(group["destination"]["paper_start_column"])
        reps = int(group["replicates"])
        paper_well = lw["paper"][f"{plate_rows[0]}{start_col}"]
        drops = _droplets_per_spot(group)
        remaining = reps * drops
        for rep in range(reps):
            dest = paper_well.bottom(s["z"]).move(Point(
                x=rep * spacing.get("x", 9.0),
                y=rep * spacing.get("y", 0.0),
                z=rep * spacing.get("z", 0.0)))
            # Stacked droplets: each one re-aspirates and returns to the SAME spot.
            for drop in range(drops):
                inst.aspirate(vol, lw["plate"][wells[0]])
                if s["air_gap"] > 0:
                    inst.air_gap(s["air_gap"], height=s["air_gap_height"])
                protocol.comment(
                    f"[{group['name']}] 8 droplets -> paper column ~{start_col + rep}, "
                    f"replicate {rep + 1}, droplet {drop + 1}/{drops}, "
                    f"{vol:g} uL, z={s['z']} mm.")
                if s["move_speed"] and s["move_speed"] > 0:
                    inst.move_to(dest, speed=s["move_speed"])
                    inst.dispense(vol + s["air_gap"])
                else:
                    inst.dispense(vol + s["air_gap"], dest)
                if s["blow_out"]:
                    inst.blow_out(dest)
                if s["dwell"] > 0:
                    protocol.delay(seconds=s["dwell"])
                if s["touch_tip"]:
                    inst.touch_tip()
                remaining -= 1
                # blow_out leaves the plunger unprepared; re-prepare IN AIR so the next
                # aspirate does not suck an extra slug at the source well's top.
                if s["blow_out"] and remaining > 0:
                    inst.move_to(paper_well.top(s["air_gap_height"]))
                    inst.prepare_to_aspirate()
        _return_or_drop_inst(inst)
        protocol.comment(
            f"[{group['name']}] {'returned' if return_tips else 'dropped'} 8-tip block.")

    def _resolve_mix_block(group: dict) -> int:
        """8-tip block column for a single_spot group's P300 pre-mix. Reuse the
        column_8up block that already services this SAME source column (so the block
        only ever touches one colour); else an explicit tips.mix_block_column; else a
        free tiprack column not used by any print block or dilution setup tip, assigned
        per distinct single_spot source column. Prevents cross-colour contamination."""
        src_col = str(group.get("source", {}).get("plate_column"))
        for g in print_groups:
            if (g.get("layout") == "column_8up"
                    and str(g.get("source", {}).get("plate_column")) == src_col):
                return int(g["tips"]["block_column"])
        explicit = group.get("tips", {}).get("mix_block_column")
        if explicit:
            return int(explicit)
        used = {int(g["tips"]["block_column"]) for g in print_groups
                if g.get("layout") == "column_8up" and g.get("tips", {}).get("block_column")}
        for t in [dil.get("water_setup_tip"), dil.get("setup_tip"),
                  *[s.get("setup_tip") for s in color_series]]:
            if t:
                used.add(_well_column(t))
        ss_cols: list = []
        for g in print_groups:
            if g.get("layout") == "single_spot":
                c = str(g.get("source", {}).get("plate_column"))
                if c not in ss_cols:
                    ss_cols.append(c)
        free = [c for c in range(1, 13) if c not in used]
        idx = ss_cols.index(src_col) if src_col in ss_cols else 0
        return free[idx % len(free)] if free else 1

    def _mix_with_p300(group: dict) -> None:
        """For a single_spot (P20) group, mix its source column 8-up with the P300,
        using a colour-safe tip block (see _resolve_mix_block)."""
        block = _resolve_mix_block(group)
        block_tip = resolve_print_tip_for_column(block, tiprack_rows)
        pipette.configure_nozzle_layout(style=ALL, tip_racks=[lw["tiprack"]])
        pipette.pick_up_tip(lw["tiprack"][block_tip])
        protocol.comment(
            f"[{group['name']}] P300 pre-mix of source column "
            f"{group.get('source', {}).get('plate_column')} with 8-tip block {block}.")
        _mix_source_column(group)
        _return_or_drop_inst(pipette)

    def _print_single_spot(group: dict, inst, do_mix: bool) -> None:
        """P20 single-channel print: one droplet at a time. NEVER an 8-nozzle layout.

        Source wells map down paper rows (A,B,...) within a paper column; replicates
        sweep across paper columns. The P20 tip is reused across the group and
        returned to its own 20 uL rack.
        """
        if do_mix:
            _mix_with_p300(group)  # single-channel P20 cannot mix a column 8-up
        wells = _group_source_wells(group)
        tips_cfg = group.get("tips", {})
        tip_well = tips_cfg.get("well")
        reuse = tips_cfg.get("reuse", True)
        s = _dispense_settings(group)
        vol = float(group["volume_ul"])
        start_col = int(group["destination"]["paper_start_column"])
        reps = int(group["replicates"])
        rack = _rack_for(inst.name)
        if reuse and tip_well:
            inst.pick_up_tip(rack[tip_well])
            protocol.comment(
                f"[{group['name']}] single_spot on {inst.name}: picked reusable tip {tip_well}.")
        drops = _droplets_per_spot(group)
        remaining = reps * len(wells) * drops
        for rep in range(reps):
            for wi, well in enumerate(wells):
                paper_row = plate_rows[wi % len(plate_rows)]
                paper_col = start_col + rep
                paper_well = lw["paper"][f"{paper_row}{paper_col}"]
                dest = paper_well.bottom(s["z"])
                # Stacked droplets: go back to the source well and print the SAME spot
                # again, `drops` times, so the spot receives vol * drops in total.
                for drop in range(drops):
                    if not reuse:
                        inst.pick_up_tip(rack[tip_well] if tip_well else None)
                    inst.aspirate(vol, lw["plate"][well])
                    if s["air_gap"] > 0:
                        inst.air_gap(s["air_gap"], height=s["air_gap_height"])
                    protocol.comment(
                        f"[{group['name']}] single droplet {well} -> paper "
                        f"{paper_row}{paper_col}, replicate {rep + 1}, "
                        f"droplet {drop + 1}/{drops}, {vol:g} uL, z={s['z']} mm.")
                    inst.dispense(vol + s["air_gap"], dest)
                    if s["blow_out"]:
                        inst.blow_out(dest)
                    if s["dwell"] > 0:
                        protocol.delay(seconds=s["dwell"])
                    remaining -= 1
                    if not reuse:
                        _return_or_drop_inst(inst)
                    elif s["blow_out"] and remaining > 0:
                        # Re-prepare the plunger IN AIR after blow_out so the next
                        # aspirate does not take an extra slug at the source well top.
                        inst.move_to(paper_well.top(s["air_gap_height"]))
                        inst.prepare_to_aspirate()
        if reuse:
            _return_or_drop_inst(inst)
        protocol.comment(
            f"[{group['name']}] {'returned' if return_tips else 'dropped'} P20 tip.")

    if params.do_dilution and dil["enabled"]:
        pipette.configure_nozzle_layout(
            style=SINGLE, start=CONFIG["pipette"]["single_start"], tip_racks=[lw["tiprack"]]
        )
        protocol.comment(
            f"Nozzle layout: SINGLE ({CONFIG['pipette']['single_start']}) for one-tip setup."
        )
        pipette.pick_up_tip(lw["tiprack"][water_setup_tip])
        protocol.comment(
            f"One-tip setup: picked tip {water_setup_tip} for water-only transfers."
        )
        protocol.comment(
            f"Vial aspiration height: {vial_aspirate_height} mm above modeled vial bottom."
        )

        for series in color_series:
            for well_name, fold in zip(series_wells[series["name"]], factors):
                _stock, water_vol = dilution_volumes(total, fold)
                if water_vol <= 0:
                    continue
                protocol.comment(
                    f"Dispensing {water_vol} uL water -> {series['name']} {well_name}."
                )
                pipette.aspirate(water_vol, water_vial_aspirate)
                pipette.dispense(water_vol, lw["plate"][well_name])

        _return_or_drop()
        protocol.comment(
            f"Water setup transfers done; {'returned' if return_tips else 'dropped'} "
            f"tip {water_setup_tip}."
        )

        for series in color_series:
            pipette.configure_nozzle_layout(
                style=SINGLE, start=CONFIG["pipette"]["single_start"], tip_racks=[lw["tiprack"]]
            )
            pipette.pick_up_tip(lw["tiprack"][series["setup_tip"]])
            protocol.comment(
                f"One-tip setup: picked tip {series['setup_tip']} for {series['name']} "
                f"stock transfers from vial {series['dye_vial']}."
            )
            for well_name, fold in zip(series_wells[series["name"]], factors):
                stock, _water_vol = dilution_volumes(total, fold)
                if stock <= 0:
                    continue
                protocol.comment(
                    f"Diluting well {well_name} to {fold:g}x "
                    f"({series['name']} stock {stock} uL); setup tip "
                    f"{series['setup_tip']}."
                )
                pipette.aspirate(stock, dye_vial_aspirates[series["name"]])
                pipette.dispense(stock, lw["plate"][well_name])
            _return_or_drop()
            protocol.comment(
                f"{series['name']} stock transfers done; "
                f"{'returned' if return_tips else 'dropped'} tip {series['setup_tip']}."
            )

        if not (params.do_print and pr.get("enabled", True)):
            # Dilution requested without printing: still mix each source column 8-up
            # with the P300 so the plate is homogeneous for later use / imaging.
            for group in print_groups:
                if not group.get("mix_before", True):
                    continue
                _mix_with_p300(group)
                protocol.comment(
                    f"Mixed source column {group['source'].get('plate_column')} "
                    f"(no print this run)."
                )

        protocol.comment(
            "Dilution series complete in plate columns "
            + ", ".join(
                f"{series['name']}={series['destination_column']}"
                for series in color_series
            )
            + "."
        )

    if params.do_print and pr.get("enabled", True):
        # Unified print dispatch: each group runs on its resolved pipette. Mixing
        # (when diluting this run) happens per group: column_8up mixes with its own
        # P300 block; single_spot mixes with the P300 first, then prints on the P20.
        do_mix = bool(params.do_dilution and dil["enabled"])
        for group in print_groups:
            # A group may opt out of its pre-mix (mix_before: false) when an earlier
            # group already mixed the same source column this run.
            group_mix = do_mix and bool(group.get("mix_before", True))
            inst = instruments.get(group["pipette"])
            if inst is None:
                raise RuntimeError(
                    f"Print group '{group['name']}' requires pipette "
                    f"'{group['pipette']}', which is not mounted "
                    f"(mounted: {sorted(instruments)}). Add it to CONFIG['pipettes']."
                )
            if group.get("layout") == "single_spot":
                _print_single_spot(group, inst, group_mix)
            else:
                _print_column_8up(group, inst, group_mix)
        protocol.comment(
            "Paper print complete: "
            + "; ".join(
                f"{g['name']} paper columns "
                f"{format_paper_columns(g['destination']['paper_start_column'], g['replicates'])}"
                for g in print_groups
            )
            + "."
        )

    # End-of-run image: one snapshot after the whole workflow.
    if cam.get("capture_after", True):
        _capture_image(protocol, "after.jpg")

    protocol.comment("=== Vial Dilution -> Paper Print Demo Completed ===")
