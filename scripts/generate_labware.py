"""
scripts/generate_labware.py
===========================
Programmatically generate Opentrons custom labware definition JSON
(schemaVersion 2) for regular grid plates / reservoirs / vial racks.

This reproduces what the Opentrons Labware Creator web tool produces, but
in code — so you can parametrically adjust well count, spacing, x/y/z
position, depth, diameter, and volume without clicking through the UI.

Coordinate convention (Opentrons OT-2)
--------------------------------------
* Origin is the FRONT-LEFT-BOTTOM corner of the labware.
* x increases to the RIGHT, y increases toward the BACK, z increases UP.
* Row "A" is at the BACK (highest y); wells within a column are listed
  back-to-front (A, B, C ...), matching how the Opentrons API expects
  `ordering` (column-major).
* Per the schema, well ``z`` is the **center-bottom of the well** relative
  to the labware bottom — i.e. how far the well floor sits ABOVE the deck.
  It equals ``zDimension - depth`` and is usually NOT 0. If you leave
  ``well_z`` unset it is computed for you.
* All of x/y/z are constrained to be >= 0 by the schema.

Example
-------
Generate a generic regular-grid plate (the bundled example reproduces the
hand-made 12-well plate's numbers; note Opentrons' own catalog item of a
similar name is a rectangular trough reservoir, not this circular plate)::

    python scripts/generate_labware.py

Make a variant with 6 columns and deeper wells::

    (edit the LabwareSpec at the bottom, or import make_labware from
     another script and pass your own spec)
"""

from __future__ import annotations

import json
import re
import string
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# Schema constraint: loadName / namespace must match this (lowercase, digits,
# dots, underscores only — no spaces, hyphens, or capitals).
_SAFE_STRING = re.compile(r"^[a-z0-9._]+$")


# ──────────────────────────────────────────────────────────────────
# Spec — every adjustable parameter lives here
# ──────────────────────────────────────────────────────────────────

@dataclass
class LabwareSpec:
    # --- Identity ---
    load_name: str                     # e.g. "usascientific12well_12_wellplate_6000ul"
    display_name: str                  # human-readable, shown in the app
    brand: str = "Custom"
    brand_ids: List[str] = field(default_factory=list)
    namespace: str = "custom_beta"     # OT-2 custom labware namespace
    version: int = 1
    display_category: str = "wellPlate"  # wellPlate | reservoir | tubeRack | tipRack ...

    # --- Layout format ---
    # "96Standard"/"384Standard" enable multichannel pipetting on standard
    # plates; "irregular" disables it; "trough"/"trash" for those types.
    plate_format: str = "irregular"    # 96Standard | 384Standard | trough | irregular | trash

    # --- Grid layout ---
    rows: int = 3                      # number of rows (A, B, C ...)
    cols: int = 4                      # number of columns (1, 2, 3 ...)

    # --- Well position (mm) ---
    # Position of the FIRST well (A1) measured from the labware's front-left origin.
    x_offset: float = 24.88            # x of column 1
    y_offset: float = 68.74            # y of row A (the back-most row)
    x_spacing: float = 26.0            # centre-to-centre spacing between columns
    y_spacing: float = 26.0            # centre-to-centre spacing between rows
    # z (center-bottom of well above labware bottom). If None, computed as
    # z_dimension - depth, per the schema. Rarely 0.
    well_z: Optional[float] = None

    # --- Well geometry ---
    shape: str = "circular"            # "circular" | "rectangular"
    diameter: Optional[float] = 22.1   # required if shape == "circular"
    x_length: Optional[float] = None   # required if shape == "rectangular" (well width, x)
    y_length: Optional[float] = None   # required if shape == "rectangular" (well depth, y)
    depth: float = 12.9                # how deep the well is (z)
    total_liquid_volume: float = 6000  # µL per well
    well_bottom_shape: str = "flat"    # flat | u | v

    # --- Outer plate footprint (mm) — standard SBS footprint by default ---
    x_dimension: float = 127.76
    y_dimension: float = 85.48
    z_dimension: float = 12.9

    # --- Misc ---
    is_tiprack: bool = False
    tip_length: Optional[float] = None   # REQUIRED when is_tiprack=True (mm)
    tip_overlap: Optional[float] = None  # optional: tip/nozzle overlap (mm)
    is_magnetic_module_compatible: bool = False
    quirks: List[str] = field(default_factory=list)  # e.g. centerMultichannelOnWells


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

