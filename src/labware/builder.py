"""
Opentrons labware definition assembly and serialization.

Takes a validated spec plus already-computed positions and produces the
schemaVersion-2 dict, then writes it to disk. Geometry lives in
:mod:`src.labware.geometry`; validation lives in :mod:`src.labware.validation`.
This module only assembles and serializes.

Key ordering of both the definition and each well is fixed to match the
known-working files in ``labware/`` so that regenerating an existing
definition produces an identical document, not a reshuffled one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.labware.geometry import (
    GridPosition,
    column_major_ordering,
    flatten_ordering,
    generate_rectangular_grid,
)
from src.labware.schemas import CommonLabwareSpec, RectangularGridSpec

SCHEMA_VERSION = 2

# Labware JSON in this repo is written with 4-space indent (the Opentrons
# Labware Creator's own output uses 2; both parse identically).
JSON_INDENT = 4


class LabwareOutputExistsError(FileExistsError):
    """Raised when writing would replace an existing labware definition.

    Custom labware JSON is a physical-hardware artifact: a definition that has
    been printed, mounted and calibrated against real glassware must not be
    silently rewritten by a regeneration run. Callers must opt in explicitly.
    """


def build_well(spec: CommonLabwareSpec, position: GridPosition) -> Dict[str, Any]:
    """Assemble one well entry. Key order matches the reference definitions."""
    well: Dict[str, Any] = {
        "depth": spec.depth,
        "totalLiquidVolume": spec.total_liquid_volume,
        "shape": spec.shape,
    }
    if spec.shape == "circular":
        well["diameter"] = spec.diameter
    else:
        well["xDimension"] = spec.x_length
        well["yDimension"] = spec.y_length
    well["x"] = position.x
    well["y"] = position.y
    well["z"] = spec.resolved_well_z
    return well


def build_labware_definition(
    spec: CommonLabwareSpec,
    positions: Sequence[GridPosition],
) -> Dict[str, Any]:
    """Assemble a complete Opentrons schemaVersion-2 labware definition."""
    ordering: List[List[str]] = column_major_ordering(positions)
    by_name = {p.name: p for p in positions}

    # Wells are inserted in the same column-major order as ``ordering``.
    wells: Dict[str, Any] = {
        name: build_well(spec, by_name[name]) for name in flatten_ordering(ordering)
    }

    parameters: Dict[str, Any] = {
        "format": spec.plate_format,
        "quirks": list(spec.quirks),
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
        "brand": {"brand": spec.brand, "brandId": list(spec.brand_ids)},
        "metadata": {
            "displayName": spec.display_name,
            "displayCategory": spec.display_category,
            # U+00B5 MICRO SIGN — the schema's enum is ["µL", "mL", "L"].
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
                "wells": flatten_ordering(ordering),
            }
        ],
        "parameters": parameters,
        "namespace": spec.namespace,
        "version": spec.version,
        "schemaVersion": SCHEMA_VERSION,
        "cornerOffsetFromSlot": {"x": 0, "y": 0, "z": 0},
    }


def build_rectangular_grid_labware(spec: RectangularGridSpec) -> Dict[str, Any]:
    """Geometry then assembly, for the ``rectangular_grid`` family."""
    positions = generate_rectangular_grid(
        rows=spec.rows,
        columns=spec.cols,
        origin_x_mm=spec.x_offset,
        origin_y_mm=spec.y_offset,
        spacing_x_mm=spec.x_spacing,
        spacing_y_mm=spec.y_spacing,
    )
    return build_labware_definition(spec, positions)


def serialize_labware(definition: Dict[str, Any], indent: int = JSON_INDENT) -> str:
    """Render a definition as the JSON text that gets written to disk."""
    return json.dumps(definition, indent=indent, ensure_ascii=False)


def read_labware_json(path: Path) -> Dict[str, Any]:
    """Load a labware definition.

    Reads bytes rather than text: these files contain U+00B5 (``µL``) and the
    default locale on Windows is cp1252, which would mojibake it into ``ÂµL``
    and fail schema validation with a confusing message.
    """
    return json.loads(Path(path).read_bytes().decode("utf-8"))


def write_labware_json(
    definition: Dict[str, Any],
    out_path: Path,
    overwrite: bool = False,
    indent: int = JSON_INDENT,
) -> str:
    """Write a definition to ``out_path``; return what happened.

    Returns ``"created"``, ``"unchanged"`` (an identical definition was already
    there, so nothing was written), or ``"overwritten"``.

    Raises :class:`LabwareOutputExistsError` when the file exists, its content
    differs, and ``overwrite`` is False. That is the default, so a regeneration
    run can never quietly change labware that hardware has been calibrated to.
    """
    out_path = Path(out_path)
    payload = serialize_labware(definition, indent=indent)
    existed = out_path.exists()

    if existed:
        try:
            existing = read_labware_json(out_path)
        except (ValueError, UnicodeDecodeError):
            existing = None  # unreadable/corrupt — treat as a real difference

        if existing == definition:
            return "unchanged"
        if not overwrite:
            raise LabwareOutputExistsError(
                f"{out_path} already exists and its content differs from the definition "
                f"being generated. Refusing to overwrite a working labware definition.\n"
                f"  - to write a variant, change `load_name` (the filename follows it), or\n"
                f"  - pass overwrite=True / --overwrite if you really mean to replace it."
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(payload, encoding="utf-8")
    return "overwritten" if existed else "created"


def definition_output_path(spec: CommonLabwareSpec, output_dir: Optional[Path] = None) -> Path:
    """Where a spec's JSON belongs.

    The repo requires the filename to equal ``parameters.loadName`` and the file
    to sit flat in ``labware/`` — protocols and the robot-side custom-labware
    store both resolve definitions that way.
    """
    from src.utils.paths import LABWARE_OUTPUT_DIR

    base = Path(output_dir) if output_dir is not None else LABWARE_OUTPUT_DIR
    return base / f"{spec.load_name}.json"
