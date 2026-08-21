"""
Layered, deterministic validation for generated labware definitions.

Four layers, checked in order, each answering a different question. They are
NOT interchangeable and a PASS in one says nothing about the next:

===============  =========================================================
``schema``       Are the input parameters well-formed and self-consistent?
                 (pydantic — types, ranges, shape/tiprack cross-rules)
``geometry``     Does the described object make physical sense?
                 (unique names, correct count, wells inside the footprint
                 accounting for well radius, well floor inside the body)
``json``         Is the output a valid, round-trippable JSON document with
                 every key the schema marks required?
``opentrons``    Does Opentrons itself accept it?
                 (jsonschema against opentrons_shared_data's labware schema 2,
                 the LabwareDefinition2 pydantic model, and the official
                 ``opentrons.protocols.labware.verify_definition``)
===============  =========================================================

None of these is physical verification on an OT-2. A definition that passes
all four can still be wrong about the real object on the deck — only a
measured deck check can tell you that.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from src.labware.geometry import GridPosition
from src.labware.schemas import CommonLabwareSpec, RectangularGridSpec, WellPlate96SpecV1

LAYERS = ("schema", "geometry", "json", "opentrons")

PASS = "PASS"
FAIL = "FAIL"
NOT_RUN = "NOT_RUN"
NOT_AVAILABLE = "NOT_AVAILABLE"


@dataclass
class ValidationIssue:
    layer: str
    code: str
    message: str
    severity: str = "error"  # "error" | "warning"

    def __str__(self) -> str:
        return f"[{self.layer}/{self.severity}] {self.code}: {self.message}"


@dataclass
class ValidationReport:
    """Outcome of every validation layer that ran."""

    layers: Dict[str, str] = field(
        default_factory=lambda: {layer: NOT_RUN for layer in LAYERS}
    )
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        """True when no layer failed. ``NOT_AVAILABLE`` does not fail a run."""
        return not self.errors and all(s != FAIL for s in self.layers.values())

    def add(self, layer: str, code: str, message: str, severity: str = "error") -> None:
        self.issues.append(ValidationIssue(layer, code, message, severity))
        if severity == "error":
            self.layers[layer] = FAIL

    def mark(self, layer: str, status: str) -> None:
        """Set a layer's status unless it has already failed."""
        if self.layers.get(layer) != FAIL:
            self.layers[layer] = status

    def summary(self) -> str:
        head = "  ".join(f"{layer}={self.layers[layer]}" for layer in LAYERS)
        if not self.issues:
            return head
        return head + "\n" + "\n".join(f"  {issue}" for issue in self.issues)


# ──────────────────────────────────────────────────────────────────
# Layer 2 — geometry
# ──────────────────────────────────────────────────────────────────

