"""
Labware family registry.

A *family* pairs a parameter schema with the geometry generator that knows how
to lay that family's positions out. The agent discovers what it can build by
querying this registry — it is never told in a prompt, so it cannot claim to
support a family that has not been implemented.

Adding a family (see docs/custom_labware_generation.md for the full procedure):

    1. subclass ``CommonLabwareSpec`` in schemas.py with only the new fields
    2. write its geometry generator in geometry.py (or a new module)
    3. write a ``build_<family>_labware(spec)`` in builder.py
    4. register it here
    5. add a baseline config under configs/labware/ and a test

Nothing in the agent, the tool, or the validation layers changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Sequence, Type

from pydantic import BaseModel

from src.labware.builder import build_rectangular_grid_labware
from src.labware.geometry import GridPosition, generate_rectangular_grid
from src.labware.schemas import CommonLabwareSpec, RectangularGridSpec, WellPlate96SpecV1


@dataclass(frozen=True)
class LabwareFamily:
    """One implemented labware family."""

    name: str
    description: str
    spec_model: Type[BaseModel]
    build: Callable[[Any], Dict[str, Any]]
    positions: Callable[[Any], Sequence[GridPosition]]
    example_configs: Sequence[str] = ()
    agent_visible: bool = True
    intent_terms: Sequence[str] = ()

    @property
    def required_fields(self) -> List[str]:
        """Field names with no default — the caller must supply these."""
        return sorted(
            name for name, f in self.spec_model.model_fields.items() if f.is_required()
        )

    @property
    def optional_fields(self) -> List[str]:
        return sorted(
            name for name, f in self.spec_model.model_fields.items() if not f.is_required()
        )


def _rectangular_grid_positions(spec: RectangularGridSpec) -> Sequence[GridPosition]:
    return generate_rectangular_grid(
        rows=spec.rows,
        columns=spec.cols,
        origin_x_mm=spec.x_offset,
        origin_y_mm=spec.y_offset,
        spacing_x_mm=spec.x_spacing,
        spacing_y_mm=spec.y_spacing,
    )


def _well_plate_96_positions(spec: WellPlate96SpecV1) -> Sequence[GridPosition]:
    return _rectangular_grid_positions(spec.to_rectangular_grid_spec())


def _build_well_plate_96(spec: WellPlate96SpecV1) -> Dict[str, Any]:
    return build_rectangular_grid_labware(spec.to_rectangular_grid_spec())


RECTANGULAR_GRID = LabwareFamily(
    name="rectangular_grid",
    description=(
        "Evenly spaced rows x columns of identical positions on a single pitch. "
        "Flat printing substrates, well plates, troughs and simple racks."
    ),
    spec_model=RectangularGridSpec,
    build=build_rectangular_grid_labware,
    positions=_rectangular_grid_positions,
    example_configs=("paper_print_96_flat", "corning_96_wellplate_360ul", "tuberack_3dprint_20ml_8vials_v2"),
    agent_visible=False,
)


WELL_PLATE_96 = LabwareFamily(
    name="well_plate_96",
    description=(
        "V1 regular 8 x 12 plate: exactly 96 identical wells with even row "
        "and column center spacing. Irregular or mixed wells are unsupported."
    ),
    spec_model=WellPlate96SpecV1,
    build=_build_well_plate_96,
    positions=_well_plate_96_positions,
    example_configs=("well_plate_96/paper_print_96_flat_v1",),
    intent_terms=("96", "8 x 12", "8x12", "paper plate", "same plate", "rest of the wells"),
)


LABWARE_FAMILIES: Dict[str, LabwareFamily] = {
    RECTANGULAR_GRID.name: RECTANGULAR_GRID,
    WELL_PLATE_96.name: WELL_PLATE_96,
    # "tube_rack": TUBE_RACK,   <- next family; see the docs procedure
}


def list_families(agent_visible_only: bool = False) -> List[LabwareFamily]:
    """Implemented families, optionally restricted to the agent's public surface."""
    families = [LABWARE_FAMILIES[name] for name in sorted(LABWARE_FAMILIES)]
    return [family for family in families if family.agent_visible] if agent_visible_only else families


def match_agent_family(user_intent: str) -> LabwareFamily | None:
    """Match natural-language routing terms declared by public registry entries."""
    normalized = user_intent.lower().replace("×", "x")
    matches = [
        family
        for family in list_families(agent_visible_only=True)
        if any(term in normalized for term in family.intent_terms)
    ]
    return matches[0] if len(matches) == 1 else None


def get_family(name: str) -> LabwareFamily:
    try:
        return LABWARE_FAMILIES[name]
    except KeyError:
        raise ValueError(
            f"unknown labware family {name!r}. Implemented families: "
            f"{sorted(LABWARE_FAMILIES)}"
        ) from None
