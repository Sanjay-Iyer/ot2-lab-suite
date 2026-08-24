"""Version 11 labware registry and shared config resolution.

Standalone on purpose: the Version 11 family does not import from the Version 1
modules, so V1 stays a frozen reference implementation that V11 cannot break.

Everything here is pure Python with no opentrons import, so the home laptop can
exercise it without a robot and without the simulator.
"""
from __future__ import annotations

import math
import re
from typing import Any

ROWS = "ABCDEFGH"
COLUMNS = list(range(1, 13))
WELL_RE = re.compile(r"^([A-H])([1-9]|1[0-2])$")


class V11ConfigError(ValueError):
    """A Version 11 configuration value is missing, malformed or unsafe."""


#: Registered labware. `load_name` is fixed laboratory hardware and may never be
#: invented by an agent; slot and heights are per-experiment.
LABWARE: dict[str, dict[str, Any]] = {
    "vial_rack": {
        "load_name": "tuberack_3dprint_20ml_8vials_v2",
        "namespace": "custom_beta",
        "version": 1,
        "usual_slot": 7,
        "well_depth_mm": 55.0,
        "default_loaded_volume_ul": 5000.0,
        "default_minimum_remaining_ul": 100.0,
        "max_volume_ul": 20000.0,
        "default_aspirate_height_mm": 4.0,
        "wells": [f"{r}{c}" for r in "AB" for c in range(1, 5)],
        "description": "Custom 3D-printed 20 mL vial rack",
    },
    "corning_plate": {
        "load_name": "corning_96_wellplate_360ul_custom",
        "namespace": "custom_beta",
        "version": 1,
        "usual_slot": 4,
        "well_depth_mm": 10.67,
        "default_loaded_volume_ul": 300.0,
        "default_minimum_remaining_ul": 20.0,
        "max_volume_ul": 360.0,
        "default_aspirate_height_mm": 1.0,
        "wells": [f"{r}{c}" for r in ROWS for c in COLUMNS],
        "description": "Existing custom Corning 96-well plate (360 uL)",
    },
    "well_plate": {
        "load_name": "brand_96_wellplate_350ul_flat_781662",
        "namespace": "custom_beta",
        "version": 1,
        "usual_slot": 1,
        "well_depth_mm": 10.65,
        "default_loaded_volume_ul": 300.0,
        "default_minimum_remaining_ul": 20.0,
        "max_volume_ul": 350.0,
        "default_aspirate_height_mm": 1.0,
        "wells": [f"{r}{c}" for r in ROWS for c in COLUMNS],
        "description": "BRAND Ref. 781662 96-well flat-bottom plate (350 uL)",
    },
}

PAPER = {
    "load_name": "paper_print_96_flat",
    "namespace": "custom_beta",
    "version": 1,
    "usual_slots": (5, 11),
    "wells": [f"{r}{c}" for r in ROWS for c in COLUMNS],
    # 8x12 grid, exact 9.00 mm pitch, first well centre at (14.38, 74.24).
    "grid_origin_x_mm": 14.38,
    "grid_origin_y_mm": 74.24,
    "pitch_mm": 9.0,
    "x_dimension_mm": 127.76,
    "y_dimension_mm": 85.48,
    "edge_margin_mm": 4.5,
}

TIPRACK = {"load_name": "opentrons_96_tiprack_20ul", "usual_slot": 9, "capacity": 96}

#: Reverse lookup so a config may name the labware literally.
BY_LOAD_NAME = {spec["load_name"]: key for key, spec in LABWARE.items()}

PIPETTE_DEFAULTS = {
    "name": "p20_single_gen2",
    "mount": "left",
    "max_volume_ul": 20.0,
    "min_volume_ul": 1.0,
    "aspirate_flow_rate_ul_s": 3.0,
    "dispense_flow_rate_ul_s": 3.0,
}


# --------------------------------------------------------------------------- #
# Well helpers
# --------------------------------------------------------------------------- #

def normalize_well(value: Any, *, label: str = "well") -> str:
    well = str(value).strip().upper()
    if not WELL_RE.match(well):
        raise V11ConfigError(f"{label} {value!r} is not a well between A1 and H12")
    return well