def validate_geometry(
    spec: CommonLabwareSpec | WellPlate96SpecV1,
    positions: Sequence[GridPosition],
    report: Optional[ValidationReport] = None,
    expected_count: Optional[int] = None,
) -> ValidationReport:
    """Check that the described object is physically coherent."""
    report = report or ValidationReport()

    if not positions:
        report.add("geometry", "no_positions", "No positions were generated.")
        return report

    # --- counts and names -----------------------------------------
    if expected_count is not None and len(positions) != expected_count:
        report.add(
            "geometry", "position_count",
            f"generated {len(positions)} positions, expected {expected_count}.",
        )

    names = [p.name for p in positions]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        report.add("geometry", "duplicate_names", f"duplicate position names: {duplicates}")

    coords = [(p.x, p.y) for p in positions]
    if len(set(coords)) != len(coords):
        collided = sorted({c for c in coords if coords.count(c) > 1})
        report.add(
            "geometry", "coincident_positions",
            f"{len(collided)} coordinate(s) shared by more than one position, "
            f"e.g. {collided[:3]} — check the spacings.",
        )

    # --- numeric sanity -------------------------------------------
    for p in positions:
        if not (math.isfinite(p.x) and math.isfinite(p.y)):
            report.add("geometry", "non_finite", f"{p.name} has non-finite coordinates ({p.x}, {p.y}).")
            break

    # --- z stack --------------------------------------------------
    core_spec = spec.to_rectangular_grid_spec() if isinstance(spec, WellPlate96SpecV1) else spec
    well_z = core_spec.resolved_well_z
    if well_z < 0:
        report.add(
            "geometry", "well_z_negative",
            f"well floor z is {well_z} mm (< 0). z_dimension ({spec.z_dimension}) "
            f"must be at least depth ({core_spec.depth}), or set well_z explicitly.",
        )
    if well_z + core_spec.depth > core_spec.z_dimension + 1e-9:
        report.add(
            "geometry", "well_taller_than_body",
            f"well floor {well_z} mm + depth {core_spec.depth} mm = {round(well_z + core_spec.depth, 3)} mm "
            f"exceeds the labware height z_dimension={core_spec.z_dimension} mm — "
            f"the well opening would sit above the top of the labware.",
            severity="warning",
        )

    # --- footprint containment (accounting for well radius) -------
    half_x = core_spec.well_footprint_x / 2.0
    half_y = core_spec.well_footprint_y / 2.0

    for p in positions:
        if p.x - half_x < -1e-9:
            report.add(
                "geometry", "well_off_left",
                f"{p.name} at x={p.x} extends to {round(p.x - half_x, 3)} mm — past the left edge (0). "
                f"Increase x_offset by at least {round(half_x - p.x, 3)} mm.",
            )
            break
    for p in positions:
        if p.x + half_x > core_spec.x_dimension + 1e-9:
            report.add(
                "geometry", "well_off_right",
                f"{p.name} at x={p.x} extends to {round(p.x + half_x, 3)} mm — past the right edge "
                f"(x_dimension={core_spec.x_dimension}). Reduce spacing/offset or widen the labware "
                f"by at least {round(p.x + half_x - core_spec.x_dimension, 3)} mm.",
            )
            break
    for p in positions:
        if p.y - half_y < -1e-9:
            report.add(
                "geometry", "well_off_front",
                f"{p.name} at y={p.y} extends to {round(p.y - half_y, 3)} mm — past the front edge (0). "
                f"Increase y_offset by at least {round(half_y - p.y, 3)} mm.",
            )
            break
    for p in positions:
        if p.y + half_y > core_spec.y_dimension + 1e-9:
            report.add(
                "geometry", "well_off_back",
                f"{p.name} at y={p.y} extends to {round(p.y + half_y, 3)} mm — past the back edge "
                f"(y_dimension={core_spec.y_dimension}). Reduce y_offset or deepen the labware "
                f"by at least {round(p.y + half_y - core_spec.y_dimension, 3)} mm.",
            )
            break

    # --- neighbour overlap (warning: legal JSON, impossible object)
    if isinstance(core_spec, RectangularGridSpec):
        overlap_severity = "error" if isinstance(spec, WellPlate96SpecV1) else "warning"
        if core_spec.cols > 1 and core_spec.x_spacing < core_spec.well_footprint_x:
            report.add(
                "geometry", "columns_overlap",
                f"x_spacing {core_spec.x_spacing} mm is smaller than the well width "
                f"{core_spec.well_footprint_x} mm — adjacent columns overlap.",
                severity=overlap_severity,
            )
        if core_spec.rows > 1 and core_spec.y_spacing < core_spec.well_footprint_y:
            report.add(
                "geometry", "rows_overlap",
                f"y_spacing {core_spec.y_spacing} mm is smaller than the well depth "
                f"{core_spec.well_footprint_y} mm — adjacent rows overlap.",
                severity=overlap_severity,
            )

    # --- multichannel format sanity -------------------------------
    if isinstance(core_spec, RectangularGridSpec):
        if core_spec.plate_format == "96Standard" and (core_spec.rows, core_spec.cols) != (8, 12):
            report.add(
                "geometry", "format_grid_mismatch",
                f"plate_format='96Standard' but the grid is {core_spec.rows}x{core_spec.cols}. "
                f"Multichannel column addressing assumes 8x12; use 'irregular' instead.",
                severity="warning",
            )
        if core_spec.plate_format == "384Standard" and (core_spec.rows, core_spec.cols) != (16, 24):
            report.add(
                "geometry", "format_grid_mismatch",
                f"plate_format='384Standard' but the grid is {core_spec.rows}x{core_spec.cols}. "
                f"Use 'irregular' instead.",
                severity="warning",
            )

    report.mark("geometry", PASS)
    return report


# ──────────────────────────────────────────────────────────────────
# Layer 3 — JSON document structure
# ──────────────────────────────────────────────────────────────────

_REQUIRED_TOP_LEVEL = (
    "schemaVersion", "version", "namespace", "metadata", "brand", "parameters",
    "cornerOffsetFromSlot", "ordering", "dimensions", "wells", "groups",
)


