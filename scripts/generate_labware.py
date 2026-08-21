"""
scripts/generate_labware.py
===========================
CLI for generating Opentrons custom labware definition JSON (schemaVersion 2)
from a YAML config — what the Opentrons Labware Creator web tool produces, but
parametric and reproducible.

    python scripts/generate_labware.py configs/labware/my_plate.yaml

The engine lives in ``src/labware/``:

    schemas.py     validated parameters (pydantic)
    geometry.py    deterministic coordinates
    builder.py     schema-2 assembly + serialization
    validation.py  schema / geometry / json / opentrons layers
    pipeline.py    runs all of the above in order

This module stays as the CLI plus the ``LabwareSpec`` dataclass that
``src/agents/labware_tools.py`` builds configs from.

Coordinate convention (OT-2, millimetres)
-----------------------------------------
* Origin is the FRONT-LEFT-BOTTOM corner of the labware.
* x increases RIGHT, y increases toward the BACK, z increases UP.
* Row "A" is the BACK row (highest y); y decreases A -> H.
* Well ``z`` is the **centre-bottom of the well** above the labware floor —
  normally ``zDimension - depth``, and usually not 0. Leave ``well_z`` unset to
  compute it; set it when you have a measurement that differs.
* All of x/y/z must be >= 0 per the schema.

Output safety
-------------
Generation will NOT replace an existing definition whose content differs.
``labware/*.json`` files are calibrated against physical hardware. Re-running a
config that produces identical output is a no-op; producing different output
requires ``--overwrite``.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.labware.builder import build_rectangular_grid_labware, write_labware_json  # noqa: E402
from src.labware.pipeline import GenerationResult, generate_labware  # noqa: E402
from src.labware.schemas import SAFE_STRING, RectangularGridSpec  # noqa: E402
from src.labware.templates import NON_SPEC_KEYS, load_template_dict  # noqa: E402

# Kept for backwards compatibility with importers of this module.
_SAFE_STRING = SAFE_STRING


# ──────────────────────────────────────────────────────────────────
# Spec — every adjustable parameter lives here
# ──────────────────────────────────────────────────────────────────

@dataclass
class LabwareSpec:
    """Flat, mutable view of a labware config.

    Mirrors ``RectangularGridSpec`` field-for-field. It stays a dataclass
    because ``labware_tools.py`` introspects it with ``dataclasses.fields()``
    to build and describe YAML configs. Validation happens when it is converted
    to the pydantic spec — see :meth:`to_grid_spec`.
    """

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

    def to_grid_spec(self) -> RectangularGridSpec:
        """Validate into the canonical pydantic spec. Raises on bad parameters."""
        return RectangularGridSpec(**{f.name: getattr(self, f.name) for f in fields(self)})


_SPEC_FIELDS = {f.name for f in fields(LabwareSpec)}
_NON_SPEC_KEYS = set(NON_SPEC_KEYS)


# ──────────────────────────────────────────────────────────────────
# Core builder
# ──────────────────────────────────────────────────────────────────

def make_labware(spec: LabwareSpec) -> Dict[str, Any]:
    """Build an Opentrons schemaVersion-2 labware definition dict.

    Raises ``pydantic.ValidationError`` / ``ValueError`` if the spec describes
    something the schema will not accept.
    """
    return build_rectangular_grid_labware(spec.to_grid_spec())


def write_labware(spec: LabwareSpec, out_path: Path, overwrite: bool = False) -> Path:
    """Build and write one definition. Refuses to clobber a differing file."""
    write_labware_json(make_labware(spec), Path(out_path), overwrite=overwrite)
    return Path(out_path)


# ──────────────────────────────────────────────────────────────────
# YAML config loading
# ──────────────────────────────────────────────────────────────────

def spec_from_yaml(path: Path) -> LabwareSpec:
    """Load a labware YAML config into a validated LabwareSpec.

    Every key in the YAML must match a LabwareSpec field name (plus the
    optional ``output_dir``). Unknown keys raise — this catches typos like
    ``well_diameter`` instead of ``diameter`` early.
    """
    data = load_template_dict(str(path))

    unknown = set(data) - _SPEC_FIELDS - _NON_SPEC_KEYS
    if unknown:
        raise ValueError(
            f"{path}: unknown field(s) {sorted(unknown)}. "
            f"Valid fields: {sorted(_SPEC_FIELDS)}"
        )

    spec = LabwareSpec(**{k: v for k, v in data.items() if k in _SPEC_FIELDS})
    spec.to_grid_spec()  # fail fast on invalid geometry, before anything is written
    return spec


def build_from_config(
    path: Path,
    overwrite: bool = False,
    write: bool = True,
) -> GenerationResult:
    """Generate one labware JSON from one YAML config file, with validation."""
    data = load_template_dict(str(path))
    spec = spec_from_yaml(path)

    output_dir: Optional[Path] = None
    if data.get("output_dir"):
        output_dir = Path(data["output_dir"])
        if not output_dir.is_absolute():
            output_dir = REPO / output_dir

    return generate_labware(
        spec.to_grid_spec(), output_dir=output_dir, overwrite=overwrite, write=write
    )


# ──────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────

def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/generate_labware.py",
        description="Generate Opentrons custom labware JSON from YAML config(s).",
        epilog="Each YAML defines one labware — see configs/labware/_template.yaml.",
    )
    parser.add_argument("configs", nargs="+", metavar="CONFIG", help="labware YAML config file(s)")
    parser.add_argument(
        "--overwrite", action="store_true",
        help="replace an existing definition whose content differs (default: refuse)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="build and validate but write nothing",
    )
    args = parser.parse_args(argv)

    exit_code = 0
    for arg in args.configs:
        path = Path(arg)
        if not path.exists():
            print(f"[ERROR] config not found: {path}", file=sys.stderr)
            exit_code = 1
            continue
        try:
            result = build_from_config(path, overwrite=args.overwrite, write=not args.dry_run)
        except (ValueError, TypeError) as exc:
            print(f"[ERROR] {path}: {exc}", file=sys.stderr)
            exit_code = 1
            continue

        print(result.summary())
        if not result.success:
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
