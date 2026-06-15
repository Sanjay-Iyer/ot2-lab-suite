#!/usr/bin/env python3
"""
OT-2 Protocol: 96-Well Plate -> 20 mL Waste Vial Disposal (config-driven)
========================================================================
Empties wells of a 96-well plate into ONE 20 mL scintillation vial in the custom
v2 tube rack, ONE WELL AT A TIME, using exactly ONE single-nozzle tip for the whole
job. Use it to clear a plate column (default column 9) into a dedicated waste vial
(default A4) before re-running an experiment. Tips are RETURNED to the box by
default (set waste.return_tips=false to trash them instead).

>>> EVERYTHING is driven by the CONFIG dict below. <<<
Edit CONFIG directly for a quick change, OR keep the canonical settings in
  configs/workflows/defaults/plate_waste_disposal.yaml
and run  scripts/build_plate_waste_disposal.py  to generate a robot-ready copy with
that YAML embedded between the CONFIG markers.

WHY apiLevel 2.28 / HOW TO RUN
  * Single-nozzle setup uses active nozzle A1 and an H-row setup tip so only one tip
    engages, identical to the vial_dilution_print demo.
  * apiLevel 2.28 is required for return_tip() in partial (single-nozzle) mode.
  * RUN PATH: 2.28 uses the new protocol engine, so on the real OT-2 run this from the
    Opentrons App or the HTTP API (provides the deck config). Bare opentrons_execute
    over SSH fails with AreaNotInDeckConfigurationError — that is expected, not a bug.

RUN-MODE FLAGS (App Runtime Parameters; DEFAULT_* / CONFIG mirror them for simulation):
  dry_run         - load + pre-flight + comments only, no liquid motion
  source_columns  - which plate column(s) to empty, e.g. "9" or "9,11"
  waste_vial      - which 20 mL rack vial collects the waste, e.g. A4
"""

import re

from opentrons import protocol_api
from opentrons.protocol_api import SINGLE

metadata = {
    "protocolName": "OT-2 96-Well Plate -> 20 mL Waste Vial Disposal",
    "author": "Antigravity AI Agent",
    "description": (
        "Config-driven single-tip emptying of a 96-well plate column into one "
        "20 mL waste vial, one well at a time."
    ),
    "apiLevel": "2.28",  # 2.28 REQUIRED: partial-mode return_tip() (blocked < 2.28)
}

# ── Runtime-parameter DEFAULTS (operator overrides in the App per run) ──────────
DEFAULT_DRY_RUN = False

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
            'tiprack': {'slot': 9, 'load_name': 'opentrons_96_tiprack_300ul'}},
  'pipette': {'name': 'p300_multi_gen2', 'mount': 'right', 'single_start': 'A1'},
  'waste': { 'source_columns': ['9'],
             'source_rows': ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'],
             'waste_vial': 'A4',
             'volume_ul': 200.0,
             'setup_tip': 'H12',
             'plate_aspirate_height_mm': 0.8,
             'waste_dispense_height_mm': 45.0,
             'blow_out': True,
             'return_tips': True},
  'safety': { 'expected_tuberack_load_name': 'tuberack_3dprint_20ml_8vials_v2',
              'expected_rack_well_count': 8,
              'expected_vial_depth_mm': 55.0,
              'expected_plate_well_count': 96,
              'pipette_max_volume_ul': 300.0,
              'tiprack_rows_per_column': 8,
              'geometry_tolerance_mm': 0.5}}
# <<< CONFIG END <<<
# ════════════════════════════════════════════════════════════════════════════════


# Fixed v2 rack vials (2 rows A/B x 4 columns). Used ONLY to populate the App
# "Waste vial" dropdown — add_parameters runs before labware loads, so the well
# names can't be derived from the rack here. Pre-flight still validates the chosen
# vial against the actually-loaded rack, so a mismatch aborts cleanly.
_RACK_VIAL_WELLS = ["A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4"]


def _config_source_columns() -> list:
    """Plate columns from CONFIG, accepting the new list or the legacy single key."""
    waste = CONFIG["waste"]
    cols = waste.get("source_columns")
    if cols is None:
        single = waste.get("source_column")
        cols = [single] if single is not None else []
    return [str(c).strip() for c in cols if str(c).strip()]