def _row_letters(n: int) -> List[str]:
    """Return ['A', 'B', ... ] for n rows (supports >26 via AA, AB...)."""
    letters = []
    for i in range(n):
        if i < 26:
            letters.append(string.ascii_uppercase[i])
        else:
            letters.append(
                string.ascii_uppercase[i // 26 - 1] + string.ascii_uppercase[i % 26]
            )
    return letters


def _well_shape_fields(spec: LabwareSpec) -> Dict[str, Any]:
    """Return the shape-specific geometry keys for a single well."""
    if spec.shape == "circular":
        if spec.diameter is None:
            raise ValueError("circular wells require `diameter`")
        return {"shape": "circular", "diameter": spec.diameter}
    elif spec.shape == "rectangular":
        if spec.x_length is None or spec.y_length is None:
            raise ValueError("rectangular wells require `x_length` and `y_length`")
        return {"shape": "rectangular", "xDimension": spec.x_length, "yDimension": spec.y_length}
    raise ValueError(f"unknown shape: {spec.shape!r}")


def _validate_spec(spec: LabwareSpec, well_z: float) -> None:
    """Fail fast on the schema constraints the builder can't otherwise honour."""
    if not _SAFE_STRING.match(spec.load_name):
        raise ValueError(
            f"load_name {spec.load_name!r} is not a valid safeString "
            "(allowed: lowercase letters, digits, '.', '_')."
        )
    if not _SAFE_STRING.match(spec.namespace):
        raise ValueError(
            f"namespace {spec.namespace!r} is not a valid safeString "
            "(allowed: lowercase letters, digits, '.', '_')."
        )
    if spec.is_tiprack and spec.tip_length is None:
        raise ValueError("is_tiprack=True requires tip_length (mm).")
    # Schema: x/y/z are all >= 0. y is the value most likely to go negative
    # (tall grids with a small offset), so check the extreme row explicitly.
    min_y = round(spec.y_offset - (spec.rows - 1) * spec.y_spacing, 2)
    if min_y < 0:
        raise ValueError(
            f"computed y for the front row is {min_y} (< 0). Increase "
            "y_offset or reduce rows/y_spacing — schema requires y >= 0."
        )
    if well_z < 0:
        raise ValueError(
            f"computed well z is {well_z} (< 0). Check depth vs z_dimension "
            "(z = z_dimension - depth must be >= 0)."
        )


# ──────────────────────────────────────────────────────────────────
# Core builder
# ──────────────────────────────────────────────────────────────────

def make_labware(spec: LabwareSpec) -> Dict[str, Any]:
    """Build an Opentrons schemaVersion-2 labware definition dict."""
    row_letters = _row_letters(spec.rows)
    shape_fields = _well_shape_fields(spec)

    # z = center-bottom of well above the labware bottom. Per schema this is
    # zDimension - depth, not 0. Allow an explicit override.
    well_z = spec.well_z if spec.well_z is not None else round(spec.z_dimension - spec.depth, 2)

    _validate_spec(spec, well_z)

    # ordering is column-major: [[A1,B1,C1], [A2,B2,C2], ...]
    ordering: List[List[str]] = []
    wells: Dict[str, Any] = {}

    for c in range(spec.cols):          # column index 0..cols-1  → label c+1
        col_wells: List[str] = []
        x = round(spec.x_offset + c * spec.x_spacing, 2)
        for r in range(spec.rows):      # row index 0..rows-1     → letter A,B,C...
            name = f"{row_letters[r]}{c + 1}"
            y = round(spec.y_offset - r * spec.y_spacing, 2)  # row A = back = highest y
            wells[name] = {
                "depth": spec.depth,
                "totalLiquidVolume": spec.total_liquid_volume,
                **shape_fields,
                "x": x,
                "y": y,
                "z": well_z,
            }
            col_wells.append(name)
        ordering.append(col_wells)

    all_well_names = [name for col in ordering for name in col]

    parameters: Dict[str, Any] = {
        "format": spec.plate_format,
        "quirks": spec.quirks,
        "isTiprack": spec.is_tiprack,
        "isMagneticModuleCompatible": spec.is_magnetic_module_compatible,
        "loadName": spec.load_name,
    }
    if spec.is_tiprack:
        parameters["tipLength"] = spec.tip_length
        if spec.tip_overlap is not None:
            parameters["tipOverlap"] = spec.tip_overlap

    return {
        "ordering": ordering,
        "brand": {"brand": spec.brand, "brandId": spec.brand_ids},
        "metadata": {
            "displayName": spec.display_name,
            "displayCategory": spec.display_category,
            "displayVolumeUnits": "µL",
            "tags": [],
        },
        "dimensions": {
            "xDimension": spec.x_dimension,
            "yDimension": spec.y_dimension,
            "zDimension": spec.z_dimension,
        },
        "wells": wells,
        "groups": [
            {
                "metadata": {"wellBottomShape": spec.well_bottom_shape},
                "wells": all_well_names,
            }
        ],
        "parameters": parameters,
        "namespace": spec.namespace,
        "version": spec.version,
        "schemaVersion": 2,
        "cornerOffsetFromSlot": {"x": 0, "y": 0, "z": 0},
    }


def write_labware(spec: LabwareSpec, out_path: Path) -> Path:
    definition = make_labware(spec)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(definition, indent=4, ensure_ascii=False), encoding="utf-8")
    return out_path