def validate_json_document(
    definition: Dict[str, Any],
    report: Optional[ValidationReport] = None,
    expected_filename: Optional[str] = None,
) -> ValidationReport:
    """Check the serialized document: required keys, ordering integrity, round-trip."""
    report = report or ValidationReport()

    missing = [key for key in _REQUIRED_TOP_LEVEL if key not in definition]
    if missing:
        report.add("json", "missing_keys", f"definition is missing required key(s): {missing}")
        return report

    # Round-trip: catches NaN/Infinity and any non-serializable value.
    try:
        text = json.dumps(definition, ensure_ascii=False, allow_nan=False)
        if json.loads(text) != definition:
            report.add("json", "round_trip", "definition does not survive a JSON round-trip unchanged.")
    except (TypeError, ValueError) as exc:
        report.add("json", "not_serializable", f"definition is not valid JSON: {exc}")
        return report

    wells = definition["wells"]
    ordering = definition["ordering"]
    ordered_names = [name for column in ordering for name in column]

    unknown = [n for n in ordered_names if n not in wells]
    if unknown:
        report.add(
            "json", "ordering_unknown_well",
            f"`ordering` references well(s) that do not exist in `wells`: {unknown[:5]}",
        )
    unordered = [n for n in wells if n not in ordered_names]
    if unordered:
        report.add(
            "json", "well_not_in_ordering",
            f"`wells` contains entries missing from `ordering`: {unordered[:5]}",
        )
    if len(ordered_names) != len(set(ordered_names)):
        report.add("json", "ordering_duplicates", "`ordering` lists the same well more than once.")

    column_lengths = {len(column) for column in ordering}
    if len(column_lengths) > 1:
        report.add(
            "json", "ragged_ordering",
            f"`ordering` columns have differing lengths {sorted(column_lengths)} — "
            f"a rectangular grid must be uniform.",
        )

    for group_index, group in enumerate(definition["groups"]):
        stray = [n for n in group.get("wells", []) if n not in wells]
        if stray:
            report.add(
                "json", "group_unknown_well",
                f"groups[{group_index}] references unknown well(s): {stray[:5]}",
            )

    load_name = definition["parameters"].get("loadName")
    if expected_filename is not None:
        stem = expected_filename[:-5] if expected_filename.endswith(".json") else expected_filename
        if stem != load_name:
            report.add(
                "json", "filename_mismatch",
                f"output filename '{expected_filename}' does not match parameters.loadName "
                f"'{load_name}'. This repo requires <loadName>.json, flat in labware/ — "
                f"protocols and the robot's custom-labware store both resolve definitions by that name.",
            )

    report.mark("json", PASS)
    return report


# ──────────────────────────────────────────────────────────────────
# Layer 4 — Opentrons' own validation
# ──────────────────────────────────────────────────────────────────

def validate_against_opentrons(
    definition: Dict[str, Any],
    report: Optional[ValidationReport] = None,
) -> ValidationReport:
    """Validate with the installed Opentrons tooling.

    Runs up to three independent checks, each skipped if its dependency is not
    importable. If none are available the layer is marked ``NOT_AVAILABLE``
    rather than ``PASS`` — an unvalidated definition must never look validated.
    """
    report = report or ValidationReport()
    ran: List[str] = []

    # numpy>=2 removed trapz, which opentrons_shared_data still imports.
    # conftest.py applies the same shim for the test suite.
    try:
        import numpy as _np

        if not hasattr(_np, "trapz"):
            _np.trapz = _np.trapezoid
    except ImportError:
        pass

    # 4a — jsonschema against the shipped labware schema 2.
    try:
        import jsonschema
        from opentrons_shared_data.labware import load_schema

        try:
            jsonschema.validate(definition, load_schema())
            ran.append("jsonschema")
        except jsonschema.ValidationError as exc:
            path = "/".join(str(p) for p in exc.absolute_path) or "<root>"
            report.add("opentrons", "jsonschema", f"at {path}: {exc.message}")
    except ImportError:
        pass

    # 4b — the shared-data pydantic model.
    try:
        from opentrons_shared_data.labware.labware_definition import LabwareDefinition2

        try:
            LabwareDefinition2.model_validate(definition)
            ran.append("LabwareDefinition2")
        except Exception as exc:  # pydantic ValidationError
            report.add("opentrons", "labware_definition_model", str(exc).splitlines()[0])
    except ImportError:
        pass

    # 4c — the function the robot itself uses to accept a definition.
    try:
        from opentrons.protocols.labware import verify_definition

        try:
            verify_definition(definition)
            ran.append("verify_definition")
        except Exception as exc:
            report.add("opentrons", "verify_definition", str(exc).splitlines()[0])
    except ImportError:
        pass

    if not ran and report.layers.get("opentrons") != FAIL:
        report.add(
            "opentrons", "unavailable",
            "no Opentrons validation tooling is importable in this interpreter "
            "(need opentrons / opentrons_shared_data / jsonschema) — the definition "
            "has NOT been checked against the Opentrons schema.",
            severity="warning",
        )
        report.mark("opentrons", NOT_AVAILABLE)
        return report

    report.mark("opentrons", PASS)
    return report


# ──────────────────────────────────────────────────────────────────
# Full run
# ──────────────────────────────────────────────────────────────────

def validate_all(
    spec: CommonLabwareSpec | WellPlate96SpecV1,
    positions: Sequence[GridPosition],
    definition: Dict[str, Any],
    expected_filename: Optional[str] = None,
    expected_count: Optional[int] = None,
) -> ValidationReport:
    """Run geometry, json and opentrons layers over an already-built definition.

    The ``schema`` layer is marked PASS because constructing ``spec`` at all
    means pydantic already accepted it — an invalid spec raises before reaching
    this function.
    """
    report = ValidationReport()
    report.mark("schema", PASS)
    validate_geometry(spec, positions, report, expected_count=expected_count)
    validate_json_document(definition, report, expected_filename=expected_filename)
    validate_against_opentrons(definition, report)
    return report