def _default_source_columns_str() -> str:
    return ",".join(_config_source_columns()) or "9"


def _source_columns_choices() -> list:
    """Dropdown options for the App. Opentrons str params must be choice-constrained,
    so we offer each single column 1-12 plus the common combos. Arbitrary subsets
    (e.g. "5,7") are still possible via waste.source_columns in the YAML config.
    """
    default = _default_source_columns_str()
    choices, seen = [], set()

    def _add(value: str, label: str) -> None:
        if value not in seen:
            choices.append({"display_name": label, "value": value})
            seen.add(value)

    for c in range(1, 13):
        _add(str(c), f"Column {c}")
    _add("9,11", "Cols 9 & 11 (both dyes)")
    _add("all", "All columns (1-12)")
    if default not in seen:                        # keep a custom config default selectable
        _add(default, default)
    return choices


def _default_waste_vial() -> str:
    return str(CONFIG["waste"]["waste_vial"]).upper()


def _waste_vial_choices() -> list:
    default = _default_waste_vial()
    wells = list(_RACK_VIAL_WELLS)
    if default not in wells:                       # honor a config vial outside the grid
        wells.append(default)
    return [{"display_name": w, "value": w} for w in wells]


def add_parameters(parameters: protocol_api.ParameterContext):
    parameters.add_bool(
        variable_name="dry_run", display_name="Dry run (no liquid)",
        description="Load labware, run pre-flight and comments only - no liquid motion.",
        default=DEFAULT_DRY_RUN)
    parameters.add_str(
        variable_name="source_columns", display_name="Plate columns to empty",
        description="Which 96-well plate column(s) to empty into the waste vial.",
        choices=_source_columns_choices(),
        default=_default_source_columns_str())
    parameters.add_str(
        variable_name="waste_vial", display_name="Waste vial",
        description="Vial in the 20 mL rack that collects all the waste.",
        choices=_waste_vial_choices(),
        default=_default_waste_vial())


# ── Helpers ──────────────────────────────────────────────────────────────────────

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


def resolve_source_columns(waste_cfg: dict, runtime_columns=None,
                           plate_columns=None) -> list:
    """Ordered, de-duplicated list of plate column labels to empty.

    Precedence: runtime parameter (operator flag in the App / runner) > config
    source_columns > legacy config source_column. Accepts either a list or a
    comma/whitespace-separated string (e.g. "9", "9,11", "9 11"). The literal token
    "all" expands to every plate column. When plate_columns is provided, each
    requested column is validated against the loaded plate.
    """
    raw = runtime_columns
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raw = _config_source_columns()
    if isinstance(raw, str):
        tokens = [t for t in re.split(r"[,\s]+", raw.strip()) if t]
    else:
        tokens = [str(t).strip() for t in raw if str(t).strip()]
    columns = []
    for tok in tokens:
        if tok.lower() == "all":
            if not plate_columns:
                raise ValueError("'all' columns requested but plate column list is unavailable.")
            for col in plate_columns:
                if col not in columns:
                    columns.append(col)
            continue
        if not tok.isdigit():
            raise ValueError(
                f"Invalid plate column {tok!r}: columns must be positive integers or "
                "'all' (e.g. '9', '9,11', 'all')."
            )
        col = str(int(tok))
        if plate_columns is not None and col not in plate_columns:
            errors = ", ".join(plate_columns)
            raise ValueError(f"Column {col!r} is not a valid plate column (available: {errors}).")
        if col not in columns:
            columns.append(col)
    if not columns:
        raise ValueError(
            "No plate columns selected — set waste.source_columns or the "
            "'Plate columns to empty' flag."
        )
    return columns


def resolve_waste_vial(waste_cfg: dict, runtime_vial=None) -> str:
    """Destination waste vial. Runtime flag overrides config; existence checked in pre-flight."""
    vial = runtime_vial if (runtime_vial is not None and str(runtime_vial).strip()) \
        else waste_cfg["waste_vial"]
    return str(vial).upper()


def resolve_source_wells(waste_cfg: dict, columns: list, plate_rows: list) -> list:
    """Ordered plate wells to empty = source_rows x each column, column by column."""
    rows = [str(r).upper() for r in waste_cfg["source_rows"]]
    for r in rows:
        if r not in plate_rows:
            raise ValueError(
                f"waste.source_rows entry {r!r} is not a valid plate row "
                f"(valid rows: {plate_rows})."
            )
    return [f"{r}{col}" for col in columns for r in rows]