def well_exists(well: str, labware_key: str) -> bool:
    return well in LABWARE[labware_key]["wells"]


def paper_well_xy(well: str) -> tuple[float, float]:
    """Centre of a paper well in labware millimetres."""
    well = normalize_well(well, label="paper well")
    row = ROWS.index(well[0])
    column = int(well[1:]) - 1
    return (
        PAPER["grid_origin_x_mm"] + column * PAPER["pitch_mm"],
        PAPER["grid_origin_y_mm"] - row * PAPER["pitch_mm"],
    )


def paper_bounds() -> dict[str, float]:
    """Usable print box: the well-centre grid grown by half a pitch."""
    xs = [paper_well_xy(f"A{c}")[0] for c in COLUMNS]
    ys = [paper_well_xy(f"{r}1")[1] for r in ROWS]
    margin = PAPER["edge_margin_mm"]
    return {
        "min_x": min(xs) - margin, "max_x": max(xs) + margin,
        "min_y": min(ys) - margin, "max_y": max(ys) + margin,
    }


def resolve_targets(explicit: Any, selection: Any, *, label: str = "targets") -> list[str]:
    """Resolve explicit targets or a row/column selector into explicit wells."""
    if explicit:
        if isinstance(explicit, str):
            explicit = [explicit]
        return [normalize_well(w, label=label) for w in explicit]
    if not selection:
        raise V11ConfigError(f"{label}: give explicit wells or a target_selection")
    if not isinstance(selection, dict):
        raise V11ConfigError(f"{label}.target_selection must be a mapping")

    column = selection.get("column")
    columns = selection.get("columns")
    row = selection.get("row")
    rows = selection.get("rows")

    has_column = column is not None or columns is not None
    has_row = row is not None or rows is not None
    if not has_column and not has_row:
        raise V11ConfigError(f"{label}.target_selection needs a column/columns or row/rows")

    # Each axis is taken from whatever was named on it; an unnamed axis spans
    # fully. Naming both narrows both -- {row: B, columns: [1,2,3]} is B1,B2,B3.
    if columns is not None:
        chosen_columns = [int(c) for c in columns]
    elif column is not None:
        chosen_columns = [int(column)]
    else:
        chosen_columns = list(COLUMNS)
    if rows is not None:
        chosen_rows = [str(r).upper() for r in rows]
    elif row is not None:
        chosen_rows = [str(row).upper()]
    else:
        chosen_rows = list(ROWS)

    for c in chosen_columns:
        if c not in COLUMNS:
            raise V11ConfigError(f"{label}: column {c} is outside 1-12")
    for r in chosen_rows:
        if r not in ROWS:
            raise V11ConfigError(f"{label}: row {r!r} is outside A-H")
    # Walk down a named column; walk across a named row.
    if has_column and not has_row:
        return [f"{r}{c}" for c in chosen_columns for r in chosen_rows]
    if has_row and not has_column:
        return [f"{r}{c}" for r in chosen_rows for c in chosen_columns]
    # Both named: row-major within the selected block.
    return [f"{r}{c}" for r in chosen_rows for c in chosen_columns]


def resolve_series_wells(series: dict[str, Any]) -> list[str]:
    """Explicit `wells`, or start_well/direction/steps resolved into explicit."""
    if series.get("wells"):
        wells = [normalize_well(w, label="series well") for w in series["wells"]]
        if len(wells) < 2:
            raise V11ConfigError("series needs at least two wells")
        return wells
    start = series.get("start_well")
    steps = series.get("steps")
    if not start or not steps:
        raise V11ConfigError(
            "series needs explicit wells, or start_well + steps (+ direction)"
        )
    steps = int(steps)
    if steps < 2:
        raise V11ConfigError(f"series.steps must be >= 2, got {steps}")
    start = normalize_well(start, label="series.start_well")
    direction = str(series.get("direction", "row")).lower()
    row_index, column_index = ROWS.index(start[0]), int(start[1:]) - 1
    wells = []
    for step in range(steps):
        if direction == "row":          # across columns
            r, c = row_index, column_index + step
        elif direction == "column":     # down rows
            r, c = row_index + step, column_index
        else:
            raise V11ConfigError(
                f"series.direction must be 'row' or 'column', got {direction!r}"
            )
        if r >= len(ROWS) or c >= len(COLUMNS):
            raise V11ConfigError(
                f"series of {steps} steps from {start} going by {direction} runs off "
                "the plate"
            )
        wells.append(f"{ROWS[r]}{c + 1}")
    return wells