# ──────────────────────────────────────────────────────────────────
# YAML config loading
# ──────────────────────────────────────────────────────────────────

# Keys allowed in a labware YAML that are NOT LabwareSpec fields.
_NON_SPEC_KEYS = {"output_dir"}
_SPEC_FIELDS = {f.name for f in fields(LabwareSpec)}


def spec_from_yaml(path: Path) -> LabwareSpec:
    """Load a labware YAML config into a validated LabwareSpec.

    Every key in the YAML must match a LabwareSpec field name (plus the
    optional ``output_dir``). Unknown keys raise — this catches typos like
    ``well_diameter`` instead of ``diameter`` early.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping of fields.")

    unknown = set(data) - _SPEC_FIELDS - _NON_SPEC_KEYS
    if unknown:
        raise ValueError(
            f"{path}: unknown field(s) {sorted(unknown)}. "
            f"Valid fields: {sorted(_SPEC_FIELDS)}"
        )

    kwargs = {k: v for k, v in data.items() if k in _SPEC_FIELDS}
    return LabwareSpec(**kwargs)


def build_from_config(path: Path) -> Path:
    """Generate one labware JSON from one YAML config file."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    spec = spec_from_yaml(path)

    repo_root = Path(__file__).resolve().parent.parent
    out_dir = Path(data.get("output_dir") or (repo_root / "labware"))
    if not out_dir.is_absolute():
        out_dir = repo_root / out_dir
    out_path = out_dir / f"{spec.load_name}.json"
    return write_labware(spec, out_path)


# ──────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────

def main(argv: List[str]) -> int:
    if not argv:
        print(
            "Usage: python scripts/generate_labware.py <config.yaml> [more.yaml ...]\n"
            "  Each YAML defines one labware (see configs/labware/_template.yaml).",
            file=sys.stderr,
        )
        return 1

    for arg in argv:
        path = Path(arg)
        if not path.exists():
            print(f"[ERROR] config not found: {path}", file=sys.stderr)
            return 1
        try:
            written = build_from_config(path)
            print(f"Wrote {written}")
        except (ValueError, TypeError) as e:
            print(f"[ERROR] {path}: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