# ── Pre-flight ───────────────────────────────────────────────────────────────────

def _preflight(protocol, lw, pipette, source_wells, setup_tip, waste_vial_name,
               plate_rows: list, tiprack_rows: list):
    """Validate config + loaded-labware geometry BEFORE any motion. Raise to abort."""
    errors = []
    deck   = CONFIG["deck"]
    safety = CONFIG["safety"]
    waste  = CONFIG["waste"]
    tol    = safety["geometry_tolerance_mm"]

    # ── Deck slot uniqueness ──────────────────────────────────────────────────────
    slots = [deck["tuberack"]["slot"], deck["plate"]["slot"], deck["tiprack"]["slot"]]
    if len(slots) != len(set(slots)):
        errors.append(f"Duplicate deck slot assignments: {slots}.")

    # ── Tube rack identity + geometry ─────────────────────────────────────────────
    tuberack = lw["tuberack"]
    if tuberack.load_name != safety["expected_tuberack_load_name"]:
        errors.append(
            f"Loaded tube rack '{tuberack.load_name}' != expected "
            f"'{safety['expected_tuberack_load_name']}'."
        )
    if len(tuberack.wells()) != safety["expected_rack_well_count"]:
        errors.append(
            f"Tube rack has {len(tuberack.wells())} wells, expected "
            f"{safety['expected_rack_well_count']}."
        )

    # ── Waste vial exists in the rack ─────────────────────────────────────────────
    waste_vial_name = str(waste_vial_name).upper()
    rack_well_names = set(tuberack.wells_by_name().keys())
    if waste_vial_name not in rack_well_names:
        errors.append(
            f"waste.waste_vial {waste_vial_name!r} is not a well in the rack "
            f"(available: {sorted(rack_well_names)})."
        )

    # ── Plate well count + source wells ───────────────────────────────────────────
    if len(lw["plate"].wells()) != safety["expected_plate_well_count"]:
        errors.append(
            f"Plate has {len(lw['plate'].wells())} wells, expected "
            f"{safety['expected_plate_well_count']}."
        )
    plate_well_names = set(lw["plate"].wells_by_name().keys())
    for w in source_wells:
        if w not in plate_well_names:
            errors.append(f"Source well {w!r} is not present in the loaded plate.")
    if not source_wells:
        errors.append("No source wells resolved — waste.source_rows is empty.")

    # ── Heights within physical bounds ────────────────────────────────────────────
    vial_depth = float(safety["expected_vial_depth_mm"])
    asp_h = float(waste["plate_aspirate_height_mm"])
    disp_h = float(waste["waste_dispense_height_mm"])
    plate_depth = float(min(w.depth for w in lw["plate"].wells()))
    if not (0 <= asp_h < plate_depth):
        errors.append(
            f"waste.plate_aspirate_height_mm {asp_h} must be >= 0 and < plate well "
            f"depth {plate_depth} mm."
        )
    if not (0 < disp_h < vial_depth):
        errors.append(
            f"waste.waste_dispense_height_mm {disp_h} must be > 0 and < vial depth "
            f"{vial_depth} mm."
        )

    # ── Volume sanity ─────────────────────────────────────────────────────────────
    vol = float(waste["volume_ul"])
    if vol <= 0:
        errors.append(f"waste.volume_ul {vol} must be > 0.")
    if vol > pipette.max_volume:
        errors.append(
            f"waste.volume_ul {vol} > pipette max {pipette.max_volume} uL "
            "(a single aspiration cannot exceed the tip capacity)."
        )
    well_max = float(min(w.max_volume for w in lw["plate"].wells()))
    if vol > well_max:
        errors.append(
            f"waste.volume_ul {vol} > plate well max {well_max} uL.")

    # ── Tiprack rows vs safety config ─────────────────────────────────────────────
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

    # ── Setup tip: in rack + correct edge row so only one nozzle engages ──────────
    tip_names = set(lw["tiprack"].wells_by_name().keys())
    if setup_tip not in tip_names:
        errors.append(f"waste.setup_tip {setup_tip!r} is not in the loaded tiprack.")
    expected_row = _edge_pickup_row(CONFIG["pipette"]["single_start"], tiprack_rows)
    if _well_row(setup_tip) != expected_row:
        errors.append(
            f"waste.setup_tip must be in row {expected_row} when "
            f"single_start={CONFIG['pipette']['single_start']} so only one tip engages; "
            f"got {setup_tip}."
        )

    if errors:
        raise RuntimeError(
            "PRE-FLIGHT VALIDATION FAILED - no motion performed:\n  - " + "\n  - ".join(errors)
        )
    protocol.comment("Pre-flight validation passed: config + labware geometry OK.")