# --------------------------------------------------------------------------- #
# Source / labware block resolution
# --------------------------------------------------------------------------- #

def resolve_labware_block(
    block: dict[str, Any] | None,
    *,
    label: str,
    wells_key: str = "well",
    default_type: str = "vial_rack",
) -> dict[str, Any]:
    """Resolve a source/destination block into deck spec + validated wells."""
    if not isinstance(block, dict):
        raise V11ConfigError(f"{label} must be a mapping")
    block = dict(block)

    key = block.get("type")
    if not key:
        declared = block.get("labware")
        if declared:
            key = BY_LOAD_NAME.get(str(declared))
            if key is None:
                raise V11ConfigError(
                    f"{label}.labware {declared!r} is not registered; known: "
                    f"{', '.join(sorted(BY_LOAD_NAME))}"
                )
        else:
            key = default_type
    if key not in LABWARE:
        raise V11ConfigError(
            f"{label}.type {key!r} is not registered; known: "
            f"{', '.join(sorted(LABWARE))}"
        )
    spec = LABWARE[key]

    declared_load = block.get("labware")
    if declared_load and str(declared_load) != spec["load_name"]:
        raise V11ConfigError(
            f"{label}.labware {declared_load!r} does not match type {key!r} "
            f"({spec['load_name']})"
        )

    raw_wells = block.get("wells")
    if raw_wells is None:
        single = block.get(wells_key) or block.get("well")
        raw_wells = [single] if single else []
    if isinstance(raw_wells, str):
        raw_wells = [raw_wells]
    wells = [normalize_well(w, label=f"{label} well") for w in raw_wells]
    if not wells:
        raise V11ConfigError(f"{label} must declare at least one well")
    duplicates = sorted({w for w in wells if wells.count(w) > 1})
    if duplicates:
        raise V11ConfigError(f"{label} wells must be unique; repeated: {duplicates}")
    missing = [w for w in wells if not well_exists(w, key)]
    if missing:
        raise V11ConfigError(
            f"{label}: {', '.join(missing)} do not exist on {spec['load_name']}"
        )

    slot = int(block.get("slot", spec["usual_slot"]))
    if not 1 <= slot <= 11:
        raise V11ConfigError(f"{label}.slot must be 1-11, got {slot}")

    height = float(
        block.get("aspirate_height_mm", spec["default_aspirate_height_mm"])
    )
    if not 0 < height < spec["well_depth_mm"]:
        raise V11ConfigError(
            f"{label}.aspirate_height_mm must be > 0 and < {spec['well_depth_mm']:g} "
            f"for {key}, got {height:g}"
        )

    loaded = float(block.get("loaded_volume_ul", 0.0) or 0.0)
    if loaded < 0:
        raise V11ConfigError(f"{label}.loaded_volume_ul must be >= 0")
    if loaded > spec["max_volume_ul"] + 1e-9:
        raise V11ConfigError(
            f"{label}.loaded_volume_ul {loaded:g} exceeds the well capacity "
            f"{spec['max_volume_ul']:g}"
        )
    reserve = float(block.get("minimum_remaining_ul", 0.0) or 0.0)
    if reserve < 0:
        raise V11ConfigError(f"{label}.minimum_remaining_ul must be >= 0")

    return {
        "type": key,
        "deck_spec": {
            "slot": slot,
            "load_name": spec["load_name"],
            "namespace": spec["namespace"],
            "version": spec["version"],
        },
        "wells": wells,
        "aspirate_height_mm": height,
        "loaded_volume_ul": loaded,
        "minimum_remaining_ul": reserve,
        "material": str(block.get("material", "liquid")),
        "well_depth_mm": spec["well_depth_mm"],
        "well_max_volume_ul": spec["max_volume_ul"],
    }


