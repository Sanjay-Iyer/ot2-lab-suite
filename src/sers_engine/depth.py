"""Aspiration depth arithmetic, shared by offline validation and simulation.

Bore dominates whether a tip is actually submerged: 500 uL is an 8 mm column in
a 6.94 mm plate well but a 0.81 mm puddle in a 28 mm vial.  Volume alone is
therefore a useless safety check across this deck's mixed labware, and both the
fast offline validator and the authoritative simulation run the same numbers.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .schema import ExperimentConfig, REPO_ROOT

MINIMUM_SUBMERSION_MM = 1.0


def liquid_height_mm(volume_ul: float, area_mm2: float) -> float:
    """1 uL is 1 mm^3; these vessels are straight-walled at working depths."""
    return float(volume_ul) / float(area_mm2)


def area_from_dimensions(
    diameter: float | None, length: float | None = None, width: float | None = None
) -> float | None:
    """Cross-sectional area of a well, or None when the shape is unknown."""
    if diameter:
        return math.pi * (float(diameter) / 2.0) ** 2
    if length and width:
        return float(length) * float(width)
    return None


def check_aspiration_depths(
    config: ExperimentConfig, area_lookup: dict[tuple[str, str], float]
) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for every aspiration the workflow performs.

    ``area_lookup`` maps (labware role, well) to the well's cross-sectional area.
    Callers supply it either from loaded Opentrons labware or straight from the
    labware definition JSON, so both paths agree by construction.
    """
    levels = config.minimum_well_volumes()
    errors: list[str] = []
    warnings: list[str] = []

    for role, well_name, offset_mm, label in config.aspiration_points():
        volume_ul = levels.get((role, well_name))
        area_mm2 = area_lookup.get((role, well_name))
        if volume_ul is None or not area_mm2:
            continue
        height_mm = liquid_height_mm(volume_ul, area_mm2)
        if offset_mm > height_mm + 1e-9:
            errors.append(
                f"{label}: aspirating {offset_mm:g} mm above the floor of "
                f"{role}:{well_name}, but its worst-case {volume_ul:g} uL is only "
                f"{height_mm:.2f} mm deep in this labware; the tip would draw air"
            )
            continue
        margin_mm = height_mm - offset_mm
        if margin_mm < MINIMUM_SUBMERSION_MM:
            warnings.append(
                f"{label}: only {margin_mm:.2f} mm of liquid above the tip at "
                f"{role}:{well_name} ({volume_ul:g} uL is {height_mm:.2f} mm deep); "
                "small Z or fill errors will aspirate air"
            )
    return errors, warnings


def areas_from_definitions(config: ExperimentConfig) -> dict[tuple[str, str], float]:
    """Well areas read straight from the labware definition JSON, no robot needed."""
    areas: dict[tuple[str, str], float] = {}
    for role, spec in config.deck_layout.labware.items():
        if not spec.definition_path:
            continue
        path = Path(spec.definition_path)
        if not path.is_absolute():
            path = REPO_ROOT / path
        if not path.is_file():
            continue
        definition = json.loads(path.read_text(encoding="utf-8"))
        for well_name, well in definition.get("wells", {}).items():
            area = area_from_dimensions(
                well.get("diameter"), well.get("xDimension"), well.get("yDimension")
            )
            if area:
                areas[(role, well_name)] = area
    return areas


def areas_from_loaded_labware(
    config: ExperimentConfig, labware_by_role: dict[str, Any]
) -> dict[tuple[str, str], float]:
    """Well areas taken from labware the protocol context has actually loaded."""
    areas: dict[tuple[str, str], float] = {}
    for role in config.deck_layout.labware:
        labware = labware_by_role.get(role)
        if labware is None:
            continue
        for well_name, well in labware.wells_by_name().items():
            area = area_from_dimensions(
                getattr(well, "diameter", None),
                getattr(well, "length", None),
                getattr(well, "width", None),
            )
            if area:
                areas[(role, well_name)] = area
    return areas