def run(protocol: protocol_api.ProtocolContext):
    params = protocol.params
    deck  = CONFIG["deck"]
    waste = CONFIG["waste"]
    return_tips = waste["return_tips"]

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
        "plate":    _load(deck["plate"]),
        "tiprack":  _load(deck["tiprack"]),
    }
    pipette = protocol.load_instrument(
        CONFIG["pipette"]["name"], CONFIG["pipette"]["mount"], tip_racks=[lw["tiprack"]]
    )

    plate_rows    = list(lw["plate"].rows_by_name().keys())
    plate_columns = list(lw["plate"].columns_by_name().keys())
    tiprack_rows  = list(lw["tiprack"].rows_by_name().keys())
    columns      = resolve_source_columns(
        waste, getattr(params, "source_columns", None), plate_columns)
    source_wells = resolve_source_wells(waste, columns, plate_rows)
    setup_tip    = str(waste["setup_tip"]).upper()
    waste_vial_name = resolve_waste_vial(waste, getattr(params, "waste_vial", None))
    volume = float(waste["volume_ul"])

    _preflight(protocol, lw, pipette, source_wells, setup_tip, waste_vial_name,
               plate_rows, tiprack_rows)

    protocol.comment("=== Plate -> Waste Vial Disposal Started ===")
    protocol.comment(
        f"Flags: dry_run={params.dry_run}, "
        f"source_columns={','.join(columns)}, waste_vial={waste_vial_name}"
    )
    protocol.comment(
        f"Plan: empty {volume:g} uL from plate column(s) {','.join(columns)} "
        f"({len(source_wells)} well(s): [{', '.join(source_wells)}]) -> waste vial "
        f"{waste_vial_name} (rack slot {deck['tuberack']['slot']}), one well at a time, "
        f"single tip {setup_tip}."
    )

    if params.dry_run:
        protocol.comment("DRY RUN: labware + pipette loaded, pre-flight passed. No liquid motion.")
        protocol.comment("=== Plate -> Waste Vial Disposal Completed (dry run) ===")
        return

    asp_h  = float(waste["plate_aspirate_height_mm"])
    disp_h = float(waste["waste_dispense_height_mm"])
    waste_vial = lw["tuberack"][waste_vial_name]

    # ── Single-tip waste transfer: one well at a time ─────────────────────────────
    pipette.configure_nozzle_layout(
        style=SINGLE, start=CONFIG["pipette"]["single_start"], tip_racks=[lw["tiprack"]]
    )
    protocol.comment(
        f"Nozzle layout: SINGLE ({CONFIG['pipette']['single_start']}) for single-tip waste removal."
    )
    pipette.pick_up_tip(lw["tiprack"][setup_tip])
    protocol.comment(
        f"One-tip setup: picked tip {setup_tip}. This is the only tip used for the whole job."
    )

    for idx, well_name in enumerate(source_wells, start=1):
        src = lw["plate"][well_name].bottom(asp_h)
        dest = waste_vial.bottom(disp_h)
        protocol.comment(
            f"Emptying well {well_name} ({idx}/{len(source_wells)}): aspirate {volume:g} uL "
            f"(z={asp_h} mm) -> waste vial {waste_vial_name} (z={disp_h} mm)."
        )
        pipette.aspirate(volume, src)
        pipette.dispense(volume, dest)
        if waste["blow_out"]:
            pipette.blow_out(dest)

    _return_or_drop()
    protocol.comment(
        f"Waste removal done; {'returned' if return_tips else 'dropped'} tip {setup_tip}. "
        f"{len(source_wells)} wells emptied into {waste_vial_name}."
    )
    protocol.comment("=== Plate -> Waste Vial Disposal Completed ===")