def resolve_paper_block(block: dict[str, Any] | None) -> dict[str, Any]:
    block = dict(block or {})
    slot = int(block.get("slot", 11))
    if not 1 <= slot <= 11:
        raise V11ConfigError(f"paper.slot must be 1-11, got {slot}")
    declared = block.get("labware")
    if declared and str(declared) != PAPER["load_name"]:
        raise V11ConfigError(
            f"paper.labware {declared!r} is not the registered paper labware "
            f"({PAPER['load_name']})"
        )
    height = float(block.get("print_height_mm", 0.5))
    if not 0 <= height < 10:
        raise V11ConfigError(
            f"paper.print_height_mm must be >= 0 and < 10, got {height:g}"
        )
    return {
        "deck_spec": {
            "slot": slot,
            "load_name": PAPER["load_name"],
            "namespace": PAPER["namespace"],
            "version": PAPER["version"],
        },
        "print_height_mm": height,
    }


def resolve_tips(block: dict[str, Any] | None, *, top_level_reuse: Any = None) -> dict[str, Any]:
    block = dict(block or {})
    reuse = top_level_reuse if top_level_reuse is not None else block.get(
        "pipette_tip_reuse", True
    )
    start_tip = normalize_well(block.get("start_tip", "A1"), label="tips.start_tip")
    return {
        "pipette_tip_reuse": bool(reuse),
        "return_tips": bool(block.get("return_tips", True)),
        "start_tip": start_tip,
    }


def resolve_pipetting(block: dict[str, Any] | None) -> dict[str, Any]:
    block = dict(block or {})
    out = {
        "aspirate_flow_rate_ul_s": float(
            block.get("aspirate_flow_rate_ul_s", PIPETTE_DEFAULTS["aspirate_flow_rate_ul_s"])
        ),
        "dispense_flow_rate_ul_s": float(
            block.get("dispense_flow_rate_ul_s", PIPETTE_DEFAULTS["dispense_flow_rate_ul_s"])
        ),
        "air_gap_ul": float(block.get("air_gap_ul", 1.5)),
        "air_gap_height_mm": float(block.get("air_gap_height_mm", 5.0)),
        "post_aspirate_delay_s": float(block.get("post_aspirate_delay_s", 0.0)),
        "post_dispense_delay_s": float(block.get("post_dispense_delay_s", 0.0)),
    }
    if out["aspirate_flow_rate_ul_s"] <= 0 or out["dispense_flow_rate_ul_s"] <= 0:
        raise V11ConfigError("pipetting flow rates must be > 0")
    for key in ("air_gap_ul", "air_gap_height_mm", "post_aspirate_delay_s",
                "post_dispense_delay_s"):
        if out[key] < 0:
            raise V11ConfigError(f"pipetting.{key} must be >= 0")
    if out["air_gap_ul"] > PIPETTE_DEFAULTS["max_volume_ul"]:
        raise V11ConfigError(
            f"pipetting.air_gap_ul {out['air_gap_ul']:g} exceeds the pipette capacity"
        )
    return out


def chunk_volume(
    total: float,
    max_chunk: float,
    air_gap: float,
    capacity: float,
    *,
    on_conflict: str = "reduce_chunk",
    label: str = "transfer",
) -> list[float]:
    """Split a volume into chunks that always fit liquid + air gap.

    Never returns a chunk where chunk + air_gap > capacity.
    """
    if total <= 0:
        return []
    if max_chunk <= 0:
        raise V11ConfigError(f"{label}.max_chunk_ul must be > 0")
    if air_gap >= capacity:
        raise V11ConfigError(
            f"{label}: air gap {air_gap:g} uL leaves no room in a {capacity:g} uL pipette"
        )
    usable = capacity - air_gap
    if max_chunk > usable + 1e-9:
        if on_conflict == "fail":
            raise V11ConfigError(
                f"{label}: max_chunk_ul {max_chunk:g} + air gap {air_gap:g} exceeds "
                f"the {capacity:g} uL pipette capacity"
            )
        max_chunk = usable
    if max_chunk <= 0:
        raise V11ConfigError(f"{label}: no usable chunk size remains")
    count = int(math.ceil(total / max_chunk - 1e-9))
    even = total / count
    return [even] * count
